"""Public Streamable HTTP endpoint with diagnostics only until auth is designed."""
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from starlette.responses import HTMLResponse
from starlette.routing import Route


DIAGNOSTIC = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    openWorldHint=False,
)


SETUP_PAGE = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>메일 MCP 연결 설정</title>
  <style>
    :root { color-scheme: light; font: 16px/1.6 system-ui, sans-serif; color: #1d2939; background: #f5f7fb; }
    body { margin: 0; }
    main { max-width: 760px; margin: 48px auto; padding: 0 20px 48px; }
    h1 { margin-bottom: 8px; font-size: 2rem; line-height: 1.2; }
    h2 { margin: 0 0 10px; font-size: 1.1rem; }
    p { margin: 8px 0; }
    .card { margin-top: 18px; padding: 20px; border: 1px solid #d7deea; border-radius: 12px; background: white; }
    .status { border-color: #e6c66b; background: #fff9e8; }
    .badge { font-weight: 700; }
    li { margin: 5px 0; }
    code { padding: 2px 5px; border-radius: 4px; background: #eef2f7; }
    a { color: #175cd3; }
    .small { color: #475467; font-size: .92rem; }
  </style>
</head>
<body>
<main>
  <h1>메일 MCP 연결 설정</h1>
  <p>네이버 메일을 읽기 전용으로 연결하기 위한 안내입니다.</p>

  <section class="card status" aria-labelledby="connection-title">
    <h2 id="connection-title">연결 상태</h2>
    <p class="badge">아직 연결되지 않았습니다.</p>
    <p>현재 원격 서버는 진단 모드입니다. 메일 계정·비밀번호를 읽거나 저장하지 않고, 메일 도구도 제공하지 않습니다.</p>
    <p>소유자 전용 로그인도 아직 연결되지 않았습니다. 이 화면은 자격 증명을 받지 않습니다.</p>
  </section>

  <section class="card" aria-labelledby="requirements-title">
    <h2 id="requirements-title">네이버에서 준비할 항목</h2>
    <ol>
      <li>네이버 메일 설정에서 IMAP 사용을 켭니다. 서버는 <code>imap.naver.com</code>, 포트는 <code>993</code> TLS입니다.</li>
      <li>네이버 계정에 2단계 인증을 설정하고, 메일 앱 전용 비밀번호를 발급할 수 있는지 확인합니다.</li>
      <li>연결에 필요한 값은 네이버 이메일 주소와 앱 비밀번호입니다. 일반 네이버 로그인 비밀번호를 사용하면 안 됩니다.</li>
    </ol>
    <p class="small">비밀번호는 이 안내 페이지에 입력하지 마세요. 일반 네이버 로그인 암호가 아니라 네이버에서 발급한 메일 앱 전용 비밀번호를 사용해야 합니다.</p>
  </section>

  <section class="card" aria-labelledby="settings-title">
    <h2 id="settings-title">현재 설정 경로</h2>
    <p>이 버전에는 계정 등록 API가 없습니다. 이 페이지는 이메일이나 비밀번호를 입력받지 않으며, 저장 완료나 연결 완료를 표시하지 않습니다.</p>
    <ul>
      <li><code>NAVER_IMAP_USER</code>: 소유자의 네이버 이메일 주소.</li>
      <li><code>NAVER_IMAP_APP_PASSWORD</code>: 네이버 메일용 앱 비밀번호. 일반 로그인 암호가 아닙니다.</li>
      <li><code>IMAP_ACCOUNTS_FILE</code>: 호스트, 포트, 환경 변수 이름만 담은 설정 파일의 경로. 예시 파일은 <code>accounts.naver.example.json</code>입니다.</li>
    </ul>
    <p>실사용 설정은 소유자 전용 접근이 확인된 뒤 Vercel 프로젝트 환경 변수에서 직접 입력해야 합니다. 현재 원격 서버는 이 변수들을 읽지 않고, 설정이 추가되더라도 메일 도구를 실행하지 않습니다.</p>
    <p class="small">브라우저 저장소, 서버리스 임시 파일, 코드 저장소, 로그에 비밀번호를 보관하지 않습니다. 공개된 테스트 배포에는 계정 설정을 추가하지 마세요.</p>
  </section>

  <section class="card" aria-labelledby="management-title">
    <h2 id="management-title">연결 해제와 비밀번호 교체</h2>
    <ul>
      <li>연결 해제: Vercel 프로젝트 환경 변수에서 메일 설정을 제거하고 이전 배포 접근도 중지한 뒤, 네이버에서 해당 앱 비밀번호를 폐기합니다.</li>
      <li>교체: 네이버에서 새 앱 비밀번호를 발급해 승인된 보안 경로로 갱신하고 배포한 다음, 기존 비밀번호를 폐기합니다.</li>
      <li>메일 읽기 오류가 나면 IMAP 사용 여부, 앱 비밀번호, 네이버 계정 정책을 확인하세요. 오류 응답에는 비밀번호와 메일 본문을 포함하지 않습니다.</li>
    </ul>
  </section>
</main>
</body>
</html>
"""


async def setup_page(request):
    return HTMLResponse(
        SETUP_PAGE,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )


def create_http_app():
    """Build a fresh diagnostics-only HTTP server and its Starlette app."""
    server = MCPServer(
        "Read-only IMAP diagnostics",
        description="Unauthenticated diagnostics only; remote mail access is disabled.",
        subscriptions=False,
    )

    @server.tool(annotations=DIAGNOSTIC)
    def service_status() -> dict:
        """Report static service capabilities without reading mail settings or mail."""
        return {
            "service": "readonly-imap-mcp",
            "mode": "diagnostics-only",
            "mail_access": "disabled",
            "mail_connection": "not configured",
            "authentication": "not configured",
        }

    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        # Vercel terminates TLS and forwards the public Host header. This
        # disables the SDK's localhost-only Host allowlist for this public,
        # diagnostics-only app. Add a deployment-domain allowlist before any
        # private mail tools are ever registered here.
        host="0.0.0.0",
    )
    app.router.routes.append(Route("/", endpoint=setup_page, methods=["GET"]))
    return server, app


remote_mcp, app = create_http_app()
