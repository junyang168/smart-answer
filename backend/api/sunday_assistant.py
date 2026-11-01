from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import hmac
import os

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from backend.sunday_assistant.engine import get_assistant, upcoming_sunday
from backend.sunday_assistant.models import ChatMessage, Settings, ZONE
from backend.sunday_assistant.store import now_text


def authenticated(authorization: str | None = Header(default=None)):
    secret = os.getenv("SUNDAY_ASSISTANT_SECRET", "")
    if not secret:
        raise HTTPException(503, "主日助理尚未設定連線密鑰")
    if not authorization or not hmac.compare_digest(authorization, "Bearer " + secret):
        raise HTTPException(401, "主日助理驗證失敗")


router = APIRouter(prefix="/admin/sunday-assistant", tags=["sunday-assistant"], dependencies=[Depends(authenticated)])


@contextmanager
def available_worker(agent):
    try:
        with agent.store.work_lock(blocking=False):
            yield
    except BlockingIOError as exc:
        raise HTTPException(409, "助理正在處理另一則消息，請稍後再操作；進度會自動更新") from exc


@router.get("/state")
def state():
    agent = get_assistant()
    day = upcoming_sunday(datetime.now(ZONE).date()).isoformat()
    try:
        import httpx
        response = httpx.get(agent.bridge.url + "/status", timeout=2,
                             headers={"Authorization": "Bearer " + os.environ["SUNDAY_ASSISTANT_SECRET"]})
        response.raise_for_status()
        bridge = response.json()
    except Exception:
        bridge = {"state": "offline", "error": "WhatsApp 橋接器尚未連線"}
    worker = agent.store.get("worker")
    if worker and (datetime.now(timezone.utc) - datetime.fromisoformat(worker["at"])).total_seconds() > 180:
        worker = {**worker, "status": "stale", "error": "處理程序超過三分鐘沒有更新，請檢查是否仍在運行"}
    return {"settings": agent.store.settings().model_dump(), "messages": agent.store.messages(),
            "proposals": agent.store.proposals(), "outbox": agent.store.outbox(),
            "worker": worker, "bridge": bridge,
            "conflicts": agent.conflicts(), "sunday": day, "missing": agent.missing(day),
            "workers": list(agent.worker_map())}


@router.put("/settings")
def settings(payload: Settings):
    agent = get_assistant()
    names = set(agent.worker_map())
    if any(name not in names for values in payload.sender_workers.values() for name in values):
        raise HTTPException(422, "發送人對應的姓名必須存在於同工名單")
    agent.store.put("settings", payload.model_dump())
    return payload


@router.get("/bridge-config")
def bridge_config():
    config = get_assistant().store.settings()
    return {"enabled": config.enabled, "groups": [g for g in [config.availability_group, config.service_group] if g]}


@router.post("/ingest")
def ingest(payload: ChatMessage):
    try:
        inserted = get_assistant().store.receive(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"accepted": inserted, "message_id": payload.message_id}


@router.post("/tick")
def tick(tasks: BackgroundTasks):
    # Every message was persisted before this optional nudge. The long-running
    # worker is the normal path; a web-process restart cannot lose the queue.
    tasks.add_task(get_assistant().tick)
    return {"status": "queued"}


class Decision(BaseModel):
    action: str = Field(pattern="^(apply|dismiss|retry)$")


@router.post("/proposals/{proposal_id}")
def decide(proposal_id: int, payload: Decision):
    agent = get_assistant()
    with available_worker(agent):
        with agent.store.db() as db:
            row = db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if not row:
            raise HTTPException(404, "找不到此更新")
        if row["status"] in {"applied", "dismissed", "resolved"}:
            raise HTTPException(409, "此更新已處理")
        if row["status"] == "waiting_reply" and payload.action == "retry":
            raise HTTPException(409, "此事項正在群內追問，請直接在群內回答；若不再需要登記，可略過此事項")
        if payload.action == "apply":
            try:
                agent.apply(proposal_id, approved=True)
            except Exception as exc:
                with agent.store.db() as db:
                    db.execute("UPDATE proposals SET status='review',error=?,updated_at=? WHERE id=?",
                               (str(exc)[:1000], now_text(), proposal_id))
                raise HTTPException(409, str(exc)) from exc
            status = "done"
        elif payload.action == "dismiss":
            with agent.store.db() as db:
                dialogue = db.execute("SELECT id FROM clarifications WHERE proposal_id=? AND status='waiting'", (proposal_id,)).fetchone()
                if dialogue:
                    import json
                    message = ChatMessage.model_validate(json.loads(row["payload"])["message"])
                    agent.finish_dialogue(db, {"clarification_id": dialogue[0]}, message, "dismissed")
                    db.execute("UPDATE outbox SET status='cancelled',updated_at=? WHERE key LIKE ? AND status='queued'",
                               (now_text(), f"clarify:{dialogue[0]}:%"))
                db.execute("UPDATE proposals SET status='dismissed',updated_at=? WHERE id=?", (now_text(), proposal_id))
                db.execute("UPDATE clarifications SET status='dismissed',updated_at=? WHERE proposal_id=?", (now_text(), proposal_id))
            status = "dismissed"
        else:
            status = "queued"
            with agent.store.db() as db:
                db.execute("UPDATE clarifications SET status='dismissed',updated_at=? WHERE proposal_id=?", (now_text(), proposal_id))
                db.execute("DELETE FROM proposals WHERE id=?", (proposal_id,))
        with agent.store.db() as db:
            db.execute("UPDATE messages SET status=?,error=NULL WHERE id=?", (status, row["message_id"]))
    return {"status": status}


@router.post("/messages/{message_id}/retry")
def retry(message_id: str):
    agent = get_assistant()
    with available_worker(agent), agent.store.db() as db:
        row = db.execute("SELECT status FROM messages WHERE id=?", (message_id,)).fetchone()
        if not row:
            raise HTTPException(404, "找不到消息")
        if row[0] != "error":
            raise HTTPException(409, "只有失敗消息需要重試")
        db.execute("UPDATE messages SET status='queued',error=NULL WHERE id=?", (message_id,))
    return {"status": "queued"}


@router.get("/groups")
def groups():
    try:
        return get_assistant().bridge.request("GET", "/groups")
    except Exception as exc:
        raise HTTPException(503, "請先啟動並連接 WhatsApp 橋接器") from exc


@router.post("/connect")
def connect():
    try:
        return get_assistant().bridge.request("POST", "/connect", {})
    except Exception as exc:
        raise HTTPException(503, "請先啟動 WhatsApp 橋接器") from exc
