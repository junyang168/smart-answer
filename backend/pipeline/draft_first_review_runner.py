"""Review a draft-first essay through three gates, revising until clean or stopped.

The generation side (#283) writes with no Brief and no author-declared
provenance, so verification happens entirely after the fact:

- alignment (gpt-5.6-sol / codex): every asserted sentence must be covered by
  the approved viewpoints or the sources; modality may not be upgraded;
  the sources' silence may not be re-attributed to Scripture; quotes verbatim.
- blind read (claude-haiku-4-5): a reader with no background restates the
  question and the answer; a separate closed comparison (gpt) checks that what
  reached the reader is what the viewpoint registry meant. The small model is
  the point — it stands in for the ordinary reader.
- editorial review (claude-fable-5): completeness, argument closure, and the
  restraint-vs-flesh balance, scored against the WQ profile's dimensions and
  hard failures. Author (opus) and reviewer are different models throughout.

Blocking findings from any gate go back to the author model for a minimal
revision, then every gate reruns on the revised text; at most two revision
rounds, then the run stops for a human. Every model call goes through a
subscription CLI — no API-key client is constructed here.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
from backend.pipeline.draft_first_author_runner import (
    argument_route_charter,
    source_texts,
    structure_unresolved_items,
    verbatim_quote_report,
    viewpoint_charter,
)
from backend.pipeline.draft_first_source_binding import reader_paragraphs
from backend.pipeline.matthew_exposition_authoring import sha256_text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPTS = Path(__file__).with_name("prompts")
MAX_REVISION_ROUNDS = 2

ALIGNMENT_SCHEMA: dict[str, Any] = {
    "name": "wang_draft_first_alignment_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "quote": {"type": "string"},
                        "kind": {
                            "type": "string",
                            "enum": [
                                "beyond_source",
                                "modality_exceeded",
                                "attribution_swap",
                            ],
                        },
                        "note": {"type": "string"},
                    },
                    "required": ["quote", "kind", "note"],
                },
            },
            "notes": {"type": "string"},
        },
        "required": ["findings", "notes"],
    },
}

BLIND_READ_SCHEMA: dict[str, Any] = {
    "name": "wang_draft_first_blind_read_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "question_as_read": {"type": "string"},
            "answer_in_one_sentence": {"type": "string"},
            "qualifications_kept": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "question_as_read",
            "answer_in_one_sentence",
            "qualifications_kept",
        ],
    },
}

BLIND_COMPARE_SCHEMA: dict[str, Any] = {
    "name": "wang_draft_first_blind_compare_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "answer_matches_settled_positions": {"type": "boolean"},
            "modality_preserved": {"type": "boolean"},
            "mismatches": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "answer_matches_settled_positions",
            "modality_preserved",
            "mismatches",
        ],
    },
}


def _review_schema(profile: Mapping[str, Any]) -> dict[str, Any]:
    dimension_ids = [str(item["id"]) for item in profile["dimensions"]]
    failure_ids = [str(item) for item in profile["hard_failures"]]
    return {
        "name": "wang_draft_first_editorial_review_v1",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "summary": {"type": "string"},
                "dimension_scores": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "dimension_id": {"type": "string", "enum": dimension_ids},
                            "score": {"type": "integer"},
                            "evidence": {"type": "string"},
                        },
                        "required": ["dimension_id", "score", "evidence"],
                    },
                },
                "hard_failure_assessments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "failure_id": {"type": "string", "enum": failure_ids},
                            "failed": {"type": "boolean"},
                            "evidence": {"type": "string"},
                        },
                        "required": ["failure_id", "failed", "evidence"],
                    },
                },
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "anchor": {"type": "string"},
                            "dimension_id": {"type": "string", "enum": dimension_ids},
                            "summary": {"type": "string"},
                            "required_change": {"type": "string"},
                            "blocking": {"type": "boolean"},
                        },
                        "required": [
                            "anchor",
                            "dimension_id",
                            "summary",
                            "required_change",
                            "blocking",
                        ],
                    },
                },
            },
            "required": [
                "summary",
                "dimension_scores",
                "hard_failure_assessments",
                "findings",
            ],
        },
    }


REVISION_SCHEMA: dict[str, Any] = {
    "name": "wang_draft_first_revision_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "manuscript_markdown": {"type": "string"},
            "dispositions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "finding_anchor": {"type": "string"},
                        "disposition": {
                            "type": "string",
                            "enum": ["resolved", "cannot_fix_within_sources"],
                        },
                        "note": {"type": "string"},
                    },
                    "required": ["finding_anchor", "disposition", "note"],
                },
            },
        },
        "required": ["manuscript_markdown", "dispositions"],
    },
}


def _call(client: Any, prompt_path: Path, payload: dict[str, Any], schema: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one gate call and return (result, fingerprint) — every judgment traceable."""

    prompt = prompt_path.read_text(encoding="utf-8")
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    result = client.generate_json(prompt, payload_json, schema)
    fingerprint = {
        "prompt": prompt_path.name,
        "prompt_sha256": sha256_text(prompt),
        "payload_sha256": sha256_text(payload_json),
        "schema": schema.get("name"),
        "model": client.model,
        "backend": client.backend,
        "reasoning_effort": client.reasoning_effort,
    }
    return result, fingerprint


def merge_delta_scores(
    baseline_review: Mapping[str, Any], delta_review: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Inherit unaffected dimension scores programmatically (#302).

    A delta reviewer told to copy the baseline may or may not comply; the
    runner therefore copies for it. Only dimensions where the delta round's
    own findings land keep the delta reviewer's score — affectedness comes
    from actual finding locations, never a static coupling table (#33's
    documented mistake). Hard failures stay re-assessed each round: a
    one-vote veto is exactly what must not be inherited blindly.
    """

    # Affected = where this round's findings land, plus where the previous
    # round's blocking findings landed — those dimensions were just revised,
    # so their baseline score is exactly the one that must not be inherited.
    affected = {str(item["dimension_id"]) for item in delta_review["findings"]}
    affected |= {
        str(item["dimension_id"])
        for item in baseline_review.get("findings") or []
        if item.get("blocking")
    }
    baseline_scores = {
        str(item["dimension_id"]): item for item in baseline_review["dimension_scores"]
    }
    merged_scores = []
    inherited: list[str] = []
    rescored: list[str] = []
    for item in delta_review["dimension_scores"]:
        dimension_id = str(item["dimension_id"])
        if dimension_id in affected or dimension_id not in baseline_scores:
            merged_scores.append(item)
            rescored.append(dimension_id)
        else:
            merged_scores.append(baseline_scores[dimension_id])
            inherited.append(dimension_id)
    merged = {**delta_review, "dimension_scores": merged_scores}
    provenance = {"rescored_dimensions": sorted(rescored), "inherited_dimensions": sorted(inherited)}
    return merged, provenance


def result_sha256(result: Mapping[str, Any]) -> str:
    """Content hash of one gate's output, bound into the run record (#302)."""

    return sha256_text(json.dumps(result, ensure_ascii=False, sort_keys=True))


def changed_and_ending_paragraphs(
    baseline: str, revised: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Paragraphs new in the revision, and the revision's actual ending.

    The old pipeline paid twice for these lessons: rerunning full reviewers
    every round re-rolls their standards, and judging the ending from a diff
    misses an unchanged closing paragraph sitting after an insertion. Delta
    rounds therefore review exactly the changed paragraphs plus the last two
    reader paragraphs, always re-read from the revised text itself.
    """

    baseline_shas = {p["paragraph_sha256"] for p in reader_paragraphs(baseline)}
    revised_paragraphs = reader_paragraphs(revised)
    changed = [p for p in revised_paragraphs if p["paragraph_sha256"] not in baseline_shas]
    ending = revised_paragraphs[-2:]
    return changed, ending


class DraftReviewContractError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DraftReviewContractError(message)


def validate_alignment(result: Mapping[str, Any], *, manuscript: str) -> None:
    for finding in result["findings"]:
        _require(
            str(finding["quote"]) in manuscript,
            f"alignment quote not verbatim in manuscript: {finding['quote'][:60]!r}",
        )


def validate_review(
    review: Mapping[str, Any], *, manuscript: str, profile: Mapping[str, Any]
) -> dict[str, Any]:
    """Check the review's own discipline, then derive the verdict."""

    weights = {str(item["id"]): item for item in profile["dimensions"]}
    seen = [str(item["dimension_id"]) for item in review["dimension_scores"]]
    _require(
        sorted(seen) == sorted(weights),
        "review must score every dimension exactly once",
    )
    below_minimum: list[str] = []
    for item in review["dimension_scores"]:
        spec = weights[str(item["dimension_id"])]
        _require(
            0 <= int(item["score"]) <= int(spec["weight"]),
            f"score outside weight for {item['dimension_id']}",
        )
        if int(item["score"]) < int(spec["minimum"]):
            below_minimum.append(str(item["dimension_id"]))
    failure_ids = [str(item) for item in profile["hard_failures"]]
    assessed = [str(item["failure_id"]) for item in review["hard_failure_assessments"]]
    _require(
        sorted(assessed) == sorted(failure_ids),
        "review must assess every hard failure exactly once",
    )
    failed = [
        str(item["failure_id"])
        for item in review["hard_failure_assessments"]
        if item["failed"]
    ]
    # Reviewers quote what the reader sees; the manuscript carries Markdown.
    # An anchor inside a blockquote or bold run ('> **編者按**：…') is verbatim
    # in the rendered text while failing a raw-byte match, and that mismatch
    # once crashed a whole review run instead of anchoring one finding.
    rendered = re.sub(r"^>\s?", "", manuscript, flags=re.MULTILINE)
    rendered = rendered.replace("**", "").replace("*", "")
    for finding in review["findings"]:
        anchor = str(finding["anchor"])
        _require(
            anchor in manuscript or anchor in rendered,
            f"review anchor not verbatim in manuscript: {finding['anchor'][:60]!r}",
        )
    # A dimension below its minimum or a declared hard failure without any
    # blocking finding would stall the revision loop with nothing to fix.
    if below_minimum or failed:
        _require(
            any(finding["blocking"] for finding in review["findings"]),
            "failing review must carry at least one blocking finding",
        )
    return {"dimensions_below_minimum": below_minimum, "hard_failures_failed": failed}


def merge_blocking_findings(
    *,
    alignment: Mapping[str, Any],
    blind_compare: Mapping[str, Any],
    review: Mapping[str, Any],
    review_verdict: Mapping[str, Any],
    quote_report: Mapping[str, Any],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for item in alignment["findings"]:
        findings.append(
            {
                "gate": "alignment",
                "kind": str(item["kind"]),
                "anchor": str(item["quote"]),
                "summary": str(item["note"]),
            }
        )
    for quote in quote_report.get("quotes_failing") or []:
        anchor = f"「{quote}」"
        if not any(f["anchor"] == anchor or f["anchor"] == quote for f in findings):
            findings.append(
                {
                    "gate": "quote_check",
                    "kind": "unverbatim_quote",
                    "anchor": quote,
                    "summary": "引文与原文不逐字一致；改为逐字引用，或提及用法去掉引号改为转述。",
                }
            )
    for quote in quote_report.get("long_quotes_not_blockquoted") or []:
        if not any(f["anchor"] == quote for f in findings):
            findings.append(
                {
                    "gate": "quote_check",
                    "kind": "long_quote_not_blockquoted",
                    "anchor": quote,
                    "summary": "整句／整节长引文须用 Markdown 引文块（>）单独成块呈现，"
                    "不得留在行内引号里；短语提及不受此限。",
                }
            )
    if not (
        blind_compare["answer_matches_settled_positions"]
        and blind_compare["modality_preserved"]
    ):
        findings.append(
            {
                "gate": "blind_read",
                "kind": "reader_path_broken",
                "anchor": "",
                "summary": "盲读者未收到应传达的答案或模态："
                + "；".join(str(x) for x in blind_compare["mismatches"]),
            }
        )
    for item in review["findings"]:
        if item["blocking"]:
            findings.append(
                {
                    "gate": "editorial_review",
                    "kind": str(item["dimension_id"]),
                    "anchor": str(item["anchor"]),
                    "summary": f"{item['summary']}｜required: {item['required_change']}",
                }
            )
    return findings


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def run_gates(
    *,
    manuscript: str,
    packet: Mapping[str, Any],
    profile: Mapping[str, Any],
    clients: Mapping[str, Any],
    round_dir: Path,
    baseline_manuscript: str | None = None,
    baseline_review: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    charter = viewpoint_charter(dict(packet))
    unresolved = structure_unresolved_items(dict(packet))
    routes = argument_route_charter(dict(packet))
    sources = source_texts(dict(packet))
    fingerprints: dict[str, Any] = {}

    delta_scope: dict[str, Any] | None = None
    if baseline_manuscript is not None:
        changed, ending = changed_and_ending_paragraphs(baseline_manuscript, manuscript)
        delta_scope = {
            "mode": "delta",
            "changed_paragraphs": [p["text"] for p in changed],
            "ending_paragraphs": [p["text"] for p in ending],
        }

    alignment_payload = {
        "manuscript_markdown": manuscript,
        "approved_viewpoints": charter,
        "unresolved_items": unresolved,
        "source_originals": sources,
    }
    if delta_scope is not None:
        alignment_payload["review_scope"] = delta_scope
    alignment, fingerprints["alignment"] = _call(
        clients["alignment"],
        PROMPTS / "draft_first_alignment_check.md",
        alignment_payload,
        ALIGNMENT_SCHEMA,
    )
    validate_alignment(alignment, manuscript=manuscript)
    fingerprints["alignment"]["result_sha256"] = result_sha256(alignment)
    _write(round_dir / "alignment.json", alignment)

    blind_read, fingerprints["blind_read"] = _call(
        clients["blind_read"],
        PROMPTS / "draft_first_blind_read.md",
        {"article": manuscript},
        BLIND_READ_SCHEMA,
    )
    fingerprints["blind_read"]["result_sha256"] = result_sha256(blind_read)
    _write(round_dir / "blind-read.json", blind_read)

    blind_compare, fingerprints["blind_compare"] = _call(
        clients["blind_compare"],
        PROMPTS / "draft_first_blind_compare.md",
        {"approved_viewpoints": charter, "unresolved_items": unresolved, "blind_read": blind_read},
        BLIND_COMPARE_SCHEMA,
    )
    fingerprints["blind_compare"]["result_sha256"] = result_sha256(blind_compare)
    _write(round_dir / "blind-compare.json", blind_compare)

    review_payload = {
        "manuscript_markdown": manuscript,
        "approved_viewpoints": charter,
        "unresolved_items": unresolved,
        "argument_routes": routes,
        "source_originals": sources,
        "quality_profile": profile,
    }
    if delta_scope is not None:
        review_payload["review_scope"] = delta_scope
        review_payload["baseline_review"] = dict(baseline_review or {})
    review, fingerprints["editorial"] = _call(
        clients["editorial"],
        PROMPTS / "draft_first_editorial_review.md",
        review_payload,
        _review_schema(profile),
    )
    if delta_scope is not None and baseline_review is not None:
        review, score_provenance = merge_delta_scores(baseline_review, review)
        review["score_provenance"] = score_provenance
    review_verdict = validate_review(review, manuscript=manuscript, profile=profile)
    fingerprints["editorial"]["result_sha256"] = result_sha256(review)
    _write(round_dir / "editorial-review.json", review)

    quote_report = verbatim_quote_report(manuscript, dict(packet))
    blocking = merge_blocking_findings(
        alignment=alignment,
        blind_compare=blind_compare,
        review=review,
        review_verdict=review_verdict,
        quote_report=quote_report,
    )
    outcome = {
        "manuscript_sha256": sha256_text(manuscript),
        "gate_fingerprints": fingerprints,
        "review": review,
        "quote_report": quote_report,
        "review_verdict": review_verdict,
        "blind_read": blind_read,
        "blind_compare": blind_compare,
        "blocking_findings": blocking,
        "passed": not blocking,
    }
    _write(round_dir / "outcome.json", outcome)
    return outcome


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", type=Path, required=True, help="draft.md from the author runner")
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument(
        "--quality-profile",
        type=Path,
        default=PROJECT_ROOT
        / "backend/config/editorial_quality_profiles/WQ-theological-topic-essay-v1.json",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    raw = json.loads(args.packet.read_text(encoding="utf-8"))
    packet = raw.get("result", raw)
    profile = json.loads(args.quality_profile.read_text(encoding="utf-8"))
    manuscript = args.draft.read_text(encoding="utf-8")

    clients = {
        "alignment": CodexSubscriptionClient(model="gpt-5.6-sol", reasoning_effort="high"),
        "blind_read": ClaudeSubscriptionClient(model="claude-haiku-4-5", reasoning_effort="high"),
        "blind_compare": CodexSubscriptionClient(model="gpt-5.6-sol", reasoning_effort="high"),
        "editorial": ClaudeSubscriptionClient(model="claude-fable-5", reasoning_effort="high"),
        "revision": ClaudeSubscriptionClient(model="claude-opus-5", reasoning_effort="high"),
    }
    writing_rules = (PROMPTS / "draft_first_topic_author.md").read_text(encoding="utf-8")
    revision_prompt = (
        (PROMPTS / "draft_first_revision.md")
        .read_text(encoding="utf-8")
        .replace("{WRITING_RULES}", writing_rules)
    )

    history: list[dict[str, Any]] = []
    baseline_manuscript: str | None = None
    baseline_review: dict[str, Any] | None = None
    for round_number in range(MAX_REVISION_ROUNDS + 1):
        round_dir = args.output_dir / f"round-{round_number:02d}"
        outcome = run_gates(
            manuscript=manuscript,
            packet=packet,
            profile=profile,
            clients=clients,
            round_dir=round_dir,
            baseline_manuscript=baseline_manuscript,
            baseline_review=baseline_review,
        )
        baseline_manuscript = manuscript
        baseline_review = outcome.get("review")
        history.append(
            {
                "round": round_number,
                "mode": "delta" if round_number else "full",
                "passed": outcome["passed"],
                "blocking_count": len(outcome["blocking_findings"]),
                "blind_answer": outcome["blind_read"]["answer_in_one_sentence"],
                "outcome_sha256": result_sha256(outcome),
                "gate_fingerprints": outcome["gate_fingerprints"],
            }
        )
        if outcome["passed"] or round_number == MAX_REVISION_ROUNDS:
            break
        revision = clients["revision"].generate_json(
            revision_prompt,
            json.dumps(
                {
                    "manuscript_markdown": manuscript,
                    "findings": outcome["blocking_findings"],
                    "approved_viewpoints": viewpoint_charter(dict(packet)),
                    "unresolved_items": structure_unresolved_items(dict(packet)),
                    "source_originals": source_texts(dict(packet)),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            REVISION_SCHEMA,
        )
        _write(round_dir / "revision.json", {"dispositions": revision["dispositions"]})
        manuscript = str(revision["manuscript_markdown"])
        (round_dir / "revised-draft.md").write_text(manuscript, encoding="utf-8")

    final = {
        "schema_version": "wang_draft_first_review_run_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "draft_path": str(args.draft),
        "evidence_packet_sha256": packet.get("evidence_packet_sha256"),
        "quality_profile_id": profile.get("profile_id"),
        "quality_profile_sha256": sha256_text(
            args.quality_profile.read_text(encoding="utf-8")
        ),
        "rounds": history,
        "status": "review_passed" if history[-1]["passed"] else "human_review_required",
        "final_manuscript_sha256": sha256_text(manuscript),
        "models": {
            name: f"{client.backend}:{client.model}" for name, client in clients.items()
        },
    }
    final["run_sha256"] = sha256_json(final)
    (args.output_dir / "final.md").write_text(manuscript, encoding="utf-8")
    _write(args.output_dir / "review-run.json", final)
    print(json.dumps({k: final[k] for k in ("status", "rounds")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
