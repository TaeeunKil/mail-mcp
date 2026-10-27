# 개인 네이버 메일 등록 및 첫 테스트

이 안내는 **본인 네이버 메일 한 계정**으로 시작합니다. 비밀번호를 프로젝트 관리자나 AI 채팅에 보내지 마세요. 아래 작업은 자신의 신뢰하는 컴퓨터에서 진행합니다. 저장소를 내려받거나 설치하는 것만으로 계정이 연결되지는 않습니다.

## 1. 설치

Python 3.11 이상이 필요합니다. macOS/Linux bash 기준입니다.

```bash
git clone https://github.com/TaeeunKil/mail-mcp.git
cd mail-mcp
python3 -m venv .venv
.venv/bin/python -m pip install '.[test]'
.venv/bin/python -m pytest -q
```

여기까지는 실제 메일 로그인 없이 실행됩니다. Windows에서는 `.venv\Scripts\python.exe`와 해당 셸의 환경 변수 설정을 사용하세요.

## 2. 네이버 설정 준비

1. 네이버 메일 PC 웹의 환경설정 → POP3/IMAP 설정 → IMAP/SMTP 설정에서 IMAP 사용을 켭니다.
2. 네이버 계정의 2단계 인증이 활성화되어 있는지 확인합니다.
3. 이 용도에 사용할 애플리케이션 비밀번호를 준비합니다. 이미 이 연동용으로 발급한 비밀번호를 안전하게 보관하고 있다면 그대로 사용할 수 있습니다. 불필요하게 새 비밀번호를 만들 필요는 없습니다. 별도 발급이 필요한 경우 본인이 네이버 계정 보안 화면에서 진행하세요.
4. 일반 로그인 비밀번호 대신 애플리케이션 비밀번호를 사용합니다. 전용 앱 비밀번호는 독립적으로 폐기할 수 있지만, 권한이 읽기 전용으로 한정되는 것은 아닙니다.

현재 화면과 정책은 [네이버 공식 설정 안내](https://help.naver.com/service/30029/contents/21344), [연결 오류 안내](https://help.naver.com/service/30029/contents/21351)를 기준으로 확인하세요.

## 3. 비밀 없는 계정 설정 등록

프로젝트 폴더에서 아래 명령은 **비밀을 포함하지 않는** `accounts.json`만 만듭니다. 기존에 본인이 만든 설정이 있다면 덮어쓰지 말고 그 파일을 편집하세요.

```bash
cat > accounts.json <<'JSON'
{
  "accounts": {
    "naver": {
      "host": "imap.naver.com",
      "port": 993,
      "username_env": "NAVER_IMAP_USER",
      "password_env": "NAVER_IMAP_APP_PASSWORD"
    }
  }
}
JSON
chmod 600 accounts.json
```

- `naver`: 도구에 전달할 계정 ID입니다.
- `host`, `port`: 공급자가 지정한 IMAP TLS 서버입니다.
- `username_env`, `password_env`: 실제 로그인 정보가 들어갈 **환경 변수 이름**입니다. 이 자리에 실제 아이디나 비밀번호를 쓰지 않습니다.
- 파일은 `.gitignore`에 포함됩니다. 공유할 설정은 `accounts.example.json`의 더미 예시를 사용하세요.

## 4. 현재 터미널 세션에만 로그인 정보 입력

아래 명령 자체에는 비밀값이 없으므로 비밀번호가 셸 명령 기록에 남지 않습니다. 비밀번호 프롬프트의 입력은 표시되지 않습니다. 아이디는 본인의 네이버 로그인 ID를 입력하세요.

```bash
read -r -p 'Naver login ID: ' NAVER_IMAP_USER
read -r -s -p 'Naver app password: ' NAVER_IMAP_APP_PASSWORD; printf '\n'
export NAVER_IMAP_USER NAVER_IMAP_APP_PASSWORD
export IMAP_ACCOUNTS_FILE="$PWD/accounts.json"
```

비밀번호를 `export NAVER_IMAP_APP_PASSWORD=실제값`처럼 명령에 직접 쓰거나 `.env`, 설정 JSON에 저장하지 마세요. 환경 변수도 같은 사용자 권한의 다른 프로그램에 노출될 수 있으므로 공유 컴퓨터에서는 사용하지 마세요.

## 5. 로컬에서 한 번 읽기 시험 (MCP 앱 없이도 가능)

같은 터미널에서 아래를 실행하면 계정 준비 여부 → 폴더 목록 → 받은편지함 최근 20개 중 최대 3개 헤더 → 첫 결과의 본문 최대 1,000자를 출력합니다. **실제 네이버 서버에 로그인하고 메일을 읽는 단계**입니다. 주변 사람이 화면을 볼 수 없는지 확인하고 출력 내용을 공개 이슈에 붙이지 마세요. 발송·삭제·이동·읽음 표시는 하지 않습니다.

```bash
.venv/bin/python - <<'PY'
import json
import os
from imap_connector.core import Connector, ConnectorError, load_accounts

try:
    connector = Connector(load_accounts(os.environ['IMAP_ACCOUNTS_FILE']))
    print(json.dumps(connector.list_accounts(), ensure_ascii=False, indent=2))
    print(json.dumps(connector.list_folders('naver'), ensure_ascii=False, indent=2))
    result = connector.search_messages('naver', scan_limit=20, limit=3)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result['messages']:
        message = connector.read_message(result['messages'][0]['id'], max_chars=1000)
        print(json.dumps(message, ensure_ascii=False, indent=2))
    else:
        print('검색 결과가 없습니다. 폴더와 최근 검색 범위를 확인하세요.')
except ConnectorError as exc:
    print(str(exc))
PY
```

이는 사용자 본인이 실행할 수 있는 시험 방법입니다. 프로젝트 제작 과정에서 실제 계정으로 실행했다는 뜻은 아닙니다. 테스트 전후 네이버 웹메일에서 읽음 상태가 바뀌지 않았는지도 확인하세요.

## 6. 로컬 stdio MCP 호스트에 등록

로컬 MCP 서버 실행을 지원하는 신뢰하는 호스트가 필요합니다. 제품마다 설정 메뉴와 환경 변수 전달 방식이 다릅니다. 아래는 일반적인 형식입니다.

```json
{
  "mcpServers": {
    "mail-mcp": {
      "command": "/absolute/path/mail-mcp/.venv/bin/python",
      "args": ["-m", "imap_connector.server"],
      "env": {
        "IMAP_ACCOUNTS_FILE": "/absolute/path/mail-mcp/accounts.json"
      }
    }
  }
}
```

1. `/absolute/path/mail-mcp`를 실제 설치 경로로 바꿉니다.
2. 호스트가 `NAVER_IMAP_USER`와 `NAVER_IMAP_APP_PASSWORD`를 서버 프로세스에 전달하도록 합니다. 위 터미널에서 호스트를 직접 실행해 상속시키거나, 호스트가 지원하는 보안 저장 기능을 사용하세요. 이미 켜진 GUI 앱에는 터미널의 변수가 자동 적용되지 않습니다.
3. 등록한 서버를 다시 시작하고 `list_accounts`의 `configured`가 true인지 확인합니다. 이 값은 환경 변수 존재 여부일 뿐 로그인 성공을 뜻하지 않습니다.
4. `list_folders(account="naver")`를 실행합니다. 성공하면 실제 인증과 목록 읽기가 된 것입니다.
5. `search_messages(account="naver", mailbox="INBOX", limit=3, scan_limit=20)`을 실행합니다.
6. 결과의 `id`를 그대로 `read_message(message_id="해당 id")`에 전달합니다.

`text="계약"`, `sender="sender@example.invalid"`, `since="2026-10-01"` 같은 선택 필터도 있습니다. 처음에는 필터 없이 연결을 먼저 검증하세요. 검색은 기본 최근 200개, 최대 500개 안에서만 수행합니다.

**ChatGPT/dot의 원격 연결은 아직 구현되어 있지 않습니다.** 이 설정 JSON을 채팅에 붙이는 것으로 dot이 메일을 읽게 되지 않습니다. 원격 연결에는 지원되는 연결 경로 확인, 인증·인가, 안전한 비밀 저장, 계정 격리 구현이 필요합니다. 인증 없는 서버나 터널로 공개하지 마세요.

## 7. 종료 또는 연결 해제

로컬 MCP 호스트/서버를 종료한 다음 같은 터미널에서 지웁니다.

```bash
unset NAVER_IMAP_USER NAVER_IMAP_APP_PASSWORD IMAP_ACCOUNTS_FILE
```

이미 실행 중인 자식 프로세스의 환경 변수까지 제거되는 것은 아니므로 호스트/서버도 종료해야 합니다. 영구 해제나 노출 의심 시 네이버에서 해당 앱 비밀번호를 폐기하세요.

## 회사 계정은 나중에

`accounts.example.json`의 `work`는 구조 예시이며 실제 연결 정보가 아닙니다. 공급자가 IMAP TLS + LOGIN/앱 비밀번호 인증을 허용하는 경우에만 관리자에게 확인한 호스트·포트·로그인 방식으로 추가할 수 있습니다. OAuth 전용, STARTTLS 전용, 사내망 전용 환경은 이 버전으로 바로 지원되지 않습니다. 네이버 개인 계정 테스트에 회사 계정은 필요하지 않습니다.

## 본문 및 첨부 메타데이터 조회

`UIDVALIDITY=0`인 경우 `search_messages`의 `message_ref`와 `Message-ID`를 `read_message_by_reference`에 전달하면, 매 호출마다 최근 메일에서 헤더·크기·INTERNALDATE를 다시 맞춰 확인한 후 텍스트 본문을 읽습니다. 잘못된 사용자 헤더를 포함한 메일 내용은 모두 불신 데이터입니다.

`list_attachments`는 BODYSTRUCTURE에서 파일명, MIME 타입, 서버 보고 크기를 확인하고, 확인 가능한 인코딩은 변환 후 크기를 추정해 표시합니다. 첨부 payload를 다운로드하거나 저장하는 기능은 없습니다.
