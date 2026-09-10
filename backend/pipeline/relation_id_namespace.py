"""Deterministically globalize extraction-generation identifiers.

The cross-section model is asked to emit short identifiers such as ``XER001``
and ``XCR001``.  Those identifiers are local to one source/model call.  Version
2 packages allowed them to escape into reviewed packages, where a batch merge
could reject the duplicate and the canonical store could silently overwrite a
different source's relation under the same ``(collection, object_id)`` key.

The source key alone is not enough: a later response can reuse ``CL001`` for a
different statement. New packages therefore bind every generated object and
relation to the exact input fingerprint and canonical model-output hash. The legacy
migration remains deliberately narrower: it changes only relation identifiers
and their explicit consensus references, and records an auditable manifest.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from backend.api.canonical_repository.postgres_store import sha256_json


MIGRATION_SCHEMA_VERSION = "wang_relation_id_namespace_migration_v1"
LEGACY_EVIDENCE_RELATION_ID = re.compile(r"XER\d+\Z")
LEGACY_CLAIM_RELATION_ID = re.compile(r"XCR\d+\Z")
CONSENSUS_CLAIM_RELATION_REFERENCE_FIELDS = (
    "removed_claim_relation_ids",
    "dissolved_claim_relation_ids",
)


class RelationIdNamespaceError(ValueError):
    """Raised when an ID-only migration cannot be proved safe."""


def package_source_key(package: Mapping[str, Any]) -> str:
    """Return the same stable source key detailed extraction namespaces with."""

    sources = package.get("source_documents") or []
    if len(sources) != 1:
        raise RelationIdNamespaceError(
            "relation-id namespace migration requires exactly one source document"
        )
    source_key = str(
        sources[0].get("transcript_id") or sources[0].get("source_id") or ""
    ).strip()
    if not source_key:
        raise RelationIdNamespaceError("source document has no stable identity")
    return source_key


def source_namespace(source_key: str) -> str:
    """The canonical extraction namespace for one source."""

    value = str(source_key or "").strip()
    if not value:
        raise RelationIdNamespaceError("source key cannot be empty")
    return f"DK-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]}"


def generation_namespace(
    source_key: str, extraction_fingerprint: str, model_output_sha256: str
) -> str:
    """Identify one exact extraction generation, not a model-local ordinal.

    ``CL001`` and ``E001`` are positions in one model response. A later model
    response can assign those ordinals to different statements, so source-only
    namespacing cannot prove record identity and could preserve an old review
    decision on new content. Exact input and output identities make equal runs
    stable while making distinct generations disjoint.
    """

    values = [
        str(source_key or "").strip(),
        str(extraction_fingerprint or "").strip(),
        str(model_output_sha256 or "").strip(),
    ]
    if not all(values):
        raise RelationIdNamespaceError(
            "generation namespace requires source, extraction fingerprint, and model output SHA"
        )
    encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    # Exact generations are new identities, so they are not constrained by the
    # 12-hex legacy source namespace. Twenty hex digits keep IDs readable while
    # making an accidental namespace collision negligible; the store's global
    # unique index remains the final fail-closed guard.
    return f"DK-{hashlib.sha256(encoded).hexdigest()[:20]}"


def package_record_namespace(package: Mapping[str, Any]) -> str:
    """Return the record namespace declared by an exact package generation."""

    declared = str((package.get("extraction") or {}).get("record_namespace") or "").strip()
    if declared:
        if not re.fullmatch(r"DK-(?:[0-9a-f]{12}|[0-9a-f]{20})", declared):
            raise RelationIdNamespaceError(
                f"package declares invalid record namespace {declared!r}"
            )
        return declared
    return source_namespace(package_source_key(package))


def namespaced_id(namespace: str, relation_id: Any) -> str:
    """Place a model-local ID in an already-resolved record namespace."""

    value = str(relation_id or "").strip()
    if not value:
        raise RelationIdNamespaceError("cross-section relation has no id")
    if value.startswith(f"{namespace}-"):
        return value
    return f"{namespace}-{value}"


def namespaced_relation_id(source_key: str, relation_id: Any) -> str:
    """Namespace one model-local relation id without changing an existing one."""

    return namespaced_id(source_namespace(source_key), relation_id)


def package_relation_id(package: Mapping[str, Any], relation_id: Any) -> str:
    """Namespace a relation in the package's exact extraction generation."""

    return namespaced_id(package_record_namespace(package), relation_id)


def is_source_extraction_relation_id(source_key: str, relation_id: Any) -> bool:
    """Whether an ID explicitly belongs to this source's extraction pipeline.

    Endpoint inference is not ownership: a later curated edge may happen to
    connect two records from the same source.  Only the extraction ID dialect
    (including sectioned and cross-section variants) is safe to supersede when
    a new extraction omits it.
    """

    return is_namespaced_extraction_relation_id(
        source_namespace(source_key), relation_id
    )


def is_namespaced_extraction_relation_id(namespace: str, relation_id: Any) -> bool:
    """Whether an edge id belongs to an explicit generated namespace."""

    escaped = re.escape(str(namespace or ""))
    return bool(
        escaped
        and re.fullmatch(
            rf"{escaped}-(?:P\d+-)?(?:ER|CR|XER|XCR)\d+",
            str(relation_id or ""),
        )
    )


def is_package_extraction_relation_id(
    package: Mapping[str, Any], relation_id: Any
) -> bool:
    """Whether an edge belongs to this exact package generation."""

    return is_namespaced_extraction_relation_id(
        package_record_namespace(package), relation_id
    )


def _legacy_values(value: Any, *, path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            findings.extend(_legacy_values(child, path=f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(_legacy_values(child, path=f"{path}[{index}]"))
    elif isinstance(value, str) and (
        LEGACY_EVIDENCE_RELATION_ID.fullmatch(value)
        or LEGACY_CLAIM_RELATION_ID.fullmatch(value)
    ):
        findings.append(f"{path}={value}")
    return findings


def _assert_round_trip(
    original: Mapping[str, Any], migrated: Mapping[str, Any], reverse: Mapping[str, str]
) -> None:
    """Prove the effective package differs only by the declared ID bijection."""

    restored = json.loads(json.dumps(migrated, ensure_ascii=False))

    def reverse_values(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: reverse_values(child) for key, child in value.items()}
        if isinstance(value, list):
            return [reverse_values(child) for child in value]
        if isinstance(value, str):
            return reverse.get(value, value)
        return value

    restored = reverse_values(restored)
    if restored != original:
        raise RelationIdNamespaceError(
            "relation-id migration failed its ID-only round-trip proof"
        )


def migrate_legacy_cross_section_relation_ids(
    package: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return an effective package and SHA-bound ID-only migration manifest.

    The input is never mutated.  Already-global packages pass through with a
    ``not_required`` manifest, which lets merge and ingest use this boundary
    unconditionally rather than relying on callers to identify legacy files.
    """

    original = json.loads(json.dumps(package, ensure_ascii=False))
    migrated = json.loads(json.dumps(package, ensure_ascii=False))
    source_key = package_source_key(original)
    namespace = package_record_namespace(original)
    input_sha = sha256_json(original)
    id_map: dict[str, str] = {}
    changed_paths: list[dict[str, str]] = []

    def map_id(value: Any, *, pattern: re.Pattern[str], path: str) -> str:
        old = str(value or "")
        if not pattern.fullmatch(old):
            return old
        new = namespaced_id(namespace, old)
        prior = id_map.setdefault(old, new)
        if prior != new:
            raise RelationIdNamespaceError(f"non-bijective mapping for {old}")
        changed_paths.append({"path": path, "before": old, "after": new})
        return new

    for index, row in enumerate(migrated.get("knowledge_relations") or []):
        row["relation_id"] = map_id(
            row.get("relation_id"),
            pattern=LEGACY_EVIDENCE_RELATION_ID,
            path=f"$.knowledge_relations[{index}].relation_id",
        )
    for index, row in enumerate(migrated.get("claim_relations") or []):
        row["claim_relation_id"] = map_id(
            row.get("claim_relation_id"),
            pattern=LEGACY_CLAIM_RELATION_ID,
            path=f"$.claim_relations[{index}].claim_relation_id",
        )

    consensus = migrated.get("consensus_application") or {}
    for field in CONSENSUS_CLAIM_RELATION_REFERENCE_FIELDS:
        if field not in consensus:
            continue
        consensus[field] = [
            map_id(
                value,
                pattern=LEGACY_CLAIM_RELATION_ID,
                path=f"$.consensus_application.{field}[{index}]",
            )
            for index, value in enumerate(consensus.get(field) or [])
        ]

    leftovers = _legacy_values(migrated)
    if leftovers:
        raise RelationIdNamespaceError(
            "legacy cross-section relation IDs remain at unsupported paths: "
            + ", ".join(leftovers)
        )
    reverse = {new: old for old, new in id_map.items()}
    if len(reverse) != len(id_map):
        raise RelationIdNamespaceError("relation-id namespace mapping is not bijective")
    _assert_round_trip(original, migrated, reverse)

    output_sha = sha256_json(migrated)
    manifest = {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "status": "applied" if changed_paths else "not_required",
        "source_key": source_key,
        "source_namespace": namespace,
        "input_canonical_sha256": input_sha,
        "output_canonical_sha256": output_sha,
        "semantic_change": "none_relation_identifiers_only",
        "round_trip_verified": True,
        "id_map": dict(sorted(id_map.items())),
        "changed_paths": changed_paths,
        "summary": {
            "identifiers_mapped": len(id_map),
            "references_rewritten": len(changed_paths),
        },
    }
    return migrated, manifest
