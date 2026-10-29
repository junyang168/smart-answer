"""Authenticated review routes shared by the application and isolated local trials."""

import json
import hashlib
import hmac
import os
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from .transcript_review import ReviewStore, ReviewError


def authenticated_actor(request: Request, body: bytes = b""):
    secret = os.getenv("TRANSCRIPT_REVIEW_ACTION_SECRET")
    if not secret:
        raise HTTPException(503, "圖文審核身份驗證尚未設定")
    actor = request.headers.get("x-review-actor", "")
    timestamp = request.headers.get("x-review-time", "")
    try:
        valid = abs(time.time() - int(timestamp)) < 90
    except ValueError:
        valid = False
    path = request.scope.get("raw_path", request.url.path.encode()).decode("ascii")
    query = request.scope.get("query_string", b"").decode("ascii")
    if query:
        path += "?" + query
    message = "\n".join(
        (request.method, path, actor, timestamp, hashlib.sha256(body).hexdigest())
    )
    expected = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    if (
        not actor
        or not valid
        or not hmac.compare_digest(
            expected, request.headers.get("x-review-signature", "")
        )
    ):
        raise HTTPException(401, "請登入後再操作")
    return actor


def create_review_router(root: Path):
    router = APIRouter()
    store = ReviewStore(root)

    async def payload_from(request):
        try:
            return envelope(await request.json())
        except ValueError as exc:
            raise HTTPException(400, "無效 JSON 修改封包") from exc

    def envelope(payload):
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("version"), str)
            or not isinstance(payload.get("operation_id"), str)
        ):
            raise HTTPException(400, "無效修改封包")
        if "operations" in payload and (
            not isinstance(payload["operations"], list)
            or any(
                not isinstance(op, dict)
                or not isinstance(op.get("id", ""), str)
                or not isinstance(op.get("kind"), str)
                for op in payload["operations"]
            )
        ):
            raise HTTPException(400, "無效修改內容")
        return payload

    def call(fn):
        try:
            return fn()
        except ReviewError as error:
            raise HTTPException(error.status, error.message) from error

    @router.get("/review_mode/{item}")
    def mode(item: str):
        # Public capability probe only; no identity, source paths or draft text.
        def result():
            store.meta(item)
            if (
                not store.folder(item).exists()
                and not store.candidate_path(item).exists()
            ):
                return {"mode": "legacy"}
            with store.lock(item):
                return {
                    "mode": "review"
                    if store.enabled(item)
                    else "candidate"
                    if store.candidate_path(item).exists()
                    else "legacy"
                }

        return call(result)

    @router.get("/review_candidate/{item}")
    def candidate(item: str, request: Request):
        return call(lambda: store.candidate(authenticated_actor(request), item))

    @router.post("/review_candidate/{item}")
    async def candidate_command(item: str, request: Request):
        raw = await request.body()
        actor = authenticated_actor(request, raw)
        payload = await payload_from(request)
        if request.headers.get("if-match", "").strip('"') != payload.get("version"):
            raise HTTPException(412, "缺少正確版本條件")
        return call(lambda: store.candidate_command(actor, item, payload))

    @router.get("/review_document/{item}")
    def document(item: str, request: Request):
        actor = authenticated_actor(request)
        doc = call(lambda: store.document(actor, item))
        access = {
            k: v
            for k, v in doc.items()
            if k not in ("script", "context", "version", "paragraph_shas", "group_shas")
        }
        return JSONResponse(
            {k: doc[k] for k in ("script", "context", "paragraph_shas", "group_shas")},
            headers={
                "ETag": '"' + doc["version"] + '"',
                "X-Review-Access": json.dumps(access, ensure_ascii=True),
                "Cache-Control": "no-store",
            },
        )

    @router.patch("/review_document/{item}")
    async def update(item: str, request: Request):
        raw = await request.body()
        actor = authenticated_actor(request, raw)
        payload = await payload_from(request)
        if request.headers.get("if-match", "").strip('"') != payload.get("version"):
            raise HTTPException(412, "缺少正確版本條件")
        return call(lambda: store.mutate(actor, item, payload))

    @router.post("/review_document/{item}/{action}")
    async def command(item: str, action: str, request: Request):
        raw = await request.body()
        actor = authenticated_actor(request, raw)
        payload = await payload_from(request)
        if request.headers.get("if-match", "").strip('"') != payload.get("version"):
            raise HTTPException(412, "缺少正確版本條件")
        if action == "claim":
            return call(lambda: store.claim(actor, item, payload))
        if action == "publish":
            return call(lambda: store.publish(actor, item, payload))
        raise HTTPException(404, "不支援的操作")

    @router.get("/review_document/{item}/baseline")
    def baseline(item: str, request: Request):
        def result():
            from .transcript_review import read

            with store.lock(item):
                store.require(authenticated_actor(request), item)
                store.load(item)
                return read(root / "script_patched" / f"{item}.json", [])

        return call(result)

    @router.get("/review_document/{item}/history")
    def history(item: str, request: Request, before: int = 10**12):
        return call(
            lambda: store.history(authenticated_actor(request), item, before=before)
        )

    @router.get("/review_document/{item}/revisions/{revision}")
    def revision(item: str, revision: int, request: Request):
        return call(
            lambda: store.revision(authenticated_actor(request), item, revision)
        )

    @router.get("/review_document/{item}/operations/{operation_id}")
    def operation(item: str, operation_id: str, request: Request):
        actor = authenticated_actor(request)

        def result():
            with store.lock(item):
                store.require(actor, item)
                return store.receipt(item, operation_id, actor)

        return call(result)

    @router.get("/review_document/{item}/assets/{asset_id}")
    def asset(item: str, asset_id: str, request: Request):
        path = call(
            lambda: store.asset_path(item, asset_id, actor=authenticated_actor(request))
        )
        return FileResponse(
            path, media_type="image/png", headers={"Cache-Control": "private, no-store"}
        )

    @router.get("/review_document/{item}/media")
    def media(item: str, request: Request):
        actor = authenticated_actor(request)

        def path():
            with store.lock(item):
                store.require(actor, item)
                store.load(item)
                path = store.folder(item) / "source.mp4"
                if not path.exists():
                    raise ReviewError(404, "此講道沒有本機影片")
                return path

        return FileResponse(
            call(path),
            media_type="video/mp4",
            headers={"Cache-Control": "private, no-store"},
        )

    @router.get("/review_public/{item}/assets/{asset_id}")
    def public_asset(item: str, asset_id: str):
        return FileResponse(
            call(lambda: store.asset_path(item, asset_id, public=True)),
            media_type="image/png",
        )

    @router.get("/review_public/{item}")
    def public_document(item: str):
        def result():
            store.meta(item)
            with store.lock(item):
                pub, snapshot = store.published_snapshot(item)
                # A stable public snapshot; no uncommitted draft or internal actor log.
                c = snapshot["context"]
                return {
                    "metadata": pub["metadata"],
                    "script": snapshot["script"],
                    "groups": c["groups"],
                    "visual_assets": {
                        k: {x: a[x] for x in ("visible_text", "image_sha256")}
                        for k, a in c["visual_assets"].items()
                    },
                    "occurrences": c["occurrences"],
                    "paragraph_bindings": c["paragraph_bindings"],
                }

        return call(result)

    return router
