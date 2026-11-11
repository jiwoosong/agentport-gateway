# Runtime Platform

AgentPort의 runtime은 도구 호출 주소와 분리된 **실행 환경 관리 단위**입니다. 운영자 수동 external, Komodo Docker stack, OpenShell sandbox 중 하나를 선택합니다. 동시 실행·기동·idle·업데이트 보호는 runtime ID를 기준으로 합니다.

## OpenShell Setup

선택 SDK 기준은 OpenShell 0.1.7입니다. CLI·Gateway·host sandbox runtime을 AgentPort가 자동 설치하지 않습니다. SDK를 사용하는 Core 이미지가 필요합니다. native gateway에 OpenShell SDK를 설치하지 않습니다.

```bash
uv sync --frozen --extra openshell
```

운영자가 OpenShell Gateway와 workspace/role을 준비하고 최소 권한 서비스 인증을 구성합니다. [인증 예제](../workers/acp/openshell-auth.example.json)를 참고하세요. SDK는 mTLS/TlsConfig와 별도로 client credentials 또는 operator callback으로 갱신되는 bearer token을 지원합니다. auth-only shared certificate만으로 사용자별 권한이 완성되는 것은 아닙니다.

`deploy/openshell-gateways.example.json`을 private config directory의 gateways.json으로 복사합니다. endpoint·CA·인증서·키 경로, 필요한 인증 설정을 실제 값으로 바꿉니다. UID 10001이 필요한 파일만 읽도록 설정합니다. 인증 비밀은 환경 변수 참조 또는 private 설정에 두고 저장소에 올리지 않습니다.

```bash
export AOG_OPENSHELL_CONFIG_DIR=/absolute/private/openshell-config
docker compose -f compose.yaml -f deploy/compose.openshell.yaml up -d --build
```

이 override는 Core에 SDK와 읽기 전용 인증 마운트만 추가합니다. sandbox 생성·정책 설치·host Docker 소켓 전달·ingress 설정은 수행하지 않습니다. OpenShell 운영 identity는 중앙 서비스의 관리 권한이며 요청 사용자의 native OpenShell 계정이 아닙니다.

## Sandbox and Worker

1. 사용자 × 프로젝트 × 하네스별 sandbox와 영속 워커 볼륨을 준비합니다.
2. 그 안에 ACP 워커와 선택한 하네스를 설치합니다. 기본 worker 이미지에는 Codex/Claude 어댑터만 있습니다.
3. `uvicorn aog.worker:app --host 0.0.0.0 --port 8090 --workers 1`을 실행하고 중앙 공개 키·소유자·프로젝트·audience를 고정합니다.
4. 사설 ingress에서 X-AOG-Context와 Idempotency-Key를 보존합니다. ingress 인증과 모델 제공자 인증을 같은 헤더로 혼용하지 않습니다.
5. Runtimes에 backend openshell과 `{gateway, workspace, sandbox, sandbox_id}`를 등록하고 worker HTTP readiness를 지정합니다.
6. 해당 runtime에 정확히 하나의 ACP worker 도구를 연결한 뒤 agent profile을 만듭니다. 처음에는 manual에서 신규 세션을 검증합니다.

같은 native target을 다른 runtime으로 중복 등록할 수 없습니다. OpenShell native ID가 바뀌면 기존 이름만 같다고 같은 sandbox로 사용하지 않습니다. OpenShell start/stop/status는 구현되어 있지만 **update는 미지원**입니다. 검증한 대체 sandbox를 준비하고 활성 작업이 없는 상태에서 binding을 변경합니다.

on_demand는 모든 업무를 중앙 임대로 추적하는 전용 환경에만 사용합니다. Hermes daemon·Paperclip heartbeat·cron·별도 ingress 작업을 중앙 임대가 인지하지 못하면 manual/always_on을 사용합니다. 중앙 종료가 원격 업무 종료를 뜻하지 않습니다.

## Network and Files

OpenShell의 정책 적용과 AgentPort의 local_only 판단은 서로 다른 계층입니다. egress_policy isolated는 외부 DNS·HTTPS·간접 프록시·native 도구의 외부 통신이 실제 차단된 경우에만 지정합니다. 클라우드 모델 하네스는 보통 unrestricted입니다.

호스트 HOME·SSH 키·Docker 소켓을 sandbox에 넣지 않습니다. native 로그인은 전용 프로필에, 작업 파일은 전용 workspace에 둡니다. 중앙은 명시적으로 선택한 파일만 전송·회수합니다. 원격 cwd와 로컬 cwd를 같은 경로 문자열로 취급하지 않습니다.

AgentPort 단절 시 원격 에이전트의 모든 새 통신을 자동 차단하는 실행 허가 갱신/네트워크 revocation은 구현하지 않았습니다. OpenShell supervisor 연결 보장과 AgentPort 연결 상태는 다릅니다. Relay 기록이 이 보장을 대신하지 않습니다.

## Hermes Relay Observation

Hermes에 native Relay 지원이 있는 버전을 별도 설치하고 `AOG_AGENT_PROVIDER=hermes`, `AOG_RELAY_ENABLED=1`을 설정합니다. 전용 HERMES_HOME과 worker root를 사용합니다. 워커가 telemetry/plugins.toml을 생성하고 HERMES_NEMO_RELAY_PLUGINS_TOML로 전달합니다.

Relay는 로컬 파일 sink, full payload 비활성으로 설정합니다. 중앙에는 agent/turn/tool/llm 시작·종료 개수만 전달하고 raw 이벤트·인자·모델 응답을 보내지 않습니다. 로컬 로그에도 경로 등 민감정보가 있을 수 있으므로 디스크 할당량·보존/회전·접근권한은 별도 설정합니다.

집계는 워커 호출 시간 구간의 관찰입니다. 지연 flush·백그라운드 이벤트가 섞일 수 있으며 정확한 비용·작업 완료·완전한 trace 연결을 보장하지 않습니다. 로그가 없으면 available false로 표시합니다. native session/job correlation은 식별용이며 검증된 trace link 증명이 아닙니다.

현재 Relay 연결은 Hermes 관측만 제공합니다. enforce 요청은 거부합니다. Codex·Claude·Kimi·OpenCode에 같은 플래그를 켜서 지원을 확대하지 않습니다. Hermes 전용 daemon이 이미 떠 있다면 업무 정리 후 해당 daemon을 재시작해야 설정을 적용·해제할 수 있습니다.

## Validation Boundary

실제 SDK/protobuf를 사용하는 모의 gRPC로 get/start/stop/wait 계약과 target ID 검사를 검증합니다. ACP·native approval·durable job은 모의 프로세스 테스트입니다. Relay는 TOML/ATOF와 payload 제외를 검증합니다.

실제 OpenShell Gateway 인증·sandbox·네트워크 차단·Hermes 모델 추론·Relay 이벤트 생성·DGX GPU 배치는 별도 실기 검증이 필요합니다. NemoClaw 소유 리소스에는 그 제품의 native 관리 계약이 필요하며 일반 OpenShell backend로 우회하지 않습니다.

공식 기준: [OpenShell 0.1.7 SDK](https://github.com/NVIDIA/OpenShell/blob/v0.1.7/docs/sdk/python.mdx), [Relay Hermes 계약](https://github.com/NVIDIA/NeMo-Relay/blob/dc00b939ae916266ce4f22ca080c9f13a40d4c48/docs/getting-started/quick-start/hermes.mdx). SDK와 설정 계약을 사용하며 이 두 제품의 엔진 소스를 복사하지 않습니다.
