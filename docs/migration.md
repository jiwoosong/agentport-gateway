# Migration and Data Preservation

## Scope

현재 구조는 활성 서비스를 ContextForge native Tool에 두고 runtime·provider·agent profile을 분리합니다. 기존 schema1 서비스의 별도 레지스트리와 `/orchestration/v1/mcp/`는 새 실행 경로가 아닙니다. 기존 완료 실행·단계·결과를 지우거나 외부 작업을 재실행하는 방식으로 이전하지 않습니다.

**활성·승인 대기·결과 불명확 작업은 이전 전에 종료·확인해야 합니다.** migration은 비종료 작업, 남은 임대, 시작/중지/업데이트 중이거나 error인 배포 상태를 거부합니다. 활성 DBOS checkpoint를 새 코드에 계속 실행하는 자동 변환은 제공하지 않습니다. 애플리케이션 실행 버전은 agentport-2입니다.

## Inventory Before Changes

| 보존 대상 | 위치·방법 |
|---|---|
| 계정·팀·역할·토큰·native Tool | ContextForge DB |
| 정의·리비전·작업·단계·결과·임대·세션 매핑 | AgentPort DB |
| 실행 checkpoint·대기 | DBOS system DB |
| 암호화·서명·JWT 키 | .env, gateway data/worker-signing.key, 외부 secret 설정 |
| 개인 native 로그인·메모리·스킬·작업 파일·job ledger | 각 워커 HOME/root·영속 볼륨 |
| OpenShell 상태·sandbox 파일·관리 인증 | OpenShell DB와 encryption key, sandbox 볼륨·설정 |
| Komodo 배포 정의·통신 키·상태 | Core DB·keys와 각 Periphery의 배포 파일·Docker 볼륨 |

중앙의 백업 명령은 접근 가능한 로컬 파일·설정만 보관합니다. 원격 워커·OpenShell·Komodo의 별도 데이터를 자동 백업하지 않습니다. native 로그인 HOME처럼 외부 경로를 manifest에 추가 안내하지만 전체 원격 계정을 복사하지 않습니다.

## Offline Procedure

1. 기존 버전에서 새 요청을 막습니다. 승인·실행·불명확 외부 작업을 확인하고 모두 종료 상태로 만듭니다. 임대를 임의 삭제하지 않습니다.
2. Core·워커·기존 단일 앱과 다른 writer를 멈춥니다. 새 gateway connector는 사용자-facing write 경로를 차단한 사설 migration 모드에서만 유지합니다. CLI가 인증된 bridge로 native 변환을 요청하므로 이 구성요소는 필요합니다. PostgreSQL 서버도 백업을 위해 유지합니다.
3. 같은 키·DB URL·파일 마운트를 유지한 새 소스 환경에서 의존성을 설치합니다. 운영 DB에는 uv sync가 아니라 실제 migration 명령이 쓰기 작업을 수행합니다.
4. private backup 경로를 정해 아래 명령을 실행합니다. --offline은 운영자가 writer를 멈췄다는 확인이며 프로세스 자동 탐지·중지는 아닙니다.

```bash
uv sync --frozen --extra dev
uv run aog backup --output /absolute/private/agentport-before-schema2
uv run aog migrate --offline --output /absolute/private/agentport-schema2-migration
```

migrate CLI는 **앱 import·변경 전에** 별도 백업을 생성하고, migration branch는 일반 Core prepare·Engine·DBOS를 시작하지 않고 connector의 migrate 계약을 호출합니다. gateway image를 최초로 교체할 때 native bootstrap이 발생할 수 있으므로 **교체 전 별도 백업도 먼저** 확보합니다. 두 경로는 존재하지 않는 새 디렉터리여야 합니다. PostgreSQL은 운영자가 설치한 pg_dump가 필요하고 DB URL의 연결 인증을 사용합니다. 제한된 기본 gateway 이미지에 백업 도구가 없으면 명령을 우회하지 말고 해당 도구와 같은 설정·볼륨을 갖춘 운영자 migration 환경을 준비합니다. 원격 DB URL·DNS가 그 환경에서 실제 운영 DB를 가리키는지도 확인합니다.

pg_dump를 migration 환경에 설치하기 어려우면 운영자가 같은 DB의 custom-format dump를 먼저 만들고 다음처럼 전달할 수 있습니다.

```bash
uv run aog backup --postgres-backup /absolute/private/agentport.dump --output /absolute/private/agentport-snapshot
uv run aog migrate --offline --postgres-backup /absolute/private/agentport.dump --output /absolute/private/agentport-migration
```

이 옵션은 설정된 PostgreSQL DB가 정확히 하나일 때만 허용합니다. ContextForge·AgentPort·DBOS의 세 URL이 같은 DB를 가리키는 구성은 해당됩니다. 입력 파일의 PGDMP header와 최소 크기를 검사하고 private 사본·hash·operator-provided 출처를 기록합니다. 이 기본 검사가 dump의 전체 무결성·현재 시점 일치·복원 가능성을 보장하지는 않습니다. writer를 멈춘 같은 시점의 dump와 파일·키를 사용하고 별도 복원 리허설을 수행해야 합니다.

backup manifest의 status completed, 파일 hash, absent_databases, external_backups_required를 검토합니다. 디렉터리·파일은 private 권한으로 생성되지만 저장 내용에는 **비밀·사용자 데이터가 포함되므로 별도 암호화·접근 정책이 필요**합니다. 이 백업을 Git에 올리지 않습니다.

## Conversion Rules

- additive schema version 2 테이블을 추가하고 기존 데이터베이스를 새 빈 DB로 교체하지 않습니다.
- 해석 가능한 schema1 도구는 native 공개 local tool + private backend로 등록합니다. 같은 ID를 유지한 service_alias에 새 tool ID·runtime ID·완료 상태를 남겨 기존 참조를 연결합니다.
- native commit과 AgentPort DB commit 사이에 종료되면 pending alias가 남습니다. 다시 offline migrate를 실행하면 migration_source_id/native 이름으로 이미 등록한 도구를 찾아 이어갑니다. 복구할 수 없는 충돌은 운영자 확인 대상으로 남깁니다.
- 기존 native target을 새 runtime으로 분리하고 phase·공개 범위·용량·실행 정책을 보존합니다. 중복 target은 임대 풀을 임의 합치지 않고 사전에 정리해야 합니다.
- 기존 agent는 인증·하네스를 추측하지 않고 agent_archive로 보관합니다. 명시한 runtime·harness·provider 또는 native login으로 새 profile을 구성합니다.
- 기존 kind model 또는 openai transport는 API 프로토콜·인증 의미가 불명확하므로 service_archive로 보존합니다. 올바른 프로토콜·하네스/도구 설정으로 다시 등록합니다.
- 완료 Run/Step/result·기존 session reference·credential은 유지합니다. 보관된 profile/session이 새 하네스와 자동 호환되는 것은 아닙니다. 새 profile로 session을 옮기는 동작은 제공하지 않습니다.
- archive를 참조하는 workflow는 운영자가 검토해 새 profile/tool ID로 수정합니다. 예전 정의와 완료 실행 기록은 이전 이력을 위해 남깁니다.
- 호환되는 기존 workflow는 같은 정의·ID를 유지하고 native 호출 도구를 게시합니다. archive를 참조하는 그래프는 자동 실행 가능 상태로 바꾸지 않고 보고서의 workflows_requiring_review에 남깁니다.

모델·agent의 archive 처리는 삭제나 자동 재실행이 아닙니다. 보관한 정의를 확인하고 사용자별 인증을 명시한 후 다시 활성 구성으로 만듭니다. 새 agent 세션으로 검증합니다.

## Validate Before Reopening

1. migration 보고서의 migrated/resumed/archive 수와 실제 보존 데이터를 대조합니다.
2. 두 사용자 로그인·team·제한 token 범위·native Tool 목록을 확인합니다.
3. 기존 완료 작업 ID와 결과 hash가 그대로이며 외부 도구 호출 횟수가 늘지 않았는지 확인합니다.
4. 새 native MCP 주소·tools/list·작업 조회를 확인합니다. 오래된 클라이언트 `/orchestration/v1/mcp/`를 `/mcp` 또는 native virtual-server 주소로 바꿉니다.
5. 공유 runtime 두 도구로 단일 기동·사용 중 stop/update 거부를 검증합니다.
6. 새 provider/profile과 신규 세션을 검증하고 결과·native 승인·취소를 확인한 뒤 일반 요청을 엽니다.

실제 PostgreSQL 운영 복원과 하드웨어·native 로그인은 별도 검증입니다. 자동 테스트가 파일 변환·모의 worker·SQLite 이전을 통과했다고 실제 계정 재개까지 보장하지 않습니다.

## Rollback and Exit

실패한 migration 이후 **이미지 버전만** 내리지 않습니다. Core·gateway·worker writer를 다시 정지하고 이전 코드와 같은 시점의 세 DB·gateway/worker 볼륨·키를 별도 복원 환경에서 복구합니다. 새로운 버전이 만든 데이터는 별도 보존해 필요한 결과를 수작업으로 회수합니다. DB 스키마의 자동 downgrade는 제공하지 않습니다.

프로젝트를 다른 제품으로 옮길 때 portable 정의와 실행 상태를 구분합니다.

| 대상 | 이동 가능 자료 | 새 시스템에서 필요한 작업 |
|---|---|---|
| 도구·runtime·provider·그래프 | 버전 JSON/YAML·입출력 스키마·선택된 API endpoint | native ID·team·credential 참조 재매핑, 그래프 노드 의미 구현 |
| 결과·이력 | 원본 DB 백업·결과 파일 | 새 UI/감사 조회 또는 보관 뷰 |
| DBOS 활성 실행 | DBOS native checkpoint | 원래 코드로 마무리·확인 후 새 실행. 범용 이식 계약 없음 |
| 하네스 세션·메모리·스킬 | 전용 HOME·workspace·job ledger | 해당 하네스 버전·인증·경로 계약 유지 |
| 계정·토큰·제품 권한 | ContextForge DB·운영 정책 | 새 인증 제품의 명시적 계정·권한 변환. 토큰 무작정 재사용 금지 |
| 배포 상태 | Komodo/OpenShell native 상태·설정 | 실제 프로세스와 대조 후 새 관리 주체에 연결 |

ContextForge 교체는 native ToolService·인증 bridge·UI/BFF를, DBOS 교체는 대기·체크포인트·실행 코드 일부를 바꾸는 작업입니다. Windmill을 핵심 runtime으로 두지 않았으므로 native flow/job DB 이전은 발생하지 않습니다. 모든 제품을 비용 없이 바꿀 수 있다는 주장은 하지 않습니다.

## Split an Existing Embedded Installation

새 설치는 Core 0.2.0과 ContextForge connector 1.1.0-1을 별도 프로세스로 실행합니다. 기존 단일 앱 prototype과 새 Core를 같은 DB에 동시에 실행하지 않습니다.

1. 이전 writer를 모두 정리·정지하고 native DB·AOG DB·DBOS DB·worker signing key·사용자 파일·키를 백업합니다. 기존 소스/이미지도 복원용으로 보관합니다.
2. 기존 .env에 없는 AOG_GATEWAY_BRIDGE_SECRET을 로컬에서 생성하고 private 권한을 유지합니다. 기존 native JWT·encryption key를 새로 생성하지 않습니다.
3. gateway와 Core의 AOG_DATABASE_URL을 같은 기존 AOG DB에 연결합니다. native DATABASE_URL은 gateway에, DBOS_SYSTEM_DATABASE_URL은 Core에만 둡니다. 기존 worker-signing.key가 들어 있는 gateway-data도 양쪽에 같은 볼륨으로 연결합니다.
4. 고정 gateway connector를 먼저 준비하고 Core 정지 상태에서 필요 migration을 수행합니다. native IDs·완료 결과·암호화 key·작업 공간을 보존합니다.
5. Core가 bridge contract 1을 확인하고 시작한 뒤 BFF의 AGENTPORT_CORE_URL을 새 Core로 설정합니다. 기존 CONTEXTFORGE_URL은 native gateway로 유지합니다.
6. 실제 두 프로세스의 로그인·두 사용자·native MCP·승인·취소·세션·결과·중복 방지를 확인한 뒤 일반 요청을 엽니다.

기존 키가 있는 .env에 bridge secret만 추가하는 예:

```bash
python3 - <<'PYKEY'
import secrets
from pathlib import Path
path = Path('.env')
lines = path.read_text().splitlines()
if any(line.startswith('AOG_GATEWAY_BRIDGE_SECRET=') for line in lines):
    raise SystemExit('Bridge key already exists; verify it locally instead of replacing it')
with path.open('a') as output:
    output.write('\nAOG_GATEWAY_BRIDGE_SECRET=' + secrets.token_urlsafe(48) + '\n')
path.chmod(0o600)
PYKEY
```

Core image는 host .env·native JWT/encryption master key를 포함하지 않습니다. Core 컨테이너 안에서 실행한 backup만으로 host의 native key가 보관된다고 가정하지 않습니다. host .env·Compose 설정·gateway native 인증 설정을 운영자가 별도 암호화 백업해야 합니다. gateway-data에는 shared worker signing key가 있으므로 해당 볼륨도 반드시 보존합니다.

구성요소는 별도 이미지이지만 현재 connector는 AOG schema 2 runtime binding·grant/job metadata를 공유합니다. 호환 contract/schema 조합을 벗어나는 교체는 별도 migration입니다. 변경 없는 component 재기동과 데이터 이전을 구분하세요.
