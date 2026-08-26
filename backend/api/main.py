from __future__ import annotations

import json
from pathlib import Path

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
    micro_sermon_admin_router,
    micro_sermon_public_router,
)
from .slides import router as slides_router
from .scripture import router as scripture_router
from .sc_api import router as sc_api_router
from .sc_api.rag import router as rag_router
from .sermon_converter_router import router as sermon_converter_router
from .lecture_router import router as lecture_router, public_router as lecture_public_router
from .sermon_search.router import router as sermon_search_router, compat_router as sermon_search_compat_router
from .canonical_repository.router import (
    router as canonical_repository_router,
    admin_router as canonical_repository_admin_router,
)
from .argument_layer import router as argument_layer_router
from .extraction_health import router as extraction_health_router
from .library_audit import router as library_audit_router
from .source_coverage import router as source_coverage_router
from .public_wang_articles import router as public_wang_articles_router
from .matthew_exposition_progress import router as matthew_exposition_progress_router
from .wang_operations import router as wang_operations_router
from .wang_articles import router as wang_articles_router
from .viewpoint_admin import router as viewpoint_admin_router

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
app.include_router(micro_sermon_admin_router)
app.include_router(micro_sermon_public_router)
app.include_router(slides_router)
app.include_router(scripture_router)
app.include_router(sc_api_router)
app.include_router(rag_router)
app.include_router(sermon_converter_router)
app.include_router(lecture_router)
app.include_router(lecture_public_router)
app.include_router(sermon_search_router)
app.include_router(sermon_search_compat_router)
app.include_router(canonical_repository_router)
app.include_router(canonical_repository_admin_router)
app.include_router(argument_layer_router)
app.include_router(extraction_health_router)
app.include_router(library_audit_router)
app.include_router(source_coverage_router)
app.include_router(public_wang_articles_router)
app.include_router(matthew_exposition_progress_router)
app.include_router(wang_operations_router)
app.include_router(wang_articles_router)
app.include_router(viewpoint_admin_router)


def _release_identity() -> dict[str, str]:
    """Read the release marker the deployment wrote beside this code.

    `{"status": "ok"}` proves something is answering, not that the thing
    answering is the build anyone intended. Deciding whether a deploy had
    landed meant comparing process start times, a checkout's HEAD and lsof
    output to reach an indirect answer -- for a question that should be one
    request. The deploy writes `release.json` into the release it builds, so
    the running service can simply say which commit it is.

    Absent or unreadable means this is not a deployed release (a dev run, or a
    tree predating the marker); the health check still passes, because liveness
    does not depend on knowing the commit.
    """

    marker = Path(__file__).resolve().parents[2] / "release.json"
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {
        key: str(payload[key])
        for key in ("release", "deployed_at")
        if isinstance(payload, dict) and payload.get(key)
    }


@app.get("/healthz")
def healthcheck() -> dict[str, str]:
    return {"status": "ok", **_release_identity()}
