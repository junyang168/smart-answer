"""Execute a resumable dual-review identity calibration; never apply results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_identity_hypotheses import (
    IdentityCalibrationPlan,
)
from backend.api.canonical_repository.viewpoint_resolution import (
    DeltaAdjudicationResponse,
    SemanticAssessment,
    StructuredJsonReviewerAdapter,
    ViewpointIdentityReviewPacket,
    ViewpointResolutionRunArtifact,
    run_viewpoint_resolution,
)
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = Path(__file__).with_name("prompts")


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_immutable(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if _read(path) != payload:
            raise ValueError(f"immutable artifact differs at {path}")
        return
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def run_calibration(
    *, plan: IdentityCalibrationPlan, packets_dir: Path, output_dir: Path,
    decided_at: str, max_new_hypotheses: int | None = None,
) -> dict[str, Any]:
    proposal_client = CodexSubscriptionClient(
        model=plan.proposal_model_id, reasoning_effort=plan.reasoning_effort,
        timeout_seconds=900, max_output_tokens=12000,
    )
    blind_client = ClaudeSubscriptionClient(
        model=plan.blind_model_id, reasoning_effort=plan.reasoning_effort,
        timeout_seconds=900, max_output_tokens=12000,
    )
    delta_client = CodexSubscriptionClient(
        model=plan.proposal_model_id, reasoning_effort=plan.reasoning_effort,
        timeout_seconds=900, max_output_tokens=8000,
    )
    proposal = StructuredJsonReviewerAdapter(
        client=proposal_client,
        prompt=(PROMPT_DIR / "viewpoint_identity_proposal.md").read_text(encoding="utf-8"),
        response_model=SemanticAssessment,
        schema_name="wang_viewpoint_identity_proposal_v1",
    )
    blind = StructuredJsonReviewerAdapter(
        client=blind_client,
        prompt=(PROMPT_DIR / "viewpoint_identity_blind_review.md").read_text(encoding="utf-8"),
        response_model=SemanticAssessment,
        schema_name="wang_viewpoint_identity_blind_review_v1",
    )
    delta = StructuredJsonReviewerAdapter(
        client=delta_client,
        prompt=(PROMPT_DIR / "viewpoint_identity_delta_adjudication.md").read_text(encoding="utf-8"),
        response_model=DeltaAdjudicationResponse,
        schema_name="wang_viewpoint_identity_delta_adjudication_response_v1",
    )
    rows = []
    new_count = 0
    packet_shas = dict(zip(
        plan.selected_hypothesis_ids, plan.selected_packet_sha256s, strict=True
    ))
    for hypothesis_id in plan.selected_hypothesis_ids:
        packet = ViewpointIdentityReviewPacket.model_validate(
            _read(packets_dir / f"{hypothesis_id}.json")
        )
        if packet.packet_sha256 != packet_shas[hypothesis_id]:
            raise ValueError(f"{hypothesis_id}: calibration packet SHA mismatch")
        result_dir = output_dir / "runs" / hypothesis_id
        existing_runs = sorted(result_dir.glob("run.*.json"))
        if not existing_runs and max_new_hypotheses is not None and new_count >= max_new_hypotheses:
            continue
        result = run_viewpoint_resolution(
            packet=packet,
            proposal_reviewer=proposal,
            blind_reviewer=blind,
            delta_adjudicator=delta,
            output_dir=result_dir,
            decided_at=decided_at,
            consumer_impact="none",
        )
        if not existing_runs:
            new_count += 1
        rows.append({
            "hypothesis_id": hypothesis_id,
            "packet_sha256": packet.packet_sha256,
            "resolution_run_id": result.resolution_run_id,
            "resolution_run_artifact_sha256": result.artifact_sha256,
            "disposition": result.disposition,
            "semantic_delta_count": len(result.semantic_deltas),
            "semantic_call_count": len(result.call_ledger),
        })
    rows.sort(key=lambda row: row["hypothesis_id"])
    payload = {
        "schema_version": "wang_viewpoint_identity_calibration_execution_v1",
        "calibration_plan_sha256": plan.artifact_sha256,
        "completed_hypothesis_ids": [row["hypothesis_id"] for row in rows],
        "results": rows,
        "statistics": {
            "selected_hypothesis_count": len(plan.selected_hypothesis_ids),
            "completed_hypothesis_count": len(rows),
            "system_approved_count": sum(row["disposition"] == "system_approved" for row in rows),
            "human_exception_count": sum(row["disposition"] == "human_exception" for row in rows),
            "semantic_delta_count": sum(row["semantic_delta_count"] for row in rows),
            "semantic_call_count": sum(row["semantic_call_count"] for row in rows),
        },
        "complete": len(rows) == len(plan.selected_hypothesis_ids),
        "master_data_mutations": 0,
        "apply_allowed": False,
    }
    payload["artifact_sha256"] = sha256_json(payload)
    _write_immutable(
        output_dir / "execution-reports" / f"{payload['artifact_sha256']}.json",
        payload,
    )
    return payload


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--packets-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--decided-at", required=True)
    parser.add_argument("--max-new-hypotheses", type=int)
    args = parser.parse_args()
    result = run_calibration(
        plan=IdentityCalibrationPlan.model_validate(_read(args.plan)),
        packets_dir=args.packets_dir,
        output_dir=args.output_dir,
        decided_at=args.decided_at,
        max_new_hypotheses=args.max_new_hypotheses,
    )
    print(json.dumps({
        "output_dir": str(args.output_dir),
        "artifact_sha256": result["artifact_sha256"],
        **result["statistics"],
        "complete": result["complete"],
        "master_data_mutations": 0,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
