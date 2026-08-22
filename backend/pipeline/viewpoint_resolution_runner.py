"""Run one SHA-bound CanonicalViewpoint identity review; never apply its proposal."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from backend.api.canonical_repository.viewpoint_resolution import (
    DeltaAdjudicationResponse,
    SemanticAssessment,
    StructuredJsonReviewerAdapter,
    ViewpointIdentityReviewPacket,
    run_viewpoint_resolution,
)
from backend.pipeline.stage1 import Stage1AnthropicClient, Stage1OpenAIClient


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = Path(__file__).with_name("prompts")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--decided-at", required=True)
    parser.add_argument(
        "--consumer-impact",
        choices=("none", "planning", "publication", "withdrawal"),
        default="none",
    )
    parser.add_argument("--proposal-model", default="gpt-5.6-sol")
    parser.add_argument("--blind-model", default="claude-sonnet-5")
    parser.add_argument("--delta-model", default="gpt-5.6-sol")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    packet = ViewpointIdentityReviewPacket.model_validate(
        json.loads(args.packet.read_text(encoding="utf-8"))
    )
    proposal_client = Stage1OpenAIClient(
        model=args.proposal_model,
        reasoning_effort="medium",
        timeout_seconds=360,
        max_retries=1,
        max_output_tokens=12000,
    )
    blind_client = Stage1AnthropicClient(
        model=args.blind_model,
        timeout_seconds=360,
        max_retries=1,
        max_output_tokens=12000,
    )
    delta_client = Stage1OpenAIClient(
        model=args.delta_model,
        reasoning_effort="medium",
        timeout_seconds=360,
        max_retries=1,
        max_output_tokens=8000,
    )
    proposal_prompt = (
        PROMPT_DIR / "viewpoint_identity_proposal.md"
    ).read_text(encoding="utf-8")
    blind_prompt = (
        PROMPT_DIR / "viewpoint_identity_blind_review.md"
    ).read_text(encoding="utf-8")
    delta_prompt = (
        PROMPT_DIR / "viewpoint_identity_delta_adjudication.md"
    ).read_text(encoding="utf-8")
    result = run_viewpoint_resolution(
        packet=packet,
        proposal_reviewer=StructuredJsonReviewerAdapter(
            client=proposal_client,
            prompt=proposal_prompt,
            response_model=SemanticAssessment,
            schema_name="wang_viewpoint_identity_proposal_v1",
        ),
        blind_reviewer=StructuredJsonReviewerAdapter(
            client=blind_client,
            prompt=blind_prompt,
            response_model=SemanticAssessment,
            schema_name="wang_viewpoint_identity_blind_review_v1",
        ),
        delta_adjudicator=StructuredJsonReviewerAdapter(
            client=delta_client,
            prompt=delta_prompt,
            response_model=DeltaAdjudicationResponse,
            schema_name="wang_viewpoint_identity_delta_adjudication_response_v1",
        ),
        output_dir=args.output_dir,
        decided_at=args.decided_at,
        consumer_impact=args.consumer_impact,
    )
    print(
        json.dumps(
            {
                "resolution_run_id": result.resolution_run_id,
                "disposition": result.disposition,
                "artifact_sha256": result.artifact_sha256,
                "output_dir": str(args.output_dir),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
