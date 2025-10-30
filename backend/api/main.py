from __future__ import annotations

from fastapi import FastAPI

from .router import (
    router,
    fellowship_router,
    surmon_series_router,
    sunday_service_router,
    sunday_workers_router,
    sunday_songs_router,
    webcast_router,
    webcast_admin_router,
)
from .slides import router as slides_router
from .scripture import router as scripture_router
from .sc_api import router as sc_api_router

app = FastAPI(title="Full Article Admin Service")
app.include_router(router)
app.include_router(fellowship_router)
app.include_router(surmon_series_router)
app.include_router(sunday_service_router)
app.include_router(sunday_workers_router)
app.include_router(sunday_songs_router)
app.include_router(webcast_router)
app.include_router(webcast_admin_router)
app.include_router(slides_router)
app.include_router(scripture_router)
app.include_router(sc_api_router)


@app.get("/healthz")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}
