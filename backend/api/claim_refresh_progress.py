"""Read-only progress of explicitly registered Claim refresh campaigns.

The frozen plan supplies the denominator, SQL supplies action outcomes, and
local worker/slot locks supply liveness. A historical pass is never presented
as a new current qualification. No model calls or qualification rebuilds here.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException

from .canonical_repository.postgres_store import PostgresKnowledgeStore, sha256_json

router = APIRouter(prefix="/admin/wang/claim-refresh", tags=["wang-admin"])


def read(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def timestamp(path: Path):
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


@lru_cache(maxsize=16)
def frozen_plan(path: str, mtime: int, size: int, digest: str):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("已登記的批次清單版本不符，需 Agent 核實")
    plan = json.loads(raw)
    ids = [cid for s in plan["sources"].values() for g in s["groups"] for cid in g["members"]]
    if len(set(ids)) != len(ids) or len(ids) != plan["original_count"]:
        raise ValueError("批次原始 Claim 清單有遺漏或重複")
    return plan


def held(path: Path) -> bool:
    """Probe existing locks; never create files or release another process's lock."""
    try:
        with path.open("rb") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle, fcntl.LOCK_UN)
    except FileNotFoundError:
        pass
    return False


def live_actions(root: Path, actions) -> set[str]:
    # execute_claim_management holds this exact per-action lock through
    # cached-answer validation, models, commit and readback, including native
    # recovery outside batch. Old PIDs and stale status files are irrelevant.
    workers = {a["action_id"] for a in actions if a.get("execution_root")
               and held(Path(a["execution_root"]) / "worker.lock")}
    # A still-running inherited transport also matters after its parent dies.
    return workers | active_model_actions(root, actions)


def sessions(root: Path):
    config = read(root / "capacity.json", {})
    capacity = Path(config["root"]) if config.get("root") else None
    result = {}
    for role in ("gpt", "opus"):
        active = 0
        if capacity:
            for i in range(2):
                journal = read(capacity / role / f"slot-{i}.json", {})
                transport = Path(journal["artifacts"]) if journal.get("artifacts") else None
                if (journal.get("active") and held(capacity / role / f"slot-{i}.lock")
                        and transport and not (transport / "exit.json").exists()):
                    active += 1
        result[role] = {"active": active, "limit": 2, "known": capacity is not None}
    return result


def active_model_actions(root: Path, actions):
    """Native recovery can use the same slots without a batch subprocess."""
    config = read(root / "capacity.json", {})
    if not config.get("root"):
        return set()
    capacity = Path(config["root"])
    active = set()
    for role in ("gpt", "opus"):
        for i in range(2):
            journal = read(capacity / role / f"slot-{i}.json", {})
            if not journal.get("active") or not journal.get("artifacts") or not held(capacity / role / f"slot-{i}.lock"):
                continue
            transport = Path(journal["artifacts"])
            if (transport / "exit.json").exists():
                continue
            for action in actions:
                if action.get("execution_root") and transport.is_relative_to(Path(action["execution_root"])):
                    active.add(action["action_id"])
    return active


def action_rows(store, ids):
    if not ids:
        return []
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT run_id, started_at, heartbeat_at,
            jsonb_build_object('action_id',metadata->'request'->>'action_id',
              'targets',metadata->'request'->'target_keys',
              'sources',metadata->'request'->'source_bindings',
              'result',metadata->'result','continued_by',metadata->'continued_by',
              'created_keys',metadata->'created_keys','findings',metadata->'findings',
              'execution_root',metadata->>'execution_root')
            FROM wang_knowledge.pipeline_runs WHERE run_id=ANY(%s)
              AND metadata->>'kind'='claim_management_action'
            ORDER BY started_at""", (["CMA-" + sha256_json(aid) for aid in sorted(ids)],))
        rows = []
        for rid, started, heartbeat, data in cur.fetchall():
            rows.append({**data, "run_id": rid, "started_at": started.isoformat(),
                         "updated_at": (heartbeat or started).isoformat()})
        return rows


def decisions(store, source_ids):
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT source_id,entry,created_at FROM wang_knowledge.human_source_decisions
            WHERE source_id=ANY(%s) AND superseded_by IS NULL""", (source_ids,))
        return [{"source_id": sid, "entry": entry, "at": at.isoformat()} for sid, entry, at in cur.fetchall()]


def answered(finding_id, finding, source_ids, saved_decisions):
    for row in saved_decisions:
        if row["source_id"] not in source_ids:
            continue
        record = row["entry"].get("record", {})
        for answer in record.get("answers", []):
            if answer.get("finding_id") == finding_id and answer.get("question") == finding.get("human_question"):
                return True
    return False


def project(entry, plan, actions, workers, saved_decisions):
    root = Path(entry["root"])
    current = [a for a in actions if not a.get("continued_by")]
    processed = {k.removeprefix("claims/") for a in current if a["result"].get("status") == "completed"
                 for k in a.get("targets", [])}
    created = {k.removeprefix("claims/") for a in actions for k in a.get("created_keys", []) if k.startswith("claims/")}
    sources, owner, stops = [], [], []
    for name, source in plan["sources"].items():
        sid = source["source_id"]
        href = "/admin/wang/sermons/" + quote(sid, safe="")
        groups = []
        for group in source["groups"]:
            group_root = root / name / ("group-" + group["id"])
            refs = read(group_root / "operator-prepared.json", {}).get("reference_claim_ids", [])
            finished = read(group_root / "finished.json", {})
            if finished.get("completed") and set(group["members"]) <= set(finished.get("members", [])):
                refs = [*refs, *group["members"]]
            processed.update(refs)
            members = set(group["members"])
            linked = [a for a in current if members & {k.removeprefix("claims/") for k in a.get("targets", [])}]
            running = any(a["action_id"] in workers for a in linked)
            done = len(members & processed)
            state = "processed" if done == len(members) else "running" if running else "paused" if any(a["result"].get("status") == "paused" for a in linked) else "partial" if done else "pending"
            groups.append({"id": group["id"], "label": group.get("label", ""), "total": len(members), "processed": done, "state": state})
        source_members = {cid for group in source["groups"] for cid in group["members"]}
        for action in current:
            if sid not in (action.get("sources") or {}):
                continue
            result = action["result"]
            if result.get("status") == "completed" or action["action_id"] in workers:
                continue
            action_owner = []
            if result.get("reason") == "human_required":
                for fid, finding in (action.get("findings") or {}).items():
                    if finding.get("category") != "source_uncertain" or not finding.get("human_question"):
                        continue
                    if answered(fid, finding, list(action.get("sources") or {}), saved_decisions):
                        continue
                    item = {"id": fid, "source_title": source["name"], "question": finding["human_question"],
                            "reason": finding["reason"], "citations": finding.get("citations", []), "href": href}
                    action_owner.append(item)
            owner.extend(action_owner)
            if not action_owner:
                stops.append({"source_title": source["name"], "reason": result.get("reason", "awaiting_agent"),
                              "detail": result.get("detail", ""), "action_id": action["action_id"]})
        structural = [read(root / name / ("group-" + g["id"]) / "source-ownership-preparation-stop.json") for g in source["groups"]]
        for stop in structural:
            if stop and not set(stop["members"]) <= processed:
                stops.append({"source_title": source["name"], "reason": "source_ownership", "detail": "舊 Claim 同時掛著不同來源，需 Agent 處理結構修復。", "action_id": None})
        final_path = root / name / "final-currentness.json"
        final = read(final_path, {})
        sources.append({"id": sid, "title": source["name"], "href": href, "total": len(source_members),
                        "processed": len(source_members & processed), "groups": groups,
                        "verification": {"passed": final.get("completed") is True, "at": timestamp(final_path) if final else None,
                                         "count": len(final.get("members", [])) if final.get("completed") else None}})
    originals = {cid for s in plan["sources"].values() for g in s["groups"] for cid in g["members"]}
    # Report the saved verification time explicitly: future shared writes can
    # stale a qualification without changing the Claim's own revision.
    return {"id": entry["id"], "title": entry["title"], "total": len(originals), "processed": len(originals & processed),
            "created": len(created), "groups_total": sum(len(s["groups"]) for s in sources),
            "groups_processed": sum(g["state"] == "processed" for s in sources for g in s["groups"]),
            "running_actions": len(workers & {a["action_id"] for a in current}), "sources": sources,
            "owner_items": list({i["id"]: i for i in owner}.values()), "agent_items": stops,
            "last_progress_at": max((a["updated_at"] for a in actions), default=None), "sessions": sessions(root)}


@router.get("")
def progress(campaign: str | None = None):
    try:
        if not os.environ.get("WANG_CLAIM_REFRESH_CAMPAIGNS_FILE") and not os.environ.get("DATA_BASE_DIR"):
            raise ValueError("DATA_BASE_DIR is required")
        default = Path(os.environ.get("DATA_BASE_DIR", ".")) / "wang-knowledge-platform/admin/claim-refresh-campaigns.json"
        index = read(Path(os.environ.get("WANG_CLAIM_REFRESH_CAMPAIGNS_FILE", str(default))), {"campaigns": []})
        entries = index["campaigns"]
        if not entries:
            return {"campaigns": [], "selected": None, "generated_at": datetime.now(timezone.utc).isoformat()}
        entry = next((e for e in entries if e["id"] == campaign), None) if campaign else entries[-1]
        if entry is None:
            raise HTTPException(404, "找不到這個 Claim 維護批次")
        binding = entry["plan"]
        path = Path(binding["path"])
        stat = path.stat()
        plan = frozen_plan(str(path), stat.st_mtime_ns, stat.st_size, binding["file_sha256"])
        root = Path(entry["root"])
        if not root.is_dir():
            raise ValueError("已登記的執行資料目錄目前無法讀取")
        ids = set(entry.get("seed_action_ids", [])) | set(entry.get("adopted_action_ids", []))
        for queue in (root / "agent-prepared-refill").glob("*.json"):
            ids.add(read(queue)["action_id"])
        store = PostgresKnowledgeStore()
        actions = action_rows(store, ids)
        result = project(entry, plan, actions, live_actions(root, actions),
                         decisions(store, [s["source_id"] for s in plan["sources"].values()]))
        return {"campaigns": [{"id": e["id"], "title": e["title"]} for e in entries], "selected": result,
                "generated_at": datetime.now(timezone.utc).isoformat()}
    except HTTPException:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(503, "進度資料無法核實，請稍後重試或交由 Agent 檢查") from exc
