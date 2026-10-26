"""Bearer boundary tests use synthetic credentials and never contact IMAP."""
import base64
import hashlib
import json

import pytest
from starlette.testclient import TestClient

import imap_connector.http_server as http_server
from imap_connector.http_server import BearerGate, DiagnosticQueryGuard, create_http_app

_TEST_BYTES = bytes([251, 255]) + bytes(range(30))
TOKEN = base64.b64encode(_TEST_BYTES).decode("ascii")
URLSAFE_TOKEN = base64.urlsafe_b64encode(_TEST_BYTES).decode("ascii").rstrip("=")
MAIL_ENV = {
    "MAIL_MCP_MODE": "token",
    "MAIL_MCP_TOKEN": TOKEN,
    "NAVER_IMAP_USER": "owner@example.invalid",
    "NAVER_IMAP_APP_PASSWORD": "synthetic-app-password",
}


def rpc(client, method, params=None, token=None, request_id=1, extra_headers=None):
    headers = {"Accept": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if extra_headers:
        headers.update(extra_headers)
    return client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
    )


def initialized(client, token):
    return rpc(
        client,
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "offline-bearer-test", "version": "1"},
        },
        token=token,
    )


@pytest.fixture(scope="module")
def private_client():
    server, app = create_http_app(environ=dict(MAIL_ENV))
    with TestClient(app) as client:
        yield server, client


def test_private_mode_requires_bearer_before_initialize_or_tool_listing(private_client):
    _, client = private_client
    for method in ("initialize", "tools/list", "tools/call"):
        response = rpc(client, method, {"name": "list_accounts", "arguments": {}})
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == 'Bearer realm="mail-mcp"'
        assert response.json() == {"error": "unauthorized"}
        assert TOKEN not in response.text
        assert "owner@example.invalid" not in response.text
        assert "synthetic-app-password" not in response.text


def test_unready_private_mode_returns_503_before_tools(private_client):
    _, client = private_client
    gate = client.app
    assert isinstance(gate, BearerGate)
    original_ready = gate.ready
    gate.ready = False
    try:
        response = rpc(client, "tools/list", token=TOKEN)
    finally:
        gate.ready = original_ready
    assert response.status_code == 503
    assert response.json() == {"error": "private_service_unavailable"}
    assert "list_accounts" not in response.text


def test_private_mode_initializes_and_lists_only_existing_read_only_tools(private_client):
    _, client = private_client
    response = initialized(client, TOKEN)
    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "Private read-only Naver mail"

    listing = rpc(client, "tools/list", token=TOKEN, request_id=2)
    assert listing.status_code == 200
    tools = listing.json()["result"]["tools"]
    assert {tool["name"] for tool in tools} == {
        "service_status", "list_accounts", "list_folders", "search_messages", "read_message"
    }
    for tool in tools:
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["annotations"]["destructiveHint"] is False
    assert not {"send_mail", "delete_message", "move_message", "mark_read"} & {tool["name"] for tool in tools}


def test_standard_and_urlsafe_base64_tokens_are_supported():
    assert "+" in TOKEN and "/" in TOKEN and TOKEN.endswith("=")
    assert "-" in URLSAFE_TOKEN and "_" in URLSAFE_TOKEN
    for token in (TOKEN, URLSAFE_TOKEN):
        assert http_server._safe_token_digest({"MAIL_MCP_TOKEN": token}) is not None


def test_private_status_is_auth_protected_and_does_not_disclose_credentials(private_client):
    _, client = private_client
    response = rpc(client, "tools/call", {"name": "service_status", "arguments": {}}, token=TOKEN)
    assert response.status_code == 200
    status = json.loads(response.json()["result"]["content"][0]["text"])
    assert status == {
        "service": "readonly-imap-mcp",
        "mode": "private-read-only",
        "mail_access": "enabled",
        "mail_connection": "configured; not tested",
        "authentication": "bearer token",
    }
    assert "owner@example.invalid" not in response.text
    assert "synthetic-app-password" not in response.text
    assert TOKEN not in response.text


def test_list_accounts_uses_only_fixed_naver_configuration_without_network(private_client, monkeypatch):
    _, client = private_client

    class NoNetworkConnector:
        def __init__(self, accounts, environ):
            assert accounts == http_server.NAVER_ACCOUNT
            assert accounts["naver"]["host"] == "imap.naver.com"
            assert accounts["naver"]["port"] == 993
            assert environ["NAVER_IMAP_USER"] == "owner@example.invalid"
            assert environ["NAVER_IMAP_APP_PASSWORD"] == "synthetic-app-password"

        def list_accounts(self):
            return [{"account": "naver", "configured": True}]

    monkeypatch.setattr(http_server, "Connector", NoNetworkConnector)
    response = rpc(client, "tools/call", {"name": "list_accounts", "arguments": {}}, token=TOKEN)
    assert response.status_code == 200
    result = json.loads(response.json()["result"]["content"][0]["text"])
    assert result == {"accounts": [{"account": "naver", "configured": True}]}
    assert "owner@example.invalid" not in response.text
    assert "synthetic-app-password" not in response.text
    assert TOKEN not in response.text


@pytest.mark.parametrize(
    "environment",
    [
        {"MAIL_MCP_MODE": "token"},
        {**MAIL_ENV, "MAIL_MCP_TOKEN": "too-short"},
        {**MAIL_ENV, "MAIL_MCP_TOKEN": "not valid because spaces"},
        {**MAIL_ENV, "MAIL_MCP_TOKEN": base64.b64encode(bytes(range(31))).decode("ascii")},
        {**MAIL_ENV, "MAIL_MCP_TOKEN": TOKEN + "="},
        {**MAIL_ENV, "MAIL_MCP_TOKEN": base64.b64encode(b"x" * 100).decode("ascii")},
        {**MAIL_ENV, "NAVER_IMAP_USER": ""},
        {**MAIL_ENV, "NAVER_IMAP_APP_PASSWORD": "bad\npassword"},
        {key: value for key, value in MAIL_ENV.items() if key != "NAVER_IMAP_USER"},
    ],
)
def test_token_mode_with_missing_or_invalid_configuration_is_unavailable(environment):
    _, app = create_http_app(environ=environment)
    assert isinstance(app, BearerGate)
    assert app.ready is False


def test_unknown_mode_fails_closed_instead_of_falling_back_to_public_diagnostics():
    _, app = create_http_app(environ={**MAIL_ENV, "MAIL_MCP_MODE": "oauth"})
    assert isinstance(app, BearerGate)
    assert app.ready is False


def test_missing_token_fails_closed_without_reading_mail_credentials():
    class TokenMissingEnvironment(dict):
        def get(self, key, default=None):
            if key in {"NAVER_IMAP_USER", "NAVER_IMAP_APP_PASSWORD"}:
                raise AssertionError("do not inspect mail credentials without a valid server token")
            return super().get(key, default)

    _, app = create_http_app(environ=TokenMissingEnvironment({"MAIL_MCP_MODE": "token"}))
    assert isinstance(app, BearerGate)
    assert app.ready is False


def test_token_alone_does_not_opt_in_to_mail_access():
    _, app = create_http_app(environ={
        "MAIL_MCP_TOKEN": TOKEN,
        "NAVER_IMAP_USER": "owner@example.invalid",
        "NAVER_IMAP_APP_PASSWORD": "synthetic-app-password",
    })
    assert isinstance(app, DiagnosticQueryGuard)


def test_diagnostics_mode_does_not_read_mail_environment():
    class DiagnosticOnlyEnvironment(dict):
        def get(self, key, default=None):
            if key in {"MAIL_MCP_TOKEN", "NAVER_IMAP_USER", "NAVER_IMAP_APP_PASSWORD"}:
                raise AssertionError("diagnostic mode must not inspect credentials")
            return super().get(key, default)

    _, app = create_http_app(environ=DiagnosticOnlyEnvironment())
    assert isinstance(app, DiagnosticQueryGuard)


def test_only_authorization_bearer_is_accepted(private_client):
    _, client = private_client
    for headers in (
        {"X-Authorization": f"Bearer {TOKEN}"},
        {"Authorization": f"Basic {TOKEN}"},
        {"Cookie": f"MAIL_MCP_TOKEN={TOKEN}"},
        {"Authorization": f"Bearer {TOKEN} "},
    ):
        response = rpc(client, "tools/list", extra_headers=headers)
        assert response.status_code == 401
        assert response.json() == {"error": "unauthorized"}


def test_url_tokens_are_rejected_even_when_valid_header_is_present(private_client):
    _, client = private_client
    response = client.post(
        "/mcp?access_token=synthetic-query-token",
        headers={"Accept": "application/json", "Authorization": f"Bearer {TOKEN}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert response.status_code == 400
    assert response.json() == {"error": "url_tokens_not_supported"}
    assert "synthetic-query-token" not in response.text


def test_constant_time_digest_comparison_is_used(private_client, monkeypatch):
    _, client = private_client
    calls = []
    real_compare = http_server.hmac.compare_digest

    def checked_compare(left, right):
        calls.append((type(left), type(right), len(left), len(right)))
        return real_compare(left, right)

    monkeypatch.setattr(http_server.hmac, "compare_digest", checked_compare)
    response = rpc(client, "tools/list", token=TOKEN)
    assert response.status_code == 200
    assert calls == [(bytes, bytes, 32, 32)]


def test_custom_header_token_is_not_a_substitute_for_authorization(private_client):
    _, client = private_client
    response = rpc(client, "tools/list", extra_headers={"MAIL_MCP_TOKEN": TOKEN})
    assert response.status_code == 401


def test_no_mail_call_is_made_while_importing_or_starting_private_app(private_client, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("creating an app must not connect to IMAP")

    monkeypatch.setattr(http_server, "Connector", forbidden)
    server, client = private_client
    response = rpc(client, "tools/list", token=TOKEN)
    assert response.status_code == 200
    assert server.session_manager._has_started is True
