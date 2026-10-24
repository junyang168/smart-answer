"""Review a draft-first essay both ways, revising until clean or stopped.

The generation side (#283) writes with no Brief and no author-declared
provenance, so verification happens entirely after the fact (#439,
`draft_first_two_way_review_solution.md`):

- program checks: quotes verbatim (sermons, or 和合本 for quote blocks), no
  backstage words or per-paragraph attribution, the professor named at most
  twice, and the author's self-check answering every checklist item.
- blind read (claude-haiku-4-5): a reader with no background restates the
  question and the answer; a separate closed comparison (gpt-6.1-sol) checks
  that what reached the reader is what the viewpoint registry meant. The small
  model is the point — it stands in for the ordinary reader.
- two-way review (gpt-6.1-sol, a codex agent in its own review folder):
  nothing added (every assertion licensed by the viewpoints or the sources),
  nothing removed (every checklist item — viewpoints, conditions,
  observations, tensions, routes — landed or absent for a stated reason),
  and quality scored against the WQ profile. It reads the sources itself.

Author (opus) and reviewer (gpt) are different vendors throughout. Blocking
findings go back to the author, who fixes each one or disputes a reviewer
finding with a verbatim source quote; the reviewer then reviews the whole
revised essay once more and rules on every dispute, as CVP review does. At
most two revision rounds, then the run stops for a human. Every model call
goes through a subscription CLI — no API-key client is constructed here.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient, CodexSubscriptionError
from backend.pipeline.draft_first_checklist import (
    DISPOSITION_ITEM_SCHEMA,
    anchor_in,
    build_checklist,
    disposition_problems,
    quote_corpus,
    quote_found,
)
from backend.pipeline.draft_first_review_agent import CodexReviewAgent, ReviewAgentError, prepare_folder
from backend.pipeline.draft_first_source_reader import SourceReadingTransportExhausted
from backend.pipeline.draft_first_author_runner import (
    argument_route_charter,
    backstage_language_report,
    MAX_PROFESSOR_NAMES,
    prompt_text,
    source_texts,
    structure_unresolved_items,
    verbatim_quote_report,
    viewpoint_charter,
)
from backend.pipeline.draft_first_scripture import cited_verses, scripture_corpus
from backend.pipeline.matthew_exposition_authoring import sha256_text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPTS = Path(__file__).with_name("prompts")
MAX_REVISION_ROUNDS = 2
REVIEW_MODEL = "gpt-6.1-sol"

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
            "comprehension_obstacles": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "quote": {"type": "string"},
                        "why": {"type": "string"},
                    },
                    "required": ["quote", "why"],
                },
            },
        },
        "required": [
            "question_as_read",
            "answer_in_one_sentence",
            "qualifications_kept",
            "comprehension_obstacles",
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

FINDING_KINDS = {
    "added": ("beyond_source", "modality_exceeded", "attribution_swap"),
    "removed": ("missing", "distorted"),
    "quality": ("quality",),
}


def two_way_review_schema(profile: Mapping[str, Any]) -> dict[str, Any]:
    dimension_ids = [str(item["id"]) for item in profile["dimensions"]]
    failure_ids = [str(item) for item in profile["hard_failures"]]
    kinds = [kind for group in FINDING_KINDS.values() for kind in group]
    return {
        "name": "wang_draft_first_two_way_review_v1",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "summary": {"type": "string"},
                "checklist_review": {
                    "type": "array",
                    "items": {
                        **DISPOSITION_ITEM_SCHEMA,
                        "properties": {
                            **DISPOSITION_ITEM_SCHEMA["properties"],
                            "acceptable": {"type": "boolean"},
                        },
                        "required": [*DISPOSITION_ITEM_SCHEMA["required"], "acceptable"],
                    },
                },
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
                            "direction": {"type": "string", "enum": list(FINDING_KINDS)},
                            "kind": {"type": "string", "enum": kinds},
                            "dimension_id": {"type": "string", "enum": dimension_ids},
                            "item_id": {"type": "string"},
                            "anchor": {"type": "string"},
                            "source_quote": {"type": "string"},
                            "summary": {"type": "string"},
                            "required_change": {"type": "string"},
                            "blocking": {"type": "boolean"},
                            "upholds": {"type": "string"},
                        },
                        "required": [
                            "direction", "kind", "dimension_id", "item_id", "anchor",
                            "source_quote", "summary", "required_change", "blocking", "upholds",
                        ],
                    },
                },
                "dispute_rulings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "finding_id": {"type": "string"},
                            "ruling": {"type": "string", "enum": ["accepted", "upheld"]},
                            "note": {"type": "string"},
                        },
                        "required": ["finding_id", "ruling", "note"],
                    },
                },
            },
            "required": [
                "summary", "checklist_review", "dimension_scores",
                "hard_failure_assessments", "findings", "dispute_rulings",
            ],
        },
    }


REVISION_SCHEMA: dict[str, Any] = {
    "name": "wang_draft_first_revision_v3",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "edits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"old_str": {"type": "string"}, "new_str": {"type": "string"}},
                    "required": ["old_str", "new_str"],
                },
            },
            "responses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "finding_id": {"type": "string"},
                        "response": {"type": "string", "enum": ["fixed", "disputed", "cannot_fix"]},
                        "note": {"type": "string"},
                        "source_quote": {"type": "string"},
                    },
                    "required": ["finding_id", "response", "note", "source_quote"],
                },
            },
            "self_check_updates": {"type": "array", "items": DISPOSITION_ITEM_SCHEMA},
        },
        "required": ["edits", "responses", "self_check_updates"],
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
        "source_reading": getattr(client, "source_reading_receipt", None),
    }
    return result, fingerprint


def merge_delta_scores(
    baseline_review: Mapping[str, Any], delta_review: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Inherit untouched dimension scores programmatically (#302).

    Every revision round is a full review now (#439): fidelity is a per-item
    disposition and re-checking it does not drift, but quality scores do —
    a full re-score re-rolls dimensions nobody touched. So a reviewer's score
    stands only where this round's findings land or where the previous
    round's blocking findings were just revised; affectedness comes from
    actual finding locations, never a static coupling table (#33's
    documented mistake). Hard failures stay re-assessed each round: a
    one-vote veto is exactly what must not be inherited blindly.
    """

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


class DraftReviewContractError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DraftReviewContractError(message)


def apply_edits(manuscript: str, edits: list[Mapping[str, Any]]) -> str:
    """Apply exact replacements in order; each old text must occur exactly once."""

    for number, edit in enumerate(edits, 1):
        old, new = str(edit.get("old_str") or ""), str(edit.get("new_str") or "")
        count = manuscript.count(old) if old else 0
        if count != 1:
            raise DraftReviewContractError(
                f"edit {number}: old_str occurs {count} times in the manuscript: {old[:60]!r}"
            )
        manuscript = manuscript.replace(old, new)
    return manuscript


def merge_self_check(previous: list[Mapping[str, Any]], updates: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The previous self-check with the revision's changed items replaced."""

    changed = {str(item["item_id"]): dict(item) for item in updates}
    merged = [changed.pop(str(item["item_id"]), dict(item)) for item in previous]
    return merged + list(changed.values())


def validate_revision(
    before: str,
    revision: Mapping[str, Any],
    findings: list[Mapping[str, Any]],
    *,
    corpus: str = "",
    scripture: Mapping[str, Any] | None = None,
) -> str:
    """The revised manuscript, or a contract error when the revision is not one.

    A revision returns exact edits, never the whole essay: asked to re-emit
    it inside one JSON string, opus returned 「PLACEHOLDER」, then 「ized」,
    then 「zzz」 with every finding otherwise answered (#439). Each finding is
    answered exactly once and the article is kept. Only a reviewer's finding
    can be disputed, and only with a verbatim quote: program checks are facts
    and the blind reader is the reader.
    """

    manuscript = apply_edits(before, list(revision.get("edits") or []))
    responses = revision.get("responses") or []
    expected = sorted(str(item["finding_id"]) for item in findings)
    answered = sorted(str(item.get("finding_id")) for item in responses)
    if answered != expected:
        raise DraftReviewContractError(
            f"revision answered {len(answered)} findings {answered[:6]} for {len(expected)} {expected[:6]}"
        )
    gates = {str(item["finding_id"]): item.get("gate") for item in findings}
    for item in responses:
        if item.get("response") != "disputed":
            continue
        if gates.get(str(item["finding_id"])) != "review":
            raise DraftReviewContractError(f"{item['finding_id']}: only a reviewer finding can be disputed")
        if not quote_found(str(item.get("source_quote") or ""), corpus, scripture):
            raise DraftReviewContractError(
                f"{item['finding_id']}: dispute quote not verbatim in the sources: {str(item.get('source_quote'))[:60]!r}"
            )
    if len(manuscript) < len(before) // 2:
        raise DraftReviewContractError(
            f"revised manuscript shrank from {len(before)} to {len(manuscript)} characters"
        )
    return manuscript


def validate_review(
    review: Mapping[str, Any], *, manuscript: str, profile: Mapping[str, Any]
) -> dict[str, Any]:
    """Check the review's scoring discipline, then derive the verdict."""

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
    for finding in review["findings"]:
        # Teaching that never reached the essay has no sentence to point at.
        if finding.get("kind") == "missing" and not finding.get("anchor"):
            continue
        _require(
            anchor_in(manuscript, str(finding["anchor"])),
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


def validate_two_way_review(
    review: Mapping[str, Any],
    *,
    manuscript: str,
    profile: Mapping[str, Any],
    checklist: list[Mapping[str, Any]],
    corpus: str,
    scripture: Mapping[str, Any] | None = None,
    disputed_ids: list[str] = (),
) -> dict[str, Any]:
    """The verdict, or a contract error: everything the reviewer cites is checked.

    Every checklist item judged once; an unacceptable item carries a blocking
    finding; a finding about removed teaching quotes that teaching verbatim;
    every dispute gets exactly one ruling, and an upheld one is re-issued.
    A finding the program cannot verify does not reach the author — the
    polarity misreading and the paraphrased anchor each cost a whole round.
    """

    verdict = validate_review(review, manuscript=manuscript, profile=profile)
    problems = disposition_problems(checklist, review["checklist_review"], manuscript)
    _require(not problems, "checklist review: " + "; ".join(problems[:5]))
    items = {str(item["item_id"]) for item in checklist}
    for finding in review["findings"]:
        kind, direction = finding["kind"], finding["direction"]
        _require(kind in FINDING_KINDS[direction], f"finding kind {kind} does not belong to {direction}")
        if direction == "removed":
            _require(str(finding["item_id"]) in items, f"removed finding names no checklist item: {finding['item_id']!r}")
            _require(
                quote_found(str(finding["source_quote"]), corpus, scripture),
                f"source quote not verbatim in the sources: {str(finding['source_quote'])[:60]!r}",
            )
        elif finding.get("source_quote"):
            _require(
                quote_found(str(finding["source_quote"]), corpus, scripture),
                f"source quote not verbatim in the sources: {str(finding['source_quote'])[:60]!r}",
            )
    flagged = {str(f["item_id"]) for f in review["findings"] if f["blocking"] and f["direction"] == "removed"}
    for item in review["checklist_review"]:
        if not item["acceptable"]:
            _require(str(item["item_id"]) in flagged, f"{item['item_id']}: judged unacceptable without a blocking finding")
    rulings = sorted(str(item["finding_id"]) for item in review["dispute_rulings"])
    _require(rulings == sorted(disputed_ids), f"dispute rulings {rulings} do not match disputes {sorted(disputed_ids)}")
    upheld = {str(item["finding_id"]) for item in review["dispute_rulings"] if item["ruling"] == "upheld"}
    reissued = {str(f["upholds"]) for f in review["findings"] if f.get("upholds") and f["blocking"]}
    _require(upheld <= reissued, f"upheld disputes not re-issued as blocking findings: {sorted(upheld - reissued)}")
    return verdict


def merge_blocking_findings(
    *,
    blind_read: Mapping[str, Any],
    blind_compare: Mapping[str, Any] | None,
    review: Mapping[str, Any] | None,
    quote_report: Mapping[str, Any],
    backstage_report: Mapping[str, Any] | None = None,
    self_check_problems: list[str] = (),
    round_number: int = 0,
) -> list[dict[str, Any]]:
    """Every blocking finding of one round, each with an id the author answers by."""

    findings: list[dict[str, Any]] = []
    for quote in quote_report.get("quotes_failing") or []:
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
    # Writing rule 9: the body speaks in the editor's voice; where each
    # paragraph comes from is the source bindings' job, not the prose's.
    backstage = backstage_report or {}
    for term in backstage.get("backstage_terms") or []:
        findings.append(
            {
                "gate": "backstage_check",
                "kind": "backstage_language",
                "anchor": term,
                "summary": "正文出现后台用语；按写作规则第 9 条改为编者口吻直接讲解，说法差异移入编者注。",
            }
        )
    for tag in backstage.get("source_location_tags") or []:
        findings.append(
            {
                "gate": "backstage_check",
                "kind": "source_location_tag",
                "anchor": tag,
                "summary": "正文标注出处；改为文章自己的口吻直接讲解，只在讲他本人的经历时点名。",
            }
        )
    names = backstage.get("professor_names") or []
    if len(names) > MAX_PROFESSOR_NAMES:
        for name in names[1:]:
            findings.append(
                {
                    "gate": "backstage_check",
                    "kind": "professor_named_again",
                    "anchor": name,
                    "summary": f"正文点名教授 {len(names)} 次，至多 {MAX_PROFESSOR_NAMES} 次：只在讲他本人的生活与经历时点名，"
                    "释经与神学改为文章自己的口吻直接讲出，不以他为说话人或主语。",
                }
            )
    # The author's self-check answers the same checklist the reviewer does;
    # an incomplete one is fixed by the author, not argued.
    for problem in self_check_problems:
        findings.append(
            {
                "gate": "self_check",
                "kind": "self_check_incomplete",
                "anchor": "",
                "summary": "自查未逐项处置清单：" + problem,
            }
        )
    # The blind reader is the lay-reader proxy (the owner's charge after the
    # keys article opened with bare verse numbers and untranslated Greek):
    # a spot that stops the reader cold is a blocking defect even when every
    # sentence is faithful. The reader quotes the exact spot so the finding
    # anchors.
    for obstacle in blind_read.get("comprehension_obstacles") or []:
        findings.append(
            {
                "gate": "blind_read",
                "kind": "reader_assumed_background",
                "anchor": str(obstacle["quote"]),
                "summary": "普通读者在此摸不着头脑："
                + str(obstacle["why"])
                + "｜首次触及的经文先交代场景；原文字符出现处必须随手给出中文对应"
                "（如 ἔσται δεδεμένον「將是已經被捆綁的」），语法概念先用人话说明，"
                "读者跳过原文字符仍能完整跟上。",
            }
        )
    if blind_compare is not None and not (
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
    for item in (review or {}).get("findings") or []:
        if item["blocking"]:
            findings.append(
                {
                    "gate": "review",
                    "kind": str(item["kind"]),
                    "direction": str(item["direction"]),
                    "dimension_id": str(item["dimension_id"]),
                    "item_id": str(item.get("item_id") or ""),
                    "anchor": str(item["anchor"]),
                    "source_quote": str(item.get("source_quote") or ""),
                    "summary": f"{item['summary']}｜required: {item['required_change']}",
                    "upholds": str(item.get("upholds") or ""),
                }
            )
    for number, finding in enumerate(findings, 1):
        finding["finding_id"] = f"R{round_number}-{number:02d}"
    return findings


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def run_two_way_review(
    *,
    agent: Any,
    manuscript: str,
    packet: Mapping[str, Any],
    profile: Mapping[str, Any],
    checklist: list[dict[str, Any]],
    task: Mapping[str, Any],
    round_dir: Path,
    disputed_ids: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """One accepted review: the agent in a fresh folder, one retry on a broken contract.

    A rejected reply is not a review: it is kept beside the accepted one,
    never scored. Run 3's editorial reviewer quoted 「於新約聖經上」 for the
    draft's 「中」; the program was right to reject it, but the rejection
    used to end the whole run.
    """

    prompt = prompt_text(PROMPTS / "draft_first_two_way_review.md")
    schema = two_way_review_schema(profile)
    scripture = scripture_corpus(packet)
    corpus = quote_corpus(packet, checklist)
    files = {
        "task.json": dict(task),
        "checklist.json": checklist,
        "approved_viewpoints.json": viewpoint_charter(dict(packet)),
        "unresolved_items.json": structure_unresolved_items(dict(packet)),
        "argument_routes.json": argument_route_charter(dict(packet)),
        "quality_profile.json": dict(profile),
        "scripture_cuv.json": cited_verses(dict(packet)),
    }
    rejected: list[dict[str, Any]] = []
    for attempt in range(2):
        attempt_files = dict(files)
        if rejected:
            attempt_files["task.json"] = {**task, "previous_attempt_rejected": rejected[-1]["error"]}
        base = round_dir / f"review-attempt-{attempt + 1}"
        folder = base / "folder"
        if not folder.exists():
            prepare_folder(folder, manuscript=manuscript, packet=packet, files=attempt_files)
        review, receipt = agent.review(folder=folder, call_dir=base / "call", prompt=prompt, schema=schema)
        try:
            verdict = validate_two_way_review(
                review, manuscript=manuscript, profile=profile, checklist=checklist,
                corpus=corpus, scripture=scripture, disputed_ids=disputed_ids,
            )
            receipt["rejected_attempts"] = len(rejected)
            return {**review, "verdict": verdict}, receipt
        except DraftReviewContractError as error:
            rejected.append({"error": str(error), "review": review})
            _write(round_dir / "review-rejected.json", {"rejected_attempts": rejected})
            if attempt:
                raise
    raise AssertionError("unreachable")


def run_gates(
    *,
    manuscript: str,
    packet: Mapping[str, Any],
    profile: Mapping[str, Any],
    clients: Mapping[str, Any],
    round_dir: Path,
    round_number: int = 0,
    self_check: list[Mapping[str, Any]] = (),
    baseline_review: Mapping[str, Any] | None = None,
    previous_findings: list[Mapping[str, Any]] = (),
    author_responses: list[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    charter = viewpoint_charter(dict(packet))
    unresolved = structure_unresolved_items(dict(packet))
    checklist = build_checklist(packet)
    fingerprints: dict[str, Any] = {}
    stage_errors: list[dict[str, str]] = []

    blind_read, fingerprints["blind_read"] = _call(
        clients["blind_read"],
        PROMPTS / "draft_first_blind_read.md",
        {"article": manuscript},
        BLIND_READ_SCHEMA,
    )
    fingerprints["blind_read"]["result_sha256"] = result_sha256(blind_read)
    _write(round_dir / "blind-read.json", blind_read)

    blind_compare = None
    try:
        blind_compare, fingerprints["blind_compare"] = _call(
            clients["blind_compare"], PROMPTS / "draft_first_blind_compare.md",
            {"approved_viewpoints": charter, "unresolved_items": unresolved, "blind_read": blind_read},
            BLIND_COMPARE_SCHEMA,
        )
        fingerprints["blind_compare"]["result_sha256"] = result_sha256(blind_compare)
        _write(round_dir / "blind-compare.json", blind_compare)
    except CodexSubscriptionError as exc:
        stage_errors.append({"stage": "blind_compare", "error": str(exc)[-500:]})

    disputed_ids = [str(item["finding_id"]) for item in author_responses if item.get("response") == "disputed"]
    task = {
        "round": round_number,
        "self_check": list(self_check),
        "previous_findings": list(previous_findings),
        "author_responses": list(author_responses),
    }
    review = None
    try:
        review, fingerprints["review"] = run_two_way_review(
            agent=clients["review"], manuscript=manuscript, packet=packet, profile=profile,
            checklist=checklist, task=task, round_dir=round_dir, disputed_ids=disputed_ids,
        )
        if baseline_review is not None:
            review, score_provenance = merge_delta_scores(baseline_review, review)
            review["score_provenance"] = score_provenance
            review["verdict"] = validate_review(review, manuscript=manuscript, profile=profile)
        fingerprints["review"]["result_sha256"] = result_sha256(review)
        _write(round_dir / "review.json", review)
    except (CodexSubscriptionError, ReviewAgentError, SourceReadingTransportExhausted, DraftReviewContractError) as exc:
        # No accepted review is not a pass: the missing check stays incomplete.
        stage_errors.append({"stage": "review", "error": str(exc)[-500:]})
        fingerprints["review"] = {"status": "provider_unavailable", "model": clients["review"].model}

    quote_report = verbatim_quote_report(manuscript, dict(packet), scripture_corpus(packet))
    backstage_report = backstage_language_report(manuscript)
    self_check_problems = disposition_problems(checklist, list(self_check), manuscript)
    blocking = merge_blocking_findings(
        blind_read=blind_read,
        blind_compare=blind_compare,
        review=review,
        quote_report=quote_report,
        backstage_report=backstage_report,
        self_check_problems=self_check_problems,
        round_number=round_number,
    )
    outcome = {
        "round": round_number,
        "manuscript_sha256": sha256_text(manuscript),
        "checklist_sha256": sha256_json(checklist),
        "gate_fingerprints": fingerprints,
        "review": review,
        "review_verdict": (review or {}).get("verdict"),
        "quote_report": quote_report,
        "backstage_language_report": backstage_report,
        "self_check_problems": self_check_problems,
        "blind_read": blind_read,
        "blind_compare": blind_compare,
        "blocking_findings": blocking,
        "stage_errors": stage_errors,
        "review_completed": review is not None,
        "passed": not blocking and not stage_errors,
    }
    _write(round_dir / "outcome.json", outcome)
    return outcome


def main() -> int:
    from backend.pipeline.draft_first_source_reader import with_source_reading
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", type=Path, required=True, help="draft.md from the author runner")
    parser.add_argument(
        "--self-check", type=Path,
        help="the author's self-check.json; defaults to the one beside the draft",
    )
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
    self_check_path = args.self_check or args.draft.with_name("self-check.json")
    self_check = json.loads(self_check_path.read_text(encoding="utf-8")) if self_check_path.exists() else []

    clients = {
        "blind_read": ClaudeSubscriptionClient(model="claude-haiku-4-5", reasoning_effort="high"),
        "blind_compare": CodexSubscriptionClient(model=REVIEW_MODEL, reasoning_effort="high"),
        "review": CodexReviewAgent(model=REVIEW_MODEL, reasoning_effort="high"),
        # A revision answers every finding and redoes the whole self-check; on the
        # 人子 essay (51 findings, 68 items) it ran past the 15-minute default.
        "revision": ClaudeSubscriptionClient(model="claude-opus-5", reasoning_effort="high", timeout_seconds=3600),
    }
    clients["revision"] = with_source_reading(clients["revision"], packet, args.output_dir / "source-reading" / "revision")
    revision_prompt = prompt_text(PROMPTS / "draft_first_revision.md").replace(
        "{WRITING_RULES}", prompt_text(PROMPTS / "draft_first_topic_author.md")
    )
    checklist = build_checklist(packet)
    corpus = quote_corpus(packet, checklist)
    scripture = scripture_corpus(packet)

    history: list[dict[str, Any]] = []
    baseline_review: dict[str, Any] | None = None
    previous_findings: list[dict[str, Any]] = []
    author_responses: list[dict[str, Any]] = []
    for round_number in range(MAX_REVISION_ROUNDS + 1):
        round_dir = args.output_dir / f"round-{round_number:02d}"
        outcome = run_gates(
            manuscript=manuscript,
            packet=packet,
            profile=profile,
            clients=clients,
            round_dir=round_dir,
            round_number=round_number,
            self_check=self_check,
            baseline_review=baseline_review,
            previous_findings=previous_findings,
            author_responses=author_responses,
        )
        baseline_review = outcome.get("review") or baseline_review
        history.append(
            {
                "round": round_number,
                "mode": "full",
                "passed": outcome["passed"],
                "blocking_count": len(outcome["blocking_findings"]),
                "blind_answer": outcome["blind_read"]["answer_in_one_sentence"],
                "outcome_sha256": result_sha256(outcome),
                "gate_fingerprints": outcome["gate_fingerprints"],
            }
        )
        if outcome["passed"] or round_number == MAX_REVISION_ROUNDS or (
            outcome.get("stage_errors") and not outcome["blocking_findings"]
        ):
            break
        findings = outcome["blocking_findings"]
        revision_payload = json.dumps(
            {
                "manuscript_markdown": manuscript,
                "findings": findings,
                "checklist": checklist,
                "self_check": self_check,
                "approved_viewpoints": viewpoint_charter(dict(packet)),
                "unresolved_items": structure_unresolved_items(dict(packet)),
                "source_originals": source_texts(dict(packet)),
                "scripture_cuv": cited_verses(dict(packet)),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        revised: str | None = None
        rejections: list[str] = []
        for _attempt in range(2):
            payload = revision_payload
            if rejections:
                # Tell the reviser why: an identical second request returned
                # the identical broken reply.
                payload = json.dumps({**json.loads(revision_payload), "previous_attempt_rejected": rejections[-1]},
                                     ensure_ascii=False, sort_keys=True)
            revision = clients["revision"].generate_json(revision_prompt, payload, REVISION_SCHEMA)
            try:
                revised = validate_revision(manuscript, revision, findings, corpus=corpus, scripture=scripture)
                break
            except DraftReviewContractError as error:
                rejections.append(str(error))
        _write(
            round_dir / "revision.json",
            {
                "edits": revision.get("edits") or [],
                "responses": revision.get("responses") or [],
                "self_check_updates": revision.get("self_check_updates") or [],
                "rejected_attempts": rejections,
            },
        )
        if revised is None:
            # Keep the reviewed manuscript; an empty revision is not a draft.
            outcome.setdefault("stage_errors", []).append(
                {"stage": "revision", "error": "; ".join(rejections)}
            )
            break
        manuscript = revised
        self_check = merge_self_check(self_check, list(revision.get("self_check_updates") or []))
        responses = {str(item["finding_id"]): item for item in revision["responses"]}
        previous_findings = findings
        author_responses = [
            {**responses[str(finding["finding_id"])], "finding": finding} for finding in findings
        ]
        (round_dir / "revised-draft.md").write_text(manuscript, encoding="utf-8")
        _write(round_dir / "revised-self-check.json", self_check)

    final = {
        "schema_version": "wang_draft_first_review_run_v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "draft_path": str(args.draft),
        "evidence_packet_sha256": packet.get("evidence_packet_sha256"),
        "checklist_sha256": sha256_json(checklist),
        "quality_profile_id": profile.get("profile_id"),
        "quality_profile_sha256": sha256_text(
            args.quality_profile.read_text(encoding="utf-8")
        ),
        "rounds": history,
        "status": ("review_passed" if history[-1]["passed"] else
                   "human_review_required" if outcome["blocking_findings"] else "provider_hold"),
        "stage_errors": outcome.get("stage_errors", []),
        "final_manuscript_sha256": sha256_text(manuscript),
        "models": {
            name: f"{client.backend}:{client.model}" for name, client in clients.items()
        },
    }
    final["run_sha256"] = sha256_json(final)
    (args.output_dir / "final.md").write_text(manuscript, encoding="utf-8")
    _write(args.output_dir / "final-self-check.json", self_check)
    _write(args.output_dir / "review-run.json", final)
    print(json.dumps({k: final[k] for k in ("status", "rounds")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
