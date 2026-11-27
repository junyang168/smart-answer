"""Serve only the new review API against explicitly isolated local data."""

import os
from pathlib import Path
from fastapi import FastAPI
from backend.api.sc_api.transcript_review_router import create_review_router

app = FastAPI()
app.include_router(
    create_review_router(Path(os.environ["DATA_BASE_DIR"])), prefix="/sc_api"
)
