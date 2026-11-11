# Deployment

## Minimal Installation

중앙 PC에 별도 gateway + Core + PostgreSQL + dashboard/BFF + Redis를 둡니다. gateway는 ContextForge native 인증·catalog·중계를, Core는 작업·세션·runtime과 내부 DBOS를 담당합니다. 에이전트·모델·GPU 컨테이너는 필요한 장치에 따로 배치합니다. 외부 DBOS 관리 서비스는 필요하지 않습니다.

```bash
python3 scripts/init-compose.py
docker compose up -d --build
docker compose ps
```

기본 접속은 웹 `http://localhost:3000/app/orchestration`, native MCP·기존 호환 REST는 `http://127.0.0.1:4444`, 직접 Core REST는 `http://127.0.0.1:4455`입니다. Core의 REST도 CF backend로 인증·권한을 검증합니다. `.env`에서 관리자 계정을 로컬로 확인하고 초기 비밀번호를 변경합니다. 비밀 값은 채팅·로그·저장소에 올리지 않습니다.

| Compose profile | 추가되는 구성 | 용도 |
|---|---|---|
| 없음 | 없음 | gateway·Core·DB·웹 기본 구성 |
| image-demo | CPU 이미지 변환기 | PNG/JPEG → WebP 사용자 시나리오 |
| demo | 텍스트 변환기 | 기존 Text → CSV 회귀 예제 |
| managed | Komodo Core + MongoDB | 선택적 Docker 배포·운영 |
| https | Caddy | 사설망 원격 HTTPS 진입점 |

```bash
docker compose --profile image-demo up -d --build
```

[Operator Quickstart](operator-quickstart.md)에서 서비스 정의 가져오기부터 결과 다운로드까지 따라 할 수 있습니다. 모든 profile을 동시에 켜는 것을 최소 구성이라고 부르지 않습니다.

운영 데이터는 Compose 볼륨에 남습니다. `docker compose down`은 컨테이너를 내립니다. **down -v는 데이터 볼륨을 삭제**합니다. 최초 생성 후 .env만 바꿔도 기존 PostgreSQL/MongoDB 사용자 비밀번호가 자동 변경되지는 않습니다.

## Accounts and Projects

기존 ContextForge 사용자·팀·역할·토큰 화면을 사용합니다. 프로젝트는 native team ID를 참조합니다. 일반 사용자에게 도구 읽기·실행 권한을 부여하고 필요한 팀에만 추가합니다. 서비스 설치·runtime 운영·업데이트는 플랫폼 관리자가 수행합니다.

정의 공개 범위는 private 또는 project입니다. 프로젝트 공유는 project_id와 visibility를 함께 설정합니다. 도구를 팀에 공유해도 작업·결과·세션·자격 증명은 요청 사용자의 자원입니다. 여러 제품을 같은 사용자 이름으로 설치해도 Paperclip·Komodo·OpenShell의 자원 권한이 자동 연결되지는 않습니다.

## Remote Web and MCP Access

사설 DNS/VPN 이름과 방화벽을 준비한 뒤 .env에 다음 값을 설정합니다.

```dotenv
AGENTPORT_HOST=agentport.internal.example
AGENTPORT_BIND=YOUR_VPN_INTERFACE_IP
PUBLIC_ORIGIN=https://agentport.internal.example
COOKIE_SECURE=true
```

```bash
docker compose --profile image-demo --profile https up -d --build
docker compose cp https:/data/caddy/pki/authorities/local/root.crt ./agentport-ca.crt
```

예제 Caddy는 내부 CA를 사용합니다. 관리하는 PC·모바일에 **공개 루트 인증서**를 신뢰 설치하거나 조직/공인 인증서로 Caddyfile을 수정하세요. TLS 검증을 끄지 않습니다. HTTPS 바인딩 기본값도 loopback이고 외부 공개를 자동 수행하지 않습니다.

웹은 BFF로, native `/mcp`, `/servers/{id}/mcp`와 `/rpc`는 gateway로 전달합니다. BFF는 `/api/orchestration/v1/*`를 Core로, 로그인과 다른 native API는 gateway로 보냅니다. Core·gateway의 내부 bridge 경로는 HTTPS 공개 라우트에 추가하지 않습니다. 브라우저 REST는 BFF의 `/api/orchestration/v1`을 사용합니다. 외부 REST 앱은 HTTPS `/orchestration/v1`에 native Bearer token으로 접속합니다. Caddy는 해당 경로만 Core로 전달하고 internal bridge 경로는 공개하지 않습니다. 직접 MCP에는 기존 ContextForge API 토큰을 설정합니다. 브라우저 로그인 쿠키와 MCP Bearer 토큰은 다릅니다. OAuth 자동 클라이언트 등록이나 별도 모바일 앱을 제공하는 것은 아닙니다.

웹 진입점은 호스트 포트 게시를 위해 frontend 일반 브리지에도 연결합니다. PostgreSQL·Redis·예제 도구는 internal 네트워크에 남습니다. gateway에는 외부 API 연결 경로가 있습니다. 서비스 URL은 **gateway 컨테이너에서 접근 가능한 주소**여야 합니다. 브라우저 localhost, 컨테이너 localhost, 원격 장치 IP는 서로 다릅니다.

## Private Service Allowlist

기본 private subnet은 `172.30.71.0/24`입니다. native SSRF 검사를 유지하고 해당 subnet만 allowlist에 둡니다.

```dotenv
SSRF_ALLOW_PRIVATE_NETWORKS=false
SSRF_ALLOWED_NETWORKS=["172.30.71.0/24","192.168.10.25/32"]
```

두 번째 주소는 실제 원격 도구의 사설 IP로 바꿉니다. 변경 후 gateway를 재생성합니다. CIDR 변경 시 Compose의 AGENTPORT_PRIVATE_SUBNET과 allowlist를 함께 맞춥니다. API 자격 증명을 URL 쿼리나 userinfo에 넣지 않습니다. 원격 도구는 gateway 장치만 접근하도록 방화벽·mTLS 등을 별도로 설정합니다.

## Komodo and Remote Devices

```bash
docker compose --profile managed up -d
```

Komodo 운영자 계정은 `http://localhost:9120`, 사용자 operator, 비밀번호는 .env의 KOMODO_ADMIN_PASSWORD입니다. 중앙 계정과 별도입니다. 신규 가입을 끄고 AgentPort용 최소 범위 API 키를 만들어 .env의 KOMODO_API_KEY/KOMODO_API_SECRET을 채운 뒤 Core를 재생성합니다. 현재 Komodo Core 연결은 하나이며 backend_ref는 default만 지원합니다.

각 PC/DGX에 운영자가 Periphery 2.3.4를 설치하고 Core에 등록합니다. [고정 버전 Compose 예제](https://github.com/moghtech/komodo/blob/v2.3.4/compose/periphery.compose.yaml)의 Core 주소·서버 이름·공개 키/온보딩 키 방식을 따릅니다. 과거 passkey 설정과 혼용하지 않습니다.

Periphery는 Docker 소켓과 배포 경로를 다루는 강한 운영 권한이 있습니다. 이 소켓을 gateway·도구·ACP 워커에 마운트하지 않습니다. 일반 사용자 입력으로 임의 Compose·셸 명령을 실행하지 않습니다.

설정 순서:

1. Komodo에서 실행 장치와 Compose 스택을 등록하고 최초 배포합니다.
2. CPU 아키텍처·GPU 런타임·드라이버를 확인하고 readiness URL을 검증합니다.
3. gateway에서 호출 URL까지 SSRF allowlist·사설 경로를, Core에서 준비 URL·Komodo/OpenShell까지 운영 경로를 설정합니다.
4. Runtimes에 backend komodo, target_id, readiness, 용량·정책을 등록합니다.
5. Services에서 여러 기능을 같은 runtime_id에 연결합니다.
6. 동시 요청·단일 기동·사용 중 중지 거부·유휴 종료를 실제 장치에서 검증합니다.

Komodo의 비동기 Update 응답은 완료·성공을 조회한 뒤 다음 단계로 진행합니다. 임대는 AgentPort 호출만 추적합니다. 자체 cron·Paperclip heartbeat·다른 입구의 작업이 있는 runtime은 on_demand로 종료하지 않습니다.

## Agent Workers and Sandboxes

```bash
docker build -t agentport-core:0.2.0 .
docker build -f deploy/worker.Dockerfile -t agentport-acp:0.1.0 .
```

워커를 **사용자 × 프로젝트 × 하네스 신뢰 경계**별 컨테이너/볼륨으로 분리합니다. 기본 이미지에는 잠긴 Codex/Claude ACP 어댑터가 들어 있습니다. Hermes·Kimi·OpenCode의 실행 파일과 설정은 운영자가 별도 이미지에 준비합니다. 설치 template이 있다고 실제 로그인·모델 추론을 검증한 것은 아닙니다.

중앙 GET `/orchestration/v1/worker-public-key`의 Ed25519 공개 키만 워커에 전달합니다. JWT_SECRET_KEY·AUTH_ENCRYPTION_SECRET·DB 비밀번호·중앙 개인 서명 키는 워커에 전달하지 않습니다. 사용자·프로젝트와 정확한 호출 audience를 고정하고 AOG_WORKER_ROOT를 영속 볼륨으로 둡니다. worker 프로세스는 하나만 실행합니다.

native 승인 기본값은 interactive입니다. Activity에 하네스가 제공한 옵션을 표시하고 사용자가 선택합니다. 그래프의 cloud 데이터 공개 승인과 별개입니다. 프로세스 재시작 후 복구할 수 없는 live 승인 요청은 확인 대상으로 남깁니다.

provider API 키와 구독/native 로그인은 별도 경로입니다. native 프로필은 운영자가 준비한 전용 HOME을 opaque ref로 등록합니다. 중앙 입력에서 호스트 경로를 선택하지 않습니다. [Worker Profiles](../workers/acp/README.md)와 [Integrations](integrations.md)에 환경 변수·프로토콜·제약을 설명합니다.

OpenShell은 선택 실행 backend입니다. [Runtime Platform](runtime-platform.md)의 설치·인증·샌드박스 등록 절차를 따릅니다. OpenShell 설치, 정책 생성, native 로그인 자동화, 전체 제품 배포를 한 번에 수행하는 기능은 없습니다.

## Updates and Recovery

진행 작업과 미확정 작업을 먼저 정리합니다. runtime 사용 중 stop/update는 409로 거부합니다. 배포가 불명확하면 외부 상태를 확인하고 작업 resolve 이후 runtime reconcile을 사용합니다. 네트워크 timeout은 외부 작업 종료의 증거가 아닙니다.

복구가 필요한 Komodo 변경은 불변 태그/digest의 별도 stack을 releases에 등록합니다. rollback_safe는 이전 버전과 데이터가 호환됨을 검증한 경우에만 true로 둡니다. 일반 update는 같은 stack 재배포이며 이전 이미지 복구를 보장하지 않습니다. OpenShell update는 미지원입니다. 검증한 대체 sandbox를 준비·재연결해야 합니다.

Core와 gateway는 별도 이미지로 업데이트합니다. 기본 gateway image는 manifest의 고정 ContextForge 소스를 빌드 시 준비해 호환 patch를 적용합니다. stock image로 임의 교체하면 native caller·local dispatch·backend guard가 없어집니다. [Integration Maintenance](upstream-contextforge.md)에 따라 검증된 image·bridge contract 1·AOG schema 2 조합만 사용합니다.

### Backup and Restore

1. 새 요청을 막고 실행·승인·불명확 작업을 정리한 뒤 Core·gateway·워커를 정지합니다.
2. PostgreSQL을 pg_dump -Fc 등 DB 자체 도구로 백업합니다. ContextForge·AgentPort·DBOS 스키마를 일관된 시점에 포함합니다.
3. gateway-data의 파일·worker-signing.key, 워커 HOME·작업공간·job SQLite, .env 암호화 키를 암호화된 별도 저장소에 보관합니다. Komodo DB·통신 키·스택도 별도 보존합니다.
4. 별도 환경에서 같은 코드 버전·키로 복원합니다. 실행 중 DB 파일을 덮어쓰지 않습니다.
5. 두 사용자 권한·자격 증명 복호화·완료 결과·워커 서명·미완료 외부 상태를 확인한 뒤 트래픽을 전환합니다.

Redis 세션은 보존 정책에 따라 백업하거나 복원 후 다시 로그인합니다. **이미지 rollback과 DB restore는 별도 작업**입니다. SQLite→PostgreSQL 자동 이관, 실제 DGX 배포, 운영 DB 복원 리허설을 완료했다고 주장하지 않습니다. schema1에서 현재 구조로의 이전은 [Migration](migration.md)을 먼저 따릅니다.

## Component Configuration

| 설정 | 위치 | 역할 |
|---|---|---|
| AOG_GATEWAY_BACKEND=contextforge | Core | 설치한 backend 선택 |
| AOG_GATEWAY_URL=http://gateway:4444 | Core | 인증·catalog·native 도구 bridge |
| AOG_CORE_URL=http://core:4455 | gateway | 검증된 native 호출을 Core로 전달 |
| AOG_GATEWAY_BRIDGE_SECRET | Core + gateway만 | 32자 이상 운영자 생성 random secret. 브라우저·worker에 배포 금지 |
| AGENTPORT_CORE_URL=http://core:4455 | BFF | 작업·세션·결과 REST 대상 |
| CONTEXTFORGE_URL=http://gateway:4444 | BFF | 기존 로그인·native API 대상 |
| DATABASE_URL | gateway | native 계정·token·Tool DB |
| AOG_DATABASE_URL | Core + gateway | 공유 schema 2 runtime binding·job/grant metadata |
| DBOS_SYSTEM_DATABASE_URL | Core만 | durable execution checkpoint |

Compose는 Core에 JWT_SECRET_KEY·AUTH_ENCRYPTION_SECRET·관리자 비밀번호를 전달하지 않습니다. Core는 암복호화가 필요하면 private gateway RPC를 사용합니다. 두 구성요소는 여전히 신뢰하는 중앙 운영 영역입니다. bridge secret은 전송 내용을 암호화하지 않으므로 다른 호스트로 나누면 검증한 TLS/mTLS·사설 터널도 필요합니다.

새 설치의 init-compose.py는 bridge secret도 생성합니다. 기존 .env는 덮어쓰지 않으므로 업그레이드 전에 해당 키를 로컬에서 추가하고 백업합니다. 키를 채팅에 보내지 않습니다. [Migration](migration.md)의 구성요소 분리 절차를 참조하세요.

게이트웨이를 먼저 준비하고 Core가 gateway health·호환 contract를 확인한 뒤 시작합니다. dashboard는 두 API가 준비된 후 실행합니다. Core 중지 시 native CF 로그인·관리만 계속되고 작업 실행은 사용할 수 없습니다. CF 중지 시 Core는 인증·권한·native 호출을 우회하지 않습니다. 같은 key·볼륨·DB를 유지한 이미지 변경도 데이터 복원과는 별도입니다.
