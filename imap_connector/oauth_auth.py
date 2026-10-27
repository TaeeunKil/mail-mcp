"""Fail-closed Supabase OAuth resource-token verification.

Supabase remains the OAuth authorization server. This module only verifies the
bearer access tokens it issues; it never exchanges authorization codes or
creates tokens. OAuth authentication requires the public configuration values.
Mail tools are registered only when valid Naver credentials are also present.
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
from jwt.exceptions import (
    DecodeError,
    ExpiredSignatureError,
    ImmatureSignatureError,
    InvalidIssuedAtError,
    InvalidIssuerError,
    InvalidSignatureError,
    MissingRequiredClaimError,
    PyJWKClientConnectionError,
    PyJWKClientError,
    PyJWKSetError,
    PyJWTError,
)
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
_VERIFICATION_REASONS = frozenset({
    "ok",
    "malformed_token",
    "unsupported_algorithm",
    "invalid_key_id",
    "jwks_unavailable",
    "jwks_invalid",
    "signing_key_not_found",
    "key_algorithm_mismatch",
    "invalid_signature",
    "issuer_mismatch",
    "audience_mismatch",
    "expired",
    "not_yet_valid",
    "issued_at_invalid",
    "missing_claim",
    "invalid_subject",
    "owner_mismatch",
    "client_mismatch",
    "scope_mismatch",
    "invalid_claims",
    "verification_unavailable",
})


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


@dataclass(frozen=True, slots=True)
class OwnerSessionVerification:
    """Safe outcome for the consent UI; reason is always a fixed category."""

    owner: bool | None
    reason: str


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

    def _decode_detailed(self, token: str, *, purpose: str) -> tuple[dict | None, str]:
        if not isinstance(token, str) or not token or len(token) > _MAX_JWT_LENGTH:
            return None, "malformed_token"
        try:
            header = jwt.get_unverified_header(token)
        except (DecodeError, PyJWTError, TypeError, ValueError):
            return None, "malformed_token"

        if not isinstance(header, Mapping):
            return None, "malformed_token"
        algorithm = header.get("alg")
        if algorithm not in _JWT_ALGORITHMS:
            return None, "unsupported_algorithm"
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid or len(kid) > 256:
            return None, "invalid_key_id"

        try:
            signing_key = self.jwks_client.get_signing_key_from_jwt(token)
        except PyJWKClientConnectionError:
            return None, "jwks_unavailable"
        except PyJWKSetError:
            return None, "jwks_invalid"
        except PyJWKClientError:
            return None, "signing_key_not_found"
        except (OSError, TimeoutError):
            return None, "jwks_unavailable"
        except Exception:
            return None, "verification_unavailable"

        if getattr(signing_key, "algorithm_name", None) != algorithm:
            return None, "key_algorithm_mismatch"

        required = ["iss", "aud", "exp", "iat", "sub"]
        if purpose == "mcp":
            required.extend(["client_id", _MCP_SCOPE_CLAIM])
        try:
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
        except ExpiredSignatureError:
            return None, "expired"
        except ImmatureSignatureError:
            return None, "not_yet_valid"
        except InvalidIssuedAtError:
            return None, "issued_at_invalid"
        except InvalidIssuerError:
            return None, "issuer_mismatch"
        except InvalidSignatureError:
            return None, "invalid_signature"
        except MissingRequiredClaimError:
            return None, "missing_claim"
        except DecodeError:
            return None, "malformed_token"
        except PyJWTError:
            return None, "invalid_claims"
        except (OSError, TimeoutError):
            return None, "jwks_unavailable"
        except Exception:
            return None, "verification_unavailable"

        if not isinstance(claims, dict):
            return None, "invalid_claims"

        expected_audience = self.settings.resource_url if purpose == "mcp" else "authenticated"
        if claims.get("aud") != expected_audience:
            return None, "audience_mismatch"
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            return None, "invalid_subject"
        if purpose == "mcp" and subject != self.settings.owner_sub:
            return None, "owner_mismatch"

        if purpose == "mcp":
            if claims.get("client_id") != self.settings.client_id:
                return None, "client_mismatch"
            scopes = claims.get(_MCP_SCOPE_CLAIM)
            if scopes != [_REQUIRED_MCP_SCOPE]:
                return None, "scope_mismatch"
        return claims, "ok"

    def _decode(self, token: str, *, purpose: str) -> dict | None:
        """Compatibility wrapper that intentionally hides all failure details."""
        claims, _reason = self._decode_detailed(token, purpose=purpose)
        return claims

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

    async def verify_owner_session(self, token: str) -> bool | None:
        """Validate a Supabase login session; None means invalid, bool means owner match."""
        return await anyio.to_thread.run_sync(self.verify_owner_session_sync, token)

    def verify_owner_session_sync(self, token: str) -> bool | None:
        """Validate an authenticated-audience session without MCP client/scope claims."""
        return self.verify_owner_session_detailed_sync(token).owner

    async def verify_owner_session_detailed(self, token: str) -> OwnerSessionVerification:
        """Return only a fixed, non-sensitive category for consent diagnostics."""
        return await anyio.to_thread.run_sync(self.verify_owner_session_detailed_sync, token)

    def verify_owner_session_detailed_sync(self, token: str) -> OwnerSessionVerification:
        claims, reason = self._decode_detailed(token, purpose="session")
        if claims is None:
            # Keep the response vocabulary closed even if future internal code
            # accidentally supplies an unrecognized category.
            if reason not in _VERIFICATION_REASONS or reason == "ok":
                reason = "verification_unavailable"
            return OwnerSessionVerification(owner=None, reason=reason)
        if claims["sub"] != self.settings.owner_sub:
            return OwnerSessionVerification(owner=False, reason="owner_mismatch")
        return OwnerSessionVerification(owner=True, reason="ok")


def is_valid_authorization_id(value: str | None) -> bool:
    """Bound untrusted authorization_id input before passing it to the SDK."""
    return bool(value and len(value) <= 256 and re.fullmatch(r"[A-Za-z0-9._~-]+", value))
