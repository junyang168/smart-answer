"""Connect an observation to the step of the professor's argument it carries.

Two shapes of repair, from a declarative spec so every link is reviewable
before anything is written.

`link` is for an observation whose step was already extracted, just never
associated: Caesarea Philippi's geography and its founding sit in the same
paragraph as the step that reasons from them ("this place emphasises the
emperor's authority; Jesus asks here, so: obey Caesar or obey Jesus?"), and
the only thing missing is the edge.  Nothing new is asserted.

`step` is for an observation whose inference was never extracted at all.  The
professor states the fact and draws the conclusion in the same breath, and the
extraction kept only the fact -- so the conclusion has to be recorded, quoted
from the transcript rather than composed.  Matt 16:18 is this case: the
professor says the original word is "the gates of Hades" and immediately gives
its force, and because that second half never became a claim, the published
article carries a note saying the material does not develop the phrase.  It
does; nothing had recorded it.

Every excerpt is verified verbatim against the segment it names before
anything is written, new ids are refused if they already exist, and claims are
written `candidate` -- promoting the professor's exegesis is a human decision.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


class LinkingError(RuntimeError):
    """Raised when a link cannot be made without inventing or overwriting."""


@dataclass(frozen=True)
class Link:
    """An observation joined to an evidence step that already exists."""

    observation_id: str
    evidence_step_id: str
    relation_id: str
    reason: str
    relation_type: str = "supports"


@dataclass(frozen=True)
class Step:
    """An observation whose inference must be recorded before it can be joined."""

    observation_id: str
    source_id: str
    segment_index: str
    excerpt: str
    statement: str
    claim_statement: str
    claim_type: str
    fragment_id: str
    evidence_step_id: str
    claim_id: str
    relation_id: str
    reason: str
    step_type: str = "original_language"
    relation_type: str = "supports"


@dataclass
class LinkingPlan:
    links: list[Link] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)


def load_plan(payload: dict[str, Any]) -> LinkingPlan:
    return LinkingPlan(
        links=[Link(**row) for row in payload.get("links", [])],
        steps=[Step(**row) for row in payload.get("steps", [])],
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise LinkingError(message)


def _segment_text(transcript: dict[str, Any], segment_index: str) -> str:
    segments = {
        f"S{index + 1:04d}": str(segment.get("text") or "")
        for index, segment in enumerate(transcript.get("script", []))
    }
    _require(segment_index in segments, f"unknown segment {segment_index}")
    return segments[segment_index]


def build_linking_package(
    plan: LinkingPlan, *, store: Any, transcripts: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Return the package that records these links, or raise before writing.

    `transcripts` maps source_id to its parsed transcript, and is only needed
    for `step` entries -- the excerpt each one quotes is checked against the
    segment it names, so a conclusion can be recorded but never composed.
    """

    relations: list[dict[str, Any]] = []
    fragments: list[dict[str, Any]] = []
    evidence_steps: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []

    for link in plan.links:
        observation = store.get_record("observations", link.observation_id)
        _require(observation is not None, f"observation not found: {link.observation_id}")
        step = store.get_record("evidence_steps", link.evidence_step_id)
        _require(step is not None, f"evidence step not found: {link.evidence_step_id}")
        _require(
            store.get_record("knowledge_relations", link.relation_id) is None,
            f"relation_id already exists, refusing to overwrite: {link.relation_id}",
        )
        _require(bool(link.reason.strip()), f"{link.relation_id}: reason is required")
        relations.append({
            "relation_id": link.relation_id,
            "from_id": link.observation_id,
            "to_id": link.evidence_step_id,
            "relation_type": link.relation_type,
            "reason": link.reason,
            "review_status": "candidate",
        })

    for step in plan.steps:
        observation = store.get_record("observations", step.observation_id)
        _require(observation is not None, f"observation not found: {step.observation_id}")
        for collection, object_id in (
            ("source_fragments", step.fragment_id),
            ("evidence_steps", step.evidence_step_id),
            ("claims", step.claim_id),
            ("knowledge_relations", step.relation_id),
        ):
            _require(
                store.get_record(collection, object_id) is None,
                f"{collection}/{object_id} already exists, refusing to overwrite",
            )
        _require(bool(step.reason.strip()), f"{step.relation_id}: reason is required")

        transcript = transcripts.get(step.source_id)
        _require(transcript is not None, f"transcript not supplied for {step.source_id}")
        text = _segment_text(transcript, step.segment_index)
        _require(
            step.excerpt in text,
            f"{step.evidence_step_id}: excerpt is not verbatim in "
            f"{step.source_id} {step.segment_index}",
        )

        scripture_refs = observation.get("scripture_refs") or []
        fragments.append({
            "fragment_id": step.fragment_id,
            "source_id": step.source_id,
            "paragraph_key": step.segment_index,
            "verbatim_excerpt": step.excerpt,
            "anchor_state": "source_version_bound",
            "review_status": "candidate",
        })
        evidence_steps.append({
            "evidence_step_id": step.evidence_step_id,
            "source_fragment_id": step.fragment_id,
            "statement": step.statement,
            "step_type": step.step_type,
            "speaker": "professor",
            "stance": "asserted",
            "discourse_role": f"inference_recorded_for_observation:{step.observation_id}",
            "support_eligibility": "eligible_candidate",
            "produced_claim_ids": [step.claim_id],
            "scripture_refs": scripture_refs,
            "review_status": "candidate",
        })
        claims.append({
            "claim_id": step.claim_id,
            "statement": step.claim_statement,
            "claim_type": step.claim_type,
            "attribution": "professor",
            "scripture_refs": scripture_refs,
            "evidence_step_ids": [step.evidence_step_id],
            "maturity": "candidate",
            "review_status": "candidate",
        })
        relations.append({
            "relation_id": step.relation_id,
            "from_id": step.observation_id,
            "to_id": step.evidence_step_id,
            "relation_type": step.relation_type,
            "reason": step.reason,
            "review_status": "candidate",
        })

    return {
        "package_id": "OBSERVATION-ARGUMENT-LINKING-V1",
        "source_documents": [],
        "source_fragments": fragments,
        "evidence_steps": evidence_steps,
        "claims": claims,
        "knowledge_relations": relations,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument(
        "--transcript", action="append", default=[], metavar="SOURCE_ID=PATH",
        help="Transcript for a source referenced by a `step` entry. Repeatable.",
    )
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    plan = load_plan(json.loads(args.plan.read_text("utf-8")))

    transcripts: dict[str, dict[str, Any]] = {}
    for entry in args.transcript:
        source_id, _, raw_path = entry.partition("=")
        path = Path(raw_path)
        if path.suffix == ".md":
            # Segment a notes manuscript exactly as extraction did, so the
            # segment a link names is the segment its fragments were anchored
            # to.  Re-splitting it any other way would shift every S-number.
            from backend.pipeline.knowledge_source import markdown_source_document

            transcripts[source_id], _, _ = markdown_source_document(
                {"source_path": str(path), "source_id": source_id}
            )
        else:
            transcripts[source_id] = json.loads(path.read_text("utf-8"))

    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore

    store = PostgresKnowledgeStore()
    package = build_linking_package(plan, store=store, transcripts=transcripts)

    print(f"relations       {len(package['knowledge_relations'])}")
    print(f"evidence steps  {len(package['evidence_steps'])}")
    print(f"claims          {len(package['claims'])}")
    for relation in package["knowledge_relations"]:
        print(f"\n  {relation['from_id']} -> {relation['to_id']}")
        print(f"    {relation['reason']}")
    for claim in package["claims"]:
        print(f"\n  new claim {claim['claim_id']}")
        print(f"    {claim['statement']}")

    if not args.apply:
        print("\n未寫入。加 --apply 才會寫進 PostgreSQL。")
        return 0

    change_set = store.plan_package(package, source_kind="observation_argument_linking")
    result = store.apply_plan(change_set, metadata={"plan": str(args.plan)})
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
