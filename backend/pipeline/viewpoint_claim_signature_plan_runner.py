"""Build a zero-call Claim semantic-signature plan from scheduler v3."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.viewpoint_candidate_recall import ViewpointCandidateRecallArtifact
from backend.api.canonical_repository.viewpoint_claim_signature import build_claim_signature_plan
from backend.api.canonical_repository.viewpoint_semantic_scheduler import SemanticBundleSchedule
from backend.api.canonical_repository.viewpoint_foundation import sha256_json

PROMPT_PATH = Path(__file__).with_name("prompts") / "viewpoint_claim_signature.md"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedule", type=Path, required=True)
    parser.add_argument("--candidate-recall", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--reasoning-effort", choices=("low", "medium", "high", "xhigh"), default="medium")
    parser.add_argument("--max-output-tokens", type=int, default=32000)
    args = parser.parse_args()
    recall = ViewpointCandidateRecallArtifact.model_validate(_read(args.candidate_recall))
    plan = build_claim_signature_plan(
        schedule=SemanticBundleSchedule.model_validate(_read(args.schedule)),
        candidate_recall_artifact_sha256=recall.artifact_sha256,
        source_ineligible_claim_ids=recall.source_ineligible_claim_ids,
        model_id=args.model,
        reasoning_effort=args.reasoning_effort,
        max_output_tokens=args.max_output_tokens,
        prompt_sha256=sha256_json({"prompt": PROMPT_PATH.read_text(encoding="utf-8")}),
    )
    for packet in plan.packets:
        _write(args.output_dir / "packets" / f"{packet.packet_id}.json", packet)
    _write(args.output_dir / "claim-signature-plan.json", plan)
    _write(args.output_dir / "claim-signature-budget.json", {
        "schema_version": "wang_claim_semantic_signature_budget_v1",
        "plan_artifact_sha256": plan.artifact_sha256,
        "model_id": plan.model_id,
        "backend": plan.backend,
        "reasoning_effort": plan.reasoning_effort,
        "max_output_tokens": plan.max_output_tokens,
        "prompt_sha256": plan.prompt_sha256,
        **plan.statistics,
        "model_calls_executed": 0,
        "apply_allowed": False,
    })
    print(json.dumps(plan.statistics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
