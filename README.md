# mail-mcp

네이버 메일 읽기 전용 MCP · 로컬 프로토타입

먼저 **본인의 네이버 메일 한 계정**으로 시험할 수 있는 읽기 전용 IMAP 도구입니다. 설정 구조는 일반 IMAP 계정도 표현할 수 있지만, 회사 메일 호환성은 향후 별도 검증 대상입니다. **최소 실행 가능 개발용 버전**입니다. 계정 목록, 폴더 목록, 최근 메일 검색, 본문 읽기를 제공합니다. Python 3.11 이상과 공식 MCP Python SDK 2.3.0을 사용합니다.

**현재 dot에 연결된 서비스는 아닙니다.** 이 저장소는 기존 로컬 **stdio MCP**를 유지하고, Vercel용 stateless Streamable HTTP와 한국어 안내 화면을 추가합니다. 인증되지 않은 원격 서버에는 진단 도구 하나만 있으며 실제 메일 도구는 노출되지 않습니다. OAuth, 소유자 전용 인증, 비밀 저장, 원격 메일 도구, 실제 dot 연결 경로는 구현되지 않았습니다. 설정 없이 공개할 수 있는 것은 민감 정보가 없는 진단 모드뿐입니다. 배포 전 보안 경계는 [Vercel 안내](VERCEL.md)를 확인하세요.

## 처음 시작하기

Vercel용 원격 서버와 계정 설정 화면은 [Vercel 배포 및 보안 안내](VERCEL.md)를 참고하세요. 현재 `/`는 계정 입력 없이 안내만 제공하고, `/mcp`는 정적 진단만 응답합니다.

본인의 네이버 메일 한 계정으로 시작하려면 [네이버 등록 및 첫 테스트 안내](NAVER_SETUP.md)를 순서대로 따라가세요. IMAP 정보 등록, 비밀 입력, 실제 읽기 시험, MCP 호스트 연결, 해제 방법까지 정리되어 있습니다.

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

`pytest`는 가짜 IMAP 연결로 읽기 전용 명령, TLS 설정, 범위/크기 제한, 한글 헤더/HTML 본문, 첨부 제외, ID 변경, 설정 검증, 오류 비밀값 제거, 시간 제한, 세션 종료 로그아웃을 검사합니다. stdio 통합 테스트는 기존 MCP 도구 목록과 오프라인 계정 목록을 확인합니다. Streamable HTTP 테스트는 lifespan, 반복·동시 요청, 메일 도구 미노출, 한국어 안내 화면을 확인합니다. **실제 계정 로그인·메일 전송·외부 배포는 테스트하지 않습니다.**

## 다음 연결 단계

회사의 메일 서비스명과 IMAP 정책 확인 → 사용자가 자신의 기기에서 비밀 입력 → 허가된 실제 계정으로 읽음 상태/첨부 제한/한글 검색 시험 → dot이 지원하는 연결 방식 확인 → 원격 접근이 필요하면 인증·인가·비밀 저장·계정 격리·접근 로그를 구현한 뒤 공개 여부 승인. 로컬 stdio를 인증 없는 원격 서버로 바꾸는 것은 배포가 아닙니다.

## 참고

- [공식 MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Python imaplib 문서](https://docs.python.org/3/library/imaplib.html)
- [네이버 IMAP 설정](https://help.naver.com/service/30029/contents/21344)
- [네이버 IMAP 문제 해결](https://help.naver.com/service/30029/contents/21351)

## 공유 및 라이선스

이 저장소에는 개인 메일·실제 자격증명을 포함하지 않습니다. 로컬 테스트 및 문서 검토가 끝난 뒤 소유자가 공개 범위를 결정합니다. 현재 별도의 오픈소스 라이선스는 선택되지 않았으며 `LICENSE` 파일도 없습니다. 저장소 공개가 임의의 사용·수정·재배포 권한 부여를 의미하지 않습니다. 재사용/배포 허용 범위는 소유자가 라이선스를 선택한 뒤 확인하세요.

보안 사용 지침은 [SECURITY.md](SECURITY.md)를 참조하세요. GitHub Actions는 가짜 IMAP과 로컬 stdio 테스트만 실행하며 실제 메일 계정 비밀은 요구하지 않습니다. 워크플로는 배포를 수행하지 않습니다.
