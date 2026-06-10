"""Cryptographic helpers + small util shared by auth modules.

We store ONLY hashes of secrets (magic link tokens, session tokens, API keys); the
plaintext is returned once to the caller and never persisted.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime


def generate_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """SHA-256 hex digest. Sufficient because tokens are high-entropy random — no need for KDF."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_expired(dt: datetime) -> bool:
    """Compare against now(UTC). SQLite-loaded datetimes may be naive — coerce to UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt < datetime.now(UTC)
