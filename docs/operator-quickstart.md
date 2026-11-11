# Operator Quickstart

이 가이드는 운영자가 기능 하나를 등록하고 일반 사용자가 파일 결과를 받는 경로를 설명합니다. 서비스 컨테이너 설치와 서비스 호출은 별도 단계입니다. Docker 이미지가 있다는 것만으로 HTTP/MCP 도구가 만들어지는 것은 아닙니다.

## 1. Run the Photo Example

저장소 루트에서 실행합니다. 기존 설치가 있다면 먼저 [Migration](migration.md)을 확인하세요.

```bash
python3 scripts/init-compose.py
docker compose --profile image-demo up -d --build
docker compose ps
```

`.env`는 소유자만 읽을 수 있도록 생성됩니다. 관리자 이메일·비밀번호를 로컬에서 확인하고 `http://localhost:3000/app/orchestration`에 로그인합니다. 초기 비밀번호를 변경합니다.

**Services → Import service definition**에서 `examples/image-webp.service.json`을 선택합니다. 별도의 API 키는 필요하지 않습니다. 정의는 이미 실행한 `image-converter`의 POST API를 가리키며, 외부 네트워크 없는 CPU 서비스로 등록합니다. runtime_id가 비어 있으면 해당 정의로 manual external runtime을 생성합니다. 기본 설치의 gateway·Core 분리는 사용자 요청 화면을 바꾸지 않습니다.

**Photo to WebP → Use service**에서 PNG/JPEG 파일과 품질을 선택하고 Run을 누릅니다. 기본 품질은 80입니다. Activity에서 완료된 작업을 열고 Download converted.webp를 누릅니다. 요청·결과는 로그인한 사용자에게 연결되며 브라우저를 닫아도 작업은 유지됩니다.

예제는 Pillow의 CPU 변환입니다. 입력 20 MiB·16 MP, 출력 20 MiB를 제한하고 다중 프레임·비이미지 입력을 거부합니다. EXIF 방향을 적용한 뒤 메타데이터를 제거합니다. AI 이미지 생성, GPU 추론, 악성 파일 분석 서비스를 의미하지 않습니다. 현재 파일 전송은 base64 JSON이며 대용량 영상은 이 계약의 대상이 아닙니다.

## 2. Give Another User Access

기존 ContextForge 사용자·팀·역할 관리에서 사용자와 프로젝트 팀을 생성합니다. 도구 읽기·실행 권한을 부여하고 해당 사용자를 팀에 추가합니다. AgentPort의 project_id는 그 native team ID입니다.

서비스 정의를 팀에 공유할 때는 `project_id`와 `visibility: project`를 함께 설정합니다. 일반 서비스 폼은 기본 비공개입니다. 공유 정의를 가져오거나 REST로 수정할 수 있습니다. 예를 들어 sample JSON 복사본에 아래 필드를 추가한 뒤 등록합니다.

```json
{"project_id": "CONTEXTFORGE_TEAM_ID", "visibility": "project"}
```

여러 도구를 하나의 컨테이너에 연결한다면 먼저 Runtimes에서 공유 runtime을 만들고 모든 서비스 정의의 runtime_id에 **같은 ID**를 넣습니다. sample을 반복 가져오면 다른 도구가 추가되므로, 기존 정의 수정에는 Configure 또는 PUT API를 사용합니다. 공유 도구의 입력·결과·세션·개인 키가 팀 전체에 공개되는 것은 아닙니다.

두 번째 사용자로 로그인해 Photo to WebP를 실행합니다. 첫 사용자 작업 ID를 직접 조회해 접근이 거부되는지도 확인합니다. 관리 화면의 존재만으로 격리를 검증했다고 판단하지 않습니다.

## 3. Connect a Remote Container

다른 PC·DGX의 운영자가 저장소와 호환되는 아키텍처에서 이미지를 빌드합니다.

```bash
docker build -f deploy/image-service.Dockerfile -t agentport-image-converter:0.1.0 .
```

호스트의 사설 인터페이스 주소를 실제 값으로 정하고 실행합니다.

```bash
export TOOL_BIND_IP=192.168.10.25
docker run -d --name agentport-image-converter \
  --restart unless-stopped --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges \
  --memory 1g --cpus 2 \
  -p "${TOOL_BIND_IP}:8080:8080" agentport-image-converter:0.1.0
```

이 예제 API 자체에는 로그인 기능이 없습니다. 방화벽에서 중앙 gateway 장치만 허용하거나 검증한 mTLS/사설 프록시를 앞에 둡니다. 공개 인터페이스에 노출하지 않습니다. TOOL_BIND_IP는 중앙에서 도달 가능한 사설 IP이며, 예제 주소를 자신의 장치 주소로 바꿉니다.

중앙의 SSRF_ALLOWED_NETWORKS JSON allowlist에 해당 장치의 `/32` 주소를 추가하고 gateway를 재생성합니다. 전체 사설망 허용으로 바꾸지 않습니다. sample의 endpoint·health_url을 원격 주소로 바꿔 등록하면 일반 사용자 화면은 동일합니다. 도구 컨테이너의 실제 외부 통신을 차단한 경우에만 isolated를 유지하세요. 로컬 Compose의 internal 네트워크와 원격 호스트의 기본 Docker 네트워크는 같은 격리가 아닙니다.

원격 에이전트가 중앙 도구를 호출한다면 `AOG_INTERNAL_GATEWAY_URL`도 워커가 접근할 수 있는 중앙의 사설 HTTPS 주소로 설정합니다. 기본 `http://gateway:4444`는 같은 Compose 네트워크용 이름입니다. 워커의 네트워크 정책과 인증서 신뢰 설정에도 해당 주소를 허용합니다.

## 4. Add Lifecycle Management

manual은 **요청 수동 처리**가 아니라 **운영자 기동·종료 관리**입니다. 컨테이너가 실행 중이면 AgentPort는 권한 확인 후 API로 요청을 전달합니다.

요청 시 기동이 필요하면 Komodo에서 실행 장치와 이미 배포된 스택을 준비합니다. Runtimes → New runtime에서 backend Komodo, 실제 stack ID, readiness URL과 정책을 등록합니다. Services의 기능 정의에서 해당 runtime을 선택합니다.

- manual: 자동 시작·유휴 종료 없음. 운영자가 켜고 끕니다.
- always_on: 요청 시 준비 상태를 확보하고 유휴 자동 종료 없음.
- on_demand: 요청 시 단일 기동·준비 대기, 마지막 임대 종료 후 유휴 중지.

runtime concurrency는 그 컨테이너에 연결된 **모든 도구의 합계**입니다. 활성 작업이 있으면 stop/update를 거부합니다. 중앙 밖의 요청·cron·자체 daemon 작업은 임대에 잡히지 않으므로 이런 runtime에는 유휴 자동 종료를 사용하지 않습니다.

장치 설치·스택 생성·이미지 선택·GPU 설정은 Komodo 운영 작업입니다. AgentPort가 임의 Compose 명령을 실행하는 기능은 없습니다. [Deployment](deployment.md#komodo-and-remote-devices)에 연결 순서를 설명합니다.

## 5. Add an Agent

1. 사용자·프로젝트·하네스 신뢰 경계별 전용 ACP 워커와 영속 볼륨을 준비합니다. [Worker Profiles](../workers/acp/README.md)를 따릅니다.
2. runtime을 등록하고 `kind: agent`, `transport: acp`, `endpoint: http://WORKER:8090/invoke`, 같은 runtime_id를 가진 서비스 정의를 등록합니다. runtime마다 ACP worker 도구는 정확히 하나여야 합니다.
3. Providers에서 본인의 API 키와 프로토콜·base URL·모델을 등록하거나, 운영자가 준비한 native 로그인 프로필을 `/credentials/native`로 등록합니다. native profile_ref는 경로가 아닌 opaque 이름입니다.
4. Agents & sessions에서 runtime·harness·provider 또는 native login을 선택하고 persona와 허용 중앙 도구를 지정합니다.
5. 신규 세션으로 작업을 보내고 Activity에서 native 승인 옵션·취소·결과를 확인합니다. 재개는 해당 하네스의 협상 결과가 지원할 때만 사용합니다.

Codex는 Responses, Claude는 Messages 연결을 구분합니다. OpenCode의 provider template은 세 프로토콜을 각각 native 설정으로 연결합니다. Kimi/Hermes 등의 네이티브 설정, 실제 로그인과 모델 도구 호환성은 별도 준비·검증이 필요합니다. 하네스 목록은 설치 감지가 아닌 제공하는 어댑터 template입니다. [Integrations](integrations.md)에 실제 지원 범위를 구분합니다.

## Completion Checks

- 일반 사용자가 Docker·주소·장치를 입력하지 않고 사진을 올리고 WebP 결과를 받는다.
- 다른 PC/모바일에서도 같은 계정으로 진행·결과를 확인한다. 원격 HTTPS 설정은 별도로 적용한다.
- 다른 사용자의 작업·결과·세션·개인 키에 접근할 수 없다.
- 같은 runtime에 연결한 A/B 도구 동시 요청이 중복 기동을 만들지 않으며, B 실행 중 stop/update는 거부된다.
- 완료 단계를 포함한 작업을 재시작 복구할 때 완료 호출을 중복 실행하지 않는다. 불명확한 외부 호출은 확인 대상으로 남긴다.

앞의 파일 변환과 UI는 자동 테스트 대상입니다. 실제 DGX·Komodo·OpenShell·유료 하네스 실행·원격 모바일 접속까지 검증했다는 뜻은 아닙니다. 현재 실행 결과는 [Validation](validation.md)을 확인하세요.
