"""Fail-closed Supabase OAuth resource-token verification.

Supabase remains the OAuth authorization server. This module only verifies the
bearer access tokens it issues; it never exchanges authorization codes or
creates tokens. OAuth mode stays unavailable until every required public
configuration value and Naver credential is present.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from urllib.parse import urlsplit
import uuid

import anyio
import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWTError
from mcp.server.auth.provider import AccessToken

from .core import ConnectorError, clean


SUPABASE_URL_ENV = "SUPABASE_URL"
SUPABASE_PUBLISHABLE_KEY_ENV = "SUPABASE_PUBLISHABLE_KEY"
OAUTH_CLIENT_ID_ENV = "MAIL_MCP_OAUTH_CLIENT_ID"
OWNER_SUB_ENV = "MAIL_MCP_OWNER_SUB"
RESOURCE_URL_ENV = "MAIL_MCP_RESOURCE_URL"

_PROJECT_HOST = re.compile(r"^[a-z0-9]{20}\.supabase\.co$")
_PUBLISHABLE_KEY = re.compile(r"^sb_publishable_[A-Za-z0-9_-]{20,200}$")
_JWT_ALGORITHMS = frozenset({"RS256", "ES256"})
_MAX_JWT_LENGTH = 16_384
_MCP_SCOPE_CLAIM = "mail_mcp_scopes"
_REQUIRED_MCP_SCOPE = "mail:read"


@dataclass(frozen=True, slots=True)
class OAuthSettings:
    """Validated configuration. All URLs are derived or exact HTTPS URLs."""

    supabase_url: str
    publishable_key: str
    issuer: str
    jwks_url: str
    resource_url: str
    client_id: str
    owner_sub: str

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> OAuthSettings | None:
        """Return settings only when every required value is safe and present."""
        supabase_url = environ.get(SUPABASE_URL_ENV, "")
        publishable_key = environ.get(SUPABASE_PUBLISHABLE_KEY_ENV, "")
        client_id = environ.get(OAUTH_CLIENT_ID_ENV, "")
        owner_sub = environ.get(OWNER_SUB_ENV, "")
        resource_url = environ.get(RESOURCE_URL_ENV, "")

        if not all(isinstance(value, str) and value for value in (
            supabase_url, publishable_key, client_id, owner_sub, resource_url
        )):
            return None

        try:
            parsed_supabase = urlsplit(supabase_url)
            parsed_resource = urlsplit(resource_url)
            supabase_port = parsed_supabase.port
            resource_port = parsed_resource.port
        except ValueError:
            return None
        if (
            parsed_supabase.scheme != "https"
            or parsed_supabase.username is not None
            or parsed_supabase.password is not None
            or supabase_port is not None
            or parsed_supabase.path not in {"", "/"}
            or parsed_supabase.query
            or parsed_supabase.fragment
            or not _PROJECT_HOST.fullmatch(parsed_supabase.hostname or "")
        ):
            return None
        canonical_supabase_url = f"https://{parsed_supabase.hostname}"

        # New Supabase projects use publishable keys. Secret/service-role keys
        # and legacy JWT-shaped keys are intentionally not accepted here.
        if not _PUBLISHABLE_KEY.fullmatch(publishable_key):
            return None
        if any(ord(char) < 0x21 or ord(char) > 0x7e for char in client_id) or len(client_id) > 256:
            return None
        if not _is_uuid(owner_sub):
            return None

        if (
            parsed_resource.scheme != "https"
            or not parsed_resource.hostname
            or parsed_resource.username is not None
            or parsed_resource.password is not None
            or resource_port is not None
            or parsed_resource.path != "/mcp"
            or parsed_resource.query
            or parsed_resource.fragment
            or parsed_resource.netloc != parsed_resource.hostname
        ):
            return None
        try:
            clean(resource_url, 512)
            clean(client_id, 256)
        except ConnectorError:
            return None

        issuer = f"{canonical_supabase_url}/auth/v1"
        return cls(
            supabase_url=canonical_supabase_url,
            publishable_key=publishable_key,
            issuer=issuer,
            jwks_url=f"{issuer}/.well-known/jwks.json",
            resource_url=resource_url,
            client_id=client_id,
            owner_sub=str(uuid.UUID(owner_sub)),
        )


def _is_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value.lower()
    except (ValueError, AttributeError):
        return False


def has_naver_credentials(environ: Mapping[str, str]) -> bool:
    """Validate presence/shape without returning or logging credential values."""
    try:
        clean(environ.get("NAVER_IMAP_USER"), 320)
        clean(environ.get("NAVER_IMAP_APP_PASSWORD"), 2048)
    except ConnectorError:
        return False
    return True


class SupabaseJwtVerifier:
    """Verify Supabase asymmetric JWTs against its fixed JWKS endpoint."""

    def __init__(self, settings: OAuthSettings, jwks_client=None):
        self.settings = settings
        # The URL is derived from a validated Supabase project host; callers
        # cannot point this fetcher at arbitrary/internal network locations.
        self.jwks_client = jwks_client or PyJWKClient(
            settings.jwks_url,
            cache_jwk_set=True,
            lifespan=300,
            timeout=3,
        )

    def _decode(self, token: str, *, purpose: str) -> dict | None:
        if not isinstance(token, str) or not token or len(token) > _MAX_JWT_LENGTH:
            return None
        try:
            header = jwt.get_unverified_header(token)
            algorithm = header.get("alg")
            kid = header.get("kid")
            if algorithm not in _JWT_ALGORITHMS or not isinstance(kid, str) or not kid or len(kid) > 256:
                return None

            signing_key = self.jwks_client.get_signing_key_from_jwt(token)
            if getattr(signing_key, "algorithm_name", None) != algorithm:
                return None

            required = ["iss", "aud", "exp", "iat", "sub"]
            if purpose == "mcp":
                required.extend(["client_id", _MCP_SCOPE_CLAIM])
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=[algorithm],
                issuer=self.settings.issuer,
                options={
                    "verify_aud": False,  # exact audience comparison below
                    "require": required,
                },
            )
            if not isinstance(claims, dict):
                return None

            expected_audience = self.settings.resource_url if purpose == "mcp" else "authenticated"
            if claims.get("aud") != expected_audience:
                return None
            if claims.get("sub") != self.settings.owner_sub:
                return None

            if purpose == "mcp":
                if claims.get("client_id") != self.settings.client_id:
                    return None
                scopes = claims.get(_MCP_SCOPE_CLAIM)
                if scopes != [_REQUIRED_MCP_SCOPE]:
                    return None
            return claims
        except (PyJWTError, OSError, TimeoutError, ValueError, TypeError, KeyError):
            # Never log raw tokens, claims, or network errors. A missing or
            # unreachable JWKS endpoint is an authentication failure.
            return None
        except Exception:
            # Fail closed for transport and JWKS client failures as well.
            return None

    def verify_mcp_token(self, token: str) -> AccessToken | None:
        claims = self._decode(token, purpose="mcp")
        if claims is None:
            return None
        return AccessToken(
            token=token,
            client_id=self.settings.client_id,
            scopes=[_REQUIRED_MCP_SCOPE],
            expires_at=int(claims["exp"]),
            resource=self.settings.resource_url,
            subject=self.settings.owner_sub,
            claims=claims,
        )

    async def verify_mcp_token_async(self, token: str) -> AccessToken | None:
        # PyJWKClient performs blocking HTTPS work; keep it off the ASGI loop.
        return await anyio.to_thread.run_sync(self.verify_mcp_token, token)

    async def verify_owner_session(self, token: str) -> bool:
        """Check the signed-in Supabase user's session for the consent UI."""
        return await anyio.to_thread.run_sync(self.verify_owner_session_sync, token)

    def verify_owner_session_sync(self, token: str) -> bool:
        return self._decode(token, purpose="session") is not None


def is_valid_authorization_id(value: str | None) -> bool:
    """Bound untrusted authorization_id input before passing it to the SDK."""
    return bool(value and len(value) <= 256 and re.fullmatch(r"[A-Za-z0-9._~-]+", value))
