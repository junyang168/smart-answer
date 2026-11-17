"""Read the rebuilt argument and its bound transcript for the colleague viewer."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException

from backend.pipeline.argument_review import connect, sentence_times
from backend.pipeline.chapter_slide_claims import instance_records

router = APIRouter(prefix="/admin/argument-layer/instance", tags=["argument-layer-admin"])
KINDS = {
    "claims": ("claim_id", "論點"),
    "evidence_steps": ("evidence_step_id", "論據"),
    "observations": ("observation_id", "經文觀察"),
    "position_nodes": ("position_id", "反方"),
    "questions": ("question_id", "問題"),
}


@router.get("/lectures")
def lectures():
    with connect() as conn:
        rows = conn.execute("""SELECT c.change_set_id, o.payload
            FROM wang_knowledge.change_sets c
            JOIN wang_knowledge.object_versions v ON v.change_set_id=c.change_set_id
            JOIN wang_knowledge.objects o USING (collection, object_id, revision)
            WHERE c.source_kind='argument_layer_rebuild' AND o.collection='source_documents'
            ORDER BY o.payload->>'title', c.applied_at DESC""").fetchall()
    # One current entry per source, even after another rebuild.
    found = {}
    for change_set, source in rows:
        found.setdefault(source["source_id"], {
            "id": source["source_id"], "title": source["title"], "change_set": change_set})
    return {"lectures": list(found.values())}


def build_view(records, meta):
    """Use stored nodes/relations; run files supply group membership and source snapshot."""
    candidate = Path(meta["candidate_path"])
    if hashlib.sha256(candidate.read_bytes()).hexdigest() != meta["candidate_sha256"]:
        raise ValueError("生成資料已變更，無法確認與新庫版本一致")
    result = json.loads((candidate.parent / "creation-result.json").read_text())
    [document] = records["source_documents"]
    snapshot = json.loads(Path(document["source_path"]).read_text())
    paragraphs = [p for p in snapshot["script"] if p.get("type") != "subtitle"]
    # Reuse the review page's alignment to timed transcript segments.
    from backend.api.config import DATA_BASE_PATH
    raw_path = DATA_BASE_PATH / "script" / (document["title"] + ".json")
    if raw_path.exists():
        entries = json.loads(raw_path.read_text()).get("entries", [])
        for para in paragraphs:
            lo, hi = para.get("start_index"), para.get("end_index")
            if lo is not None and hi is not None:
                para["segments"] = [(e["start_ms"] / 1000, e["end_ms"] / 1000, e["text"])
                                    for e in entries if lo <= e["index"] <= hi]
    fragments = {r["fragment_id"]: r for r in records.get("source_fragments", [])}
    nodes = {}
    for collection, (key, kind) in KINDS.items():
        for row in records.get(collection, []):
            fids = list(row.get("source_fragment_ids") or [])
            if collection == "claims":
                fids = [a["source_fragment_id"] for o in row.get("occurrences", [])
                        for a in o.get("anchors", []) if a.get("source_fragment_id")
                        and not a.get("evidence_id") and not a.get("observation_id")] + fids
            anchors = []
            for fid in dict.fromkeys(fids):
                if fid not in fragments:
                    continue
                f = fragments[fid]
                excerpt = f["verbatim_excerpt"]
                # Exact match into the bound snapshot; no current transcript is substituted.
                para = next((p for p in paragraphs if p.get("index") == f.get("source_segment_index")
                             and excerpt in p.get("text", "")), None)
                if para is None:
                    para = next((p for p in paragraphs if excerpt in p.get("text", "")), None)
                start, end = f.get("media_time"), f.get("media_end_time")
                if para is not None:
                    start, end = sentence_times(
                        {"script_index": para["index"], "text": excerpt,
                         "_raw_at": para["text"].find(excerpt)}, {str(para["index"]): para})
                anchors.append({
                    "id": fid, "quote": excerpt, "paragraph": para.get("text") if para else None,
                    "start": start, "end": end,
                    "paragraph_key": f.get("paragraph_key"),
                })
            anchors.sort(key=lambda a: a["start"] if a["start"] is not None else float("inf"))
            nodes[row[key]] = {
                "id": row[key], "kind": kind,
                "statement": row.get("statement") or row.get("text") or row.get("title", ""),
                "reason": row.get("discourse_role", ""), "scriptures": row.get("scripture_refs") or [],
                "question": result.get("claim_candidates", {}).get(row[key].rsplit("-", 1)[-1], {}).get("answers_question", "") if collection == "claims" else "",
                "anchors": anchors,
            }
    relations = [{"id": r["relation_id"], "from": r["from_id"], "to": r["to_id"],
                  "type": r["relation_type"], "reason": r.get("reason", "")}
                 for r in records.get("knowledge_relations", [])
                 if r["from_id"] in nodes and r["to_id"] in nodes]
    # Normalized native links live on records, not only in knowledge_relations.
    seen = {(r["from"], r["to"], r["type"]) for r in relations}
    def declared(source, target, kind, reason):
        key = (source, target, kind)
        if source in nodes and target in nodes and key not in seen:
            seen.add(key)
            relations.append({"id": "|".join(key), "from": source, "to": target,
                              "type": kind, "reason": reason})
    for claim in records.get("claims", []):
        for eid in claim.get("evidence_step_ids", []):
            declared(eid, claim["claim_id"], "supports", nodes.get(eid, {}).get("reason", ""))
        for pid in claim.get("opposed_position_ids", []):
            declared(claim["claim_id"], pid, "refutes", "")
    for step in records.get("evidence_steps", []):
        for cid in step.get("produced_claim_ids", []):
            declared(step["evidence_step_id"], cid, "supports", step.get("discourse_role", ""))
    for question in records.get("questions", []):
        for cid in question.get("answer_claim_ids", []):
            declared(cid, question["question_id"], "answers", "")
    for relation in records.get("claim_relations", []):
        declared(relation["from_claim_id"], relation["to_claim_id"],
                 relation["relation_type"], relation.get("reason", ""))
    groups = []
    for index, (gid, shorts) in enumerate(result["group_claim_ids"].items()):
        claims = [nid for short in shorts for nid, n in nodes.items()
                  if n["kind"] == "論點" and nid.endswith("-" + short)]
        prefix = f"-P{index + 1:02d}-"
        included = set(claims) | {nid for nid in nodes if prefix in nid}
        # Follow actual incoming support paths, plus questions/positions attached to these Claims.
        while True:
            before = len(included)
            for edge in relations:
                if edge["to"] in included and edge["type"] in ("supports", "qualifies"):
                    included.add(edge["from"])
                if edge["from"] in included and edge["type"] in ("answers", "refutes"):
                    included.add(edge["to"])
            if len(included) == before:
                break
        steps = sorted((nid for nid in included if nodes[nid]["kind"] != "論點"),
                       key=lambda nid: (
                           min((a["start"] for a in nodes[nid]["anchors"] if a["start"] is not None), default=float("inf")),
                           nid))
        groups.append({"id": gid, "title": result["groups"][index]["title"],
                       "claims": claims, "steps": steps,
                       "relations": [r for r in relations if r["from"] in included and r["to"] in included],
                       "related_claims": [nid for nid in included if nodes[nid]["kind"] == "論點" and nid not in claims]})
    return {"title": document["title"], "source_id": document["source_id"],
            "source_sha": document.get("source_sha256"), "groups": groups, "nodes": nodes,
            "media_url": "/web/video/" + quote(document["title"], safe="") + ".mp3"}


@router.get("/lectures/{source_id}")
def lecture(source_id: str):
    try:
        with connect() as conn:
            row = conn.execute("""SELECT v.change_set_id FROM wang_knowledge.objects o
                JOIN wang_knowledge.object_versions v USING (collection,object_id,revision)
                WHERE o.collection='source_documents' AND o.object_id=%s""", (source_id,)).fetchone()
            if not row:
                raise HTTPException(404, "新庫沒有這篇講道")
            records, meta = instance_records(conn, row[0])
        return dict(build_view(records, meta), change_set=row[0])
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc
