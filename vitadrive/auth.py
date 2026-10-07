"""Clerk authentication.

The dashboard signs users in with Clerk (https://clerk.com). Clerk gives the browser a short-lived
session JWT, which the dashboard sends as ``Authorization: Bearer <token>``. The API verifies its
signature against Clerk's public keys (JWKS) and uses the ``sub`` claim (the Clerk user ID) as the
owner of the user's vehicles.

When ``CLERK_PUBLISHABLE_KEY`` is not set, authentication is disabled and every request acts as a
single local user - convenient for development, never for a public deployment.
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field

import jwt

from .storage import LOCAL_OWNER


class AuthError(Exception):
    pass


def frontend_api_from_publishable_key(key: str) -> str:
    """Clerk publishable keys are ``pk_(test|live)_`` + base64("<frontend-api-host>$")."""
    try:
        encoded = key.split("_", 2)[2]
        decoded = base64.b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
    except (IndexError, ValueError) as exc:
        raise ValueError("Invalid CLERK_PUBLISHABLE_KEY") from exc
    return decoded.rstrip("$")


def _origin(url: str) -> str:
    """Normalise an origin for comparison: trims spaces/quotes, a trailing slash and letter case."""
    return url.strip().strip("'\"").rstrip("/").lower()


@dataclass
class ClerkAuth:
    publishable_key: str | None = None
    jwks_url: str | None = None
    authorized_parties: list[str] = field(default_factory=list)
    _jwks: jwt.PyJWKClient | None = field(default=None, init=False, repr=False)

    @classmethod
    def from_env(cls) -> ClerkAuth:
        parties = [p for p in os.environ.get("CLERK_AUTHORIZED_PARTIES", "").split(",") if _origin(p)]
        return cls(publishable_key=os.environ.get("CLERK_PUBLISHABLE_KEY") or None,
                   jwks_url=os.environ.get("CLERK_JWKS_URL") or None,
                   authorized_parties=parties)

    @property
    def enabled(self) -> bool:
        return bool(self.publishable_key)

    @property
    def frontend_api(self) -> str | None:
        return frontend_api_from_publishable_key(self.publishable_key) if self.publishable_key else None

    def _jwks_client(self) -> jwt.PyJWKClient:
        if self._jwks is None:
            url = self.jwks_url or f"https://{self.frontend_api}/.well-known/jwks.json"
            self._jwks = jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)
        return self._jwks

    def user_id(self, authorization: str | None) -> str:
        """Return the signed-in user's ID from an ``Authorization`` header, or raise AuthError."""
        if not self.enabled:
            return LOCAL_OWNER
        if not authorization or not authorization.lower().startswith("bearer "):
            raise AuthError("Sign in required")
        token = authorization[7:].strip()
        try:
            key = self._jwks_client().get_signing_key_from_jwt(token)
            claims = jwt.decode(token, key.key, algorithms=["RS256"], options={"require": ["exp", "sub"]},
                                leeway=10)
        except jwt.PyJWTError as exc:
            raise AuthError("Invalid or expired session") from exc
        azp = _origin(claims.get("azp") or "")
        if self.authorized_parties and azp not in {_origin(p) for p in self.authorized_parties}:
            raise AuthError(f"Token issued for an unauthorized origin ({azp or 'none'}); "
                            "add it to CLERK_AUTHORIZED_PARTIES")
        return claims["sub"]
