# AgentPort Gateway

Self-hosted gateway for AI agents, tools, and workflows.

## Overview

AgentPort는 여러 장치에 배치한 AI 서비스와 에이전트를 웹·MCP로 사용하는 자체 호스팅 게이트웨이입니다. 운영자는 서비스와 접근 권한을 관리하고, 사용자는 허용된 도구를 선택해 작업을 요청하고 결과를 확인합니다.

기존 API·MCP 서비스와 에이전트 실행 환경을 연결합니다. 모델과 도구는 별도로 준비하며, Paperclip 같은 협업 서비스는 선택적으로 연결할 수 있습니다.

## Features

- **Service access:** 등록한 도구와 에이전트를 웹·REST API·MCP로 호출합니다.
- **Workflows:** 도구 호출, 에이전트 실행, 조건, 병렬 처리, 승인 단계를 그래프로 연결합니다.
- **Sessions and results:** 에이전트 세션을 선택하고 작업 상태와 결과를 조회합니다. 세션 재개는 연결한 어댑터의 지원 범위에 따릅니다.
- **Access control:** 사용자·프로젝트별 접근 범위를 설정하고 서비스 사용 권한과 운영 권한을 구분합니다.
- **Service lifecycle:** 연결한 실행 백엔드를 통해 상시 실행·요청 시 실행·수동 기동 정책을 적용합니다.

## Quick Start

### Prerequisites

Git, Python 3, Docker Engine과 Compose v2가 필요합니다. 기본 예제에는 GPU나 유료 AI 계정이 필요하지 않습니다.

### Installation

```bash
git clone https://github.com/jiwoosong/agentport-gateway.git
cd agentport-gateway
python3 scripts/init-compose.py
docker compose --profile demo up -d --build
```

1. 생성된 `.env` 파일에서 관리자 이메일과 비밀번호를 확인합니다. 이 파일은 공유하지 마세요.
2. [대시보드](http://localhost:3000/app/orchestration)에 로그인하고 초기 비밀번호를 변경합니다.
3. **Register service**에 [예제 서비스 정의](examples/converter.service.json)를 등록합니다.
4. **Use service**에서 텍스트를 전달하고, **Activity**에서 CSV 결과를 내려받습니다.

## Documentation

- [Getting Started](docs/deployment.md) — 설치, 팀 설정, 다른 PC와 모바일 접속
- [Integrations](docs/integrations.md) — 에이전트와 외부 서비스 연결
- [Architecture](docs/architecture.md) — 구성요소와 실행 방식
- [API Reference](docs/contracts.md) — REST·MCP와 서비스·워크플로우 정의
- [Security](docs/security.md) — 접근 권한, 네트워크, 데이터 보호 설정
- [Documentation Index](docs/README.md) — 운영, 개발, 호환성 문서

## Contributing

[Development Guide](docs/development.md)와 [Contributing Guide](CONTRIBUTING.md)를 참조하세요. 보안 문제는 [Security Policy](SECURITY.md)에 따라 보고해 주세요.

## License

[Apache-2.0](LICENSE). 재사용한 프로젝트의 출처와 라이선스는 [NOTICE](NOTICE)와 [Reuse and Compatibility](docs/reuse-and-compatibility.md)에 기록합니다.
