"""Versioned consumer eligibility from verified historical review bridges.

No modern consensus seal or human approval is fabricated for a legacy review.
The original review and actual applied repair history remain explicit proof.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.api.canonical_repository.knowledge_models import EvidenceStepRecord, evidence_fragment_ids
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import CvpProductionBlocked, validate_content_addressed
from backend.pipeline.viewpoint_historical_review_bridge import verify_historical_review_bridge


class HistoricalSourceEligibilityRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_id: str
    pinned_claim_revision: int = Field(ge=1)
    claim_revision_sha256: str
    source_id: str
    source_sha256: str
    historical_review_bridge_sha256: str
    attestation_sha256: str

    @model_validator(mode="after")
    def check_sha(self):
        if self.attestation_sha256 != sha256_json(self.model_dump(mode="json", exclude={"attestation_sha256"})):
            raise ValueError("historical source eligibility row SHA mismatch")
        return self


class HistoricalSourceEligibilityArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["wang_historical_viewpoint_source_eligibility_v1"]
    claim_manifest_sha256: str
    bridge_manifest_binding: dict[str, str]
    attestations: list[HistoricalSourceEligibilityRow]
    exceptions: list[Any]
    statistics: dict[str, int]
    approval_status: Literal["not_human_approved"]
    master_data_mutations: Literal[0]
    artifact_sha256: str

    @model_validator(mode="after")
    def check_sha(self):
        ids = [row.claim_id for row in self.attestations]
        if not ids or ids != sorted(set(ids)) or self.exceptions:
            raise ValueError("historical eligibility must cover a unique nonempty qualified scope")
        if self.statistics != {"input_claim_count": len(ids), "attested_claim_count": len(ids), "exception_claim_count": 0}:
            raise ValueError("historical source eligibility denominator mismatch")
        if self.artifact_sha256 != sha256_json(self.model_dump(mode="json", exclude={"artifact_sha256"})):
            raise ValueError("historical source eligibility artifact SHA mismatch")
        if set(self.bridge_manifest_binding) != {"path", "file_sha256"} or not Path(self.bridge_manifest_binding["path"]).is_absolute():
            raise ValueError("historical bridge requires an explicit absolute physical binding")
        return self


def build_historical_source_eligibility(*, claim_manifest: dict[str, Any], bridge_manifest_path: Path, store: Any) -> HistoricalSourceEligibilityArtifact:
    validate_content_addressed(claim_manifest, "manifest_sha256")
    bridges = json.loads(bridge_manifest_path.read_text(encoding="utf-8"))
    validate_content_addressed(bridges, "artifact_sha256")
    if bridges.get("schema_version") != "wang_historical_claim_review_bridges_v1" or bridges.get("status") != "verified":
        raise CvpProductionBlocked(["unverified historical review bridge manifest"])
    manifest_rows = {row["claim_id"]: row for row in claim_manifest["claims"]}
    proof_rows = {row["claim_id"]: row for row in bridges["proofs"]}
    if len(proof_rows) != len(bridges["proofs"]) or len(manifest_rows) != len(claim_manifest["claims"]) or set(manifest_rows) != set(proof_rows):
        raise CvpProductionBlocked(["historical review bridge and Claim manifest denominator mismatch"])
    attestations = []
    with store.connect() as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        for cid in sorted(manifest_rows):
            prior_proof = proof_rows[cid]
            current = connection.execute("SELECT payload FROM wang_knowledge.objects WHERE collection='claims' AND object_id=%s AND retired_at IS NULL", (cid,)).fetchone()
            if current is None:
                raise CvpProductionBlocked([f"{cid}: current Claim missing"])
            history = connection.execute("SELECT v.revision,v.content_sha256,v.payload,v.change_set_id,c.source_kind,c.source_sha256,c.status,c.metadata FROM wang_knowledge.object_versions v JOIN wang_knowledge.change_sets c USING(change_set_id) WHERE v.collection='claims' AND v.object_id=%s ORDER BY v.revision", (cid,)).fetchall()
            columns = ("revision", "content_sha256", "payload", "change_set_id", "source_kind", "source_sha256", "status", "metadata")
            evidence, fragments = {}, {}
            for eid in current[0]["evidence_step_ids"]:
                row = connection.execute("SELECT payload FROM wang_knowledge.objects WHERE collection='evidence_steps' AND object_id=%s AND retired_at IS NULL", (eid,)).fetchone()
                if row is None:
                    raise CvpProductionBlocked([f"{cid}: current evidence missing"])
                evidence[eid] = row[0]
                for fid in evidence_fragment_ids(EvidenceStepRecord.model_validate(row[0])):
                    row = connection.execute("SELECT payload FROM wang_knowledge.objects WHERE collection='source_fragments' AND object_id=%s AND retired_at IS NULL", (fid,)).fetchone()
                    if row is None:
                        raise CvpProductionBlocked([f"{cid}: current fragment missing"])
                    fragments[fid] = row[0]
            actual_proof = verify_historical_review_bridge(
                claim_id=cid, current_claim=current[0], versions=[dict(zip(columns, row)) for row in history],
                review_binding=prior_proof["review_binding"], review_input_binding=prior_proof["review_input_binding"],
                current_evidence=evidence, current_fragments=fragments,
                human_closure_binding=prior_proof.get("human_spot_check_closure_binding"),
                sealed_lineage_binding=prior_proof.get("sealed_lineage_binding"),
            )
            if actual_proof != prior_proof:
                raise CvpProductionBlocked([f"{cid}: historical bridge no longer matches current source lineage"])
            if any(actual_proof[key] != manifest_rows[cid][key] for key in ("pinned_claim_revision", "claim_revision_sha256")):
                raise CvpProductionBlocked([f"{cid}: historical eligibility differs from pinned Claim manifest"])
            sources = {(fragment["source_id"], fragment["source_sha256"]) for fragment in fragments.values()}
            if len(sources) != 1:
                raise CvpProductionBlocked([f"{cid}: historical eligibility is not source-local"])
            source_id, source_sha = next(iter(sources))
            source = connection.execute("SELECT payload FROM wang_knowledge.objects WHERE collection='source_documents' AND object_id=%s AND retired_at IS NULL", (source_id,)).fetchone()
            if source is None or source[0].get("source_sha256") != source_sha:
                raise CvpProductionBlocked([f"{cid}: source document and evidence SHA mismatch"])
            row = {"claim_id": cid, "pinned_claim_revision": actual_proof["pinned_claim_revision"],
                   "claim_revision_sha256": actual_proof["claim_revision_sha256"], "source_id": source_id,
                   "source_sha256": source_sha, "historical_review_bridge_sha256": actual_proof["artifact_sha256"]}
            attestations.append(row | {"attestation_sha256": sha256_json(row)})
    body = {"schema_version": "wang_historical_viewpoint_source_eligibility_v1",
            "claim_manifest_sha256": claim_manifest["manifest_sha256"],
            "bridge_manifest_binding": {"path": str(bridge_manifest_path.resolve()), "file_sha256": hashlib.sha256(bridge_manifest_path.read_bytes()).hexdigest()},
            "attestations": attestations, "exceptions": [],
            "statistics": {"input_claim_count": len(attestations), "attested_claim_count": len(attestations), "exception_claim_count": 0},
            "approval_status": "not_human_approved", "master_data_mutations": 0}
    return HistoricalSourceEligibilityArtifact(**body, artifact_sha256=sha256_json(body))


def validate_historical_source_eligibility(payload: dict[str, Any], *, claim_manifest: dict[str, Any], store: Any) -> None:
    artifact = HistoricalSourceEligibilityArtifact.model_validate(payload)
    binding = artifact.bridge_manifest_binding
    path = Path(binding["path"])
    if not path.is_absolute() or hashlib.sha256(path.read_bytes()).hexdigest() != binding["file_sha256"]:
        raise CvpProductionBlocked(["historical bridge manifest physical SHA drift"])
    actual = build_historical_source_eligibility(claim_manifest=claim_manifest, bridge_manifest_path=path, store=store)
    if actual.model_dump(mode="json") != artifact.model_dump(mode="json"):
        raise CvpProductionBlocked(["historical source eligibility differs from authenticated current lineage"])
