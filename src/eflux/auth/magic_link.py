"""Passwordless email magic link.

Dev mode: instead of sending an email, we log the link to stdout (and return it from the
API for now — clearly tagged as dev-only). Production needs an SMTP integration.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from eflux.auth.hashing import generate_token, hash_token, is_expired
from eflux.config import get_settings
from eflux.db.models import MagicLink, User

log = logging.getLogger(__name__)


async def create_magic_link(session: AsyncSession, email: str) -> str:
    """Generate a magic-link token, persist its hash, return the plaintext token."""
    settings = get_settings()
    token = generate_token()
    expires = datetime.now(UTC) + timedelta(minutes=settings.magic_link_ttl_min)
    ml = MagicLink(
        email=email.strip().lower(),
        token_hash=hash_token(token),
        expires_at=expires,
    )
    session.add(ml)
    await session.flush()
    if settings.env == "dev":
        log.warning("DEV MAGIC LINK for %s: token=%s (expires %s)", email, token, expires)
    return token


async def consume_magic_link(session: AsyncSession, token: str) -> User | None:
    """Validate token, mark consumed, return/create the associated User."""
    th = hash_token(token)
    stmt = select(MagicLink).where(MagicLink.token_hash == th)
    ml = (await session.execute(stmt)).scalar_one_or_none()
    if ml is None:
        return None
    if ml.consumed_at is not None:
        return None
    if is_expired(ml.expires_at):
        return None

    ml.consumed_at = datetime.now(UTC)
    user = (
        await session.execute(select(User).where(User.email == ml.email))
    ).scalar_one_or_none()
    if user is None:
        user = User(email=ml.email, is_active=True, created_at=datetime.now(UTC))
        session.add(user)
        await session.flush()
    return user
