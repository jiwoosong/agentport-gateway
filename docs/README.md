# Documentation

## Using AgentPort

- [Operator Quickstart](operator-quickstart.md): 사진 → WebP, 사용자 공유, 원격 도구·runtime·agent 연결
- [Deployment](deployment.md): 설치, HTTPS·모바일, Komodo·원격 장치, 업데이트·백업
- [Integrations](integrations.md): 하네스·모델 API·Hermes·NemoClaw·Paperclip의 연결 범위
- [Runtime Platform](runtime-platform.md): OpenShell 인증·sandbox, Hermes Relay 관측
- [Security](security.md): 사용자·서비스·네트워크·데이터 경계
- [Migration](migration.md): schema1 보존·이전, 백업·복원·제품 교체

## Architecture and Development

- [Architecture](architecture.md): 물리적 배치, native 호출 경로, 기존 제품과 자체 코드의 경계
- [API and Definition Contracts](contracts.md): REST·native MCP·runtime/provider/profile·그래프
- [Reuse and Compatibility](reuse-and-compatibility.md): 고정 버전·라이선스·재사용 범위
- [ContextForge Integration Maintenance](upstream-contextforge.md) · [Upstream Manifest](upstream-manifest.json)
- [Development](development.md) · [Contributing](../CONTRIBUTING.md)
- [Validation](validation.md): 실제 수행한 검증과 아직 검증하지 않은 환경

## Design History

- [Competitive Landscape](competitive-landscape.md): 이전 조사 기준의 유사 프로젝트·경쟁력 검토. 현재 구조는 Architecture를 우선합니다.
- [Code Review History](code-review.md): 이전 리팩터링 기록. 현재 코드·API의 기준은 위 문서입니다.

실제 장치·유료 모델·제품 전체 기능을 지원한다는 주장은 해당 실기 검증을 완료한 범위에 한합니다. template·모의 테스트·초기 ACP 협상을 전체 종단 간 검증과 구분합니다.
