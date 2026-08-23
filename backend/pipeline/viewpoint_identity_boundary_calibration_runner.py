"""Run the closed two-reviewer boundary calibration; never synthesize or apply."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_identity_boundary import (
    IdentityBoundaryCalibrationPlan,
    IdentityBoundaryAssessment,
    IdentityBoundaryRunArtifact,
    run_identity_boundary_review,
)
from backend.api.canonical_repository.viewpoint_identity_hypotheses import (
    IdentityCalibrationPlan,
)
from backend.api.canonical_repository.viewpoint_resolution import (
    StructuredJsonReviewerAdapter,
    ViewpointIdentityReviewPacket,
)
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient


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


def run_boundary_calibration(
    *,
    sampling_plan: IdentityCalibrationPlan | IdentityBoundaryCalibrationPlan,
    packets_dir: Path,
    output_dir: Path,
    max_new_hypotheses: int | None = None,
    blind_model_id: str | None = None,
    reasoning_effort: str = "high",
    selected_hypothesis_ids: set[str] | None = None,
    context_packets_dir: Path | None = None,
) -> dict[str, Any]:
    """Reuse only the immutable stratified sample, not the old review schema."""

    proposal_prompt = (
        PROMPT_DIR / "viewpoint_identity_boundary_proposal.md"
    ).read_text(encoding="utf-8")
    blind_prompt = (
        PROMPT_DIR / "viewpoint_identity_boundary_blind_review.md"
    ).read_text(encoding="utf-8")
    proposal = StructuredJsonReviewerAdapter(
        client=CodexSubscriptionClient(
            model=sampling_plan.proposal_model_id,
            reasoning_effort=reasoning_effort,
            timeout_seconds=900,
            max_output_tokens=5000,
        ),
        prompt=proposal_prompt,
        response_model=IdentityBoundaryAssessment,
        schema_name="wang_viewpoint_identity_boundary_proposal_v1",
    )
    actual_blind_model_id = blind_model_id or sampling_plan.blind_model_id
    blind = StructuredJsonReviewerAdapter(
        client=ClaudeSubscriptionClient(
            model=actual_blind_model_id,
            reasoning_effort=reasoning_effort,
            timeout_seconds=900,
            max_output_tokens=5000,
        ),
        prompt=blind_prompt,
        response_model=IdentityBoundaryAssessment,
        schema_name="wang_viewpoint_identity_boundary_blind_review_v1",
    )
    packet_shas = dict(
        zip(
            sampling_plan.selected_hypothesis_ids,
            sampling_plan.selected_packet_sha256s,
            strict=True,
        )
    )
    rows: list[dict[str, Any]] = []
    new_count = 0
    selected_ids = [
        hypothesis_id
        for hypothesis_id in sampling_plan.selected_hypothesis_ids
        if selected_hypothesis_ids is None or hypothesis_id in selected_hypothesis_ids
    ]
    if selected_hypothesis_ids is not None and set(selected_ids) != selected_hypothesis_ids:
        missing = sorted(selected_hypothesis_ids - set(selected_ids))
        raise ValueError(f"selected hypotheses are not in the sampling plan: {missing}")
    for hypothesis_id in selected_ids:
        packet = ViewpointIdentityReviewPacket.model_validate(
            _read(packets_dir / f"{hypothesis_id}.json")
        )
        if packet.packet_sha256 != packet_shas[hypothesis_id]:
            raise ValueError(f"{hypothesis_id}: boundary packet SHA mismatch")
        result_dir = output_dir / "runs" / hypothesis_id
        context_path = (
            context_packets_dir / f"{hypothesis_id}.json"
            if context_packets_dir is not None
            else None
        )
        context_packet = (
            _read(context_path)
            if context_path is not None and context_path.is_file()
            else None
        )
        expected_binding_sha = (
            str(context_packet.get("packet_sha256"))
            if context_packet is not None
            else packet.packet_sha256
        )
        existing = sorted(result_dir.glob("run.VIBR-*.json"))
        if not existing and max_new_hypotheses is not None and new_count >= max_new_hypotheses:
            continue
        if existing:
            if len(existing) != 1:
                raise ValueError(f"{hypothesis_id}: multiple boundary run artifacts")
            result = IdentityBoundaryRunArtifact.model_validate_json(
                existing[0].read_text(encoding="utf-8")
            )
            if result.packet_sha256 != expected_binding_sha:
                raise ValueError(f"{hypothesis_id}: cached boundary context binding mismatch")
        else:
            result = run_identity_boundary_review(
                hypothesis_id=hypothesis_id,
                packet=packet,
                proposal_reviewer=proposal,
                blind_reviewer=blind,
                output_dir=result_dir,
                context_packet=context_packet,
            )
            new_count += 1
        rows.append(
            {
                "hypothesis_id": hypothesis_id,
                "parent_packet_sha256": packet.packet_sha256,
                "packet_sha256": result.packet_sha256,
                "boundary_run_id": result.boundary_run_id,
                "boundary_run_artifact_sha256": result.artifact_sha256,
                "disposition": result.disposition,
                "agreed_relation": result.agreed_relation,
                "synthesis_eligible": result.synthesis_eligible,
            }
        )
    rows.sort(key=lambda row: row["hypothesis_id"])
    relation_counts = {
        relation: sum(row["agreed_relation"] == relation for row in rows)
        for relation in (
            "equivalent_all",
            "component",
            "tension",
            "related_only",
            "mixed",
            "unknown",
        )
    }
    payload = {
        "schema_version": "wang_viewpoint_identity_boundary_calibration_execution_v1",
        "parent_sampling_plan_sha256": sampling_plan.artifact_sha256,
        "proposal_backend": sampling_plan.proposal_backend,
        "proposal_model_id": sampling_plan.proposal_model_id,
        "proposal_prompt_sha256": proposal.prompt_sha256,
        "blind_backend": sampling_plan.blind_backend,
        "blind_model_id": actual_blind_model_id,
        "blind_prompt_sha256": blind.prompt_sha256,
        "reasoning_effort": reasoning_effort,
        "selected_hypothesis_ids": selected_ids,
        "selected_hypothesis_count": len(selected_ids),
        "completed_hypothesis_ids": [row["hypothesis_id"] for row in rows],
        "results": rows,
        "statistics": {
            "completed_hypothesis_count": len(rows),
            "semantic_call_count": 2 * len(rows),
            "boundary_agreement_count": sum(
                row["disposition"] == "agreed_boundary" for row in rows
            ),
            "boundary_disagreement_count": sum(
                row["disposition"] == "boundary_disagreement" for row in rows
            ),
            "synthesis_eligible_count": sum(row["synthesis_eligible"] for row in rows),
            **{f"agreed_{key}_count": value for key, value in relation_counts.items()},
        },
        "complete": len(rows) == len(selected_ids),
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
    parser.add_argument("--sampling-plan", type=Path, required=True)
    parser.add_argument("--packets-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-new-hypotheses", type=int)
    parser.add_argument("--blind-model")
    parser.add_argument(
        "--reasoning-effort", choices=("medium", "high", "xhigh"), default="high"
    )
    parser.add_argument("--selected-hypothesis-id", action="append")
    parser.add_argument("--context-packets-dir", type=Path)
    args = parser.parse_args()
    raw_plan = _read(args.sampling_plan)
    sampling_plan = (
        IdentityBoundaryCalibrationPlan.model_validate(raw_plan)
        if raw_plan.get("schema_version")
        == "wang_viewpoint_identity_boundary_calibration_plan_v1"
        else IdentityCalibrationPlan.model_validate(raw_plan)
    )
    result = run_boundary_calibration(
        sampling_plan=sampling_plan,
        packets_dir=args.packets_dir,
        output_dir=args.output_dir,
        max_new_hypotheses=args.max_new_hypotheses,
        blind_model_id=args.blind_model,
        reasoning_effort=args.reasoning_effort,
        selected_hypothesis_ids=(
            set(args.selected_hypothesis_id) if args.selected_hypothesis_id else None
        ),
        context_packets_dir=args.context_packets_dir,
    )
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir),
                "artifact_sha256": result["artifact_sha256"],
                **result["statistics"],
                "complete": result["complete"],
                "master_data_mutations": 0,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
