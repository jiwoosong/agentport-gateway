# Validation

## Separate Components Verification

현재 구성은 Core와 gateway를 분리한 구조입니다. 아래 단일 앱 재구성·과거 측정 결과를 새 구성의 종단 간 검증으로 합산하지 않습니다.

- Python 3.13.18 전체 회귀: **242 passed**, 40.39초. 운영자 `.env`가 없는 임시 디렉터리에서도 통과했으며 테스트 전용 키는 매번 생성합니다. 두 프로세스 HTTP/MCP·두 사용자 격리·Core 강제 종료 복구·실제 ACP 테스트 프로세스·승인/취소/불명확 작업 정리·이전·설정 변경 잠금·서명 bridge·토큰 IP/시간/사용량 제한을 포함합니다. schema2의 Responses 연결·native 로그인과 빈 팀 범위의 비공개 Core 데이터 차단도 포함합니다. 모델 공급자의 유료 추론은 사용하지 않았습니다.
- 준비 스크립트로 재현한 gateway의 선택 상류 회귀: **852 passed, 9 skipped**, 14.93초. RBAC·권한 축소·토큰 카탈로그·도구·bootstrap 대상입니다.
- Core는 Python 3.13과 3.14 모두에서 upstream 소스와 `mcpgateway` 패키지가 없는 상태로 frozen 설치·앱 import를 통과했습니다. 설치된 다른 gateway entry point를 선택하는 검사도 포함합니다.
- ContextForge가 설치되지 않은 Python 3.14 Core 환경의 선택 회귀는 **36 passed, 1 skipped**입니다.

- BFF proxy/config 집중 검증: 41 passed. Core 전용 경로 분기·native API 유지·Bearer/CSRF·redirect·내부 header 차단. 전체 BFF 회귀는 26개 파일·291 tests passed, TypeScript build 통과.
- Compose: demo/image-demo/managed/https 및 Core용 OpenShell override config 통과. 초기화의 private bridge key·.env0600·기존 값 보존과 Core에 native JWT/encryption master가 없는 구성도 확인.
- GitHub Actions Ubuntu amd64에서 분리된 Core·ContextForge connector·대시보드 이미지 빌드와 PostgreSQL·Redis 기동을 확인했습니다. 실제 로그인 → native 경유 작업 제출 → DBOS 실행 → BFF 사진 입력(1 MiB 초과 PNG) → WebP 결과 인출·해시·크기 검증이 Paperclip 없이 통과했습니다. [Compose 실행 기록](https://github.com/jiwoosong/agentport-gateway/actions/runs/42952018595/job/130298286353). 로컬 Docker daemon은 접근 제한이 있으며, 이 CI 결과가 DGX·GPU·Komodo 검증을 대신하지 않습니다.

로컬 vLLM 검증은 운영자가 서비스 업데이트를 위해 중지한 상태여서 보류했습니다. 실기·공급자 추론·업데이트 복원을 이 자동 테스트 결과에 포함하지 않습니다. Python schema sandbox fork 및 상류 ToolPreInvokePayload header의 deprecation 경고가 남습니다.

## 2026-11-11 Native Gateway Rebuild Before Component Split

아래는 이번 구조의 검증 범위입니다. 뒤의 2026-11-09/10 결과·스크린샷·메모리/기동 측정은 **이전 코드의 이력**이며 현재 포크의 성능 수치나 전체 회귀 결과로 사용하지 않습니다.

| 검증 | 결과와 범위 |
|---|---|
| ContextForge 포크 원본 회귀 선택 실행 | 852 passed, 9 skipped. native RBAC·TokenCatalog·ToolService·bootstrap. upstream 전체 테스트라는 뜻은 아님 |
| AgentPort 서버 회귀 | 68 passed, 2 skipped 시점의 결과. native catalog·내부 backend guard·위임 범위·공유 runtime·provider/profile·worker job/승인/취소·이미지 계약 포함. 후속 추가 회귀는 별도 기록 |
| 실제 native HTTP 통합 | 로그인·두 사용자 격리·native MCP/REST 도구·process restart·완료 결과 재사용 경로 통과. 모델·원격 장치 대신 로컬 stand-in 사용 |
| 이미지 예제 | 4 passed. PNG/JPEG → WebP bytes/hash, 잘못된 입력·크기·decoded pixel 제한, EXIF 방향과 metadata 제외 |
| AgentPort UI 집중 검증 | 13 passed, 3 files. runtime/provider/profile 폼·JSON definition import·파일 입력·native 승인·binary 결과 다운로드 |
| Dashboard 전체 회귀 | 230 test files passed, 3,803 tests passed·1 skipped, 150.32초. 이번 코드의 전체 UI 회귀 결과 |
| UI 빌드·정적 검사 | TypeScript·Vite production build, 변경한 AgentPort UI ESLint 통과. 기존 vendor chunk/Zod annotation 경고 유지 |
| BFF | 287 passed, 26 files와 빌드 통과 |
| Compose | image-demo config 검증. 현재 로컬 환경은 Docker daemon 접근 거부로 실제 이미지 build/run 미실행 |

후속 집중 검증: session 준비 실패 시 소유 lease만 해제하고 기존 다른 작업의 lease는 보존하는 회귀, 삭제/비활성 API token의 queued principal 실행 거부를 포함한 contracts/native delegation 22개 통과. schema2 review snapshot·정확한 공개 입력·cloud worker 경계 6개, 설정 변경 claim 5개 및 기존 runtime 6개 통과.

재시작 bootstrap이 owned private backend/control을 public으로 바꾸지 않는 경계를 보정하고, 반복 bootstrap 및 실제 프로세스 재시작으로 확인했습니다. native REST mutation의 자동 재시도는 끄고 응답 유실은 한 번의 외부 시도 이후 확인 대상으로 남깁니다. native MCP/RPC 경로가 lifecycle·승인 단계를 건너뛰지 않는 테스트를 포함합니다.

실제 DGX Spark·원격 PC GPU·Komodo Periphery·OpenShell 인증/네트워크 정책·유료 Codex/Claude 추론·Kimi/OpenCode/Hermes binary·Relay native 이벤트는 이번 검증에 포함되지 않습니다. PostgreSQL 실제 복원과 schema 변경 rollback은 별도 리허설이 필요합니다. 테스트 template/모의 프로세스를 제품 실기 검증으로 표시하지 않습니다.

## Historical Results

초기 작업 환경: 2026-11-09, Linux amd64, Python 3.14.4, Node.js 24.22.2. 이 문서는 코드 구현, 모의 계약 검증, 실제 외부 통합 검증을 구분합니다. CI 결과가 환경별로 다르면 해당 실행 결과를 우선합니다.

## 실행한 검증

| 검증 | 결과·범위 |
|---|---|
| Python 전체 | **18 통과**. private/project 격리, 실행 비공개, 순환/참조 거부, 동시 임대, 단일 기동, 사용 중 중지 금지, 권한 선확인, 응답 유실 시 재실행 금지, 정확한 공개 입력, 업데이트 복구 순서, 파일 경로/해시, ACP wire 세션·재개·멱등성 |
| 실제 DBOS 그래프 | 모의 에이전트로 작성→검수→수정 3회, 작성자 세션 재개, 영구 거절 시 반복 한도 종료 |
| 실제 HTTP 통합 | ContextForge 로그인, 파일 변환, 멱등 요청, 병렬·합류·승인, 두 사용자 격리, 파일 접근 차단, 운영자 권한 |
| 강제 종료·재시작 | 승인 대기 중 gateway SIGKILL 후 복구, 완료된 변환 호출 횟수 불변 |
| 데이터 경계 | 로컬 선택→정확한 승인 payload→외부 HTTP stand-in, 비선택 원문이 결과/로그에 없음, 그래프 리비전 고정, 승인 대기 취소 |
| 실제 MCP SDK | Streamable HTTP 초기 연결·도구 호출, 위임 토큰의 도구 범위·호출 예산·멱등 재요청, 부모 기록 및 관리 REST 차단, Paperclip 없는 상태 |
| 실제 ACP 패키지 | Codex ACP 2.6.0, Claude ACP 0.101.0: protocol 1 초기 연결과 loadSession 지원 응답. 유료 추론은 호출하지 않음 |
| 대시보드 회귀 | 228개 테스트 파일, **3,792 통과·1 스킵**. 새 서비스 폼·그래프 저장 테스트 포함 |
| 로그인 BFF 회귀 | **287 통과** |
| 빌드 | TypeScript·Vite UI, Fastify BFF 빌드 통과 |
| npm 감사 | UI·BFF·ACP 워커 잠금 파일에서 알려진 취약점 **0건**. 감사 시점의 DB 기준이며 무취약성 보장이 아님 |
| Compose 정적 검증 | 공식 Compose 5.7.0으로 demo·managed·https profile의 config 검증 통과. Docker 데몬 실행 검증과는 별개 |
| 실제 Docker Compose | GitHub Actions Ubuntu amd64에서 demo 이미지 빌드·PostgreSQL 18·Redis 8 기동, 실제 로그인·DBOS 변환, BFF 로그인·세션·완료 결과 조회 통과. Paperclip 없이 실행. [실행 기록](https://github.com/jiwoosong/agentport-gateway/actions/runs/42542531228) |
| 브라우저 | 실제 Chromium에서 로그인→텍스트 파일 업로드→비동기 실행→CSV 다운로드, 독립 모바일 세션 로그인→같은 작업 조회, 390 px 폭 가로 넘침 없음 |

모의 Komodo 테스트는 Docker의 실제 컨테이너 생성·중지·이미지 복구를 검증한 것이 아닙니다. 외부 HTTP stand-in은 클라우드 공급자나 법률 자료 처리의 정확성을 검증하지 않습니다. 실제 ACP 초기 연결도 모델 응답·프로젝트 코딩의 성공을 의미하지 않습니다.

첫 Compose CI에서 내부 전용 네트워크에만 연결된 대시보드의 호스트 포트가 게시되지 않는 문제가 발견됐습니다. 웹 진입점에 일반 `frontend` 브리지를 추가한 뒤 실제 호스트 접속과 BFF 경로가 통과했습니다. HTTPS profile의 인증서·원격 모바일 접속과 Komodo 수명주기는 이 demo CI의 검증 범위가 아닙니다.

## 화면과 자원 측정

[데스크톱 화면](screenshots/desktop.png), [모바일 화면](screenshots/mobile.png)은 테스트 계정과 예제 데이터로 만든 실제 화면입니다. [브라우저 검증 측정값](screenshots/smoke-metrics.json)을 함께 기록합니다.

최근 단일 측정에서 gateway 준비까지 약 **3.8초**, gateway 프로세스 최대 RSS 약 **405 MiB**였습니다. SQLite 개발 환경의 한 번 측정이며 BFF·PostgreSQL·Redis·Komodo·브라우저·모델 메모리는 제외합니다. 전체 스택이 수백 MB 미만이거나 DGX에서 같은 성능이라고 주장하지 않습니다. 처리량·장시간 안정성·다중 장치 지연 벤치마크는 아직 없습니다.

## 미검증 / 다음 배포 검증

- 실제 Docker/Komodo 단일 기동·유휴 종료·무상태 이미지 rollback.
- PostgreSQL 운영 데이터 백업·별도 환경 복원 리허설, 상태 저장 서비스 스키마 복구.
- DGX Spark arm64 이미지·GPU 런타임·장치 간 사설 네트워크.
- 실제 Codex·Claude 계정의 코드 작성·검수·모델 설정·네이티브 승인 정책.
- Paperclip·RAG 문서 ACL·Hermes/OpenClaw/Kimi 전용 어댑터 및 메신저 계정 연결.
- 호스트 방화벽을 포함한 local-only 외부 유출 차단, 침투 테스트, 적대적 다중 고객 격리.
- 대용량 이미지·영상의 업로드·진행 이벤트·객체 저장소, 다중 게이트웨이 복제.

## 재현

```bash
uv sync --frozen --extra dev
uv run ruff check --config pyproject.toml src tests scripts examples
uv run --no-sync pytest -q tests/test_core*.py tests/test_worker.py tests/test_worker_jobs.py tests/test_contextforge_source.py
```

ContextForge 연결과 전체 서버 회귀는 Python 3.13 환경에서 실행합니다.

```bash
python scripts/prepare-contextforge.py
uv sync --python 3.13 --frozen --extra contextforge --extra openshell --extra dev
AOG_INTEGRATION=1 AOG_OPENSHELL_WIRE=1 uv run --no-sync pytest -q
```

```bash
cd dashboard
npm ci --ignore-scripts
npm run build
npm test -- --maxWorkers=2
npm audit
cd server
npm ci --ignore-scripts
npm run build
npm run test:run
npm audit
```

루트에서 브라우저 테스트:

```bash
cd dashboard
npx playwright install chromium
cd ..
node scripts/browser-smoke.mjs
```

모든 실제 HTTP/브라우저 테스트에는 로컬 소켓 접근이 필요합니다. 제한된 실행 샌드박스에서 소켓이 금지되어 있으면 일반 개발 환경에서 실행하세요. 테스트는 임시 DB·계정·포트를 사용하고 자신이 시작한 프로세스만 종료합니다. `.github/workflows/ci.yml`이 공개 저장소에서도 검증을 반복합니다.

## 2026-11-10 실행 플랫폼 개편

- OpenShell SDK 0.1.7 선택 의존성 설치와 잠금 검증. 실제 SDK·protobuf를 사용하는 로컬 모의 gRPC 서버로 조회·시작·중지·상태 대기 검증.
- 전체 테스트: `AOG_INTEGRATION=1 AOG_OPENSHELL_WIRE=1 pytest -q` → **28 passed, 17.56초**. 새 binding 중복·ID 변경·불명확 mutation·활성 작업 보호·Relay 원문 제외 및 기존 로그인/MCP/두 사용자 격리/프로세스 강제 종료 복구 포함.
- 대시보드 프로덕션 빌드 통과. AgentPort 화면 테스트 4개 통과 (일반 사용자·그래프·OpenShell 제어·Relay 요약). 기존 vendor chunk/annotation 빌드 경고는 남아 있음.
- Ruff 및 diff 공백 검사 통과. OpenShell Compose override 구문 검증 통과. 실제 Docker 이미지 빌드는 이 로컬 환경에서 수행하지 않음.
- 제한 환경에서 thread/DBOS 테스트가 멈추던 문제는 일반 로컬 실행으로 재검증하여 통과 확인. 제품 로직 실패로 재현되지 않음.
- 미검증: 실제 OpenShell Gateway mTLS/샌드박스/네트워크 정책, Hermes 모델+Relay 이벤트 생성, DGX/GPU, native 취소 및 AgentPort 단절 시 외부 호출 차단. 모의 프로토콜 통과를 이 항목의 지원 보장으로 사용하지 않음.

## 2026-11-10 문서 정리와 리팩터링

- 기존 테스트 수정 없이 `AOG_INTEGRATION=1 AOG_OPENSHELL_WIRE=1 pytest -q`: **28 passed, 17.80초**.
- AgentPort UI 테스트 4개와 프로덕션 빌드 통과. 변경한 UI 파일 ESLint(경고 없음)·Prettier, Python Ruff 및 문서 내부 파일 링크 검사 통과.
- DB 스키마, API 계약, DBOS 단계 순서는 변경하지 않았습니다. 실제 장비 지원 범위를 확대하는 변경이 아닙니다.
- [코드 검토 기록](code-review.md)에 범위와 구조 변경을 설명합니다.
