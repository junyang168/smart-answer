"""Build a per-source view of the argument layer straight from the authoring store.

The reviewer workbench reads `argument_graph.json`, which
`export_claim_argument_graph` parses out of a hand-authored `claim-graph.html`
covering two lectures.  The PostgreSQL store now holds twenty-five sources, so
that file can no longer show a reviewer what the corpus actually argues.

This module reads the store directly and emits one self-contained HTML page
holding every source's argument layer.  The page is a *view*: it records no
decision and writes nothing back.  Its output quotes the professor verbatim, so
it is written under DATA_BASE_DIR and never committed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from backend.api.canonical_repository.postgres_store import (
    PostgresKnowledgeStore,
    database_url_from_env,
)

# Collections that make up one source's argument layer.  `source_fragments`
# carries the verbatim quote each node is anchored to; without it a reviewer
# cannot check whether a node says what the professor said.
NODE_COLLECTIONS = (
    "evidence_steps",
    "observations",
    "claims",
    "questions",
    "position_nodes",
)

# Ids grew in layers: `DK-<source hash>-E017` is the current shape, but the
# pilot predates it (`L3-E001`, `OBS-L3-E012`, `CL-0004`), AI arbitration adds
# its own prefix (`AI-ADJ-DK-<hash>-CL012-01`), and a few records were created
# by hand against a manuscript (`ES-STEP-M16-003-1`).  Matching a hash anywhere
# in the id keeps every one of them attached to its source instead of silently
# dropping it out of the view.
DK_ID = re.compile(r"DK-([0-9a-f]{6,})")
PILOT_ID = re.compile(r"\bL\d-[A-Z]+\d+")
PILOT_CLAIM_ID = re.compile(r"^(?:AI-ADJ-)?CL-\d+")
ORDINAL = re.compile(r"(?:OBS|POS|CL|Q|E)(\d+)")
PILOT_KEY = "PILOT"
# Records that name no source: hand-built against a manuscript, or spanning
# several.  They get their own bucket so the view never hides a record.
UNSOURCED_KEY = "UNSOURCED"

# The five lanes the professor's own hand-drawn graph reads in: a move is a
# question, a piece of scripture, a step of reasoning, a conclusion, or an
# application.  Only the 143 pilot steps carry `argument_lane`; the other 877
# are placed by `step_type`, which extraction v2 always sets.
LANES = ("問題・背景", "經文證據", "解經・推理", "結論", "神學・應用")
LANE_BY_STEP_TYPE = {
    "question": 0,
    "dialogue_context": 0,
    "scripture_evidence": 1,
    "original_language": 1,
    "literary_context": 1,
    "historical_background": 1,
    "historical_cultural": 1,
    "historical_context": 1,
    "reasoning": 2,
    "qualification": 2,
    "interpretive_method": 2,
    "interpretive_judgment": 2,
    "explicit_support": 2,
    "answer": 3,
    "interpretive_conclusion": 3,
    "application": 4,
}
# Pre-v2 steps have no `step_type`; their `discourse_role` is the only signal.
LANE_BY_DISCOURSE_ROLE = {
    "question_context": 0,
    "own_reasoning": 2,
    "ai_summary": 2,
}
DEFAULT_LANE = 2


def _source_key(object_id: str) -> str:
    match = DK_ID.search(object_id)
    if match:
        return match.group(1)
    if PILOT_ID.search(object_id) or PILOT_CLAIM_ID.match(object_id):
        return PILOT_KEY
    return UNSOURCED_KEY


def _ordinal(object_id: str) -> int:
    """Position within its source, so nodes read in the order they were said."""
    matches = ORDINAL.findall(object_id)
    return int(matches[0]) if matches else 0


def _label(object_id: str) -> str:
    """Short badge for a card: the id minus the noise every id in a source shares."""
    text = DK_ID.sub("", object_id)
    text = re.sub(r"^(AI-ADJ|ES|EV|OQ|FR)-", "", text)
    return text.strip("-") or object_id


def _paragraph_rank(paragraph_key: Any) -> int | None:
    """Transcript position, so a node sits where the professor said it."""
    if paragraph_key is None:
        return None
    text = str(paragraph_key)
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else None


def _lane(payload: dict[str, Any]) -> int:
    lane = payload.get("argument_lane")
    if isinstance(lane, int) and 0 <= lane < len(LANES):
        return lane
    if isinstance(lane, str) and lane.isdigit() and 0 <= int(lane) < len(LANES):
        return int(lane)
    step_type = (payload.get("step_type") or "").strip()
    if step_type in LANE_BY_STEP_TYPE:
        return LANE_BY_STEP_TYPE[step_type]
    role = (payload.get("discourse_role") or "").strip()
    return LANE_BY_DISCOURSE_ROLE.get(role, DEFAULT_LANE)


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return []


class ArgumentLayerReader:
    def __init__(self, store: PostgresKnowledgeStore):
        self.store = store

    def _rows(self, collections: Iterable[str]) -> list[tuple[str, str, str, dict[str, Any]]]:
        names = tuple(collections)
        with self.store.connect() as conn:
            cursor = conn.execute(
                """SELECT collection, object_id, review_status, payload
                     FROM wang_knowledge.objects
                    WHERE retired_at IS NULL AND collection = ANY(%s)""",
                (list(names),),
            )
            return [(row[0], row[1], row[2], row[3]) for row in cursor.fetchall()]

    def _edges(self) -> list[dict[str, Any]]:
        with self.store.connect() as conn:
            cursor = conn.execute(
                """SELECT edge_id, from_id, to_id, relation_type, review_status, payload
                     FROM wang_knowledge.edges
                    WHERE retired_at IS NULL AND edge_collection = 'knowledge_relations'"""
            )
            return [
                {
                    "id": row[0],
                    "from": row[1],
                    "to": row[2],
                    "type": row[3],
                    "review_status": row[4],
                    "reason": (row[5] or {}).get("reason", ""),
                }
                for row in cursor.fetchall()
            ]

    def build(self) -> dict[str, Any]:
        rows = self._rows(NODE_COLLECTIONS + ("source_documents", "source_fragments"))

        fragments: dict[str, dict[str, Any]] = {}
        sources: dict[str, dict[str, Any]] = {}
        by_collection: dict[str, list[tuple[str, str, dict[str, Any]]]] = defaultdict(list)
        for collection, object_id, review_status, payload in rows:
            if collection == "source_fragments":
                fragments[object_id] = payload
            elif collection == "source_documents":
                sources[object_id] = payload
            else:
                by_collection[collection].append((object_id, review_status, payload))

        # A source document's key is the hash its records carry.  A manuscript's
        # own id carries no hash, so its fragments are what tie it to its
        # records; without this the three Matthew 16 manuscripts would show up
        # as untitled hashes.
        fragment_keys: dict[str, list[str]] = defaultdict(list)
        for fragment_id, payload in fragments.items():
            match = DK_ID.search(fragment_id)
            owner = str(payload.get("source_id") or "")
            if match and owner and match.group(1) not in fragment_keys[owner]:
                fragment_keys[owner].append(match.group(1))

        # The pilot's two lectures share one key because its thirty claims span both.
        source_meta: dict[str, dict[str, Any]] = {}
        for source_id, payload in sources.items():
            title = payload.get("title") or payload.get("transcript_id") or source_id
            if source_id in {"SRC-L3", "SRC-L4"}:
                entry = source_meta.setdefault(
                    PILOT_KEY,
                    {
                        "key": PILOT_KEY,
                        "title": "馬太福音釋經（五）第3、4講",
                        "note": "pilot：三十條共享主張跨這兩講，故合為一張圖",
                        "source_ids": [],
                        "source_type": payload.get("source_type", ""),
                    },
                )
                entry["source_ids"].append(source_id)
                continue
            match = re.match(r"^SRC-.*-([0-9a-f]{6,})$", source_id)
            keys = [match.group(1)] if match else fragment_keys.get(source_id, [source_id])
            for key in keys:
                source_meta[key] = {
                    "key": key,
                    "title": title,
                    "note": "",
                    "source_ids": [source_id],
                    "source_type": payload.get("source_type", ""),
                }

        buckets: dict[str, dict[str, Any]] = {}

        def bucket(key: str) -> dict[str, Any]:
            if key not in buckets:
                meta = source_meta.get(key) or {
                    "key": key,
                    "title": "未歸屬來源" if key == UNSOURCED_KEY else key,
                    "note": (
                        "id 未指名來源：手工建立或跨來源"
                        if key == UNSOURCED_KEY
                        else "來源文件不在庫中"
                    ),
                    "source_ids": [],
                    "source_type": "",
                }
                buckets[key] = {
                    **meta,
                    "steps": [],
                    "observations": [],
                    "claims": [],
                    "questions": [],
                    "positions": [],
                    "edges": [],
                }
            return buckets[key]

        def anchor(payload: dict[str, Any]) -> dict[str, Any]:
            ids = _as_list(payload.get("source_fragment_ids"))
            primary = payload.get("source_fragment_id")
            if primary:
                ids = [str(primary)] + [i for i in ids if i != primary]
            quotes = []
            rank = None
            for fragment_id in ids[:4]:
                fragment = fragments.get(fragment_id)
                if not fragment:
                    continue
                quotes.append(
                    {
                        "id": fragment_id,
                        "text": fragment.get("verbatim_excerpt", ""),
                        "paragraph_key": fragment.get("paragraph_key"),
                        "media_time": fragment.get("media_time"),
                        "anchor_state": fragment.get("anchor_state", ""),
                    }
                )
                if rank is None:
                    rank = _paragraph_rank(fragment.get("paragraph_key"))
            return {"quotes": quotes, "rank": rank}

        node_owner: dict[str, str] = {}

        for object_id, review_status, payload in by_collection["evidence_steps"]:
            key = _source_key(object_id)
            node_owner[object_id] = key
            found = anchor(payload)
            bucket(key)["steps"].append(
                {
                    "id": object_id,
                    "label": _label(object_id),
                    "statement": payload.get("statement", ""),
                    "step_type": payload.get("step_type") or "",
                    "discourse_role": payload.get("discourse_role") or "",
                    "speaker": payload.get("speaker") or "",
                    "stance": payload.get("stance") or "",
                    "eligibility": payload.get("support_eligibility") or "",
                    "anchor_quality": payload.get("anchor_quality") or "",
                    "review_status": review_status,
                    "scripture_refs": _as_list(payload.get("scripture_refs")),
                    "claim_ids": _as_list(payload.get("produced_claim_ids")),
                    "claim_group_label": payload.get("claim_group_label") or "",
                    "lane": _lane(payload),
                    "ordinal": _ordinal(object_id),
                    "rank": found["rank"],
                    "quotes": found["quotes"],
                }
            )

        for object_id, review_status, payload in by_collection["observations"]:
            key = _source_key(object_id)
            node_owner[object_id] = key
            found = anchor(payload)
            bucket(key)["observations"].append(
                {
                    "id": object_id,
                    "label": _label(object_id),
                    "statement": payload.get("statement", ""),
                    "observation_type": payload.get("observation_type") or "",
                    "argument_role": payload.get("argument_role") or "",
                    "review_status": review_status,
                    "scripture_refs": _as_list(payload.get("scripture_refs")),
                    "ordinal": _ordinal(object_id),
                    "rank": found["rank"],
                    "quotes": found["quotes"],
                }
            )

        for object_id, review_status, payload in by_collection["questions"]:
            key = _source_key(object_id)
            node_owner[object_id] = key
            found = anchor(payload)
            bucket(key)["questions"].append(
                {
                    "id": object_id,
                    "label": _label(object_id),
                    "statement": payload.get("text", ""),
                    "questioner": payload.get("questioner") or "",
                    "question_type": payload.get("question_type") or "",
                    "answer_state": payload.get("answer_state") or "",
                    "answer_verified_by_human": payload.get("answer_verified_by_human"),
                    "claim_ids": _as_list(payload.get("answer_claim_ids")),
                    "review_status": review_status,
                    "ordinal": _ordinal(object_id),
                    "rank": found["rank"],
                    "quotes": found["quotes"],
                }
            )

        for object_id, review_status, payload in by_collection["position_nodes"]:
            key = _source_key(object_id)
            node_owner[object_id] = key
            found = anchor(payload)
            bucket(key)["positions"].append(
                {
                    "id": object_id,
                    "label": _label(object_id),
                    "statement": payload.get("title", ""),
                    "attribution": payload.get("attribution") or "",
                    "review_status": review_status,
                    "ordinal": _ordinal(object_id),
                    "rank": found["rank"],
                    "quotes": found["quotes"],
                }
            )

        for object_id, review_status, payload in by_collection["claims"]:
            key = _source_key(object_id)
            step_ids = _as_list(payload.get("evidence_step_ids"))
            if key in {PILOT_KEY, UNSOURCED_KEY}:
                owners = {node_owner[s] for s in step_ids if s in node_owner} - {UNSOURCED_KEY}
                if len(owners) == 1:
                    key = owners.pop()
                elif key == UNSOURCED_KEY and owners:
                    key = sorted(owners)[0]
            claim = {
                "id": object_id,
                "label": _label(object_id),
                "statement": payload.get("statement", ""),
                "claim_type": payload.get("claim_type") or "",
                "attribution": payload.get("attribution") or "",
                "maturity": payload.get("maturity") or "",
                "review_status": review_status,
                "scripture_refs": [str(r) for r in payload.get("scripture_refs") or []],
                "topic_terms": _as_list(payload.get("topic_terms")),
                "step_ids": step_ids,
                "opposed_position_ids": _as_list(payload.get("opposed_position_ids")),
                "ordinal": _ordinal(object_id),
                "reviewed_by": payload.get("reviewed_by") or "",
                "review_note": payload.get("review_note") or "",
            }
            bucket(key)["claims"].append(claim)

        for edge in self._edges():
            owner = node_owner.get(edge["from"]) or node_owner.get(edge["to"])
            if owner:
                bucket(owner)["edges"].append(edge)

        for entry in buckets.values():
            for name in ("steps", "observations", "questions", "positions", "claims"):
                entry[name].sort(key=lambda item: (item.get("rank") is None, item.get("rank") or 0, item["ordinal"]))
            step_ids = {step["id"] for step in entry["steps"]}
            linked = set()
            for edge in entry["edges"]:
                linked.add(edge["from"])
                linked.add(edge["to"])
            entry["stats"] = {
                "steps": len(entry["steps"]),
                "steps_linked": len(step_ids & linked),
                "steps_isolated": len(step_ids - linked),
                "observations": len(entry["observations"]),
                "observations_linked": len({o["id"] for o in entry["observations"]} & linked),
                "claims": len(entry["claims"]),
                "questions": len(entry["questions"]),
                "positions": len(entry["positions"]),
                "edges": len(entry["edges"]),
            }

        ordered = sorted(buckets.values(), key=lambda item: (-item["stats"]["steps"], item["title"]))
        totals: dict[str, int] = defaultdict(int)
        for entry in ordered:
            for name, value in entry["stats"].items():
                totals[name] += value
        return {"lanes": list(LANES), "sources": ordered, "totals": dict(totals)}


def render_html(data: dict[str, Any], template: Path) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return template.read_text(encoding="utf-8").replace(
        "__ARGUMENT_LAYER_DATA__", payload.replace("</", "<\\/")
    )


def default_output() -> Path:
    base = os.getenv("DATA_BASE_DIR")
    if not base:
        raise SystemExit("Set DATA_BASE_DIR, or pass --output.")
    return (
        Path(base)
        / "wang-knowledge-platform/staging/reports/argument-layer-view/argument-layer.html"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()

    store = PostgresKnowledgeStore(database_url_from_env(args.database_url))
    data = ArgumentLayerReader(store).build()

    output = args.output or default_output()
    output.parent.mkdir(parents=True, exist_ok=True)
    template = Path(__file__).with_name("argument_layer_view.html")
    output.write_text(render_html(data, template), encoding="utf-8")
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps({"output": str(output), **data["totals"], "sources": len(data["sources"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
