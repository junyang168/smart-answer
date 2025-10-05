from __future__ import annotations

from fastapi import FastAPI

from .router import router

app = FastAPI(title="Full Article Admin Service")
app.include_router(router)


@app.get("/healthz")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}
