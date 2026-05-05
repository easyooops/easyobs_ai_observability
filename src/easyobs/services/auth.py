"""Password hashing + JWT issue / decode utilities.

The JWT signing key is loaded from ``settings.jwt_secret`` when set, otherwise
generated once at boot and persisted to ``data/jwt.secret`` so restarts don't
invalidate active sessions during local development.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()
_ALG = "HS256"


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        _hasher.verify(hashed, plain)
        return True
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def load_or_create_secret(configured: str, persistent_path: Path) -> str:
    """Pick the env-supplied secret if present, otherwise materialize a random
    one and persist it under ``data/`` so subsequent boots reuse it."""
    if configured:
        return configured
    if persistent_path.exists():
        return persistent_path.read_text(encoding="utf-8").strip()
    persistent_path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_urlsafe(48)
    persistent_path.write_text(secret, encoding="utf-8")
    return secret


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """Decoded session JWT payload.

    ``current_org`` is empty until the user picks an org via ``select-org``;
    SA users may switch freely while non-SA must hold an approved membership."""

    user_id: str
    is_super_admin: bool
    current_org: str | None
    exp: int


class JwtCodec:
    def __init__(self, secret: str, ttl_hours: int) -> None:
        self._secret = secret
        self._ttl = timedelta(hours=ttl_hours)

    def issue(
        self,
        *,
        user_id: str,
        is_super_admin: bool,
        current_org: str | None,
    ) -> str:
        now = datetime.now(tz=timezone.utc)
        payload: dict[str, Any] = {
            "sub": user_id,
            "sa": bool(is_super_admin),
            "org": current_org or "",
            "iat": int(now.timestamp()),
            "exp": int((now + self._ttl).timestamp()),
        }
        return jwt.encode(payload, self._secret, algorithm=_ALG)

    def decode(self, token: str) -> TokenClaims | None:
        try:
            payload = jwt.decode(token, self._secret, algorithms=[_ALG])
        except jwt.PyJWTError:
            return None
        org = payload.get("org") or None
        return TokenClaims(
            user_id=payload["sub"],
            is_super_admin=bool(payload.get("sa", False)),
            current_org=org,
            exp=int(payload.get("exp", 0)),
        )
