"""FastAPI application entry point."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
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
from .installation_bound import BoundLaunchDenied
from .installation_bound_api import router as installation_bound_router
from .laboratory_learning import router as laboratory_learning_router
from .legal import router as legal_router
from .media_api import router as media_router
from .pairing_api import router as pairing_router
from .schemas import HealthResponse
from .voice_alerts import run_live_alert_monitor
from .wear_api import router as wear_router
from .wear_devices import WearDenied

try:
    application_version = version("ekonex-voice")
except PackageNotFoundError:
    application_version = "0.1.0"


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    monitor: asyncio.Task[None] | None = None
    wear_worker: asyncio.Task[None] | None = None
    from .database import async_session_factory
    from .wear_bridge import verify_bridge_startup
    await verify_bridge_startup(async_session_factory, fence_enabled=get_settings().wear_bridge_fence_enabled)
    # Explicit provisioning only; no key and no feature flags means no provider.
    if get_settings().wear_bridge_fence_enabled:
        from .wear_key_provider import cloud_provider, KeyUnavailable
        config={'wear_bridge_master_b64':os.environ.get('EKONEX_WEAR_BRIDGE_MASTER_B64'),
                'wear_bridge_previous_master_b64':os.environ.get('EKONEX_WEAR_BRIDGE_PREVIOUS_MASTER_B64')}
        try: application.state.wear_bridge_key_provider=cloud_provider(config)
        except KeyUnavailable: application.state.wear_bridge_key_provider=None
    if get_settings().environment == "laboratory":
        monitor = asyncio.create_task(run_live_alert_monitor())
    if get_settings().wear_bridge_fence_enabled and get_settings().wear_bridge_wake_worker_enabled:
        from .database import async_session_factory
        from .wear_wake_worker import run_worker
        wear_worker = asyncio.create_task(run_worker(async_session_factory))
    try:
        yield
    finally:
        application.state.wear_bridge_key_provider=None
        if monitor is not None:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        if wear_worker is not None:
            wear_worker.cancel()
            await asyncio.gather(wear_worker, return_exceptions=True)


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

app.include_router(installation_bound_router)
app.include_router(wear_router)
from .wear_bridge import router as wear_bridge_router
app.include_router(wear_bridge_router)
from .wear_control_service import router as wear_control_router
app.include_router(wear_control_router)


@app.exception_handler(BoundLaunchDenied)
async def bounded_denied(_request, _error) -> JSONResponse:
    """Uniform bounded denial without resource-existence disclosure."""
    return JSONResponse({"detail": "bounded_resource_denied"}, status_code=404)


@app.exception_handler(WearDenied)
async def wear_denied(_request, _error) -> JSONResponse:
    """Do not disclose which Wear scope or capability check failed."""
    return JSONResponse({"detail": "wear_resource_denied"}, status_code=403)


@app.get("/health", response_model=HealthResponse, tags=["operations"])
async def health() -> HealthResponse:
    """Return process liveness without depending on external services."""

    return HealthResponse(version=application_version)
