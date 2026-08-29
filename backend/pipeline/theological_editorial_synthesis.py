"""Contracts for evidence-bound theological editorial synthesis.

This module deliberately stops before reader prose.  It turns one reviewed
ViewpointStructure into the bounded evidence and editorial decisions an Author
may consume.  It is not a theological judge and it never infers a missing
positive position from the professor's rejection of another position.
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
STOP_STATUSES = frozenset({
    "insufficient_material",
    "unresolved_structure",
    "human_editor_required",
})


class TheologicalEditorialContractError(ValueError):
    """A synthesis artifact violates an authority or coverage contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise TheologicalEditorialContractError(message)


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
            raise TheologicalEditorialContractError(
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
) -> dict[str, Any]:
    """Create the immutable human/editor-owned entry contract."""

    payload = {
        "schema_version": "wang_theological_editorial_scope_v1",
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
        "editorial_attribution": "church_editor",
        "not_professor_words": True,
    }
    payload["scope_sha256"] = sha256_json(payload)
    return payload


def validate_editorial_scope(scope: Mapping[str, Any]) -> None:
    _require(
        scope.get("schema_version") == "wang_theological_editorial_scope_v1",
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
    """Compile the exact reviewed graph one Composition Agent may inspect.

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
                        "No source fully attests an approved ArgumentRoute; Composition "
                        "must use a bounded claim or route the viewpoint out."
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
            else "ready_for_composition"
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


def validate_editorial_brief_candidate(
    candidate: Mapping[str, Any], *, evidence_packet: Mapping[str, Any]
) -> None:
    """Validate composition judgment without pretending to make it."""

    validate_theological_evidence_packet(evidence_packet)
    _require(
        candidate.get("schema_version")
        == "wang_theological_editorial_brief_candidate_v1",
        "unsupported theological editorial brief candidate schema",
    )
    _require(
        candidate.get("evidence_packet_sha256")
        == evidence_packet.get("evidence_packet_sha256"),
        "brief candidate belongs to another evidence packet",
    )
    status = str(candidate.get("status") or "")
    _require(
        status in {"ready", *STOP_STATUSES},
        f"unsupported brief status: {status}",
    )
    _nonempty(candidate.get("summary"), "summary")

    focal_by_revision = {
        str(item["revision"]["viewpoint_revision_id"]): item
        for item in evidence_packet.get("focal_viewpoints") or []
    }
    coverage = list(candidate.get("viewpoint_coverage") or [])
    coverage_ids = _unique(
        [str(item.get("viewpoint_revision_id") or "") for item in coverage],
        "brief viewpoint coverage",
    )
    _require(
        set(coverage_ids) == set(focal_by_revision),
        "brief must include or route out every focal viewpoint exactly once",
    )
    sections = list(candidate.get("sections") or [])
    section_ids = _unique(
        [str(item.get("section_id") or "") for item in sections], "section ids"
    )
    section_by_id = {str(item["section_id"]): item for item in sections}
    for index, section in enumerate(sections, start=1):
        _nonempty(section.get("heading"), f"sections[{index}].heading")
        _nonempty(section.get("reader_function"), f"sections[{index}].reader_function")
        _require(
            section.get("article_function") in ARTICLE_FUNCTIONS,
            f"section {section.get('section_id')}: invalid article function",
        )
        _unique(section.get("viewpoint_revision_ids") or [], "section viewpoints")
        _unique(section.get("argument_route_revision_ids") or [], "section routes")

    if status != "ready":
        _require(
            bool(candidate.get("stop_reasons")),
            "non-ready brief needs formal stop reasons",
        )
        return

    _require(bool(sections), "ready brief requires sections")
    _nonempty(candidate.get("article_title"), "article_title")
    _nonempty(candidate.get("reader_takeaway"), "reader_takeaway")
    _require(
        candidate.get("reader_takeaway_attribution") == "editorial_synthesis",
        "reader takeaway must be explicitly attributed to editorial synthesis",
    )
    _require(not candidate.get("stop_reasons"), "ready brief cannot have stop reasons")

    included: set[str] = set()
    routed_out: set[str] = set()
    for item in coverage:
        revision_id = str(item["viewpoint_revision_id"])
        disposition = item.get("disposition")
        _require(
            disposition in {"include", "route_out"},
            f"{revision_id}: invalid coverage disposition",
        )
        section_id = str(item.get("section_id") or "")
        reason = str(item.get("reason") or "").strip()
        if disposition == "include":
            _require(section_id in section_by_id, f"{revision_id}: unknown section")
            _require(
                revision_id
                in set(section_by_id[section_id].get("viewpoint_revision_ids") or []),
                f"{revision_id}: coverage and section ledger disagree",
            )
            included.add(revision_id)
        else:
            _require(not section_id, f"{revision_id}: routed-out item cannot name a section")
            _require(bool(reason), f"{revision_id}: route_out needs a reason")
            routed_out.add(revision_id)

    ledger_included = {
        str(value)
        for section in sections
        for value in section.get("viewpoint_revision_ids") or []
    }
    _require(ledger_included == included, "section viewpoint ledger is not exact")
    roles_by_revision = {
        revision_id: str(item["structure_role"])
        for revision_id, item in focal_by_revision.items()
    }
    central_ids = {
        value for value, role in roles_by_revision.items() if role == "central_claim"
    }
    positive_ids = {
        value
        for value, role in roles_by_revision.items()
        if role == "positive_identification"
    }
    _require(central_ids <= included, "ready brief must include every central claim")
    _require(
        bool(positive_ids & included),
        "ready brief must include at least one positive identification",
    )
    _require(
        not any(
            roles_by_revision[value] == "negative_boundary"
            for value in candidate.get("reader_takeaway_viewpoint_revision_ids") or []
        ),
        "negative boundary cannot carry the reader takeaway",
    )

    route_by_revision = {
        str(item["revision"]["argument_route_revision_id"]): item
        for item in evidence_packet.get("argument_routes") or []
    }
    used_routes = {
        str(value)
        for section in sections
        for value in section.get("argument_route_revision_ids") or []
    }
    unknown_routes = used_routes - set(route_by_revision)
    _require(not unknown_routes, f"brief cites unknown routes: {sorted(unknown_routes)}")
    for section in sections:
        section_viewpoints = set(section.get("viewpoint_revision_ids") or [])
        for route_revision_id in section.get("argument_route_revision_ids") or []:
            route = route_by_revision[str(route_revision_id)]
            conclusion_id = str(
                route["revision"][
                    "validated_against_conclusion_viewpoint_revision_id"
                ]
            )
            _require(
                conclusion_id in section_viewpoints,
                f"route {route_revision_id} is separated from its conclusion",
            )
            _require(
                int(route.get("full_attestation_count") or 0) > 0,
                f"route {route_revision_id} has no full source attestation",
            )

    required_unresolved = set(
        evidence_packet.get("structure", {})
        .get("revision", {})
        .get("unresolved_items")
        or []
    )
    declared_unresolved = set(candidate.get("unresolved_items") or [])
    _require(
        required_unresolved <= declared_unresolved,
        "brief silently removed a structure unresolved item",
    )


def compile_approved_editorial_brief(
    *,
    candidate: Mapping[str, Any],
    evidence_packet: Mapping[str, Any],
    review: Mapping[str, Any],
) -> dict[str, Any]:
    validate_editorial_brief_candidate(candidate, evidence_packet=evidence_packet)
    _require(candidate.get("status") == "ready", "only a ready candidate can pass")
    _require(
        review.get("schema_version")
        == "wang_theological_editorial_brief_review_v1",
        "unsupported editorial brief review schema",
    )
    _require(review.get("decision") == "pass", "editorial brief review did not pass")
    _require(not review.get("findings"), "passing brief review cannot contain findings")
    candidate_sha = sha256_json(dict(candidate))
    _require(
        review.get("brief_candidate_sha256") == candidate_sha,
        "brief review belongs to another candidate",
    )
    payload = {
        "schema_version": "wang_theological_editorial_brief_v1",
        "scope_sha256": evidence_packet["scope"]["scope_sha256"],
        "evidence_packet_sha256": evidence_packet["evidence_packet_sha256"],
        "brief_candidate_sha256": candidate_sha,
        "brief_review_sha256": sha256_json(dict(review)),
        "editorial_decisions": dict(candidate),
    }
    payload["brief_sha256"] = sha256_json(payload)
    return payload


BRIEF_CANDIDATE_SCHEMA: dict[str, Any] = {
    "name": "wang_theological_editorial_brief_candidate_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {
                "type": "string",
                "enum": ["wang_theological_editorial_brief_candidate_v1"],
            },
            "evidence_packet_sha256": {"type": "string"},
            "status": {
                "type": "string",
                "enum": ["ready", *sorted(STOP_STATUSES)],
            },
            "summary": {"type": "string"},
            "article_title": {"type": "string"},
            "reader_takeaway": {"type": "string"},
            "reader_takeaway_attribution": {
                "type": "string",
                "enum": ["editorial_synthesis"],
            },
            "reader_takeaway_viewpoint_revision_ids": {
                "type": "array",
                "items": {"type": "string"},
            },
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "section_id": {"type": "string"},
                        "heading": {"type": "string"},
                        "article_function": {
                            "type": "string",
                            "enum": sorted(ARTICLE_FUNCTIONS),
                        },
                        "reader_function": {"type": "string"},
                        "viewpoint_revision_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "argument_route_revision_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "required_qualifications": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "prohibited_functions": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "section_id",
                        "heading",
                        "article_function",
                        "reader_function",
                        "viewpoint_revision_ids",
                        "argument_route_revision_ids",
                        "required_qualifications",
                        "prohibited_functions",
                    ],
                },
            },
            "viewpoint_coverage": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "viewpoint_revision_id": {"type": "string"},
                        "disposition": {
                            "type": "string",
                            "enum": ["include", "route_out"],
                        },
                        "section_id": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": [
                        "viewpoint_revision_id",
                        "disposition",
                        "section_id",
                        "reason",
                    ],
                },
            },
            "unresolved_items": {"type": "array", "items": {"type": "string"}},
            "stop_reasons": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "code": {"type": "string"},
                        "record_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "explanation": {"type": "string"},
                        "next_action": {"type": "string"},
                    },
                    "required": ["code", "record_ids", "explanation", "next_action"],
                },
            },
        },
        "required": [
            "schema_version",
            "evidence_packet_sha256",
            "status",
            "summary",
            "article_title",
            "reader_takeaway",
            "reader_takeaway_attribution",
            "reader_takeaway_viewpoint_revision_ids",
            "sections",
            "viewpoint_coverage",
            "unresolved_items",
            "stop_reasons",
        ],
    },
}


BRIEF_REVIEW_SCHEMA: dict[str, Any] = {
    "name": "wang_theological_editorial_brief_review_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {
                "type": "string",
                "enum": ["wang_theological_editorial_brief_review_v1"],
            },
            "scope_confirmation": {
                "type": "string",
                "enum": ["editorial_structure_and_material_no_theological_judgment"],
            },
            "brief_candidate_sha256": {"type": "string"},
            "decision": {
                "type": "string",
                "enum": ["pass", "changes_required", *sorted(STOP_STATUSES)],
            },
            "summary": {"type": "string"},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "finding_id": {"type": "string"},
                        "code": {
                            "type": "string",
                            "enum": [
                                "positive_center_missing",
                                "negative_material_displaces_center",
                                "unsupported_editorial_bridge",
                                "argument_route_not_source_local",
                                "modality_or_scope_upgraded",
                                "unresolved_item_silently_harmonized",
                                "focal_viewpoint_omitted",
                                "material_insufficient",
                                "product_axis_mismatch",
                                "other",
                            ],
                        },
                        "severity": {
                            "type": "string",
                            "enum": ["low", "medium", "high", "critical"],
                        },
                        "blocking": {"type": "boolean"},
                        "record_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "explanation": {"type": "string"},
                        "recommended_action": {"type": "string"},
                    },
                    "required": [
                        "finding_id",
                        "code",
                        "severity",
                        "blocking",
                        "record_ids",
                        "explanation",
                        "recommended_action",
                    ],
                },
            },
        },
        "required": [
            "schema_version",
            "scope_confirmation",
            "brief_candidate_sha256",
            "decision",
            "summary",
            "findings",
        ],
    },
}


BRIEF_REVISION_SCHEMA: dict[str, Any] = {
    "name": "wang_theological_editorial_brief_revision_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {
                "type": "string",
                "enum": ["wang_theological_editorial_brief_revision_v1"],
            },
            "baseline_candidate_sha256": {"type": "string"},
            "baseline_review_sha256": {"type": "string"},
            "finding_dispositions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "finding_id": {"type": "string"},
                        "resolution": {
                            "type": "string",
                            "enum": ["resolved", "cannot_resolve"],
                        },
                        "changed_fields": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "explanation": {"type": "string"},
                    },
                    "required": [
                        "finding_id",
                        "resolution",
                        "changed_fields",
                        "explanation",
                    ],
                },
            },
            "revised_candidate": BRIEF_CANDIDATE_SCHEMA["schema"],
        },
        "required": [
            "schema_version",
            "baseline_candidate_sha256",
            "baseline_review_sha256",
            "finding_dispositions",
            "revised_candidate",
        ],
    },
}


def validate_brief_review(
    review: Mapping[str, Any], *, candidate: Mapping[str, Any]
) -> None:
    _require(
        review.get("schema_version")
        == "wang_theological_editorial_brief_review_v1",
        "unsupported editorial brief review schema",
    )
    _require(
        review.get("scope_confirmation")
        == "editorial_structure_and_material_no_theological_judgment",
        "brief reviewer must confirm its non-theological scope",
    )
    _require(
        review.get("brief_candidate_sha256") == sha256_json(dict(candidate)),
        "brief review belongs to another candidate",
    )
    decision = str(review.get("decision") or "")
    findings = list(review.get("findings") or [])
    if decision == "pass":
        _require(candidate.get("status") == "ready", "non-ready candidate cannot pass")
        _require(not findings, "passing review cannot contain findings")
    else:
        _require(bool(findings), "non-passing brief review needs findings")
        _require(
            any(item.get("blocking") is True for item in findings),
            "non-passing brief review needs a blocking finding",
        )
    finding_ids = [str(item.get("finding_id") or "") for item in findings]
    _unique(finding_ids, "brief review finding ids")


def validate_brief_revision(
    revision: Mapping[str, Any],
    *,
    candidate: Mapping[str, Any],
    review: Mapping[str, Any],
    evidence_packet: Mapping[str, Any],
) -> None:
    _require(
        revision.get("schema_version")
        == "wang_theological_editorial_brief_revision_v1",
        "unsupported editorial brief revision schema",
    )
    _require(
        revision.get("baseline_candidate_sha256") == sha256_json(dict(candidate)),
        "brief revision belongs to another candidate",
    )
    _require(
        revision.get("baseline_review_sha256") == sha256_json(dict(review)),
        "brief revision belongs to another review",
    )
    findings = list(review.get("findings") or [])
    expected_ids = {str(item["finding_id"]) for item in findings}
    dispositions = list(revision.get("finding_dispositions") or [])
    received_ids = _unique(
        [str(item.get("finding_id") or "") for item in dispositions],
        "brief revision finding dispositions",
    )
    _require(
        set(received_ids) == expected_ids,
        "brief revision must dispose every review finding exactly once",
    )
    for item in dispositions:
        _nonempty(item.get("explanation"), "finding disposition explanation")
        if item.get("resolution") == "resolved":
            _require(
                bool(item.get("changed_fields")),
                f"{item.get('finding_id')}: resolved disposition needs changed_fields",
            )
    revised = revision.get("revised_candidate") or {}
    validate_editorial_brief_candidate(revised, evidence_packet=evidence_packet)
    if any(item.get("resolution") == "cannot_resolve" for item in dispositions):
        _require(
            revised.get("status") == "human_editor_required",
            "cannot_resolve must produce human_editor_required",
        )
    else:
        _require(
            revised.get("status") == "ready",
            "resolved composition findings must produce a ready candidate",
        )
