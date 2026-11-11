# Development

Core는 Python 3.12–3.14, UI/BFF는 Node.js 24를 사용합니다. ContextForge native addon 개발에는 Python 3.12/3.13이 필요합니다. 일반 설치는 Docker Compose를 권장합니다.

## Core Only

```bash
uv sync --frozen --extra dev
uv run aog serve --host 127.0.0.1 --port 4455
```

이 설치는 ContextForge Python package를 요구하지 않습니다. 기본 backend를 사용하려면 별도로 실행한 gateway의 주소와 bridge secret을 설정해야 합니다. Core가 단독으로 별도 로그인·MCP·tool registry를 구현하는 것은 아닙니다.

## Local Gateway Development

```bash
python3 scripts/prepare-contextforge.py
uv sync --python 3.13 --frozen --extra contextforge --extra dev --extra openshell
uv run --extra contextforge --extra openshell uvicorn aog.integrations.contextforge.app:app --env-file .env --host 127.0.0.1 --port 4444 --workers 1
```

두 번째 터미널에서 같은 bridge secret, AOG_DATABASE_URL과 native 주소를 설정하고 Core를 실행합니다. 같은 개발 venv를 쓸 때는 uv가 CF extra를 제거하지 않도록 양쪽 실행에 extra를 유지합니다.

```bash
uv run --extra contextforge --extra openshell aog serve --host 127.0.0.1 --port 4455
```

native addon의 AOG_CORE_URL은 http://127.0.0.1:4455, Core의 AOG_GATEWAY_URL은 http://127.0.0.1:4444입니다. DBOS는 Core에서만 시작합니다. gateway-data의 signing 파일과 기존 암호화 설정을 보존하세요. shared AOG schema 2를 쓰는 두 writer의 정지·backup도 함께 수행합니다.

## Dashboard and BFF

```bash
cd dashboard
npm ci --ignore-scripts
npm run build
cd server
npm ci --ignore-scripts
npm run build
COOKIE_SECURE=false REDIS_URL=memory:// PUBLIC_ORIGIN=http://localhost:3000 CONTEXTFORGE_URL=http://127.0.0.1:4444 AGENTPORT_CORE_URL=http://127.0.0.1:4455 npm start
```

브라우저에는 native Bearer token 대신 기존 HttpOnly session·CSRF 경계를 유지합니다. BFF에 bridge secret을 전달하지 않습니다. Core API와 native API를 분리하더라도 사용자 화면은 같은 진입점을 사용합니다.

## Checks

```bash
uv run ruff check --config pyproject.toml src tests scripts examples
uv run pytest -q
```

native integration 테스트는 고정 소스 준비와 contextforge extra가 필요합니다. 원본 관련 회귀와 두 프로세스 HTTP/MCP 검증은 CI의 분리된 항목을 기준으로 합니다. OpenShell SDK는 Core의 선택 extra입니다. 실제 장비·유료 모델·native 로그인은 계약 테스트와 별도로 검증합니다.

[Integration Maintenance](upstream-contextforge.md), [Migration](migration.md), [Contributing](../CONTRIBUTING.md)를 참조하세요. 준비된 upstream/contextforge는 Git에 추가하지 않습니다.

## Gateway Integrations

Core의 Python 접점은 `aog.backends.GatewayBackend`입니다. 외부 패키지는 다음 entry point를 등록하고 운영자가 `AOG_GATEWAY_BACKEND`로 선택합니다.

```toml
[project.entry-points."agentport.gateway_backends"]
example = "example_gateway:Backend"
```

인증·현재 권한 확인, catalog, 호출, secret codec, 제한된 위임을 구현하고 Core에 native ORM이나 SDK 객체를 반환하지 않습니다. 기본 연결의 HTTP 구현은 `aog.integrations.contextforge.client`를 참고하세요. 새로운 연결은 먼저 자신의 로그인·도구·MCP 권한 의미를 검증해야 합니다. entry point가 있다고 모든 backend가 이미 지원된다는 뜻은 아닙니다. 설치된 외부 backend를 선택해 Core의 신원·권한 경로가 변경되는 회귀와, `mcpgateway` import를 금지한 Core 검사가 있습니다.
