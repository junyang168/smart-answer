"""Read-only, isolated grouping sample for one reviewed passage key.

This is never a production grouping envelope or a partition authorization.
The CLI preserves the model response before validating it, and writes nothing
to Claim or Registry stores.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.api.canonical_repository.viewpoint_batch_resolution import ClaimGroupingResponse
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
from backend.pipeline.viewpoint_passage_grouping_preflight import (
    build_preview,
    plan_reviewed_passage_unit,
)


PROMPT = Path(__file__).resolve().parent / "prompts" / "canonical_viewpoint_passage_argument_split_sample.md"
MODEL = "claude-fable-5-1"


def _write_new(path: Path, value: dict) -> None:
    if path.exists():
        raise ValueError(f"refusing to overwrite {path}")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role-ledger", type=Path, required=True)
    parser.add_argument("--role-packet", type=Path, required=True)
    parser.add_argument("--passage-key", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise ValueError(f"refusing to reuse sample output root {args.output_dir}")
    ledger = json.loads(args.role_ledger.read_text(encoding="utf-8"))
    packet = json.loads(args.role_packet.read_text(encoding="utf-8"))
    preview = build_preview(ledger=ledger, packet=packet, batch_size=20)
    passage = next(
        (row for row in preview["passages"] if row["passage_key"] == args.passage_key),
        None,
    )
    if passage is None:
        raise ValueError(f"no reviewed passage bucket for {args.passage_key}")
    if passage["claim_count"] <= 20:
        raise ValueError("this sample runner is only for oversized passage buckets")
    claim_index = {row["claim_id"]: row for row in packet["claims"]}
    claim_ids = [row["claim_id"] for row in passage["claims"]]
    payload = {
        "scope_label": args.passage_key,
        "claims": [
            {
                "claim_id": claim_id,
                "statement": claim_index[claim_id]["statement"],
                "source_id": claim_index[claim_id]["source_id"],
                "other_interpreted_passage_keys": row["other_interpreted_passage_keys"],
            }
            for row in passage["claims"]
            for claim_id in [row["claim_id"]]
        ],
    }
    args.output_dir.mkdir(parents=True)
    prompt = PROMPT.read_text(encoding="utf-8")
    request = {
        "schema_version": "wang_passage_grouping_sample_request_v1",
        "status": "review_only_not_production_grouping",
        "role_ledger_sha256": preview["role_ledger_sha256"],
        "role_packet_sha256": preview["role_packet_sha256"],
        "prompt_sha256": sha256_json({"prompt": prompt}),
        "model": MODEL,
        "effort": "high",
        "passage_key": args.passage_key,
        "excluded_overlapping_bucket_keys": passage["overlapping_bucket_keys"],
        "payload": payload,
    }
    request["artifact_sha256"] = sha256_json(request)
    _write_new(args.output_dir / "request.json", request)
    adapter = StructuredJsonReviewerAdapter(
        client=ClaudeSubscriptionClient(model=MODEL, reasoning_effort="high"),
        prompt=prompt,
        response_model=ClaimGroupingResponse,
        schema_name="wang_canonical_viewpoint_claim_grouping_v1",
    )
    try:
        response = dict(adapter.generate(payload))
        raw = {
            "schema_version": "wang_passage_grouping_sample_raw_v1",
            "request_sha256": request["artifact_sha256"],
            "response": response,
        }
        raw["artifact_sha256"] = sha256_json(raw)
        _write_new(args.output_dir / "raw-response.json", raw)
        grouping = ClaimGroupingResponse.model_validate(response)
        plan_reviewed_passage_unit(
            unit_id=args.passage_key,
            claim_ids=claim_ids,
            batch_size=20,
            model_split=grouping,
        )
        report = {
            "schema_version": "wang_passage_grouping_sample_report_v1",
            "status": "review_only_validated_not_production_grouping",
            "request_sha256": request["artifact_sha256"],
            "raw_response_sha256": raw["artifact_sha256"],
            "claim_count": len(claim_ids),
            "group_count": len(grouping.groups),
            "groups": [
                {"group_key": group.group_key, "claim_count": len(group.claim_ids),
                 "claim_ids": group.claim_ids, "rationale": group.rationale}
                for group in grouping.groups
            ],
            "model_calls_executed": 1,
            "master_data_mutations": 0,
        }
        report["artifact_sha256"] = sha256_json(report)
        _write_new(args.output_dir / "report.json", report)
        print(json.dumps({key: value for key, value in report.items() if key != "groups"}, ensure_ascii=False, indent=2))
    except Exception as exc:
        failure = {
            "schema_version": "wang_passage_grouping_sample_failure_v1",
            "request_sha256": request["artifact_sha256"],
            "error_type": type(exc).__name__,
            "error": str(exc),
            "raw_response_path": "raw-response.json" if (args.output_dir / "raw-response.json").exists() else None,
        }
        failure["artifact_sha256"] = sha256_json(failure)
        _write_new(args.output_dir / "failure.json", failure)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
