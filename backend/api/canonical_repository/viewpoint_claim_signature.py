"""Screening-only semantic signatures for source-local Claims."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .viewpoint_foundation import sha256_json
from .viewpoint_semantic_scheduler import SemanticBundleSchedule

SIGNATURE_PACKET_VERSION = "wang_claim_semantic_signature_packet_v1"
SIGNATURE_PLAN_VERSION = "wang_claim_semantic_signature_plan_v2"
SIGNATURE_RESPONSE_VERSION = "wang_claim_semantic_signature_response_v2"
SIGNATURE_POLICY_VERSION = "claim_semantic_signature_screening_policy_v1"
MAX_CLAIMS_PER_PACKET = 48
MAX_PACKET_BYTES = 96 * 1024


class StrictSignatureModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SignatureInputClaim(StrictSignatureModel):
    claim_id: str
    claim_revision_sha256: str
    statement: str = Field(min_length=1)
    claim_type: str
    attribution: str | None = None
    scripture_refs: list[str]

    @model_validator(mode="after")
    def validate_refs(self) -> "SignatureInputClaim":
        if self.scripture_refs != sorted(set(self.scripture_refs)):
            raise ValueError("signature input scripture refs must be sorted and unique")
        return self


class ClaimSignaturePacket(StrictSignatureModel):
    schema_version: Literal["wang_claim_semantic_signature_packet_v1"] = SIGNATURE_PACKET_VERSION
    policy_version: Literal["claim_semantic_signature_screening_policy_v1"] = SIGNATURE_POLICY_VERSION
    packet_id: str
    schedule_artifact_sha256: str
    claims: list[SignatureInputClaim] = Field(min_length=1, max_length=MAX_CLAIMS_PER_PACKET)
    statistics: dict[str, int]
    packet_sha256: str

    @model_validator(mode="after")
    def validate_packet(self) -> "ClaimSignaturePacket":
        ids = [claim.claim_id for claim in self.claims]
        if ids != sorted(set(ids)):
            raise ValueError("signature packet Claims must use canonical unique order")
        if self.statistics != {"claim_count": len(self.claims)}:
            raise ValueError("signature packet statistics mismatch")
        payload = self.model_dump(mode="json", exclude={"packet_sha256"})
        if self.packet_sha256 != sha256_json(payload):
            raise ValueError("signature packet SHA mismatch")
        identity = dict(payload)
        identity.pop("packet_id")
        if self.packet_id != f"VCSP-{sha256_json(identity)[:20]}":
            raise ValueError("signature packet id mismatch")
        return self


class ClaimSignaturePlan(StrictSignatureModel):
    schema_version: Literal["wang_claim_semantic_signature_plan_v2"] = SIGNATURE_PLAN_VERSION
    schedule_artifact_sha256: str
    candidate_recall_artifact_sha256: str | None
    model_id: str
    backend: Literal["codex_subscription"]
    reasoning_effort: Literal["low", "medium", "high", "xhigh"]
    max_output_tokens: int = Field(ge=1)
    prompt_sha256: str
    packets: list[ClaimSignaturePacket]
    source_ineligible_claim_ids: list[str]
    statistics: dict[str, int]
    model_calls_executed: Literal[0] = 0
    identity_evidence: Literal[False] = False
    apply_allowed: Literal[False] = False
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_plan(self) -> "ClaimSignaturePlan":
        packet_ids = [packet.packet_id for packet in self.packets]
        if packet_ids != sorted(set(packet_ids)):
            raise ValueError("signature plan packets must use canonical unique order")
        claims = [claim for packet in self.packets for claim in packet.claims]
        claim_ids = [claim.claim_id for claim in claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("signature plan must cover each Claim exactly once")
        if self.source_ineligible_claim_ids != sorted(set(self.source_ineligible_claim_ids)):
            raise ValueError("signature source-ineligible ids must be sorted and unique")
        sizes = [len(packet.model_dump_json().encode("utf-8")) for packet in self.packets]
        expected = {
            "input_claim_count": len(claims) + len(self.source_ineligible_claim_ids),
            "source_eligible_claim_count": len(claims),
            "source_ineligible_claim_count": len(self.source_ineligible_claim_ids),
            "packet_count": len(self.packets),
            "model_call_count": len(self.packets),
            "input_bytes": sum(sizes),
            "estimated_input_tokens": sum(max(1, math.ceil(size / 2)) for size in sizes),
        }
        if self.statistics != expected:
            raise ValueError("signature plan statistics mismatch")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("signature plan SHA mismatch")
        return self


class SemanticAtomCandidate(StrictSignatureModel):
    atom_index: int = Field(ge=0)
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    object: str = Field(min_length=1)
    polarity: Literal["affirmed", "denied", "unknown"]
    stance: Literal[
        "endorsed", "rejected", "presented_as_possibility",
        "reported_external", "unknown",
    ]
    modality: str = Field(min_length=1)
    discourse_roles: list[Literal[
        "conclusion", "premise", "observation", "evidence", "example",
        "analogy", "application", "qualification", "external_position",
    ]]
    population_scope: list[str]
    temporal_scope: list[str]
    conditions: list[str]
    material_qualifications: list[str]

    @model_validator(mode="before")
    @classmethod
    def canonicalize_unordered_lists(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        result = dict(value)
        for name in (
            "discourse_roles", "population_scope", "temporal_scope", "conditions",
            "material_qualifications"
        ):
            if isinstance(result.get(name), list):
                result[name] = sorted(set(str(item) for item in result[name]))
        return result

    @model_validator(mode="after")
    def validate_lists(self) -> "SemanticAtomCandidate":
        for name in (
            "discourse_roles", "population_scope", "temporal_scope", "conditions",
            "material_qualifications"
        ):
            values = getattr(self, name)
            if values != sorted(set(values)):
                raise ValueError(f"semantic atom {name} must be sorted and unique")
        return self


class ClaimSemanticSignatureCandidate(StrictSignatureModel):
    claim_id: str
    claim_revision_sha256: str
    semantic_atoms: list[SemanticAtomCandidate] = Field(min_length=1)
    evidence_sufficient: bool
    ambiguities: list[str]
    screening_only: Literal[True] = True
    identity_evidence: Literal[False] = False

    @model_validator(mode="before")
    @classmethod
    def canonicalize_ambiguities(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        result = dict(value)
        if isinstance(result.get("ambiguities"), list):
            result["ambiguities"] = sorted(set(str(item) for item in result["ambiguities"]))
        if result.get("evidence_sufficient") is False and not result.get("ambiguities"):
            result["ambiguities"] = ["model_marked_evidence_insufficient_without_detail"]
        return result

    @model_validator(mode="after")
    def validate_candidate(self) -> "ClaimSemanticSignatureCandidate":
        indexes = [atom.atom_index for atom in self.semantic_atoms]
        if indexes != list(range(len(indexes))):
            raise ValueError("semantic atom indexes must be contiguous canonical order")
        if self.ambiguities != sorted(set(self.ambiguities)):
            raise ValueError("signature ambiguities must be sorted and unique")
        if not self.evidence_sufficient and not self.ambiguities:
            raise ValueError("insufficient signature must state at least one ambiguity")
        return self


class ClaimSignatureResponse(StrictSignatureModel):
    schema_version: Literal["wang_claim_semantic_signature_response_v2"] = SIGNATURE_RESPONSE_VERSION
    packet_sha256: str
    signatures: list[ClaimSemanticSignatureCandidate]

    @model_validator(mode="after")
    def validate_signatures(self) -> "ClaimSignatureResponse":
        ids = [signature.claim_id for signature in self.signatures]
        if ids != sorted(set(ids)):
            raise ValueError("signature response must use canonical Claim order")
        return self


class ClaimSignatureIndexArtifact(StrictSignatureModel):
    schema_version: Literal["wang_claim_semantic_signature_index_v1"] = (
        "wang_claim_semantic_signature_index_v1"
    )
    plan_artifact_sha256: str
    model_id: str
    backend: Literal["codex_subscription"]
    prompt_sha256: str
    generation_config_sha256: str
    call_artifact_sha256s: list[str]
    signatures: list[ClaimSemanticSignatureCandidate]
    source_ineligible_claim_ids: list[str]
    statistics: dict[str, int]
    identity_evidence: Literal[False] = False
    apply_allowed: Literal[False] = False
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_index(self) -> "ClaimSignatureIndexArtifact":
        if self.call_artifact_sha256s != sorted(set(self.call_artifact_sha256s)):
            raise ValueError("signature index call SHAs must be sorted and unique")
        ids = [signature.claim_id for signature in self.signatures]
        if ids != sorted(set(ids)):
            raise ValueError("signature index Claims must be canonical and unique")
        if self.source_ineligible_claim_ids != sorted(set(self.source_ineligible_claim_ids)):
            raise ValueError("signature index source-ineligible ids must be canonical")
        stances = Counter(
            atom.stance for signature in self.signatures for atom in signature.semantic_atoms
        )
        expected = {
            "input_claim_count": len(self.signatures) + len(self.source_ineligible_claim_ids),
            "signature_count": len(self.signatures),
            "source_ineligible_claim_count": len(self.source_ineligible_claim_ids),
            "semantic_atom_count": sum(len(signature.semantic_atoms) for signature in self.signatures),
            "multi_atom_claim_count": sum(len(signature.semantic_atoms) > 1 for signature in self.signatures),
            "insufficient_evidence_count": sum(not signature.evidence_sufficient for signature in self.signatures),
            "endorsed_atom_count": stances["endorsed"],
            "rejected_atom_count": stances["rejected"],
            "possibility_atom_count": stances["presented_as_possibility"],
            "external_atom_count": stances["reported_external"],
            "unknown_stance_atom_count": stances["unknown"],
        }
        if self.statistics != expected:
            raise ValueError("signature index statistics mismatch")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("signature index artifact SHA mismatch")
        return self


def build_claim_signature_index(
    *,
    plan: ClaimSignaturePlan,
    responses_by_packet_id: Mapping[str, ClaimSignatureResponse],
    call_artifact_sha_by_packet_id: Mapping[str, str],
    generation_config_sha256: str,
) -> ClaimSignatureIndexArtifact:
    expected_packet_ids = {packet.packet_id for packet in plan.packets}
    if set(responses_by_packet_id) != expected_packet_ids:
        raise ValueError("signature index responses must cover plan packets exactly once")
    if set(call_artifact_sha_by_packet_id) != expected_packet_ids:
        raise ValueError("signature index call artifacts must cover plan packets exactly once")
    signatures: list[ClaimSemanticSignatureCandidate] = []
    for packet in plan.packets:
        response = validate_signature_response(packet, responses_by_packet_id[packet.packet_id])
        signatures.extend(response.signatures)
    signatures.sort(key=lambda signature: signature.claim_id)
    stances = Counter(atom.stance for signature in signatures for atom in signature.semantic_atoms)
    statistics = {
        "input_claim_count": len(signatures) + len(plan.source_ineligible_claim_ids),
        "signature_count": len(signatures),
        "source_ineligible_claim_count": len(plan.source_ineligible_claim_ids),
        "semantic_atom_count": sum(len(signature.semantic_atoms) for signature in signatures),
        "multi_atom_claim_count": sum(len(signature.semantic_atoms) > 1 for signature in signatures),
        "insufficient_evidence_count": sum(not signature.evidence_sufficient for signature in signatures),
        "endorsed_atom_count": stances["endorsed"],
        "rejected_atom_count": stances["rejected"],
        "possibility_atom_count": stances["presented_as_possibility"],
        "external_atom_count": stances["reported_external"],
        "unknown_stance_atom_count": stances["unknown"],
    }
    payload = {
        "schema_version": "wang_claim_semantic_signature_index_v1",
        "plan_artifact_sha256": plan.artifact_sha256,
        "model_id": plan.model_id,
        "backend": plan.backend,
        "prompt_sha256": plan.prompt_sha256,
        "generation_config_sha256": generation_config_sha256,
        "call_artifact_sha256s": sorted(call_artifact_sha_by_packet_id.values()),
        "signatures": [signature.model_dump(mode="json") for signature in signatures],
        "source_ineligible_claim_ids": plan.source_ineligible_claim_ids,
        "statistics": statistics,
        "identity_evidence": False,
        "apply_allowed": False,
    }
    return ClaimSignatureIndexArtifact(**payload, artifact_sha256=sha256_json(payload))


def validate_signature_response(
    packet: ClaimSignaturePacket,
    response: Mapping[str, Any] | ClaimSignatureResponse,
) -> ClaimSignatureResponse:
    if isinstance(response, ClaimSignatureResponse):
        result = response
    else:
        payload = dict(response)
        signatures = [dict(item) for item in payload.get("signatures") or []]
        expected_revisions = {
            claim.claim_id: claim.claim_revision_sha256 for claim in packet.claims
        }
        for signature in signatures:
            claim_id = signature.get("claim_id")
            if claim_id in expected_revisions:
                signature["claim_revision_sha256"] = expected_revisions[claim_id]
        payload["signatures"] = signatures
        result = ClaimSignatureResponse.model_validate(payload)
    if result.packet_sha256 != packet.packet_sha256:
        raise ValueError("signature response belongs to another packet")
    expected = [(claim.claim_id, claim.claim_revision_sha256) for claim in packet.claims]
    actual = [(signature.claim_id, signature.claim_revision_sha256) for signature in result.signatures]
    if actual != expected:
        raise ValueError("signature response must cover packet Claims exactly once")
    return result


def _make_packet(claims: list[SignatureInputClaim], schedule_sha: str) -> ClaimSignaturePacket:
    unsigned: dict[str, Any] = {
        "schema_version": SIGNATURE_PACKET_VERSION,
        "policy_version": SIGNATURE_POLICY_VERSION,
        "packet_id": "pending",
        "schedule_artifact_sha256": schedule_sha,
        "claims": [claim.model_dump(mode="json") for claim in claims],
        "statistics": {"claim_count": len(claims)},
    }
    identity = dict(unsigned)
    identity.pop("packet_id")
    unsigned["packet_id"] = f"VCSP-{sha256_json(identity)[:20]}"
    packet = ClaimSignaturePacket(**unsigned, packet_sha256=sha256_json(unsigned))
    if len(packet.model_dump_json().encode("utf-8")) > MAX_PACKET_BYTES:
        raise ValueError("Claim signature packet exceeds byte budget")
    return packet


def build_claim_signature_plan(
    *, schedule: SemanticBundleSchedule,
    candidate_recall_artifact_sha256: str | None,
    source_ineligible_claim_ids: list[str],
    model_id: str,
    reasoning_effort: Literal["low", "medium", "high", "xhigh"],
    max_output_tokens: int,
    prompt_sha256: str,
) -> ClaimSignaturePlan:
    if schedule.candidate_recall_artifact_sha256 != candidate_recall_artifact_sha256:
        raise ValueError("signature schedule and candidate recall binding mismatch")
    claims: dict[str, SignatureInputClaim] = {}
    for work in schedule.work_items:
        rows = list(work.semantic_input.get("claims") or [])
        if len(rows) != 1:
            raise ValueError("signature plan requires one source Claim per work item")
        row = rows[0]
        claim = SignatureInputClaim(
            claim_id=str(row["claim_id"]),
            claim_revision_sha256=str(row["claim_revision_sha256"]),
            statement=str(row["statement"]),
            claim_type=str(row["claim_type"]),
            attribution=row.get("attribution"),
            scripture_refs=sorted(set(str(ref) for ref in row.get("scripture_refs") or [])),
        )
        if claim.claim_id in claims:
            raise ValueError("signature plan encountered duplicate Claim")
        claims[claim.claim_id] = claim
    ordered = [claims[claim_id] for claim_id in sorted(claims)]
    packets: list[ClaimSignaturePacket] = []
    current: list[SignatureInputClaim] = []
    for claim in ordered:
        candidate = current + [claim]
        if len(candidate) > MAX_CLAIMS_PER_PACKET:
            packets.append(_make_packet(current, schedule.artifact_sha256))
            current = [claim]
            continue
        try:
            _make_packet(candidate, schedule.artifact_sha256)
        except ValueError as exc:
            if not current or "exceeds byte budget" not in str(exc):
                raise
            packets.append(_make_packet(current, schedule.artifact_sha256))
            current = [claim]
            _make_packet(current, schedule.artifact_sha256)
        else:
            current = candidate
    if current:
        packets.append(_make_packet(current, schedule.artifact_sha256))
    packets.sort(key=lambda packet: packet.packet_id)
    sizes = [len(packet.model_dump_json().encode("utf-8")) for packet in packets]
    statistics = {
        "input_claim_count": len(ordered) + len(source_ineligible_claim_ids),
        "source_eligible_claim_count": len(ordered),
        "source_ineligible_claim_count": len(source_ineligible_claim_ids),
        "packet_count": len(packets),
        "model_call_count": len(packets),
        "input_bytes": sum(sizes),
        "estimated_input_tokens": sum(max(1, math.ceil(size / 2)) for size in sizes),
    }
    payload = {
        "schema_version": SIGNATURE_PLAN_VERSION,
        "schedule_artifact_sha256": schedule.artifact_sha256,
        "candidate_recall_artifact_sha256": candidate_recall_artifact_sha256,
        "model_id": model_id,
        "backend": "codex_subscription",
        "reasoning_effort": reasoning_effort,
        "max_output_tokens": max_output_tokens,
        "prompt_sha256": prompt_sha256,
        "packets": [packet.model_dump(mode="json") for packet in packets],
        "source_ineligible_claim_ids": sorted(source_ineligible_claim_ids),
        "statistics": statistics,
        "model_calls_executed": 0,
        "identity_evidence": False,
        "apply_allowed": False,
    }
    return ClaimSignaturePlan(**payload, artifact_sha256=sha256_json(payload))
