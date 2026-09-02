"""Compile a SHA-bound theological evidence packet for draft-first authoring.

This module is shared infrastructure, not an article-writing pipeline. It turns
one reviewed ViewpointStructure into the bounded CVP, ArgumentRoute, Claim,
source-fragment, and complete-original packet that a draft-first Author may
consume. It stops before reader prose, makes no theological judgment, and never
infers a missing positive position from a rejected one.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from backend.api.canonical_repository.viewpoint_foundation import (
    semantic_record_sha,
    sha256_json,
)


APPROVED = frozenset({"system_approved", "human_approved", "approved"})
STRUCTURE_ROLES = frozenset({
    "central_claim",
    "negative_boundary",
    "positive_identification",
    "supporting_conclusion",
    "qualification",
    "tension_side",
    "application",
    "methodological_boundary",
})
ARTICLE_FUNCTIONS = frozenset({
    "introduction",
    "positive_exposition",
    "argument_development",
    "qualification",
    "tension_disclosure",
    "methodological_boundary",
    "negative_boundary",
    "application",
    "conclusion",
})


class TheologicalEvidencePacketError(ValueError):
    """An evidence packet violates an authority or coverage contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise TheologicalEvidencePacketError(message)


def _nonempty(value: Any, field: str) -> str:
    text = str(value or "").strip()
    _require(bool(text), f"{field} must be non-empty")
    return text


def _unique(values: Sequence[str], field: str) -> list[str]:
    result = [str(value) for value in values]
    _require(len(result) == len(set(result)), f"{field} must be unique")
    return result


def _record_id(collection: str, record: Mapping[str, Any]) -> str:
    fields = {
        "viewpoint_structures": "structure_id",
        "viewpoint_structure_revisions": "structure_revision_id",
        "canonical_viewpoints": "viewpoint_id",
        "viewpoint_revisions": "viewpoint_revision_id",
        "viewpoint_claim_links": "viewpoint_claim_link_id",
        "argument_routes": "argument_route_id",
        "argument_route_revisions": "argument_route_revision_id",
        "argument_route_attestations": "argument_route_attestation_id",
        "viewpoint_relations": "viewpoint_relation_id",
        "claims": "claim_id",
        "evidence_steps": "evidence_step_id",
        "source_fragments": "fragment_id",
        "source_documents": "source_id",
    }
    return str(record[fields[collection]])


SourceOriginalReader = Callable[[Mapping[str, Any]], Mapping[str, Any]]
MAX_DIRECT_SOURCE_ORIGINAL_CHARACTERS = 120_000


def _filesystem_source_original(source: Mapping[str, Any]) -> dict[str, Any]:
    """Read one complete scoped original and turn it into model-readable text."""

    source_id = _nonempty(source.get("source_id"), "source_id")
    source_type = _nonempty(source.get("source_type"), f"{source_id}.source_type")
    _require(
        source_type in {"sermon_transcript", "notes_manuscript"},
        f"unsupported theological source type: {source_type}",
    )
    path = Path(_nonempty(source.get("source_path"), f"{source_id}.source_path"))
    _require(path.is_file(), f"scoped source original is missing: {source_id}")
    raw = path.read_bytes()
    file_sha256 = hashlib.sha256(raw).hexdigest()
    _require(
        file_sha256 == str(source.get("source_sha256") or ""),
        f"scoped source original SHA mismatch: {source_id}",
    )
    if source_type == "notes_manuscript":
        content = raw.decode("utf-8")
        content_format = "markdown"
    else:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TheologicalEvidencePacketError(
                f"scoped sermon transcript is unreadable: {source_id}"
            ) from exc
        script = payload.get("script") if isinstance(payload, dict) else payload
        _require(
            isinstance(script, list) and bool(script),
            f"sermon transcript has no script: {source_id}",
        )
        lines: list[str] = []
        for segment in script:
            if not isinstance(segment, dict):
                continue
            text = str(segment.get("text") or "").strip()
            if not text:
                continue
            timeline = str(segment.get("start_timeline") or "").strip()
            if not timeline and isinstance(segment.get("start_time"), (int, float)):
                seconds = max(0, int(segment["start_time"]))
                timeline = f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
            if not timeline and segment.get("index") is not None:
                end_index = segment.get("end_index")
                timeline = (
                    f"{segment['index']}-{end_index}"
                    if end_index is not None
                    else str(segment["index"])
                )
            lines.append(f"[{timeline}] {text}" if timeline else text)
        content = "\n\n".join(lines)
        content_format = "timestamped_transcript"
    _require(bool(content.strip()), f"scoped source original is empty: {source_id}")
    return {
        "original_file_sha256": file_sha256,
        "content_format": content_format,
        "content": content,
    }


def build_scoped_source_originals(
    sources: Sequence[Mapping[str, Any]],
    *,
    reader: SourceOriginalReader | None = None,
    max_total_characters: int = MAX_DIRECT_SOURCE_ORIGINAL_CHARACTERS,
) -> dict[str, Any]:
    """Compile every complete scoped transcript/manuscript for runtime roles."""

    _require(bool(sources), "theological synthesis requires scoped source originals")
    source_ids = _unique(
        [_nonempty(item.get("source_id"), "source_id") for item in sources],
        "source original ids",
    )
    read = reader or _filesystem_source_original
    originals: list[dict[str, Any]] = []
    for source in sorted(sources, key=lambda item: str(item["source_id"])):
        source_id = str(source["source_id"])
        source_type = _nonempty(source.get("source_type"), f"{source_id}.source_type")
        _require(
            source_type in {"sermon_transcript", "notes_manuscript"},
            f"unsupported theological source type: {source_type}",
        )
        loaded = dict(read(source))
        content = _nonempty(loaded.get("content"), f"{source_id}.content")
        original_file_sha256 = _nonempty(
            loaded.get("original_file_sha256"), f"{source_id}.original_file_sha256"
        )
        _require(
            original_file_sha256 == str(source.get("source_sha256") or ""),
            f"scoped source original SHA mismatch: {source_id}",
        )
        originals.append(
            {
                "source_id": source_id,
                "source_type": source_type,
                "title": str(
                    source.get("title") or source.get("transcript_id") or source_id
                ),
                "transcript_id": source.get("transcript_id"),
                "source_version_sha256": original_file_sha256,
                "content_format": _nonempty(
                    loaded.get("content_format"), f"{source_id}.content_format"
                ),
                "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "character_count": len(content),
                "content": content,
            }
        )
    manifest = [
        {key: value for key, value in item.items() if key != "content"}
        for item in originals
    ]
    total_character_count = sum(int(item["character_count"]) for item in originals)
    _require(
        total_character_count <= max_total_characters,
        "complete scoped originals exceed the direct context limit; "
        "batched source reading is required before generation",
    )
    packet = {
        "schema_version": "wang_scoped_source_originals_v1",
        "source_ids": sorted(source_ids),
        "source_types": sorted({str(item["source_type"]) for item in originals}),
        "coverage": {
            "source_count": len(originals),
            "sermon_transcript_count": sum(
                item["source_type"] == "sermon_transcript" for item in originals
            ),
            "notes_manuscript_count": sum(
                item["source_type"] == "notes_manuscript" for item in originals
            ),
            "total_character_count": total_character_count,
            "direct_context_limit_characters": max_total_characters,
            "delivery_mode": "complete_originals_in_context",
            "overflow_policy": "stop_before_generation_pending_batched_reading",
            "truncation_allowed": False,
        },
        "originals": originals,
        "manifest": manifest,
        "manifest_sha256": sha256_json(manifest),
    }
    packet["source_originals_sha256"] = sha256_json(packet)
    return packet


def validate_scoped_source_originals(
    packet: Mapping[str, Any],
    *,
    source_documents: Sequence[Mapping[str, Any]],
) -> None:
    _require(
        packet.get("schema_version") == "wang_scoped_source_originals_v1",
        "unsupported scoped source originals schema",
    )
    originals = list(packet.get("originals") or [])
    manifest = list(packet.get("manifest") or [])
    coverage = dict(packet.get("coverage") or {})
    expected_ids = sorted(str(item["source_id"]) for item in source_documents)
    received_ids = [str(item.get("source_id") or "") for item in originals]
    _require(
        received_ids == sorted(received_ids) and received_ids == expected_ids,
        "scoped source originals must cover every source document exactly once",
    )
    documents = {str(item["source_id"]): item for item in source_documents}
    for item in originals:
        source_id = str(item["source_id"])
        content = _nonempty(item.get("content"), f"{source_id}.content")
        _require(
            item.get("source_type") == documents[source_id].get("source_type"),
            f"scoped source original type mismatch: {source_id}",
        )
        _require(
            item.get("source_version_sha256")
            == documents[source_id].get("source_sha256"),
            f"scoped source original version mismatch: {source_id}",
        )
        _require(
            item.get("content_sha256")
            == hashlib.sha256(content.encode("utf-8")).hexdigest(),
            f"scoped source original content SHA mismatch: {source_id}",
        )
    expected_manifest = [
        {key: value for key, value in item.items() if key != "content"}
        for item in originals
    ]
    _require(manifest == expected_manifest, "scoped source original manifest mismatch")
    _require(
        coverage.get("source_count") == len(originals)
        and coverage.get("sermon_transcript_count")
        == sum(item.get("source_type") == "sermon_transcript" for item in originals)
        and coverage.get("notes_manuscript_count")
        == sum(item.get("source_type") == "notes_manuscript" for item in originals)
        and coverage.get("total_character_count")
        == sum(int(item.get("character_count") or 0) for item in originals)
        and coverage.get("delivery_mode") == "complete_originals_in_context"
        and coverage.get("truncation_allowed") is False,
        "scoped source original coverage mismatch",
    )
    _require(
        packet.get("manifest_sha256") == sha256_json(manifest),
        "source original manifest SHA mismatch",
    )
    body = {
        key: value
        for key, value in packet.items()
        if key != "source_originals_sha256"
    }
    _require(
        packet.get("source_originals_sha256") == sha256_json(body),
        "scoped source originals SHA mismatch",
    )


def make_editorial_scope(
    *,
    scope_id: str,
    working_title: str,
    reader_question: str,
    passage_refs: Sequence[str],
    structure_revision_id: str,
    publication_profile_id: str,
    explicit_exclusions: Sequence[Mapping[str, str]] = (),
    editorial_constraints: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Create the immutable human/editor-owned entry contract."""

    payload = {
        "schema_version": "wang_theological_editorial_scope_v2",
        "scope_id": _nonempty(scope_id, "scope_id"),
        "product_kind": "theological_topic_essay",
        "working_title": _nonempty(working_title, "working_title"),
        "reader_question": _nonempty(reader_question, "reader_question"),
        "passage_refs": sorted(_unique(list(passage_refs), "passage_refs")),
        "structure_revision_id": _nonempty(
            structure_revision_id, "structure_revision_id"
        ),
        "publication_profile_id": _nonempty(
            publication_profile_id, "publication_profile_id"
        ),
        "explicit_exclusions": sorted(
            [
                {
                    "record_id": _nonempty(item.get("record_id"), "record_id"),
                    "reason": _nonempty(item.get("reason"), "exclusion reason"),
                }
                for item in explicit_exclusions
            ],
            key=lambda item: item["record_id"],
        ),
        "editorial_constraints": [
            {
                "constraint_id": _nonempty(
                    item.get("constraint_id"), "editorial constraint id"
                ),
                "constraint_type": _nonempty(
                    item.get("constraint_type"), "editorial constraint type"
                ),
                "target_record_ids": _unique(
                    list(item.get("target_record_ids") or []),
                    "editorial constraint target record ids",
                ),
                "required_value": _nonempty(
                    item.get("required_value"), "editorial constraint required value"
                ),
                "instruction": _nonempty(
                    item.get("instruction"), "editorial constraint instruction"
                ),
                "rationale": _nonempty(
                    item.get("rationale"), "editorial constraint rationale"
                ),
                "feedback_artifact_sha256": _nonempty(
                    item.get("feedback_artifact_sha256"),
                    "editorial constraint feedback artifact SHA",
                ),
            }
            for item in editorial_constraints
        ],
        "editorial_attribution": "church_editor",
        "not_professor_words": True,
    }
    payload["scope_sha256"] = sha256_json(payload)
    return payload


def validate_editorial_scope(scope: Mapping[str, Any]) -> None:
    _require(
        scope.get("schema_version")
        in {
            "wang_theological_editorial_scope_v1",
            "wang_theological_editorial_scope_v2",
        },
        "unsupported editorial scope schema",
    )
    _require(
        scope.get("product_kind") == "theological_topic_essay",
        "scope product_kind must be theological_topic_essay",
    )
    for field in (
        "scope_id",
        "working_title",
        "reader_question",
        "structure_revision_id",
        "publication_profile_id",
    ):
        _nonempty(scope.get(field), field)
    _unique(scope.get("passage_refs") or [], "passage_refs")
    _require(
        scope.get("editorial_attribution") == "church_editor"
        and scope.get("not_professor_words") is True,
        "scope must identify its questions and framing as editorial",
    )
    constraints = list(scope.get("editorial_constraints") or [])
    if scope.get("schema_version") == "wang_theological_editorial_scope_v2":
        constraint_ids = _unique(
            [str(item.get("constraint_id") or "") for item in constraints],
            "editorial constraint ids",
        )
        _require(
            len(constraint_ids) == len(constraints),
            "editorial constraints must have IDs",
        )
        for item in constraints:
            constraint_id = str(item["constraint_id"])
            constraint_type = str(item.get("constraint_type") or "")
            _require(
                constraint_type
                in {
                    "material_placement",
                    "section_count",
                    "prohibited_article_function",
                    "approved_outline",
                },
                f"{constraint_id}: unsupported editorial constraint type",
            )
            _unique(
                item.get("target_record_ids") or [],
                f"{constraint_id} target record ids",
            )
            for field in (
                "required_value",
                "instruction",
                "rationale",
                "feedback_artifact_sha256",
            ):
                _nonempty(item.get(field), f"{constraint_id}.{field}")
            if constraint_type == "material_placement":
                _require(
                    bool(item.get("target_record_ids"))
                    and item.get("required_value") in {"footnote", "inline_note"},
                    f"{constraint_id}: material placement needs records and note mode",
                )
            elif constraint_type == "section_count":
                _require(
                    str(item.get("required_value") or "").isdigit()
                    and int(item["required_value"]) > 0,
                    f"{constraint_id}: section count must be a positive integer",
                )
            elif constraint_type == "prohibited_article_function":
                _require(
                    item.get("required_value") in ARTICLE_FUNCTIONS,
                    f"{constraint_id}: unknown prohibited article function",
                )
    stated = str(scope.get("scope_sha256") or "")
    body = {key: value for key, value in scope.items() if key != "scope_sha256"}
    _require(stated == sha256_json(body), "editorial scope SHA mismatch")


def _evidence_fragment_ids(step: Mapping[str, Any]) -> list[str]:
    values = list(step.get("source_fragment_ids") or [])
    legacy = step.get("source_fragment_id")
    if legacy and legacy not in values:
        values.append(str(legacy))
    return values


def _dependency(
    collection: str, record: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "collection": collection,
        "record_id": _record_id(collection, record),
        "revision": int(record.get("revision") or 1),
        "sha256": semantic_record_sha(record),
    }


def compile_theological_evidence_packet(
    *,
    scope: Mapping[str, Any],
    records: Mapping[str, Sequence[Mapping[str, Any]]],
    source_original_reader: SourceOriginalReader | None = None,
) -> dict[str, Any]:
    """Compile the exact reviewed graph one draft-first Author may inspect.

    The compiler selects by the reviewed structure, never by semantic search or
    term frequency.  It includes every focal viewpoint and records the absence
    of a source-local route instead of manufacturing one.
    """

    validate_editorial_scope(scope)
    indexes = {
        collection: {
            _record_id(collection, record): dict(record) for record in rows
        }
        for collection, rows in records.items()
    }

    revision_id = str(scope["structure_revision_id"])
    structure_revision = indexes.get("viewpoint_structure_revisions", {}).get(
        revision_id
    )
    _require(structure_revision is not None, f"missing structure revision {revision_id}")
    _require(
        structure_revision.get("review_status") in APPROVED,
        f"structure revision is not approved: {revision_id}",
    )
    structure_id = str(structure_revision["structure_id"])
    structure = indexes.get("viewpoint_structures", {}).get(structure_id)
    _require(structure is not None, f"missing structure {structure_id}")
    _require(
        structure.get("effective_state") == "active"
        and structure.get("current_revision_id") == revision_id,
        f"structure revision is not active/current: {revision_id}",
    )
    _require(
        structure.get("review_status") in APPROVED,
        f"structure is not approved: {structure_id}",
    )

    focal_rows = list(structure_revision.get("focal_viewpoints") or [])
    _require(bool(focal_rows), "structure has no focal viewpoints")
    focal_revision_ids = _unique(
        [str(item.get("viewpoint_revision_id") or "") for item in focal_rows],
        "structure focal viewpoint revisions",
    )

    active_links_by_viewpoint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in indexes.get("viewpoint_claim_links", {}).values():
        if (
            link.get("effective_state") == "active"
            and link.get("review_status") in APPROVED
        ):
            active_links_by_viewpoint[str(link.get("viewpoint_id"))].append(link)

    route_bundles_by_viewpoint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    all_route_attestations = list(
        indexes.get("argument_route_attestations", {}).values()
    )
    for route in indexes.get("argument_routes", {}).values():
        if (
            route.get("route_status") != "active"
            or route.get("review_status") not in APPROVED
        ):
            continue
        route_revision_id = str(route.get("current_revision_id") or "")
        route_revision = indexes.get("argument_route_revisions", {}).get(
            route_revision_id
        )
        if (
            route_revision is None
            or route_revision.get("review_status") not in APPROVED
        ):
            continue
        attestations = sorted(
            [
                item
                for item in all_route_attestations
                if item.get("argument_route_id") == route.get("argument_route_id")
                and item.get("effective_state") == "active"
                and item.get("review_status") in APPROVED
                and item.get("validated_against_route_revision_id")
                == route_revision_id
            ],
            key=lambda item: str(item["argument_route_attestation_id"]),
        )
        if not attestations:
            continue
        route_bundles_by_viewpoint[str(route["conclusion_viewpoint_id"])].append(
            {
                "route": route,
                "revision": route_revision,
                "attestations": attestations,
                "full_attestation_count": sum(
                    item.get("completeness") == "full" for item in attestations
                ),
                "distinct_full_source_count": len(
                    {
                        str(item["source_id"])
                        for item in attestations
                        if item.get("completeness") == "full"
                    }
                ),
            }
        )

    findings: list[dict[str, Any]] = []
    focal_viewpoints: list[dict[str, Any]] = []
    selected_links: list[dict[str, Any]] = []
    selected_routes: list[dict[str, Any]] = []
    selected_claim_ids: set[str] = set()
    selected_evidence_ids: set[str] = set()
    selected_fragment_ids: set[str] = set()
    selected_source_ids: set[str] = set()

    for focal in focal_rows:
        role = str(focal.get("structure_role") or "")
        _require(role in STRUCTURE_ROLES, f"unsupported structure role: {role}")
        viewpoint_revision_id = str(focal["viewpoint_revision_id"])
        viewpoint_revision = indexes.get("viewpoint_revisions", {}).get(
            viewpoint_revision_id
        )
        _require(
            viewpoint_revision is not None,
            f"missing viewpoint revision {viewpoint_revision_id}",
        )
        viewpoint_id = str(viewpoint_revision["viewpoint_id"])
        viewpoint = indexes.get("canonical_viewpoints", {}).get(viewpoint_id)
        _require(viewpoint is not None, f"missing viewpoint {viewpoint_id}")
        _require(
            viewpoint.get("identity_status") == "active"
            and viewpoint.get("current_revision_id") == viewpoint_revision_id,
            f"viewpoint revision is not active/current: {viewpoint_revision_id}",
        )
        _require(
            viewpoint.get("review_status") in APPROVED
            and viewpoint_revision.get("review_status") in APPROVED,
            f"viewpoint revision is not approved: {viewpoint_revision_id}",
        )

        links = sorted(
            [
                link
                for link in active_links_by_viewpoint.get(viewpoint_id, [])
                if link.get("validated_against_viewpoint_revision_id")
                == viewpoint_revision_id
            ],
            key=lambda item: str(item["viewpoint_claim_link_id"]),
        )
        route_bundles = sorted(
            [
                bundle
                for bundle in route_bundles_by_viewpoint.get(viewpoint_id, [])
                if bundle["revision"].get(
                    "validated_against_conclusion_viewpoint_revision_id"
                )
                == viewpoint_revision_id
            ],
            key=lambda item: str(
                item["revision"]["argument_route_revision_id"]
            ),
        )
        member_claim_ids = sorted({str(link["claim_id"]) for link in links})
        if not member_claim_ids:
            findings.append(
                {
                    "code": "viewpoint_has_no_active_claim",
                    "severity": "error",
                    "viewpoint_revision_id": viewpoint_revision_id,
                    "message": "The focal viewpoint has no active approved Claim link.",
                }
            )
        if not any(bundle["full_attestation_count"] for bundle in route_bundles):
            findings.append(
                {
                    "code": "viewpoint_has_no_full_argument_route",
                    "severity": "warning",
                    "viewpoint_revision_id": viewpoint_revision_id,
                    "message": (
                        "No source fully attests an approved ArgumentRoute; authoring "
                        "must use a bounded claim or leave the viewpoint out."
                    ),
                }
            )

        selected_links.extend(links)
        selected_routes.extend(route_bundles)
        selected_claim_ids.update(member_claim_ids)
        for bundle in route_bundles:
            for attestation in bundle["attestations"]:
                selected_claim_ids.update(str(value) for value in attestation.get("claim_ids") or [])
                selected_source_ids.add(str(attestation["source_id"]))
                for binding in attestation.get("step_bindings") or []:
                    selected_evidence_ids.update(
                        str(value) for value in binding.get("evidence_step_ids") or []
                    )
                    selected_fragment_ids.update(
                        str(value) for value in binding.get("source_fragment_ids") or []
                    )
        focal_viewpoints.append(
            {
                "structure_role": role,
                "basis_claim_ids": sorted(
                    str(value) for value in focal.get("basis_claim_ids") or []
                ),
                "viewpoint": viewpoint,
                "revision": viewpoint_revision,
                "active_claim_link_ids": [
                    str(item["viewpoint_claim_link_id"]) for item in links
                ],
                "member_claim_ids": member_claim_ids,
                "argument_route_revision_ids": [
                    str(item["revision"]["argument_route_revision_id"])
                    for item in route_bundles
                ],
            }
        )

    claims_index = indexes.get("claims", {})
    evidence_index = indexes.get("evidence_steps", {})
    fragments_index = indexes.get("source_fragments", {})
    sources_index = indexes.get("source_documents", {})
    claims = [
        claims_index[value]
        for value in sorted(selected_claim_ids)
        if value in claims_index
    ]
    missing_claims = sorted(selected_claim_ids - set(claims_index))
    _require(not missing_claims, f"missing claims: {missing_claims}")
    for claim in claims:
        selected_evidence_ids.update(
            str(value) for value in claim.get("evidence_step_ids") or []
        )
    evidence = [
        evidence_index[value]
        for value in sorted(selected_evidence_ids)
        if value in evidence_index
    ]
    missing_evidence = sorted(selected_evidence_ids - set(evidence_index))
    _require(not missing_evidence, f"missing evidence steps: {missing_evidence}")
    for step in evidence:
        selected_fragment_ids.update(_evidence_fragment_ids(step))
    fragments = [
        fragments_index[value]
        for value in sorted(selected_fragment_ids)
        if value in fragments_index
    ]
    missing_fragments = sorted(selected_fragment_ids - set(fragments_index))
    _require(not missing_fragments, f"missing source fragments: {missing_fragments}")
    selected_source_ids.update(str(item["source_id"]) for item in fragments)
    sources = [
        sources_index[value]
        for value in sorted(selected_source_ids)
        if value in sources_index
    ]
    missing_sources = sorted(selected_source_ids - set(sources_index))
    _require(not missing_sources, f"missing source documents: {missing_sources}")

    selected_viewpoint_ids = {
        str(item["revision"]["viewpoint_id"]) for item in focal_viewpoints
    }
    relations = sorted(
        [
            relation
            for relation in indexes.get("viewpoint_relations", {}).values()
            if relation.get("source_viewpoint_id") in selected_viewpoint_ids
            and relation.get("target_viewpoint_id") in selected_viewpoint_ids
            and relation.get("review_status") in APPROVED
        ],
        key=lambda item: str(item["viewpoint_relation_id"]),
    )

    dependency_rows: list[tuple[str, Mapping[str, Any]]] = [
        ("viewpoint_structures", structure),
        ("viewpoint_structure_revisions", structure_revision),
    ]
    for item in focal_viewpoints:
        dependency_rows.extend(
            [
                ("canonical_viewpoints", item["viewpoint"]),
                ("viewpoint_revisions", item["revision"]),
            ]
        )
    dependency_rows.extend(
        ("viewpoint_claim_links", item) for item in selected_links
    )
    for bundle in selected_routes:
        dependency_rows.extend(
            [
                ("argument_routes", bundle["route"]),
                ("argument_route_revisions", bundle["revision"]),
                *[
                    ("argument_route_attestations", item)
                    for item in bundle["attestations"]
                ],
            ]
        )
    dependency_rows.extend(("viewpoint_relations", item) for item in relations)
    dependency_rows.extend(("claims", item) for item in claims)
    dependency_rows.extend(("evidence_steps", item) for item in evidence)
    dependency_rows.extend(("source_fragments", item) for item in fragments)
    dependency_rows.extend(("source_documents", item) for item in sources)
    dependencies = sorted(
        {
            (collection, _record_id(collection, record)): _dependency(
                collection, record
            )
            for collection, record in dependency_rows
        }.values(),
        key=lambda item: (item["collection"], item["record_id"]),
    )

    packet = {
        "schema_version": "wang_theological_evidence_packet_v1",
        "scope": dict(scope),
        "structure": {
            "pointer": structure,
            "revision": structure_revision,
            "revision_sha256": semantic_record_sha(structure_revision),
        },
        "focal_viewpoints": focal_viewpoints,
        "argument_routes": selected_routes,
        "claims": claims,
        "evidence_steps": evidence,
        "source_fragments": fragments,
        "source_documents": sources,
        "source_originals": build_scoped_source_originals(
            sources,
            reader=source_original_reader,
        ),
        "relations": relations,
        "compiler_findings": findings,
        "compiler_readiness": (
            "insufficient_material"
            if any(item["severity"] == "error" for item in findings)
            else "ready_for_authoring"
        ),
        "dependency_manifest": dependencies,
        "dependency_manifest_sha256": sha256_json(dependencies),
    }
    packet["evidence_packet_sha256"] = sha256_json(packet)
    return packet


def validate_theological_evidence_packet(packet: Mapping[str, Any]) -> None:
    _require(
        packet.get("schema_version") == "wang_theological_evidence_packet_v1",
        "unsupported theological evidence packet schema",
    )
    validate_editorial_scope(packet.get("scope") or {})
    validate_scoped_source_originals(
        packet.get("source_originals") or {},
        source_documents=packet.get("source_documents") or [],
    )
    dependencies = list(packet.get("dependency_manifest") or [])
    _require(
        packet.get("dependency_manifest_sha256") == sha256_json(dependencies),
        "evidence dependency manifest SHA mismatch",
    )
    stated = str(packet.get("evidence_packet_sha256") or "")
    body = {
        key: value
        for key, value in packet.items()
        if key != "evidence_packet_sha256"
    }
    _require(stated == sha256_json(body), "theological evidence packet SHA mismatch")
