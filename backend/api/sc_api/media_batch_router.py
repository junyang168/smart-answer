"""Admin-only read/control plane. GET never starts or resumes processing."""

import json
import os
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from backend.pipeline.media_batch.store import Store, Conflict
from backend.pipeline.media_batch.runtime import capacity_status
from .transcript_review_router import authenticated_actor
from .roles import user_roles


class Control(BaseModel):
    operation_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    revision: int = Field(ge=1)
    action: str = Field(pattern=r"^(pause|resume|retry)$")


def create_media_batch_router(root, store_factory=Store):
    async def admin(request: Request):
        actor = authenticated_actor(request, await request.body())
        try:
            config = json.loads((Path(root) / "config/config.json").read_text())
        except (OSError, ValueError):
            raise HTTPException(503, "無法讀取角色設定")
        if "admin" not in user_roles(config, actor):
            raise HTTPException(403, "需要管理員權限")
        return actor

    router = APIRouter(prefix="/media_batches", dependencies=[Depends(admin)])

    @router.get("")
    def get(
        response: Response,
        batch: str | None = None,
        offset: int = Query(0, ge=0),
        limit: int = Query(30, ge=1, le=100),
    ):
        try:
            response.headers["Cache-Control"] = "private, no-store"
            store = store_factory()
            result = store.snapshot(batch, offset, limit)
            if result["selected"]:
                with store.connection() as c:
                    config = c.execute(
                        "SELECT config FROM media_ingest.batches WHERE id=%s",
                        (result["selected"]["id"],),
                    ).fetchone()["config"]
                result["capacity"] = capacity_status(config["capacity_root"])
                from backend.pipeline.media_batch.disk import status

                try:
                    result["disk"] = status(store, config)
                except (OSError, ValueError):
                    result["disk"] = {
                        name: dict(unavailable=True)
                        for name in ("artifact_root", "media_root")
                    }
            return result
        except KeyError:
            raise HTTPException(404, "找不到批次")
        except (ValueError, OSError):
            raise HTTPException(503, "批次服務尚未設定或無法讀取")
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Media batch read failed")
            raise HTTPException(503, "暫時無法讀取批次台帳")

    @router.post("/{batch}/control")
    def control(batch: str, body: Control):
        try:
            return dict(
                revision=store_factory().control(
                    batch, body.operation_id, body.revision, body.action
                )
            )
        except KeyError:
            raise HTTPException(404, "找不到批次")
        except Conflict as e:
            raise HTTPException(409, str(e))
        except Exception:
            raise HTTPException(503, "控制指令未確認，請重新讀取狀態")

    @router.get("/{batch}/files")
    def files(
        batch: str,
        response: Response,
        offset: int = Query(0, ge=0),
        limit: int = Query(30, ge=1, le=100),
        search: str = Query("", max_length=200),
        state: str = Query(
            "all",
            pattern=r"^(all|actionable|waiting_download|ready_to_plan|needs_assembly|needs_scope_review|pending|running|complete|existing_transcript|reused|duplicate|excluded|failed|needs_resolution|unknown_outcome|unverified|interrupted)$",
        ),
    ):
        from backend.pipeline.media_batch.files import snapshot

        response.headers["Cache-Control"] = "private, no-store"
        try:
            return snapshot(store_factory(), batch, offset, limit, search, state)
        except KeyError:
            raise HTTPException(404, "找不到批次")
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Media file inventory read failed")
            raise HTTPException(503, "暫時無法讀取檔案清單")

    return router
