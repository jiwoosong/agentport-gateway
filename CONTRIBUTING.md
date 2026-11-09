# 기여 안내

중앙 게이트웨이는 인증·권한·서비스 운영·명시적인 그래프 실행에 집중합니다. 추론·개인 메모리·스킬 학습을 중앙 규칙 엔진으로 다시 만들지 않습니다. 새로운 도구는 먼저 정의 파일 + 기존 HTTP/MCP 계약으로 추가할 수 있는지 확인하세요.

Python 변경은 `uv sync --frozen --extra dev`, `uv run ruff check src tests scripts examples`, `uv run pytest -q`로 확인합니다. 인증·실행·복구 변경은 `AOG_INTEGRATION=1 uv run pytest tests/test_http_integration.py -q`도 실행합니다.

UI는 `dashboard/`에서 `npm ci --ignore-scripts`, `npm run build`, `npm test -- --maxWorkers=2`; BFF는 `dashboard/server/`에서 같은 설치·빌드 후 `npm run test:run`을 실행합니다. 실제 브라우저 검증은 Chromium 설치 후 `node scripts/browser-smoke.mjs`를 사용합니다. 스크립트는 임시 계정/DB를 만들고 자신이 시작한 서버를 종료합니다.

출처·라이선스·고정 버전·재사용/수정 이유를 `docs/reuse-and-compatibility.md`에 기록하세요. 검증하지 않은 공급자·GPU·보안 기능을 지원 목록에 올리지 않습니다. 기존 인증 체계를 우회하는 개발 전용 토큰이나 테스트 전용 API를 제품 코드에 추가하지 않습니다.

PR은 해결하는 문제와 실제 동작, 검증 결과, 남은 제약을 설명합니다. DBOS 실행 함수/단계 순서나 테이블 구조를 바꾸면 진행 중 실행과 데이터 이전 방법을 함께 명시합니다. 자격 증명·개인 데이터·생성된 DB·node_modules를 커밋하지 않습니다.
