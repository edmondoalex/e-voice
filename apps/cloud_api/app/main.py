"""FastAPI application entry point."""

from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI

from .evcp import router as evcp_router
from .schemas import HealthResponse

try:
    application_version = version("ekonex-voice")
except PackageNotFoundError:
    application_version = "0.1.0"

app = FastAPI(title="Ekonex Voice Cloud API", version=application_version)
app.include_router(evcp_router)


@app.get("/health", response_model=HealthResponse, tags=["operations"])
async def health() -> HealthResponse:
    """Return process liveness without depending on external services."""

    return HealthResponse(version=application_version)
