"""Deterministic helpers for moving legacy source anchors to spoken-body space.

This module deliberately has no database or file-writing code.  A caller must
freeze its own source cohort, read the current source bytes, and submit the
returned records through the canonical ChangeSet store.  Ambiguous or missing
text is a finding, never an invitation to fuzzy-match professor source.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from backend.pipeline.source_projection import (
    LOCATOR_SPACE,
    SourceProjection,
    is_editorial_row,
    live_script,
    project_script,
)
from backend.api.canonical_repository.postgres_store import (
    ChangeOperation,
    ChangeSetPlan,
    operation_fingerprint_rows,
    record_content_sha,
    sha256_json,
)


LEGACY_LOCATOR = re.compile(r"^S(\d{4})$")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class BodyRow:
    locator: str
    physical_ordinal: int
    source_segment_index: Any
    text: str


@dataclass(frozen=True)
class AnchorResolution:
    status: str
    locator: str | None = None
    source_segment_index: Any = None
    paragraph_text: str | None = None
    proof: str | None = None


class BodyLocatorIndex:
    """Index current source rows without confusing editorial rows with speech."""

    def __init__(self, script: Any):
        rows: list[BodyRow] = []
        for physical_ordinal, row in enumerate(live_script(script), 1):
            if is_editorial_row(row):
                continue
            rows.append(
                BodyRow(
                    locator=f"S{len(rows) + 1:04d}",
                    physical_ordinal=physical_ordinal,
                    source_segment_index=row.get("index"),
                    text=str(row.get("text") or ""),
                )
            )
        self.rows = tuple(rows)
        self.by_physical_ordinal = {row.physical_ordinal: row for row in rows}
        self.by_source_segment_index: dict[str, tuple[BodyRow, ...]] = {}
        for row in rows:
            key = str(row.source_segment_index)
            self.by_source_segment_index[key] = (
                *self.by_source_segment_index.get(key, ()),
                row,
            )

    def resolve(
        self,
        *,
        paragraph_key: Any,
        exact_text: str,
        source_segment_index: Any = None,
    ) -> AnchorResolution:
        """Resolve from recorded coordinates, falling back to unique exact text.

        A recorded physical ordinal or source row index is sufficient proof only
        when that exact row contains the stored excerpt.  If both coordinates
        resolve and disagree, the record is contradictory and fails closed.
        """

        excerpt = str(exact_text or "")
        if not excerpt:
            return AnchorResolution(status="no_excerpt")

        coordinate_matches: list[tuple[str, BodyRow]] = []
        match = LEGACY_LOCATOR.fullmatch(str(paragraph_key or ""))
        if match:
            row = self.by_physical_ordinal.get(int(match.group(1)))
            if row is not None and excerpt in row.text:
                coordinate_matches.append(("physical_ordinal", row))

        legacy_source_index = None
        if paragraph_key not in {None, ""} and match is None:
            legacy_source_index = paragraph_key

        source_index_ambiguous = False
        source_indices = []
        if source_segment_index not in {None, ""}:
            source_indices.append(("source_segment_index", source_segment_index))
        if legacy_source_index is not None and str(legacy_source_index) != str(
            source_segment_index
        ):
            source_indices.append(
                ("legacy_paragraph_source_segment_index", legacy_source_index)
            )
        for coordinate_kind, coordinate_value in source_indices:
            candidates = tuple(
                row
                for row in self.by_source_segment_index.get(
                    str(coordinate_value), ()
                )
                if excerpt in row.text
            )
            source_index_ambiguous = source_index_ambiguous or len(candidates) > 1
            if len(candidates) == 1:
                coordinate_matches.append((coordinate_kind, candidates[0]))

        coordinate_rows = {row.locator: row for _, row in coordinate_matches}
        if len(coordinate_rows) > 1:
            return AnchorResolution(status="coordinate_conflict")
        if coordinate_rows:
            row = next(iter(coordinate_rows.values()))
            proof = "+".join(sorted(kind for kind, _ in coordinate_matches))
            return AnchorResolution(
                status="resolved",
                locator=row.locator,
                source_segment_index=row.source_segment_index,
                paragraph_text=row.text,
                proof=proof,
            )

        if source_index_ambiguous:
            return AnchorResolution(status="ambiguous_source_segment_index")

        exact_rows = [row for row in self.rows if excerpt in row.text]
        if not exact_rows:
            return AnchorResolution(status="absent")
        if len(exact_rows) > 1:
            return AnchorResolution(status="ambiguous_exact_text")
        row = exact_rows[0]
        return AnchorResolution(
            status="resolved",
            locator=row.locator,
            source_segment_index=row.source_segment_index,
            paragraph_text=row.text,
            proof="unique_exact_text",
        )


def migrate_source_document(
    source: Mapping[str, Any], *, raw_source: bytes, projection: SourceProjection
) -> dict[str, Any]:
    """Return the same source identity expressed in the body-coordinate contract."""

    row = dict(source)
    row.update(
        {
            "source_sha256": projection.body_sha256,
            "source_body_sha256": projection.body_sha256,
            "source_file_sha256": hashlib.sha256(raw_source).hexdigest(),
            "source_text_sha256": projection.spoken_text_sha256,
            "source_visual_sha256": projection.visual_content_sha256,
            "anchor_binding_sha256": projection.body_sha256,
            "editorial_structure_sha256": projection.editorial_structure_sha256,
            "editorial_topology_sha256": projection.editorial_topology_sha256,
            "locator_space": LOCATOR_SPACE,
        }
    )
    return row


def migrate_source_fragment(
    fragment: Mapping[str, Any], index: BodyLocatorIndex, *, source_sha256: str
) -> tuple[dict[str, Any] | None, AnchorResolution]:
    if fragment.get("source_modality") == "visual" or "/V" in str(
        fragment.get("paragraph_key") or ""
    ):
        return None, AnchorResolution(
            status="visual_requires_attested_locator_migration"
        )
    resolution = index.resolve(
        paragraph_key=fragment.get("paragraph_key"),
        source_segment_index=fragment.get("source_segment_index"),
        exact_text=str(fragment.get("verbatim_excerpt") or ""),
    )
    if resolution.status != "resolved":
        return None, resolution
    excerpt = str(fragment.get("verbatim_excerpt") or "")
    row = dict(fragment)
    row.update(
        {
            "paragraph_key": resolution.locator,
            "source_segment_index": resolution.source_segment_index,
            "source_sha256": source_sha256,
            "paragraph_text_sha256": _sha256_text(resolution.paragraph_text or ""),
            "verbatim_excerpt_sha256": _sha256_text(excerpt),
            "anchor_state": "source_version_bound",
        }
    )
    return row, resolution


def migrate_claim_occurrence_anchors(
    claim: Mapping[str, Any],
    index: BodyLocatorIndex,
    *,
    source_id: str,
    transcript_id: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Move only locator provenance inside matching Claim occurrences."""

    row = json.loads(_canonical_json(claim))
    findings: list[dict[str, Any]] = []
    changed = False
    for occurrence_index, occurrence in enumerate(row.get("occurrences") or []):
        if not isinstance(occurrence, dict) or not (
            str(occurrence.get("source_id") or "") == source_id
            or str(occurrence.get("transcript_id") or "") == transcript_id
        ):
            continue
        for anchor_index, anchor in enumerate(occurrence.get("anchors") or []):
            if not isinstance(anchor, dict):
                findings.append(
                    {
                        "path": f"occurrences[{occurrence_index}].anchors[{anchor_index}]",
                        "status": "invalid_anchor",
                    }
                )
                continue
            highlight = anchor.get("proposed_highlight") or {}
            exact_text = str(highlight.get("text") or "") if isinstance(highlight, dict) else ""
            resolution = index.resolve(
                paragraph_key=anchor.get("paragraph_key"), exact_text=exact_text
            )
            path = f"occurrences[{occurrence_index}].anchors[{anchor_index}].paragraph_key"
            if resolution.status != "resolved":
                findings.append({"path": path, "status": resolution.status})
                continue
            if str(anchor.get("paragraph_key") or "") != resolution.locator:
                anchor["paragraph_key"] = resolution.locator
                changed = True
                findings.append(
                    {"path": path, "status": "changed", "proof": resolution.proof}
                )
    if any(item["status"] not in {"changed"} for item in findings):
        return None, findings
    return (row if changed else None), findings


def remap_claim_occurrence_source(
    claim: Mapping[str, Any], *, old_source_id: str, canonical_source_id: str
) -> dict[str, Any] | None:
    """Replace one retired SourceDocument alias without changing Claim meaning."""

    row = json.loads(_canonical_json(claim))
    changed = False
    for occurrence in row.get("occurrences") or []:
        if not isinstance(occurrence, dict):
            continue
        if str(occurrence.get("source_id") or "") == old_source_id:
            occurrence["source_id"] = canonical_source_id
            changed = True
    return row if changed else None


def migrate_route_attestation(
    attestation: Mapping[str, Any], *, source_sha256: str
) -> dict[str, Any] | None:
    if str(attestation.get("source_revision_sha256") or "") == source_sha256:
        return None
    row = dict(attestation)
    row["source_revision_sha256"] = source_sha256
    return row


def claim_without_coordinate_provenance(claim: Mapping[str, Any]) -> dict[str, Any]:
    """Remove only fields this migration is allowed to change from a Claim."""

    row = json.loads(_canonical_json(claim))
    for occurrence in row.get("occurrences") or []:
        if not isinstance(occurrence, dict):
            continue
        occurrence.pop("source_id", None)
        for anchor in occurrence.get("anchors") or []:
            if isinstance(anchor, dict):
                anchor.pop("paragraph_key", None)
    return row


def assert_claim_semantics_unchanged(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> None:
    if claim_without_coordinate_provenance(before) != claim_without_coordinate_provenance(after):
        raise ValueError("Claim changed outside coordinate provenance")


def build_projection(script: Any) -> tuple[BodyLocatorIndex, SourceProjection]:
    return BodyLocatorIndex(script), project_script(script)


def changed_paths(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    """Return stable leaf paths for an audit report."""

    paths: list[str] = []
    missing = object()

    def visit(left: Any, right: Any, path: str) -> None:
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right)):
                visit(
                    left.get(key, missing),
                    right.get(key, missing),
                    f"{path}.{key}" if path else key,
                )
        elif isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
            for position, (lvalue, rvalue) in enumerate(zip(left, right)):
                visit(lvalue, rvalue, f"{path}[{position}]")
        elif left != right:
            paths.append(path)

    visit(before, after, "")
    return paths


def unresolved_statuses(findings: Iterable[Mapping[str, Any]]) -> set[str]:
    return {
        str(item.get("status") or "")
        for item in findings
        if item.get("status") not in {"changed", "unchanged", "resolved"}
    }


SOURCE_CONTRACT_CLEANUP_KIND = "wkp368_source_contract_cleanup"
_ALLOWED_COLLECTIONS = frozenset(
    {
        "source_documents",
        "source_fragments",
        "claims",
        "argument_route_attestations",
    }
)
_ALLOWED_SOURCE_FIELDS = frozenset(
    {
        "source_sha256",
        "source_body_sha256",
        "source_file_sha256",
        "source_text_sha256",
        "source_visual_sha256",
        "anchor_binding_sha256",
        "editorial_structure_sha256",
        "editorial_topology_sha256",
        "locator_space",
    }
)
_ALLOWED_FRAGMENT_FIELDS = frozenset(
    {
        "source_id",
        "paragraph_key",
        "source_segment_index",
        "source_sha256",
        "paragraph_text_sha256",
        "verbatim_excerpt_sha256",
        "anchor_state",
    }
)


def _assert_allowed_record_change(
    collection: str, before: Mapping[str, Any], after: Mapping[str, Any]
) -> None:
    paths = changed_paths(before, after)
    if collection == "claims":
        assert_claim_semantics_unchanged(before, after)
        allowed = all(
            re.fullmatch(
                r"occurrences\[\d+\](?:\.source_id|\.anchors\[\d+\]\.paragraph_key)",
                path,
            )
            for path in paths
        )
    elif collection == "source_documents":
        allowed = all(path in _ALLOWED_SOURCE_FIELDS for path in paths)
    elif collection == "source_fragments":
        allowed = all(path in _ALLOWED_FRAGMENT_FIELDS for path in paths)
    elif collection == "argument_route_attestations":
        allowed = all(path in {"source_id", "source_revision_sha256"} for path in paths)
    else:
        allowed = False
    if not paths or not allowed:
        raise ValueError(
            f"{collection} change is outside the source-coordinate contract: {paths}"
        )


def build_source_contract_cleanup_plan(
    *,
    package_id: str,
    current: Mapping[tuple[str, str], Mapping[str, Any]],
    replacements: Mapping[tuple[str, str], Mapping[str, Any]],
    human_settled_claim_ids: Iterable[str] = (),
    source_kind: str = SOURCE_CONTRACT_CLEANUP_KIND,
) -> ChangeSetPlan:
    """Build a CAS-bound plan without impersonating a reviewed Claim package.

    Generic package ingest correctly requires the original sealed editorial
    review whenever an AI-reviewed Claim is supplied.  Coordinate migration is
    narrower: it may edit only source provenance paths, creates no review
    event, and must prove every other Claim byte unchanged.
    """

    protected_claims = set(human_settled_claim_ids)
    operations: list[ChangeOperation] = []
    unchanged = 0
    for (collection, object_id), replacement in sorted(replacements.items()):
        if collection not in _ALLOWED_COLLECTIONS:
            raise ValueError(f"unsupported cleanup collection: {collection}")
        state = current.get((collection, object_id))
        if state is None:
            raise ValueError(f"cleanup cannot create missing record: {collection}/{object_id}")
        before = dict(state.get("payload") or {})
        after = dict(replacement)
        expected_id = {
            "source_documents": "source_id",
            "source_fragments": "fragment_id",
            "claims": "claim_id",
            "argument_route_attestations": "argument_route_attestation_id",
        }[collection]
        if str(after.get(expected_id) or "") != object_id:
            raise ValueError(f"cleanup changed record identity: {collection}/{object_id}")
        before_sha = str(state.get("content_sha256") or "")
        if before_sha != record_content_sha(before):
            raise ValueError(f"current SHA is inconsistent: {collection}/{object_id}")
        after_sha = record_content_sha(after)
        if before_sha == after_sha:
            unchanged += 1
            continue
        if collection == "claims" and object_id in protected_claims:
            raise ValueError(f"cleanup cannot alter human-settled Claim: {object_id}")
        _assert_allowed_record_change(collection, before, after)
        revision = int(state.get("revision") or 0)
        operations.append(
            ChangeOperation(
                operation="update",
                collection=collection,
                object_id=object_id,
                before_sha256=before_sha,
                after_sha256=after_sha,
                before_revision=revision,
                after_revision=revision + 1,
                payload=after,
            )
        )

    authority = {
        "schema_version": "wang_source_contract_cleanup_authority_v1",
        "package_id": package_id,
        "operations": operation_fingerprint_rows(operations),
    }
    source_sha = sha256_json(authority)
    fingerprint = sha256_json(
        {
            "planner_schema": "wang_source_contract_cleanup_plan_v1",
            "source_kind": source_kind,
            "source_sha256": source_sha,
            "package_id": package_id,
            "operations": operation_fingerprint_rows(operations),
            "review_events": [],
        }
    )
    return ChangeSetPlan(
        change_set_id=f"KCS-{fingerprint[:20]}",
        fingerprint_sha256=fingerprint,
        package_id=package_id,
        source_kind=source_kind,
        source_sha256=source_sha,
        operations=tuple(operations),
        unchanged=unchanged,
        ignored_keys=(),
        review_events=(),
    )
