"""Small, same-origin Supabase login and OAuth consent page."""

from __future__ import annotations

import html
import json
import secrets

from starlette.responses import HTMLResponse

from .oauth_auth import OAuthSettings


# Pinned to the latest stable Supabase JS v2 release reviewed on 2026-10-26.
# The browser SDK handles Supabase login, authorization_id, PKCE, and consent;
# this app does not implement an authorization-code server.
SUPABASE_JS_MODULE = "https://esm.sh/@supabase/supabase-js@2.118.0?bundle"

NOT_CONFIGURED_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer"><title>메일 접근 설정 필요</title></head>
<body><main><h1>메일 접근 설정이 아직 준비되지 않았습니다</h1>
<p>현재 이 서비스는 진단 전용입니다. OAuth 로그인과 동의 화면, 메일 도구는 Supabase 프로젝트와 소유자 설정이 완료되기 전까지 사용할 수 없습니다.</p>
<p>이 화면은 메일 주소, 네이버 앱 비밀번호, OAuth 토큰을 입력받거나 저장하지 않습니다.</p></main></body></html>"""


_PAGE = r"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>메일 읽기 접근 승인</title>
  <style>
    :root { color-scheme: light; font: 16px/1.55 system-ui, sans-serif; color: #182230; background: #f4f6fa; }
    body { margin: 0; }
    main { max-width: 620px; margin: 8vh auto; padding: 0 20px 48px; }
    section { padding: 24px; border: 1px solid #d0d5dd; border-radius: 14px; background: #fff; }
    h1 { margin-top: 0; font-size: 1.6rem; }
    label { display: block; margin: 14px 0 4px; font-weight: 650; }
    input { width: 100%; box-sizing: border-box; padding: 10px; border: 1px solid #98a2b3; border-radius: 7px; font: inherit; }
    button { margin: 12px 8px 0 0; padding: 10px 16px; border: 0; border-radius: 7px; background: #175cd3; color: white; font: inherit; font-weight: 650; cursor: pointer; }
    button.secondary { background: #475467; }
    button.deny { background: #b42318; }
    button:disabled { opacity: .6; cursor: wait; }
    .notice { padding: 12px; border-radius: 8px; background: #f2f4f7; }
    .warning { background: #fff7e6; }
    [hidden] { display: none !important; }
    code, li { overflow-wrap: anywhere; }
    #message { min-height: 1.5em; color: #b42318; }
  </style>
</head>
<body>
<main>
  <section>
    <h1>개인 메일 읽기 접근 승인</h1>
    <p class="notice warning">이 화면은 네이버 메일 비밀번호를 받지 않습니다. 로그인은 별도 Supabase 계정으로 진행됩니다. 승인된 연결에는 폴더·검색·본문 읽기만 제공됩니다.</p>
    <p id="message" role="status" aria-live="polite"></p>
    <form id="login" hidden>
      <h2>소유자 로그인</h2>
      <p>Supabase 프로젝트에 미리 등록한 소유자 계정으로 로그인하세요. 이 화면에는 계정 생성 기능이 없습니다.</p>
      <label for="email">이메일</label><input id="email" name="email" type="email" autocomplete="username" required>
      <label for="password">Supabase 비밀번호</label><input id="password" name="password" type="password" autocomplete="current-password" required>
      <button id="login-button" type="submit">로그인</button>
    </form>
    <div id="signed-in" hidden>
      <p>Supabase 소유자 계정을 확인했습니다.</p>
      <div id="request" hidden>
        <h2>요청 내용</h2>
        <p><strong>앱:</strong> <span id="client-name"></span></p>
        <p><strong>앱 ID:</strong> <code id="client-id"></code></p>
        <p><strong>복귀 주소:</strong> <code id="redirect-uri"></code></p>
        <p><strong>Supabase가 요청한 로그인 범위:</strong></p>
        <ul id="scopes"></ul>
        <p class="notice">Supabase 기본 범위는 계정 정보에 적용됩니다. 메일 읽기 권한은 이 서버가 별도로 소유자·앱 ID·대상 주소·읽기 전용 권한 claim을 확인할 때만 열립니다.</p>
        <button id="approve" type="button">승인</button>
        <button id="deny" class="deny" type="button">거부</button>
      </div>
      <button id="sign-out" class="secondary" type="button">로그아웃</button>
    </div>
  </section>
</main>
<script type="module" nonce="__NONCE__">
const config = __CONFIG__;
const message = document.querySelector('#message');
const loginForm = document.querySelector('#login');
const signedIn = document.querySelector('#signed-in');
const requestBox = document.querySelector('#request');
const authId = new URL(location.href).searchParams.get('authorization_id');
const authIdPattern = /^[A-Za-z0-9._~-]{1,256}$/;
let supabase;
let details;

function setMessage(text) { message.textContent = text; }
function toggleBusy(busy) {
  for (const button of document.querySelectorAll('button')) button.disabled = busy;
}
function destinationUrl(candidate, expected) {
  const dest = new URL(candidate);
  const original = new URL(expected);
  if (dest.protocol !== 'https:' || dest.origin !== original.origin) throw new Error('invalid_redirect');
  return dest.href;
}
function displayDetails(value) {
  const client = value?.client;
  const clientId = client?.client_id ?? client?.id ?? value?.client_id;
  if (!client || typeof client.name !== 'string' || clientId !== config.clientId) {
    throw new Error('client_mismatch');
  }
  if (typeof value.redirect_uri !== 'string' || !value.redirect_uri.startsWith('https://')) {
    throw new Error('redirect_mismatch');
  }
  document.querySelector('#client-name').textContent = client.name;
  document.querySelector('#client-id').textContent = clientId;
  document.querySelector('#redirect-uri').textContent = value.redirect_uri;
  const scopeList = document.querySelector('#scopes');
  scopeList.replaceChildren();
  const requested = typeof value.scope === 'string' ? value.scope.split(/\s+/).filter(Boolean) : [];
  for (const scope of requested) {
    const item = document.createElement('li');
    item.textContent = scope;
    scopeList.append(item);
  }
  requestBox.hidden = false;
}

try {
  const { createClient } = await import('__SUPABASE_JS_MODULE__');
  supabase = createClient(config.url, config.publishableKey, {
    auth: { flowType: 'pkce', persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
  });

  async function checkOwner(session) {
    const response = await fetch('/oauth/session-check', {
      method: 'GET',
      headers: { Authorization: `Bearer ${session.access_token}` },
      cache: 'no-store',
      credentials: 'omit',
    });
    if (!response.ok) return false;
    const body = await response.json();
    return body.owner === true;
  }

  async function loadAuthorization() {
    if (!authId || !authIdPattern.test(authId)) {
      setMessage('OAuth 승인 요청 식별자가 없거나 유효하지 않습니다. 연결 화면에서 다시 시작하세요.');
      return;
    }
    const { data, error } = await supabase.auth.oauth.getAuthorizationDetails(authId);
    if (error || !data) throw new Error('authorization_details_unavailable');
    if (!('authorization_id' in data)) {
      if (typeof data.redirect_url === 'string') location.assign(data.redirect_url);
      return;
    }
    details = data;
    displayDetails(data);
    setMessage('요청한 앱과 계정 정보 범위를 확인한 뒤 승인하거나 거부하세요.');
  }

  async function showSession() {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session) {
      loginForm.hidden = false;
      signedIn.hidden = true;
      return;
    }
    const { data: { user }, error } = await supabase.auth.getUser();
    if (error || !user) {
      await supabase.auth.signOut();
      loginForm.hidden = false;
      signedIn.hidden = true;
      return;
    }
    if (!(await checkOwner(session))) {
      loginForm.hidden = true;
      signedIn.hidden = false;
      requestBox.hidden = true;
      setMessage('이 Supabase 계정은 메일 MCP의 소유자로 등록되지 않았습니다.');
      return;
    }
    loginForm.hidden = true;
    signedIn.hidden = false;
    setMessage('');
    await loadAuthorization();
  }

  loginForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    toggleBusy(true);
    const email = document.querySelector('#email').value;
    const passwordField = document.querySelector('#password');
    const password = passwordField.value;
    try {
      const { error } = await supabase.auth.signInWithPassword({ email, password });
      passwordField.value = '';
      if (error) throw new Error('login_failed');
      await showSession();
    } catch {
      passwordField.value = '';
      setMessage('로그인에 실패했습니다. Supabase 소유자 계정 설정을 확인하세요.');
    } finally { toggleBusy(false); }
  });

  async function decide(approve) {
    if (!details || !authId) return;
    toggleBusy(true);
    try {
      const method = approve ? 'approveAuthorization' : 'denyAuthorization';
      const { data, error } = await supabase.auth.oauth[method](authId);
      if (error || typeof data?.redirect_url !== 'string') throw new Error('decision_failed');
      location.assign(destinationUrl(data.redirect_url, details.redirect_uri));
    } catch {
      setMessage('승인 처리가 되지 않았습니다. 연결을 다시 시작하고 요청 주소를 확인하세요.');
      toggleBusy(false);
    }
  }

  document.querySelector('#approve').addEventListener('click', () => decide(true));
  document.querySelector('#deny').addEventListener('click', () => decide(false));
  document.querySelector('#sign-out').addEventListener('click', async () => {
    await supabase.auth.signOut();
    requestBox.hidden = true;
    details = undefined;
    setMessage('로그아웃했습니다.');
    await showSession();
  });
  await showSession();
} catch {
  setMessage('Supabase 인증 서비스에 연결할 수 없습니다. 잠시 후 다시 시도하세요.');
}
</script>
</body>
</html>"""


def consent_page(settings: OAuthSettings | None) -> HTMLResponse:
    """Render only public Supabase configuration; never render credentials."""
    if settings is None:
        return HTMLResponse(
            NOT_CONFIGURED_HTML,
            status_code=503,
            headers=_security_headers("default-src 'none'; style-src 'unsafe-inline'"),
        )

    nonce = secrets.token_urlsafe(18)
    config = json.dumps(
        {
            "url": settings.supabase_url,
            "publishableKey": settings.publishable_key,
            "clientId": settings.client_id,
        },
        separators=(",", ":"),
    ).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    rendered = (
        _PAGE.replace("__NONCE__", html.escape(nonce, quote=True))
        .replace("__CONFIG__", config)
        .replace("__SUPABASE_JS_MODULE__", SUPABASE_JS_MODULE)
    )
    parsed = settings.supabase_url.removeprefix("https://")
    csp = (
        f"default-src 'none'; script-src 'nonce-{nonce}' https://esm.sh; "
        f"connect-src 'self' https://{parsed}; style-src 'unsafe-inline'; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    return HTMLResponse(rendered, headers=_security_headers(csp))


def _security_headers(csp: str) -> dict[str, str]:
    return {
        "Cache-Control": "no-store",
        "Content-Security-Policy": csp,
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
    }
