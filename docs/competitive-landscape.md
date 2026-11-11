# Competitive Landscape

> 이 문서는 당시 조사·변경의 기록입니다. 현재 구성·지원 범위·호출 경로는 [Architecture](architecture.md), [Integrations](integrations.md), [Validation](validation.md)을 기준으로 합니다.

조사일: 2026-11-10. AgentPort 기준: `5f27f60`. 아래 비교는 공식 저장소·문서와 현재 AgentPort 코드·검증 기록을 대조한 결과입니다. 경쟁 제품을 직접 설치해 비교한 성능·사용성 시험은 아닙니다. 공식 문서에 없다는 이유만으로 기능이 없다고 판정하지 않습니다. 개발 브랜치의 설명과 실제 릴리스·배포판의 지원 범위도 다를 수 있습니다.

## Assessment

**AgentPort의 가능성은 여러 장치의 기존 에이전트와 일반 서비스를 작은 팀이 함께 사용하는 운영 경험에 있습니다. 현재 경쟁 우위가 입증된 상태는 아닙니다.**

MCP, 다중 에이전트, 승인, 세션, 그래프, 자체 호스팅은 이미 여러 구현체가 제공합니다. 로그인과 MCP 중계는 우리가 재사용하는 ContextForge에도 있습니다. 이 기능들의 존재만으로 AgentPort를 선택할 이유를 설명하기는 어렵습니다.

목표 사용자는 로컬 PC·GPU 서버와 클라우드 계정을 함께 운영하는 소규모 팀입니다. 개발자는 기존 도구를 등록하고, 일반 사용자는 실행 장치나 컨테이너를 몰라도 요청과 결과를 다룰 수 있어야 합니다. 서비스 기동·권한·세션·결과·장애 복구를 이 사용 경로에서 일관되게 연결하는 것이 차별화 후보입니다.

## Comparable Projects

| 프로젝트 | 공식 구현의 중심과 겹치는 기능 | 기술 스택·구현 방식 | AgentPort 관점의 판단 |
|---|---|---|---|
| [Agno / AgentOS](https://github.com/agno-agi/agno/blob/abedb7b046561fb38b5a400420c84f905a364320/README.md) | 에이전트·팀·워크플로우, API/MCP, 세션, 권한, 승인, durable execution. Claude·Codex 하네스도 지원 | Python SDK와 FastAPI 실행 서버. 자체 호스팅 런타임에 관리 UI 연결 | 가장 직접적인 비교 대상 중 하나. 기존 하네스를 보존한다는 설명만으로는 차별화되지 않음 |
| [Windmill](https://github.com/windmill-labs/windmill/blob/466885fd26b74bd20de67ee0ef0e55a47826cd47/README.md) | 스크립트·워크플로우를 폼·앱·API·MCP로 제공. 작업 큐, 승인, 작업자 분배, 배포 관리 | Rust 서버·워커, PostgreSQL 큐, Svelte UI. 코드와 시각적 플로우를 함께 사용 | 일반 사용자에게 기능을 제공하는 경로와 분산 실행을 비교할 핵심 대상. 단순한 자동화 도구로 축소해서 보면 안 됨 |
| [Paperclip](https://github.com/paperclipai/paperclip/blob/93faca77efb58e1408ce82c38d169edbdd900bbb/README.md) | 여러 하네스, 작업·조직·예산·승인, 연결 권한, MCP, 결과물, 실행 환경 | Node.js/TypeScript 서버와 React UI. 작업·조직과 어댑터 중심 | 협업 영역에서는 직접 경쟁하며 선택적 연동 대상이기도 함. 현재는 단순한 작업 보드보다 범위가 넓음 |
| [Dify](https://github.com/langgenius/dify/blob/8fb0e23773ee3091e4a9f1586a528df945dcafa8/README.md) | 시각적 AI 앱·워크플로우, RAG, 모델·도구 연결, 에이전트 샌드박스, 앱 API | Python/Flask·Celery 백엔드, React/Next.js 웹, 별도 실행·저장 구성 | AI 앱 제작과 RAG를 빠르게 제공할 때 유력한 대안. AgentPort 안에서 자체 RAG 제작기를 다시 만들 이유는 약함 |
| [agentgateway](https://github.com/agentgateway/agentgateway/blob/a332d7f8739e57d5c77d32e52ebd757df261361a/README.md) | LLM·MCP·A2A 프록시, 인증·정책·관측, 추론 라우팅. 관리 UI도 제공 | 프록시와 선언형 정책 중심, standalone 또는 Kubernetes 배포 | 통신 게이트웨이 영역의 직접 비교 대상. 빠른 중계·정책 기능 자체를 우리의 고유 기술로 주장할 수 없음 |
| [ContextForge v1.1.0](https://github.com/IBM/mcp-context-forge/tree/v1.1.0) | MCP·API 통합, 레지스트리, 인증·권한, 관리 화면 | Python 게이트웨이와 별도 Web UI. AgentPort가 실제 재사용하는 기반 | 비교 기준이자 의존성. 그 위에 추가한 서비스 운영·작업 실행·사용자 경험이 AgentPort의 가치를 설명해야 함 |

Agno의 [하네스 서비스 가이드](https://docs.agno.com/tutorials/harnesses-as-a-service)는 Claude·Codex의 실행, 스트리밍, 세션과 백그라운드 작업을 명시합니다. [런타임](https://docs.agno.com/agent-os/overview)과 [권한 문서](https://docs.agno.com/agent-os/security/overview)도 공개되어 있습니다. Agno를 자체 에이전트 SDK만 지원하는 프로젝트로 비교하지 않습니다. 런타임 저장소의 공개 라이선스와 관리 UI의 배포·이용 조건은 별도로 확인해야 합니다.

Windmill의 [MCP 문서](https://www.windmill.dev/docs/core_concepts/mcp)는 작업과 플로우의 도구 노출을, [worker groups](https://www.windmill.dev/docs/core_concepts/worker_groups)는 태그 기반 분배와 배포판별 차이를 설명합니다. 기본 그룹·태그 편집과 고급 기능의 범위가 같지 않으며 autoscaling·dedicated workers 등은 Enterprise 기능으로 안내합니다. [권한 문서](https://www.windmill.dev/docs/core_concepts/roles_and_permissions)에도 기본 역할과 Enterprise 기능의 구분이 있습니다. 모든 기능을 무료 배포판의 지원으로 합산하지 않습니다.

Dify의 스택은 [Python 의존성](https://github.com/langgenius/dify/blob/8fb0e23773ee3091e4a9f1586a528df945dcafa8/api/pyproject.toml)과 [웹 의존성](https://github.com/langgenius/dify/blob/8fb0e23773ee3091e4a9f1586a528df945dcafa8/web/package.json)을 확인했습니다. 나머지 표의 기술 구조는 링크한 공식 README와 Agno 런타임 문서에 근거합니다.

## What AgentPort Has Today

| 차별화 후보 | 현재 구현·증거 | 아직 필요한 증거 |
|---|---|---|
| 기존 하네스와 일반 도구를 같은 권한·작업 경로로 사용 | HTTP/MCP 서비스 계약, ACP 워커, 세션·결과 접근 검사. 실제 로그인·MCP와 모의 ACP 테스트 | 실제 Codex·Claude·Hermes 계정의 연속 작업과 세션 재개. Paperclip은 현재 HTTP 연결 예제 수준 |
| 요청할 때 서비스를 켜고 사용이 끝나면 중지 | 단일 기동 claim, 활성 작업 임대, 유휴 중지. Komodo 계약 테스트와 OpenShell SDK/모의 gRPC 테스트 | 실제 PC·DGX, GPU, Komodo/OpenShell 환경의 동시 요청·실패·복구. 장치 자동 등록과 GPU 자원 스케줄러는 없음 |
| 사용자가 실행 흐름과 외부 전달 데이터를 지정 | 명시적 그래프, 입력 매핑, 승인, 반복 한도, 실행 시 그래프 고정. 승인·재시작 테스트 | 네트워크 정책까지 포함한 외부 유출 차단. `local_only`는 호스트 방화벽을 자동 구성하지 않음 |
| 기술 지식 없이 도구 실행·결과 확인 | 스키마 기반 입력 폼, 텍스트 파일 변환·다운로드, 모바일 폭 검증 | 이미지·영상 업로드, 대용량 결과, 3D 미리보기, 이해하기 쉬운 오류·취소·복구 화면 |
| 연결 단절 후 중복 부작용 없이 복구 | DBOS 기록과 불명확한 외부 실행의 확인 대기 | 원격 native 취소, 중앙 단절 시 새 외부 호출 차단, 재연결 후 실행 상태 대조. 관측 로그는 완료 증거를 대신하지 않음 |

근거: [검증 기록](validation.md), [실행 플랫폼의 책임·제약](runtime-platform.md), [보안 경계](security.md), [호출 폼](../dashboard/src/pages/orchestration/InvokeForm.tsx), [수명주기 관리](../src/aog/lifecycle.py).

현재 파일 입력은 텍스트 예제 중심입니다. “사진을 올리면 Trellis가 실행되고 3D 결과물을 받는다”는 경로는 목표 시나리오이며 구현·검증 완료 기능이 아닙니다. 또한 전체 Compose 구성의 메모리·처리량을 비교하지 않았으므로 경쟁 제품보다 가볍거나 빠르다고 주장하지 않습니다.

## Positioning

AgentPort의 제품 방향은 다음 한 문장으로 유지합니다.

> 여러 장치에 배치한 AI 서비스와 에이전트를 등록하고, 사용자별 권한에 따라 웹·메신저·MCP에서 호출하며, 서비스 수명주기와 실행 흐름을 시각적으로 제어하는 자체 호스팅 중앙 게이트웨이.

이 문장은 목표 범위입니다. 현재 제공 경로는 웹·REST·MCP이며 메신저는 후속 범위입니다.

차별화는 **이미 운영 중인 서비스를 얼마나 적은 작업으로 팀에 제공할 수 있는가**로 평가합니다. 서비스 하나를 연결할 때 인증, 상태 확인, 결과 조회, 실행 정책을 매번 별도로 만드는 비용을 줄이는 것이 핵심입니다. 여러 도구를 단순히 한 화면에 나열하는 것으로는 부족합니다.

다음 경우에는 기존 프로젝트를 먼저 평가하는 편이 합리적입니다. 이는 위 공개 기능을 바탕으로 한 선택 가이드이며 실제 비교 실험의 결론은 아닙니다.

- 에이전트 조직·작업 배분·예산·협업이 중심이면 Paperclip.
- 에이전트 API와 세션·워크플로우 실행 플랫폼이 중심이면 Agno/AgentOS.
- 사내 스크립트를 폼·앱·자동화로 제공하는 것이 중심이면 Windmill.
- AI 앱과 RAG 파이프라인 제작이 중심이면 Dify.
- 모델·도구 연결의 인증·정책·관측만 필요하면 ContextForge 또는 agentgateway.

AgentPort는 이들 전체의 기능 수를 따라가기보다, 장치에 배치한 기존 서비스와 하네스를 함께 운영하는 사용 경로를 검증합니다. 동일 시나리오를 기존 도구 조합이 더 쉽게 해결한다면 그 부분은 연결 기능으로 전환하고 자체 구현 범위를 줄일 수 있어야 합니다.

## Reuse Strategy

| 참조 대상 | 차용하거나 연결할 부분 | 자체 구현의 경계 |
|---|---|---|
| ContextForge | 로그인·권한·MCP/API 연결과 기존 UI | 서비스 정의, 실행 정책, 사용자 그래프와 작업·결과 경계를 추가. 기반 기능을 새로 구현하지 않음 |
| Windmill | 스키마→사용자 폼, 작업 상태·결과 화면, 운영자/사용자 구분 방식 | UX와 계약을 참고. 필요하면 기존 플로우를 API로 연결; 두 개의 실행 엔진을 기본 구성에 함께 넣지 않음 |
| Agno/AgentOS | 하네스 기능 표기, 세션·실행 API 구분, 중단·재개·승인 계약 | 필요하면 실행 서비스로 연결. Agno의 에이전트 내부 구조를 중앙의 필수 계약으로 만들지 않음 |
| Paperclip | 작업 위임·검수·결과물 연결 | 작업 제출·상태 조회·결과·취소 의미를 어댑터로 보완. 계정·조직·스킬 관리 전체를 중앙에 복제하지 않음 |
| Dify | 서비스로 공개된 RAG·앱 API, 입력·결과 UX | RAG 제작기와 자체 에이전트 하네스를 중앙 범위에 추가하지 않음 |
| agentgateway | 선언형 통신 정책과 MCP 권한 모델 | 필요 시 별도 게이트웨이 연동을 검토. 현재 ContextForge와 중복되는 필수 프록시를 추가하지 않음 |

OpenShell·Relay는 [현재 실행 플랫폼](runtime-platform.md)의 격리·관측 연동입니다. 적용 자체가 경쟁 우위의 증거는 아닙니다. OpenShell의 정책 적용 여부와 Relay의 활동 요약, AgentPort가 판정하는 작업 완료 상태는 별도로 다룹니다.

코드 차용 전에는 원본 라이선스와 배포판을 확인합니다. Agno와 agentgateway는 해당 저장소에서 Apache-2.0, Paperclip은 MIT로 표시됩니다. Dify는 추가 조건이 있는 [별도 라이선스](https://github.com/langgenius/dify/blob/8fb0e23773ee3091e4a9f1586a528df945dcafa8/LICENSE)이며, Windmill은 [소스·배포 바이너리·상용 조건을 구분](https://github.com/windmill-labs/windmill/blob/466885fd26b74bd20de67ee0ef0e55a47826cd47/README.md#license)합니다. 기능을 참고한다는 이유로 타 프로젝트 코드를 AgentPort의 Apache-2.0 아래 그대로 옮기지 않습니다. 이번 조사는 코드·의존성을 추가하지 않습니다.

## Validation Priorities

| 순서 | 사용자 시나리오 | 완료 조건 |
|---|---|---|
| 1 | 운영자가 기존 도구를 등록하고 일반 사용자가 실행 | JSON 직접 편집 없이 검증된 템플릿 등록·연결 확인. 사용자에게 허용된 도구·입력·상태·결과만 표시. 신규 서비스 연결에 필요한 단계와 시간을 기록 |
| 2 | 사진 → 원격 GPU 처리 → 결과 다운로드 | 한 개의 실제 이미지/3D 서비스와 PC·DGX 경로 검증. 업로드·진행·결과, A/B 동시 사용, 기동 1회, 사용 중 종료 거부, 유휴 중지 확인 |
| 3 | Codex 작성 → Claude 검수 → 제한된 수정 | 실제 계정과 지정 세션, 재개, 반복 한도, 결과물 전달 검증. 지원하지 않는 재개·취소 기능은 화면에서 제공하지 않음 |
| 4 | 민감한 원문 → 로컬 추출 → 승인 → 클라우드 | 승인한 필드만 전달. 원문·비밀이 외부 통신과 로그에 없는지 검사. 중앙 단절·재연결 시 통신 제한과 작업 중복 여부까지 확인 |

비교할 때는 동일 장치·입력·모델과 배포판을 기록합니다. 설치 시간, 서비스 등록 단계 수, 첫 성공 결과까지 걸린 시간, 전체 스택 유휴 RSS, 동시 요청의 기동 횟수, 장애 후 중복 외부 호출 수, 사용자가 복구에 필요한 조작 수를 측정합니다. 무조건적인 exactly-once 실행을 약속하지 않고, 완료 여부가 불명확한 작업을 어떻게 확인·복구하는지 평가합니다.

위 시나리오를 Agno·Windmill·Paperclip 중 적합한 후보와 비교하기 전까지는 “더 쉽다”, “더 안전하다”, “더 가볍다”를 공개 성능 주장으로 사용하지 않습니다. 다음 개발의 우선순위는 어댑터 이름을 늘리는 것보다 이 경로를 끝까지 완성하는 것입니다.
