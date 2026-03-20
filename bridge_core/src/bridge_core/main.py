"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

try:
    from ui_web import STATIC_DIR
except ImportError:
    STATIC_DIR = None  # type: ignore

from bridge_core.api import (
    events_router,
    health_router,
    sessions_router,
    sources_router,
    targets_router,
)

app = FastAPI(
    title="IKEA SYMFONISK Bridge",
    description="Local bridge platform for IKEA SYMFONISK speakers",
    version="0.1.0",
)

app.include_router(health_router)
app.include_router(sources_router)
app.include_router(targets_router)
app.include_router(sessions_router)
app.include_router(events_router)

# Try to mount the UI
if STATIC_DIR is not None and STATIC_DIR.exists() and (STATIC_DIR / "index.html").exists():
    # Serve the index.html on the root path
    @app.get("/")
    async def root_ui() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    # Mount the rest of the static files
    app.mount("/", StaticFiles(directory=str(STATIC_DIR)), name="ui")
else:
    @app.get("/")
    async def root() -> dict[str, str]:
        return {
            "service": "ikea-symfonisk-gateway",
            "version": "0.1.0",
            "status": "running",
            "ui": "not installed",
        }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8732)
