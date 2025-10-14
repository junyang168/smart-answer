from __future__ import annotations

from fastapi import FastAPI

from .router import router
from .slides import router as slides_router
from .scripture import router as scripture_router

app = FastAPI(title="Full Article Admin Service")
app.include_router(router)
app.include_router(slides_router)
app.include_router(scripture_router)


@app.get("/healthz")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}
