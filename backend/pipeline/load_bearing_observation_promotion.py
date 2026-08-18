"""Promote a load-bearing original-language observation into a claim.

An `ObservationRecord` sits outside the claim graph: no CompositionPlan can
reference it, and no application chain's `professor_interpretation_claim_ids`
can resolve to it. When the observation is load-bearing -- the publication
profile's own test is "delete this observation; does the paragraph's
conclusion still hold?" -- that invisibility is a real gap, not a formality:
Matt 16:23's phroneo observation records the professor's own reworking of
"you do not have in mind the things of God, but the things of man," and nine
successive drafts of the matthew-16-21-23 article, including one produced by
a real end-to-end run of the authoring pipeline, never used it, because
nothing obligated any of them to.

This module does not decide which observations are load-bearing -- that is
an editorial judgment, and this platform's design is explicit that AI
proposes, humans decide. It takes an explicit `rationale` from the caller,
verifies the observation and its source fragment still resolve to real,
matching records, and writes a minimal, traceable claim plus the evidence
step that backs it. It never infers a rationale and never promotes silently.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any


class LoadBearingPromotionError(RuntimeError):
    """Raised when a promotion cannot proceed without losing traceability."""


@dataclass(frozen=True)
class PromotionRequest:
    observation_id: str
    claim_id: str
    evidence_step_id: str
    claim_type: str
    rationale: str
    statement: str | None = None
    step_type: str = "original_language"
    attribution: str = "professor"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise LoadBearingPromotionError(message)


def build_promotion_package(
    request: PromotionRequest, *, store: Any
) -> dict[str, Any]:
    """Return a knowledge package promoting one observation to a claim.

    Raises `LoadBearingPromotionError` if the observation, its fragment, or
    either target id are not in the state this promotion assumes -- never
    silently substitutes a different record or overwrites an existing one.
    """

    _require(bool(request.rationale.strip()), "rationale is required and must not be empty")

    observation = store.get_record("observations", request.observation_id)
    _require(observation is not None, f"observation not found: {request.observation_id}")

    fragment_id = observation.get("source_fragment_id")
    if not fragment_id:
        fragment_ids = observation.get("source_fragment_ids") or []
        _require(len(fragment_ids) == 1, (
            f"observation {request.observation_id} must resolve to exactly one "
            f"source fragment to promote deterministically; got {fragment_ids}"
        ))
        fragment_id = fragment_ids[0]
    fragment = store.get_record("source_fragments", fragment_id)
    _require(fragment is not None, f"source fragment not found: {fragment_id}")

    _require(
        store.get_record("claims", request.claim_id) is None,
        f"claim_id already exists, refusing to overwrite: {request.claim_id}",
    )
    _require(
        store.get_record("evidence_steps", request.evidence_step_id) is None,
        f"evidence_step_id already exists, refusing to overwrite: {request.evidence_step_id}",
    )

    statement = request.statement or observation["statement"]
    scripture_refs = observation.get("scripture_refs") or []

    evidence_step = {
        "evidence_step_id": request.evidence_step_id,
        "source_fragment_id": fragment_id,
        "statement": statement,
        "step_type": request.step_type,
        "speaker": request.attribution,
        "stance": "asserted",
        "discourse_role": f"promoted_from_observation:{request.observation_id}",
        "support_eligibility": "eligible_candidate",
        "produced_claim_ids": [request.claim_id],
        "scripture_refs": scripture_refs,
    }
    claim = {
        "claim_id": request.claim_id,
        "statement": statement,
        "claim_type": request.claim_type,
        "attribution": request.attribution,
        "scripture_refs": scripture_refs,
        "evidence_step_ids": [request.evidence_step_id],
        "maturity": "candidate",
        "promoted_from_observation_id": request.observation_id,
        "promotion_rationale": request.rationale,
    }

    # The fragment already exists in the store; carrying it unchanged keeps
    # the package's own cross-reference validation satisfiable (it checks
    # `source_fragment_id` resolves within the *submitted* package, not
    # against what is already stored) without re-authoring or duplicating it.
    return {
        "package_id": f"PROMOTE-{request.observation_id}",
        "source_documents": [],
        "source_fragments": [fragment],
        "claims": [claim],
        "evidence_steps": [evidence_step],
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observation-id", required=True)
    parser.add_argument("--claim-id", required=True)
    parser.add_argument("--evidence-step-id", required=True)
    parser.add_argument("--claim-type", required=True)
    parser.add_argument(
        "--rationale", required=True,
        help="Why this observation is load-bearing: what conclusion fails without it.",
    )
    parser.add_argument("--statement", default=None)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore

    store = PostgresKnowledgeStore()
    request = PromotionRequest(
        observation_id=args.observation_id,
        claim_id=args.claim_id,
        evidence_step_id=args.evidence_step_id,
        claim_type=args.claim_type,
        rationale=args.rationale,
        statement=args.statement,
    )
    package = build_promotion_package(request, store=store)
    print(f"observation={request.observation_id} -> claim={request.claim_id}, "
          f"evidence_step={request.evidence_step_id}")
    print(f"rationale: {request.rationale}")

    if not args.apply:
        print("\n驗證通過。加 --apply 才會寫入 PostgreSQL。")
        return 0

    plan = store.plan_package(package, source_kind="load_bearing_observation_promotion")
    result = store.apply_plan(plan, metadata={"promotion": request.observation_id})
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
