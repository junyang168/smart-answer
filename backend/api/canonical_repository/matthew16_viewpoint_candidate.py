"""One-viewpoint vertical pilot artifact for Matthew 16:18."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .knowledge_models import ViewpointPropositionSignature, ViewpointScope
from .viewpoint_foundation import sha256_json
from .viewpoint_proposition_units import PropositionUnitCandidate
from .viewpoint_resolution import ReviewClaim


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
