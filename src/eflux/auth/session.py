"""Session token issuance + lookup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from eflux.auth.hashing import generate_token, hash_token, is_expired
from eflux.config import get_settings
from eflux.db.models import Session, User


async def create_session(session: AsyncSession, user: User) -> str:
    settings = get_settings()
    token = generate_token()
    expires = datetime.now(UTC) + timedelta(days=settings.session_ttl_day)
    s = Session(
        user_id=user.id,
        token_hash=hash_token(token),
        expires_at=expires,
    )
    session.add(s)
    await session.flush()
    return token


async def get_user_for_session_token(session: AsyncSession, token: str) -> User | None:
    th = hash_token(token)
    stmt = (
        select(Session)
        .options(selectinload(Session.user))
        .where(Session.token_hash == th)
    )
    s = (await session.execute(stmt)).scalar_one_or_none()
    if s is None:
        return None
    if is_expired(s.expires_at):
        return None
    if not s.user.is_active:
        return None
    return s.user


async def delete_session_for_token(session: AsyncSession, token: str) -> bool:
    """Delete one session by its plaintext Bearer token."""
    result = await session.execute(delete(Session).where(Session.token_hash == hash_token(token)))
    return result.rowcount == 1
