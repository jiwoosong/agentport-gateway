# AgentPort Gateway

Self-hosted gateway for AI agents, tools, and workflows.

## Overview

AgentPort는 여러 PC에 배치한 AI 에이전트와 도구를 웹·MCP에서 사용하는 자체 호스팅 게이트웨이입니다. 운영자는 실행 환경과 접근 권한을 관리하고, 사용자는 허용된 기능에 요청을 보내고 결과를 받습니다.

Codex·Claude Code 같은 기존 하네스, 모델 API, Docker로 배포한 이미지 처리·검색·RAG 서비스를 연결합니다. 사용자 그래프로 작업을 배분하고 승인·세션·진행 상황을 한 화면에서 확인합니다. 인증·MCP 게이트웨이와 작업 실행 Core를 별도 구성요소로 운영하므로 각각 검증된 버전으로 업데이트할 수 있습니다.

## Features

- **Tools:** HTTP·MCP 서비스를 등록하고 파일과 요청을 전달합니다.
- **Agents:** 하네스, 모델 연결, 개인 인증, persona와 허용 도구를 선택합니다.
- **Workflows:** 도구·에이전트·조건·병렬 처리·승인·제한된 검수 반복을 연결합니다.
- **Sessions and results:** 지원되는 세션 재개·native 승인·취소를 제어하고 결과를 내려받습니다.
- **Access control:** 사용자·프로젝트별 도구 권한과 개인 작업·자격 증명을 구분합니다.
- **Runtime management:** 운영자 수동 기동·상시 실행·요청 시 기동, 공유 runtime의 사용 중 보호와 업데이트를 관리합니다.

## Quick Start

Git, Python 3, Docker Engine과 Compose v2가 필요합니다. 사진 변환 예제에는 GPU나 유료 AI 계정이 필요하지 않습니다.

```bash
git clone https://github.com/jiwoosong/agentport-gateway.git
cd agentport-gateway
python3 scripts/init-compose.py
docker compose --profile image-demo up -d --build
```

1. 생성된 `.env`에서 관리자 계정을 확인하고 [대시보드](http://localhost:3000/app/orchestration)에 로그인합니다. 초기 비밀번호를 변경합니다.
2. **Services → Import service definition**에서 [image-webp.service.json](examples/image-webp.service.json)을 선택합니다.
3. **Photo to WebP → Use service**에서 PNG/JPEG 사진을 올리고 **Run**을 누릅니다.
4. **Activity**에서 진행 상황을 확인하고 완성된 WebP 파일을 내려받습니다.

일반 사용자는 등록된 서비스의 요청 화면만 사용합니다. 운영자가 새 서비스와 실행 환경을 연결하는 방법은 [Operator Quickstart](docs/operator-quickstart.md)를 참조하세요.

## Documentation

- [Installation](docs/deployment.md) — 설치, 다른 PC·모바일 접속, 원격 장치와 업데이트
- [Operator Quickstart](docs/operator-quickstart.md) — 사진 변환부터 runtime·agent 등록까지
- [Integrations](docs/integrations.md) — 하네스·모델 API·선택 제품의 실제 연결 범위
- [Architecture](docs/architecture.md) — 구성요소, 호출 경로와 재사용 경계
- [API Reference](docs/contracts.md) — REST·MCP·버전이 있는 정의 형식
- [Security](docs/security.md) · [Migration](docs/migration.md) · [Documentation Index](docs/README.md)

## Contributing

[Development Guide](docs/development.md)와 [Contributing Guide](CONTRIBUTING.md)를 참조하세요. 보안 문제는 [Security Policy](SECURITY.md)에 따라 보고해 주세요.

## License

[Apache-2.0](LICENSE). ContextForge 연결 패키지·Web UI의 출처와 변경 내역은 [NOTICE](NOTICE)와 [Reuse and Compatibility](docs/reuse-and-compatibility.md)에 기록합니다.
