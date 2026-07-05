"""API keys for SDK access (external agents).

Format: `<prefix><short-id>.<secret>` — e.g. `eflux_a1b2c3.<32-char-secret>`.
The prefix + short-id is non-secret and indexed, used to find candidates fast.
The full key's hash is stored for verification.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from eflux.auth.hashing import generate_token, hash_token
from eflux.config import get_settings
from eflux.db.models import ApiKey, User


@dataclass
class IssuedApiKey:
    plaintext: str  # show ONCE to user
    record: ApiKey


async def create_api_key(session: AsyncSession, user: User, name: str) -> IssuedApiKey:
    settings = get_settings()
    short_id = secrets.token_hex(4)  # 8 hex chars
    secret = generate_token(32)
    plaintext = f"{settings.api_key_prefix}{short_id}.{secret}"
    rec = ApiKey(
        user_id=user.id,
        name=name,
        key_prefix=f"{settings.api_key_prefix}{short_id}",
        key_hash=hash_token(plaintext),
    )
    session.add(rec)
    await session.flush()
    return IssuedApiKey(plaintext=plaintext, record=rec)


async def list_api_keys(session: AsyncSession, user: User) -> list[ApiKey]:
    """The user's API keys (newest first), including revoked ones so the UI can show status."""
    stmt = select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc())
    return list((await session.execute(stmt)).scalars().all())


async def revoke_api_key(session: AsyncSession, user: User, key_prefix: str) -> bool:
    """Revoke one of the user's keys by its (non-secret) prefix. Idempotent — returns False if
    no matching active key belongs to the user."""
    stmt = select(ApiKey).where(
        ApiKey.user_id == user.id,
        ApiKey.key_prefix == key_prefix,
        ApiKey.revoked_at.is_(None),
    )
    rec = (await session.execute(stmt)).scalar_one_or_none()
    if rec is None:
        return False
    rec.revoked_at = datetime.now(UTC)
    return True


async def verify_api_key(session: AsyncSession, plaintext: str) -> User | None:
    settings = get_settings()
    if not plaintext.startswith(settings.api_key_prefix):
        return None
    try:
        prefix_part, _ = plaintext.split(".", 1)
    except ValueError:
        return None
    th = hash_token(plaintext)
    stmt = (
        select(ApiKey)
        .options(selectinload(ApiKey.user))
        .where(ApiKey.key_prefix == prefix_part, ApiKey.key_hash == th, ApiKey.revoked_at.is_(None))
    )
    rec = (await session.execute(stmt)).scalar_one_or_none()
    if rec is None:
        return None
    if not rec.user.is_active:
        return None
    rec.last_used_at = datetime.now(UTC)
    return rec.user
