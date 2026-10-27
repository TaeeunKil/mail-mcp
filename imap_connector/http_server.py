"""Public Streamable HTTP endpoint with diagnostics and optional bearer-gated mail access."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
import hashlib
import hmac
import os
import re
from urllib.parse import parse_qsl

from mcp.server import MCPServer
from mcp.server.auth.routes import create_protected_resource_routes
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from .core import Connector, ConnectorError, clean
from .oauth_auth import OAuthSettings, SupabaseJwtVerifier, has_naver_credentials, is_valid_authorization_id
from .oauth_ui import consent_page
from .server import register_mail_tools


DIAGNOSTIC = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    openWorldHint=False,
)
READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    openWorldHint=True,
)
TOKEN_MODE_ENV = "MAIL_MCP_MODE"
TOKEN_ENV = "MAIL_MCP_TOKEN"
OAUTH_MODE = "oauth"
MIN_TOKEN_CHARS = 43  # Base64 encoding of at least 32 random bytes.
MAX_TOKEN_CHARS = 128
TOKEN_PATTERN = re.compile(rf"^[A-Za-z0-9+/_-]{{{MIN_TOKEN_CHARS},{MAX_TOKEN_CHARS}}}={{0,2}}$")
NAVER_ACCOUNT = {
    "naver": {
        "host": "imap.naver.com",
        "port": 993,
        "username_env": "NAVER_IMAP_USER",
        "password_env": "NAVER_IMAP_APP_PASSWORD",
    }
}


SETUP_PAGE = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>개인용 네이버 메일 MCP 설정</title>
  <style>
    :root { color-scheme: light; font: 16px/1.6 system-ui, sans-serif; color: #1d2939; background: #f5f7fb; }
    body { margin: 0; }
    main { max-width: 780px; margin: 42px auto; padding: 0 20px 48px; }
    h1 { margin-bottom: 8px; font-size: 2rem; line-height: 1.2; }
    h2 { margin: 0 0 10px; font-size: 1.1rem; }
    p { margin: 8px 0; }
    .card { margin-top: 18px; padding: 20px; border: 1px solid #d7deea; border-radius: 12px; background: white; }
    .status { border-color: #9bbcf5; background: #f3f7ff; }
    .warning { border-color: #e6c66b; background: #fff9e8; }
    .badge { font-weight: 700; }
    li { margin: 6px 0; }
    code, pre { border-radius: 5px; background: #eef2f7; }
    code { padding: 2px 5px; overflow-wrap: anywhere; }
    pre { padding: 12px; overflow-x: auto; }
    a { color: #175cd3; }
    .small { color: #475467; font-size: .92rem; }
  </style>
</head>
<body>
<main>
  <h1>개인용 네이버 메일 MCP</h1>
  <p>Codex에서 사용할 개인용 읽기 전용 메일 연결의 설정 안내입니다.</p>

  <section class="card status" aria-labelledby="status-title">
    <h2 id="status-title">현재 동작 범위</h2>
    <p class="badge">이 화면은 자격 증명을 받거나 저장하지 않습니다.</p>
    <p>메일 도구를 켜려면 소유자가 Vercel Production 환경변수와 자신의 Codex 사용자 환경에 직접 설정해야 합니다. 환경변수를 바꾼 뒤에는 Vercel 재배포와 Codex 재시작이 필요합니다.</p>
    <p>Bearer 방식은 데스크톱 Codex용입니다. ChatGPT 웹/cloud dot용 Supabase OAuth 로그인/동의 화면과 JWT 검증 코드는 준비됐지만, OAuth client ID, 소유자 UUID, 필요한 Vercel 환경변수가 올바르게 적용되기 전까지 실제 메일 도구를 열지 않습니다.</p>
    <p>이전 진단 배포에서는 인증 없는 <code>service_status</code>만 제공됩니다. 개인용 Bearer 설정이 적용된 배포에서는 인증 전 MCP 도구 목록과 계정 정보가 차단됩니다.</p>
  </section>

  <section class="card" aria-labelledby="prepare-title">
    <h2 id="prepare-title">네이버에서 준비할 항목</h2>
    <ol>
      <li>네이버 메일 설정에서 IMAP 사용을 켭니다. 원격 서버는 <code>imap.naver.com:993</code> TLS로 고정되어 있습니다.</li>
      <li>네이버 계정의 2단계 인증과 앱 비밀번호 발급 가능 여부를 확인합니다.</li>
      <li>메일 주소와 네이버가 발급한 앱 비밀번호를 준비합니다. 일반 로그인 비밀번호는 사용하지 않습니다.</li>
    </ol>
    <p class="small">이 안내 페이지에는 메일 주소나 비밀번호 입력란이 없습니다. 실제 연결 전까지 계정에 접속하거나 연결 성공을 표시하지 않습니다.</p>
  </section>

  <section class="card" aria-labelledby="token-title">
    <h2 id="token-title">개인 액세스 토큰 준비</h2>
    <p>메일 서버 접근을 보호하는 임의의 공유 Bearer 토큰을 본인 컴퓨터에서 생성합니다. 이 토큰은 네이버 앱 비밀번호와 별개이며, 가진 사람은 메일 읽기 도구를 호출할 수 있으므로 이 대화나 코드 저장소에 붙여 넣지 마세요.</p>
    <pre><code>$bytes = New-Object byte[] 32
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try {
  $rng.GetBytes($bytes)
  [Convert]::ToBase64String($bytes)
} finally {
  $rng.Dispose()
}</code></pre>
    <p>Vercel의 <code>MAIL_MCP_TOKEN</code>과 Codex 사용자 환경변수 <code>MAIL_MCP_TOKEN</code>에 같은 값을 직접 설정합니다. 서버는 <code>MAIL_MCP_MODE=token</code>일 때만 도구를 열고 <code>Authorization: Bearer …</code> 헤더를 검사합니다. 표준 Base64의 <code>+</code>, <code>/</code>, 끝의 <code>=</code>는 허용하며 URL query token은 받지 않습니다.</p>
  </section>

  <section class="card" aria-labelledby="vercel-title">
    <h2 id="vercel-title">Vercel Production 환경변수</h2>
    <p>Vercel 프로젝트의 Settings → Environment Variables에서 아래 값을 Production 범위에 설정하고 새 배포를 만드세요.</p>
    <ul>
      <li><code>MAIL_MCP_MODE</code>: <code>token</code></li>
      <li><code>MAIL_MCP_TOKEN</code>: 본인이 생성한 임의 토큰</li>
      <li><code>NAVER_IMAP_USER</code>: 네이버 메일 주소</li>
      <li><code>NAVER_IMAP_APP_PASSWORD</code>: 네이버 앱 비밀번호</li>
    </ul>
    <p>누락되거나 형식이 잘못된 토큰/메일 설정은 도구 실행 전에 차단됩니다. 서버는 설정 파일이나 임시 파일에 비밀번호를 쓰지 않습니다. 실제 IMAP 로그인을 별도로 확인하기 전에는 연결 상태가 검증된 것으로 볼 수 없습니다.</p>
  </section>

  <section class="card" aria-labelledby="codex-title">
    <h2 id="codex-title">Codex 사용자 MCP 설정</h2>
    <ol>
      <li>사용자 설정에서 맞춤형 MCP 연결을 추가하고 전송 방식으로 Streamable HTTP를 선택합니다.</li>
      <li>URL을 <code>https://mail-mcp-beta.vercel.app/mcp</code>로 설정합니다.</li>
      <li>인증 토큰 환경변수 이름을 <code>MAIL_MCP_TOKEN</code>으로 지정하고, 본인 Codex의 사용자 환경변수에 Vercel과 같은 토큰 값을 설정합니다.</li>
      <li>Codex를 재시작하고 인증이 적용된 뒤 읽기 전용 도구 목록을 확인합니다.</li>
    </ol>
    <p class="small">Codex 설정 화면은 서버 환경변수를 대신 저장하지 않습니다. 토큰을 URL, 임의 헤더 값, 프롬프트, 저장소 파일에 직접 쓰지 말고 환경변수 연결을 사용하세요.</p>
  </section>

  <section class="card warning" aria-labelledby="limits-title">
    <h2 id="limits-title">연결 해제와 제한</h2>
    <ul>
      <li>접근을 막으려면 Vercel에서 <code>MAIL_MCP_MODE</code>를 진단 모드로 바꾸거나 <code>MAIL_MCP_TOKEN</code>을 제거하고 재배포하세요. 토큰 교체는 Vercel과 Codex 양쪽 값을 갱신한 뒤 재배포/재시작합니다.</li>
      <li>네이버 앱 비밀번호를 폐기하려면 네이버 보안 설정에서 직접 폐기하세요. 이 서버는 비밀번호를 취소하거나 변경할 수 없습니다.</li>
      <li>공유 Bearer 토큰은 사용자별 신원을 구분하지 않습니다. 토큰을 공유하면 그 사용자도 같은 읽기 권한을 갖습니다.</li>
      <li>제공 도구는 폴더 목록, 최근 메일 검색, 메일 본문 읽기뿐입니다. 발송, 삭제, 이동, 읽음 변경 기능은 없습니다.</li>
    </ul>
  </section>
</main>
</body>
</html>
"""


def setup_page(request):
    return HTMLResponse(
        SETUP_PAGE,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )


def _safe_token_digest(environ: Mapping[str, str]) -> bytes | None:
    token = environ.get(TOKEN_ENV)
    if not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token):
        return None
    encoded = token.rstrip("=")
    if len(token) > MAX_TOKEN_CHARS or len(token) - len(encoded) > 2 or len(encoded) % 4 == 1:
        return None
    try:
        decoded = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        return None
    if len(decoded) < 32:
        return None
    standard = base64.b64encode(decoded).decode("ascii")
    urlsafe = base64.urlsafe_b64encode(decoded).decode("ascii")
    if token not in {standard, standard.rstrip("="), urlsafe, urlsafe.rstrip("=")}:
        return None
    return hashlib.sha256(token.encode("ascii")).digest()


def _valid_naver_settings(environ: Mapping[str, str]) -> bool:
    user = environ.get("NAVER_IMAP_USER")
    password = environ.get("NAVER_IMAP_APP_PASSWORD")
    try:
        clean(user, 320)
        clean(password, 2048)
    except ConnectorError:
        return False
    return True


class BearerGate:
    """Require one shared bearer token before any private MCP request is parsed."""

    def __init__(self, app: ASGIApp, *, token_digest: bytes | None, ready: bool):
        self.app = app
        self.token_digest = token_digest
        self.ready = ready

    async def _respond(self, scope: Scope, receive: Receive, send: Send, status_code: int, code: str, challenge: bool = False) -> None:
        headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        if challenge:
            headers["WWW-Authenticate"] = 'Bearer realm="mail-mcp"'
        response = JSONResponse({"error": code}, status_code=status_code, headers=headers)
        await response(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path != "/mcp" and not path.startswith("/mcp/"):
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        query = parse_qsl(request.url.query, keep_blank_values=True)
        if any(key.lower() in {"token", "access_token"} for key, _ in query):
            await self._respond(scope, receive, send, 400, "url_tokens_not_supported")
            return
        if not self.ready or self.token_digest is None:
            await self._respond(scope, receive, send, 503, "private_service_unavailable")
            return

        authorization = request.headers.getlist("authorization")
        if len(authorization) != 1 or len(authorization[0]) > 2048:
            await self._respond(scope, receive, send, 401, "unauthorized", challenge=True)
            return
        value = authorization[0]
        if len(value) < 8 or value[:7].lower() != "bearer ":
            await self._respond(scope, receive, send, 401, "unauthorized", challenge=True)
            return
        candidate = value[7:]
        if not candidate or candidate != candidate.strip() or not candidate.isascii():
            await self._respond(scope, receive, send, 401, "unauthorized", challenge=True)
            return
        candidate_digest = hashlib.sha256(candidate.encode("ascii")).digest()
        if not hmac.compare_digest(candidate_digest, self.token_digest):
            await self._respond(scope, receive, send, 401, "unauthorized", challenge=True)
            return
        await self.app(scope, receive, send)


class OAuthGate:
    """Require a verified owner/client/resource token for every private MCP call."""

    def __init__(self, app: ASGIApp, *, verifier: SupabaseJwtVerifier, auth_ready: bool, metadata_url: str):
        self.app = app
        self.verifier = verifier
        self.auth_ready = auth_ready
        self.metadata_url = metadata_url

    async def _respond(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        status_code: int,
        code: str,
        challenge: bool = False,
    ) -> None:
        headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        if challenge:
            headers["WWW-Authenticate"] = (
                f'Bearer resource_metadata="{self.metadata_url}", error="invalid_token"'
            )
        response = JSONResponse({"error": code}, status_code=status_code, headers=headers)
        await response(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path != "/mcp" and not path.startswith("/mcp/"):
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        if any(key.lower() in {"token", "access_token"} for key, _ in parse_qsl(request.url.query, keep_blank_values=True)):
            await self._respond(scope, receive, send, 400, "url_tokens_not_supported")
            return
        if not self.auth_ready:
            await self._respond(scope, receive, send, 503, "private_service_unavailable")
            return
        authorization = request.headers.getlist("authorization")
        if len(authorization) != 1 or len(authorization[0]) > 20_000:
            await self._respond(scope, receive, send, 401, "unauthorized", challenge=True)
            return
        value = authorization[0]
        if len(value) < 8 or value[:7].lower() != "bearer ":
            await self._respond(scope, receive, send, 401, "unauthorized", challenge=True)
            return
        token = value[7:]
        if not token or token != token.strip() or not token.isascii():
            await self._respond(scope, receive, send, 401, "unauthorized", challenge=True)
            return
        if await self.verifier.verify_mcp_token_async(token) is None:
            await self._respond(scope, receive, send, 401, "unauthorized", challenge=True)
            return
        await self.app(scope, receive, send)


def _authorization_bearer(request: Request) -> str | None:
    values = request.headers.getlist("authorization")
    if len(values) != 1 or len(values[0]) > 20_000:
        return None
    value = values[0]
    if len(value) < 8 or value[:7].lower() != "bearer ":
        return None
    token = value[7:]
    if not token or token != token.strip() or not token.isascii():
        return None
    return token


def _new_server(name: str, description: str) -> MCPServer:
    return MCPServer(name, description=description, subscriptions=False, log_level="WARNING")


def _register_status(server: MCPServer, status: dict[str, str]) -> None:
    @server.tool(annotations=DIAGNOSTIC)
    def service_status() -> dict:
        """Report static service capability state without opening a mailbox."""
        return dict(status)


def create_http_app(environ: Mapping[str, str] | None = None):
    """Build diagnostics, desktop-token, or explicitly opted-in OAuth app."""
    env = os.environ if environ is None else environ
    mode = env.get(TOKEN_MODE_ENV, "diagnostic")
    token_digest: bytes | None = None
    ready = False
    auth_ready = False
    oauth_settings = OAuthSettings.from_environ(env) if mode == OAUTH_MODE else None
    oauth_verifier = SupabaseJwtVerifier(oauth_settings) if oauth_settings is not None else None
    credential_env: dict[str, str | None] = {}

    if mode == "diagnostic":
        server = _new_server(
            "Read-only IMAP diagnostics",
            "Unauthenticated diagnostics only; remote mail access is disabled.",
        )
        _register_status(server, {
            "service": "readonly-imap-mcp",
            "mode": "diagnostics-only",
            "mail_access": "disabled",
            "mail_connection": "not configured",
            "authentication": "not configured",
        })
    elif mode == "token":
        token_digest = _safe_token_digest(env)
        if token_digest is not None:
            credential_env = {
                "NAVER_IMAP_USER": env.get("NAVER_IMAP_USER"),
                "NAVER_IMAP_APP_PASSWORD": env.get("NAVER_IMAP_APP_PASSWORD"),
            }
            ready = _valid_naver_settings(credential_env)
        if ready:
            server = _new_server(
                "Private read-only Naver mail",
                "Personal, bearer-protected read-only access to one fixed Naver IMAP account.",
            )
            _register_status(server, {
                "service": "readonly-imap-mcp",
                "mode": "private-read-only",
                "mail_access": "enabled",
                "mail_connection": "configured; not tested",
                "authentication": "bearer token",
            })
            connector = lambda: Connector(NAVER_ACCOUNT, environ=credential_env)
            register_mail_tools(server, connector)
        else:
            # Keep an empty MCP server behind a 503 gate. Do not fall back to
            # the public diagnostic or expose partial readiness information.
            server = _new_server(
                "Private read-only Naver mail",
                "Private service unavailable until required settings are valid.",
            )
    elif mode == OAUTH_MODE:
        credential_env = {
            "NAVER_IMAP_USER": env.get("NAVER_IMAP_USER"),
            "NAVER_IMAP_APP_PASSWORD": env.get("NAVER_IMAP_APP_PASSWORD"),
        }
        auth_ready = oauth_settings is not None
        ready = auth_ready and has_naver_credentials(credential_env)
        if ready:
            server = _new_server(
                "Private read-only Naver mail",
                "Owner-only read-only access protected by Supabase OAuth bearer tokens.",
            )
            _register_status(server, {
                "service": "readonly-imap-mcp",
                "mode": "private-read-only",
                "mail_access": "enabled",
                "mail_connection": "configured; not tested",
                "authentication": "Supabase OAuth; owner/client/audience/scope checked",
            })
            connector = lambda: Connector(NAVER_ACCOUNT, environ=credential_env)
            register_mail_tools(server, connector)
        elif auth_ready:
            # OAuth can be exercised without opening a mailbox. Keep the
            # authenticated surface to this static status tool until both
            # Naver credentials are configured.
            server = _new_server(
                "Private read-only Naver mail",
                "OAuth owner access is ready; mail access is disabled until account settings are configured.",
            )
            _register_status(server, {
                "service": "readonly-imap-mcp",
                "mode": "oauth-ready-mail-disabled",
                "mail_access": "disabled",
                "mail_connection": "not configured",
                "authentication": "Supabase OAuth; owner/client/audience/scope checked",
            })
        else:
            server = _new_server(
                "Private read-only Naver mail",
                "Private service unavailable until required OAuth and mail settings are valid.",
            )
    else:
        # Unknown mode strings are a configuration error, never a reason to
        # expose mail tools or switch silently to a public mode.
        server = _new_server(
            "Private read-only Naver mail",
            "Private service unavailable until required settings are valid.",
        )

    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        host="0.0.0.0",
    )
    app.router.routes.append(Route("/", endpoint=setup_page, methods=["GET"]))
    async def oauth_consent(request: Request):
        if mode != OAUTH_MODE:
            return consent_page(None)
        authorization_id = request.query_params.get("authorization_id")
        if authorization_id is not None and not is_valid_authorization_id(authorization_id):
            return JSONResponse(
                {"error": "invalid_authorization_request"},
                status_code=400,
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            )
        return consent_page(oauth_settings)

    async def oauth_session_check(request: Request):
        headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        if mode != OAUTH_MODE or oauth_settings is None or oauth_verifier is None:
            return JSONResponse({"error": "oauth_not_configured"}, status_code=503, headers=headers)
        token = _authorization_bearer(request)
        if token is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401, headers=headers)
        outcome = await oauth_verifier.verify_owner_session_detailed(token)
        if outcome.owner is None:
            return JSONResponse(
                {"error": "invalid_session", "reason": outcome.reason},
                status_code=401,
                headers=headers,
            )
        if not outcome.owner:
            return JSONResponse({"owner": False}, status_code=403, headers=headers)
        return JSONResponse({"owner": True}, headers=headers)

    app.router.routes.extend([
        Route("/oauth/consent", endpoint=oauth_consent, methods=["GET"]),
        Route("/oauth/session-check", endpoint=oauth_session_check, methods=["GET"]),
    ])
    if mode == OAUTH_MODE and oauth_settings is not None:
        metadata_routes = create_protected_resource_routes(
            AnyHttpUrl(oauth_settings.resource_url),
            [AnyHttpUrl(oauth_settings.issuer)],
            scopes_supported=None,
            resource_name="Personal read-only Naver mail MCP",
            resource_documentation=AnyHttpUrl(oauth_settings.resource_url.rsplit("/mcp", 1)[0] + "/"),
        )
        app.router.routes.extend(metadata_routes)
    app.state.mail_mcp_mode = mode
    app.state.mail_mcp_ready = ready if mode in {"token", OAUTH_MODE} else mode == "diagnostic"
    app.state.mail_mcp_auth_ready = auth_ready if mode == OAUTH_MODE else mode == "diagnostic"
    app.state.mail_mcp_server = server
    if mode == "token":
        app = BearerGate(app, token_digest=token_digest, ready=ready)
    elif mode == OAUTH_MODE:
        metadata_url = ""
        if oauth_settings is not None:
            metadata_url = (
                oauth_settings.resource_url.rsplit("/mcp", 1)[0]
                + "/.well-known/oauth-protected-resource/mcp"
            )
        if oauth_verifier is not None:
            app = OAuthGate(
                app,
                verifier=oauth_verifier,
                auth_ready=auth_ready,
                metadata_url=metadata_url,
            )
        else:
            app = BearerGate(app, token_digest=None, ready=False)
    else:
        # Diagnostic mode never reads account variables and has only a static
        # service_status tool. It still rejects URL-carried tokens explicitly.
        app = DiagnosticQueryGuard(app)
    return server, app


class DiagnosticQueryGuard:
    """Reject URL tokens even on the intentionally public diagnostics endpoint."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and (scope.get("path") == "/mcp" or scope.get("path", "").startswith("/mcp/")):
            request = Request(scope, receive=receive)
            if any(key.lower() in {"token", "access_token"} for key, _ in parse_qsl(request.url.query, keep_blank_values=True)):
                response = JSONResponse(
                    {"error": "url_tokens_not_supported"},
                    status_code=400,
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


remote_mcp, app = create_http_app()
