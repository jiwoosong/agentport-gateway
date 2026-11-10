# 설치와 운영

## 기본 배치

중앙 PC에 gateway + PostgreSQL + dashboard/BFF + Redis를 둡니다. 모델·영상·이미지 컨테이너는 PC 또는 DGX Spark에 따로 배치합니다. 서비스의 엔드포인트는 **게이트웨이 컨테이너에서 도달 가능한 주소**여야 합니다. 브라우저의 localhost, 컨테이너의 localhost, 원격 호스트 주소는 서로 다릅니다.

```bash
python3 scripts/init-compose.py
docker compose --profile demo up -d --build
docker compose ps
```

관리자 이메일·비밀번호는 생성된 `.env`에서 로컬로 확인합니다. 웹은 `http://localhost:3000/app/orchestration`, 직접 REST/MCP는 loopback 4444입니다. 관리자 초기 비밀번호를 변경합니다. 기존 ContextForge 사용자·팀 화면에서 사용자를 만들고 프로젝트 팀과 역할을 부여합니다. AgentPort 서비스의 `project_id`는 그 팀 ID입니다.

대시보드와 HTTPS 프록시는 호스트 포트 게시를 위해 `frontend` 일반 브리지에도 연결합니다. 이 두 구성요소에는 외부 통신 경로가 생깁니다. PostgreSQL·Redis·예제 변환기는 내부 전용 네트워크에 남습니다. Docker는 내부 전용 네트워크에만 연결한 컨테이너의 포트를 게시하지 않으므로, 웹 진입점까지 `internal`로 바꾸면 호스트 접속이 실패합니다. [Docker의 설명](https://github.com/moby/moby/discussions/53256)

운영 데이터는 Compose 볼륨에 남습니다. `docker compose down`은 컨테이너를 내리고, **`down -v`는 데이터 볼륨을 삭제**하므로 운영 환경에서 쓰지 않습니다. 최초 DB 비밀번호를 바꿀 때 `.env`만 수정하면 기존 PostgreSQL/Mongo 계정 비밀번호가 자동으로 바뀌지는 않습니다.

소스 개발은 [개발 환경](development.md)의 uv·npm 명령을 사용합니다. 생성된 `.env`의 SQLite 파일들은 `data/`에 있습니다. 기존 SQLite를 PostgreSQL로 자동 이전하는 도구는 제공하지 않습니다. 개발 데이터를 내보내고 새 운영 환경에 정의를 재등록하거나 검증한 별도 마이그레이션을 수행하세요. 암호화 키를 잃으면 저장된 자격 증명을 복원할 수 없습니다.

## 다른 PC와 모바일

사설 DNS 또는 VPN 이름을 정하고 `.env`에 다음 값을 설정합니다.

```dotenv
AGENTPORT_HOST=agentport.internal.example
AGENTPORT_BIND=YOUR_VPN_INTERFACE_IP
PUBLIC_ORIGIN=https://agentport.internal.example
COOKIE_SECURE=true
```

```bash
docker compose --profile demo --profile https up -d --build
docker compose cp https:/data/caddy/pki/authorities/local/root.crt ./agentport-ca.crt
```

이 예제 Caddy는 내부 CA를 사용합니다. 해당 공개 루트 인증서를 자신이 관리하는 PC·모바일에 신뢰 설치해야 합니다. 조직 인증서나 공인 인증서를 쓰려면 `deploy/Caddyfile`을 운영 정책에 맞게 바꿉니다. TLS 검증을 끄지 않습니다. DNS 이름·VPN 경로·방화벽을 함께 구성하세요. HTTPS 포트 기본 바인딩도 loopback이며 외부 공개는 자동으로 하지 않습니다.

HTTPS에서 웹은 BFF로, `/orchestration/v1/mcp/`는 게이트웨이로 전달됩니다. 직접 REST가 필요한 사설 클라이언트는 별도 보호된 라우트를 운영자가 추가하거나 VPN 터널로 loopback 포트에 연결합니다. 브라우저 세션과 MCP API 토큰은 별개입니다. 모바일 네이티브 앱이나 OAuth 기반 MCP 자동 등록을 구현한 것은 아닙니다.

## Komodo와 원격 장치

```bash
docker compose --profile managed up -d
```

`http://localhost:9120`의 운영자 계정은 `operator`, 비밀번호는 `.env`의 `KOMODO_ADMIN_PASSWORD`입니다. 별도 배포 관리 계정이며 중앙 사용자 계정이 아닙니다. 신규 회원 등록은 끕니다. Komodo에서 AgentPort 전용 API 키를 만들고 필요한 스택에만 배포 권한을 부여합니다. `.env`에 `KOMODO_URL=http://komodo:9120`, `KOMODO_API_KEY`, `KOMODO_API_SECRET`을 설정한 뒤 gateway를 재생성합니다.

각 PC·DGX에 Komodo **Periphery 2.3.4**를 운영자가 설치하고 Core에 등록합니다. 2.3.4는 Core 주소·서버 이름·공개 키/온보딩 키로 연결하는 버전입니다. 과거 passkey 방식의 설정을 혼용하지 않습니다. [고정 버전 Periphery 예제](https://github.com/moghtech/komodo/blob/v2.3.4/compose/periphery.compose.yaml)를 참고하세요.

Periphery는 Docker 소켓과 배포 경로에 접근하는 강한 운영 권한을 가집니다. 이 소켓을 gateway·일반 도구·ACP 워커에 마운트하지 않습니다. 전용 호스트 계정, 사설망, 제한된 운영자 접근으로 보호하세요. 이 저장소는 호스트 Docker 데몬이나 Periphery를 자동 설치하지 않습니다.

1. Komodo에서 실행 장치와 Compose 스택을 등록합니다.
2. 이미지의 CPU 아키텍처와 GPU 런타임을 확인하고 최초 배포·준비 URL을 검증합니다.
3. 게이트웨이가 준비 URL과 호출 URL에 접근할 수 있게 사설 네트워크를 연결합니다.
4. AgentPort 서비스에 스택 ID, 호출 스키마, 실행 정책을 등록합니다.
5. `on_demand`에서 병렬 요청·단일 기동·사용 중 중지 거부·유휴 종료를 실제 장치에서 확인합니다.

게이트웨이에서 임의 Docker Compose 텍스트나 셸 명령을 일반 사용자 입력으로 실행하지 않습니다. 배포 대상은 운영자가 등록한 스택입니다. Komodo API 응답이 비동기 Update이면 완료·성공 상태를 조회한 후 다음 단계로 진행합니다.

## PC와 DGX Spark

중앙 PC의 x86_64 이미지와 Spark의 aarch64/arm64 이미지를 같은 것으로 가정하지 않습니다. 모델마다 CUDA·드라이버·프레임워크 버전·GPU 메모리 요구사항을 별도로 확인합니다. 게이트웨이는 GPU 스케줄러가 아니며 VRAM을 추정해 자동 재배치하지 않습니다. 서비스 `concurrency`와 별도 스택으로 사용자가 용량과 위치를 지정합니다.

로컬 작업 환경에는 Docker 데몬과 DGX 접속 경로가 없었습니다. 기본 demo Compose는 GitHub Actions Ubuntu amd64에서 실제 빌드·로그인·변환·결과 조회를 검증했습니다. 실제 GPU 추론·장치 간 네트워크·NVIDIA 컨테이너 런타임은 아직 미검증입니다. NVIDIA NIM·Triton 등 HTTP API로 노출되는 런타임도 같은 등록 계약으로 연결할 수 있지만 해당 제품의 라이선스·arm64 지원·모델 호환성은 별도로 검증해야 합니다.

## ACP 워커

먼저 게이트웨이 이미지를 만든 뒤 `deploy/worker.Dockerfile`로 워커 이미지를 만듭니다.

```bash
docker build -t agentport-gateway:0.1.0 .
docker build -f deploy/worker.Dockerfile -t agentport-acp:0.1.0 .
```

워커는 **사용자 × 프로젝트 × 에이전트 신뢰 경계별 컨테이너/볼륨**으로 분리합니다. 일반 서비스와 달리 여러 사용자를 같은 워커에 넣지 않습니다. 게이트웨이 운영자가 `/orchestration/v1/worker-public-key`에서 얻은 공개 키만 워커에 전달합니다. 중앙의 `JWT_SECRET_KEY`, `AUTH_ENCRYPTION_SECRET`, `AOG_DELEGATION_SECRET`, DB 비밀번호를 워커에 넣지 않습니다.

운영자가 다른 사용자를 위한 워커 서비스를 등록할 때는 그 사용자 전용 ContextForge 팀에 공유하고, 같은 팀 ID를 워커 프로젝트에 설정합니다. 에이전트 인스턴스는 런타임 서비스의 프로젝트를 상속합니다. 여러 사람이 포함된 팀에 공유해도 워커는 지정 소유자 외의 호출을 거부합니다. 사용자별 워커 자동 프로비저닝은 아직 없으며 운영자가 Komodo에서 준비합니다.

필요한 환경 변수:

| 변수 | 의미 |
|---|---|
| `AOG_WORKER_OWNER` | 요청 사용자 이메일 |
| `AOG_WORKER_PROJECT` | 서비스 프로젝트 ID; 개인 서비스는 빈 문자열 |
| `AOG_WORKER_AUDIENCE` | 중앙에 등록한 정확한 호출 URL |
| `AOG_WORKER_PUBLIC_KEY` | 중앙 Ed25519 공개 키, base64 |
| `AOG_AGENT_PROVIDER` | codex, claude, hermes (별도 런타임 설치 필요) |
| `AOG_WORKER_ROOT` | 전용 볼륨 내부 데이터/작업 경로 |
| `AOG_WORKER_EGRESS` | 실제 네트워크 정책과 일치하는 unrestricted 또는 isolated |
| `AOG_AGENT_APPROVAL_POLICY` | 기본은 모든 네이티브 승인 거부; container는 격리 경계 내 allow_once 허용 |
| `AOG_AGENT_TIMEOUT` | 워커 호출 제한; 서비스 timeout_seconds와 맞출 것 |

네이티브 승인 요청을 웹 UI로 실시간 전달하는 기능은 아직 없습니다. 워커 기본값은 거부이며, 전용 격리 컨테이너에서만 운영자가 container 정책을 명시적으로 선택할 수 있습니다. 중앙의 그래프 승인과 네이티브 셸/도구 승인은 별개의 경계입니다.

개인 API 키는 중앙 자격 증명에 등록하여 인스턴스에 연결하거나 워커별 전용 인증 저장소를 준비합니다. 호스트의 전체 홈·SSH 키·Docker 소켓을 마운트하지 않습니다. 하네스는 새 프로세스로 실행하되 전용 HOME과 세션 작업 디렉터리를 유지합니다. 키·모델 지원은 실제 공급자 계정으로 추가 검증해야 합니다.

## 업데이트와 복구

불변 버전 태그나 digest를 사용한 별도 Komodo 스택을 `releases`에 등록합니다. 현재 실행·임대가 있으면 배포는 409로 거부됩니다. 사용자가 작업을 끝내거나 확인 가능한 취소를 수행한 뒤 다시 적용합니다. `rollback_safe`는 무상태 또는 이전 버전과 데이터가 호환됨을 검증한 경우에만 true로 둡니다.

배포 상태가 불명확하면 Komodo와 컨테이너의 실제 상태를 확인합니다. 실행 결과가 불명확한 작업을 먼저 resolve하고, 이후 서비스 reconcile을 사용합니다. 네트워크 시간 초과를 작업 실패의 증거로 간주하지 않습니다.

### 데이터 백업·복원 절차

1. 새 요청을 차단하고 실행·승인·불명확 작업을 정리합니다. 게이트웨이와 워커를 정지합니다.
2. PostgreSQL은 `pg_dump -Fc` 등 DB 자체 백업으로 보존합니다. DBOS 스키마와 AgentPort·ContextForge 테이블을 같은 일관된 시점에 포함합니다. Redis 세션은 백업 정책에 따라 보존하거나 복원 후 다시 로그인하게 합니다.
3. `gateway-data`의 파일·`worker-signing.key`, 워커별 HOME/작업/멱등 DB, `.env`의 암호화 키를 암호화된 별도 백업에 보관합니다. Komodo DB·통신 키·스택 정의도 따로 백업합니다.
4. **별도 복원 환경**에서 같은 애플리케이션 버전과 키로 DB/볼륨을 복원합니다. 데이터 파일을 실행 중 DB에 덮어쓰지 않습니다.
5. 두 사용자 로그인·권한, 자격 증명 복호화, 완료 결과, 워커 서명 검증을 확인합니다. 미완료 외부 작업은 실제 상태와 대조합니다.
6. 검증 후 운영 트래픽을 전환합니다. 이미지 rollback과 DB restore는 별도 작업입니다. 단순 이미지 복구로 이미 적용된 스키마 변경이 취소되지는 않습니다.

현재 PostgreSQL 실제 복원·DGX 배포·상태 저장 서비스의 데이터 마이그레이션 rollback은 이 환경에서 검증하지 않았습니다. 운영 도입 전 독립 복원 리허설이 필요합니다.

Hermes·NemoClaw·Paperclip 연결 범위와 설정은 [호환성 가이드](integrations.md)를 참고하세요. 기본 ACP 이미지에는 Hermes/NemoClaw가 포함되지 않습니다.

[OpenShell·Relay 실행 플랫폼 개편](runtime-platform.md)에서 새 실행 백엔드, 설치·이전 절차, 보안 경계와 검증 범위를 확인하세요.
