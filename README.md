# AgentPort

**내 서버의 AI 도구와 에이전트를 한곳에서 사용하는 자체 호스팅 게이트웨이입니다.**

서비스를 등록하고 팀에 공유하면, 사용자는 웹이나 MCP로 작업을 요청하고 진행 상황과 결과를 확인할 수 있습니다. 여러 장치에 있는 서비스를 하나의 대시보드에서 관리합니다.

## 무엇을 할 수 있나요?

- **도구 사용:** 파일 변환, 검색, 이미지 처리 등 연결한 서비스에 요청을 보내고 결과를 받습니다.
- **에이전트 작업:** 에이전트와 세션을 선택하고, 역할과 사용할 도구를 지정합니다.
- **작업 흐름 구성:** 작성 → 검수 → 승인처럼 여러 단계를 그래프로 연결합니다.
- **팀과 공유:** 사용자·프로젝트별로 접근 범위를 나누고 작업 결과를 관리합니다.
- **서비스 운영:** 서비스를 켜고 끄며, 상시 실행하거나 요청할 때 실행하도록 설정합니다.

사용할 모델과 도구는 직접 연결합니다. AgentPort는 연결된 서비스의 접근과 실행을 관리합니다.

## 빠른 시작

Git, Python 3, Docker Engine과 Compose v2가 필요합니다. 기본 예제에는 GPU나 유료 AI 계정이 필요하지 않습니다.

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

## 더 알아보기

- [설치·팀 설정·다른 PC와 모바일 접속](docs/deployment.md)
- [에이전트와 외부 서비스 연결](docs/integrations.md)
- [전체 문서와 지원 현황](docs/README.md)
- [개발 환경](docs/development.md) · [기여 안내](CONTRIBUTING.md)

현재 초기 개발 단계이며 웹·MCP를 우선 지원합니다. 메신저 연동은 후속 범위입니다. 외부 접속과 민감한 작업에 사용하기 전 [보안 경계](docs/security.md)와 [검증 현황](docs/validation.md)을 확인하세요.

[Apache-2.0](LICENSE) · [출처 고지](NOTICE) · [보안 보고](SECURITY.md)
