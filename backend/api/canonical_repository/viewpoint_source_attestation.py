"""Consumer-specific source eligibility attestations for identity review.

An attestation does not approve or mutate an upstream Claim.  It proves that a
pinned candidate Claim was generated from source-bound evidence, independently
reviewed, and (when necessary) corrected in the reviewed candidate package.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .knowledge_models import ClaimRecord, EvidenceStepRecord, SourceFragmentRecord, evidence_fragment_ids
from .viewpoint_foundation import semantic_record_sha, sha256_json


class StrictAttestationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IdentitySourceEligibilityAttestation(StrictAttestationModel):
    claim_id: str
    pinned_claim_revision: int = Field(ge=1)
    claim_revision_sha256: str
    source_id: str
    source_sha256: str
    extraction_model_id: str
    extraction_backend: str
    extraction_fingerprint_sha256: str
    independent_review_model_id: str
    independent_review_provider: str
    independent_review_fingerprint_sha256: str
    independent_review_decision: Literal["pass", "changes_suggested_applied"]
    review_input_artifact_sha256: str
    independent_review_artifact_sha256: str
    reviewed_candidate_artifact_sha256: str
    evidence_dependency_sha256: str
    attestation_sha256: str
    eligibility_scope: Literal["viewpoint_identity_review"] = "viewpoint_identity_review"
    approval_status: Literal["not_human_approved"] = "not_human_approved"
    master_data_mutation: Literal[False] = False

    @model_validator(mode="after")
    def validate_attestation(self) -> "IdentitySourceEligibilityAttestation":
        payload = self.model_dump(mode="json", exclude={"attestation_sha256"})
        if self.attestation_sha256 != sha256_json(payload):
            raise ValueError("source eligibility attestation SHA mismatch")
        return self


class IdentitySourceEligibilityException(StrictAttestationModel):
    claim_id: str
    code: Literal[
        "missing_reviewed_candidate",
        "missing_independent_review",
        "stale_dependency",
        "human_review_required",
        "unapplied_review_change",
        "invalid_source_evidence",
        "review_binding_mismatch",
    ]
    detail: str = Field(min_length=1)


class IdentitySourceEligibilityArtifact(StrictAttestationModel):
    schema_version: Literal["wang_viewpoint_source_eligibility_attestation_v1"] = (
        "wang_viewpoint_source_eligibility_attestation_v1"
    )
    claim_manifest_sha256: str
    attestations: list[IdentitySourceEligibilityAttestation]
    exceptions: list[IdentitySourceEligibilityException]
    statistics: dict[str, int]
    approval_status: Literal["not_human_approved"] = "not_human_approved"
    master_data_mutations: Literal[0] = 0
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_artifact(self) -> "IdentitySourceEligibilityArtifact":
        attested = [item.claim_id for item in self.attestations]
        excepted = [item.claim_id for item in self.exceptions]
        if attested != sorted(set(attested)) or excepted != sorted(set(excepted)):
            raise ValueError("source eligibility rows must be canonical and unique")
        if set(attested) & set(excepted):
            raise ValueError("a Claim cannot be both attested and excepted")
        codes = Counter(item.code for item in self.exceptions)
        expected = {
            "input_claim_count": len(attested) + len(excepted),
            "attested_claim_count": len(attested),
            "exception_claim_count": len(excepted),
            **{f"{code}_count": count for code, count in sorted(codes.items())},
        }
        if self.statistics != expected:
            raise ValueError("source eligibility statistics mismatch")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("source eligibility artifact SHA mismatch")
        return self


def build_source_eligibility_artifact(
    *,
    claim_manifest: Mapping[str, Any],
    claims: Sequence[Mapping[str, Any]],
    evidence_steps: Sequence[Mapping[str, Any]],
    source_fragments: Sequence[Mapping[str, Any]],
    reviewed_packages_by_claim_id: Mapping[str, Mapping[str, Any]],
    reviews_by_claim_id: Mapping[str, Mapping[str, Any]],
) -> IdentitySourceEligibilityArtifact:
    """Verify reviewed-candidate and independent-review provenance claim by claim."""

    unsigned_manifest = dict(claim_manifest)
    manifest_sha = str(unsigned_manifest.pop("manifest_sha256", ""))
    if not manifest_sha or manifest_sha != sha256_json(unsigned_manifest):
        raise ValueError("source eligibility requires a valid Claim manifest")
    claim_index = {row["claim_id"]: ClaimRecord.model_validate(row) for row in claims}
    evidence_index = {
        row["evidence_step_id"]: EvidenceStepRecord.model_validate(row)
        for row in evidence_steps
    }
    fragment_index = {
        row["fragment_id"]: SourceFragmentRecord.model_validate(row)
        for row in source_fragments
    }
    attestations: list[IdentitySourceEligibilityAttestation] = []
    exceptions: list[IdentitySourceEligibilityException] = []

    def reject(claim_id: str, code: str, detail: str) -> None:
        exceptions.append(
            IdentitySourceEligibilityException(
                claim_id=claim_id, code=code, detail=detail
            )
        )

    for pinned in sorted(claim_manifest.get("claims") or [], key=lambda row: row["claim_id"]):
        claim_id = str(pinned["claim_id"])
        package_binding = reviewed_packages_by_claim_id.get(claim_id)
        review_binding = reviews_by_claim_id.get(claim_id)
        if package_binding is None:
            reject(claim_id, "missing_reviewed_candidate", "No reviewed-candidate package covers this Claim.")
            continue
        if review_binding is None:
            reject(claim_id, "missing_independent_review", "No independent Claim review covers this Claim.")
            continue
        claim = claim_index.get(claim_id)
        if (
            claim is None
            or claim.revision != int(pinned["pinned_claim_revision"])
            or semantic_record_sha(claim) != pinned["claim_revision_sha256"]
        ):
            reject(claim_id, "stale_dependency", "Current Claim differs from the pinned manifest revision/SHA.")
            continue
        package = package_binding["payload"]
        package_claims = {row["claim_id"]: row for row in package.get("claims") or []}
        try:
            package_claim = ClaimRecord.model_validate(package_claims[claim_id])
        except Exception:
            reject(claim_id, "review_binding_mismatch", "Reviewed candidate does not contain a valid matching Claim.")
            continue
        if semantic_record_sha(package_claim) != semantic_record_sha(claim):
            reject(claim_id, "review_binding_mismatch", "Reviewed candidate Claim differs from the authoring Claim.")
            continue
        review = review_binding["payload"]
        review_row = review_binding["claim_review"]
        reviewer = review.get("reviewer") or {}
        source = review.get("source") or {}
        if (
            reviewer.get("fingerprint_sha256") != review_row.get("reviewer_fingerprint")
            or source.get("package_sha256") != review_binding.get("review_input_artifact_sha256")
        ):
            reject(claim_id, "review_binding_mismatch", "Independent review fingerprint or input package SHA does not bind.")
            continue
        decision = str(review_row.get("decision") or "")
        applied = set((package.get("consensus_application") or {}).get("applied_claim_ids") or [])
        if decision == "human_review_required":
            reject(claim_id, "human_review_required", "Independent source review explicitly requires human review.")
            continue
        if decision == "changes_suggested" and claim_id not in applied:
            reject(claim_id, "unapplied_review_change", "Independent review requested a change not bound as applied.")
            continue
        if decision not in {"pass", "changes_suggested"}:
            reject(claim_id, "review_binding_mismatch", f"Unsupported independent review decision: {decision}")
            continue
        dependencies = []
        source_ids: set[str] = set()
        valid = True
        for evidence_id in claim.evidence_step_ids:
            evidence = evidence_index.get(evidence_id)
            if evidence is None or evidence.support_eligibility not in {
                "eligible", "eligible_candidate", "eligible_with_label"
            }:
                valid = False
                break
            fragments = []
            for fragment_id in evidence_fragment_ids(evidence):
                fragment = fragment_index.get(fragment_id)
                if (
                    fragment is None
                    or fragment.anchor_state not in {
                        "source_version_bound", "canonical_citation_bound", "verified", "valid"
                    }
                    or not fragment.source_sha256
                    or not fragment.verbatim_excerpt
                ):
                    valid = False
                    break
                source_ids.add(fragment.source_id)
                fragments.append({
                    "fragment_id": fragment.fragment_id,
                    "semantic_sha256": semantic_record_sha(fragment),
                    "source_sha256": fragment.source_sha256,
                })
            if not valid or not fragments:
                valid = False
                break
            dependencies.append({
                "evidence_step_id": evidence.evidence_step_id,
                "semantic_sha256": semantic_record_sha(evidence),
                "fragments": sorted(fragments, key=lambda row: row["fragment_id"]),
            })
        if not valid or len(source_ids) != 1:
            reject(claim_id, "invalid_source_evidence", "Claim evidence is missing, unbound, ineligible, or not source-local.")
            continue
        source_id = next(iter(source_ids))
        source_sha_values = {
            row["source_sha256"]
            for item in dependencies for row in item["fragments"]
        }
        if len(source_sha_values) != 1:
            reject(claim_id, "invalid_source_evidence", "Claim fragments do not bind one source revision.")
            continue
        extraction = package.get("extraction") or {}
        row_payload = {
            "claim_id": claim_id,
            "pinned_claim_revision": claim.revision,
            "claim_revision_sha256": semantic_record_sha(claim),
            "source_id": source_id,
            "source_sha256": next(iter(source_sha_values)),
            "extraction_model_id": str(extraction.get("model_id") or ""),
            "extraction_backend": str(extraction.get("backend") or ""),
            "extraction_fingerprint_sha256": str(extraction.get("fingerprint_sha256") or ""),
            "independent_review_model_id": str(reviewer.get("review_model_id") or ""),
            "independent_review_provider": str(reviewer.get("provider") or ""),
            "independent_review_fingerprint_sha256": str(reviewer.get("fingerprint_sha256") or ""),
            "independent_review_decision": "pass" if decision == "pass" else "changes_suggested_applied",
            "review_input_artifact_sha256": str(review_binding["review_input_artifact_sha256"]),
            "independent_review_artifact_sha256": str(review_binding["artifact_sha256"]),
            "reviewed_candidate_artifact_sha256": str(package_binding["artifact_sha256"]),
            "evidence_dependency_sha256": sha256_json(dependencies),
            "eligibility_scope": "viewpoint_identity_review",
            "approval_status": "not_human_approved",
            "master_data_mutation": False,
        }
        attestations.append(
            IdentitySourceEligibilityAttestation(
                **row_payload, attestation_sha256=sha256_json(row_payload)
            )
        )
    attestations.sort(key=lambda row: row.claim_id)
    exceptions.sort(key=lambda row: row.claim_id)
    codes = Counter(row.code for row in exceptions)
    statistics = {
        "input_claim_count": len(attestations) + len(exceptions),
        "attested_claim_count": len(attestations),
        "exception_claim_count": len(exceptions),
        **{f"{code}_count": count for code, count in sorted(codes.items())},
    }
    payload = {
        "schema_version": "wang_viewpoint_source_eligibility_attestation_v1",
        "claim_manifest_sha256": manifest_sha,
        "attestations": [row.model_dump(mode="json") for row in attestations],
        "exceptions": [row.model_dump(mode="json") for row in exceptions],
        "statistics": statistics,
        "approval_status": "not_human_approved",
        "master_data_mutations": 0,
    }
    return IdentitySourceEligibilityArtifact(
        **payload, artifact_sha256=sha256_json(payload)
    )
