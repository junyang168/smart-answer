"""Decide whether an unlinked observation's content is in the argument layer.

`observation_argument_coverage` answers a structural question -- does an
evidence step in this paragraph quote this observation's sentence -- and that
question has a known blind spot: the professor routinely states a fact in one
paragraph and draws the conclusion several paragraphs later, and the same
point is often extracted again from a second sermon on the same passage.  Both
read as gaps.  So its two large buckets are upper bounds on what was lost, and
the count of actual loss is unknown.

The question that settles it is semantic: is this observation's content
carried by any claim?  This module puts that question to a model, with two
constraints that keep the answer checkable.  A `covered` verdict must name the
claims that carry it, so "yes" cannot be a shrug; and only claims present in
the packet may be named, so it cannot invent an alibi.  The output is a
proposal for review, never a write -- deciding that a piece of the professor's
exegesis is safely represented is an editorial judgment.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional

from backend.pipeline.observation_argument_coverage import (
    REACHED,
    measure_coverage,
    observations_for_passage,
)

ADJUDICATION_VERSION = "wang_observation_coverage_adjudication_v1"
VERDICTS = ["covered", "not_covered"]

PROMPT_PATH = Path(__file__).with_name("prompts") / "observation_coverage_adjudication.md"

RESPONSE_SCHEMA: dict[str, Any] = {
    "name": ADJUDICATION_VERSION,
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "observation_id": {"type": "string"},
                        "verdict": {"type": "string", "enum": VERDICTS},
                        "covering_claim_ids": {"type": "array", "items": {"type": "string"}},
                        "reason": {"type": "string"},
                    },
                    "required": ["observation_id", "verdict", "covering_claim_ids", "reason"],
                },
            },
        },
        "required": ["verdicts"],
    },
}


class AdjudicationValidationError(ValueError):
    pass


def pending_observations(package: dict[str, Any]) -> list[dict[str, Any]]:
    """The observations whose status the structural measure could not settle."""

    report = measure_coverage(package)
    unsettled = {
        row["observation_id"]
        for row in report["observations"]
        if row["status"] not in REACHED
    }
    return [
        row for row in package.get("observations", [])
        if str(row.get("observation_id")) in unsettled
    ]


def build_packet(
    package: dict[str, Any], observations: list[dict[str, Any]], *, scope: str
) -> dict[str, Any]:
    """Assemble the observations to judge and the claims they may be covered by.

    Claims carry the statements of the evidence steps behind them: an
    observation is often covered by the wording of a step rather than by the
    claim's own summary, and withholding that would produce false gaps.
    """

    steps = {
        str(row.get("evidence_step_id")): str(row.get("statement") or "")
        for row in package.get("evidence_steps", [])
    }
    claims = []
    for row in package.get("claims", []):
        claims.append({
            "claim_id": str(row.get("claim_id")),
            "statement": str(row.get("statement") or row.get("title") or ""),
            "evidence_statements": [
                steps[str(step_id)]
                for step_id in row.get("evidence_step_ids") or []
                if str(step_id) in steps
            ],
        })
    return {
        "scope": scope,
        "observations": [
            {
                "observation_id": str(row.get("observation_id")),
                "observation_type": row.get("observation_type"),
                "statement": str(row.get("statement") or ""),
                "scripture_refs": row.get("scripture_refs") or [],
            }
            for row in observations
        ],
        "claims": claims,
    }


def validate_adjudication(response: dict[str, Any], packet: dict[str, Any]) -> None:
    """Reject a verdict set that cannot be checked against the packet."""

    errors: list[str] = []
    expected = {row["observation_id"] for row in packet["observations"]}
    claim_ids = {row["claim_id"] for row in packet["claims"]}
    seen: set[str] = set()

    for row in response.get("verdicts", []):
        observation_id = str(row.get("observation_id") or "")
        if observation_id not in expected:
            errors.append(f"{observation_id}: not an observation in this packet")
            continue
        if observation_id in seen:
            errors.append(f"{observation_id}: judged more than once")
        seen.add(observation_id)

        covering = [str(value) for value in row.get("covering_claim_ids") or []]
        unknown = sorted(set(covering) - claim_ids)
        if unknown:
            errors.append(f"{observation_id}: unknown claim {', '.join(unknown)}")
        if row.get("verdict") == "covered" and not covering:
            errors.append(f"{observation_id}: covered without naming a claim")
        if row.get("verdict") == "not_covered" and covering:
            errors.append(f"{observation_id}: not_covered but names claims")
        if not str(row.get("reason") or "").strip():
            errors.append(f"{observation_id}: reason is required")

    missing = sorted(expected - seen)
    if missing:
        errors.append(f"not judged: {', '.join(missing)}")
    if errors:
        raise AdjudicationValidationError(
            "adjudication validation failed: " + " | ".join(errors)
        )


def summarize(response: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    """Fold the verdicts into the finding: how much was really lost."""

    statements = {row["observation_id"]: row["statement"] for row in packet["observations"]}
    verdicts = list(response.get("verdicts", []))
    not_covered = [row for row in verdicts if row.get("verdict") == "not_covered"]
    return {
        "schema_version": ADJUDICATION_VERSION,
        "scope": packet["scope"],
        "totals": {
            "judged": len(verdicts),
            "covered": len(verdicts) - len(not_covered),
            "not_covered": len(not_covered),
        },
        "not_covered": [
            {
                "observation_id": row["observation_id"],
                "statement": statements.get(row["observation_id"], ""),
                "reason": row.get("reason", ""),
            }
            for row in not_covered
        ],
        "verdicts": verdicts,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=None)
    parser.add_argument("--book", default="Matt")
    parser.add_argument("--chapter", type=int, default=None)
    parser.add_argument("--start", type=int, default=None)
    parser.add_argument("--end", type=int, default=None)
    parser.add_argument("--output", type=Path, default=None)
    # The same reasoning model the extraction and review runners use; the
    # shared client only sends reasoning_effort for the gpt-5.6 family.
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--max-output-tokens", type=int, default=32000)
    parser.add_argument(
        "--packet-only", action="store_true",
        help="Write the packet and stop, without calling a model.",
    )
    return parser.parse_args(argv)


def _load_package(path: Optional[Path]) -> dict[str, Any]:
    if path:
        return json.loads(path.read_text("utf-8"))
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore

    return PostgresKnowledgeStore().compile_package()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    package = _load_package(args.package)

    scope = "whole store"
    if args.chapter is not None:
        from backend.pipeline.passage_knowledge_slice import Passage, reference_overlaps

        passage = Passage(args.book, args.chapter, args.start, args.end)
        scope = passage.display

        def in_scope(row: dict[str, Any]) -> bool:
            return any(
                reference_overlaps(str(ref), passage)
                for ref in row.get("scripture_refs") or []
            )

        package = {
            **package,
            "observations": observations_for_passage(package, passage),
            "claims": [row for row in package.get("claims", []) if in_scope(row)],
        }

    observations = pending_observations(package)
    packet = build_packet(package, observations, scope=scope)
    print(f"scope        {scope}")
    print(f"to judge     {len(packet['observations'])} observations")
    print(f"against      {len(packet['claims'])} claims")

    if args.packet_only:
        if args.output:
            args.output.write_text(
                json.dumps(packet, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
            )
            print(f"wrote packet {args.output}")
        return 0

    from dotenv import load_dotenv

    from backend.pipeline.stage1 import Stage1OpenAIClient

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    client = Stage1OpenAIClient(
        model=args.model, reasoning_effort=args.reasoning_effort,
        timeout_seconds=600, max_retries=3, max_output_tokens=args.max_output_tokens,
    )
    response = client.generate_json(
        PROMPT_PATH.read_text(encoding="utf-8"),
        json.dumps(packet, ensure_ascii=False),
        RESPONSE_SCHEMA,
    )
    validate_adjudication(response, packet)
    report = summarize(response, packet)

    totals = report["totals"]
    print(f"\ncovered      {totals['covered']}")
    print(f"not covered  {totals['not_covered']}   <- the real gap")
    for row in report["not_covered"]:
        print(f"\n  {row['statement'][:82]}")
        print(f"    {row['reason'][:98]}")

    if args.output:
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
