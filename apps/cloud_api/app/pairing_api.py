"""HTTP API for Home Assistant pairing bootstrap and polling."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .database import get_database_session
from .domain.enums import PairingStatus
from .pairing import PairingAccessDeniedError, PairingService

router = APIRouter(prefix="/connector/v1/pairing", tags=["connector-pairing"])


class PairingSessionCreateRequest(BaseModel):
    installation_nonce: str = Field(min_length=16, max_length=255)


class PairingSessionCreateResponse(BaseModel):
    session_id: str
    code: str
    polling_secret: str
    expires_at: str


class PairingPollResponse(BaseModel):
    status: str
    installation_id: str | None = None
    connector_credential: str | None = None


def _service(session: AsyncSession) -> PairingService:
    settings = get_settings()
    return PairingService(
        session,
        code_pepper=settings.pairing_code_pepper.encode(),
        delivery_key=settings.pairing_delivery_key.encode(),
    )


@router.post("/sessions", response_model=PairingSessionCreateResponse)
async def create_pairing_session(
    payload: PairingSessionCreateRequest,
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> PairingSessionCreateResponse:
    started = await _service(session).create_session(installation_nonce=payload.installation_nonce)
    return PairingSessionCreateResponse(
        session_id=str(started.session_id),
        code=started.code,
        polling_secret=started.polling_secret,
        expires_at=started.expires_at.isoformat(),
    )


@router.get("/sessions/{session_id}", response_model=PairingPollResponse)
async def poll_pairing_session(
    session_id: UUID,
    session: Annotated[AsyncSession, Depends(get_database_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> PairingPollResponse:
    if not authorization or not authorization.startswith("Pairing "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    polling_secret = authorization.removeprefix("Pairing ").strip()
    try:
        result = await _service(session).poll(
            session_id=session_id,
            polling_secret=polling_secret,
        )
    except PairingAccessDeniedError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED) from error

    result_status = "denied" if result.status is PairingStatus.LOCKED else result.status.value
    return PairingPollResponse(
        status=result_status,
        installation_id=str(result.installation_id) if result.installation_id else None,
        connector_credential=result.connector_credential,
    )
