# Hermes · NemoClaw · Paperclip 호환성

2026-11-10 소스 조사 기준입니다. **프로토콜 연결, 제품 실기 검증, 운영 수명주기 지원은 별도입니다.** 세 제품은 필수 의존성이 아니며 기본 Compose에 설치되지 않습니다.

| 제품 | AgentPort의 연결 경로 | 현재 검증 | 아직 제공하지 않는 기능 |
|---|---|---|---|
| Hermes | 전용 ACP 워커 → `hermes-acp` → Hermes 네이티브 런타임 | 실행 프로필, 전용 HOME, 세션 재개, 중복 호출 억제의 모의 ACP 테스트 | 실제 모델 추론, 위임 MCP 도구가 있는 세션 재개, 원격 취소 전파 |
| NemoClaw | 공식 `nemoclaw-acp --sandbox … --gateway …`로 관리형 Hermes 샌드박스 연결 가능 | 공식 소스의 연결 계약 확인; AgentPort 실행 프로필은 미구현 | 원격 작업 디렉터리/파일 전송, 중앙 수명주기 제어, 실기 검증; 모든 에이전트 지원 아님 |
| Paperclip | 기존 JSON HTTP transport → 회사별 업무 API | 생성/조회 정의 파일 및 모의 HTTP 계약 테스트 | 업무 완료까지 자동 대기, 네이티브 run 취소/복구, SSO·사용자 자동 동기화 |

## Hermes 연결

1. 운영자가 Hermes와 ACP 기능을 설치한 별도 워커 환경을 준비합니다. 기본 `deploy/worker.Dockerfile`은 Codex/Claude용이며 Hermes를 포함하지 않습니다. 검토한 소스 커밋은 아래 표에 있습니다. 설치된 `hermes-acp`가 워커 PATH에서 실행 가능해야 합니다.
2. 기존 [ACP 워커 설정](deployment.md#acp-워커)의 소유자, 프로젝트, audience, 중앙 공개 키를 설정하고 `AOG_AGENT_PROVIDER=hermes`를 지정합니다. `uvicorn aog.worker:app --host 0.0.0.0 --port 8080`으로 워커를 실행합니다. 공개 인터넷에 워커를 직접 노출하지 않습니다.
3. 워커 전용 `$AOG_WORKER_ROOT/home/.hermes`에 Hermes 프로필·모델 설정·인증을 준비합니다. 자식 프로세스의 `HERMES_HOME`은 이 경로로 고정됩니다. 호스트 전체 HOME이나 운영자 공용 프로필을 연결하지 않습니다. 프로필의 네이티브 도구·MCP·외부 통신 정책도 운영자가 제한해야 합니다.
4. `kind=agent`, `transport=acp`, `endpoint=http://hermes-worker:8080/invoke`, `lifecycle=manual`로 서비스를 등록하고 해당 서비스에 에이전트 인스턴스를 연결합니다. timeout은 워커 실행 제한과 맞춥니다. 중앙 `credential_ref`는 비워 둡니다. Hermes는 여러 공급자를 지원하므로 중앙의 일반 Bearer 값을 임의의 공급자 API 키로 해석하지 않고 거부합니다.
5. 신규 세션부터 검증합니다. 모델 선택은 Hermes가 제공하는 모델 ID를 사용합니다. MCP 도구가 없는 세션의 재개는 ACP `loadSession` 협상에 따릅니다. Hermes는 재개 시 기존 MCP 자격 증명의 fingerprint를 확인하지만 AgentPort는 위임 토큰을 갱신하므로, **위임 도구를 가진 Hermes 세션 재개는 현재 409로 거부**합니다. 세션을 새로 생성하거나 추후 안전한 토큰 갱신 계약을 구현해야 합니다.

사용자·프로젝트·에이전트마다 전용 워커/프로필을 사용합니다. 중앙의 `allowed_service_ids`는 중앙 MCP 호출 범위이며 Hermes 자체 셸·플러그인 권한 전체를 제한하는 설정이 아닙니다. `isolated` 표시는 실제 네트워크 격리가 설치된 경우에만 사용합니다. 네이티브 런타임 종료와 ACP 클라이언트 종료도 동일하지 않습니다.

## NemoClaw 연결 범위

검토한 버전에는 공식 `nemoclaw-acp`가 있습니다. 이 연결은 **등록된 관리형 Hermes 샌드박스**와 OpenShell/NemoClaw 버전·샌드박스 식별 정보를 검증합니다. 따라서 NemoClaw를 일반 OpenAI API나 임의의 Docker 컨테이너로 취급하지 않습니다. OpenClaw 등 다른 샌드박스 에이전트에도 동일한 ACP 경로가 있다고 가정하지 않습니다.

AgentPort에 추가할 최소 범위는 다음과 같습니다.

- 운영자가 고정한 gateway/sandbox를 선택하는 ACP 실행 프로필. 사용자의 임의 CLI 명령은 받지 않습니다.
- 로컬 워커와 원격 샌드박스의 작업 디렉터리 매핑 및 명시적 파일 전송. 이 계약이 없으면 기존 workspace snapshot 기능을 거부해야 합니다.
- OpenShell/NemoClaw 공식 인터페이스를 통한 상태 조회·시작·중지·복구. Komodo로 내부 컨테이너를 직접 종료하는 방식은 사용하지 않습니다.
- 샌드박스의 네트워크 정책과 중앙 MCP 접근 범위를 함께 검증하고, 중단된 SSH/ACP 연결을 작업 종료의 증거로 간주하지 않습니다.

현재는 소스 호환성 조사까지이며 실행 프로필·배포 예제는 제공하지 않습니다. 운영자 홈/샌드박스 인증을 재사용하는 임시 우회 연결도 만들지 않았습니다. 실제 NemoClaw 설치 환경에서 위 항목을 검증한 뒤 지원 상태를 올립니다.

## Paperclip 연결

`examples/paperclip-issues-create.service.json`과 `paperclip-issues-list.service.json`은 기존 transport로 등록 가능한 예제입니다. 주소와 `REPLACE_COMPANY_ID`를 실제 회사 ID로 바꾸고, 개인 자격 증명에 해당 사용자의 Paperclip Board API 키를 저장하고, `REPLACE_CREDENTIAL_RESOURCE_ID`를 생성된 자격 증명 리소스 ID로 바꿉니다. 예제는 비공개이며 인증된 Paperclip 배포를 전제로 합니다. `local_trusted`를 여러 사용자용 권한 경계로 쓰지 않습니다.

- 생성: `POST /api/companies/{companyId}/issues`, 입력 `{"title":"모델 검수","description":"결과를 확인해주세요"}`. 반환된 issue는 **접수 결과**이며 작업 완료 결과가 아닙니다.
- 조회: `GET /api/companies/{companyId}/issues`, 선택적 status 쿼리. 응답·검색 범위는 Paperclip API 키의 권한을 따릅니다.
- AgentPort 로그인은 Paperclip UI 로그인으로 자동 이어지지 않습니다. 공용 관리자 API 키를 모든 사용자에게 공유하지 않습니다.
- 중앙의 HTTP 호출 임대는 API 응답 시 끝납니다. 이후 Paperclip 업무나 heartbeat run은 계속될 수 있으므로 Paperclip에는 현재 `on_demand`/유휴 자동 종료를 사용하지 않습니다. 상시 운영 또는 운영자 관리 `manual`을 사용합니다.
- issue/run ID를 저장하고 상태 조회·취소 확인·결과 회수까지 처리하는 전용 비동기 어댑터가 있어야 그래프의 “업무 완료” 노드로 사용할 수 있습니다. 현재 이를 구현했다고 주장하지 않습니다. 생성 요청의 응답이 유실되면 중복 생성 대신 확인 대상으로 남겨야 합니다. Idempotency-Key 헤더를 보낸다고 상류가 중복 제거를 보장하는 것은 아닙니다.

## 연결 단절과 복구의 공통 경계

현재 AgentPort는 불명확한 외부 실행을 무조건 재실행하지 않습니다. 그러나 중앙이 꺼졌을 때 세 제품의 새 외부 통신을 자동 차단하거나 원격 작업을 취소하는 기능은 없습니다. 이를 보장하려면 원격 실행 허가의 만료/갱신과 실제 네트워크 차단, 원격 작업 ID 기반 상태 대조를 함께 구현해야 합니다. 모델 API 타임아웃이나 컨테이너 정지만으로 정확한 중단·재개를 보장하지 않습니다.

## 조사 기준과 출처

상류 코드를 복사하거나 제품을 설치하지 않았습니다. 아래는 조사 기준 커밋이며 설치 잠금/실기 검증 버전이라는 뜻은 아닙니다.

| 제품 | 소스 기준 | 사용한 계약 |
|---|---|---|
| [Hermes](https://github.com/NousResearch/hermes-agent/tree/e790d946d8a62020c18ffcb37acab8d215ca386b) | `e790d946d8a62020c18ffcb37acab8d215ca386b` | `acp_adapter/entry.py`, `gateway_server.py`, `gateway_session_meta.py` |
| [NemoClaw](https://github.com/NVIDIA/NemoClaw/tree/4d5d613f73b96d51a92e7ae88df5da5103836067) | `4d5d613f73b96d51a92e7ae88df5da5103836067` | `src/lib/acp/command.ts`, OpenShell Hermes ACP SSH transport, lifecycle 문서 |
| [Paperclip](https://github.com/paperclipai/paperclip/tree/365c77103099c666b71fc8d4cc9ab0d4ef2081d6) | `365c77103099c666b71fc8d4cc9ab0d4ef2081d6` | `cli/src/commands/client/issue.ts`, `run.ts` |

공식 문서: [Hermes ACP](https://hermes-agent.nousresearch.com/docs/user-guide/features/acp/), [NemoClaw](https://docs.nvidia.com/nemoclaw/latest/), [Paperclip 인증](https://docs.paperclip.ing/reference/api/authentication/).

## 이번 변경의 검증 기록

- `pytest -q tests/test_contracts.py tests/test_worker.py tests/test_integrations.py`: 20 passed.
- 변경한 Python 파일의 Ruff 검사 및 포맷 검사 통과.
- 앞선 제한 환경의 DBOS 테스트 중단은 후속 로컬 실행에서 재검증했습니다. 최신 결과는 [검증 기록](validation.md)을 참조하세요.
- 세 제품을 실제 설치하거나 모델 API를 호출하지 않았습니다. Paperclip HTTP와 Hermes ACP는 모의 상대 프로세스/서버 계약 테스트입니다.

OpenShell 독립 실행 백엔드와 Hermes Relay 관찰 지원은 [실행 플랫폼 개편](runtime-platform.md)을 참고하세요. NemoClaw 전용 관리 연동과는 별개입니다.
