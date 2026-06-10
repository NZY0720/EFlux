"""Auth: passwordless email magic link, session issuance, API key minting."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from eflux.api.deps import CurrentUser, DbSession
from eflux.auth.api_key import create_api_key
from eflux.auth.magic_link import consume_magic_link, create_magic_link
from eflux.auth.session import create_session
from eflux.config import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


class MagicLinkRequest(BaseModel):
    email: EmailStr


class MagicLinkResponse(BaseModel):
    sent: bool
    # Dev-only echo of the token so you can copy-paste it during local dev. NEVER in prod.
    dev_token: str | None = None


class ConsumeRequest(BaseModel):
    token: str = Field(min_length=10)


class SessionResponse(BaseModel):
    session_token: str
    user_id: int
    email: str


class ApiKeyMintRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ApiKeyMintResponse(BaseModel):
    name: str
    key: str  # plaintext — shown ONCE
    prefix: str
    created_at: datetime


@router.post("/magic-link", response_model=MagicLinkResponse)
async def request_magic_link(payload: MagicLinkRequest, session: DbSession) -> MagicLinkResponse:
    token = await create_magic_link(session, payload.email)
    settings = get_settings()
    return MagicLinkResponse(
        sent=True,
        dev_token=token if settings.env == "dev" else None,
    )


@router.post("/consume", response_model=SessionResponse)
async def consume(payload: ConsumeRequest, session: DbSession) -> SessionResponse:
    user = await consume_magic_link(session, payload.token)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid or expired token")
    tok = await create_session(session, user)
    return SessionResponse(session_token=tok, user_id=user.id, email=user.email)


@router.post("/api-keys", response_model=ApiKeyMintResponse)
async def mint_api_key(
    payload: ApiKeyMintRequest,
    session: DbSession,
    user: CurrentUser,
) -> ApiKeyMintResponse:
    issued = await create_api_key(session, user, name=payload.name)
    return ApiKeyMintResponse(
        name=issued.record.name,
        key=issued.plaintext,
        prefix=issued.record.key_prefix,
        created_at=issued.record.created_at,
    )
