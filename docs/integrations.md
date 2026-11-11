# Integrations

모델 API, 실행 하네스, 배포 runtime, 협업 제품은 서로 다른 대상입니다. 아래의 template은 구현된 연결 방식이며, 실제 계정·GPU·샌드박스에서의 종단 간 검증을 대신하지 않습니다. 제품 설치와 네이티브 로그인은 운영자가 준비합니다.

## Harnesses and Model Providers

| 대상 | 구현된 연결 | 인증·프로토콜 | 검증과 제한 |
|---|---|---|---|
| Codex | 전용 ACP 워커 → codex-acp 2.6.0 | ProviderConnection Responses 또는 operator native profile | 실제 초기 ACP 협상·모의 세션/job/승인. 유료 추론 미검증 |
| Claude Code | 전용 ACP 워커 → claude-agent-acp 0.101.0 | ProviderConnection Messages 또는 native profile | 같은 범위. Responses/Chat Completions 자동 변환 없음 |
| Hermes | 별도 설치한 hermes-acp | 전용 HERMES_HOME의 native provider/login | 모의 ACP·Relay 설정 검증. 위임 MCP가 있는 세션 재개는 409로 거부 |
| Kimi Code | 별도 설치한 kimi acp | operator가 준비한 native profile | 실행 template·인증 경계 테스트. binary·계정 추론 미검증 |
| OpenCode | 별도 설치한 opencode acp | 세 API 프로토콜별 native provider 설정 또는 native profile | v1.19.5 설정 계약·모의 프로세스 검증. binary·실제 provider 미검증 |
| DeepSeek·OpenRouter·기타 모델 제공자 | 호환되는 하네스의 provider 또는 native 설정 | 해당 endpoint가 실제 지원하는 프로토콜·개인 API 키 | 제공자 이름만으로 tool calling·streaming·모델 지원을 보장하지 않음 |

Codex의 OpenAI 계정 구독 로그인과 OpenAI API 키는 다른 인증입니다. Claude 구독 로그인과 Anthropic API 키도 별개입니다. native profile은 워커에 사전 설치한 전용 HOME의 opaque 참조이고 중앙에서 토큰·브라우저 인증을 자동 추출하지 않습니다.

Chat Completions, Responses, Messages를 같은 JSON으로 취급하지 않습니다. 예를 들어 Chat Completions만 제공하는 모델 endpoint를 Codex Responses provider에 넣으면 호환되지 않습니다. OpenCode template은 프로토콜별 native AI SDK provider 설정을 생성하지만 upstream/model의 실제 도구·스트리밍 호환성은 별도로 확인해야 합니다. provider 등록 성공이 모델 호출 성공은 아닙니다.

[Worker Profiles](../workers/acp/README.md)에 인증 설정과 job 제어 경로가 있습니다. 기본 worker Dockerfile은 Codex·Claude ACP만 설치합니다. Hermes·Kimi·OpenCode는 해당 native binary가 들어간 별도 이미지를 만들어야 합니다. 중앙의 허용 도구 목록은 **중앙 MCP 호출**만 제한하고 하네스 자체의 셸·파일·플러그인 전체를 제어하지 않습니다.

## Service Connections

HTTP·Streamable HTTP MCP endpoint를 제공하는 Docker 서비스는 정의 파일로 등록할 수 있습니다. 도구 입력·출력 스키마와 원격 readiness, 필요한 인증을 명시합니다. 같은 프로세스의 여러 기능은 같은 runtime_id를 사용합니다.

Docker image만 제공하는 CLI 프로젝트에는 HTTP/MCP wrapper가 필요합니다. 긴 작업에는 job ID·상태·취소·결과 계약이 추가로 필요합니다. 단순 POST 접수 성공을 백그라운드 작업 완료로 해석하지 않습니다. 이미지 변환 예제는 짧은 순수 함수이며, ACP worker는 별도의 영속 job 계약을 구현합니다.

RAG는 원본 문서 권한을 검색 단계에서 검증하는 서비스로 연결합니다. 도구 실행 권한만으로 모든 문서 접근을 허용하지 않습니다. 개인 메모리·스킬은 하네스에 두고 공유 게시 동작은 명시적인 별도 도구로 등록합니다.

## OpenShell and NeMo Relay

OpenShell 0.1.7은 선택 실행 backend입니다. 등록한 sandbox의 조회·시작·중지와 identity 일치를 검증합니다. 파일·네트워크 정책 적용은 실제 OpenShell 환경이 담당하며 AgentPort가 정책 편집·자동 설치를 제공하지는 않습니다. native 리소스 관리 권한은 operator의 OpenShell role/workspace 설정에 따라야 합니다.

Relay는 관측·호출 미들웨어 기능을 제공하는 별도 프로젝트입니다. AgentPort에 구현된 연결은 **Hermes native Relay의 로컬 활동 요약**입니다. Relay enforcement, 중앙 단절 시 네트워크 차단, 모든 하네스의 trace, exactly-once/작업 완료 판정은 구현하지 않았습니다. [Runtime Platform](runtime-platform.md)을 참조하세요.

## NemoClaw and OpenClaw

두 제품은 기본 설치 대상이 아닙니다. NemoClaw 공식 ACP 연결은 검토한 버전에서 관리형 Hermes sandbox를 대상으로 하며 이를 모든 하네스의 공통 연결이라고 확대 해석하지 않습니다.

현재 AgentPort에는 NemoClaw 전용 launcher·파일 전송·리소스 소유권 조정 어댑터가 없습니다. OpenClaw 전용 계정/세션 어댑터도 없습니다. 각 제품이 제공하는 검증한 HTTP/MCP 기능은 일반 서비스로 등록할 수 있지만 제품 전체의 작업·세션·승인·취소를 지원한다는 뜻은 아닙니다.

전용 연동을 추가하려면 native job/session 식별자, 원격 작업공간 전송, 명확한 종료 확인, 사용자 인증 매핑을 정의해야 합니다. NemoClaw 소유 sandbox를 일반 OpenShell/Komodo backend로 우회 관리하지 않습니다. 운영자 공용 홈을 마운트하는 임시 연결도 사용자 격리의 대체가 아닙니다.

## Paperclip

Paperclip은 선택적인 별도 협업 제품입니다. 중지해도 AgentPort 로그인·다른 도구·자체 그래프는 작동합니다. [issue 생성](../examples/paperclip-issues-create.service.json)과 [issue 조회](../examples/paperclip-issues-list.service.json) 예제가 있습니다.

실제 company ID·사설 주소·사용자 본인의 Board API credential 참조로 바꿉니다. AgentPort 로그인은 Paperclip 로그인을 자동 제공하지 않습니다. 여러 사용자의 호출에 공용 관리자 키를 쓰고 권한 분리가 완료됐다고 판단하지 않습니다.

POST issue 응답은 **업무 접수 결과**입니다. Paperclip의 heartbeat·native run 완료를 기다리는 어댑터는 없습니다. HTTP 임대는 API 응답 후 끝나므로 Paperclip에는 manual/always_on을 사용합니다. 자체 업무가 있는 Paperclip 컨테이너를 AgentPort의 idle 판단으로 내리지 않습니다.

Paperclip이 특정 하네스 어댑터를 제공해도 임의의 OpenShell 환경까지 자동 지원하는 것은 아닙니다. job 완료·취소·복구·결과를 그래프에 결합하려면 별도의 durable job bridge가 필요합니다. 응답 유실 후 issue 생성을 무조건 반복하지 않습니다.

## Optional Products and References

| 제품 | 소스 조사 기준 | 현재 사용 범위 |
|---|---|---|
| [Hermes](https://github.com/NousResearch/hermes-agent/tree/e790d946d8a62020c18ffcb37acab8d215ca386b) | e790d946d8a62020c18ffcb37acab8d215ca386b | ACP/native profile·Relay 계약 참고. 소스 복사 없음 |
| [NemoClaw](https://github.com/NVIDIA/NemoClaw/tree/4d5d613f73b96d51a92e7ae88df5da5103836067) | 4d5d613f73b96d51a92e7ae88df5da5103836067 | 호환성 조사. 전용 연결 미구현 |
| [Paperclip](https://github.com/paperclipai/paperclip/tree/365c77103099c666b71fc8d4cc9ab0d4ef2081d6) | 365c77103099c666b71fc8d4cc9ab0d4ef2081d6 | 선택 JSON HTTP issue 예제 |
| [OpenCode](https://github.com/anomalyco/opencode/tree/v1.19.5) | v1.19.5 | native provider/ACP 설정 계약. 기본 이미지에 미포함 |
| [NeMo Relay](https://github.com/NVIDIA/NeMo-Relay/tree/dc00b939ae916266ce4f22ca080c9f13a40d4c48) | dc00b939ae916266ce4f22ca080c9f13a40d4c48 | Hermes 로컬 관측 설정 |
| [Windmill](https://github.com/windmill-labs/windmill/tree/f9a797281218d331cc0898fbca87e155959f5ae0) | f9a797281218d331cc0898fbca87e155959f5ae0 | UX·운영 방식 참고. 코드 복사·실행 의존성 없음 |

이는 설치 잠금 또는 실기 검증 목록이 아닙니다. 실제 잠긴 의존성은 [Reuse and Compatibility](reuse-and-compatibility.md), 실행 결과는 [Validation](validation.md)에 기록합니다. 메신저 계정 연결은 현재 제공하지 않으며 웹·native MCP를 우선합니다.
