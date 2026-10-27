"""Synthetic-only tests for Supabase configuration and signed JWT checks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

from imap_connector.oauth_auth import OAuthSettings, SupabaseJwtVerifier
from imap_connector.http_server import BearerGate, OAuthGate, SETUP_PAGE, create_http_app
from imap_connector.oauth_ui import SUPABASE_JS_MODULE, consent_page


OWNER_SUB = "12345678-1234-5678-1234-567812345678"
OTHER_SUB = "87654321-4321-8765-4321-876543218765"
CLIENT_ID = "synthetic-oauth-client-id"
RESOURCE = "https://mail-mcp-beta.vercel.app/mcp"
ISSUER = "https://abcdefghijklmnopqrst.supabase.co/auth/v1"
PUBLIC_KEY = "sb_publishable_test_only_do_not_use_1234567890"
BASE_ENV = {
    "SUPABASE_URL": "https://abcdefghijklmnopqrst.supabase.co",
    "SUPABASE_PUBLISHABLE_KEY": PUBLIC_KEY,
    "MAIL_MCP_OAUTH_CLIENT_ID": CLIENT_ID,
    "MAIL_MCP_OWNER_SUB": OWNER_SUB,
    "MAIL_MCP_RESOURCE_URL": RESOURCE,
}


@dataclass
class StaticKeySet:
    public_key: object
    algorithm_name: str = "RS256"
    fail: bool = False

    def get_signing_key_from_jwt(self, token: str):
        if self.fail:
            raise PyJWKClientConnectionError("synthetic network detail must not leak")
        header = jwt.get_unverified_header(token)
        if header.get("kid") != "synthetic-key-id":
            raise PyJWKClientError("synthetic key ID not found")
        return SimpleNamespace(key=self.public_key, algorithm_name=self.algorithm_name)


@pytest.fixture(scope="module")
def signing_keys():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key, wrong_key


@pytest.fixture(scope="module")
def settings():
    result = OAuthSettings.from_environ(BASE_ENV)
    assert result is not None
    return result


def claims(settings, **overrides):
    payload = {
        "iss": settings.issuer,
        "aud": settings.resource_url,
        "exp": int(time.time()) + 300,
        "iat": int(time.time()) - 1,
        "sub": OWNER_SUB,
        "client_id": CLIENT_ID,
        "mail_mcp_scopes": ["mail:read"],
    }
    payload.update(overrides)
    return payload


def make_token(payload, private_key, *, algorithm="RS256", kid="synthetic-key-id"):
    headers = {"kid": kid} if kid else {}
    return jwt.encode(payload, private_key, algorithm=algorithm, headers=headers)


def verifier(settings, signing_key, *, algorithm="RS256", fail=False):
    return SupabaseJwtVerifier(
        settings,
        jwks_client=StaticKeySet(signing_key.public_key(), algorithm_name=algorithm, fail=fail),
    )


def test_oauth_settings_are_derived_and_contain_no_private_key():
    parsed = OAuthSettings.from_environ(BASE_ENV)
    assert parsed is not None
    assert parsed.issuer == ISSUER
    assert parsed.jwks_url == ISSUER + "/.well-known/jwks.json"
    assert parsed.resource_url == RESOURCE
    assert parsed.publishable_key == PUBLIC_KEY


@pytest.mark.parametrize(
    "changes",
    [
        {"SUPABASE_URL": "https://127.0.0.1"},
        {"SUPABASE_URL": "http://abcdefghijklmnopqrst.supabase.co"},
        {"SUPABASE_URL": "https://abcdefghijklmnopqrst.supabase.co:8443"},
        {"SUPABASE_URL": "https://abcdefghijklmnopqrst.supabase.co:notaport"},
        {"SUPABASE_URL": "https://abcdefghijklmnopqrst.supabase.co.evil.invalid"},
        {"SUPABASE_URL": "https://user@abcdefghijklmnopqrst.supabase.co"},
        {"SUPABASE_PUBLISHABLE_KEY": "sb_secret_test_key_that_must_be_rejected"},
        {"SUPABASE_PUBLISHABLE_KEY": "service_role_test_value"},
        {"MAIL_MCP_OWNER_SUB": "owner@example.invalid"},
        {"MAIL_MCP_RESOURCE_URL": "http://mail-mcp-beta.vercel.app/mcp"},
        {"MAIL_MCP_RESOURCE_URL": "https://mail-mcp-beta.vercel.app/mcp?token=x"},
        {"MAIL_MCP_RESOURCE_URL": "https://mail-mcp-beta.vercel.app/internal"},
        {"MAIL_MCP_RESOURCE_URL": "https://user@mail-mcp-beta.vercel.app/mcp"},
        {"MAIL_MCP_OAUTH_CLIENT_ID": "client id with spaces"},
    ],
)
def test_unsafe_or_incomplete_oauth_settings_fail_closed(changes):
    assert OAuthSettings.from_environ({**BASE_ENV, **changes}) is None


@pytest.mark.parametrize(
    "missing",
    [
        "SUPABASE_URL",
        "SUPABASE_PUBLISHABLE_KEY",
        "MAIL_MCP_OAUTH_CLIENT_ID",
        "MAIL_MCP_OWNER_SUB",
        "MAIL_MCP_RESOURCE_URL",
    ],
)
def test_missing_oauth_values_fail_closed(missing):
    env = dict(BASE_ENV)
    del env[missing]
    assert OAuthSettings.from_environ(env) is None


def test_valid_signed_resource_token_returns_read_only_sdk_access(signing_keys, settings):
    private_key, _ = signing_keys
    token = make_token(claims(settings), private_key)
    verified = verifier(settings, private_key).verify_mcp_token(token)
    assert verified is not None
    assert verified.subject == OWNER_SUB
    assert verified.client_id == CLIENT_ID
    assert verified.scopes == ["mail:read"]
    assert verified.resource == RESOURCE


def test_valid_es256_signed_resource_token_matches_supabase_project_algorithm(settings):
    private_key = ec.generate_private_key(ec.SECP256R1())
    token = jwt.encode(
        claims(settings),
        private_key,
        algorithm="ES256",
        headers={"kid": "synthetic-key-id"},
    )
    verified = verifier(settings, private_key, algorithm="ES256").verify_mcp_token(token)
    assert verified is not None
    assert verified.subject == OWNER_SUB
    assert verified.client_id == CLIENT_ID


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://wrong-project.supabase.co/auth/v1"},
        {"aud": "authenticated"},
        {"aud": [RESOURCE, "authenticated"]},
        {"sub": OTHER_SUB},
        {"client_id": "another-client"},
        {"mail_mcp_scopes": []},
        {"mail_mcp_scopes": ["mail:read", "mail:write"]},
        {"exp": int(time.time()) - 60},
        {"nbf": int(time.time()) + 600},
    ],
)
def test_invalid_resource_claims_are_rejected(overrides, signing_keys, settings):
    private_key, _ = signing_keys
    token = make_token(claims(settings, **overrides), private_key)
    assert verifier(settings, private_key).verify_mcp_token(token) is None


@pytest.mark.parametrize("claim", ["iss", "aud", "exp", "iat", "sub", "client_id", "mail_mcp_scopes"])
def test_missing_required_claim_is_rejected(claim, signing_keys, settings):
    private_key, _ = signing_keys
    payload = claims(settings)
    del payload[claim]
    token = make_token(payload, private_key)
    assert verifier(settings, private_key).verify_mcp_token(token) is None


def test_wrong_signature_and_unknown_kid_are_rejected(signing_keys, settings):
    private_key, wrong_key = signing_keys
    bad_signature = make_token(claims(settings), wrong_key)
    unknown_kid = make_token(claims(settings), private_key, kid="untrusted-kid")
    token_verifier = verifier(settings, private_key)
    assert token_verifier.verify_mcp_token(bad_signature) is None
    assert token_verifier.verify_mcp_token(unknown_kid) is None


def test_unsupported_algorithm_is_rejected(signing_keys, settings):
    token = make_token(claims(settings), b"synthetic-hmac-key-unused-in-production", algorithm="HS256")
    assert verifier(settings, signing_keys[0]).verify_mcp_token(token) is None


def test_jwks_failure_fails_closed(signing_keys, settings):
    private_key, _ = signing_keys
    token = make_token(claims(settings), private_key)
    assert verifier(settings, private_key, fail=True).verify_mcp_token(token) is None


@pytest.mark.parametrize(
    ("overrides", "expected_reason"),
    [
        ({"iss": "https://wrong-project.supabase.co/auth/v1"}, "issuer_mismatch"),
        ({"aud": "wrong-audience"}, "audience_mismatch"),
        ({"exp": int(time.time()) - 30}, "expired"),
        ({"nbf": int(time.time()) + 300}, "not_yet_valid"),
    ],
)
def test_owner_session_verification_reports_only_fixed_claim_categories(
    overrides, expected_reason, signing_keys, settings
):
    private_key, _ = signing_keys
    session_claims = {
        "iss": settings.issuer,
        "aud": "authenticated",
        "exp": int(time.time()) + 300,
        "iat": int(time.time()) - 1,
        "sub": OWNER_SUB,
    }
    session_claims.update(overrides)
    token = make_token(session_claims, private_key)

    result = verifier(settings, private_key).verify_owner_session_detailed_sync(token)
    assert result.owner is None
    assert result.reason == expected_reason
    assert "wrong-project" not in result.reason
    assert "synthetic" not in result.reason


def test_owner_session_verification_classifies_missing_claim_and_signature(signing_keys, settings):
    private_key, wrong_key = signing_keys
    missing_subject = make_token(
        {
            "iss": settings.issuer,
            "aud": "authenticated",
            "exp": int(time.time()) + 300,
            "iat": int(time.time()) - 1,
        },
        private_key,
    )
    bad_signature = make_token(
        {
            "iss": settings.issuer,
            "aud": "authenticated",
            "exp": int(time.time()) + 300,
            "iat": int(time.time()) - 1,
            "sub": OWNER_SUB,
        },
        wrong_key,
    )
    token_verifier = verifier(settings, private_key)

    assert token_verifier.verify_owner_session_detailed_sync(missing_subject).reason == "missing_claim"
    assert token_verifier.verify_owner_session_detailed_sync(bad_signature).reason == "invalid_signature"


def test_owner_session_verification_classifies_jwks_outage_without_exposing_transport_error(signing_keys, settings):
    private_key, _ = signing_keys
    token = make_token(
        {
            "iss": settings.issuer,
            "aud": "authenticated",
            "exp": int(time.time()) + 300,
            "iat": int(time.time()) - 1,
            "sub": OWNER_SUB,
        },
        private_key,
    )

    result = verifier(settings, private_key, fail=True).verify_owner_session_detailed_sync(token)
    assert result.owner is None
    assert result.reason == "jwks_unavailable"
    assert "synthetic network detail" not in result.reason


def test_owner_session_verification_classifies_unsupported_algorithm_and_key_mismatch(signing_keys, settings):
    private_key, _ = signing_keys
    hs_token = make_token(
        {
            "iss": settings.issuer,
            "aud": "authenticated",
            "exp": int(time.time()) + 300,
            "iat": int(time.time()) - 1,
            "sub": OWNER_SUB,
        },
        b"synthetic-hmac-key-unused-in-production",
        algorithm="HS256",
    )
    rs_token = make_token(
        {
            "iss": settings.issuer,
            "aud": "authenticated",
            "exp": int(time.time()) + 300,
            "iat": int(time.time()) - 1,
            "sub": OWNER_SUB,
        },
        private_key,
    )

    assert verifier(settings, private_key).verify_owner_session_detailed_sync(hs_token).reason == "unsupported_algorithm"
    assert verifier(settings, private_key, algorithm="ES256").verify_owner_session_detailed_sync(rs_token).reason == "key_algorithm_mismatch"


def test_owner_session_verification_classifies_malformed_token(signing_keys, settings):
    result = verifier(settings, signing_keys[0]).verify_owner_session_detailed_sync("not-a-jwt")

    assert result.owner is None
    assert result.reason == "malformed_token"


def test_owner_session_uses_authenticated_audience_only_for_owner(signing_keys, settings):
    private_key, _ = signing_keys
    token = make_token(
        {
            "iss": settings.issuer,
            "aud": "authenticated",
            "exp": int(time.time()) + 300,
            "iat": int(time.time()) - 1,
            "sub": OWNER_SUB,
        },
        private_key,
    )
    wrong_owner = make_token(
        {
            "iss": settings.issuer,
            "aud": "authenticated",
            "exp": int(time.time()) + 300,
            "iat": int(time.time()) - 1,
            "sub": OTHER_SUB,
        },
        private_key,
    )
    token_verifier = verifier(settings, private_key)
    assert token_verifier.verify_owner_session_sync(token) is True
    assert token_verifier.verify_owner_session_sync(wrong_owner) is False
    # A normal signInWithPassword session has no MCP client or mail-scope claims.
    assert token_verifier.verify_mcp_token(token) is None


def test_mcp_resource_token_cannot_be_used_as_supabase_owner_session(signing_keys, settings):
    private_key, _ = signing_keys
    token = make_token(claims(settings), private_key)
    token_verifier = verifier(settings, private_key)

    assert token_verifier.verify_mcp_token(token) is not None
    assert token_verifier.verify_owner_session_sync(token) is None


def test_invalid_owner_session_is_distinct_from_valid_non_owner_session(signing_keys, settings):
    private_key, _ = signing_keys
    valid_other_user = make_token(
        {
            "iss": settings.issuer,
            "aud": "authenticated",
            "exp": int(time.time()) + 300,
            "iat": int(time.time()) - 1,
            "sub": OTHER_SUB,
        },
        private_key,
    )
    expired = make_token(
        {
            "iss": settings.issuer,
            "aud": "authenticated",
            "exp": int(time.time()) - 30,
            "iat": int(time.time()) - 60,
            "sub": OWNER_SUB,
        },
        private_key,
    )
    token_verifier = verifier(settings, private_key)

    assert token_verifier.verify_owner_session_sync(valid_other_user) is False
    assert token_verifier.verify_owner_session_sync(expired) is None


def test_setup_page_does_not_claim_oauth_is_unimplemented_or_active():
    assert "Supabase OAuth" in SETUP_PAGE
    assert "OAuth client ID" in SETUP_PAGE
    assert "실제 메일 도구" in SETUP_PAGE
    assert "oauth가 후보로 선택됐지만" not in SETUP_PAGE


def test_oauth_http_configuration_fails_closed_until_required_mail_credentials_exist():
    server, unconfigured = create_http_app(environ={"MAIL_MCP_MODE": "oauth"})
    assert isinstance(unconfigured, BearerGate)
    assert unconfigured.ready is False
    assert unconfigured.app.state.mail_mcp_ready is False
    assert server._tool_manager._tools == {}


def test_oauth_metadata_route_and_consent_ui_are_prepared_without_mail_credentials():
    server, private_app = create_http_app(environ={"MAIL_MCP_MODE": "oauth", **BASE_ENV})
    assert isinstance(private_app, OAuthGate)
    assert private_app.auth_ready is True
    assert private_app.app.state.mail_mcp_auth_ready is True
    assert private_app.app.state.mail_mcp_ready is False
    assert set(server._tool_manager._tools) == {"service_status"}
    status = server._tool_manager._tools["service_status"].fn()
    assert status == {
        "service": "readonly-imap-mcp",
        "mode": "oauth-ready-mail-disabled",
        "mail_access": "disabled",
        "mail_connection": "not configured",
        "authentication": "Supabase OAuth; owner/client/audience/scope checked",
    }

    routes = {route.path for route in private_app.app.router.routes if hasattr(route, "path")}
    assert "/oauth/consent" in routes
    assert "/oauth/session-check" in routes
    assert "/.well-known/oauth-protected-resource/mcp" in routes
    assert private_app.metadata_url == "https://mail-mcp-beta.vercel.app/.well-known/oauth-protected-resource/mcp"

    settings = OAuthSettings.from_environ(BASE_ENV)
    page = consent_page(settings)
    assert page.status_code == 200
    page_text = page.body.decode("utf-8")
    assert "getAuthorizationDetails" in page_text
    assert "approveAuthorization" in page_text
    assert "denyAuthorization" in page_text
    assert SUPABASE_JS_MODULE in page_text
    assert "service_role" not in page_text
    assert "NAVER_IMAP_APP_PASSWORD" not in page_text
    assert "publishableKey" in page_text
    assert "response.status === 401" in page_text
    assert "response.status === 403" in page_text
    assert "issuer_mismatch:" in page_text
    assert "jwks_unavailable:" in page_text
    assert "await showSession({ loginJustSucceeded: true })" in page_text
    assert "ownerCheck.kind === 'invalid'" in page_text
    assert "console.log" not in page_text
    assert "nonce-" in page.headers["content-security-policy"]


@pytest.mark.parametrize("authorization_id", ["", "bad/value", "x" * 257, "id with spaces"])
def test_unsafe_authorization_ids_are_rejected(authorization_id):
    from imap_connector.oauth_auth import is_valid_authorization_id

    assert is_valid_authorization_id(authorization_id) is False


def test_synthetic_naver_configuration_registers_only_existing_read_tools():
    server, private_app = create_http_app({
        "MAIL_MCP_MODE": "oauth",
        **BASE_ENV,
        "NAVER_IMAP_USER": "synthetic@example.invalid",
        "NAVER_IMAP_APP_PASSWORD": "TEST_ONLY_DO_NOT_USE_SYNTHETIC_APP_PASSWORD",
    })
    assert isinstance(private_app, OAuthGate)
    assert private_app.auth_ready is True
    tool_names = set(server._tool_manager._tools)
    assert tool_names == {"service_status", "list_accounts", "list_folders", "search_messages", "read_message"}
    assert tool_names.isdisjoint({"send_email", "delete_email", "move_email", "mark_read"})


def test_custom_access_token_hook_template_requires_client_and_owner():
    hook = (Path(__file__).parents[1] / "supabase" / "mail_mcp_custom_access_token_hook.sql.example").read_text(encoding="utf-8")

    assert "<REPLACE_WITH_SUPABASE_OAUTH_CLIENT_ID>" in hook
    assert "<REPLACE_WITH_EXACT_HTTPS_MCP_RESOURCE_URL>" in hook
    assert "<REPLACE_WITH_OWNER_SUB_UUID>" in hook
    assert "and token_client_id = allowed_client_id then" not in hook
    assert "and token_client_id = allowed_client_id" in hook
    assert "and original_claims ->> 'sub' = allowed_owner_sub then" in hook
    assert "updated_claims := original_claims;" in hook
    assert "original_claims - 'mail_mcp_scopes'" not in hook
    assert "service_role key" in hook
