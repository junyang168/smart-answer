"""Resolve account aliases from WhatsApp evidence, never from contact labels."""
from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field, model_validator


def normalize_id(value):
    value = re.sub(r":\d+(?=@)", "", value or "").replace("@s.whatsapp.net", "@c.us")
    return value if re.fullmatch(r"\d+@(lid|c\.us)", value) else None


class Member(BaseModel):
    id: str
    ids: list[str] = Field(min_length=1, max_length=2)
    groups: list[str] = Field(min_length=1, max_length=3)
    contact_name: str = Field(default="", max_length=300)
    push_name: str = Field(default="", max_length=300)
    is_my_contact: bool = False
    error: str | None = None

    @model_validator(mode="after")
    def validate_ids(self):
        if normalize_id(self.id) != self.id or any(normalize_id(x) != x for x in self.ids):
            raise ValueError("Member IDs must be normalized WhatsApp account IDs")
        if self.id not in self.ids or len(set(self.ids)) != len(self.ids):
            raise ValueError("Account lookup must include the queried participant")
        if len(self.ids) == 2 and {x.split("@", 1)[1] for x in self.ids} != {"lid", "c.us"}:
            raise ValueError("Only one WhatsApp LID/phone pair may link accounts")
        return self


class Roster(BaseModel):
    groups: list[str] = Field(max_length=3)
    members: list[Member] = Field(max_length=500)
    checked_at: datetime


def save_roster(store, payload):
    before = waiting_identity_states(store)
    allowed = store.settings().source_group_ids
    if set(payload.groups) != allowed or any(not set(m.groups) <= allowed for m in payload.members):
        raise ValueError("Member roster must match the currently selected source groups")
    # Retain pair history so a reassigned phone cannot silently bind a new LID.
    with store.db() as db:
        import json
        row = db.execute("SELECT value FROM kv WHERE key='whatsapp_account_pairs'").fetchone()
        pairs = {tuple(x) for x in json.loads(row[0])} if row else set()
        pairs.update(tuple(sorted(m.ids)) for m in payload.members if len(m.ids) == 2)
        if len(pairs) > 2000:
            raise ValueError("Account history requires operator review")
        db.execute("INSERT OR REPLACE INTO kv VALUES('whatsapp_account_pairs',?)", (json.dumps(sorted(pairs)),))
        db.execute("INSERT OR REPLACE INTO kv VALUES('whatsapp_member_roster',?)", (payload.model_dump_json(),))
    recheck_waiting(store, before)


def resolution(store, sender_id, workers, settings=None):
    settings = settings or store.settings()
    # Older stored/test sender keys remain exact-match only; the bridge admits
    # only real normalized WhatsApp IDs for new messages.
    sender = normalize_id(sender_id) or sender_id
    if not sender:
        return {"ids": [], "workers": [], "conflict": "invalid_account_id"}
    ids = {sender}
    pairs = store.get("whatsapp_account_pairs", [])
    while True:
        expanded = ids | {x for pair in pairs if ids.intersection(pair) for x in pair}
        if expanded == ids:
            break
        ids = expanded
    # More than one LID/phone in a component is an account reassignment or bad lookup.
    account_conflict = any(sum(x.endswith(suffix) for x in ids) > 1 for suffix in ("@lid", "@c.us"))
    configured = {name for key, values in settings.sender_workers.items() if (normalize_id(key) or key) in ids for name in values}
    remembered = {row["worker_name"] for row in store.identities() if (normalize_id(row["sender_id"]) or row["sender_id"]) in ids}
    # Explicit operator mappings override self-introduction memory, but never each other.
    names = configured or remembered
    conflict = "account_alias_conflict" if account_conflict else "multiple_workers" if len(names) > 1 else "missing_worker" if names - set(workers) else None
    return {"ids": sorted(ids), "workers": sorted(names) if not conflict else [], "conflict": conflict}


def audit(store, workers, settings=None):
    from opencc import OpenCC
    normalize = OpenCC("t2s").convert
    settings = settings or store.settings()
    roster = store.get("whatsapp_member_roster", {})
    aliases = {name: name for name in workers} | settings.worker_aliases
    rows = []
    seen = set()
    for member in roster.get("members", []):
        resolved = resolution(store, member["id"], workers, settings)
        key = tuple(resolved["ids"])
        if key in seen:
            continue
        seen.add(key)
        matching = [m for m in roster["members"] if set(m["ids"]).intersection(resolved["ids"])]
        labels = sorted({label for m in matching for label in [m.get("contact_name"), m.get("push_name")] if label})
        candidates = sorted({target for alias, target in aliases.items() if target in workers and any(normalize(alias).casefold() == normalize(label).casefold() for label in labels)})
        names = resolved["workers"]
        rows.append({"id": member["id"], "ids": resolved["ids"], "groups": sorted({g for m in matching for g in m["groups"]}),
                     "labels": labels, "saved_contact": any(m.get("is_my_contact") for m in matching),
                     "worker": names[0] if len(names) == 1 else None, "candidates": candidates,
                     "status": "conflict" if resolved["conflict"] else "confirmed" if len(names) == 1 else "needs_confirmation",
                     "reason": resolved["conflict"], "lookup_errors": sorted({m["error"] for m in matching if m.get("error")})})
    missing = sorted(name for name in workers if not any(r["worker"] == name for r in rows))
    present_ids = {id for row in rows for id in row["ids"]}
    unseen = [{"id": key, "workers": names} for key, names in settings.sender_workers.items() if (normalize_id(key) or key) not in present_ids]
    return {"checked_at": roster.get("checked_at"), "members": rows, "workers_without_account": missing, "unseen_mappings": unseen}


def waiting_identity_states(store):
    import json
    with store.db() as db:
        rows = db.execute("SELECT id,payload FROM messages WHERE status='waiting_reply' ORDER BY received_at DESC LIMIT 500").fetchall()
    names = set(name for values in store.settings().sender_workers.values() for name in values)
    names.update(row["worker_name"] for row in store.identities())
    return {row["id"]: bool(resolution(store, json.loads(row["payload"])["sender_id"], names)["workers"]) for row in rows}


def recheck_waiting(store, before):
    """Resume an internally held identity case after new operator/account evidence.

    Never replay applied parts or questions already posted externally.
    """
    import json
    from .store import now_text
    settings = store.settings()
    known = set(name for values in settings.sender_workers.values() for name in values)
    known.update(row["worker_name"] for row in store.identities())
    for message_id, was_known in before.items():
        if was_known:
            continue
        with store.db() as db:
            row = db.execute("SELECT payload,status FROM messages WHERE id=?", (message_id,)).fetchone()
            if not row or row["status"] != "waiting_reply":
                continue
            msg = json.loads(row["payload"])
            if msg.get("initial_sync") or not resolution(store, msg["sender_id"], known)["workers"]:
                continue
            if db.execute("SELECT 1 FROM proposals WHERE message_id=? AND status='applied'", (message_id,)).fetchone():
                continue
            cases = db.execute("SELECT id FROM clarifications WHERE status='waiting' AND proposal_id IN (SELECT id FROM proposals WHERE message_id=?)", (message_id,)).fetchall()
            if not cases:
                continue
            outbox = [db.execute("SELECT status FROM outbox WHERE key LIKE ?", (f"clarify:{case['id']}:%",)).fetchall() for case in cases]
            if any(not rows or any(r["status"] != "held" for r in rows) for rows in outbox):
                continue
            for case in cases:
                db.execute("UPDATE outbox SET status='cancelled',updated_at=? WHERE key LIKE ? AND status='held'", (now_text(), f"clarify:{case['id']}:%"))
                db.execute("UPDATE clarifications SET status='dismissed',updated_at=? WHERE id=?", (now_text(), case["id"]))
            db.execute("UPDATE proposals SET status='dismissed',updated_at=?,error='Identity evidence updated; queued for fresh assessment' WHERE message_id=? AND status='waiting_reply'", (now_text(), message_id))
            key = "graph_revision:" + message_id
            old = db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, json.dumps((json.loads(old[0]) if old else 0) + 1)))
            db.execute("UPDATE messages SET status='queued',error=NULL,result=NULL WHERE id=?", (message_id,))
