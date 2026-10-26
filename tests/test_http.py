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
