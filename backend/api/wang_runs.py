"""Claim and CVP management runs, recorded human decisions and reviewer
calibrations, for the operations pages (#453).

Engineering reads these to see what ran, what paused and why. They are not
the to-do list: a paused Claim action whose reason is `human_required` stays
paused after the owner has answered its question in conversation (the answer
is recorded in `human_source_decisions`, the action is not resumed), so it
cannot tell anyone that a decision is still owed.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from fastapi import APIRouter

from .canonical_repository.postgres_store import PostgresKnowledgeStore, database_url_from_env

router = APIRouter(prefix="/admin/wang/runs", tags=["wang-admin"])

LIMIT = 25


def _store() -> PostgresKnowledgeStore:
    return PostgresKnowledgeStore(database_url_from_env())


def cvp_actions(cursor: Any) -> list[dict[str, Any]]:
    cursor.execute("""
        SELECT run_id, started_at, metadata FROM wang_knowledge.pipeline_runs
        WHERE metadata->>'kind' = 'cvp_management_action' ORDER BY started_at DESC LIMIT %s""", (LIMIT,))
    rows = []
    for run_id, started_at, metadata in cursor.fetchall():
        units = metadata.get("units") or {}
        rows.append({
            "run_id": run_id,
            "action_id": metadata.get("action_id") or (metadata.get("request") or {}).get("action_id"),
            "started_at": started_at.isoformat() if started_at else None,
            "status": (metadata.get("result") or {}).get("status"),
            "units": dict(Counter(str(unit.get("status")) for unit in units.values())),
            "open": [
                {"unit": key, "status": unit.get("status"), "detail": str(unit.get("detail") or "")[:300]}
                for key, unit in units.items() if unit.get("status") not in ("completed", "superseded_input")
            ],
        })
    return rows


def claim_actions(cursor: Any) -> list[dict[str, Any]]:
    cursor.execute("""
        SELECT run_id, started_at, metadata FROM wang_knowledge.pipeline_runs
        WHERE metadata->>'kind' = 'claim_management_action' ORDER BY started_at DESC LIMIT %s""", (LIMIT,))
    return [
        {
            "run_id": run_id,
            "action_id": (metadata.get("request") or {}).get("action_id"),
            "operation": (metadata.get("request") or {}).get("operation"),
            "claims": len(metadata.get("claim_ids") or []),
            "started_at": started_at.isoformat() if started_at else None,
            "status": (metadata.get("result") or {}).get("status"),
            "reason": (metadata.get("result") or {}).get("reason"),
            "continued_by": metadata.get("continued_by"),
        }
        for run_id, started_at, metadata in cursor.fetchall()
    ]


def human_decisions(cursor: Any) -> list[dict[str, Any]]:
    cursor.execute("""
        SELECT d.decision_id, d.source_id, d.entry, d.created_at, d.superseded_by, s.payload->>'title'
        FROM wang_knowledge.human_source_decisions d
        LEFT JOIN wang_knowledge.objects s ON s.collection = 'source_documents' AND s.object_id = d.source_id
        ORDER BY d.created_at DESC LIMIT %s""", (LIMIT,))
    rows = []
    for decision_id, source_id, entry, created_at, superseded_by, title in cursor.fetchall():
        record = (entry or {}).get("record") or {}
        answers = record.get("answers") or []
        text = answers[0].get("answer") if answers else (
            f"{record.get('before')} → {record.get('after')}" if record.get("after") else "")
        rows.append({
            "decision_id": decision_id,
            "source_id": source_id,
            "source_title": title or source_id,
            "kind": (entry or {}).get("kind"),
            "text": str(text or "")[:400],
            "created_at": created_at.isoformat() if created_at else None,
            "superseded": bool(superseded_by),
        })
    return rows


def calibrations() -> list[dict[str, Any]]:
    from backend.pipeline.draft_first_calibration import CalibrationRequired, records_path

    try:
        path = records_path()
    except CalibrationRequired:
        return []
    entries = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    return [
        {
            "calibrated_at": entry.get("calibrated_at"),
            "model": entry.get("model"),
            "prompt_sha256": entry.get("prompt_sha256"),
            "key_id": entry.get("key_id"),
            "agrees": entry.get("agrees"),
            "missed": [row.get("id") for row in entry.get("rows") or [] if row.get("result") not in ("hit", "manual")],
        }
        for entry in reversed(entries)
    ]


def cvp_disputes(store: PostgresKnowledgeStore | None = None) -> list[dict[str, Any]]:
    """CVP units a live action stopped for a person: the to-do list's CVP source."""

    store = store or _store()
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute("""
            SELECT run_id, started_at, metadata->>'action_id', u.key, u.value->>'detail'
            FROM wang_knowledge.pipeline_runs, jsonb_each(metadata->'units') u
            WHERE metadata->>'kind' = 'cvp_management_action'
              AND coalesce(metadata->'result'->>'status', '') NOT IN ('superseded', 'cancelled')
              AND u.value->>'status' = 'human_required'
            ORDER BY started_at DESC""")
        return [
            {"run_id": run_id, "started_at": started_at.isoformat() if started_at else None,
             "action_id": action_id, "unit": unit, "detail": detail}
            for run_id, started_at, action_id, unit, detail in cursor.fetchall()
        ]


@router.get("/management")
def management() -> dict[str, Any]:
    with _store().connect() as conn, conn.cursor() as cursor:
        return {
            "cvp_actions": cvp_actions(cursor),
            "claim_actions": claim_actions(cursor),
            "human_decisions": human_decisions(cursor),
            "calibrations": calibrations(),
        }
