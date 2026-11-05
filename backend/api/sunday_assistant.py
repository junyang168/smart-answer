from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import hmac
import os

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse

from backend.sunday_assistant.engine import get_assistant, upcoming_sunday
from backend.sunday_assistant.models import ChatMessage, Settings, ZONE
from backend.sunday_assistant.store import now_text
from backend.sunday_service_skeletons import skeleton_status
from backend.sunday_assistant.admin_monitor import AdminMonitor, monitor_state as admin_monitor_state
from backend.sunday_assistant.member_identity import Roster, save_roster, audit, waiting_identity_states, recheck_waiting


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
    fellowship = []
    if agent.store.settings().fellowship_enabled:
        from backend.sunday_assistant.fellowship import Fellowship
        fellowship = Fellowship(agent).state()
    return {"fellowship": fellowship, "settings": agent.store.settings().model_dump(), "messages": [row for row in agent.store.messages() if row["payload"]["group_id"] != agent.store.settings().admin_group],
            "proposals": agent.store.proposals(), "outbox": agent.store.outbox(),
            "worker": worker, "bridge": bridge,
            "conflicts": agent.conflicts(), "sunday": day, "missing": agent.missing(day),
            "workers": list(agent.worker_map()), "identities": agent.store.identities(),
            "skeletons": skeleton_status(agent.repository, datetime.now(ZONE).date()),
            "identity_audit": audit(agent.store, agent.worker_map()),
            "admin_monitor": admin_monitor_state(agent.store.root),
            "ppt_readiness": agent.store.get('ppt_readiness'), "ppt_jobs": ppt_jobs(agent)}


def ppt_jobs(agent):
    with agent.store.db() as db:
        return [dict(row) for row in db.execute("SELECT day,version,status,error,updated_at FROM ppt_jobs ORDER BY updated_at DESC LIMIT 20")]


@router.get("/ppt-ready/{day}/{version}")
def download_ready_ppt(day: str, version: str):
    from backend.sunday_assistant.ppt_ready import PptReady
    try:
        path = PptReady(get_assistant()).downloadable(day,version)
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return FileResponse(path, filename=f"主日崇拜-{day}-{version[:8]}.pptx",
                        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                        headers={"Cache-Control": "no-store"})


@router.post("/ppt-retry/{day}")
def retry_ready_ppt(day: str):
    from backend.sunday_assistant.ppt_ready import PptReady
    agent = get_assistant()
    with available_worker(agent):
        PptReady(agent).retry(day)
    return {"status": "queued"}


@router.put("/settings")
def settings(payload: Settings):
    agent = get_assistant()
    names = set(agent.worker_map())
    if any(name not in names for values in payload.sender_workers.values() for name in values):
        raise HTTPException(422, "發送人對應的姓名必須存在於同工名單")
    if any(name not in names for name in payload.worship_rotation):
        raise HTTPException(422, "領詩輪值姓名必須存在於同工名單")
    if any(name not in names for pair in payload.households for name in pair):
        raise HTTPException(422, "夫妻對應的姓名必須存在於同工名單")
    if any(target not in names or (alias in names and alias != target) for alias, target in payload.worker_aliases.items()):
        raise HTTPException(422, "姓名別名需對應現有同工，不能改指向另一位現有同工")
    if payload.fellowship_enabled:
        if payload.fellowship_since is None:
            payload.fellowship_since = datetime.now(timezone.utc)
        if payload.fellowship_start_date is None:
            payload.fellowship_start_date = payload.fellowship_since.astimezone(ZONE).date()
    before = waiting_identity_states(agent.store)
    agent.store.put("settings", payload.model_dump())
    recheck_waiting(agent.store, before)
    if payload.fellowship_enabled:
        with agent.store.db() as db:
            db.execute("UPDATE messages SET status='queued' WHERE status='fellowship_paused' AND json_extract(payload,'$.group_id')=?",(payload.admin_group,))
    return payload


@router.post("/admin-help-test")
def admin_help_test():
    agent = get_assistant()
    try:
        key = AdminMonitor(agent.store.root).queue_test(agent.store.settings().model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"status": "queued", "key": key}


@router.post("/identity-roster")
def identity_roster(payload: Roster):
    try:
        save_roster(get_assistant().store, payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"accepted": True}


@router.post("/identity-audit")
def refresh_identity_audit():
    agent = get_assistant()
    try:
        payload = Roster.model_validate(agent.bridge.request("GET", "/members"))
        save_roster(agent.store, payload)
    except Exception as exc:
        raise HTTPException(503, "無法完成群成員核對，既有對應保留；請查看橋接狀態") from exc
    return audit(agent.store, agent.worker_map())


@router.get("/bridge-config")
def bridge_config():
    config = get_assistant().store.settings()
    from backend.sunday_assistant.gmail_source import configuration
    try:
        mail = configuration()
    except (ValueError, OSError):
        mail = None  # An email setup failure must not disconnect WhatsApp.
    return {"allow_speaker_followup": bool(config.enabled and mail and mail.get('enabled') and mail.get('saturday_followup', True)),
            "allow_fellowship": bool(config.enabled and config.fellowship_enabled),
            "ingest_groups": sorted(config.ingest_group_ids),
            "fellowship_since": config.fellowship_since.timestamp() if config.fellowship_since else None,
            "enabled": config.enabled, "groups": sorted(config.source_group_ids), "availability_group": config.availability_group,
            "allow_questions": config.group_questions_enabled, "allow_reminders": config.reminders_enabled,
            "allow_admin_help": config.admin_help_enabled, "admin_group": config.admin_group,
            "allow_ppt_ready": config.enabled and config.auto_apply and config.auto_generate_ppt}


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
            agent.store.restart_execution(row["message_id"])
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
        agent.store.restart_execution(message_id)
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


@router.get("/gmail/state")
def gmail_state():
    from backend.sunday_assistant.gmail_inbox import GmailInbox
    return GmailInbox(get_assistant()).state()


class MailDecision(BaseModel):
    action: str = Field(pattern="^(apply|dismiss)$")
    date: str | None = None
    speaker: str | None = None


@router.post("/gmail/files/{job_id}")
def gmail_decide(job_id: str, payload: MailDecision):
    from backend.sunday_assistant.gmail_inbox import GmailInbox
    try:
        GmailInbox(get_assistant()).decide(job_id, payload.action, payload.date, payload.speaker)
    except BlockingIOError as exc:
        raise HTTPException(409, "Gmail 正在收存附件，請稍後重試") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "saved"}


@router.post("/gmail/messages/{key}/retry")
def gmail_retry(key: str):
    from backend.sunday_assistant.gmail_inbox import GmailInbox
    try:
        GmailInbox(get_assistant()).retry_message(key)
    except (ValueError, BlockingIOError) as exc:
        raise HTTPException(409, str(exc) or "Gmail 正在處理，請稍後重試") from exc
    return {"status": "queued"}
