"""Give every required argument step a claim the grounding gate can see.

A contract's `required_argument_steps` carry the reasoning an article must
preserve, quoted verbatim from the base manuscript. But the grounding gate
checks a paragraph against the claims it cites, and several of those
excerpts were never extracted into the claim layer at all: for
CP-matthew-16-21-23, three of five steps had no source fragment behind them.
The result is a contradiction the author cannot satisfy -- the contract
obliges it to write reasoning that the gate then reports as unsupported,
because the material exists only as contract text.

This backfills the missing link: for each required step whose excerpt has no
claim, it creates fragment -> evidence step -> claim, and records the
resulting `claim_id` on the step so the author knows which claim to cite.
Every excerpt is verified to be a verbatim substring of the manuscript the
step names before anything is written; nothing is invented, and steps that
already resolve to a claim are left untouched.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.pipeline.matthew_exposition_authoring import sha256_text


class RequiredStepBackfillError(RuntimeError):
    """Raised when a step cannot be backfilled without inventing material."""


@dataclass
class StepPlan:
    step_id: str
    source_id: str
    source_excerpt: str
    statement: str
    existing_claim_id: str | None = None
    fragment_id: str | None = None
    evidence_step_id: str | None = None
    claim_id: str | None = None

    @property
    def needs_backfill(self) -> bool:
        return self.existing_claim_id is None


@dataclass
class BackfillPlan:
    plan_id: str
    steps: list[StepPlan] = field(default_factory=list)

    @property
    def pending(self) -> list[StepPlan]:
        return [step for step in self.steps if step.needs_backfill]


def _claim_for_excerpt(
    excerpt: str, knowledge: dict[str, Any]
) -> str | None:
    """Return a claim already backed by this excerpt, if one exists.

    Matching is containment in either direction: an extracted fragment may be
    a longer sentence than the contract quoted, or the contract may quote a
    span covering several fragments.
    """

    fragments = {
        f["fragment_id"]: str(f.get("verbatim_excerpt") or "")
        for f in knowledge.get("source_fragments", [])
    }
    matching_fragments = {
        fragment_id
        for fragment_id, text in fragments.items()
        if text and (excerpt in text or text in excerpt)
    }
    if not matching_fragments:
        return None
    evidence_ids = {
        step["evidence_step_id"]
        for step in knowledge.get("evidence_steps", [])
        if set(step.get("source_fragment_ids") or [step.get("source_fragment_id")])
        & matching_fragments
    }
    if not evidence_ids:
        return None
    for claim in knowledge.get("claims", []):
        if set(claim.get("evidence_step_ids") or []) & evidence_ids:
            return str(claim["claim_id"])
    return None


def plan_backfill(
    plan_payload: dict[str, Any],
    knowledge: dict[str, Any],
    *,
    id_prefix: str,
) -> BackfillPlan:
    """Decide, without writing anything, which steps need new claims."""

    plan = BackfillPlan(plan_id=str(plan_payload.get("plan_id")))
    base_source_id = str((plan_payload.get("base_source") or {}).get("source_id") or "")
    sequence = 0
    for section in plan_payload.get("authoring_sections") or []:
        for step in section.get("required_argument_steps") or []:
            excerpt = str(step.get("source_excerpt") or "")
            if not excerpt:
                raise RequiredStepBackfillError(
                    f"required step has no source_excerpt: {step.get('step_id')}"
                )
            entry = StepPlan(
                step_id=str(step["step_id"]),
                source_id=str(step.get("source_id") or base_source_id),
                source_excerpt=excerpt,
                statement=str(step.get("statement") or ""),
                existing_claim_id=_claim_for_excerpt(excerpt, knowledge),
            )
            if entry.needs_backfill:
                sequence += 1
                suffix = f"{id_prefix}-{sequence:02d}"
                entry.fragment_id = f"FR-STEP-{suffix}"
                entry.evidence_step_id = f"ES-STEP-{suffix}"
                entry.claim_id = f"CL-STEP-{suffix}"
            plan.steps.append(entry)
    return plan


def verify_excerpts(plan: BackfillPlan, source_paths: dict[str, Path]) -> list[str]:
    """Return a failure message per step whose excerpt is not verbatim in its source."""

    failures: list[str] = []
    cache: dict[str, str] = {}
    for step in plan.pending:
        path = source_paths.get(step.source_id)
        if path is None:
            failures.append(f"{step.step_id}: source_id {step.source_id!r} has no known path")
            continue
        if step.source_id not in cache:
            cache[step.source_id] = path.read_text(encoding="utf-8")
        if step.source_excerpt not in cache[step.source_id]:
            failures.append(
                f"{step.step_id}: source_excerpt is not a verbatim substring of {step.source_id}"
            )
    return failures


def build_backfill_package(
    plan: BackfillPlan, plan_payload: dict[str, Any], *, source_documents: list[dict[str, Any]]
) -> dict[str, Any]:
    fragments: list[dict[str, Any]] = []
    evidence_steps: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []

    for step in plan.pending:
        fragments.append(
            {
                "fragment_id": step.fragment_id,
                "source_id": step.source_id,
                "verbatim_excerpt": step.source_excerpt,
                "verbatim_excerpt_sha256": sha256_text(step.source_excerpt),
                "anchor_state": "source_version_bound",
                "backfilled_for_step_id": step.step_id,
            }
        )
        evidence_steps.append(
            {
                "evidence_step_id": step.evidence_step_id,
                "source_fragment_id": step.fragment_id,
                # The claim states what the professor said, not what the
                # author must do with it. A step's `statement` is an editorial
                # instruction ("preserve the full mission content", "do not
                # reduce the messiah to a title"); storing that as a claim
                # makes the grounding gate compare prose against a directive
                # rather than against the material, and correctly rejects
                # faithful sentences for not restating the instruction. The
                # instruction stays on the step, where it belongs.
                "statement": step.source_excerpt,
                "step_type": "reasoning",
                "speaker": "professor",
                "stance": "asserted",
                "discourse_role": f"required_argument_step:{step.step_id}",
                "support_eligibility": "eligible_candidate",
                "produced_claim_ids": [step.claim_id],
            }
        )
        claims.append(
            {
                "claim_id": step.claim_id,
                "statement": step.source_excerpt,
                "editorial_instruction": step.statement,
                "claim_type": "base_manuscript_argument_step",
                "attribution": "professor",
                "evidence_step_ids": [step.evidence_step_id],
                "maturity": "candidate",
                "backfilled_for_step_id": step.step_id,
                "backfilled_for_plan_id": plan.plan_id,
            }
        )

    return {
        "package_id": f"REQUIRED-STEP-BACKFILL-{plan.plan_id}",
        "source_documents": source_documents,
        "source_fragments": fragments,
        "evidence_steps": evidence_steps,
        "claims": claims,
    }


def apply_claim_ids_to_plan(
    plan_payload: dict[str, Any], plan: BackfillPlan
) -> dict[str, Any]:
    """Return the plan payload with each step carrying its claim_id.

    The author needs to know which claim backs each obligation; without this
    the claim exists but nothing connects it to the step that requires it.
    """

    claim_by_step = {
        step.step_id: (step.existing_claim_id or step.claim_id) for step in plan.steps
    }
    updated = json.loads(json.dumps(plan_payload, ensure_ascii=False))
    for section in updated.get("authoring_sections") or []:
        for step in section.get("required_argument_steps") or []:
            claim_id = claim_by_step.get(str(step.get("step_id")))
            if claim_id:
                step["claim_id"] = claim_id
    return updated


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-id", required=True)
    parser.add_argument(
        "--id-prefix", required=True,
        help="Short stable token for generated ids, e.g. M16-003.",
    )
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore

    store = PostgresKnowledgeStore()
    plan_payload = store.get_record("composition_plans", args.plan_id)
    if plan_payload is None:
        raise RequiredStepBackfillError(f"plan not found: {args.plan_id}")
    knowledge = store.compile_package(package_id="BACKFILL-READ")

    plan = plan_backfill(plan_payload, knowledge, id_prefix=args.id_prefix)
    source_documents = {
        str(d.get("source_id")): d for d in knowledge.get("source_documents", [])
    }
    source_paths = {
        source_id: Path(doc["source_path"])
        for source_id, doc in source_documents.items()
        if doc.get("source_path")
    }
    failures = verify_excerpts(plan, source_paths)

    for step in plan.steps:
        state = (
            f"已有 claim {step.existing_claim_id}"
            if step.existing_claim_id
            else f"需要建立 {step.claim_id}"
        )
        print(f"  {step.step_id:<16} {state}")
    if failures:
        print("\n逐字驗證失敗，沒有寫入任何資料：")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    if not plan.pending:
        print("\n全部承重步驟都已有 claim，無需補建。")
        return 0
    if not args.apply:
        print(f"\n驗證通過，{len(plan.pending)} 條需要補建。加 --apply 才會寫入。")
        return 0

    used_source_ids = {step.source_id for step in plan.pending}
    package = build_backfill_package(
        plan,
        plan_payload,
        source_documents=[
            source_documents[source_id]
            for source_id in sorted(used_source_ids)
            if source_id in source_documents
        ],
    )
    change = store.apply_plan(
        store.plan_package(package, source_kind="required_step_claim_backfill"),
        metadata={"backfill": plan.plan_id},
    )
    print(json.dumps(change, ensure_ascii=False, indent=1, default=str))

    updated_plan = apply_claim_ids_to_plan(plan_payload, plan)
    decisions = [
        store.get_record("composition_decisions", decision_id)
        for decision_id in updated_plan.get("decision_ids") or []
    ]
    updated_plan["decisions"] = [d for d in decisions if d]
    plan_change = store.apply_plan(
        store.plan_package(
            {"package_id": f"REQUIRED-STEP-CLAIMIDS-{plan.plan_id}", "product_plans": [updated_plan]},
            source_kind="required_step_claim_backfill",
        ),
        metadata={"backfill_plan_update": plan.plan_id},
    )
    print(json.dumps(plan_change, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
