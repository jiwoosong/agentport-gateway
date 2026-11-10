# 개발 환경


Python 3.12–3.14, Node.js 24, uv를 사용합니다. SQLite는 단일 프로세스 개발용입니다.

```bash
uv sync --frozen --extra dev
uv run aog init
uv run aog serve
```

별도 터미널에서 UI와 BFF를 실행합니다.

```bash
cd dashboard
npm ci --ignore-scripts
npm run build
cd server
npm ci --ignore-scripts
npm run build
COOKIE_SECURE=false REDIS_URL=memory:// PUBLIC_ORIGIN=http://localhost:3000 npm start
```

초기화 명령은 기존 `.env`를 덮어쓰지 않습니다. 소스 개발과 Compose 구성을 전환할 때 데이터베이스와 비밀 키를 함께 보존하세요. [상세 설치](deployment.md)


## 검증

```bash
uv run ruff check src tests scripts examples
uv run pytest -q
AOG_INTEGRATION=1 uv run pytest tests/test_http_integration.py -q
```

OpenShell SDK 검증은 `uv sync --frozen --extra dev --extra openshell` 후 `AOG_OPENSHELL_WIRE=1 uv run --extra openshell pytest -q`로 실행합니다.

UI·BFF 검사와 기여 절차는 [기여 안내](../CONTRIBUTING.md)를 참고하세요.
