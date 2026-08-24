"""Batch CanonicalViewpoint resolution: proposal, review, deterministic gates.

One reviewed Claim batch goes to a single proposer call, deterministic code
checks every reference and span against the pinned Claims, an independent
reviewer judges the semantics, and only the survivors reach a ChangeSet.

The model never assigns a canonical id, an approval status, or a derived
count.  It emits character offsets and dispositions; this module does the
slicing, the coverage arithmetic and the fail-closed comparisons.
"""

from __future__ import annotations

from copy import deepcopy
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .viewpoint_foundation import sha256_json
from .viewpoint_resolution import ReviewClaim

BATCH_PACKET_VERSION = "wang_canonical_viewpoint_batch_packet_v1"
PROPOSAL_VERSION = "wang_canonical_viewpoint_proposal_v1"
REVIEW_VERSION = "wang_canonical_viewpoint_review_v1"
VALIDATION_VERSION = "wang_canonical_viewpoint_batch_validation_v1"
CVP_READBACK_VERSION = "wang_cvp_batch_readback_receipt_v1"
ROUTE_JOB_VERSION = "wang_route_resolution_job_v1"
ROUTE_WORK_UNIT_VERSION = "wang_route_resolution_work_unit_v1"

#: Claims per batch.  The 62-Claim POC ran over ten minutes against a 900s
#: subprocess ceiling, and the reviewer emits more than the proposer does, so
#: the ceiling is set well below the size that has actually been observed.
DEFAULT_BATCH_SIZE = 20

EXISTING_DISPOSITIONS = frozenset(
    {
        "member_existing",
        "support_existing",
        "qualification_existing",
        "tension_existing",
    }
)
TERMINAL_DISPOSITIONS = frozenset(
    {"new_viewpoint", "no_registry_assertion", "deferred"}
)
DISPOSITIONS = EXISTING_DISPOSITIONS | TERMINAL_DISPOSITIONS


class BatchResolutionError(ValueError):
    def __init__(self, findings: Sequence[str]):
        self.findings = list(findings)
        super().__init__("batch resolution failed: " + " | ".join(self.findings))


class StrictBatchModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CommittedViewpointRevision(StrictBatchModel):
    """One logical viewpoint revision proven current by authority readback."""

    viewpoint_id: str = Field(min_length=1)
    viewpoint_revision_id: str = Field(min_length=1)


class CvpBatchReadbackReceipt(StrictBatchModel):
    """Authority proof required before route work may be enqueued.

    This is deliberately not a model approval artifact.  It records that the
    deterministic CVP ChangeSet was applied and that a fresh Registry read saw
    the exact revisions the ChangeSet intended to make current.
    """

    schema_version: Literal["wang_cvp_batch_readback_receipt_v1"] = (
        CVP_READBACK_VERSION
    )
    scope_label: str = Field(min_length=1)
    scope_manifest_sha256: str = Field(min_length=1)
    triggering_cvp_batch_id: str = Field(min_length=1)
    cvp_changeset_id: str = Field(min_length=1)
    cvp_changeset_sha256: str = Field(min_length=1)
    committed_viewpoint_revisions: list[CommittedViewpointRevision] = Field(
        min_length=1
    )
    readback_status: Literal["verified"] = "verified"
    artifact_sha256: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_receipt(self) -> "CvpBatchReadbackReceipt":
        pairs = [
            (item.viewpoint_id, item.viewpoint_revision_id)
            for item in self.committed_viewpoint_revisions
        ]
        if pairs != sorted(set(pairs)):
            raise ValueError("committed viewpoint revisions must be sorted and unique")
        if len({item.viewpoint_id for item in self.committed_viewpoint_revisions}) != len(
            self.committed_viewpoint_revisions
        ):
            raise ValueError("readback may name only one current revision per viewpoint")
        body = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(body):
            raise ValueError("CVP readback receipt SHA mismatch")
        return self


class RouteResolutionJob(StrictBatchModel):
    """Immutable enqueue artifact created only from a verified CVP readback."""

    schema_version: Literal["wang_route_resolution_job_v1"] = ROUTE_JOB_VERSION
    job_id: str = Field(min_length=1)
    scope_label: str = Field(min_length=1)
    scope_manifest_sha256: str = Field(min_length=1)
    triggering_cvp_batch_id: str = Field(min_length=1)
    cvp_changeset_sha256: str = Field(min_length=1)
    cvp_readback_sha256: str = Field(min_length=1)
    logical_viewpoint_ids: list[str] = Field(min_length=1)
    enqueued_viewpoint_revision_ids: list[str] = Field(min_length=1)
    evidence_scope_sha256: str = Field(min_length=1)
    enqueue_reason: Literal["created_or_revised"] = "created_or_revised"
    route_policy_fingerprint_sha256: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    status: Literal["queued"] = "queued"
    artifact_sha256: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_job(self) -> "RouteResolutionJob":
        if self.logical_viewpoint_ids != sorted(set(self.logical_viewpoint_ids)):
            raise ValueError("logical_viewpoint_ids must be sorted and unique")
        if len(self.enqueued_viewpoint_revision_ids) != len(
            set(self.enqueued_viewpoint_revision_ids)
        ):
            raise ValueError("enqueued_viewpoint_revision_ids must be unique")
        if len(self.logical_viewpoint_ids) != len(
            self.enqueued_viewpoint_revision_ids
        ):
            raise ValueError("route job viewpoint and revision cuts must have equal size")
        body = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(body):
            raise ValueError("RouteResolutionJob SHA mismatch")
        return self


class RouteResolutionWorkUnit(StrictBatchModel):
    """Derived current queue cut; original enqueue jobs remain immutable."""

    schema_version: Literal["wang_route_resolution_work_unit_v1"] = (
        ROUTE_WORK_UNIT_VERSION
    )
    scope_label: str = Field(min_length=1)
    scope_manifest_sha256: str = Field(min_length=1)
    source_job_ids: list[str] = Field(min_length=1)
    current_viewpoint_revisions: list[CommittedViewpointRevision] = Field(min_length=1)
    superseded_job_ids: list[str] = Field(default_factory=list)
    artifact_sha256: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_work_unit(self) -> "RouteResolutionWorkUnit":
        for field_name in ("source_job_ids", "superseded_job_ids"):
            values = getattr(self, field_name)
            if values != sorted(set(values)):
                raise ValueError(f"{field_name} must be sorted and unique")
        pairs = [
            (item.viewpoint_id, item.viewpoint_revision_id)
            for item in self.current_viewpoint_revisions
        ]
        if pairs != sorted(set(pairs)) or len({item[0] for item in pairs}) != len(pairs):
            raise ValueError("work unit current revisions must be sorted and unique")
        body = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(body):
            raise ValueError("RouteResolutionWorkUnit SHA mismatch")
        return self


def build_cvp_batch_readback_receipt(
    *,
    scope_label: str,
    scope_manifest_sha256: str,
    triggering_cvp_batch_id: str,
    cvp_changeset_id: str,
    cvp_changeset_sha256: str,
    expected_current_revisions: Mapping[str, str],
    observed_current_revisions: Mapping[str, str],
) -> CvpBatchReadbackReceipt:
    """Build a receipt only when authority readback exactly matches intent."""

    expected = dict(expected_current_revisions)
    observed = {key: observed_current_revisions.get(key) for key in expected}
    if not expected:
        raise BatchResolutionError(["CVP ChangeSet affected no viewpoint revisions"])
    if expected != observed:
        findings = [
            f"{viewpoint_id}: expected current {revision_id}, observed "
            f"{observed.get(viewpoint_id)}"
            for viewpoint_id, revision_id in sorted(expected.items())
            if observed.get(viewpoint_id) != revision_id
        ]
        raise BatchResolutionError(findings)
    body = {
        "schema_version": CVP_READBACK_VERSION,
        "scope_label": scope_label,
        "scope_manifest_sha256": scope_manifest_sha256,
        "triggering_cvp_batch_id": triggering_cvp_batch_id,
        "cvp_changeset_id": cvp_changeset_id,
        "cvp_changeset_sha256": cvp_changeset_sha256,
        "committed_viewpoint_revisions": [
            {
                "viewpoint_id": viewpoint_id,
                "viewpoint_revision_id": revision_id,
            }
            for viewpoint_id, revision_id in sorted(expected.items())
        ],
        "readback_status": "verified",
    }
    return CvpBatchReadbackReceipt.model_validate(
        body | {"artifact_sha256": sha256_json(body)}
    )


def build_route_resolution_job(
    *,
    receipt: CvpBatchReadbackReceipt,
    evidence_scope_sha256: str,
    route_policy_fingerprint_sha256: str,
) -> RouteResolutionJob:
    """Derive one idempotent enqueue artifact from verified committed CVPs."""

    viewpoint_ids = sorted(
        item.viewpoint_id for item in receipt.committed_viewpoint_revisions
    )
    revisions_by_viewpoint = {
        item.viewpoint_id: item.viewpoint_revision_id
        for item in receipt.committed_viewpoint_revisions
    }
    revision_ids = [revisions_by_viewpoint[key] for key in viewpoint_ids]
    identity = {
        "scope_manifest_sha256": receipt.scope_manifest_sha256,
        "cvp_readback_sha256": receipt.artifact_sha256,
        "logical_viewpoint_ids": viewpoint_ids,
        "enqueued_viewpoint_revision_ids": revision_ids,
        "evidence_scope_sha256": evidence_scope_sha256,
        "route_policy_fingerprint_sha256": route_policy_fingerprint_sha256,
    }
    idempotency_key = sha256_json(identity)
    body = {
        "schema_version": ROUTE_JOB_VERSION,
        "job_id": f"RRJ-{idempotency_key[:20]}",
        "scope_label": receipt.scope_label,
        "scope_manifest_sha256": receipt.scope_manifest_sha256,
        "triggering_cvp_batch_id": receipt.triggering_cvp_batch_id,
        "cvp_changeset_sha256": receipt.cvp_changeset_sha256,
        "cvp_readback_sha256": receipt.artifact_sha256,
        "logical_viewpoint_ids": viewpoint_ids,
        "enqueued_viewpoint_revision_ids": revision_ids,
        "evidence_scope_sha256": evidence_scope_sha256,
        "enqueue_reason": "created_or_revised",
        "route_policy_fingerprint_sha256": route_policy_fingerprint_sha256,
        "idempotency_key": idempotency_key,
        "status": "queued",
    }
    return RouteResolutionJob.model_validate(
        body | {"artifact_sha256": sha256_json(body)}
    )


def coalesce_route_resolution_jobs(
    jobs: Sequence[RouteResolutionJob],
    *,
    current_viewpoint_revisions: Mapping[str, str],
) -> RouteResolutionWorkUnit:
    """Replace queued stale revisions with the Registry's current revision cut."""

    if not jobs:
        raise ValueError("cannot coalesce an empty route queue")
    jobs = list({item.job_id: item for item in jobs}.values())
    scope_keys = {(item.scope_label, item.scope_manifest_sha256) for item in jobs}
    if len(scope_keys) != 1:
        raise ValueError("one route work unit cannot cross scope manifests")
    requested = sorted(
        {viewpoint_id for item in jobs for viewpoint_id in item.logical_viewpoint_ids}
    )
    missing = [key for key in requested if not current_viewpoint_revisions.get(key)]
    if missing:
        raise BatchResolutionError(
            [f"{key}: no current Registry revision at queue cut" for key in missing]
        )
    current = [
        {
            "viewpoint_id": key,
            "viewpoint_revision_id": current_viewpoint_revisions[key],
        }
        for key in requested
    ]
    superseded = sorted(
        item.job_id
        for item in jobs
        if any(
            current_viewpoint_revisions[viewpoint_id] != revision_id
            for viewpoint_id, revision_id in zip(
                item.logical_viewpoint_ids,
                item.enqueued_viewpoint_revision_ids,
                strict=True,
            )
        )
    )
    scope_label, scope_manifest_sha256 = next(iter(scope_keys))
    body = {
        "schema_version": ROUTE_WORK_UNIT_VERSION,
        "scope_label": scope_label,
        "scope_manifest_sha256": scope_manifest_sha256,
        "source_job_ids": sorted({item.job_id for item in jobs}),
        "current_viewpoint_revisions": current,
        "superseded_job_ids": superseded,
    }
    return RouteResolutionWorkUnit.model_validate(
        body | {"artifact_sha256": sha256_json(body)}
    )


class ProposedSpan(StrictBatchModel):
    """One character range of a pinned Claim statement.

    Offsets are Unicode code points over the raw stored statement, `start`
    inclusive and `end` exclusive, matching Python slicing.  No normalization
    of any kind is applied.  ``exact_text`` is not redundant: without it an
    off-by-one offset would be checked against the very slice it selected, so
    it is the only evidence that the model meant this text.
    """

    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    exact_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_span(self) -> "ProposedSpan":
        if self.end_char <= self.start_char:
            raise ValueError("span range is empty or reversed")
        if len(self.exact_text) != self.end_char - self.start_char:
            raise ValueError("span exact_text length does not match its range")
        return self


class ProposedComponent(StrictBatchModel):
    """One truth condition carved out of a Claim, with its disposition.

    ``statement_component`` is deliberately absent: it is the concatenation of
    the spans and carries no independent signal, so making the model emit it
    would spend output tokens on text the validator can rebuild.
    """

    spans: list[ProposedSpan] = Field(min_length=1)
    disposition: Literal[
        "member_existing",
        "support_existing",
        "qualification_existing",
        "tension_existing",
        "new_viewpoint",
        "no_registry_assertion",
        "deferred",
    ]
    target_viewpoint_revision_id: str | None = None
    local_new_viewpoint_key: str | None = None
    evidence_step_ids: list[str] = Field(default_factory=list)
    source_fragment_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_component(self) -> "ProposedComponent":
        keys = [(item.start_char, item.end_char) for item in self.spans]
        if len(keys) != len(set(keys)):
            raise ValueError("component spans must be unique")
        keys = sorted(keys)
        for earlier, later in zip(keys, keys[1:], strict=False):
            if later[0] < earlier[1]:
                raise ValueError("component spans overlap")
        if self.disposition in EXISTING_DISPOSITIONS:
            if not self.target_viewpoint_revision_id:
                raise ValueError(f"{self.disposition} requires a target viewpoint revision")
            if self.local_new_viewpoint_key:
                raise ValueError(f"{self.disposition} may not claim a new viewpoint key")
        elif self.disposition == "new_viewpoint":
            if not self.local_new_viewpoint_key:
                raise ValueError("new_viewpoint requires a local candidate key")
            if self.target_viewpoint_revision_id:
                raise ValueError("new_viewpoint may not target an existing revision")
        else:
            if self.target_viewpoint_revision_id or self.local_new_viewpoint_key:
                raise ValueError(f"{self.disposition} may not carry an identity target")
        needs_evidence = self.disposition in EXISTING_DISPOSITIONS | {"new_viewpoint"}
        if needs_evidence and not (self.evidence_step_ids and self.source_fragment_ids):
            raise ValueError(f"{self.disposition} requires evidence bindings")
        return self

    def statement_component(self) -> str:
        """Rebuild the component text the model referred to."""

        return "".join(item.exact_text for item in self.spans)

    def canonical_spans(self) -> list[list[Any]]:
        return [[item.start_char, item.end_char, item.exact_text] for item in self.spans]


class ProposedClaimDecision(StrictBatchModel):
    claim_id: str
    components: list[ProposedComponent] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_decision(self) -> "ProposedClaimDecision":
        keys = [
            (span.start_char, span.end_char)
            for component in self.components
            for span in component.spans
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("a Claim may not bind the same span to two components")
        return self


class NewViewpointCandidate(StrictBatchModel):
    local_key: str = Field(min_length=1)
    core_proposition: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    # Intransitive/copular signatures such as "该论证成立" have no object.
    # Empty is semantically preferable to inventing a filler noun phrase.
    object: str
    polarity: Literal["affirmed", "denied"]
    modality: str = Field(min_length=1)
    scripture_scope: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    population_scope: list[str] = Field(default_factory=list)
    novelty_comparison: str = Field(min_length=1)



#: Controlled node roles. Route identity turns on the ordered skeleton, so the
#: roles must be a fixed vocabulary — a free-text label would let two sermons
#: false-split on wording, or false-merge on a shared word.
ROUTE_NODE_ROLES = (
    "observation",
    "premise",
    "bridge",
    "objection",
    "response",
    "qualification",
    "conclusion",
    "application",
)

#: Broad method facets for retrieval and blockers. Never a route id: one code
#: covers many routes, and one route may carry several compatible codes.
INFERENCE_METHOD_CODES = (
    "lexical_semantics",
    "morphology",
    "syntax",
    "literary_context",
    "historical_context",
    "cross_scripture",
    "contrast_elimination",
    "analogy_typology",
    "causal_reasoning",
    "theological_synthesis",
    "pastoral_application",
    "other",
)

ATTESTATION_STATUSES = ("attested", "missing", "ambiguous")


class ConclusionRef(StrictBatchModel):
    """A route conclusion is an already-approved ViewpointRevision.

    Route generation begins only after the CVP phase has committed and read
    back the scope's viewpoints.  A batch-local candidate is therefore never a
    legal route conclusion.
    """

    target_viewpoint_revision_id: str = Field(min_length=1)

    def key(self) -> str:
        return self.target_viewpoint_revision_id


class InferenceNode(StrictBatchModel):
    route_step_key: str = Field(min_length=1)
    role: Literal[ROUTE_NODE_ROLES]  # type: ignore[valid-type]
    normalized_proposition: str | None = None
    conclusion_ref: ConclusionRef | None = None
    required_for_full_attestation: bool

    @model_validator(mode="after")
    def validate_node(self) -> "InferenceNode":
        if self.role == "conclusion":
            if self.conclusion_ref is None:
                raise ValueError("a conclusion node must name the viewpoint it reaches")
        elif self.conclusion_ref is not None:
            raise ValueError("only a conclusion node names a viewpoint")
        elif not self.normalized_proposition:
            raise ValueError(f"{self.role} node requires a normalized proposition")
        return self


class ArgumentRouteCandidate(StrictBatchModel):
    local_route_key: str = Field(min_length=1)
    conclusion_ref: ConclusionRef
    proposed_action: Literal["match_existing", "create_new", "defer"]
    target_argument_route_revision_id: str | None = None
    route_label: str = Field(min_length=1)
    inference_method_codes: list[str] = Field(min_length=1)
    inference_method_note: str | None = None
    ordered_inference_nodes: list[InferenceNode] = Field(min_length=2)
    identity_comparison: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_candidate(self) -> "ArgumentRouteCandidate":
        keys = [item.route_step_key for item in self.ordered_inference_nodes]
        if len(keys) != len(set(keys)):
            raise ValueError("route step keys must be unique")
        for code in self.inference_method_codes:
            if code not in INFERENCE_METHOD_CODES:
                raise ValueError(f"{code} is not a policy inference method code")
        if "other" in self.inference_method_codes and not self.inference_method_note:
            raise ValueError("method code other requires a reviewable note")
        if self.ordered_inference_nodes[-1].role != "conclusion":
            raise ValueError("a route ends at its conclusion node")
        if sum(1 for item in self.ordered_inference_nodes if item.role == "conclusion") != 1:
            raise ValueError("a route reaches exactly one conclusion")
        if self.proposed_action == "match_existing":
            if not self.target_argument_route_revision_id:
                raise ValueError("match_existing must pin the route revision it matches")
        elif self.target_argument_route_revision_id:
            raise ValueError(f"{self.proposed_action} may not pin an existing route revision")
        return self


class RouteRef(StrictBatchModel):
    target_argument_route_revision_id: str | None = None
    local_route_key: str | None = None

    @model_validator(mode="after")
    def validate_ref(self) -> "RouteRef":
        if bool(self.target_argument_route_revision_id) == bool(self.local_route_key):
            raise ValueError("an attestation names exactly one of existing revision or local key")
        return self

    def key(self) -> str:
        return str(self.target_argument_route_revision_id or self.local_route_key)


class StepBinding(StrictBatchModel):
    route_step_key: str = Field(min_length=1)
    claim_component_keys: list[str] = Field(default_factory=list)
    evidence_step_ids: list[str] = Field(default_factory=list)
    source_fragment_ids: list[str] = Field(default_factory=list)
    attestation_status: Literal[ATTESTATION_STATUSES]  # type: ignore[valid-type]

    @model_validator(mode="after")
    def validate_binding(self) -> "StepBinding":
        if self.attestation_status == "attested":
            if not self.claim_component_keys:
                raise ValueError("an attested step must bind at least one Claim component")
            if not self.evidence_step_ids:
                raise ValueError("an attested step must bind at least one EvidenceStep")
        return self


class SourceRouteAttestation(StrictBatchModel):
    """One route as it actually appears in one source revision."""

    local_attestation_key: str = Field(min_length=1)
    route_ref: RouteRef
    source_id: str
    source_revision_sha256: str = Field(min_length=1)
    claim_ids: list[str] = Field(min_length=1)
    step_bindings: list[StepBinding] = Field(min_length=1)
    terminal_claim_component_key: str | None = None
    completeness: Literal["full", "partial"]
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_attestation(self) -> "SourceRouteAttestation":
        keys = [item.route_step_key for item in self.step_bindings]
        if len(keys) != len(set(keys)):
            raise ValueError("an attestation binds each route step at most once")
        if self.completeness == "full" and not self.terminal_claim_component_key:
            raise ValueError("a full attestation names the component stating the conclusion")
        return self


class RouteComponentBinding(StrictBatchModel):
    """Deterministically compiled component available to the Route phase."""

    claim_component_key: str = Field(pattern=r"^CCK-[0-9a-f]{64}$")
    claim_id: str
    source_id: str
    disposition: Literal[
        "member_existing",
        "support_existing",
        "qualification_existing",
        "tension_existing",
        "no_registry_assertion",
        "deferred",
    ]
    target_viewpoint_revision_id: str | None = None
    statement_component: str = Field(min_length=1)
    spans: list[ProposedSpan] = Field(min_length=1)
    evidence_step_ids: list[str] = Field(default_factory=list)
    source_fragment_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_target(self) -> "RouteComponentBinding":
        if self.disposition in EXISTING_DISPOSITIONS:
            if not self.target_viewpoint_revision_id:
                raise ValueError(f"{self.disposition} requires a target viewpoint revision")
        elif self.target_viewpoint_revision_id:
            raise ValueError(f"{self.disposition} may not carry a viewpoint target")
        return self


class CanonicalViewpointProposalResponse(StrictBatchModel):
    """Exactly what the proposer model returns.

    No ids, no approval status, no counts: everything derivable is derived by
    :func:`validate_proposal` instead.
    """

    schema_version: Literal["wang_canonical_viewpoint_proposal_v1"] = PROPOSAL_VERSION
    batch_id: str
    claim_decisions: list[ProposedClaimDecision] = Field(min_length=1)
    new_viewpoint_candidates: list[NewViewpointCandidate] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_response(self) -> "CanonicalViewpointProposalResponse":
        claim_ids = [item.claim_id for item in self.claim_decisions]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("proposal claim decisions must be unique")
        keys = [item.local_key for item in self.new_viewpoint_candidates]
        if len(keys) != len(set(keys)):
            raise ValueError("new viewpoint candidates must be unique")
        return self


class ReviewedChange(StrictBatchModel):
    """One decision about one proposed CVP component."""

    claim_id: str
    component_index: int = Field(ge=0)
    decision: Literal["pass", "correct", "reject", "defer"]
    finding_codes: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)
    correction: str | None = None

    @model_validator(mode="after")
    def validate_change(self) -> "ReviewedChange":
        if self.finding_codes != sorted(set(self.finding_codes)):
            raise ValueError("finding codes must be sorted and unique")
        if self.decision == "pass":
            if self.finding_codes or self.correction:
                raise ValueError("a passing change carries no finding or correction")
        elif not self.finding_codes:
            raise ValueError(f"{self.decision} requires at least one finding code")
        if self.decision == "correct" and not self.correction:
            raise ValueError("correct requires the acceptance criteria of the correction")
        return self


class NoveltyReview(StrictBatchModel):
    status: Literal["pass", "missed_novelty"]
    missed_claim_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_novelty(self) -> "NoveltyReview":
        if self.missed_claim_ids != sorted(set(self.missed_claim_ids)):
            raise ValueError("missed claim ids must be sorted and unique")
        if self.status == "missed_novelty" and not self.missed_claim_ids:
            raise ValueError("missed_novelty requires the Claims that were missed")
        if self.status == "pass" and self.missed_claim_ids:
            raise ValueError("a passing novelty review names no missed Claims")
        return self


class CanonicalViewpointReviewResponse(StrictBatchModel):
    schema_version: Literal["wang_canonical_viewpoint_review_v1"] = REVIEW_VERSION
    proposal_sha256: str
    change_reviews: list[ReviewedChange] = Field(min_length=1)
    novelty_review: NoveltyReview

    @model_validator(mode="after")
    def validate_review(self) -> "CanonicalViewpointReviewResponse":
        keys = [(item.claim_id, item.component_index) for item in self.change_reviews]
        if len(keys) != len(set(keys)):
            raise ValueError("change reviews must be unique")
        return self

    def outcome(self) -> str:
        if self.novelty_review.status != "pass":
            return "findings"
        if any(item.decision != "pass" for item in self.change_reviews):
            return "findings"
        return "pass"


def split_batches(
    claim_ids: Sequence[str], *, batch_size: int = DEFAULT_BATCH_SIZE
) -> list[list[str]]:
    """Cut a scope into ordered batches of at most ``batch_size`` Claims."""

    if batch_size < 1:
        raise ValueError("batch size must be positive")
    ordered = sorted(set(claim_ids))
    if len(ordered) != len(claim_ids):
        raise ValueError("batch scope contains duplicate Claim ids")
    return [ordered[index : index + batch_size] for index in range(0, len(ordered), batch_size)]


def component_key(claim: ReviewClaim, component: ProposedComponent) -> str:
    """Derived uniqueness key for one Claim component.

    Never stored: `component_locator` is only valid on `equivalent_component`
    links, so an `equivalent_full` link has nowhere to put one.  Computing the
    key instead lets both link types share the single-active-owner invariant.
    """

    return "CCK-" + sha256_json(
        {
            "claim_id": claim.claim_id,
            "claim_revision_sha256": claim.claim_revision_sha256,
            "canonical_spans": component.canonical_spans(),
        }
    )


def _route_findings(
    *,
    proposal: "ArgumentRouteProposalResponse",
    claims: Sequence[ReviewClaim],
    approved_revisions: set[str],
    known_route_revisions: set[str],
    known_route_conclusions: Mapping[str, str],
    component_bindings: Mapping[str, RouteComponentBinding],
) -> list[str]:
    """Check routes and attestations, above all for cross-source composition.

    An attestation must live entirely inside one source revision. Assembling a
    premise from one sermon and a conclusion from another produces an argument
    the professor never delivered anywhere — the one error this layer exists to
    make impossible.
    """

    findings: list[str] = []
    claim_index = {item.claim_id: item for item in claims}
    routes = {item.local_route_key: item for item in proposal.argument_route_candidates}

    for route in proposal.argument_route_candidates:
        where = f"route {route.local_route_key}"
        conclusion = route.conclusion_ref.key()
        if conclusion not in approved_revisions:
            findings.append(f"{where}: conclusion revision {conclusion} is not approved in this scope")
        if (
            route.proposed_action == "match_existing"
            and route.target_argument_route_revision_id not in known_route_revisions
        ):
            findings.append(
                f"{where}: existing route revision "
                f"{route.target_argument_route_revision_id} was not in the packet"
            )
        elif route.proposed_action == "match_existing" and (
            known_route_conclusions.get(str(route.target_argument_route_revision_id))
            != conclusion
        ):
            findings.append(
                f"{where}: existing route revision belongs to another conclusion viewpoint"
            )
        for node in route.ordered_inference_nodes:
            if node.role == "conclusion" and node.conclusion_ref.key() != conclusion:
                findings.append(f"{where}: conclusion node reaches a different viewpoint")

    referenced_routes: set[str] = set()
    for attestation in proposal.source_route_attestations:
        where = f"attestation {attestation.local_attestation_key}"
        route_key = attestation.route_ref.key()
        referenced_routes.add(route_key)
        route = routes.get(route_key) if attestation.route_ref.local_route_key else None
        if not attestation.route_ref.local_route_key:
            findings.append(
                f"{where}: attestation must reference a proposal-local route key; "
                "existing identity is carried by the route candidate"
            )
        elif route is None:
            findings.append(f"{where}: names local route {route_key}, which was not proposed")

        source_ids = set()
        for claim_id in attestation.claim_ids:
            claim = claim_index.get(claim_id)
            if claim is None:
                findings.append(f"{where}: Claim {claim_id} is not in this batch")
                continue
            source_ids.add(claim.source_id)
        # The invariant is the source revision, not the Claims the attestation
        # happened to list. A step from a sibling Claim in the same sermon is
        # the professor's own reasoning; only another sermon is fabrication.
        allowed_steps = {
            item.evidence_step_id
            for claim in claims
            if claim.source_id == attestation.source_id
            for item in claim.evidence
        }
        allowed_fragments = {
            item.source_fragment_id
            for claim in claims
            if claim.source_id == attestation.source_id
            for item in claim.evidence
        }
        source_shas = {
            item.source_sha256
            for claim in claims
            if claim.source_id == attestation.source_id
            for item in claim.evidence
        }
        if attestation.source_revision_sha256 not in source_shas:
            findings.append(
                f"{where}: source revision {attestation.source_revision_sha256} "
                "does not match the pinned source evidence"
            )
        if len(source_ids) > 1:
            findings.append(
                f"{where}: Claims span {sorted(source_ids)}; an attestation is one source only"
            )
        elif source_ids and attestation.source_id not in source_ids:
            findings.append(f"{where}: declared source is not the source of its Claims")

        bound_steps: set[str] = set()
        for binding in attestation.step_bindings:
            if route is not None and binding.route_step_key not in {
                node.route_step_key for node in route.ordered_inference_nodes
            }:
                findings.append(f"{where}: step {binding.route_step_key} is not in the route")
            for step_id in binding.evidence_step_ids:
                if step_id not in allowed_steps:
                    findings.append(f"{where}: EvidenceStep {step_id} is outside this source")
            for fragment_id in binding.source_fragment_ids:
                if fragment_id not in allowed_fragments:
                    findings.append(f"{where}: SourceFragment {fragment_id} is outside this source")
            for component_key_value in binding.claim_component_keys:
                component = component_bindings.get(component_key_value)
                if component is None:
                    findings.append(
                        f"{where}: Claim component {component_key_value} is not in the route packet"
                    )
                    continue
                if component.source_id != attestation.source_id:
                    findings.append(
                        f"{where}: Claim component {component_key_value} is outside this source"
                    )
                if component.claim_id not in attestation.claim_ids:
                    findings.append(
                        f"{where}: Claim component {component_key_value} belongs to unlisted "
                        f"Claim {component.claim_id}"
                    )
            if binding.attestation_status == "attested":
                bound_steps.add(binding.route_step_key)

        if attestation.completeness == "full" and route is not None:
            required = {
                node.route_step_key
                for node in route.ordered_inference_nodes
                if node.required_for_full_attestation
            }
            for unmet in sorted(required - bound_steps):
                findings.append(
                    f"{where}: full requires an attested binding for required step {unmet}"
                )
            terminal = component_bindings.get(str(attestation.terminal_claim_component_key))
            if terminal is None:
                findings.append(
                    f"{where}: terminal Claim component "
                    f"{attestation.terminal_claim_component_key} is not in the route packet"
                )
            elif terminal.source_id != attestation.source_id:
                findings.append(f"{where}: terminal Claim component is outside this source")
            elif terminal.disposition != "member_existing":
                findings.append(f"{where}: terminal Claim component is not an approved member")
            elif route is not None and (
                terminal.target_viewpoint_revision_id != route.conclusion_ref.key()
            ):
                findings.append(
                    f"{where}: terminal Claim component belongs to another conclusion viewpoint"
                )
            elif terminal.claim_id not in attestation.claim_ids:
                findings.append(f"{where}: terminal Claim is not listed by the attestation")
            conclusion_key = next(
                node.route_step_key
                for node in route.ordered_inference_nodes
                if node.role == "conclusion"
            )
            conclusion_bindings = [
                binding
                for binding in attestation.step_bindings
                if binding.route_step_key == conclusion_key
                and binding.attestation_status == "attested"
            ]
            if not any(
                attestation.terminal_claim_component_key in binding.claim_component_keys
                for binding in conclusion_bindings
            ):
                findings.append(
                    f"{where}: conclusion binding does not contain the terminal Claim component"
                )

    for unused in sorted(set(routes) - referenced_routes):
        findings.append(f"route {unused}: proposed with no source attestation")

    return findings


def validate_proposal(
    *,
    proposal: CanonicalViewpointProposalResponse,
    batch_id: str,
    claims: Sequence[ReviewClaim],
    registry_revision_ids: Sequence[str],
) -> dict[str, Any]:
    """Fail closed on everything a program can decide, before the reviewer runs.

    Returns a validation report.  Raises :class:`BatchResolutionError` with
    every finding rather than the first, so one run surfaces the whole problem.
    """

    findings: list[str] = []
    if proposal.batch_id != batch_id:
        findings.append(f"proposal batch {proposal.batch_id} is not batch {batch_id}")

    claim_index = {item.claim_id: item for item in claims}
    expected_ids = sorted(claim_index)
    decided_ids = [item.claim_id for item in proposal.claim_decisions]
    for missing in sorted(set(expected_ids) - set(decided_ids)):
        findings.append(f"{missing}: Claim has no disposition")
    for extra in sorted(set(decided_ids) - set(expected_ids)):
        findings.append(f"{extra}: Claim is not in this batch")

    known_revisions = set(registry_revision_ids)
    candidate_keys = {item.local_key for item in proposal.new_viewpoint_candidates}
    referenced_keys: set[str] = set()
    seen_component_keys: dict[str, tuple[str, int]] = {}
    counts = {name: 0 for name in sorted(DISPOSITIONS)}

    for decision in proposal.claim_decisions:
        claim = claim_index.get(decision.claim_id)
        if claim is None:
            continue
        statement = claim.statement
        evidence_pairs = {
            (item.evidence_step_id, item.source_fragment_id) for item in claim.evidence
        }
        eligible_pairs = {
            (item.evidence_step_id, item.source_fragment_id)
            for item in claim.evidence
            if item.valid_for_identity_review
        }
        for index, component in enumerate(decision.components):
            where = f"{decision.claim_id}#{index}"
            counts[component.disposition] += 1
            for span in component.spans:
                if span.end_char > len(statement):
                    findings.append(f"{where}: span {span.start_char}-{span.end_char} runs past the statement")
                elif statement[span.start_char : span.end_char] != span.exact_text:
                    findings.append(f"{where}: span text does not match the pinned statement")
            if (
                component.target_viewpoint_revision_id
                and component.target_viewpoint_revision_id not in known_revisions
            ):
                findings.append(
                    f"{where}: target revision {component.target_viewpoint_revision_id} "
                    "was not in the packet"
                )
            if component.local_new_viewpoint_key:
                referenced_keys.add(component.local_new_viewpoint_key)
                if component.local_new_viewpoint_key not in candidate_keys:
                    findings.append(
                        f"{where}: local key {component.local_new_viewpoint_key} has no candidate"
                    )
            claim_steps = {pair[0] for pair in evidence_pairs}
            claim_fragments = {pair[1] for pair in evidence_pairs}
            for step_id in component.evidence_step_ids:
                if step_id not in claim_steps:
                    findings.append(f"{where}: EvidenceStep {step_id} does not belong to the Claim")
            for fragment_id in component.source_fragment_ids:
                if fragment_id not in claim_fragments:
                    findings.append(f"{where}: SourceFragment {fragment_id} does not belong to the Claim")
            # The two lists are independent sets, not positional pairs — one
            # EvidenceStep may bind several fragments. The real pairs are the
            # Claim's own, restricted to what this component referenced.
            referenced = {
                pair
                for pair in evidence_pairs
                if pair[0] in set(component.evidence_step_ids)
                and pair[1] in set(component.source_fragment_ids)
            }
            if component.disposition in EXISTING_DISPOSITIONS | {"new_viewpoint"}:
                if not referenced:
                    findings.append(f"{where}: referenced evidence forms no real (step, fragment) pair")
                elif not (referenced & eligible_pairs):
                    findings.append(f"{where}: no identity-eligible evidence pair")
            key = component_key(claim, component)
            if component.disposition in {"member_existing", "new_viewpoint"}:
                owner = seen_component_keys.get(key)
                if owner is not None:
                    findings.append(
                        f"{where}: component already claimed as a member by "
                        f"{owner[0]}#{owner[1]}"
                    )
                else:
                    seen_component_keys[key] = (decision.claim_id, index)

    for orphan in sorted(candidate_keys - referenced_keys):
        findings.append(f"{orphan}: new viewpoint candidate has no member component")

    if findings:
        raise BatchResolutionError(findings)

    report = {
        "schema_version": VALIDATION_VERSION,
        "batch_id": batch_id,
        "input_claim_count": len(expected_ids),
        "decided_claim_count": len(decided_ids),
        "component_count": sum(len(item.components) for item in proposal.claim_decisions),
        "disposition_counts": counts,
        "new_viewpoint_candidate_count": len(proposal.new_viewpoint_candidates),
        "member_component_keys": sorted(seen_component_keys),
        "checks_passed": [
            "exact_once_claim_coverage",
            "span_within_statement",
            "span_text_matches_pinned_statement",
            "target_revision_in_packet",
            "new_viewpoint_key_resolvable",
            "evidence_belongs_to_claim",
            "identity_evidence_eligible",
            "member_component_single_owner",
            "candidate_has_member_component",
        ],
    }
    report["artifact_sha256"] = sha256_json(report)
    return report


def validate_review(
    *,
    review: CanonicalViewpointReviewResponse,
    proposal: CanonicalViewpointProposalResponse,
    proposal_sha256: str,
) -> dict[str, Any]:
    """Require the reviewer to have answered every proposed change."""

    findings: list[str] = []
    if review.proposal_sha256 != proposal_sha256:
        findings.append("review is bound to a different proposal")

    expected = sorted(
        (decision.claim_id, index)
        for decision in proposal.claim_decisions
        for index in range(len(decision.components))
    )
    reviewed = sorted(
        (item.claim_id, item.component_index)
        for item in review.change_reviews
    )
    for missing in sorted(set(expected) - set(reviewed)):
        findings.append(f"{missing[0]}#{missing[1]}: no review decision")
    for extra in sorted(set(reviewed) - set(expected)):
        findings.append(f"{extra[0]}#{extra[1]}: review points at no proposed change")

    claim_ids = {item.claim_id for item in proposal.claim_decisions}
    for claim_id in review.novelty_review.missed_claim_ids:
        if claim_id not in claim_ids:
            findings.append(f"{claim_id}: novelty finding names a Claim outside the batch")

    if findings:
        raise BatchResolutionError(findings)

    outcome = review.outcome()
    report = {
        "schema_version": "wang_canonical_viewpoint_review_validation_v1",
        "proposal_sha256": proposal_sha256,
        "reviewed_change_count": len(reviewed),
        "outcome": outcome,
        "decision_counts": {
            name: sum(1 for item in review.change_reviews if item.decision == name)
            for name in ("pass", "correct", "reject", "defer")
        },
        "novelty_status": review.novelty_review.status,
        "reconsideration_required": outcome != "pass",
        "correction_required": any(
            item.decision == "correct" for item in review.change_reviews
        ),
    }
    report["artifact_sha256"] = sha256_json(report)
    return report


def build_batch_packet(
    *,
    batch_id: str,
    scope_label: str,
    claims: Sequence[ReviewClaim],
    registry_context: Sequence[Mapping[str, Any]],
    pending_candidates: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Compile the deterministic packet one proposer call receives."""

    claim_ids = [item.claim_id for item in claims]
    if claim_ids != sorted(set(claim_ids)):
        raise ValueError("batch packet claims must be id-sorted and unique")
    if not claims:
        raise ValueError("batch packet requires at least one Claim")
    packet = {
        "schema_version": BATCH_PACKET_VERSION,
        "batch_id": batch_id,
        "scope_label": scope_label,
        "registry_completeness_warning": (
            "现有 CanonicalViewpoints 是开放参考集，不是封闭 taxonomy。"
            "Registry 可能不完整；没有匹配项时必须提出 new_viewpoint，不得强行归类。"
        ),
        "discourse_role_note": (
            "EvidenceStep 的 discourse_role 是抽取层的 source-local 自由文本，"
            "各篇写法不一。它只作 provenance 参考，不是 route node role，"
            "也不得 slug 化后当作 inference method code 或 route identity。"
        ),
        "claims": [item.model_dump(mode="json") for item in claims],
        "registry_context": [dict(item) for item in registry_context],
        "pending_candidates": [dict(item) for item in pending_candidates],
    }
    packet["packet_sha256"] = sha256_json(packet)
    return packet


GROUPING_VERSION = "wang_canonical_viewpoint_claim_grouping_v1"


class ProposedClaimGroup(StrictBatchModel):
    group_key: str = Field(min_length=1)
    claim_ids: list[str] = Field(min_length=1)
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_group(self) -> "ProposedClaimGroup":
        if len(self.claim_ids) != len(set(self.claim_ids)):
            raise ValueError("group claim ids must be unique")
        return self


class ClaimGroupingResponse(StrictBatchModel):
    """Which Claims are worth comparing together — never which are the same.

    Grouping decides batch composition only.  If it were allowed to decide
    identity, a cheap unreviewed call would be making the judgment the whole
    proposal/review contract exists to govern.  Its errors are therefore
    recoverable by construction: a split pair falls back to the pending-candidate
    path, and a wrongly merged pair is simply two viewpoints proposed in one
    batch, which is the normal case.
    """

    schema_version: Literal["wang_canonical_viewpoint_claim_grouping_v1"] = GROUPING_VERSION
    scope_label: str
    groups: list[ProposedClaimGroup] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_grouping(self) -> "ClaimGroupingResponse":
        keys = [item.group_key for item in self.groups]
        if len(keys) != len(set(keys)):
            raise ValueError("groups must be unique")
        return self


RESIDUAL_GROUP_KEY = "zz_ungrouped_residual"


def repair_grouping(
    *, grouping: ClaimGroupingResponse, claim_ids: Sequence[str]
) -> tuple[ClaimGroupingResponse, list[str]]:
    """Force exact-once coverage deterministically, recording each repair.

    Grouping is a batching plan, not identity evidence, so an assignment slip
    must not cost the whole scope's call.  Every repair here is mechanical and
    order-independent: a Claim placed twice keeps its first group, a Claim left
    out joins a residual group, and an id that is not in scope is dropped.
    None of it decides what any Claim means.
    """

    repairs: list[str] = []
    expected = set(claim_ids)
    seen: set[str] = set()
    groups: list[ProposedClaimGroup] = []
    for group in sorted(grouping.groups, key=lambda item: item.group_key):
        kept: list[str] = []
        for claim_id in sorted(group.claim_ids):
            if claim_id not in expected:
                repairs.append(f"{claim_id}: dropped, not in scope (group {group.group_key})")
                continue
            if claim_id in seen:
                repairs.append(f"{claim_id}: dropped from {group.group_key}, already grouped")
                continue
            seen.add(claim_id)
            kept.append(claim_id)
        if kept:
            groups.append(group.model_copy(update={"claim_ids": kept}))

    missing = sorted(expected - seen)
    if missing:
        repairs.append(f"{len(missing)} ungrouped Claims placed in {RESIDUAL_GROUP_KEY}")
        groups.append(
            ProposedClaimGroup(
                group_key=RESIDUAL_GROUP_KEY,
                claim_ids=missing,
                rationale="程序补入：分组结果未覆盖这些 Claim，按残余批次处理。",
            )
        )
    return (
        grouping.model_copy(update={"groups": sorted(groups, key=lambda item: item.group_key)}),
        repairs,
    )


def validate_grouping(
    *,
    grouping: ClaimGroupingResponse,
    scope_label: str,
    claim_ids: Sequence[str],
) -> dict[str, Any]:
    """Require exact-once coverage; grouping may reorder work, never drop it."""

    findings: list[str] = []
    if grouping.scope_label != scope_label:
        findings.append(f"grouping is for scope {grouping.scope_label}, not {scope_label}")

    expected = set(claim_ids)
    seen: dict[str, str] = {}
    for group in grouping.groups:
        for claim_id in group.claim_ids:
            if claim_id not in expected:
                findings.append(f"{claim_id}: grouped Claim is not in this scope")
            owner = seen.get(claim_id)
            if owner is not None:
                findings.append(f"{claim_id}: in both group {owner} and {group.group_key}")
            else:
                seen[claim_id] = group.group_key
    for missing in sorted(expected - set(seen)):
        findings.append(f"{missing}: Claim was not assigned to any group")

    if findings:
        raise BatchResolutionError(findings)

    report = {
        "schema_version": "wang_canonical_viewpoint_grouping_validation_v1",
        "scope_label": scope_label,
        "claim_count": len(expected),
        "group_count": len(grouping.groups),
        "group_sizes": sorted(len(item.claim_ids) for item in grouping.groups),
        "checks_passed": ["exact_once_group_coverage", "no_foreign_claim"],
    }
    report["artifact_sha256"] = sha256_json(report)
    return report


def batches_from_groups(
    grouping: ClaimGroupingResponse, *, batch_size: int = DEFAULT_BATCH_SIZE
) -> list[list[str]]:
    """Order groups into batches, splitting any group past the size ceiling.

    A group larger than the ceiling is split in Claim-id order rather than
    resized by the model: the ceiling exists because of call latency, and
    letting a semantic call negotiate it would mix the two concerns.  The split
    parts stay adjacent, so the serial checkpoint carries the first part's
    candidates into the second.
    """

    if batch_size < 1:
        raise ValueError("batch size must be positive")
    ordered = sorted(grouping.groups, key=lambda item: (item.claim_ids[0], item.group_key))
    batches: list[list[str]] = []
    for group in ordered:
        claim_ids = group.claim_ids
        for index in range(0, len(claim_ids), batch_size):
            batches.append(claim_ids[index : index + batch_size])
    return batches


#: Fields whose order carries no meaning. The model emits them in narrative
#: order; rejecting that would throw away a ten-minute call over presentation.
_SET_FIELDS = (
    "approved_viewpoint_revision_ids",
    "claim_ids",
    "claim_component_keys",
    "evidence_step_ids",
    "finding_codes",
    "inference_method_codes",
    "missed_claim_ids",
    "source_fragment_ids",
    "scripture_scope",
    "conditions",
    "population_scope",
)


#: Object lists whose order carries no meaning, and the key they sort by.
_ORDERED_LISTS = {
    "claim_decisions": lambda item: str(item.get("claim_id", "")),
    "new_viewpoint_candidates": lambda item: str(item.get("local_key", "")),
    "spans": lambda item: (int(item.get("start_char", 0)), int(item.get("end_char", 0))),
    "change_reviews": lambda item: (
        str(item.get("target_kind") or "component"),
        str(item.get("target_key") or item.get("claim_id") or ""),
        int(item.get("component_index") or 0),
    ),
    "argument_route_candidates": lambda item: str(item.get("local_route_key", "")),
    "source_route_attestations": lambda item: str(item.get("local_attestation_key", "")),
    "viewpoints_with_no_route": lambda item: str(item.get("viewpoint_revision_id", "")),
    "groups": lambda item: str(item.get("group_key", "")),
}


def canonicalize_proposal(raw: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Sort and de-duplicate order-free list fields, recording what changed.

    Returns the canonical payload and a change log.  Only ordering and exact
    duplicates are touched: no id is added, removed or rewritten, so no
    reader-visible text and no truth condition can move.
    """

    changes: list[str] = []

    def walk(node: Any, path: str) -> Any:
        if isinstance(node, Mapping):
            result = {}
            for key, value in node.items():
                where = f"{path}/{key}"
                if key in _SET_FIELDS and isinstance(value, list):
                    canonical = sorted({str(item) for item in value})
                    if canonical != list(value):
                        changes.append(where)
                    result[key] = canonical
                elif key in _ORDERED_LISTS and isinstance(value, list):
                    walked = [walk(item, f"{where}/{index}") for index, item in enumerate(value)]
                    canonical = sorted(walked, key=_ORDERED_LISTS[key])
                    if canonical != walked:
                        changes.append(where)
                    result[key] = canonical
                else:
                    result[key] = walk(value, where)
            return result
        if isinstance(node, list):
            return [walk(item, f"{path}/{index}") for index, item in enumerate(node)]
        return node

    return walk(dict(raw), ""), sorted(changes)


def canonicalize_review(raw: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Canonicalize only order-free review lists before strict validation.

    The same structural normalizer is used for proposals and reviews, but this
    named entry point makes the semantic boundary explicit at each call site.
    Raw model output remains immutable beside the normalized envelope.
    """

    return canonicalize_proposal(raw)


def anchor_proposal_spans(
    raw: Mapping[str, Any], *, claim_statements: Mapping[str, str]
) -> tuple[dict[str, Any], list[str]]:
    """Recompute model-supplied offsets only when the exact quote is unique.

    Offsets are transport metadata, not a semantic model judgment. Models can
    count one Unicode code point incorrectly even when ``exact_text`` is
    byte-for-byte present in the pinned Claim. A unique exact match is safe to
    anchor deterministically; missing or ambiguous quotes fail closed.
    """

    payload = deepcopy(dict(raw))
    proposal = payload.get("revised_proposal", payload)
    findings: list[str] = []
    changes: list[str] = []
    prefix = "/revised_proposal" if proposal is not payload else ""
    for decision_index, decision in enumerate(proposal.get("claim_decisions") or []):
        claim_id = str(decision.get("claim_id") or "")
        statement = claim_statements.get(claim_id)
        if statement is None:
            continue
        for component_index, component in enumerate(decision.get("components") or []):
            for span_index, span in enumerate(component.get("spans") or []):
                exact_text = span.get("exact_text")
                start = span.get("start_char")
                end = span.get("end_char")
                if (
                    not isinstance(exact_text, str)
                    or not isinstance(start, int)
                    or not isinstance(end, int)
                ):
                    continue
                if end - start == len(exact_text) and statement[start:end] == exact_text:
                    continue
                occurrences: list[int] = []
                cursor = statement.find(exact_text)
                while cursor >= 0:
                    occurrences.append(cursor)
                    cursor = statement.find(exact_text, cursor + 1)
                where = f"{claim_id}#{component_index}/span-{span_index}"
                if len(occurrences) != 1:
                    findings.append(
                        f"{where}: exact_text has {len(occurrences)} matches in the pinned statement"
                    )
                    continue
                anchored_start = occurrences[0]
                span["start_char"] = anchored_start
                span["end_char"] = anchored_start + len(exact_text)
                changes.append(
                    f"{prefix}/claim_decisions/{decision_index}/components/"
                    f"{component_index}/spans/{span_index}/offsets"
                )
    if findings:
        raise BatchResolutionError(findings)
    return payload, sorted(changes)


RECONSIDERATION_VERSION = "wang_canonical_viewpoint_reconsideration_v1"


class FindingDisposition(StrictBatchModel):
    claim_id: str
    component_index: int = Field(ge=0)
    disposition: Literal["accepted", "rebutted", "deferred"]
    reason: str = Field(min_length=1)


class CanonicalViewpointReconsiderationResponse(StrictBatchModel):
    """The proposer's single answer to reviewer findings.

    It carries a whole revised proposal so the result stays one self-contained
    artifact, but only components the reviewer actually flagged may differ.
    Everything else must come back byte-identical.
    """

    schema_version: Literal["wang_canonical_viewpoint_reconsideration_v1"] = (
        RECONSIDERATION_VERSION
    )
    proposal_sha256: str
    review_sha256: str
    finding_dispositions: list[FindingDisposition] = Field(min_length=1)
    revised_proposal: CanonicalViewpointProposalResponse

    @model_validator(mode="after")
    def validate_reconsideration(self) -> "CanonicalViewpointReconsiderationResponse":
        keys = [(item.claim_id, item.component_index) for item in self.finding_dispositions]
        if len(keys) != len(set(keys)):
            raise ValueError("finding dispositions must be unique")
        return self


def validate_reconsideration(
    *,
    reconsideration: CanonicalViewpointReconsiderationResponse,
    proposal: CanonicalViewpointProposalResponse,
    review: CanonicalViewpointReviewResponse,
    proposal_sha256: str,
    review_sha256: str,
) -> dict[str, Any]:
    """Confine the revision to what the reviewer asked for.

    A reconsideration is not a second chance at the whole batch.  Anything the
    reviewer passed must come back unchanged, and any finding the proposer
    rebuts or defers goes to a human rather than being re-argued until the two
    models agree.
    """

    findings: list[str] = []
    if reconsideration.proposal_sha256 != proposal_sha256:
        findings.append("reconsideration is bound to a different proposal")
    if reconsideration.review_sha256 != review_sha256:
        findings.append("reconsideration is bound to a different review")

    flagged = {
        (item.claim_id, item.component_index)
        for item in review.change_reviews
        if item.decision == "correct"
    }
    answered = {
        (item.claim_id, item.component_index) for item in reconsideration.finding_dispositions
    }
    for missing in sorted(flagged - answered):
        findings.append(f"{missing[0]}#{missing[1]}: finding has no disposition")
    for extra in sorted(answered - flagged):
        findings.append(f"{extra[0]}#{extra[1]}: disposition answers no finding")

    before_by_claim = {
        decision.claim_id: [item.model_dump(mode="json") for item in decision.components]
        for decision in proposal.claim_decisions
    }
    after_by_claim = {
        decision.claim_id: [item.model_dump(mode="json") for item in decision.components]
        for decision in reconsideration.revised_proposal.claim_decisions
    }
    flagged_by_claim: dict[str, set[int]] = {}
    for claim_id, component_index in flagged:
        flagged_by_claim.setdefault(claim_id, set()).add(component_index)
    for claim_id, before_components in before_by_claim.items():
        after_components = after_by_claim.get(claim_id)
        if after_components is None:
            findings.append(f"{claim_id}: Claim decision disappeared during reconsideration")
            continue
        claim_flags = flagged_by_claim.get(claim_id, set())
        if not claim_flags:
            if before_components != after_components:
                findings.append(
                    f"{claim_id}: unflagged Claim decision changed during reconsideration"
                )
            continue
        # Corrected components may be merged, split, replaced or deleted when
        # the reviewer explicitly asks for it. Every component the reviewer
        # passed must still survive byte-identically somewhere in the revised
        # Claim; matching by original index would make an authorized merge
        # shift later components and falsely report that they changed.
        remaining = list(after_components)
        flagged_spans = {
            sha256_json(span)
            for index in claim_flags
            if index < len(before_components)
            for span in before_components[index].get("spans", [])
        }
        for index, component in enumerate(before_components):
            if index in claim_flags:
                continue
            try:
                matched = remaining.index(component)
            except ValueError:
                # A reviewer can flag one component and require its span to be
                # merged into a neighbouring component that otherwise passed.
                # Permit only that exact structural operation: every non-span
                # field remains byte-identical and every added span belonged to
                # a flagged component in the immutable original proposal.
                original_without_spans = {
                    key: value for key, value in component.items() if key != "spans"
                }
                original_spans = {
                    sha256_json(span)
                    for span in component.get("spans", [])
                }
                merge_match = None
                for candidate_index, candidate in enumerate(remaining):
                    candidate_without_spans = {
                        key: value for key, value in candidate.items() if key != "spans"
                    }
                    candidate_spans = {
                        sha256_json(span)
                        for span in candidate.get("spans", [])
                    }
                    added_spans = candidate_spans - original_spans
                    if (
                        candidate_without_spans == original_without_spans
                        and original_spans <= candidate_spans
                        and added_spans
                        and added_spans <= flagged_spans
                    ):
                        merge_match = candidate_index
                        break
                if merge_match is None:
                    findings.append(
                        f"{claim_id}#{index}: unflagged component changed or disappeared "
                        "during reconsideration"
                    )
                else:
                    remaining.pop(merge_match)
            else:
                remaining.pop(matched)
    for extra_claim_id in sorted(set(after_by_claim) - set(before_by_claim)):
        findings.append(
            f"{extra_claim_id}: Claim decision was added during reconsideration"
        )

    escalations = sorted(
        f"{item.claim_id}#{item.component_index}:{item.disposition}"
        for item in reconsideration.finding_dispositions
        if item.disposition != "accepted"
    )
    escalations.extend(
        sorted(
            f"{item.claim_id}#{item.component_index}:{item.decision}"
            for item in review.change_reviews
            if item.decision in {"reject", "defer"}
        )
    )
    accepted = {
        (item.claim_id, item.component_index)
        for item in reconsideration.finding_dispositions
        if item.disposition == "accepted"
    }
    revised_new_viewpoint_claim_ids = {
        decision.claim_id
        for decision in reconsideration.revised_proposal.claim_decisions
        if any(item.disposition == "new_viewpoint" for item in decision.components)
    }
    accepted_novelty_claim_ids = {
        claim_id
        for claim_id in review.novelty_review.missed_claim_ids
        if any(
            flagged_key in accepted
            for flagged_key in flagged
            if flagged_key[0] == claim_id
        )
    }
    for claim_id in sorted(
        accepted_novelty_claim_ids - revised_new_viewpoint_claim_ids
    ):
        findings.append(
            f"{claim_id}: accepted novelty correction produced no new_viewpoint"
        )
    resolved_novelty_claim_ids = sorted(
        accepted_novelty_claim_ids & revised_new_viewpoint_claim_ids
    )
    unresolved_novelty_claim_ids = sorted(
        set(review.novelty_review.missed_claim_ids) - set(resolved_novelty_claim_ids)
    )
    if unresolved_novelty_claim_ids:
        escalations.append(f"novelty:{review.novelty_review.status}")

    if findings:
        raise BatchResolutionError(findings)

    report = {
        "schema_version": "wang_canonical_viewpoint_reconsideration_validation_v1",
        "proposal_sha256": proposal_sha256,
        "review_sha256": review_sha256,
        "finding_count": len(flagged),
        "accepted_count": sum(
            1 for item in reconsideration.finding_dispositions if item.disposition == "accepted"
        ),
        "resolved_novelty_claim_ids": resolved_novelty_claim_ids,
        "unresolved_novelty_claim_ids": unresolved_novelty_claim_ids,
        "escalations": escalations,
        # Fail closed: a rebutted or deferred finding, or a novelty miss, is a
        # human judgment. The system never re-asks until the models agree.
        "outcome": "resolved" if not escalations else "exception",
    }
    report["artifact_sha256"] = sha256_json(report)
    return report


ROUTE_PROPOSAL_VERSION = "wang_argument_route_proposal_v1"
ROUTE_REVIEW_VERSION = "wang_argument_route_review_v1"
ROUTE_RECONSIDERATION_VERSION = "wang_argument_route_reconsideration_v1"


class NoRouteDisposition(StrictBatchModel):
    viewpoint_revision_id: str = Field(min_length=1)
    reason_code: Literal["no_attested_route", "evidence_insufficient", "deferred"]
    reason: str = Field(min_length=1)


class ArgumentRouteProposalResponse(StrictBatchModel):
    """Routes for the exact approved-CVP set of one scope."""

    schema_version: Literal["wang_argument_route_proposal_v1"] = ROUTE_PROPOSAL_VERSION
    scope_label: str
    approved_viewpoint_revision_ids: list[str] = Field(min_length=1)
    argument_route_candidates: list[ArgumentRouteCandidate] = Field(default_factory=list)
    source_route_attestations: list[SourceRouteAttestation] = Field(default_factory=list)
    viewpoints_with_no_route: list[NoRouteDisposition] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_response(self) -> "ArgumentRouteProposalResponse":
        if self.approved_viewpoint_revision_ids != sorted(
            set(self.approved_viewpoint_revision_ids)
        ):
            raise ValueError("approved viewpoint revision ids must be sorted and unique")
        route_keys = [item.local_route_key for item in self.argument_route_candidates]
        if len(route_keys) != len(set(route_keys)):
            raise ValueError("route candidates must be unique")
        attest_keys = [item.local_attestation_key for item in self.source_route_attestations]
        if len(attest_keys) != len(set(attest_keys)):
            raise ValueError("attestations must be unique")
        no_route = [item.viewpoint_revision_id for item in self.viewpoints_with_no_route]
        if len(no_route) != len(set(no_route)):
            raise ValueError("no-route dispositions must be unique")
        return self


class ReviewedRouteChange(StrictBatchModel):
    target_key: str = Field(min_length=1)
    target_kind: Literal["route", "attestation", "no_route"]
    decision: Literal["pass", "correct", "reject", "defer"]
    finding_codes: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)
    correction: str | None = None

    @model_validator(mode="after")
    def validate_change(self) -> "ReviewedRouteChange":
        if self.finding_codes != sorted(set(self.finding_codes)):
            raise ValueError("finding codes must be sorted and unique")
        if self.decision == "pass":
            if self.finding_codes or self.correction:
                raise ValueError("a passing route change carries no finding or correction")
        elif not self.finding_codes:
            raise ValueError(f"{self.decision} requires at least one finding code")
        if self.decision == "correct" and not self.correction:
            raise ValueError("correct requires route correction acceptance criteria")
        return self


class ArgumentRouteReviewResponse(StrictBatchModel):
    schema_version: Literal["wang_argument_route_review_v1"] = ROUTE_REVIEW_VERSION
    route_proposal_sha256: str
    route_evidence_packet_sha256: str
    change_reviews: list[ReviewedRouteChange] = Field(min_length=1)
    cross_source_composition_found: bool
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_response(self) -> "ArgumentRouteReviewResponse":
        keys = [(item.target_kind, item.target_key) for item in self.change_reviews]
        if len(keys) != len(set(keys)):
            raise ValueError("route review changes must be unique")
        if self.cross_source_composition_found and all(
            item.decision == "pass" for item in self.change_reviews
        ):
            raise ValueError("cross-source composition is never a passing review")
        return self


class RouteFindingDisposition(StrictBatchModel):
    target_key: str = Field(min_length=1)
    target_kind: Literal["route", "attestation", "no_route"]
    disposition: Literal["accepted", "rebutted", "deferred"]
    reason: str = Field(min_length=1)


class ArgumentRouteReconsiderationResponse(StrictBatchModel):
    schema_version: Literal["wang_argument_route_reconsideration_v1"] = (
        ROUTE_RECONSIDERATION_VERSION
    )
    route_proposal_sha256: str
    route_review_sha256: str
    finding_dispositions: list[RouteFindingDisposition] = Field(min_length=1)
    revised_proposal: ArgumentRouteProposalResponse

    @model_validator(mode="after")
    def validate_response(self) -> "ArgumentRouteReconsiderationResponse":
        keys = [(item.target_kind, item.target_key) for item in self.finding_dispositions]
        if len(keys) != len(set(keys)):
            raise ValueError("route finding dispositions must be unique")
        return self


def validate_route_proposal(
    *,
    routes: ArgumentRouteProposalResponse,
    scope_label: str,
    claims: Sequence[ReviewClaim],
    approved_viewpoint_revision_ids: Sequence[str],
    known_route_revision_ids: Sequence[str],
    known_route_conclusions: Mapping[str, str] | None = None,
    component_bindings: Sequence[RouteComponentBinding],
) -> dict[str, Any]:
    """Check routes against the scope's exact approved viewpoint set."""

    findings: list[str] = []
    if routes.scope_label != scope_label:
        findings.append(f"route proposal is for {routes.scope_label}, not {scope_label}")
    approved = sorted(set(approved_viewpoint_revision_ids))
    if routes.approved_viewpoint_revision_ids != approved:
        findings.append("route proposal approved viewpoint set differs from the scope cut")
    component_index = {item.claim_component_key: item for item in component_bindings}
    if len(component_index) != len(component_bindings):
        findings.append("route component bindings contain duplicate component keys")
    findings.extend(
        _route_findings(
            proposal=routes,
            claims=claims,
            approved_revisions=set(approved),
            known_route_revisions=set(known_route_revision_ids),
            known_route_conclusions=known_route_conclusions or {},
            component_bindings=component_index,
        )
    )
    routed = {item.conclusion_ref.key() for item in routes.argument_route_candidates}
    no_route = {item.viewpoint_revision_id for item in routes.viewpoints_with_no_route}
    for duplicate in sorted(routed & no_route):
        findings.append(f"{duplicate}: both routed and declared to have no route")
    for missing in sorted(set(approved) - routed - no_route):
        findings.append(f"{missing}: approved viewpoint has no route or no-route disposition")
    for extra in sorted((routed | no_route) - set(approved)):
        findings.append(f"{extra}: route coverage names a viewpoint outside the approved set")
    if findings:
        raise BatchResolutionError(findings)

    report = {
        "schema_version": "wang_argument_route_validation_v1",
        "scope_label": scope_label,
        "approved_viewpoint_count": len(approved),
        "route_count": len(routes.argument_route_candidates),
        "attestation_count": len(routes.source_route_attestations),
        "full_count": sum(
            1 for item in routes.source_route_attestations if item.completeness == "full"
        ),
        "partial_count": sum(
            1 for item in routes.source_route_attestations if item.completeness == "partial"
        ),
        "attested_sources": sorted({item.source_id for item in routes.source_route_attestations}),
        "inference_method_codes": sorted(
            {code for item in routes.argument_route_candidates for code in item.inference_method_codes}
        ),
        "checks_passed": [
            "approved_viewpoint_exact_coverage",
            "conclusion_is_approved_revision",
            "existing_route_revision_in_packet",
            "claim_component_key_recomputed",
            "attestation_is_single_source",
            "evidence_belongs_to_the_source",
            "full_requires_every_required_node",
            "full_terminal_is_conclusion_member",
            "no_route_without_an_attestation",
        ],
    }
    report["artifact_sha256"] = sha256_json(report)
    return report


def validate_route_review(
    *,
    review: ArgumentRouteReviewResponse,
    proposal: ArgumentRouteProposalResponse,
    route_proposal_sha256: str,
    route_evidence_packet_sha256: str,
) -> dict[str, Any]:
    """Require exact review coverage and bind it to both semantic inputs."""

    findings: list[str] = []
    if review.route_proposal_sha256 != route_proposal_sha256:
        findings.append("route review is bound to a different proposal")
    if review.route_evidence_packet_sha256 != route_evidence_packet_sha256:
        findings.append("route review is bound to a different evidence packet")
    expected = {
        ("route", item.local_route_key)
        for item in proposal.argument_route_candidates
    } | {
        ("attestation", item.local_attestation_key)
        for item in proposal.source_route_attestations
    } | {
        ("no_route", item.viewpoint_revision_id)
        for item in proposal.viewpoints_with_no_route
    }
    reviewed = {(item.target_kind, item.target_key) for item in review.change_reviews}
    for kind, key in sorted(expected - reviewed):
        findings.append(f"{kind}:{key}: no route review decision")
    for kind, key in sorted(reviewed - expected):
        findings.append(f"{kind}:{key}: review points at no proposed route change")
    if findings:
        raise BatchResolutionError(findings)
    counts = {
        name: sum(1 for item in review.change_reviews if item.decision == name)
        for name in ("pass", "correct", "reject", "defer")
    }
    report = {
        "schema_version": "wang_argument_route_review_validation_v1",
        "route_proposal_sha256": route_proposal_sha256,
        "route_evidence_packet_sha256": route_evidence_packet_sha256,
        "reviewed_change_count": len(reviewed),
        "decision_counts": counts,
        "cross_source_composition_found": review.cross_source_composition_found,
        "outcome": "pass" if counts == {"pass": len(reviewed), "correct": 0, "reject": 0, "defer": 0} else "findings",
        "reconsideration_required": any(
            item.decision == "correct" for item in review.change_reviews
        ),
        "exception_keys": sorted(
            f"{item.target_kind}:{item.target_key}"
            for item in review.change_reviews
            if item.decision in {"reject", "defer"}
        ),
    }
    report["artifact_sha256"] = sha256_json(report)
    return report


def _route_change_payloads(
    proposal: ArgumentRouteProposalResponse,
) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        **{
            ("route", item.local_route_key): item.model_dump(mode="json")
            for item in proposal.argument_route_candidates
        },
        **{
            ("attestation", item.local_attestation_key): item.model_dump(mode="json")
            for item in proposal.source_route_attestations
        },
        **{
            ("no_route", item.viewpoint_revision_id): item.model_dump(mode="json")
            for item in proposal.viewpoints_with_no_route
        },
    }


def validate_route_reconsideration(
    *,
    reconsideration: ArgumentRouteReconsiderationResponse,
    proposal: ArgumentRouteProposalResponse,
    review: ArgumentRouteReviewResponse,
    route_proposal_sha256: str,
    route_review_sha256: str,
) -> dict[str, Any]:
    """Confine the one Route correction to reviewer-flagged objects."""

    findings: list[str] = []
    if reconsideration.route_proposal_sha256 != route_proposal_sha256:
        findings.append("route reconsideration is bound to a different proposal")
    if reconsideration.route_review_sha256 != route_review_sha256:
        findings.append("route reconsideration is bound to a different review")
    if reconsideration.revised_proposal.scope_label != proposal.scope_label:
        findings.append("route reconsideration changed the scope")
    if (
        reconsideration.revised_proposal.approved_viewpoint_revision_ids
        != proposal.approved_viewpoint_revision_ids
    ):
        findings.append("route reconsideration changed the approved viewpoint set")

    flagged = {
        (item.target_kind, item.target_key)
        for item in review.change_reviews
        if item.decision == "correct"
    }
    answered = {
        (item.target_kind, item.target_key)
        for item in reconsideration.finding_dispositions
    }
    for kind, key in sorted(flagged - answered):
        findings.append(f"{kind}:{key}: route finding has no disposition")
    for kind, key in sorted(answered - flagged):
        findings.append(f"{kind}:{key}: disposition answers no correctable finding")

    before = _route_change_payloads(proposal)
    after = _route_change_payloads(reconsideration.revised_proposal)
    if set(before) != set(after):
        findings.append("route reconsideration changed proposal object keys")
    for key in sorted(set(before) & set(after)):
        if key not in flagged and before[key] != after[key]:
            findings.append(
                f"{key[0]}:{key[1]}: unflagged route object changed during reconsideration"
            )

    escalations = sorted(
        f"{item.target_kind}:{item.target_key}:{item.disposition}"
        for item in reconsideration.finding_dispositions
        if item.disposition != "accepted"
    )
    if findings:
        raise BatchResolutionError(findings)
    report = {
        "schema_version": "wang_argument_route_reconsideration_validation_v1",
        "route_proposal_sha256": route_proposal_sha256,
        "route_review_sha256": route_review_sha256,
        "finding_count": len(flagged),
        "accepted_count": sum(
            1
            for item in reconsideration.finding_dispositions
            if item.disposition == "accepted"
        ),
        "escalations": escalations,
        "outcome": "resolved" if not escalations else "exception",
    }
    report["artifact_sha256"] = sha256_json(report)
    return report
