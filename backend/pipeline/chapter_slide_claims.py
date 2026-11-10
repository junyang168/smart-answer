"""Compile source-local Claim hints for a chapter-slide experiment; never change knowledge."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from backend.pipeline import chapter_exposition, chapter_slides


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def validate_context(context, passage, lecture, decisions, decisions_sha):
    if (context.get("passage"), context.get("lecture"), context.get("decisions_sha256")) != (passage, lecture, decisions_sha):
        raise ValueError("Claim context does not match these decisions/lecture/passage")
    if context.get("transcript_sha256") != decisions.get("transcript_sha256"):
        raise ValueError("Claim context transcript changed")


def compile_context(passage: str, lecture: str, *, data_base_dir: Path, store: Any) -> dict:
    from backend.pipeline.original_audio_index import Sermons
    path = chapter_exposition.record_path(data_base_dir, passage, lecture)
    decisions = json.loads(path.read_text())
    documents = [d for d in store.list_records("source_documents") if d.get("transcript_id") == lecture]
    if len(documents) != 1:
        raise ValueError("Expected one source document")
    document = documents[0]
    sermons = Sermons(data_base_dir)
    source_path = sermons.transcript_path(document)
    if not source_path or chapter_exposition.file_sha256(source_path) != decisions["transcript_sha256"]:
        raise ValueError("Actual transcript differs from saved keep decisions")
    if chapter_exposition.cut(sermons.load(document["source_id"], document)) != decisions["pieces"]:
        raise ValueError("Actual transcript pieces differ from saved keep decisions")
    # Locate verbatim excerpts in the actual, byte-verified transcript, including
    # excluded pieces. Only sentences overlapping an excerpt can be cited as hints.
    text = ""
    piece_offsets = {}
    for piece in decisions["pieces"]:
        piece_offsets[piece["id"]] = len(text)
        text += compact(piece["text"])
    sentence_spans = []
    for line in chapter_slides.sentences(chapter_slides.kept_pieces(decisions)):
        piece = next(p for p in decisions["pieces"] if p["id"] == line["piece"])
        part = compact(piece["text"])
        # Repeated identical sentences are tracked in order within each piece.
        previous = [end - piece_offsets[line["piece"]] for _, end, _, pid in sentence_spans if pid == line["piece"]]
        start = part.find(compact(line["text"]), previous[-1] if previous else 0)
        if start < 0:
            raise ValueError("Cannot locate a retained sentence")
        low = piece_offsets[line["piece"]] + start
        sentence_spans.append((low, low + len(compact(line["text"])), line["id"], line["piece"]))
    steps = {e["evidence_step_id"]: e for e in store.list_records("evidence_steps")}
    fragments = {f["fragment_id"]: f for f in store.list_records("source_fragments")}
    included, excluded = [], []
    for claim in sorted(store.list_records("claims"), key=lambda c: c["claim_id"]):
        if not any(o.get("transcript_id") == lecture for o in claim.get("occurrences", [])):
            continue
        cid = claim["claim_id"]
        if claim.get("review_status") in {"superseded", "retired", "rejected"}:
            excluded.append({"claim_id": cid, "reason": claim.get("review_status")})
            continue
        evidence = []
        for eid in claim.get("evidence_step_ids", []):
            step = steps.get(eid)
            if step is None:
                raise ValueError(f"Missing evidence step: {eid}")
            anchors = []
            fids = list(step.get("source_fragment_ids") or [])
            if step.get("source_fragment_id"):
                fids.append(step["source_fragment_id"])
            for fid in dict.fromkeys(fids):
                fragment = fragments.get(fid)
                if fragment is None:
                    raise ValueError(f"Missing fragment: {fid}")
                if fragment.get("source_id") != document["source_id"]:
                    raise ValueError(f"Cross-sermon fragment in {cid}: {fid}")
                quote = fragment.get("verbatim_excerpt") or ""
                needle = compact(quote)
                if not needle or text.count(needle) != 1:
                    raise ValueError(f"Excerpt missing/ambiguous in actual transcript: {fid}")
                low = text.index(needle)
                high = low + len(needle)
                ids = [sid for a, b, sid, _ in sentence_spans if a < high and b > low]
                if ids:
                    anchors.append({"fragment_id": fid, "fragment_sha256": digest(fragment),
                                    "quote": quote, "sentences": ids})
            if anchors:
                evidence.append({"evidence_step_id": eid, "sha256": digest(step),
                                 "statement": step.get("statement"), "step_type": step.get("step_type"),
                                 "review_status": step.get("review_status"), "anchors": anchors})
        if evidence:
            included.append({"claim_id": cid, "sha256": digest(claim), "statement": claim["statement"],
                             "review_status": claim.get("review_status"), "attribution": claim.get("attribution"),
                             "scripture_refs": claim.get("scripture_refs"), "evidence_steps": evidence})
        else:
            excluded.append({"claim_id": cid, "sha256": digest(claim), "reason": "no evidence in retained sentences"})
    return {"schema_version": "chapter-slide-claim-context.v1", "passage": passage, "lecture": lecture,
            "decisions_sha256": chapter_exposition.file_sha256(path),
            "transcript_sha256": decisions["transcript_sha256"], "source_id": document["source_id"],
            "claims": included, "excluded": excluded}



def review_contract(context: dict, slides: list[dict]) -> tuple[str, dict]:
    from copy import deepcopy
    schema = deepcopy(chapter_slides.REVIEW_SCHEMA)
    schema["properties"]["claim_checks"] = {"type": "array", "items": {
        "type": "object", "properties": {
            "claim_id": {"type": "string"},
            "status": {"type": "string", "enum": ["covered", "missing", "not_supported"]},
            "slides": {"type": "array", "items": {"type": "string"}},
            "reason": {"type": "string"}},
        "required": ["claim_id", "status", "slides", "reason"]}}
    schema["required"].append("claim_checks")
    instruction = """\n另逐項回查提示包 claims 中的每一條（不含 excluded），回傳 claim_checks。
covered：保留原文支持且投影片已表達重要結論、理由和限定，slides 列投影片 first。
missing：保留原文支持但漏了重要內容，必須同時列一條「完整」finding 並標 blocking。
not_supported：這條提示不受保留原文支持，不得補進幻燈；reason 說明原文依據。
每條恰好一次，不因 Claim 已經審核就信它。不要為逐條覆蓋重複要點、硬加標題或改講課次序。
"""
    return instruction, schema


def check_review(answer: dict, context: dict, slides: list[dict]) -> None:
    expected = {c["claim_id"] for c in context["claims"]}
    checks = answer.get("claim_checks", [])
    ids = [c.get("claim_id") for c in checks]
    if len(ids) != len(expected) or set(ids) != expected:
        raise ValueError("Review must check every supplied Claim exactly once")
    firsts = {s["first"] for s in slides}
    for check in checks:
        if check.get("status") not in {"covered", "missing", "not_supported"} or not check.get("reason"):
            raise ValueError("Invalid Claim disposition")
        if not set(check.get("slides", [])) <= firsts:
            raise ValueError("Claim check references unknown slide")
        if check["status"] == "covered" and not check.get("slides"):
            raise ValueError("Covered Claim needs a slide")
        if check["status"] == "missing" and (not answer.get("blocking") or not any(
            f.get("kind") == "完整" for f in answer.get("findings", [])
        )):
            raise ValueError("Missing Claim requires a blocking completeness finding")


def saved_context(record: dict, decisions: dict, decisions_sha: str) -> dict | None:
    context = record.get("claim_context")
    if context is not None:
        if digest(context) != record.get("claim_context_sha256"):
            raise ValueError("Saved Claim context changed")
        validate_context(context, record["passage"], record["lecture"], decisions, decisions_sha)
    return context

def main():
    from backend.api.config import DATA_BASE_PATH
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("passage")
    parser.add_argument("--lecture", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; reuse the frozen context")
    context = compile_context(args.passage, args.lecture, data_base_dir=Path(DATA_BASE_PATH), store=PostgresKnowledgeStore())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(context, ensure_ascii=False, indent=2))
    print(json.dumps({"claims": len(context["claims"]), "excluded": len(context["excluded"]),
                      "sha256": digest(context), "file": str(args.output)}, ensure_ascii=False))


class RecordedClient:
    """Save exact requests and raw CLI replies; replay never starts an unknown call."""

    def __init__(self, client, directory: Path):
        self.client, self.directory = client, directory
        self.model = client.model

    def generate_json(self, system, prompt, schema):
        request = {"system": system, "user": prompt, "schema": schema, "model": self.model}
        root = self.directory / digest(request)
        root.mkdir(parents=True, exist_ok=True)
        target = root / "request.json"
        if target.exists():
            if json.loads(target.read_text()) != request:
                raise ValueError("Saved request changed")
        else:
            target.write_text(json.dumps(request, ensure_ascii=False, indent=2))
        self.client.call_artifacts = root
        answer = self.client.generate_json(system, prompt, schema)
        (root / "answer.json").write_text(json.dumps(answer, ensure_ascii=False, indent=2))
        return answer


if __name__ == "__main__":
    main()
