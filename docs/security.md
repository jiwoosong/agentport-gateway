# 보안 경계

기본 신뢰 모델은 운영자가 관리하는 소규모 팀입니다. 적대적 다중 고객·호스트 관리자 차단·외부 보안 감사 완료를 보장하지 않습니다.

## 적용한 경계

- ContextForge 인증과 현재 계정·팀·액션 권한을 외부 실행 직전 재검증합니다. UI에서 숨기는 것만으로 권한을 구현하지 않습니다.
- 도구 정의는 ContextForge native private/team 범위이고 AgentPort API는 private/project로 표현합니다. runtime·profile도 같은 프로젝트를 참조합니다. 실행·세션·결과·자격 증명은 사용자 경계를 적용합니다. 관리자는 신뢰 주체입니다.
- 자격 증명은 기존 ContextForge 암호화 기능을 재사용하고 조회 API에서 값/암호문을 반환하지 않습니다. 개인 키를 다른 사용자 실행에 사용하지 않습니다.
- 에이전트는 native API 토큰·virtual server와 실행 grant를 결합합니다. 사용자 권한과 profile 허용 목록의 교집합, 부모 실행·만료·도구 호출 한도를 검증합니다. 관리 REST에 사용할 수 없습니다.
- 중앙→워커 신원은 Ed25519 공개 키 검증입니다. 워커에 중앙 서명 비밀을 배포하지 않습니다. 네이티브 프로세스에 중앙 환경 변수를 상속하지 않습니다.
- 일반 사용자·에이전트에 Docker 소켓, 임의 배포 명령, 임의 URL override를 제공하지 않습니다. 설치·배포는 운영자만 할 수 있습니다.
- 외부 호출 결과가 불명확하면 중복 실행을 피하고 임대를 유지합니다. 업데이트는 실행 중 작업을 정리한 뒤 수행합니다.
- 감사 로그는 식별자·상태·해시 중심입니다. 프롬프트·API 키를 감사 기록에 넣지 않습니다. 실행 입력/결과는 복구를 위해 **로컬 DB에 저장**됩니다.

## Native Gateway and Runtime Boundaries

공개 AgentPort 도구와 private backend는 native Tool에 등록합니다. backend는 dispatcher guard가 있는 실행 컨텍스트에서만 호출하며 native REST/MCP 중계를 사용합니다. 서비스 설치 권한으로 임의 local handler를 등록하지 못하도록 운영 권한을 확인합니다. bootstrap은 소유권·visibility가 있는 private 자원을 재시작 시 public으로 변경하지 않습니다.

제한된 관리자·빈 team 범위·server-scoped token을 무제한 관리자 컨텍스트로 확대하지 않습니다. 인증한 caller 컨텍스트를 native RPC/MCP에서 유지하고 외부 단계 직전에 현재 권한을 재검증합니다. native 내부 backend의 REST mutation 자동 재시도를 끄고 응답 유실은 불명확 실행으로 남깁니다.

운영자가 같은 원격 endpoint를 별도의 무제한 native 도구로 등록하거나 외부에서 직접 공개하면 AgentPort 정책 밖의 경로가 생깁니다. 운영자는 방화벽·서비스 인증과 등록 정책으로 그 경계를 관리해야 합니다. 원격 실행 관리 API와 도구 실행 API는 서로 다른 권한입니다.

OpenShell·Komodo에는 중앙의 별도 운영 identity를 사용합니다. 해당 제품의 workspace/role/API key 범위를 설정해야 하며 공통 로그인으로 자동 사용자 위임이 되지 않습니다. OpenShell 인증은 mTLS 외에도 실제 role/workspace에 맞는 bearer/client credentials를 구성해야 합니다.

## local_only의 의미

`local_only: true`는 cloud 위치와 unrestricted 서비스를 거부합니다. `egress_policy: isolated`는 운영자의 검증 선언이며 네트워크 방화벽을 자동 적용하는 기능이 아닙니다. 실제 배포에서 외부 경로가 없는 네트워크, 제한된 마운트, 로컬 모델만 사용해야 합니다. 예제 converter는 Compose internal 네트워크만 사용합니다.

여러 서비스가 같은 내부 네트워크를 공유하면 서로 도달할 수 있으므로, 악의적인 코드나 민감한 데이터에는 신뢰 영역별 네트워크·호스트 방화벽·최소 포트 허용 규칙이 추가로 필요합니다. 게이트웨이 자체에는 외부 API 연결을 위한 네트워크 권한이 있습니다. 중앙 호스트가 침해되면 local_only 플래그가 데이터를 지켜주지는 않습니다.

외부 노드에는 승인한 정확한 입력 해시만 허용합니다. 승인 전 전달 필드·파일을 확인해야 합니다. 이 기능은 자동 개인정보 제거기가 아닙니다. 네이티브 클라우드 에이전트는 공급자 SDK의 자체 컨텍스트·도구·세션 정책을 사용하므로, 해당 에이전트에 보내는 작업공간·페르소나·세션도 민감도 검토 대상입니다.

## RAG·스킬·연결 서비스

RAG는 검색 시 원본 문서 권한을 검증해야 합니다. 게이트웨이 서비스 사용 권한만으로 모든 문서 읽기 권한을 대체할 수 없습니다. 서명한 요청 컨텍스트와 문서 ACL을 결합하고, 인덱싱·공유 지식 게시 권한을 검색 권한과 분리하세요. 악의적인 문서·모델 출력이 새 권한을 부여하지 않도록 도구 실행은 항상 게이트웨이에서 재검증합니다.

모든 대화를 자동으로 공유 RAG에 넣지 않습니다. 각 에이전트의 개인 메모리와 스킬은 하네스에 두고, 공유 게시 서비스를 별도 도구로 등록하여 명시적 요청·승인을 적용합니다. 메신저 사용자는 중앙 계정과 연결하기 전에는 도구를 호출할 수 없도록 후속 어댑터를 구현해야 합니다. 현재 메신저 연동은 없습니다.

## 운영상 남은 한계

DB 행 수준 암호화·KMS, 조직별 별도 DB/암호화 키, 사용자별 CPU/GPU 할당량, IP별 과부하 방어, 대용량 악성 파일 검사, 완전한 시크릿 회전·삭제 UI는 아직 제공하지 않습니다. DBOS 실행 기록과 워커 세션에는 사용자 콘텐츠가 저장되므로 디스크 암호화·보존/삭제 정책이 필요합니다. ContextForge 기본 로그 설정을 DEBUG로 변경하면 외부 라이브러리의 로그 내용도 다시 점검해야 합니다.

프런트엔드는 기존 BFF의 HttpOnly 쿠키·CSRF 경계를 유지합니다. 원격 접속에서 HTTPS, 정확한 PUBLIC_ORIGIN, COOKIE_SECURE=true, 신뢰할 수 있는 DNS/VPN 구성을 함께 적용하세요. 자체 호스팅해도 클라우드·Slack 등으로 보낸 데이터는 해당 제공자가 처리합니다.

## Native Credentials and Interactive Control

provider API 키와 구독/native login을 분리합니다. 프로필은 native 인증 파일을 중앙 요청으로 업로드하는 방식이 아니라 사전 설치한 private HOME의 opaque 참조입니다. 워커는 소유자·프로젝트·harness 일치를 검증하며 요청자가 임의 경로를 선택할 수 없습니다.

native 권한 요청은 실제 하네스가 제공한 option ID만 허용합니다. interactive가 기본이며 deny/container는 운영자가 선택하는 정책입니다. container 정책은 명시한 격리 경계 안에서 allow_once를 자동 선택할 수 있으므로 해당 환경에서만 사용합니다. 그래프 데이터 공개 승인과 동일한 권한으로 해석하지 않습니다.

워커 프로세스가 사라지면 pending native 승인과 실행을 확인 대상으로 남깁니다. 중앙/브라우저 연결이 끊겼다는 이유로 하네스의 외부 통신이 자동 차단되지는 않습니다. Relay는 현재 관측만 제공하고 enforcement는 거부합니다. 네트워크 차단은 OpenShell·방화벽 등의 실제 정책과 별도로 검증해야 합니다.

사진 예제는 입력 크기·포맷·decoded pixel 수를 제한하고 오류에 원본 bytes를 되돌리지 않습니다. 대용량 콘텐츠가 run DB와 DBOS 이력에 저장될 수 있으므로 디스크 용량·보존 정책을 운영해야 합니다. 파일 다운로드를 HTML preview로 렌더링하지 않습니다.

## Core and Gateway Bridge

Core는 native JWT/encryption master를 요구하지 않고 CF connector에 인증·권한·catalog·암복호화를 요청합니다. 기본 Compose는 두 secret을 gateway에만 전달합니다. 사용자·job 권한 검증을 공유 관리자 token으로 대체하지 않습니다. Core↔gateway의 운영 bridge는 별도 random AOG_GATEWAY_BRIDGE_SECRET으로 본문 hash·요청 ID·방향·짧은 만료·재전송을 검증합니다.

bridge와 shared AOG DB는 신뢰하는 제어 영역입니다. 두 이미지로 분리했다고 서로 침해를 격리하는 보안 경계가 되는 것은 아닙니다. bridge는 전송 암호화를 제공하지 않으므로 원격 분리에는 검증한 HTTPS/mTLS/사설 터널이 추가로 필요합니다. 공개 proxy에는 internal RPC 경로를 노출하지 않습니다. BFF는 내부 bridge 서명 header를 브라우저에서 전달받아 재사용하지 않습니다.

native caller의 계정·token·server scope 재검증은 CF connector 안에서 수행합니다. Core에는 해당 package/ORM이 없습니다. connector는 이 경계를 위해 검토한 호환 patch를 유지하며 순수 stock CF/CPEX로 동일한 보장을 제공한다고 주장하지 않습니다. gateway 장애 시 Core가 권한 확인을 생략하지 않습니다.
