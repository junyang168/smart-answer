"""Deterministic scope compiler for the Matthew 16 CanonicalViewpoint pilot.

The compiler deliberately separates the authoritative twelve-source map from
the latest successful detailed-extraction cohort.  A mapped source that has no
member in that cohort is reported as a gap; older Claims are never silently
substituted.  Published articles are downstream fixtures, not source authority.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.pipeline.passage_knowledge_slice import Passage, reference_overlaps

from .knowledge_models import ClaimRecord, KnowledgeSourceDocument
from .viewpoint_foundation import semantic_record_sha, sha256_json


class StrictPilotModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PilotSource(StrictPilotModel):
    catalog_source_id: str
    title: str
    source_type: str
    transcript_id: str | None = None
    canonical_source_ids: list[str] = Field(default_factory=list)
    processing_phase: Literal["passage_exegesis", "thematic_followup"]
    status: Literal[
        "latest_detailed_available", "latest_detailed_missing", "thematic_deferred"
    ]


class PilotClaim(StrictPilotModel):
    claim_id: str
    pinned_claim_revision: int
    claim_revision_sha256: str
    source_id: str
    statement: str
    claim_type: str
    attribution: str | None = None
    review_status: str
    scripture_refs: list[Any] = Field(default_factory=list)
    lane: Literal["core", "source_context_candidate"]


class ArticleAcceptanceFixture(StrictPilotModel):
    draft_id: str
    manuscript_sha256: str
    program_audit_sha256: str
    used_claim_ids: list[str]
    exact_current_claim_ids: list[str]
    requires_semantic_alignment_claim_ids: list[str]


class Matthew16PilotScope(StrictPilotModel):
    schema_version: Literal["wang_matthew16_viewpoint_pilot_scope_v1"] = (
        "wang_matthew16_viewpoint_pilot_scope_v1"
    )
    chapter: Literal[16] = 16
    passage_units: list[str]
    source_catalog_sha256: str
    source_map_sha256: str
    source_selection_sha256: str
    parent_claim_manifest_sha256: str
    sources: list[PilotSource]
    claims: list[PilotClaim]
    article_acceptance_fixtures: list[ArticleAcceptanceFixture]
    statistics: dict[str, int]
    model_calls_executed: Literal[0] = 0
    master_data_mutations: Literal[0] = 0
    apply_allowed: Literal[False] = False
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_artifact(self) -> "Matthew16PilotScope":
        if [item.catalog_source_id for item in self.sources] != sorted(
            item.catalog_source_id for item in self.sources
        ):
            raise ValueError("pilot sources must be sorted")
        if [item.claim_id for item in self.claims] != sorted(
            item.claim_id for item in self.claims
        ):
            raise ValueError("pilot Claims must be sorted")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("Matthew 16 pilot artifact SHA mismatch")
        return self


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verified_embedded_sha(payload: Mapping[str, Any], field: str) -> str:
    unsigned = dict(payload)
    stated = str(unsigned.pop(field, ""))
    if not stated or stated != sha256_json(unsigned):
        raise ValueError(f"{field} mismatch")
    return stated


def _catalog_source_rows(catalog: Mapping[str, Any]) -> list[dict[str, Any]]:
    chapter = next(
        (row for row in catalog.get("chapters", []) if int(row.get("chapter", 0)) == 16),
        None,
    )
    if chapter is None:
        raise ValueError("source catalog has no Matthew 16 chapter")
    rows = [dict(row) for row in chapter.get("sources") or []]
    if len(rows) != 12:
        raise ValueError("Matthew 16 source map must contain exactly 12 sources")
    return rows


def _article_fixture(article_dir: Path, current_claim_ids: set[str]) -> ArticleAcceptanceFixture:
    manuscript = article_dir / "manuscript.md"
    audit_path = article_dir / "program-audit.json"
    if not manuscript.is_file() or not audit_path.is_file():
        raise ValueError(f"incomplete article fixture: {article_dir}")
    import json

    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    used = sorted(
        {
            str(claim_id)
            for row in audit.get("paragraph_provenance") or []
            for claim_id in row.get("claim_ids") or []
        }
    )
    exact = sorted(set(used) & current_claim_ids)
    return ArticleAcceptanceFixture(
        draft_id=str(audit.get("draft_id") or article_dir.name),
        manuscript_sha256=file_sha256(manuscript),
        program_audit_sha256=file_sha256(audit_path),
        used_claim_ids=used,
        exact_current_claim_ids=exact,
        requires_semantic_alignment_claim_ids=sorted(set(used) - set(exact)),
    )


def build_matthew16_pilot_scope(
    *,
    source_catalog: Mapping[str, Any],
    source_catalog_sha256: str,
    source_map_sha256: str,
    source_selection: Mapping[str, Any],
    claim_manifest: Mapping[str, Any],
    source_documents: Sequence[Mapping[str, Any]],
    claims: Sequence[Mapping[str, Any]],
    article_dirs: Sequence[Path] = (),
    thematic_source_ids: Sequence[str] = (),
) -> Matthew16PilotScope:
    """Compile the exact zero-call Claim denominator for the chapter pilot."""

    selection_sha = _verified_embedded_sha(source_selection, "selection_sha256")
    manifest_sha = _verified_embedded_sha(claim_manifest, "manifest_sha256")
    selected_source_ids = {
        str(row["source_id"]) for row in source_selection.get("members") or []
        if row.get("latest_extraction_status") == "applied"
    }
    source_models = [KnowledgeSourceDocument.model_validate(row) for row in source_documents]
    selected_documents = {
        row.source_id: row for row in source_models if row.source_id in selected_source_ids
    }
    pilot_sources: list[PilotSource] = []
    eligible_source_ids: set[str] = set()
    thematic_ids = set(thematic_source_ids)
    for row in _catalog_source_rows(source_catalog):
        catalog_id = str(row["source_id"])
        transcript_id = catalog_id.removeprefix("sermon:") if catalog_id.startswith("sermon:") else None
        matches = sorted(
            source.source_id
            for source in selected_documents.values()
            if source.source_id == catalog_id
            or (transcript_id is not None and source.transcript_id == transcript_id)
        )
        is_thematic = catalog_id in thematic_ids
        if not is_thematic:
            eligible_source_ids.update(matches)
        pilot_sources.append(
            PilotSource(
                catalog_source_id=catalog_id,
                title=str(row.get("title") or catalog_id),
                source_type=str(row.get("source_type") or "unknown"),
                transcript_id=transcript_id,
                canonical_source_ids=matches,
                processing_phase="thematic_followup" if is_thematic else "passage_exegesis",
                status=(
                    "thematic_deferred"
                    if is_thematic
                    else "latest_detailed_available" if matches else "latest_detailed_missing"
                ),
            )
        )
    pilot_sources.sort(key=lambda item: item.catalog_source_id)

    manifest_rows = {
        str(row["claim_id"]): dict(row)
        for row in claim_manifest.get("claims") or []
        if str(row.get("source_id") or "") in eligible_source_ids
    }
    claim_index = {
        item.claim_id: item for item in (ClaimRecord.model_validate(row) for row in claims)
        if item.claim_id in manifest_rows
    }
    if set(claim_index) != set(manifest_rows):
        raise ValueError("database is missing a pinned Matthew 16 pilot Claim")
    passage = Passage("Matt", 16, 1, 28)
    pilot_claims: list[PilotClaim] = []
    for claim_id, pinned in manifest_rows.items():
        claim = claim_index[claim_id]
        if (
            claim.revision != int(pinned["pinned_claim_revision"])
            or semantic_record_sha(claim) != pinned["claim_revision_sha256"]
        ):
            raise ValueError(f"{claim_id}: pinned Claim revision mismatch")
        lane = (
            "core"
            if any(reference_overlaps(str(ref), passage) for ref in claim.scripture_refs)
            else "source_context_candidate"
        )
        pilot_claims.append(
            PilotClaim(
                claim_id=claim_id,
                pinned_claim_revision=claim.revision,
                claim_revision_sha256=str(pinned["claim_revision_sha256"]),
                source_id=str(pinned["source_id"]),
                statement=claim.statement,
                claim_type=claim.claim_type,
                attribution=claim.attribution,
                review_status=claim.review_status,
                scripture_refs=claim.scripture_refs,
                lane=lane,
            )
        )
    pilot_claims.sort(key=lambda item: item.claim_id)
    current_claim_ids = {item.claim_id for item in pilot_claims}
    article_fixtures = sorted(
        (_article_fixture(path, current_claim_ids) for path in article_dirs),
        key=lambda item: item.draft_id,
    )
    payload = {
        "schema_version": "wang_matthew16_viewpoint_pilot_scope_v1",
        "chapter": 16,
        "passage_units": ["16:1-12", "16:13-18", "16:19", "16:20-23", "16:24-27", "16:28-17:8"],
        "source_catalog_sha256": source_catalog_sha256,
        "source_map_sha256": source_map_sha256,
        "source_selection_sha256": selection_sha,
        "parent_claim_manifest_sha256": manifest_sha,
        "sources": [item.model_dump(mode="json") for item in pilot_sources],
        "claims": [item.model_dump(mode="json") for item in pilot_claims],
        "article_acceptance_fixtures": [item.model_dump(mode="json") for item in article_fixtures],
        "statistics": {
            "mapped_source_total": len(pilot_sources),
            "passage_exegesis_source_total": sum(item.processing_phase == "passage_exegesis" for item in pilot_sources),
            "thematic_deferred_source_total": sum(item.status == "thematic_deferred" for item in pilot_sources),
            "latest_detailed_source_total": sum(item.status == "latest_detailed_available" for item in pilot_sources),
            "latest_detailed_source_gap_total": sum(item.status == "latest_detailed_missing" for item in pilot_sources),
            "claim_total": len(pilot_claims),
            "core_claim_total": sum(item.lane == "core" for item in pilot_claims),
            "source_context_candidate_total": sum(item.lane == "source_context_candidate" for item in pilot_claims),
            "article_fixture_total": len(article_fixtures),
            "article_used_claim_total": sum(len(item.used_claim_ids) for item in article_fixtures),
            "article_exact_current_claim_total": sum(len(item.exact_current_claim_ids) for item in article_fixtures),
            "article_semantic_alignment_required_total": sum(len(item.requires_semantic_alignment_claim_ids) for item in article_fixtures),
        },
        "model_calls_executed": 0,
        "master_data_mutations": 0,
        "apply_allowed": False,
    }
    return Matthew16PilotScope(**payload, artifact_sha256=sha256_json(payload))
