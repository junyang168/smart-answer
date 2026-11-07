"""Editor-accessible read/control plane. GET never starts or resumes processing."""

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
    async def editor(request: Request):
        actor = authenticated_actor(request, await request.body())
        try:
            config = json.loads((Path(root) / "config/config.json").read_text())
        except (OSError, ValueError):
            raise HTTPException(503, "無法讀取角色設定")
        if not user_roles(config, actor).intersection(
            {"admin", "editor", "beta_tester"}
        ):
            raise HTTPException(403, "需要編輯權限")
        return actor

    router = APIRouter(prefix="/media_batches", dependencies=[Depends(editor)])

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
            if result["selected"]:
                from backend.pipeline.media_batch.preflight_report import load

                report = load(root, result["selected"]["id"])
                if report:
                    result["preflight"] = report["summary"]
            return result
        except KeyError:
            raise HTTPException(404, "找不到批次")
        except (ValueError, OSError):
            raise HTTPException(503, "批次服務尚未設定或無法讀取")
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Media batch read failed")
            raise HTTPException(503, "暫時無法讀取批次台帳")

    @router.get("/{batch}/preflight")
    def preflight(batch: str):
        from backend.pipeline.media_batch.preflight_report import load

        try:
            report = load(root, batch)
        except (OSError, ValueError, KeyError):
            raise HTTPException(409, "查重對照表版本不一致，請重新產生")
        if not report:
            raise HTTPException(404, "此批次尚無查重對照表")
        return Response(
            report["html"],
            media_type="text/html",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.get("/{batch}/rough/{recording}")
    def rough_text(batch: str, recording: str):
        from backend.pipeline.media_batch.rough import render
        from backend.pipeline.media_batch.runtime import file_sha

        with store_factory().connection() as c:
            task = c.execute(
                "SELECT t.payload,t.result FROM media_ingest.tasks t JOIN media_ingest.batch_tasks b ON b.task=t.id WHERE b.batch=%s AND t.recording=%s AND t.stage='rough_asr' AND t.state='complete'",
                (batch, recording),
            ).fetchone()
        if not task:
            raise HTTPException(404, "粗逐字稿尚未完成")
        out = Path(task["payload"]["out"])
        if file_sha(out / "rough.json") != task["result"]["rough_sha256"]:
            raise HTTPException(409, "粗逐字稿版本不一致")
        return Response(
            render(out),
            media_type="text/html",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
                "X-Content-Type-Options": "nosniff",
            },
        )

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
            pattern=r"^(all|actionable|rough_complete|waiting_download|ready_to_plan|needs_assembly|needs_scope_review|pending|running|complete|existing_transcript|reused|duplicate|excluded|failed|needs_resolution|unknown_outcome|unverified|interrupted)$",
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
