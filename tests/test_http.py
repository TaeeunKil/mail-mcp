"""Stateless HTTP protocol tests; no mail account or credentials are used."""
from concurrent.futures import ThreadPoolExecutor
import json

import pytest
from starlette.testclient import TestClient

from imap_connector import server as stdio_server
from imap_connector.http_server import create_http_app

remote_mcp, app = create_http_app(environ={})


def rpc(client, method, params=None, request_id=1, headers=None):
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    return client.post(
        "/mcp",
        headers=request_headers,
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        },
    )


@pytest.fixture(scope="module")
def client():
    # MCP 2.3.0 starts the session manager in this app lifespan. Its run()
    # context is intentionally entered exactly once for this server instance.
    with TestClient(app) as test_client:
        initialized = rpc(
            test_client,
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "offline-test", "version": "1"},
            },
        )
        assert initialized.status_code == 200
        assert initialized.json()["result"]["serverInfo"]["name"] == "Read-only IMAP diagnostics"
        assert "Mcp-Session-Id" not in initialized.headers
        ready = test_client.post(
            "/mcp",
            headers={"Accept": "application/json"},
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        assert ready.status_code == 202
        yield test_client


def test_http_app_uses_sdk_lifespan(client):
    assert remote_mcp.session_manager._has_started is True
    response = rpc(client, "ping", request_id=2)
    assert response.status_code == 200
    assert response.json()["result"] == {}


def test_remote_server_exposes_only_static_diagnostics(client):
    response = rpc(client, "tools/list", request_id=3)
    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()["result"]["tools"]}
    assert names == {"service_status"}

    result = rpc(
        client,
        "tools/call",
        {"name": "service_status", "arguments": {}},
        request_id=4,
    ).json()["result"]
    assert result["isError"] is not True
    assert json.loads(result["content"][0]["text"]) == {
        "service": "readonly-imap-mcp",
        "mode": "diagnostics-only",
        "mail_access": "disabled",
        "mail_connection": "not configured",
        "authentication": "not configured",
    }


def test_diagnostic_server_does_not_register_mail_tools_or_read_settings(client, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("diagnostics must never instantiate the mail connector")

    monkeypatch.setattr(stdio_server, "connector", forbidden)
    response = rpc(
        client,
        "tools/call",
        {"name": "search_messages", "arguments": {"account": "naver"}},
        request_id=5,
    )
    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True


def test_repeated_and_concurrent_stateless_requests(client):
    def call(request_id):
        response = rpc(
            client,
            "tools/call",
            {"name": "service_status", "arguments": {}},
            request_id=request_id,
        )
        assert response.status_code == 200
        return json.loads(response.json()["result"]["content"][0]["text"])

    for request_id in range(10, 14):
        assert call(request_id)["mode"] == "diagnostics-only"

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(call, range(20, 36)))
    assert len(results) == 16
    assert all(result["mail_access"] == "disabled" for result in results)


def test_invalid_protocol_method_returns_bounded_error(client):
    response = rpc(client, "mail/send", {"body": "must not be echoed"}, request_id=50)
    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32601
    assert "must not be echoed" not in response.text


def test_get_is_not_a_mail_or_diagnostic_operation(client):
    response = client.get("/mcp", headers={"Accept": "application/json"})
    assert response.status_code in (405, 406)


def test_setup_page_does_not_collect_or_store_credentials(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert 'lang="ko"' in response.text
    assert "NAVER_IMAP_USER" in response.text
    assert "NAVER_IMAP_APP_PASSWORD" in response.text
    assert "MAIL_MCP_TOKEN" in response.text
    assert "RandomNumberGenerator]::Create()" in response.text
    assert "ToBase64String($bytes)" in response.text
    assert "표준 Base64" in response.text
    assert "Authorization: Bearer" in response.text
    assert "<form" not in response.text.lower()
    assert "<input" not in response.text.lower()
    assert "localstorage" not in response.text.lower()


def test_diagnostic_mode_rejects_url_tokens(client):
    response = client.get("/mcp?access_token=never-use-url-tokens")
    assert response.status_code == 400
    assert response.json() == {"error": "url_tokens_not_supported"}
    assert "never-use-url-tokens" not in response.text


def test_oauth_authentication_is_available_without_mail_credentials(monkeypatch):
    from types import SimpleNamespace

    from imap_connector.http_server import OAuthGate

    base_env = {
        "SUPABASE_URL": "https://abcdefghijklmnopqrst.supabase.co",
        "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_test_only_do_not_use_1234567890",
        "MAIL_MCP_OAUTH_CLIENT_ID": "synthetic-oauth-client-id",
        "MAIL_MCP_OWNER_SUB": "12345678-1234-5678-1234-567812345678",
        "MAIL_MCP_RESOURCE_URL": "https://mail-mcp-beta.vercel.app/mcp",
    }
    server, oauth_app = create_http_app({"MAIL_MCP_MODE": "oauth", **base_env})
    assert isinstance(oauth_app, OAuthGate)

    async def accept_synthetic_token(token):
        return SimpleNamespace(subject="synthetic-owner") if token == "synthetic-token" else None

    monkeypatch.setattr(oauth_app.verifier, "verify_mcp_token_async", accept_synthetic_token)

    with TestClient(oauth_app) as oauth_client:
        unauthenticated = rpc(
            oauth_client,
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "offline-test", "version": "1"},
            },
        )
        assert unauthenticated.status_code == 401
        assert unauthenticated.headers["www-authenticate"] == (
            'Bearer resource_metadata="https://mail-mcp-beta.vercel.app/'
            '.well-known/oauth-protected-resource/mcp", error="invalid_token"'
        )

        auth = {"Authorization": "Bearer synthetic-token"}
        initialized = rpc(
            oauth_client,
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "offline-test", "version": "1"},
            },
            request_id=2,
            headers=auth,
        )
        assert initialized.status_code == 200
        ready = oauth_client.post(
            "/mcp",
            headers={"Accept": "application/json", **auth},
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        assert ready.status_code == 202

        listed = rpc(oauth_client, "tools/list", request_id=3, headers=auth)
        assert listed.status_code == 200
        assert {tool["name"] for tool in listed.json()["result"]["tools"]} == {"service_status"}

        status = rpc(
            oauth_client,
            "tools/call",
            {"name": "service_status", "arguments": {}},
            request_id=4,
            headers=auth,
        )
        assert status.status_code == 200
        result = json.loads(status.json()["result"]["content"][0]["text"])
        assert result["mail_access"] == "disabled"
        assert result["mail_connection"] == "not configured"
        assert "NAVER_IMAP" not in json.dumps(result)

    assert set(server._tool_manager._tools) == {"service_status"}


def test_owner_session_check_is_separate_from_mcp_resource_auth(monkeypatch):
    from types import SimpleNamespace

    from imap_connector.http_server import OAuthGate
    from imap_connector.oauth_auth import OwnerSessionVerification

    base_env = {
        "SUPABASE_URL": "https://abcdefghijklmnopqrst.supabase.co",
        "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_test_only_do_not_use_1234567890",
        "MAIL_MCP_OAUTH_CLIENT_ID": "synthetic-oauth-client-id",
        "MAIL_MCP_OWNER_SUB": "12345678-1234-5678-1234-567812345678",
        "MAIL_MCP_RESOURCE_URL": "https://mail-mcp-beta.vercel.app/mcp",
    }
    _, oauth_app = create_http_app({"MAIL_MCP_MODE": "oauth", **base_env})
    assert isinstance(oauth_app, OAuthGate)

    async def verify_session(token):
        if token == "synthetic-owner-session":
            return OwnerSessionVerification(owner=True, reason="ok")
        if token == "synthetic-other-user-session":
            return OwnerSessionVerification(owner=False, reason="owner_mismatch")
        return OwnerSessionVerification(owner=None, reason="issuer_mismatch")

    async def verify_mcp_token(token):
        return SimpleNamespace(subject="synthetic-owner") if token == "synthetic-resource-token" else None

    monkeypatch.setattr(oauth_app.verifier, "verify_owner_session_detailed", verify_session)
    monkeypatch.setattr(oauth_app.verifier, "verify_mcp_token_async", verify_mcp_token)

    with TestClient(oauth_app) as oauth_client:
        owner = oauth_client.get(
            "/oauth/session-check",
            headers={"Authorization": "Bearer synthetic-owner-session"},
        )
        assert owner.status_code == 200
        assert owner.json() == {"owner": True}

        other_user = oauth_client.get(
            "/oauth/session-check",
            headers={"Authorization": "Bearer synthetic-other-user-session"},
        )
        assert other_user.status_code == 403
        assert other_user.json() == {"owner": False}

        invalid = oauth_client.get(
            "/oauth/session-check",
            headers={"Authorization": "Bearer synthetic-invalid-session"},
        )
        assert invalid.status_code == 401
        assert invalid.json() == {"error": "invalid_session", "reason": "issuer_mismatch"}
        assert "synthetic-invalid-session" not in invalid.text

        session_to_mcp = rpc(
            oauth_client,
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "offline-test", "version": "1"},
            },
            request_id=5,
            headers={"Authorization": "Bearer synthetic-owner-session"},
        )
        assert session_to_mcp.status_code == 401
        assert "issuer_mismatch" not in session_to_mcp.text

        resource_to_mcp = rpc(
            oauth_client,
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "offline-test", "version": "1"},
            },
            request_id=6,
            headers={"Authorization": "Bearer synthetic-resource-token"},
        )
        assert resource_to_mcp.status_code == 200
