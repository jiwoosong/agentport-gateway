# 서비스·그래프·MCP 계약 v1

모든 정의에 `schema_version: 1`을 사용합니다. 자격 증명 값·호스트 경로·Docker 명령을 입력 요청으로 받지 않습니다. 운영자가 엔드포인트와 배포를 먼저 등록합니다. JSON Schema는 서비스 입력·출력을 검증합니다. 완전한 스키마는 `src/aog/models.py`와 `/openapi.json`을 참고합니다.

## 서비스

```json
{
  "schema_version": 1,
  "name": "Text to CSV",
  "kind": "tool",
  "transport": "http",
  "endpoint": "http://converter:8080/convert",
  "http_method": "POST",
  "input_schema": {
    "type": "object",
    "properties": {"text": {"type": "string", "maxLength": 500000}},
    "required": ["text"],
    "additionalProperties": false
  },
  "lifecycle": "manual",
  "location": "local",
  "egress_policy": "isolated"
}
```

`kind`: `tool` / `model` / `agent` / `paperclip`. `transport`: `http` / `mcp` / `openai` / `acp` / `paperclip`。`openai`와 `paperclip`은 일반 JSON HTTP 호출 계약을 사용합니다. 제공자의 원래 요청 형식을 입력 스키마에 정의하세요. `GET`은 입력을 쿼리 매개변수, 다른 메서드는 JSON body로 전달합니다. 엔드포인트는 사용자가 호출마다 바꿀 수 없습니다. 응답은 JSON이어야 합니다.

MCP는 Streamable HTTP와 `tool_name`을 사용합니다. 결과는 MCP `CallToolResult`의 JSON 구조입니다. HTTP 자격 증명은 개인 자격 증명 참조의 Bearer 토큰입니다. 그 외 인증 방식은 ContextForge 도구 프록시 또는 전용 어댑터를 연결하세요.

`accepts_identity: true`는 신뢰하는 ACL 인식 서비스에만 설정합니다. 게이트웨이가 `X-AOG-Context`에 EdDSA 서명한 요청 사용자·팀·프로젝트·호출 ID·만료·대상 URL을 전달합니다. 일반 클라우드 모델 API에는 이 헤더를 보내지 않습니다. 서비스는 운영자가 배포한 공개 키로 서명을 검증한 후 원본 데이터 권한을 적용해야 합니다. 임의의 `X-User` 헤더는 신뢰하지 않습니다.

`always_on`·`on_demand`는 `stack_id`와 `health_url`이 필요합니다. `idle_seconds` 기본 300, 동시 실행 `concurrency` 기본 1, 호출/준비 제한 `timeout_seconds` 기본 120초입니다. 최초 이미지는 운영자가 Komodo에서 배포해야 하며 StartStack은 기존 스택을 시작합니다.

`releases: {"v2": "immutable-komodo-stack-id"}`, `current_release`, `rollback_safe`로 검증한 배포 버전을 등록합니다. 일반 update는 같은 스택을 다시 배포하며 이전 이미지 복구를 보장하지 않습니다. 복구가 필요한 변경은 서로 다른 불변 릴리스 스택과 명시적인 복구 정책을 사용합니다.

## 에이전트와 파일

에이전트 인스턴스는 등록된 `kind: agent`, `transport: acp` 서비스를 참조합니다. 인스턴스에는 `persona`, `model`, 개인 `credential_ref`, `allowed_service_ids`, `max_tool_calls`를 설정합니다. 현재 워커 템플릿은 Codex ACP와 Claude ACP입니다.

`POST /agents/{id}/invoke`의 `session_id`가 없으면 새 중앙 세션을 생성합니다. 있으면 소유자와 에이전트 결합을 검사하고, 어댑터가 `loadSession`을 지원할 때 네이티브 세션을 불러옵니다. fork는 지원하지 않습니다. 네이티브 모델/도구 기능을 자체적으로 흉내 내지 않습니다.

에이전트 입력의 `workspace_files`와 `export_paths`는 명시적인 파일 전송 계약입니다.

```json
{
  "instruction": "이 파일을 수정하고 내보내세요",
  "workspace_files": [{"path": "src/main.py", "content_base64": "...", "sha256": "..."}],
  "export_paths": ["src/main.py"]
}
```

경로는 세션 작업 디렉터리 아래의 상대 경로이며 `..`, 절대 경로, 외부로 나가는 심볼릭 링크를 거부합니다. 스냅샷은 파일당 1 MB, 합계 10 MB, 100개 제한입니다. 해시를 검증하고 응답 `files`에 선택된 파일만 넣습니다. 전체 작업 디렉터리·대화·자격 증명을 자동 전달하지 않습니다. 네이티브 세션을 재개하면 그 하네스의 기존 컨텍스트 정책이 적용됩니다.

## 호출·실행 API

기본 경로는 `/orchestration/v1`입니다. UI BFF에서는 `/api/orchestration/v1`로 프록시합니다. 직접 REST/MCP 호출은 ContextForge Bearer 토큰을 요구합니다.

| 경로 | 동작 |
|---|---|
| `GET /me` | 현재 사용자·팀·권한 범위 |
| `GET/POST /services`, `/agents`, `/workflows` | 허용된 정의 조회/생성 |
| `PUT /{collection}/{id}` | 정의 수정, 리비전 증가 |
| `GET /{collection}/{id}/export?format=json` | 버전·정의 내보내기; `yaml`도 가능 |
| `POST /{collection}/{id}/invoke` | 202 + 비동기 작업 ID |
| `GET /runs`, `/runs/{id}` | 비공개 작업 상태·단계·결과 |
| `POST /runs/{id}/cancel` | 협력적 취소 |
| `POST /runs/{id}/steps/{node}/approve` | `{"approved": true}` 또는 false |
| `POST /runs/{id}/steps/{node}/resolve` | 운영자가 확인한 완료/실패 및 결과 제공 |
| `GET /sessions` | 본인의 에이전트 세션 |
| `GET/POST /credentials` | 개인 키의 이름/ID 조회, 암호화 등록; 값 재조회 없음 |
| `POST /artifacts`, `GET /artifacts/{id}` | 개인 파일 base64 업로드·인출, 약 500 KB 입력 한도 |
| `POST /services/{id}/operations/{start,stop,update}` | 운영자 배포 동작 |
| `POST /services/{id}/releases/{release}/deploy` | 등록된 릴리스 적용 |
| `POST /services/{id}/reconcile` | 확인한 runtime 상태로 복구; 활성 임대가 있으면 거부 |
| `GET /worker-public-key` | 운영자용 EdDSA 공개 키 배포 |

호출 body는 `{"inputs": {...}, "session_id": null, "local_only": true, "allow_cloud": false, "idempotency_key": "선택 키"}`입니다. 같은 사용자·멱등 키·동일 정의/입력이면 동일 실행을 반환하고, 다른 입력이면 409입니다. 직접 클라우드 호출은 `local_only: false`, `allow_cloud: true`로 정확한 입력을 공개합니다. 워크플로우는 이 우회 플래그 대신 승인 노드를 사용합니다.

확인 API는 `{"outcome":"completed","output":{...},"external_execution_stopped":true}`입니다. 외부 실행 종료를 확인하지 않고 임대를 해제하지 마세요. 실패면 `outcome: failed`를 사용합니다. runtime 확인은 `{"phase":"stopped","deployment_verified":true}`이며 running은 준비 URL도 검사합니다.

일반 파일 업로드 ID를 서비스로 보내면 그 서비스가 중앙 파일을 자동으로 열 수 있는 것은 아닙니다. 승인된 사용자가 인출한 선택 파일 내용을 서비스 입력에 명시적으로 매핑하세요. 대용량 이미지·영상은 이후 별도 객체 저장소·서명 URL 계약으로 확장할 영역입니다.

## 그래프 데이터 연결

`depends_on`은 실행 순서, `inputs`는 전달 필드를 정합니다.

```json
{
  "id": "cloud",
  "type": "model",
  "target": "REGISTERED_MODEL_SERVICE_ID",
  "depends_on": ["release"],
  "inputs": {"$ref": "nodes.release.payload"}
}
```

입력 참조는 `input.field`, 결과 참조는 `nodes.node_id.field`입니다. 배열 인덱스도 숫자 경로로 지정합니다. 누락된 필드는 실패합니다. 조건은 `{"left": {"$ref": "nodes.check.matched"}, "op":"eq", "right":true}`입니다. 비교는 eq/ne/in/gt/lt만 지원합니다. 조건이 거짓인 노드는 skipped 결과를 남깁니다.

[로컬→승인→외부 예제](../examples/local-release.workflow.json), [작성→검수 예제](../examples/review.workflow.json)의 대문자 ID를 실제 등록 ID로 교체합니다. `review.release: true`는 매 클라우드 요청마다 별도 승인 대기를 만듭니다. 자동 팀 구성과 무제한 반복은 없습니다.

## MCP

주소: `http://127.0.0.1:4444/orchestration/v1/mcp/` (원격은 HTTPS). 제공 도구:

- `list_capabilities`: 현재 사용자 또는 위임 에이전트에게 허용된 정의.
- `invoke`: `kind`, `resource_id`, `inputs`, 선택 세션·공개 정책으로 작업 제출.
- `get_run`: 본인 작업 진행·결과 조회.

MCP 클라이언트에 ContextForge 토큰을 안전한 비밀 저장소로 설정하세요. 브라우저의 로그인 세션 쿠키와 직접 MCP Bearer 토큰은 별개입니다. 에이전트는 중앙이 발급한 좁은 범위의 짧은 토큰을 자동으로 전달받습니다.
