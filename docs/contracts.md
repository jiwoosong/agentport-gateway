# API and Definition Contracts

## Versions and Ownership

새 runtime·provider·agent profile은 schema_version 2를 사용합니다. workflow는 1과 2를 읽습니다. 서비스 등록 API는 schema1 호환 입력과 schema2의 runtime_id 참조를 받지만 활성 도구 정의는 **ContextForge native Tool**에 보관합니다. 완전한 입력 스키마는 `src/aog/models.py`와 실행 서버의 `/openapi.json`을 기준으로 합니다.

별도 API에 provider 비밀 값·native 로그인 파일 경로·Docker 명령을 섞지 않습니다. 정의 export에는 credential 참조만 포함하고 비밀은 포함하지 않습니다. 참조 ID는 다른 설치에 가져올 때 재매핑해야 합니다. export는 DBOS 체크포인트·세션·작업 DB 백업을 대체하지 않습니다.

## Runtime

```json
{
  "schema_version": 2,
  "name": "Photo service on remote PC",
  "backend": "komodo",
  "backend_ref": "default",
  "target_id": "REGISTERED_KOMODO_STACK_ID",
  "health_url": "http://PRIVATE_HOST:8080/health",
  "lifecycle": "on_demand",
  "idle_seconds": 300,
  "concurrency": 1,
  "location": "local",
  "egress_policy": "isolated"
}
```

backend는 external, komodo, openshell입니다. external은 운영자 수동 기동을 의미하며 manual만 허용합니다. Komodo는 현재 기본 Core 하나를 사용합니다. OpenShell은 `openshell: {gateway, workspace, sandbox, sandbox_id}`로 운영자 설정을 참조하고 Komodo target과 함께 지정하지 않습니다.

관리 runtime의 자동 기동에는 readiness URL이 필요합니다. OpenShell native READY와 워커 HTTP 준비 상태는 별개입니다. idle_seconds 기본 300초, timeout_seconds 기본 120초, concurrency 기본 1입니다. 같은 컨테이너의 모든 도구는 같은 runtime_id를 사용합니다.

Komodo release는 `releases: {"v2": "IMMUTABLE_STACK_ID"}`, current_release, rollback_safe로 정의합니다. OpenShell 이미지 update는 미지원이며 대체 sandbox 준비·검증 후 binding을 변경합니다.

## Service

```json
{
  "schema_version": 2,
  "name": "Photo to WebP",
  "kind": "tool",
  "transport": "http",
  "endpoint": "http://image-converter:8080/convert",
  "http_method": "POST",
  "runtime_id": "RUNTIME_ID",
  "input_schema": {
    "type": "object",
    "properties": {
      "image_base64": {"type": "string", "contentEncoding": "base64", "maxLength": 27962028},
      "quality": {"type": "integer", "minimum": 1, "maximum": 100, "default": 80}
    },
    "required": ["image_base64"],
    "additionalProperties": false
  }
}
```

전체 사진 예제는 [image-webp.service.json](../examples/image-webp.service.json)입니다. runtime_id가 없으면 호환 입력의 실행 메타데이터로 runtime을 생성합니다. 새 구성에서는 runtime을 먼저 등록해 여러 도구가 같은 용량·기동 잠금을 공유하도록 합니다.

kind는 tool/model/agent/paperclip, transport는 http/mcp/openai/acp/paperclip입니다. openai·paperclip은 원래 JSON 요청을 전달하는 호환 transport 이름이며 모델 프로토콜 변환기가 아닙니다. GET은 query, 다른 메서드는 JSON body로 전달합니다. 응답은 JSON이어야 합니다. MCP는 Streamable HTTP와 tool_name을 사용합니다. API/MCP 중계는 native ToolService를 통과합니다.

`accepts_identity: true`는 서명을 검증하고 원본 데이터 ACL을 적용하는 신뢰 서비스에만 지정합니다. X-AOG-Context에 사용자·프로젝트·invocation·audience·만료가 서명되어 전달됩니다. 일반 클라우드 API에 이 헤더를 보내지 않습니다. 임의 X-User 헤더를 신뢰하지 않습니다.

서비스의 credential_ref는 개인 credential 리소스의 참조입니다. native 중계 설정으로 등록한 공용 인증은 해당 도구의 운영 credential이며 사용자별 키의 대체가 아닙니다. 제공자별 인증 형식이 다르면 그 형식을 지원하는 native 설정이나 전용 어댑터를 사용합니다.

## Provider and Agent Profile

ProviderConnection은 `protocol: chat_completions | responses | messages`, base_url, credential_ref, 선택 model, location을 보관합니다. base URL에는 자격 증명·query·fragment를 넣지 않습니다. local provider 선언은 운영자 검증 대상입니다.

```json
{
  "schema_version": 2,
  "name": "My reviewer",
  "harness_key": "claude",
  "runtime_id": "CLAUDE_WORKER_RUNTIME_ID",
  "provider_connection_id": "MY_MESSAGES_PROVIDER_ID",
  "native_login_ref": null,
  "persona": "Review the provided changes and return a structured verdict.",
  "allowed_tool_ids": ["NATIVE_MANAGED_TOOL_ID"],
  "max_tool_calls": 20
}
```

provider_connection_id 또는 native_login_ref 중 **하나만** 지정합니다. native_login_ref는 중앙에 등록한 credential 리소스 ID이고, 그 credential의 opaque profile_ref를 워커가 사전 설치한 프로필에 연결합니다. 구독 로그인 정보를 API 키로 바꿔 해석하지 않습니다. provider와 credential의 소유자는 profile 소유자와 같아야 합니다. runtime에는 해당 하네스의 ACP worker 서비스가 정확히 하나 등록되어야 합니다.

하네스별 프로토콜·native 프로필·설치 범위는 [Integrations](integrations.md)를 따릅니다. GET /harnesses는 제공하는 template 목록이며 실제 장치 설치·모델 호출 성공을 뜻하지 않습니다.

## REST

기본 경로는 Core:4455의 `/orchestration/v1`입니다. 브라우저는 기존 BFF의 `/api/orchestration/v1`로 Core에 프록시합니다. gateway:4444의 같은 경로는 기존 클라이언트용 호환 proxy입니다. 직접 API는 ContextForge Bearer 토큰을 요구합니다. server-scoped 토큰은 native virtual-server MCP/RPC 경로를 사용합니다.

| 경로 | 동작 |
|---|---|
| GET /me | 현재 호출자 범위, native MCP 주소와 제어 tool ID |
| GET/POST /services, /agents, /workflows | 허용된 정의 조회·생성 |
| PUT /services/{id}, /agents/{id}, /workflows/{id} | 정의 수정·리비전 증가 |
| GET /{collection}/{id}/export?format=json | 서비스·agent·workflow 내보내기. yaml도 가능 |
| GET/POST /runtimes, /providers | runtime·개인 provider 관리 |
| GET/PUT /runtimes/{id}, /providers/{id} | 상세 조회·수정 |
| GET /runtimes/{id}/export | runtime 정의·리비전 JSON |
| GET /harnesses | 하네스 template·프로토콜 지원 목록 |
| POST /{collection}/{id}/invoke | 202 + 비동기 작업 ID |
| GET /runs, /runs/{id} | 비공개 상태·단계·결과·native permissions |
| POST /runs/{id}/cancel | 취소 요청, 원격 종료 확인은 별도 |
| POST /runs/{id}/steps/{node}/approve | 그래프 승인 `{"approved": true}` 또는 false |
| POST /runs/{id}/permissions/{permission} | native 승인 `{"option_id": "ORIGINAL_OPTION_ID"}` |
| POST /runs/{id}/steps/{node}/resolve | 운영자가 외부 확인 후 결과/실패 입력 |
| GET /sessions | 본인 profile의 중앙·native 세션 매핑 |
| GET/POST /credentials | 개인 API 키 metadata 조회·암호화 등록, 값 재조회 없음 |
| POST /credentials/native | `{name, profile_ref, location}` native 프로필 metadata 등록 |
| POST /runtimes/{id}/operations/{start,stop,update} | 운영자 runtime 제어. OpenShell update 거부 |
| POST /runtimes/{id}/releases/{release}/deploy | 고정 Komodo release 적용 |
| POST /runtimes/{id}/reconcile | 외부 확인한 상태 적용, 활성 임대가 있으면 거부 |
| GET /worker-public-key | 운영자용 Ed25519 공개 키 |
| POST /artifacts, GET /artifacts/{id} | 별도 소형 개인 파일 보관 API |

기존 `/services/{id}/operations/*`, releases, reconcile은 연결된 runtime으로 전달하는 호환 경로입니다. 새 운영 코드는 runtime API를 사용합니다.

호출 body:

```json
{"inputs": {}, "session_id": null, "local_only": true, "allow_cloud": false, "idempotency_key": "OPTIONAL_CLIENT_KEY"}
```

같은 사용자·멱등 키·정의/입력이면 같은 실행을 반환하고 입력이 바뀌면 409입니다. 직접 클라우드 호출은 local_only false와 allow_cloud true로 정확한 입력을 공개합니다. 그래프는 승인 노드로 공개 범위를 지정합니다. 중앙의 멱등 키가 외부 API의 멱등 구현을 대신하지는 않습니다.

resolve body는 `{"outcome":"completed","output":{},"external_execution_stopped":true}`입니다. 실패면 outcome failed를 사용합니다. runtime reconcile은 `{"phase":"stopped","deployment_verified":true}`이며 running은 readiness도 검사합니다. 외부 실행 종료를 확인하지 않고 임대를 해제하지 않습니다.

## Files and Sessions

세션 ID가 없으면 새 중앙 세션을 생성합니다. 기존 세션은 소유자·profile 결합을 검사한 뒤 협상된 loadSession 지원이 있는 경우 재개합니다. fork는 제공하지 않습니다. 워커는 세션별 동시 turn을 거부합니다.

에이전트 파일 전송은 `workspace_files: [{path,content_base64,sha256}]`, export_paths 배열로 명시합니다. 경로는 세션 아래 상대 경로이며 절대 경로·상위 이동·외부 symlink를 거부합니다. 파일당 1 MB, 전체 10 MB, 100개 제한입니다. 전체 workspace나 대화를 자동 전달하지 않습니다.

사진 도구는 서비스의 base64 필드로 직접 업로드합니다. 웹은 contentEncoding base64에 PNG/JPEG 파일 선택을 제공하고 결과의 `{filename,media_type,content_base64}`를 바이너리 다운로드합니다. 이는 artifact 보관 API의 약 500 KB 제한과 별개입니다. 대용량 영상·서명 URL·객체 저장소는 별도 계약이 필요합니다.

## User Graph

workflow 노드는 tool, model, agent, condition, join, approval, review입니다. depends_on은 순서, inputs는 전달 필드입니다.

```json
{
  "id": "cloud",
  "type": "model",
  "target": "REGISTERED_MODEL_TOOL_ID",
  "depends_on": ["release"],
  "inputs": {"$ref": "nodes.release.payload"}
}
```

입력 참조는 input.field, 결과는 nodes.node_id.field이며 조상 노드만 참조할 수 있습니다. 누락 필드는 실패합니다. when은 eq/ne/in/gt/lt 비교만 허용합니다. review는 작성자 target·reviewer·기본 3회/최대 10회 max_rounds·선택 session_id를 사용합니다. release true이면 매 cloud 호출에 정확한 payload 승인을 요구합니다.

[로컬→승인→외부 예제](../examples/local-release.workflow.json), [작성→검수 예제](../examples/review.workflow.json)의 대문자 ID를 실제 등록 ID로 바꿉니다. 자동 팀 구성이나 무제한 반복은 없습니다.

## Native MCP

기본 주소는 `http://127.0.0.1:4444/mcp`입니다. native virtual server는 `/servers/{id}/mcp`를 사용합니다. 원격은 HTTPS가 필요합니다. 기존 `/orchestration/v1/mcp/` 경로는 제거되었습니다.

등록된 서비스·profile·workflow는 native 도구 목록에서 발견합니다. 서비스 도구의 입력은 위 Invocation 형태이고 결과는 장시간 작업 ID입니다. native get_run·cancel·approve 등 제어 도구의 ID는 GET /me의 mcp_tools에서 확인합니다. 클라이언트는 tools/list의 실제 이름·스키마를 사용합니다. 이전의 list_capabilities/invoke라는 별도 MCP 서버를 가정하지 않습니다.

에이전트에게 발급하는 native token/virtual server에는 허용된 관리 도구와 제한된 작업 조회만 포함합니다. 관리 REST·부모 전체 기록·다른 profile/workflow 실행·추가 토큰 발급은 위임하지 않습니다. 고정 endpoint와 현재 native 권한 검증을 유지합니다.

## Gateway Component Contract

Core와 connector의 private bridge는 contract_version 1을 협상합니다. Core→gateway는 고정 `/internal/agentport/v1/rpc`, 검증된 native dispatch→Core는 `/internal/agentport/v1/dispatch`를 사용합니다. 임의 Python method·URL·module 이름을 실행하는 API가 아닙니다. 메시지는 shared secret으로 본문 hash·방향·요청/응답 ID·짧은 만료와 재전송을 검증하며 자동 mutation retry를 하지 않습니다.

이 내부 계약을 사용자-facing 인증으로 사용하지 않습니다. public REST/MCP 사용자 인증은 native CF가 유지합니다. 현재 connector의 runtime binding·grant/job metadata는 Core와 같은 AOG schema 2 DB를 사용합니다. 이 DB 구조까지 자유롭게 교체 가능한 wire 형식이라고 가정하지 않습니다. [Architecture](architecture.md#component-contract-and-updates)에 책임과 중단 영향을 설명합니다.
