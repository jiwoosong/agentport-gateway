# AgentPort 문서

## 사용과 운영

- [설치와 운영](deployment.md): 계정, 다른 PC·모바일 접속, 서비스 배포, 업데이트와 백업
- [연결 가능한 제품](integrations.md): Hermes, NemoClaw, Paperclip 연결과 제한
- [OpenShell·Relay 설정](runtime-platform.md): 샌드박스 실행과 활동 기록
- [지원 및 검증 현황](#지원-및-검증-현황): 확인된 기능과 아직 검증하지 않은 환경

## 개발과 구조

- [개발 환경](development.md): 소스 실행과 테스트
- [아키텍처](architecture.md): 구성요소, 권한, 실행과 복구
- [API·서비스·워크플로우 계약](contracts.md): REST, MCP, JSON/YAML 정의
- [보안 경계](security.md): 데이터 접근, 외부 통신, 운영 책임
- [재사용과 호환성](reuse-and-compatibility.md): 기술 스택, 버전, 라이선스
- [코드 검토·리팩터링 기록](code-review.md)
- [검증 기록](validation.md): 테스트 결과와 남은 제약
- [기여 안내](../CONTRIBUTING.md)

## 지원 및 검증 현황


| 영역 | 현재 상태 |
|---|---|
| ContextForge 로그인, 사용자·프로젝트 경계, 비공개 실행·결과 | 실제 HTTP 로그인 및 두 사용자 격리 테스트 |
| REST `/orchestration/v1`, MCP SDK 엔드포인트 | 실제 MCP 초기 연결·도구 호출 테스트 |
| 비동기 작업, 병렬·합류·조건·승인, 그래프 버전 고정 | DBOS 실행 및 실제 서버 테스트 |
| 재시작 복구와 중복 실행 방지 | 승인 대기 중 프로세스 강제 종료·재시작 테스트 |
| 단일 기동, 사용 중 보호, 유휴 중지, 실패 시 배포 복구 | 동시성·모의 Komodo 계약 테스트; 실제 장치 검증은 별도 |
| Codex·Claude ACP | 실제 초기 연결 확인, 모의 ACP 프로세스로 세션·재개 검증; 유료 추론 미검증 |
| React Flow 대시보드, 기존 로그인 BFF | 빌드·회귀 테스트; 상세 결과는 검증 문서 |
| Docker Compose, PostgreSQL, Redis | Ubuntu amd64 CI에서 실제 빌드·로그인·DBOS 변환·BFF 세션·결과 조회 통과 |
| 원격 PC·DGX GPU, HTTPS profile | 배포 구성 제공; 실장비·원격 연결 미검증 |
| Paperclip·RAG·OpenAI 호환 API·HTTP/MCP 도구 | 선언형 연결 계약. 해당 제공자별 전체 기능 검증을 의미하지 않음 |
| Hermes ACP·NeMo Relay | 실행 프로필·로컬 활동 요약·모의 ACP/ATOF 검증. 실제 Hermes 추론 미검증 |
| OpenShell 0.1.7 | 선택적 시작·중지·상태 SDK backend 및 UI. 실제 SDK/모의 gRPC 검증, 실샌드박스 미검증 |
| NemoClaw·OpenClaw·Kimi, 메신저 | 전용 운영 어댑터 미구현. 기존 서비스 계약으로 확장 |

[검증 기록](validation.md)에서 실제 실행한 항목과 미검증 항목을 구분합니다. 보안 감사 완료나 인터넷 공개형 다중 고객 격리를 주장하지 않습니다.

