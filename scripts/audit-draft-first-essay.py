#!/usr/bin/env python3
"""Check a draft-first delivery without changing prose, knowledge, or publication."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha, sha256_json
from backend.api.wang_article_reviews import _draft_first_annotated_markdown
from backend.pipeline.draft_first_author_runner import sha256_text, verbatim_quote_report
from backend.pipeline.draft_first_review_runner import validate_review
from backend.pipeline.draft_first_source_binding import reader_paragraphs, verify_bindings
from backend.pipeline.theological_evidence_packet import (
    _filesystem_source_original, _record_id, validate_theological_evidence_packet,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--review-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    packet = json.loads(args.packet.read_text())
    run = json.loads((args.review_dir / "review-run.json").read_text())
    markdown = (args.review_dir / "final.md").read_text()
    bindings = json.loads((args.review_dir / "source-bindings.json").read_text())
    errors, warnings = [], []
    validate_theological_evidence_packet(packet)
    manuscript_sha = sha256_text(markdown)
    for label, record in (("review", run), ("bindings", bindings)):
        if record.get("final_manuscript_sha256", record.get("manuscript_sha256")) != manuscript_sha:
            errors.append({"code": "manuscript_sha_mismatch", "stage": label})
        if record.get("evidence_packet_sha256") != packet["evidence_packet_sha256"]:
            errors.append({"code": "packet_sha_mismatch", "stage": label})
    if run["status"] != "review_passed":
        errors.append({"code": "editorial_review_not_passed"})
    profile_path = ROOT / "backend/config/editorial_quality_profiles/WQ-theological-topic-essay-v1.json"
    profile = json.loads(profile_path.read_text())
    if run["quality_profile_sha256"] != sha256_text(profile_path.read_text()):
        errors.append({"code": "quality_profile_changed"})
    last = run["rounds"][-1]["round"]
    outcome = json.loads((args.review_dir / f"round-{last:02d}" / "outcome.json").read_text())
    verdict = validate_review(outcome["review"], manuscript=markdown, profile=profile)
    if not outcome["passed"] or verdict["dimensions_below_minimum"] or verdict["hard_failures_failed"]:
        errors.append({"code": "editorial_verdict_invalid"})
    quotes = verbatim_quote_report(markdown, packet)
    if quotes["quotes_failing"] or quotes["long_quotes_not_blockquoted"]:
        errors.append({"code": "quote_verification_failed", "report": quotes})
    paragraphs = reader_paragraphs(markdown)
    verified = verify_bindings(bindings, paragraphs=paragraphs, packet=packet)
    for item in verified["bindings"]:
        if not item["spans"]:
            errors.append({"code": "paragraph_without_source", "paragraph_index": item["paragraph_index"]})
    for item in verified["findings"]:
        (warnings if item["code"] == "low_overlap" else errors).append(item)
    _, annotations, projection, playback = _draft_first_annotated_markdown(markdown, bindings, packet)
    if not projection["passed"]:
        errors.append({"code": "source_projection_failed", "findings": projection["findings"]})
    # The dependency ledger covers knowledge records; separately reopen every
    # original on disk. Stored anchor_state is never used as proof of bytes.
    for source in packet["source_documents"]:
        try:
            _filesystem_source_original(source)
        except Exception as exc:
            errors.append({"code": "source_original_changed", "source_id": source["source_id"], "detail": str(exc)})
    store = PostgresKnowledgeStore()
    by_collection = {}
    for dependency in packet["dependency_manifest"]:
        collection = dependency["collection"]
        if collection not in by_collection:
            by_collection[collection] = {_record_id(collection, x): x for x in store.list_records(collection)}
        current = by_collection[collection].get(dependency["record_id"])
        if current is None or semantic_record_sha(current) != dependency["sha256"]:
            errors.append({"code": "knowledge_dependency_changed", "collection": collection, "record_id": dependency["record_id"]})
    result = {"schema_version": "wang_draft_first_program_audit_v1", "status": "pass" if not errors else "fail",
              "manuscript_sha256": manuscript_sha, "evidence_packet_sha256": packet["evidence_packet_sha256"],
              "editorial_review_sha256": sha256_json(outcome["review"]), "errors": errors, "warnings": warnings,
              "paragraphs_checked": len(paragraphs), "annotations": len(annotations),
              "quote_report": quotes, "source_projection_audit": projection, "source_playback_audit": playback,
              "not_independent_library_audit": True, "publication_authorized": False}
    result["audit_sha256"] = sha256_json(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "errors": len(errors), "warnings": len(warnings),
                      "output": str(args.output)}, ensure_ascii=False))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
