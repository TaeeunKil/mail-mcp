# mail-mcp

네이버 메일 한 계정을 대상으로 하는 읽기 전용 MCP입니다. 로컬 `stdio` 사용을 유지하고, Vercel용 stateless Streamable HTTP 진단 경로와 데스크톱 Codex용 개인 Bearer 인증 모드를 제공합니다. Python 3.11 이상과 MCP Python SDK 2.3.0을 사용합니다.

## 지원 범위와 현재 상태

- `list_accounts`, `list_folders`, `search_messages`, `read_message`만 제공합니다. 메일 발송, 삭제, 이동, 읽음 변경, 첨부 다운로드, SMTP 기능은 없습니다.
- 원격 Naver IMAP 목적지는 `imap.naver.com:993` TLS로 고정됩니다.
- 원격 기본 모드는 `diagnostic`이며 정적 `service_status` 하나만 공개합니다. 메일 환경변수는 이 모드에서 읽지 않습니다.
- `MAIL_MCP_MODE=token`을 명시했을 때만 Bearer 인증 뒤 메일 도구를 등록합니다. 토큰 또는 네이버 설정이 누락/잘못되면 `/mcp`는 503으로 fail closed합니다. 모든 MCP 요청은 `Authorization: Bearer …` 인증을 먼저 통과해야 합니다.
- Bearer 방식은 데스크톱 Codex용이며 ChatGPT 웹/cloud dot에는 OAuth 모드가 필요합니다. Supabase public PKCE OAuth client는 등록됐고 DCR은 꺼져 있습니다. 사용자가 Vercel OAuth 설정과 owner/client 제한 Hook을 구성했으며, end-to-end 로그인 확인은 진행 중입니다.
- Production에는 Naver IMAP 자격증명이 없습니다. 현재 배포된 코드는 OAuth와 IMAP 준비 상태를 함께 확인해 `/mcp`에 503을 반환합니다. 이 변경은 이를 분리해 인증 challenge와 비민감 상태 확인을 허용하되, 메일 도구는 IMAP 자격증명이 생길 때까지 등록하지 않습니다.

[데스크톱 Codex/Vercel 설정 안내](VERCEL.md), [네이버 로컬 첫 연결 안내](NAVER_SETUP.md), [보안 안내](SECURITY.md)를 확인하세요.

## 처음 시작하기

로컬 stdio 사용에는 [설치 및 계정 설정](#1-설치-macoslinux), Vercel을 통한 원격 설정에는 [Vercel 안내](VERCEL.md)를 참고하세요. 안내 웹 페이지는 토큰이나 비밀번호를 받거나 저장하지 않습니다.

## 1. 설치 (macOS/Linux)

압축을 풀고 해당 폴더의 터미널에서:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install '.[test]'
cp accounts.example.json accounts.json
chmod 600 accounts.json
.venv/bin/python -m pytest -q
```

Windows에서는 `.venv\Scripts\python.exe`를 사용하며 환경 변수 설정 방식도 달라집니다. Docker는 필요하지 않습니다.

## 2. 계정 설정

`accounts.json`에는 비밀번호 자체가 아닌 **환경 변수 이름**만 넣습니다. 첫 개인 테스트에서는 예시의 `work` 항목을 지우고 `naver` 하나만 남기세요. 회사 계정은 지금 연결할 필요가 없습니다. 회사 계정은 관리자가 알려준 실제 IMAP 호스트·TLS 포트로 수정합니다. 이 파일은 로컬 운영자만 편집하고 AI 도구에는 쓰기 권한을 주지 마세요.

- 네이버: `imap.naver.com`, 포트 `993`, SSL/TLS. 네이버 메일 설정에서 IMAP 사용을 켜고, 2단계 인증과 애플리케이션 비밀번호를 준비합니다. 현재 설정 경로와 요구사항은 [네이버 공식 안내](https://help.naver.com/service/30029/contents/21344)를 확인하세요.
- 회사 메일: IMAP 지원 여부, 서버, 인증 방식, 외부 접속, 관리자 정책을 먼저 확인합니다. 이 버전은 TLS 위 LOGIN/앱 비밀번호 방식만 지원합니다. OAuth 전용 Microsoft 365·Google Workspace 계정, 사내망/VPN 전용 서버, STARTTLS 전용 서버는 그대로 사용할 수 없습니다. 회사 보안 정책을 우회하지 마세요.
- 앱 비밀번호는 그 자체로 읽기 전용 권한이 아닙니다. 이 프로그램이 읽기 명령만 실행하는 것이며, 유출된 동일 비밀번호로 다른 프로그램이 더 넓은 작업을 할 수 있습니다.
- 실제 비밀번호/앱 비밀번호는 채팅, 소스, MCP 설정 JSON, Git 또는 스크린샷에 넣지 마세요. 전용 앱 비밀번호를 사용하고 해지할 때 공급자에서 폐기하세요.

로컬 터미널의 현재 세션에서만 입력하는 예시 (bash, 셸 기록에 비밀이 남지 않는 입력):

```bash
read -r -p 'Naver login ID/email: ' NAVER_IMAP_USER
read -r -s -p 'Naver app password: ' NAVER_IMAP_APP_PASSWORD; printf '\n'
export NAVER_IMAP_USER NAVER_IMAP_APP_PASSWORD
export IMAP_ACCOUNTS_FILE="$PWD/accounts.json"
```

회사 계정은 `WORK_IMAP_USER`, `WORK_IMAP_APP_PASSWORD`를 같은 방식으로 설정하세요. 환경 변수도 같은 사용자 권한의 프로세스/진단 도구에 노출될 수 있으므로 공유 컴퓨터에서는 사용하지 마세요. 영구 운영에서는 OS 키체인 또는 전용 비밀 저장소 통합이 필요합니다. `.env` 파일을 자동으로 읽지 않습니다.

## 3. 로컬 MCP 호스트 연결

호스트의 로컬 stdio 서버 설정에 다음 개념의 항목을 등록합니다. **호스트마다 설정 형식과 환경 변수 상속 방식이 다르므로 해당 제품 문서를 따르세요.** 아래는 일반적인 예시이며 dot 앱 설정을 의미하지 않습니다.

```json
{
  "mcpServers": {
    "readonly-imap": {
      "command": "/absolute/path/imap-connector/.venv/bin/python",
      "args": ["-m", "imap_connector.server"],
      "env": {
        "IMAP_ACCOUNTS_FILE": "/absolute/path/imap-connector/accounts.json"
      }
    }
  }
}
```

`pip install`로 설치되므로 실행 작업 디렉터리는 자유입니다. 실제 절대 경로로 바꾸고, 호스트가 사용자·비밀번호 환경 변수를 안전하게 자식 서버에 전달하는지 확인하세요. 터미널에서 설정한 변수는 이미 실행된 GUI 앱에 자동 전달되지 않습니다. 비밀번호를 JSON에 하드코딩하는 대신 호스트가 지원하는 보안 입력/비밀 저장 기능을 이용하세요.

MCP 호스트 없이 `.venv/bin/python -m imap_connector.server`를 실행하면 stdin의 프로토콜 메시지를 기다립니다. 일반 대화형 명령줄 UI가 아닙니다.

## 사용 예시

1. `list_accounts`: 설정된 계정 ID와 환경 변수 준비 여부만 확인 (메일 접속 없음).
2. `list_folders(account="naver")`: 메일함 이름 확인. 한글 메일함 지원.
3. `search_messages(account="naver", mailbox="INBOX", text="계약", limit=10, scan_limit=200)`: 최근 최대 200개 안에서 검색. `sender`와 ISO 날짜 `since="2026-10-01"`도 지원.
4. `read_message(message_id="검색에서 받은 id")`: 검색 결과의 ID를 그대로 전달.

검색은 **전체 과거 메일 검색이 아닙니다.** 기본 최근 200개, 최대 500개 메시지만 검색하며 결과는 최대 50개입니다. 날짜순 대신 UID 내림차순입니다. 오래된 일치 항목은 검색되지 않을 수 있습니다. 서버가 UTF-8 검색을 지원하지 않으면 한글 검색이 실패할 수 있으며, 자동으로 전체 메일을 내려받아 우회하지 않습니다.

## 안전장치와 제한

- TLS 인증서·호스트 이름 검증 필수. 인증서 검증 끄기 옵션 없음.
- `EXAMINE`(readonly select), `BODY.PEEK`만 사용. 읽음 표시, 이동, 삭제, 발송, 첨부 저장 도구 없음. 계정/서버 접속 기록은 공급자에 남을 수 있습니다.
- 계정은 로컬 설정의 허용 목록만 사용. 도구 호출로 새 호스트나 자격증명을 지정할 수 없음.
- 원시 IMAP 쿼리 없음. 문자열 인용/이스케이프 및 제어 문자 차단.
- 메시지 ID는 계정+메일함+UIDVALIDITY+UID. UIDVALIDITY가 바뀌면 재검색 요구. ID는 암호화/인증 토큰이 아니며 로컬 호스트는 설정된 모든 계정을 읽을 수 있음.
- 헤더 최대 16 KiB, 본문 파트 최대 256 KiB, 출력 최대 30,000자. 첨부 및 첨부된 메시지는 내려받지 않음. MIME에서 일반 텍스트를 우선하여 **본문 한 파트만** 읽음. HTML만 있으면 텍스트로 변환하며 외부 이미지/링크를 열지 않음.
- 메시지 전체 크기 대신 실제 선택한 텍스트 파트 크기를 제한하므로 큰 첨부가 있는 작은 본문은 읽을 수 있음. 큰 텍스트 본문은 다운로드하지 않고 제한 안내 반환.
- 소켓 대기 15초, 세션 읽기 제한 약 60초, 응답 누적 4 MiB 제한. 악성 서버에 대한 완전한 프로세스 격리/총 실행 시간 보장은 아님.
- 본문, 제목, 발신자, 폴더명은 **신뢰할 수 없는 외부 데이터**. 그 안의 지시, 링크, 비밀번호 요청은 사용자 승인으로 취급하면 안 됨.
- 메일 내용 캐시/파일 저장 없음. MCP 호스트 자체의 대화 기록·로그 정책은 별도로 확인.
- MIME 특이 형식, 서버별 문자셋/검색 호환성, 실제 네이버/회사 서버 호환성은 미검증. 생산 운영·보안 감사를 마친 제품이 아님.

## 검증

현재 실행한 97개 단위 테스트는 통과했습니다. 전체 115개 suite는 Windows 환경에서 MCP HTTP/stdio 시작이 멈춰 완료되지 않았습니다. 자세한 실행 및 미검증 상태는 TEST_REPORT.md를 확인하세요.

## 다음 연결 단계

- 로컬 stdio에서 본인 네이버 계정을 연결하려면 `NAVER_SETUP.md`의 절차를 따르세요.
- 데스크톱 Codex 원격 연결 절차는 [Vercel 안내](VERCEL.md)를 참고하세요. 실제 앱 비밀번호와 공유 토큰은 사용자가 Vercel/Codex 설정에 직접 넣어야 하며, 이 코드/안내 화면은 값을 저장하지 않습니다.
- ChatGPT 웹/cloud dot 연결은 OAuth consent route, Supabase OAuth client, owner UUID 및 audience Hook이 모두 검증되어야 합니다. mail 설정 또는 인증 경계가 하나라도 빠지면 원격 메일 도구는 열리지 않습니다.

## 참고

- [공식 MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Python imaplib 문서](https://docs.python.org/3/library/imaplib.html)
- [네이버 IMAP 설정](https://help.naver.com/service/30029/contents/21344)
- [네이버 IMAP 문제 해결](https://help.naver.com/service/30029/contents/21351)

## 공유 및 라이선스

이 저장소에는 개인 메일·실제 자격증명을 포함하지 않습니다. 로컬 테스트 및 문서 검토가 끝난 뒤 소유자가 공개 범위를 결정합니다. 현재 별도의 오픈소스 라이선스는 선택되지 않았으며 `LICENSE` 파일도 없습니다. 저장소 공개가 임의의 사용·수정·재배포 권한 부여를 의미하지 않습니다. 재사용/배포 허용 범위는 소유자가 라이선스를 선택한 뒤 확인하세요.

보안 사용 지침은 [SECURITY.md](SECURITY.md)를 참조하세요. GitHub Actions는 가짜 IMAP과 로컬 stdio 테스트만 실행하며 실제 메일 계정 비밀은 요구하지 않습니다. 워크플로는 배포를 수행하지 않습니다.
