"""FastAPI application entry point."""

from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI

from .admin_console import router as admin_console_router
from .alexa import router as alexa_router
from .evcp import router as evcp_router
from .pairing_api import router as pairing_router
from .schemas import HealthResponse

try:
    application_version = version("ekonex-voice")
except PackageNotFoundError:
    application_version = "0.1.0"

app = FastAPI(title="Ekonex Voice Cloud API", version=application_version)
app.include_router(pairing_router)
app.include_router(admin_console_router)
app.include_router(evcp_router)
app.include_router(alexa_router)


@app.get("/health", response_model=HealthResponse, tags=["operations"])
async def health() -> HealthResponse:
    """Return process liveness without depending on external services."""

    return HealthResponse(version=application_version)
