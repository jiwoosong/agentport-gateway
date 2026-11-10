# 재사용과 호환성

재사용 순서는 설정 → 공개 API/SDK → 확장 코드 → 필요한 최소 수정입니다. 중앙 게이트웨이를 Paperclip 플러그인으로 만들지 않습니다.

| 프로젝트 | 고정 기준 | 라이선스 | 재사용·수정 경계 |
|---|---|---|---|
| [ContextForge](https://github.com/IBM/mcp-context-forge/tree/v1.1.0) | 1.1.0 / `aee91ff6db63b6aafa77522684a9483c418bb2a3` | Apache-2.0 | PyPI 패키지. 원본 소스 무수정, FastAPI lifespan·라우터와 인증/권한 서비스 통합 |
| [ContextForge Web UI](https://github.com/contextforge-org/contextforge-web-ui/tree/v0.2.4) | 0.2.4 / `3ab7b7253b379b2afa5ad65b4d4150aa84ab7559` | Apache-2.0 | `dashboard/`에 원본 복사, 출처·라이선스·테스트 유지 |
| [DBOS Python](https://github.com/dbos-inc/dbos-transact-py) | 3.4.0, `uv.lock` | MIT | 로컬 실행 라이브러리, 체크포인트·메시지 대기 재사용; 실행 그래프는 자체 구현 |
| [React Flow](https://github.com/xyflow/xyflow) | `@xyflow/react` 12.12.1 | MIT | 노드·선 편집. 노드 의미·권한·실행은 자체 구현 |
| [Komodo](https://github.com/moghtech/komodo/tree/v2.3.4) | 2.3.4 | GPL-3.0 | 별도 컨테이너와 HTTP API만 사용. GPL 코드 복사 없음 |
| [ACP Python](https://github.com/agentclientprotocol/python-sdk) | 0.12.1, `uv.lock` | Apache-2.0 | stdio 프로토콜·세션 협상 재사용 |
| [Codex ACP](https://github.com/agentclientprotocol/codex-acp) | 2.6.0, `workers/acp/package-lock.json` | Apache-2.0 | 기존 하네스 연결. 별도 프로세스로 실행 |
| [Claude ACP](https://github.com/agentclientprotocol/claude-agent-acp) | 0.101.0, 같은 잠금 파일 | Apache-2.0 | 기존 하네스 연결. 별도 프로세스로 실행 |
| [Paperclip](https://github.com/paperclipai/paperclip) | 필수 버전 없음, 전용 기능 미검증 | 별도 프로젝트 라이선스 적용 | 선택 HTTP/MCP 서비스; 로그인·권한·그래프 엔진의 기준 아님 |

Python·npm 의존성의 실제 설치 버전과 무결성은 `uv.lock`, `dashboard/package-lock.json`, `dashboard/server/package-lock.json`, `workers/acp/package-lock.json`에 있습니다. 인프라 이미지의 PostgreSQL·Redis·MongoDB·Caddy 태그는 Compose에 기록합니다. 운영 릴리스는 각 대상 아키텍처에서 검증한 이미지 digest로 추가 고정해야 합니다.

## Web UI 변경 기록

원본은 React·TypeScript·Vite, Fastify BFF, Redis 세션을 사용합니다. 다음 변경을 추가했습니다.

- `src/pages/Orchestration.tsx`: 서비스 호출·운영, 에이전트·개인 자격 증명, React Flow 그래프, 작업·승인·결과.
- `src/App.tsx`, 사이드바: `/app/orchestration` 라우트와 AgentPort 진입점.
- `tsconfig.app.json`: Vite 클라이언트 타입을 명시하여 스타일/환경 타입 빌드 보정.
- `src/hooks/useToolForm.ts`: Zod 4의 명시적인 record 키 스키마에 맞춤.
- 의존성 잠금 갱신: React Flow 추가, Zod 4.6.7, 보안 수정된 테스트/BFF 의존성. CLI인 `shadcn`은 실행 의존성에서 제거하고 필요한 4.23.0 CSS만 MIT 고지와 함께 보존.
- `src/pages/Orchestration.test.tsx`: 새 사용 경로 회귀 테스트.

기존 로그인/BFF 인증·CSRF 흐름을 대체하지 않았습니다. `dashboard/README.md`는 상류 문서이며 AgentPort 설치에는 루트 README와 `docs/deployment.md`를 우선합니다. 상류 테스트·예제의 이름이 ContextForge인 것은 출처를 보존하기 위함입니다.

## 실제 지원의 의미

Codex·Claude의 초기 ACP 연결에서 protocol 1과 `loadSession` 지원을 확인했습니다. 네이티브 세션 생성·재개 호출은 SDK와 모의 ACP 프로세스로 검증했습니다. 실제 계정으로 수행하는 코딩·검수, 모델 선택 및 provider 도구 권한의 모든 조합은 미검증입니다. 분기(fork)는 제공하지 않습니다. 지원 여부는 어댑터의 협상 결과로 판단하며 불가능하면 실패합니다.

`openai`와 `paperclip` transport는 선언한 JSON HTTP 엔드포인트를 호출합니다. 공급자별 의미 변환이나 포괄적인 SDK 지원을 뜻하지 않습니다. 다른 인증 헤더·스트리밍·웹훅 프로토콜이 필요한 API는 ContextForge 또는 별도 HTTP/MCP 어댑터로 연결합니다. Hermes ACP 실행 프로필은 추가했으며 모의 프로토콜 테스트까지 검증했습니다. 실제 Hermes 런타임·NemoClaw·OpenClaw·Kimi의 종단 간 연결은 미검증입니다. [제품별 호환성 가이드](integrations.md)에 연결 방식과 제한을 기록합니다.

RAG 검색은 서비스 계약이며 원본 문서 ACL 검증은 RAG 구현의 책임입니다. 공유 스킬 게시도 별도 서비스의 명시적 동작입니다. 중앙은 임의로 대화를 스킬로 만들거나 벡터 저장소에 게시하지 않습니다.

## 업스트림 갱신 절차

1. 대상 태그·커밋·라이선스·릴리스 노트를 기록합니다.
2. 원본 UI와 최소 변경 지점을 비교합니다. 인증 코드를 새로 만들지 않습니다.
3. 잠금 파일을 갱신하고 서버·UI·BFF 테스트와 실제 로그인/MCP/두 사용자/복구 검증을 수행합니다.
4. DBOS 실행 함수나 체크포인트 순서가 바뀌면 활성 실행을 먼저 정리하고 데이터 백업을 남깁니다.
5. 검증 기록을 수정하고 새 버전을 발행합니다. 보안 수정 외의 무계획 자동 업데이트는 하지 않습니다.

참고: [DBOS 통합](https://docs.dbos.dev/python/integrating-dbos), [Komodo Compose](https://komo.do/docs/deploy/compose), [Komodo 원격 서버 연결](https://komo.do/docs/setup/connect-servers).

## 실행 플랫폼 추가

- OpenShell Python SDK 0.1.7 (Apache-2.0): 선택 의존성, `uv.lock` 고정. 시작·중지·조회와 상태 대기에 사용. 호스트 런타임 자동 설치 없음.
- NeMo Relay (Apache-2.0): Hermes 네이티브 연결의 TOML/ATOF 계약 재사용. 소스 조사 기준 `dc00b939ae916266ce4f22ca080c9f13a40d4c48`. 중앙 SDK 의존성 없음.
- [실행 플랫폼 가이드](runtime-platform.md)에 검증 상태와 미지원 기능을 기록합니다.
