# Reuse and Compatibility

기존 기능은 설정·공개 API/SDK를 우선 사용합니다. ContextForge는 별도 게이트웨이 구성요소로 등록·인증·권한·MCP/API 중계를 맡습니다. Core에는 native 패키지를 설치하지 않고 connector가 필요한 호환 패치를 유지합니다. DBOS는 유일한 내부 실행 엔진입니다. Windmill은 UX 참고 대상이며 코드를 복사하지 않았습니다.

## Adopted Components

| 프로젝트 | 고정 기준 | 라이선스 | 사용·수정 범위 |
|---|---|---|---|
| [ContextForge](https://github.com/IBM/mcp-context-forge/tree/aee91ff6db63b6aafa77522684a9483c418bb2a3) | 1.1.0 / aee91ff6db63b6aafa77522684a9483c418bb2a3 | Apache-2.0 | 선택 connector image. 고정 소스를 빌드 시 준비하고 검증한 patch 적용. native ToolService·계정·token·중계 유지; Core와 HTTP bridge |
| [ContextForge Web UI](https://github.com/contextforge-org/contextforge-web-ui/tree/3ab7b7253b379b2afa5ad65b4d4150aa84ab7559) | 0.2.4 / 3ab7b7253b379b2afa5ad65b4d4150aa84ab7559 | Apache-2.0 | dashboard source copy. 로그인·BFF·CSRF·기존 관리 UI 유지, AgentPort 화면 추가 |
| [DBOS Python](https://github.com/dbos-inc/dbos-transact-py) | 3.4.0 / uv.lock | MIT | 애플리케이션 내부 체크포인트·대기·복구. Conductor 불필요. 사용자 그래프 의미는 자체 코드 |
| [React Flow](https://github.com/xyflow/xyflow) | @xyflow/react 12.12.1 | MIT | 노드·연결선 편집. 노드 실행·권한은 자체 코드 |
| [Komodo](https://github.com/moghtech/komodo/tree/v2.3.4) | 2.3.4 | GPL-3.0 | 별도 Core/Periphery와 HTTP API. 소스 복사 없음 |
| [OpenShell SDK](https://github.com/NVIDIA/OpenShell/tree/v0.1.7) | 0.1.7 / 선택 uv extra | Apache-2.0 | native start/stop/status·인증. host runtime 자동 설치 없음 |
| [ACP Python](https://github.com/agentclientprotocol/python-sdk) | 0.12.1 / uv.lock | Apache-2.0 | stdio 세션·도구·permission 프로토콜 |
| [Codex ACP](https://github.com/agentclientprotocol/codex-acp) | 2.6.0 / worker package-lock | Apache-2.0 | 별도 프로세스의 기존 하네스 어댑터 |
| [Claude ACP](https://github.com/agentclientprotocol/claude-agent-acp) | 0.101.0 / worker package-lock | Apache-2.0 | 별도 프로세스의 기존 하네스 어댑터 |
| [Pillow](https://pillow.readthedocs.io/en/stable/about.html#license) | 13.0.0 | MIT-CMU | 선택 CPU PNG/JPEG → WebP 예제. 배포 의존성의 원본 license 유지 |

Python/npm의 설치 버전·무결성은 uv.lock, dashboard/package-lock.json, dashboard/server/package-lock.json, workers/acp/package-lock.json에 있습니다. PostgreSQL·Redis·MongoDB·Caddy image tag는 Compose에 있습니다. 운영 배포는 해당 아키텍처에서 확인한 image digest로 추가 고정합니다.

[Connector Manifest](../integrations/contextforge/manifest.json), [Upstream Manifest](upstream-manifest.json), [Integration Maintenance](upstream-contextforge.md)에 source/patch hash와 호환 지점을 기록합니다. full upstream은 Git에 vendoring하지 않습니다. 준비된 소스와 빌드 image에는 원본 라이선스를 보존합니다. 저장소 Apache-2.0 라이선스가 외부 제품·모델·하네스 구독 약관을 대체하지 않습니다.

## Dashboard Changes

Web UI baseline은 React·TypeScript·Vite와 Fastify BFF·Redis 세션입니다. 원본 LICENSE/THIRD-PARTY-NOTICES를 보존합니다.

- App/사이드바: /app/orchestration 진입점.
- Orchestration 및 하위 화면: 서비스 호출·정의 가져오기, runtime/provider/profile 폼, 세션·작업·native 승인·결과, React Flow 편집.
- BFF: 기존 HttpOnly 쿠키·CSRF 유지, Orchestration API 프록시와 파일 입력 크기 경계.
- Zod record 타입·Vite 타입 보정, React Flow 의존성, 보안 수정된 테스트/BFF 잠금 갱신.
- shadcn CLI 실행 의존성 제거. 필요한 4.23.0 CSS만 MIT 원본 LICENSE와 함께 보존.

`dashboard/README.md`는 원본 프로젝트 문서입니다. AgentPort 설치는 루트 README와 Deployment를 기준으로 합니다. 상류 테스트·문서의 ContextForge 이름을 출처 보존을 위해 유지합니다.

## Compatibility Meaning

연결 상태는 네 단계로 구분합니다: **공식 계약**, **설정 가능한 연결**, **추가 어댑터 필요**, **실제로 수행한 검증**. Docker image 존재·API 등록 성공·ACP 초기 협상·모의 프로세스 통과를 실제 유료 모델 작업의 성공으로 표시하지 않습니다.

Codex/Claude 초기 ACP protocol 1과 loadSession 응답은 확인했습니다. job·세션·native permission·취소·불명확 복구는 모의 프로세스로 검증합니다. Kimi/OpenCode/Hermes template은 별도 native binary/로그인/모델 배치가 필요합니다. 자세한 제한은 [Integrations](integrations.md), 현재 실행 결과는 [Validation](validation.md)을 참조하세요.

RAG 문서 ACL은 검색 서비스가 적용합니다. AgentPort의 도구 권한이 원본 문서 권한을 대체하지 않습니다. 공유 스킬은 명시적 게시 서비스로 연결하고 하네스 개인 메모리를 자동 수집하지 않습니다.

## Upgrade and Replacement

upstream manifest·라이선스·잠금 파일·connector patch record를 함께 갱신하고 native 로그인/MCP/두 사용자/제한 토큰/내부 backend guard/복구를 반복 검증합니다. 새 DBOS 코드로 활성 실행을 무조건 이전하지 않습니다.

독립 JSON 정의는 다른 gateway나 실행기에 연결할 자료로 보존하지만 native tool ID·token·DBOS checkpoint·하네스 세션은 제품별 상태입니다. 설정 파일 export가 전체 실행 상태 이식성을 의미하지 않습니다. [Migration](migration.md)에 보존·재매핑·교체 비용을 기록합니다.

## Separate Components

기본 배포의 gateway는 agentport-contextforge:1.1.0-1, Core는 agentport-core:0.2.0입니다. ContextForge는 기본으로 필요하지만 backend 계약으로 교체할 수 있도록 경계를 둡니다. 현재 다른 backend 구현을 제공하지 않습니다. Core만의 uv 설치에는 CF extra가 없으며 native addon 개발/빌드에서만 contextforge extra를 사용합니다.

현재 bridge contract 1과 AOG schema 2는 호환 범위입니다. connector가 runtime binding·grant·job metadata를 공유하므로 단순히 두 Docker image로 나눴다고 DB까지 독립된 것은 아닙니다. 원래 gateway와 Core를 한 프로세스로 실행하던 prototype은 새 배포 경로가 아닙니다. 실제 이전 전 백업·완료 작업·native IDs·불명확 외부 실행을 보존합니다.
