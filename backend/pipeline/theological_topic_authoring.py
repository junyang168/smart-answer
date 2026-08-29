"""Authoring contracts for evidence-bound theological topic essays."""

from __future__ import annotations

import re
from typing import Any, Mapping

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.manuscript_grounding_check import extract_provenance_paragraphs
from backend.pipeline.matthew_exposition_authoring import sha256_text, validate_strict_schema
from backend.pipeline.theological_editorial_synthesis import (
    TheologicalEditorialContractError,
    validate_theological_evidence_packet,
)


AUTHOR_STATUSES = frozenset({"drafted", "composition_change_required"})
TOPIC_REVIEW_SCOPE = "theological_topic_essay_quality"
FORBIDDEN_TOPIC_READER_PHRASES = (
    "教授",
    "解释链",
    "解释路径",
    "论证链",
    "观点识别",
    "近距语境",
    "独立检验",
    "另一项检验",
    "证据管理",
    "现有材料",
    "经文材料",
    "有限结论",
    "要正面理解",
    "进一步考察",
    "接下来需要说明",
    "读者最终应当记住",
    "本文要使人看见",
    "焦点应当回到",
    "根基的焦点",
    "正面答案须按",
    "正面答案可以并列表述",
    "正面的答案需要",
)


TOPIC_AUTHOR_SCHEMA: dict[str, Any] = {
    "name": "wang_theological_topic_author_result_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "status": {"type": "string", "enum": sorted(AUTHOR_STATUSES)},
            "manuscript_markdown": {"type": "string"},
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "section_id": {"type": "string"},
                        "claim_ids_used": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "viewpoint_revision_ids_used": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "argument_route_revision_ids_used": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "output_anchor": {"type": "string"},
                    },
                    "required": [
                        "section_id",
                        "claim_ids_used",
                        "viewpoint_revision_ids_used",
                        "argument_route_revision_ids_used",
                        "output_anchor",
                    ],
                },
            },
            "composition_change_requests": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "request_id": {"type": "string"},
                        "reason": {"type": "string"},
                        "proposed_change": {"type": "string"},
                        "affected_record_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "request_id",
                        "reason",
                        "proposed_change",
                        "affected_record_ids",
                    ],
                },
            },
        },
        "required": [
            "status",
            "manuscript_markdown",
            "sections",
            "composition_change_requests",
        ],
    },
}


TOPIC_GROUNDING_REVISION_SCHEMA: dict[str, Any] = {
    "name": "wang_theological_topic_grounding_revision_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {
                "type": "string",
                "enum": ["wang_theological_topic_grounding_revision_v1"],
            },
            "baseline_manuscript_sha256": {"type": "string"},
            "finding_dispositions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "finding_id": {"type": "string"},
                        "resolution": {
                            "type": "string",
                            "enum": ["resolved", "composition_change_required"],
                        },
                        "resolution_anchor": {"type": "string"},
                        "explanation": {"type": "string"},
                    },
                    "required": [
                        "finding_id",
                        "resolution",
                        "resolution_anchor",
                        "explanation",
                    ],
                },
            },
            "revised_author_result": TOPIC_AUTHOR_SCHEMA["schema"],
        },
        "required": [
            "schema_version",
            "baseline_manuscript_sha256",
            "finding_dispositions",
            "revised_author_result",
        ],
    },
}


def _review_finding_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "finding_id": {"type": "string"},
            "dimension_id": {"type": "string"},
            "section_id": {"type": "string"},
            "severity": {"type": "string", "enum": ["minor", "major"]},
            "blocking": {"type": "boolean"},
            "manuscript_anchor": {"type": "string"},
            "problem": {"type": "string"},
            "required_change": {"type": "string"},
        },
        "required": [
            "finding_id", "dimension_id", "section_id", "severity", "blocking",
            "manuscript_anchor", "problem", "required_change",
        ],
    }


TOPIC_EDITORIAL_REVIEW_SCHEMA: dict[str, Any] = {
    "name": "wang_theological_topic_editorial_review_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "string", "enum": ["wang_theological_topic_editorial_review_v1"]},
            "reviewed_manuscript_sha256": {"type": "string"},
            "scope_confirmation": {"type": "string", "enum": [TOPIC_REVIEW_SCOPE]},
            "summary": {"type": "string"},
            "dimension_scores": {
                "type": "array",
                "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "dimension_id": {"type": "string"},
                        "score": {"type": "integer"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["dimension_id", "score", "evidence"],
                },
            },
            "hard_failure_assessments": {
                "type": "array",
                "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "failure_id": {"type": "string"},
                        "failed": {"type": "boolean"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["failure_id", "failed", "evidence"],
                },
            },
            "findings": {"type": "array", "items": _review_finding_schema()},
        },
        "required": [
            "schema_version", "reviewed_manuscript_sha256", "scope_confirmation",
            "summary", "dimension_scores", "hard_failure_assessments", "findings",
        ],
    },
}


TOPIC_EDITORIAL_REVISION_SCHEMA: dict[str, Any] = {
    "name": "wang_theological_topic_editorial_revision_v1",
    "strict": True,
    "schema": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "string", "enum": ["wang_theological_topic_editorial_revision_v1"]},
            "baseline_manuscript_sha256": {"type": "string"},
            "finding_dispositions": {
                "type": "array",
                "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "finding_id": {"type": "string"},
                        "resolution": {"type": "string", "enum": ["resolved", "composition_change_required"]},
                        "resolution_anchor": {"type": "string"},
                        "explanation": {"type": "string"},
                    },
                    "required": ["finding_id", "resolution", "resolution_anchor", "explanation"],
                },
            },
            "revised_author_result": TOPIC_AUTHOR_SCHEMA["schema"],
        },
        "required": ["schema_version", "baseline_manuscript_sha256", "finding_dispositions", "revised_author_result"],
    },
}


TOPIC_FINAL_DELTA_REVIEW_SCHEMA: dict[str, Any] = {
    "name": "wang_theological_topic_final_delta_review_v1",
    "strict": True,
    "schema": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "string", "enum": ["wang_theological_topic_final_delta_review_v1"]},
            "baseline_manuscript_sha256": {"type": "string"},
            "reviewed_manuscript_sha256": {"type": "string"},
            "summary": {"type": "string"},
            "dimension_scores": TOPIC_EDITORIAL_REVIEW_SCHEMA["schema"]["properties"]["dimension_scores"],
            "hard_failure_assessments": TOPIC_EDITORIAL_REVIEW_SCHEMA["schema"]["properties"]["hard_failure_assessments"],
            "finding_dispositions": TOPIC_EDITORIAL_REVISION_SCHEMA["schema"]["properties"]["finding_dispositions"],
            "findings": {"type": "array", "items": _review_finding_schema()},
        },
        "required": [
            "schema_version", "baseline_manuscript_sha256", "reviewed_manuscript_sha256",
            "summary", "dimension_scores", "hard_failure_assessments", "finding_dispositions", "findings",
        ],
    },
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise TheologicalEditorialContractError(message)


def build_topic_authoring_packet(
    *,
    evidence_packet: Mapping[str, Any],
    approved_brief: Mapping[str, Any],
    publication_profile: Mapping[str, Any],
    quality_profile: Mapping[str, Any],
) -> dict[str, Any]:
    validate_theological_evidence_packet(evidence_packet)
    _require(
        approved_brief.get("schema_version")
        in {
            "wang_theological_editorial_brief_v1",
            "wang_theological_editorial_brief_v2",
        },
        "unsupported approved brief schema",
    )
    _require(
        approved_brief.get("evidence_packet_sha256")
        == evidence_packet.get("evidence_packet_sha256"),
        "approved brief belongs to another evidence packet",
    )
    brief_body = {
        key: value for key, value in approved_brief.items() if key != "brief_sha256"
    }
    _require(
        approved_brief.get("brief_sha256") == sha256_json(brief_body),
        "approved brief SHA mismatch",
    )
    scope = evidence_packet["scope"]
    _require(
        publication_profile.get("profile_id") == scope["publication_profile_id"],
        "publication profile does not match EditorialScope",
    )
    packet = {
        "schema_version": "wang_theological_topic_authoring_packet_v1",
        "scope": scope,
        "approved_brief": dict(approved_brief),
        "editorial_decisions": approved_brief["editorial_decisions"],
        "knowledge": {
            "claims": evidence_packet["claims"],
            "evidence_steps": evidence_packet["evidence_steps"],
            "source_fragments": evidence_packet["source_fragments"],
            "source_documents": evidence_packet["source_documents"],
            "source_originals": evidence_packet["source_originals"],
        },
        "viewpoints": evidence_packet["focal_viewpoints"],
        "argument_routes": evidence_packet["argument_routes"],
        "relations": evidence_packet["relations"],
        "publication_profile": dict(publication_profile),
        "quality_profile": dict(quality_profile),
        "input_bindings": {
            "scope_sha256": scope["scope_sha256"],
            "evidence_packet_sha256": evidence_packet["evidence_packet_sha256"],
            "brief_sha256": approved_brief["brief_sha256"],
            "publication_profile_sha256": sha256_json(dict(publication_profile)),
            "quality_profile_sha256": sha256_json(dict(quality_profile)),
        },
    }
    packet["packet_sha256"] = sha256_json(packet)
    return packet


def validate_topic_author_result(
    result: Mapping[str, Any], *, authoring_packet: Mapping[str, Any]
) -> None:
    status = str(result.get("status") or "")
    _require(status in AUTHOR_STATUSES, f"unsupported author status: {status}")
    requests = list(result.get("composition_change_requests") or [])
    if status == "composition_change_required":
        _require(bool(requests), "composition change requires at least one request")
        _require(not result.get("manuscript_markdown"), "change handoff cannot include a draft")
        _require(not result.get("sections"), "change handoff cannot include a section ledger")
        return
    _require(not requests, "drafted result cannot retain composition change requests")

    manuscript = str(result.get("manuscript_markdown") or "")
    _require(bool(manuscript.strip()), "drafted result requires manuscript Markdown")
    reader_text = topic_reader_text(manuscript)
    forbidden = [
        phrase for phrase in FORBIDDEN_TOPIC_READER_PHRASES if phrase in reader_text
    ]
    _require(
        not forbidden,
        f"forbidden reader-prose phrases: {forbidden}",
    )
    decisions = authoring_packet["editorial_decisions"]
    title = str(decisions["article_title"])
    _require(
        manuscript.lstrip().startswith(f"# {title}"),
        "manuscript H1 must exactly match the approved brief title",
    )
    brief_sections = list(decisions["sections"])
    expected_section_ids = [str(item["section_id"]) for item in brief_sections]
    ledger = list(result.get("sections") or [])
    received_section_ids = [str(item.get("section_id") or "") for item in ledger]
    _require(
        received_section_ids == expected_section_ids,
        "author ledger must match approved brief sections in order",
    )

    last_heading_offset = -1
    claims_by_id = {
        str(item["claim_id"]): item
        for item in authoring_packet["knowledge"]["claims"]
    }
    focal_by_revision = {
        str(item["revision"]["viewpoint_revision_id"]): item
        for item in authoring_packet["viewpoints"]
    }
    routes_by_revision = {
        str(item["revision"]["argument_route_revision_id"]): item
        for item in authoring_packet["argument_routes"]
    }
    all_ledger_claims: set[str] = set()
    for brief_section, ledger_section in zip(brief_sections, ledger, strict=True):
        heading = f"## {brief_section['heading']}"
        heading_offset = manuscript.find(heading)
        _require(heading_offset >= 0, f"missing approved heading: {heading}")
        _require(
            heading_offset > last_heading_offset,
            "approved headings are not in approved order",
        )
        last_heading_offset = heading_offset
        claim_ids = [str(value) for value in ledger_section.get("claim_ids_used") or []]
        _require(bool(claim_ids), f"{brief_section['section_id']}: section uses no Claims")
        _require(
            len(claim_ids) == len(set(claim_ids)),
            f"{brief_section['section_id']}: duplicate Claim in ledger",
        )
        unknown_claims = set(claim_ids) - set(claims_by_id)
        _require(not unknown_claims, f"unknown Claim IDs: {sorted(unknown_claims)}")
        all_ledger_claims.update(claim_ids)

        expected_viewpoints = set(brief_section["viewpoint_revision_ids"])
        used_viewpoints = set(
            ledger_section.get("viewpoint_revision_ids_used") or []
        )
        _require(
            used_viewpoints == expected_viewpoints,
            f"{brief_section['section_id']}: viewpoint ledger differs from brief",
        )
        expected_routes = set(brief_section["argument_route_revision_ids"])
        used_routes = set(
            ledger_section.get("argument_route_revision_ids_used") or []
        )
        _require(
            used_routes == expected_routes,
            f"{brief_section['section_id']}: route ledger differs from brief",
        )
        _require(
            used_routes <= set(routes_by_revision),
            f"{brief_section['section_id']}: unknown route revision",
        )
        for viewpoint_revision_id in expected_viewpoints:
            member_claim_ids = set(
                focal_by_revision[viewpoint_revision_id]["member_claim_ids"]
            )
            _require(
                bool(member_claim_ids & set(claim_ids)),
                f"{brief_section['section_id']}: viewpoint {viewpoint_revision_id} "
                "has no member Claim in the authored section",
            )
        anchor = str(ledger_section.get("output_anchor") or "")
        _require(bool(anchor) and anchor in manuscript, "section output anchor not found")

    provenance_claims: set[str] = set()
    section_route_ranges: list[tuple[int, int, set[str]]] = []
    for index, brief_section in enumerate(brief_sections):
        start = manuscript.find(f"## {brief_section['heading']}")
        end = (
            manuscript.find(f"## {brief_sections[index + 1]['heading']}")
            if index + 1 < len(brief_sections)
            else len(manuscript)
        )
        section_route_ranges.append(
            (
                start,
                end,
                {
                    str(value)
                    for value in brief_section.get("argument_route_revision_ids") or []
                },
            )
        )
    for paragraph in extract_provenance_paragraphs(manuscript):
        provenance = paragraph["provenance"]
        if not isinstance(provenance, dict):
            continue
        attribution = provenance.get("attribution")
        claim_ids = [str(value) for value in provenance.get("claim_ids") or []]
        if attribution in {"professor", "editorial_synthesis"}:
            _require(bool(claim_ids), "substantive provenance requires claim_ids")
            unknown = set(claim_ids) - set(claims_by_id)
            _require(not unknown, f"provenance cites unknown Claims: {sorted(unknown)}")
            provenance_claims.update(claim_ids)
            route_ids = [
                str(value)
                for value in provenance.get("argument_route_revision_ids") or []
            ]
            _require(
                len(route_ids) == len(set(route_ids)),
                "paragraph provenance has duplicate ArgumentRoute revisions",
            )
            unknown_routes = set(route_ids) - set(routes_by_revision)
            _require(
                not unknown_routes,
                f"provenance cites unknown ArgumentRoutes: {sorted(unknown_routes)}",
            )
            paragraph_offset = int(paragraph.get("comment_offset") or 0)
            section_routes = next(
                (
                    allowed
                    for start, end, allowed in section_route_ranges
                    if start <= paragraph_offset < end
                ),
                None,
            )
            if route_ids and section_routes is not None:
                outside_section = set(route_ids) - section_routes
                _require(
                    not outside_section,
                    "paragraph provenance cites ArgumentRoutes outside its section: "
                    f"{sorted(outside_section)}",
                )
    _require(
        provenance_claims <= all_ledger_claims,
        "paragraph provenance cites Claims absent from the section ledger",
    )
    _require(
        bool(provenance_claims),
        "draft has no checkable professor/editorial_synthesis provenance",
    )


def topic_reader_text(markdown: str) -> str:
    return re.sub(r"<!--.*?-->", "", markdown, flags=re.DOTALL)


def editorial_instructions_by_claim(
    *, authoring_packet: Mapping[str, Any], author_result: Mapping[str, Any]
) -> dict[str, str]:
    """Project approved brief decisions onto each section's Claim material.

    These instructions are editor-attributed grounds for article structure and
    qualification.  They do not become professor assertions, and the grounding
    packet keeps them in a separate ``editorial_instruction`` field.
    """

    brief_sections = {
        str(item["section_id"]): item
        for item in authoring_packet["editorial_decisions"]["sections"]
    }
    global_unresolved = list(
        authoring_packet["editorial_decisions"].get("unresolved_items") or []
    )
    instructions: dict[str, list[str]] = {}
    for ledger in author_result.get("sections") or []:
        section_id = str(ledger["section_id"])
        brief = brief_sections[section_id]
        parts = [
            f"Section reader function: {brief['reader_function']}",
            *[
                f"Required qualification: {value}"
                for value in brief.get("required_qualifications") or []
            ],
            *[
                f"Prohibited editorial move: {value}"
                for value in brief.get("prohibited_functions") or []
            ],
            *[f"Unresolved structure item: {value}" for value in global_unresolved],
        ]
        for claim_id in ledger.get("claim_ids_used") or []:
            values = instructions.setdefault(str(claim_id), [])
            for part in parts:
                if part not in values:
                    values.append(part)
    return {claim_id: "\n".join(values) for claim_id, values in instructions.items()}


def validate_topic_grounding_revision(
    revision: Mapping[str, Any],
    *,
    baseline_manuscript_sha256: str,
    findings: list[Mapping[str, Any]],
    authoring_packet: Mapping[str, Any],
) -> None:
    _require(
        revision.get("schema_version")
        == "wang_theological_topic_grounding_revision_v1",
        "unsupported topic grounding revision schema",
    )
    _require(
        revision.get("baseline_manuscript_sha256")
        == baseline_manuscript_sha256,
        "grounding revision belongs to another manuscript",
    )
    expected_ids = {str(item["finding_id"]) for item in findings}
    dispositions = list(revision.get("finding_dispositions") or [])
    received_ids = [str(item.get("finding_id") or "") for item in dispositions]
    _require(
        len(received_ids) == len(set(received_ids))
        and set(received_ids) == expected_ids,
        "grounding revision must dispose every finding exactly once",
    )
    revised = revision.get("revised_author_result") or {}
    validate_topic_author_result(revised, authoring_packet=authoring_packet)
    manuscript = str(revised.get("manuscript_markdown") or "")
    for item in dispositions:
        _require(bool(str(item.get("explanation") or "").strip()), "disposition needs explanation")
        anchor = str(item.get("resolution_anchor") or "")
        if item.get("resolution") == "resolved":
            _require(bool(anchor) and anchor in manuscript, "resolution anchor not found")
        else:
            _require(
                revised.get("status") == "composition_change_required",
                "composition_change_required disposition needs change handoff",
            )


def build_topic_editorial_review_packet(
    *, authoring_packet: Mapping[str, Any], author_result: Mapping[str, Any]
) -> dict[str, Any]:
    validate_topic_author_result(author_result, authoring_packet=authoring_packet)
    manuscript = str(author_result["manuscript_markdown"])
    packet = {
        "schema_version": "wang_theological_topic_editorial_review_packet_v1",
        "manuscript_sha256": sha256_text(manuscript),
        "manuscript_markdown": manuscript,
        "editorial_decisions": authoring_packet["editorial_decisions"],
        "author_section_ledger": author_result["sections"],
        "quality_profile": authoring_packet["quality_profile"],
        "route_contracts": [
            {
                "argument_route_revision_id": item["revision"]["argument_route_revision_id"],
                "route_label": item["revision"]["route_label"],
                "ordered_inference_nodes": item["revision"]["ordered_inference_nodes"],
                "source_document_ids": sorted(
                    {
                        attestation["source_document_id"]
                        for attestation in item.get("full_attestations", [])
                    }
                ),
            }
            for item in authoring_packet["argument_routes"]
        ],
        "claim_statements": [
            {
                "claim_id": item["claim_id"],
                "statement": item.get("statement") or item.get("title"),
            }
            for item in authoring_packet["knowledge"]["claims"]
        ],
        "source_fragments": authoring_packet["knowledge"]["source_fragments"],
        "source_originals": authoring_packet["knowledge"]["source_originals"],
        "editorial_scope": authoring_packet["scope"],
        "scope": {
            "include": ["writing_quality", "structural_fidelity", "route_integrity", "source_fidelity"],
            "exclude": ["theological_correctness", "external_commentary", "program_audit"],
        },
    }
    packet["packet_sha256"] = sha256_json(packet)
    return packet


def evaluate_topic_editorial_review(
    review: Mapping[str, Any], *, quality_profile: Mapping[str, Any]
) -> dict[str, Any]:
    configured = {str(item["id"]): item for item in quality_profile["dimensions"]}
    scores = {str(item["dimension_id"]): item for item in review.get("dimension_scores") or []}
    below = [
        dimension_id for dimension_id, config in configured.items()
        if int(scores[dimension_id]["score"]) < int(config["minimum"])
    ]
    failures = [
        str(item["failure_id"])
        for item in review.get("hard_failure_assessments") or []
        if item["failed"]
    ]
    blocking = [str(item["finding_id"]) for item in review.get("findings") or [] if item["blocking"]]
    return {
        "passed": not below and not failures and not blocking,
        "below_minimum_dimensions": below,
        "hard_failures": failures,
        "blocking_finding_ids": blocking,
        "total_score": sum(int(item["score"]) for item in scores.values()),
        "total_score_decides_nothing": True,
    }


def validate_topic_editorial_review(
    review: Mapping[str, Any], *, review_packet: Mapping[str, Any]
) -> dict[str, Any]:
    validate_strict_schema(dict(review), TOPIC_EDITORIAL_REVIEW_SCHEMA)
    _require(review["reviewed_manuscript_sha256"] == review_packet["manuscript_sha256"], "review belongs to another manuscript")
    profile = review_packet["quality_profile"]
    dimension_ids = [str(item["id"]) for item in profile["dimensions"]]
    received = [str(item["dimension_id"]) for item in review["dimension_scores"]]
    _require(len(received) == len(set(received)) and set(received) == set(dimension_ids), "review must score every configured dimension exactly once")
    weights = {str(item["id"]): int(item["weight"]) for item in profile["dimensions"]}
    for item in review["dimension_scores"]:
        _require(0 <= int(item["score"]) <= weights[str(item["dimension_id"])], "review score outside dimension weight")
    hard_ids = [str(value) for value in profile["hard_failures"]]
    received_hard = [str(item["failure_id"]) for item in review["hard_failure_assessments"]]
    _require(len(received_hard) == len(set(received_hard)) and set(received_hard) == set(hard_ids), "review must assess every hard failure exactly once")
    section_ids = {str(item["section_id"]) for item in review_packet["author_section_ledger"]}
    finding_ids: set[str] = set()
    manuscript = str(review_packet["manuscript_markdown"])
    _require(sha256_text(manuscript) == review_packet["manuscript_sha256"], "review packet manuscript SHA mismatch")
    for item in review["findings"]:
        finding_id = str(item["finding_id"])
        _require(finding_id not in finding_ids, "duplicate editorial finding ID")
        finding_ids.add(finding_id)
        _require(item["dimension_id"] in dimension_ids, "finding uses unknown dimension")
        _require(item["section_id"] in section_ids, "finding uses unknown section")
        _require(bool(item["manuscript_anchor"]) and item["manuscript_anchor"] in manuscript, "finding anchor not found")
    return evaluate_topic_editorial_review(review, quality_profile=profile)


def validate_topic_editorial_revision(
    revision: Mapping[str, Any], *, baseline_manuscript_sha256: str,
    findings: list[Mapping[str, Any]], authoring_packet: Mapping[str, Any]
) -> None:
    validate_strict_schema(dict(revision), TOPIC_EDITORIAL_REVISION_SCHEMA)
    _require(revision["baseline_manuscript_sha256"] == baseline_manuscript_sha256, "editorial revision belongs to another manuscript")
    expected = {str(item["finding_id"]) for item in findings if item["blocking"]}
    dispositions = list(revision["finding_dispositions"])
    received = [str(item["finding_id"]) for item in dispositions]
    _require(len(received) == len(set(received)) and set(received) == expected, "editorial revision must dispose every blocking finding exactly once")
    revised = revision["revised_author_result"]
    validate_topic_author_result(revised, authoring_packet=authoring_packet)
    manuscript = str(revised.get("manuscript_markdown") or "")
    for item in dispositions:
        if item["resolution"] == "resolved":
            _require(bool(item["resolution_anchor"]) and item["resolution_anchor"] in manuscript, "editorial resolution anchor not found")
        else:
            _require(revised["status"] == "composition_change_required", "composition change disposition needs change handoff")


def validate_topic_final_delta_review(
    review: Mapping[str, Any], *, baseline_manuscript_sha256: str,
    revised_manuscript: str, affected_dimension_ids: list[str],
    affected_hard_failure_ids: list[str], findings: list[Mapping[str, Any]],
    quality_profile: Mapping[str, Any]
) -> None:
    validate_strict_schema(dict(review), TOPIC_FINAL_DELTA_REVIEW_SCHEMA)
    revised_sha = sha256_text(revised_manuscript)
    _require(review["baseline_manuscript_sha256"] == baseline_manuscript_sha256, "delta review baseline SHA mismatch")
    _require(review["reviewed_manuscript_sha256"] == revised_sha, "delta review manuscript SHA mismatch")
    score_ids = [str(item["dimension_id"]) for item in review["dimension_scores"]]
    _require(len(score_ids) == len(set(score_ids)) and set(score_ids) == set(affected_dimension_ids), "delta review dimensions mismatch")
    weights = {str(item["id"]): int(item["weight"]) for item in quality_profile["dimensions"]}
    for item in review["dimension_scores"]:
        _require(0 <= int(item["score"]) <= weights[str(item["dimension_id"])], "delta score outside dimension weight")
    hard_ids = [str(item["failure_id"]) for item in review["hard_failure_assessments"]]
    _require(len(hard_ids) == len(set(hard_ids)) and set(hard_ids) == set(affected_hard_failure_ids), "delta hard-failure assessments mismatch")
    expected = {str(item["finding_id"]) for item in findings if item["blocking"]}
    dispositions = [str(item["finding_id"]) for item in review["finding_dispositions"]]
    _require(len(dispositions) == len(set(dispositions)) and set(dispositions) == expected, "delta review must verify every revised finding exactly once")
    configured_dimensions = {str(item["id"]) for item in quality_profile["dimensions"]}
    for item in review["findings"]:
        # A delta reviewer scores only affected dimensions, but the reviewer-call
        # invariant also requires it to return any next-round finding in this same
        # response.  A changed paragraph can expose a problem in another configured
        # dimension; that finding becomes the next revision's direct scope rather
        # than being silently discarded or forcing another full review.
        _require(item["dimension_id"] in configured_dimensions, "delta finding uses unknown dimension")
        _require(bool(item["manuscript_anchor"]) and item["manuscript_anchor"] in revised_manuscript, "delta finding anchor not found")
