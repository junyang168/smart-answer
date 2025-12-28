from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .router import (
    router as api_router,
    fellowship_router,
    surmon_series_router,
    sunday_service_router,
    sunday_workers_router,
    sunday_songs_router,
    webcast_router,
    webcast_admin_router,
    email_router,
)
from .slides import router as slides_router
from .scripture import router as scripture_router
from .sc_api import router as sc_api_router
from .sc_api.rag import router as rag_router
from .sermon_converter_router import router as sermon_converter_router
from .lecture_router import router as lecture_router

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust this in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(fellowship_router)
app.include_router(surmon_series_router)
app.include_router(sunday_service_router)
app.include_router(sunday_workers_router)
app.include_router(sunday_songs_router)
app.include_router(webcast_router)
app.include_router(webcast_admin_router)
app.include_router(email_router)
app.include_router(slides_router)
app.include_router(scripture_router)
app.include_router(sc_api_router)
app.include_router(rag_router)
app.include_router(sermon_converter_router)
app.include_router(lecture_router)


@app.get("/healthz")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}
