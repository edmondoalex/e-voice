"""FastAPI application entry point."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .admin_console import router as admin_console_router
from .alexa import router as alexa_router
from .alexa_laboratory import router as alexa_laboratory_router
from .alexa_portal_oauth import router as alexa_portal_oauth_router
from .alexa_routines import connector_router as alexa_routine_connector_router
from .alexa_routines import router as alexa_routines_router
from .config import get_settings
from .control4_favorites import router as control4_favorites_router
from .doorbells import public_router as doorbell_public_router
from .doorbells import router as doorbell_router
from .evcp import router as evcp_router
from .laboratory_learning import router as laboratory_learning_router
from .legal import router as legal_router
from .media_api import router as media_router
from .pairing_api import router as pairing_router
from .schemas import HealthResponse
from .voice_alerts import run_live_alert_monitor

try:
    application_version = version("ekonex-voice")
except PackageNotFoundError:
    application_version = "0.1.0"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    monitor: asyncio.Task[None] | None = None
    if get_settings().environment == "laboratory":
        monitor = asyncio.create_task(run_live_alert_monitor())
    try:
        yield
    finally:
        if monitor is not None:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)


app = FastAPI(title="Ekonex Voice Cloud API", version=application_version, lifespan=lifespan)
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).resolve().parents[3] / "brand"),
    name="static",
)
app.include_router(pairing_router)
app.include_router(legal_router)
app.include_router(admin_console_router)
app.include_router(evcp_router)
# Browser-facing account linking must win the GET /oauth/authorize route;
# token/revoke/directive endpoints remain on the Alexa adapter router.
app.include_router(alexa_portal_oauth_router)
app.include_router(alexa_router)
app.include_router(alexa_laboratory_router)
app.include_router(laboratory_learning_router)
app.include_router(alexa_routines_router)
app.include_router(alexa_routine_connector_router)
app.include_router(doorbell_router)
app.include_router(doorbell_public_router)
app.include_router(control4_favorites_router)
app.include_router(media_router)


@app.get("/health", response_model=HealthResponse, tags=["operations"])
async def health() -> HealthResponse:
    """Return process liveness without depending on external services."""

    return HealthResponse(version=application_version)
