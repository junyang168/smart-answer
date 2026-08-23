"""One-viewpoint vertical pilot artifact for Matthew 16:18."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .knowledge_models import ViewpointPropositionSignature, ViewpointScope
from .viewpoint_foundation import sha256_json
from .viewpoint_proposition_units import PropositionUnitCandidate
from .viewpoint_resolution import ReviewClaim
from .viewpoint_runtime_projection import (
    ProjectionDependency,
    ViewpointKnowledgeProjection,
)


class StrictCandidateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PilotViewpointMember(StrictCandidateModel):
    proposition_unit: PropositionUnitCandidate
    parent_claim: ReviewClaim


class AdjacentPropositionUnit(StrictCandidateModel):
    proposition_unit_id: str
    parent_claim_id: str
    unit_statement: str
    disposition: Literal["adjacent_non_member"] = "adjacent_non_member"
    reason: Literal["different_truth_condition"] = "different_truth_condition"


class ArticleViewpointAcceptance(StrictCandidateModel):
    draft_id: str
    manuscript_sha256: str
    article_proposition: str
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    article_is_source_authority: Literal[False] = False
    supporting_proposition_unit_ids: list[str] = Field(min_length=1)
    alignment_basis: Literal[
        "exact_article_clause_plus_dual_model_atomic_equivalence"
    ] = "exact_article_clause_plus_dual_model_atomic_equivalence"
    status: Literal["supported"] = "supported"


class ViewpointKnowledgeClassification(StrictCandidateModel):
    """Versioned downstream classification; it is not viewpoint identity."""

    schema_version: Literal["wang_viewpoint_knowledge_classification_v1"] = (
        "wang_viewpoint_knowledge_classification_v1"
    )
    knowledge_role: Literal["passage_interpretation"]
    processing_phase: Literal["passage_exegesis"]
    scripture_scope: list[str] = Field(min_length=1)
    policy_version: Literal["matthew16_pilot_classification_v1"] = (
        "matthew16_pilot_classification_v1"
    )
    basis_fields: list[Literal[
        "proposition_signature.modality",
        "scope.scripture_scope",
    ]]


class Matthew16ViewpointPilotArtifact(StrictCandidateModel):
    schema_version: Literal["wang_matthew16_viewpoint_pilot_v1"] = (
        "wang_matthew16_viewpoint_pilot_v1"
    )
    viewpoint_candidate_id: str
    viewpoint_revision_candidate_id: str
    core_proposition: str
    wording_label: Literal["编辑归一化，不是逐字引文"] = "编辑归一化，不是逐字引文"
    proposition_signature: ViewpointPropositionSignature
    scope: ViewpointScope
    review_status: Literal["dual_model_boundary_agreed_candidate"] = (
        "dual_model_boundary_agreed_candidate"
    )
    consumer_eligibility: Literal["internal_candidate"] = "internal_candidate"
    members: list[PilotViewpointMember] = Field(min_length=2)
    adjacent_non_members: list[AdjacentPropositionUnit]
    article_acceptance: ArticleViewpointAcceptance
    parent_scope_artifact_sha256: str
    atomic_execution_artifact_sha256: str
    atomic_identity_execution_artifact_sha256: str
    boundary_run_artifact_sha256: str
    model_ids: list[str]
    blockers: list[str]
    master_data_mutations: Literal[0] = 0
    apply_allowed: Literal[False] = False
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_artifact(self) -> "Matthew16ViewpointPilotArtifact":
        member_ids = [item.proposition_unit.proposition_unit_id for item in self.members]
        if member_ids != sorted(set(member_ids)):
            raise ValueError("pilot viewpoint members must be sorted and unique")
        adjacent_ids = [item.proposition_unit_id for item in self.adjacent_non_members]
        if adjacent_ids != sorted(set(adjacent_ids)):
            raise ValueError("adjacent units must be sorted and unique")
        if set(member_ids) & set(adjacent_ids):
            raise ValueError("member and adjacent units overlap")
        if self.article_acceptance.supporting_proposition_unit_ids != member_ids:
            raise ValueError("article acceptance must bind the exact identity members")
        if self.model_ids != sorted(set(self.model_ids)):
            raise ValueError("pilot model ids must be canonical")
        if self.blockers != sorted(set(self.blockers)):
            raise ValueError("pilot blockers must be canonical")
        identity = {
            "core_proposition": self.core_proposition,
            "proposition_signature": self.proposition_signature.model_dump(mode="json"),
            "scope": self.scope.model_dump(mode="json"),
            "member_unit_ids": member_ids,
            "boundary_run_artifact_sha256": self.boundary_run_artifact_sha256,
        }
        if self.viewpoint_candidate_id != f"CVP-{sha256_json(identity)[:20]}":
            raise ValueError("unstable pilot viewpoint candidate id")
        revision_identity = {"viewpoint_candidate_id": self.viewpoint_candidate_id, **identity}
        if self.viewpoint_revision_candidate_id != f"CVPR-{sha256_json(revision_identity)[:20]}":
            raise ValueError("unstable pilot revision candidate id")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("pilot viewpoint artifact SHA mismatch")
        return self


def build_matthew16_viewpoint_pilot(
    *,
    core_proposition: str,
    members: list[PilotViewpointMember],
    adjacent_non_members: list[AdjacentPropositionUnit],
    article_acceptance: ArticleViewpointAcceptance,
    parent_scope_artifact_sha256: str,
    atomic_execution_artifact_sha256: str,
    atomic_identity_execution_artifact_sha256: str,
    boundary_run_artifact_sha256: str,
    model_ids: list[str],
) -> Matthew16ViewpointPilotArtifact:
    members.sort(key=lambda item: item.proposition_unit.proposition_unit_id)
    adjacent_non_members.sort(key=lambda item: item.proposition_unit_id)
    signature = ViewpointPropositionSignature(
        subject="太16:18的「磐石」",
        predicate="指向",
        object="彼得本人",
        polarity="denied",
        modality="教授的释经判断",
        temporal_scope=[],
        conditions=[],
        population_scope=[],
    )
    scope = ViewpointScope(
        scripture_scope=["Matt.16.18"],
        audience_scope=[],
        historical_scope=[],
    )
    identity = {
        "core_proposition": core_proposition,
        "proposition_signature": signature.model_dump(mode="json"),
        "scope": scope.model_dump(mode="json"),
        "member_unit_ids": [item.proposition_unit.proposition_unit_id for item in members],
        "boundary_run_artifact_sha256": boundary_run_artifact_sha256,
    }
    viewpoint_id = f"CVP-{sha256_json(identity)[:20]}"
    revision_identity = {"viewpoint_candidate_id": viewpoint_id, **identity}
    payload: dict[str, Any] = {
        "schema_version": "wang_matthew16_viewpoint_pilot_v1",
        "viewpoint_candidate_id": viewpoint_id,
        "viewpoint_revision_candidate_id": f"CVPR-{sha256_json(revision_identity)[:20]}",
        "core_proposition": core_proposition,
        "wording_label": "编辑归一化，不是逐字引文",
        "proposition_signature": signature.model_dump(mode="json"),
        "scope": scope.model_dump(mode="json"),
        "review_status": "dual_model_boundary_agreed_candidate",
        "consumer_eligibility": "internal_candidate",
        "members": [item.model_dump(mode="json") for item in members],
        "adjacent_non_members": [item.model_dump(mode="json") for item in adjacent_non_members],
        "article_acceptance": article_acceptance.model_dump(mode="json"),
        "parent_scope_artifact_sha256": parent_scope_artifact_sha256,
        "atomic_execution_artifact_sha256": atomic_execution_artifact_sha256,
        "atomic_identity_execution_artifact_sha256": atomic_identity_execution_artifact_sha256,
        "boundary_run_artifact_sha256": boundary_run_artifact_sha256,
        "model_ids": sorted(set(model_ids)),
        "blockers": ["not_master_applied", "pilot_scope_only"],
        "master_data_mutations": 0,
        "apply_allowed": False,
    }
    return Matthew16ViewpointPilotArtifact(**payload, artifact_sha256=sha256_json(payload))


def classify_pilot_viewpoint(
    pilot: Matthew16ViewpointPilotArtifact,
) -> ViewpointKnowledgeClassification:
    """Classify the pilot deterministically and fail closed on another shape."""

    if (
        pilot.proposition_signature.modality != "教授的释经判断"
        or not pilot.scope.scripture_scope
    ):
        raise ValueError(
            "Matthew 16 pilot cannot be classified as passage interpretation"
        )
    return ViewpointKnowledgeClassification(
        knowledge_role="passage_interpretation",
        processing_phase="passage_exegesis",
        scripture_scope=pilot.scope.scripture_scope,
        basis_fields=[
            "proposition_signature.modality",
            "scope.scripture_scope",
        ],
    )


def build_pilot_composition_projection(
    pilot: Matthew16ViewpointPilotArtifact,
) -> ViewpointKnowledgeProjection:
    """Project one internal candidate for authoring shadow validation.

    This intentionally cannot grant composition eligibility.  It proves that
    the Matthew runner can read a bounded, standard downstream projection
    without scanning either the registry or staging directories.
    """

    classification = classify_pilot_viewpoint(pilot)
    claims = {
        item.parent_claim.claim_id: item.parent_claim
        for item in pilot.members
    }
    evidence = {
        (row.evidence_step_id, row.source_fragment_id): row
        for item in pilot.members
        for row in item.proposition_unit.evidence
    }
    sources: dict[str, dict[str, Any]] = {}
    fragments: dict[str, dict[str, Any]] = {}
    citations: dict[str, dict[str, Any]] = {}
    for row in evidence.values():
        sources[row.source_id] = {
            "source_id": row.source_id,
            "source_sha256": row.source_sha256,
        }
        fragment = {
                "fragment_id": row.source_fragment_id,
                "source_id": row.source_id,
                "verbatim_excerpt": row.verbatim_excerpt,
                "paragraph_key": row.paragraph_key,
                "media_time": row.media_time,
                "anchor_state": row.anchor_state,
                "source_sha256": row.source_sha256,
            }
        prior_fragment = fragments.get(row.source_fragment_id)
        if prior_fragment is not None and prior_fragment != fragment:
            raise ValueError("one source fragment resolved to conflicting projection rows")
        fragments[row.source_fragment_id] = fragment
        citations[row.citation_id] = {
            "citation_id": row.citation_id,
            "revision": row.citation_revision,
            "status": row.citation_status,
            "source_id": row.source_id,
            "source_sha256": row.source_sha256,
        }
    dependencies = [
        ProjectionDependency(
            collection="matthew16_viewpoint_pilots",
            record_id=pilot.viewpoint_candidate_id,
            revision=1,
            sha256=pilot.artifact_sha256,
        ),
        *[
            ProjectionDependency(
                collection="claims",
                record_id=claim.claim_id,
                revision=claim.pinned_claim_revision,
                sha256=claim.claim_revision_sha256,
            )
            for claim in claims.values()
        ],
        *[
            ProjectionDependency(
                collection="proposition_units",
                record_id=item.proposition_unit.proposition_unit_id,
                revision=1,
                sha256=sha256_json(item.proposition_unit.model_dump(mode="json")),
            )
            for item in pilot.members
        ],
    ]
    dependencies.sort(key=lambda item: (item.collection, item.record_id))
    manifest = [item.model_dump(mode="json") for item in dependencies]
    payload = {
        "schema_version": "wang_viewpoint_knowledge_projection_v1",
        "consumer_kind": "composition_plan",
        "scope_viewpoint_ids": [pilot.viewpoint_candidate_id],
        "coverage_snapshot_id": f"PILOT-SCOPE-{pilot.parent_scope_artifact_sha256[:20]}",
        "resolution_ledger_id": None,
        "quality_report_id": None,
        "eligibility": "internal_candidate",
        "blocker_codes": pilot.blockers,
        "viewpoints": [
            {
                "candidate_id": pilot.viewpoint_candidate_id,
                "revision_candidate_id": pilot.viewpoint_revision_candidate_id,
                "core_proposition": pilot.core_proposition,
                "proposition_signature": pilot.proposition_signature.model_dump(mode="json"),
                "scope": pilot.scope.model_dump(mode="json"),
                "knowledge_classification": classification.model_dump(mode="json"),
                "review_status": pilot.review_status,
                "member_proposition_unit_ids": [
                    item.proposition_unit.proposition_unit_id for item in pilot.members
                ],
                "article_acceptance": pilot.article_acceptance.model_dump(mode="json"),
            }
        ],
        "argument_routes": [],
        "expanded_claims": [
            claims[key].model_dump(mode="json") for key in sorted(claims)
        ],
        "expanded_evidence": [
            evidence[key].model_dump(mode="json") for key in sorted(evidence)
        ],
        "expanded_fragments": [fragments[key] for key in sorted(fragments)],
        "expanded_sources": [sources[key] for key in sorted(sources)],
        "expanded_citations": [citations[key] for key in sorted(citations)],
        "relations": [],
        "dependency_manifest": manifest,
        "dependency_manifest_sha256": sha256_json(manifest),
    }
    return ViewpointKnowledgeProjection(
        **payload, projection_sha256=sha256_json(payload)
    )
