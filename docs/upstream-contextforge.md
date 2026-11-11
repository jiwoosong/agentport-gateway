# ContextForge Integration Maintenance

## Distribution Boundary

Core는 독립 FastAPI·DBOS 애플리케이션입니다. ContextForge 연결은 별도 프로세스·이미지·선택 Python extra에 둡니다. 기본 backend는 ContextForge이므로 현재 기본 배포에는 필요합니다. 다른 인증·도구·MCP backend가 이미 구현되어 있다는 뜻은 아닙니다.

기준은 [ContextForge 1.1.0 commit aee91ff6db63b6aafa77522684a9483c418bb2a3](https://github.com/IBM/mcp-context-forge/tree/aee91ff6db63b6aafa77522684a9483c418bb2a3)입니다. 저장소는 upstream 전체 소스를 포함하지 않고 다음 자료를 유지합니다.

- [Connector manifest](../integrations/contextforge/manifest.json): 소스 commit·archive URL/hash·패치 hash·호환 버전.
- [호환 패치](../integrations/contextforge/patches/agentport-v1.1.0.patch): 필요한 native 인증·scope·dispatch 보정.
- [준비 스크립트](../scripts/prepare-contextforge.py): 고정 archive 검증·추출·패치·생성 stamp.
- aog.integrations.contextforge: 별도 앱, native bridge, Core용 HTTP client.

준비한 upstream/contextforge는 Git에서 제외합니다. 기존 단일 앱 prototype은 새 배포의 실행 의존성이 아니며 검증 전 보관·이전 대상으로 남깁니다. 전체 upstream을 도입했던 local commit을 공개 저장소의 필수 기준으로 참조하지 않습니다. image 배포 시 Apache-2.0 LICENSE·원본 copyright·필요한 notice를 유지합니다.

## Prepare the Optional Gateway Source

```bash
python3 scripts/prepare-contextforge.py
```

네트워크를 쓰지 않을 때는 같은 hash의 고정 archive를 운영자가 준비할 수 있습니다.

```bash
python3 scripts/prepare-contextforge.py --archive /absolute/private/contextforge.tar.gz
```

스크립트는 manifest의 archive·patch hash를 검증합니다. 기존 디렉터리를 무조건 덮어쓰지 않습니다. --force는 자신이 생성 stamp로 확인한 디렉터리의 재준비용이며 이전 사본을 보존합니다. 검토하지 않은 수작업 source directory를 삭제하는 옵션이 아닙니다. 기본 Docker 빌드는 deploy/contextforge.Dockerfile 안에서 이 준비 절차를 수행합니다.

Python 개발 환경에서 native gateway를 실행할 때만 Python 3.12/3.13과 `uv sync --frozen --extra contextforge`를 사용합니다. Core만 설치할 때는 CF extra가 필요하지 않습니다. 개발용 동시 실행은 [Development](development.md)를 참조하세요.

## Compatibility Patch Record

CPEX 공개 hook만으로 현재의 인증한 caller·제한 token/server·내부 backend dispatch 경계를 모두 검증하지 못했으므로 작은 호환 패치를 유지합니다. **stock CF 또는 순수 CPEX plugin이라고 표시하지 않습니다.**

| upstream 경로 | 유지 이유 |
|---|---|
| mcpgateway/agentport.py | 명시적 native addon hook, 검증된 caller 문맥, 내부 backend 실행 guard |
| mcpgateway/main.py | native RPC/REST caller 전달·local 등록 권한·관리자의 own-private 도구 소유 문맥 보존 |
| mcpgateway/common/models.py | 인증 문맥 전달 타입 |
| mcpgateway/middleware/token_scoping.py | addon의 좁은 bridge/호환 경로와 기존 token scope 처리의 연결 |
| mcpgateway/schemas.py | AGENTPORT_LOCAL 정의·비네트워크 검증, MCP transport 갱신·Python-only 내부 MCP 생성 context |
| mcpgateway/services/tool_service.py | native ACL 이후 local dispatch, private backend 직접 호출 거부, 요청별 credential, mutation 자동 재시도 금지 |
| mcpgateway/toolops/toolops_altk_service.py | 자연어 보조 호출이 local/internal 도구를 직접 실행하지 못하도록 차단 |
| mcpgateway/transports/streamablehttp_transport.py | native MCP·virtual-server caller/scope 전달 |
| mcpgateway/bootstrap_db.py | owned private backend/control이 재시작 때 public으로 바뀌지 않도록 보존 |

현재 내용은 manifest에 지정한 patch 파일이 기준입니다. 내부 MCP 생성 허가는 Python 모델 검증 context이며 사용자가 HTTP JSON flag로 선택할 수 없습니다. restricted admin·빈 team 범위·token/server 범위를 무제한 관리자로 확대하지 않습니다. 같은 서비스를 별도 무제한 native 도구로 등록하거나 서비스 자체를 외부에 노출하면 그 경로는 AgentPort 정책 밖입니다.

native addon은 Core의 DBOS 실행기를 시작하지 않습니다. 대신 검증한 호출자를 서명 bridge로 전달하고 native catalog·token·중계를 담당합니다. Core는 작업·세션·결과·그래프·runtime을 관리합니다. 현재 AOG schema 2의 runtime binding/grant/job metadata를 공유하므로 Core와 connector의 데이터 호환성을 함께 검사해야 합니다.

## Update the Connector

1. Core·CF·워커의 진행/미확정 작업을 정리하고 세 DB·파일·키를 백업합니다.
2. upstream commit·license·archive hash를 검토하고 manifest를 갱신합니다. patch도 검토·재생성하고 hash를 갱신합니다.
3. 준비 스크립트로 깨끗한 소스를 만들고 patch 적용·원본 관련 회귀를 확인합니다.
4. native 로그인·두 사용자·좁은 token/server·관리자 scope·내부 backend 우회·ToolOps·반복 bootstrap·process restart를 검증합니다.
5. 두 실제 프로세스로 bridge v1·변조/재전송 거부·Core 무CF import·catalog/작업/승인/취소·결과·복구를 확인합니다.
6. 호환되는 Core·connector·AOG DB schema 조합으로 배포합니다. 공유 schema 변경은 양쪽을 정지하고 먼저 검증한 migration을 수행합니다.

Core와 gateway는 다른 이미지로 업데이트하지만 임의 버전을 섞는 무제한 호환성을 제공하지 않습니다. 순수 stock CF+CPEX로 같은 검증을 통과하면 해당 호환 패치를 제거할 수 있습니다. 포크 코드를 Core에 재흡수하거나 runtime monkey patch로 숨기지 않습니다.
