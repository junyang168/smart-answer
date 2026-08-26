"""Batch CanonicalViewpoint resolution: proposal, review, deterministic gates.

One reviewed Claim batch goes to a single proposer call, deterministic code
checks every reference and span against the pinned Claims, an independent
reviewer judges the semantics, and only the survivors reach a ChangeSet.

The model never assigns a canonical id, an approval status, or a derived
count.  It emits character offsets and dispositions; this module does the
slicing, the coverage arithmetic and the fail-closed comparisons.
"""

from __future__ import annotations

from collections import Counter
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
ROUTE_VALIDATION_VERSION = "wang_argument_route_validation_v1"

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
ROUTE_TARGETED_DISPOSITIONS = EXISTING_DISPOSITIONS | {
    "extension_existing", "application_existing"
}


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
    evidence_scope_sha256: str = Field(min_length=1)
    route_policy_fingerprint_sha256: str = Field(min_length=1)
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
    scope_keys = {
        (
            item.scope_label,
            item.scope_manifest_sha256,
            item.evidence_scope_sha256,
            item.route_policy_fingerprint_sha256,
        )
        for item in jobs
    }
    if len(scope_keys) != 1:
        raise ValueError(
            "one route work unit cannot cross scope manifests, evidence scopes, or policies"
        )
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
    (
        scope_label,
        scope_manifest_sha256,
        evidence_scope_sha256,
        route_policy_sha256,
    ) = next(iter(scope_keys))
    body = {
        "schema_version": ROUTE_WORK_UNIT_VERSION,
        "scope_label": scope_label,
        "scope_manifest_sha256": scope_manifest_sha256,
        "evidence_scope_sha256": evidence_scope_sha256,
        "route_policy_fingerprint_sha256": route_policy_sha256,
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
            # A component can support, qualify or contest a viewpoint that this
            # same batch is proposing, so the target is either a committed
            # revision or a local candidate key -- exactly one of them. Without
            # the local form an argument for a new viewpoint has nowhere to
            # attach and has to become its own CVP.
            targets = [self.target_viewpoint_revision_id, self.local_new_viewpoint_key]
            if not any(targets):
                raise ValueError(
                    f"{self.disposition} requires a target viewpoint revision or local key"
                )
            if all(targets):
                raise ValueError(
                    f"{self.disposition} may not target both a revision and a local key"
                )
            if self.disposition == "member_existing" and self.local_new_viewpoint_key:
                raise ValueError(
                    "member_existing targets a committed revision; use new_viewpoint "
                    "with a shared local key to make components members of one new viewpoint"
                )
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


class ProposedViewpointRevision(StrictBatchModel):
    """New wording for a committed viewpoint this batch found too narrow.

    Without this the first batch to touch a topic fixes how it is carved
    forever: every later Claim can only squeeze into that wording or start a
    parallel viewpoint, so better evidence arriving second produces a duplicate
    rather than a correction.  A 2026-08-25 experiment put the choice to both
    models on a real case and 5 of 8 runs asked to revise; the pipeline offered
    no such option, so all of them had to create a duplicate instead.

    This revises the *wording* of one identity, never merges two.  Merging
    committed viewpoints moves Claim links between them and is a separate
    operation.
    """

    target_viewpoint_revision_id: str = Field(min_length=1)
    core_proposition: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    object: str
    polarity: Literal["affirmed", "denied"]
    modality: str = Field(min_length=1)
    scripture_scope: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    population_scope: list[str] = Field(default_factory=list)
    #: Why the committed wording cannot hold this batch's Claim, and why the
    #: new wording is still the same truth condition rather than a broader one
    #: that would swallow neighbouring viewpoints.
    revision_reason: str = Field(min_length=1)


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
    """One route as this scope proposes to leave it.

    ``revise_existing`` is the counterpart of the viewpoint layer's revision:
    the same route, its skeleton corrected. Without it the first scope to write
    a route fixes its steps forever, and a reviewer who finds a load-bearing
    node missing has only two illegal answers -- leave it (a conclusion half of
    whose scripture scope no node supports) or create a parallel route for the
    same conclusion (the false split the reviewer is trying to prevent).

    binding_loosing_meaning met it head on: two routes concluded in viewpoints
    covering 太16:19 and 太18:18 while every node stopped at 16:19. The review
    said so, named the bridge to add, and wrote that the action had to become a
    new revision of the existing route. The proposer wrote `create_new` pinned
    to the existing revision -- the nearest legal shape to what was asked -- and
    the schema rejected it.
    """

    local_route_key: str = Field(min_length=1)
    conclusion_ref: ConclusionRef
    proposed_action: Literal["match_existing", "revise_existing", "create_new", "defer"]
    target_argument_route_revision_id: str | None = None
    route_label: str = Field(min_length=1)
    inference_method_codes: list[str] = Field(min_length=1)
    inference_method_note: str | None = None
    ordered_inference_nodes: list[InferenceNode] = Field(min_length=2)
    identity_comparison: str = Field(min_length=1)
    #: Why the committed skeleton cannot carry this conclusion, and why the
    #: revised one is the same argument corrected rather than a different route.
    revision_reason: str | None = None

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
        if self.proposed_action in ("match_existing", "revise_existing"):
            if not self.target_argument_route_revision_id:
                raise ValueError(
                    f"{self.proposed_action} must pin the route revision it acts on"
                )
        elif self.target_argument_route_revision_id:
            raise ValueError(f"{self.proposed_action} may not pin an existing route revision")
        if self.proposed_action == "revise_existing" and not self.revision_reason:
            raise ValueError("revise_existing must say why the committed skeleton cannot hold")
        if self.proposed_action != "revise_existing" and self.revision_reason:
            raise ValueError("only revise_existing carries a revision reason")
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
        "extension_existing",
        "qualification_existing",
        "application_existing",
        "tension_existing",
        "no_registry_assertion",
        "deferred",
    ]
    target_viewpoint_revision_id: str | None = None
    viewpoint_claim_link_id: str | None = None
    occurrence_ref_ids: list[str] = Field(default_factory=list)
    statement_component: str = Field(min_length=1)
    spans: list[ProposedSpan] = Field(min_length=1)
    evidence_step_ids: list[str] = Field(default_factory=list)
    source_fragment_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_target(self) -> "RouteComponentBinding":
        if self.occurrence_ref_ids != sorted(set(self.occurrence_ref_ids)):
            raise ValueError("occurrence_ref_ids must be sorted and unique")
        if self.disposition in ROUTE_TARGETED_DISPOSITIONS:
            if not self.target_viewpoint_revision_id:
                raise ValueError(f"{self.disposition} requires a target viewpoint revision")
        elif self.target_viewpoint_revision_id:
            raise ValueError(f"{self.disposition} may not carry a viewpoint target")
        return self


class ProposedViewpointRelation(StrictBatchModel):
    """A typed edge between two viewpoints this batch knows about.

    Direction reads source-first, matching ``specializes``/``generalizes``:
    ``source applies target`` means the source viewpoint is an application of
    the target, not the other way round. Recording an application as a Claim
    link would invert it, because a Claim link says the Claim is evidence *for*
    the viewpoint.

    Each endpoint is either a revision id from the packet or a local candidate
    key from this proposal, never both.
    """

    source_viewpoint_revision_id: str | None = None
    source_local_key: str | None = None
    target_viewpoint_revision_id: str | None = None
    target_local_key: str | None = None
    relation_type: Literal["applies", "extends", "entails", "specializes", "generalizes"]
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_relation(self) -> "ProposedViewpointRelation":
        for side in ("source", "target"):
            revision = getattr(self, f"{side}_viewpoint_revision_id")
            local = getattr(self, f"{side}_local_key")
            if not revision and not local:
                raise ValueError(f"{side} endpoint requires a revision id or a local key")
            if revision and local:
                raise ValueError(f"{side} endpoint may not carry both a revision id and a local key")
        if self.endpoints()[0] == self.endpoints()[1]:
            raise ValueError("viewpoint relation endpoints must differ")
        return self

    def endpoints(self) -> tuple[tuple[str, str], tuple[str, str]]:
        def one(side: str) -> tuple[str, str]:
            revision = getattr(self, f"{side}_viewpoint_revision_id")
            return ("existing", revision) if revision else ("new", str(getattr(self, f"{side}_local_key")))

        return one("source"), one("target")


class ProposedStructureFocal(StrictBatchModel):
    """One viewpoint's role in the proposed centre."""

    viewpoint_revision_id: str | None = None
    local_key: str | None = None
    structure_role: Literal[
        "central_claim",
        "negative_boundary",
        "positive_identification",
        "supporting_conclusion",
        "qualification",
        "tension_side",
        "application",
        "methodological_boundary",
    ]

    @model_validator(mode="after")
    def validate_focal(self) -> "ProposedStructureFocal":
        if not self.viewpoint_revision_id and not self.local_key:
            raise ValueError("structure focal requires a revision id or a local key")
        if self.viewpoint_revision_id and self.local_key:
            raise ValueError("structure focal may not carry both a revision id and a local key")
        return self

    def endpoint(self) -> tuple[str, str]:
        if self.viewpoint_revision_id:
            return ("existing", self.viewpoint_revision_id)
        return ("new", str(self.local_key))


class ProposedViewpointStructure(StrictBatchModel):
    """The reviewed centre this scope's viewpoints add up to.

    It organises viewpoints the proposal already lists; it may not introduce a
    claim of its own, which is why ``central_synthesis`` is checkable only
    against the listed focal viewpoints.
    """

    central_synthesis: str = Field(min_length=1)
    focal: list[ProposedStructureFocal] = Field(min_length=1)
    unresolved_items: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_structure(self) -> "ProposedViewpointStructure":
        endpoints = [item.endpoint() for item in self.focal]
        if len(endpoints) != len(set(endpoints)):
            raise ValueError("a viewpoint may hold only one role in a structure")
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
    viewpoint_relations: list[ProposedViewpointRelation] = Field(default_factory=list)
    structures: list[ProposedViewpointStructure] = Field(default_factory=list)
    viewpoint_revisions: list[ProposedViewpointRevision] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_response(self) -> "CanonicalViewpointProposalResponse":
        claim_ids = [item.claim_id for item in self.claim_decisions]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("proposal claim decisions must be unique")
        keys = [item.local_key for item in self.new_viewpoint_candidates]
        if len(keys) != len(set(keys)):
            raise ValueError("new viewpoint candidates must be unique")
        targets = [item.target_viewpoint_revision_id for item in self.viewpoint_revisions]
        if len(targets) != len(set(targets)):
            raise ValueError("a viewpoint may be revised only once per batch")
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


class ReviewedViewpointRevision(StrictBatchModel):
    """One decision about rewriting one committed viewpoint.

    Kept out of ``change_reviews`` because it is not a decision about a Claim
    component: it changes wording that other batches already matched against,
    so it carries its own risk and its own coverage requirement.
    """

    target_viewpoint_revision_id: str = Field(min_length=1)
    decision: Literal["pass", "correct", "reject", "defer"]
    finding_codes: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)
    correction: str | None = None
    #: Every committed record pinned to the wording being replaced. The store
    #: refuses a revision that strands one, and re-pointing them without a
    #: reading would turn "checked against this wording" into bookkeeping, so a
    #: passing review has to name each one it confirmed still holds.
    confirmed_dependent_ids: list[str] = Field(default_factory=list)


class ReviewedStructure(StrictBatchModel):
    """One decision about the centre this batch says its viewpoints add up to.

    The review prompt has always asked whether `central_synthesis` is entailed
    by the listed focal viewpoints, and the schema had nowhere to record the
    answer -- so a reviewer that skipped the question passed anyway, and the
    rule was unenforceable. It is the one object downstream articles quote as
    what the professor holds.
    """

    structure_index: int = Field(ge=0)
    decision: Literal["pass", "correct", "reject", "defer"]
    finding_codes: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)
    correction: str | None = None
    #: Whether `central_synthesis` says only what the listed focal viewpoints
    #: entail. Asked separately because it is the question the prompt names and
    #: a free-text reason lets a reviewer answer around it.
    synthesis_entailed_by_focal: bool
    #: Material the sources leave open that the synthesis quietly resolved.
    unresolved_material_omitted: list[str] = Field(default_factory=list)


class ReviewedRelation(StrictBatchModel):
    """One decision about one typed edge, direction included.

    `source applies target` means the source is an application of the target.
    Nothing verified that, so an edge written backwards committed unchallenged.
    """

    source_ref: str = Field(min_length=1)
    target_ref: str = Field(min_length=1)
    relation_type: str = Field(min_length=1)
    decision: Literal["pass", "correct", "reject", "defer"]
    finding_codes: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)
    correction: str | None = None
    #: Whether the edge reads correctly source-first. Separated for the same
    #: reason as the synthesis question: it is the failure that has no other
    #: check, and prose can slide past it.
    direction_correct: bool

    def edge(self) -> tuple[str, str, str]:
        return (self.source_ref, self.target_ref, self.relation_type)


class CanonicalViewpointReviewResponse(StrictBatchModel):
    schema_version: Literal["wang_canonical_viewpoint_review_v1"] = REVIEW_VERSION
    proposal_sha256: str
    change_reviews: list[ReviewedChange] = Field(min_length=1)
    novelty_review: NoveltyReview
    revision_reviews: list[ReviewedViewpointRevision] = Field(default_factory=list)
    structure_reviews: list[ReviewedStructure] = Field(default_factory=list)
    relation_reviews: list[ReviewedRelation] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_review(self) -> "CanonicalViewpointReviewResponse":
        keys = [(item.claim_id, item.component_index) for item in self.change_reviews]
        if len(keys) != len(set(keys)):
            raise ValueError("change reviews must be unique")
        targets = [item.target_viewpoint_revision_id for item in self.revision_reviews]
        if len(targets) != len(set(targets)):
            raise ValueError("revision reviews must be unique")
        structures = [item.structure_index for item in self.structure_reviews]
        if len(structures) != len(set(structures)):
            raise ValueError("structure reviews must be unique")
        edges = [item.edge() for item in self.relation_reviews]
        if len(edges) != len(set(edges)):
            raise ValueError("relation reviews must be unique")
        return self

    def outcome(self) -> str:
        if self.novelty_review.status != "pass":
            return "findings"
        if any(item.decision != "pass" for item in self.change_reviews):
            return "findings"
        if any(item.decision != "pass" for item in self.revision_reviews):
            return "findings"
        if any(item.decision != "pass" for item in self.structure_reviews):
            return "findings"
        if any(item.decision != "pass" for item in self.relation_reviews):
            return "findings"
        # A structure whose synthesis is not entailed, or an edge written
        # backwards, is a finding even when the reviewer typed `pass`: the two
        # questions exist because prose slides past them.
        if any(not item.synthesis_entailed_by_focal for item in self.structure_reviews):
            return "findings"
        if any(not item.direction_correct for item in self.relation_reviews):
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

    return component_key_from_spans(
        claim_id=claim.claim_id,
        claim_revision_sha256=claim.claim_revision_sha256,
        canonical_spans=component.canonical_spans(),
    )


def component_key_from_spans(
    *,
    claim_id: str,
    claim_revision_sha256: str,
    canonical_spans: Sequence[Mapping[str, Any] | Sequence[Any]],
) -> str:
    """Transport key shared by proposal and Registry-backed Route packets."""

    normalized = []
    for item in canonical_spans:
        if isinstance(item, Mapping):
            normalized.append(
                [int(item["start_char"]), int(item["end_char"]), str(item["exact_text"])]
            )
        else:
            normalized.append([int(item[0]), int(item[1]), str(item[2])])

    return "CCK-" + sha256_json(
        {
            "claim_id": claim_id,
            "claim_revision_sha256": claim_revision_sha256,
            "canonical_spans": normalized,
        }
    )


def _sibling_hint(
    component_bindings: Mapping[str, "RouteComponentBinding"],
    *,
    claim_id: str,
    wanted_revision: str | None,
) -> str:
    """Name the components on this Claim that the rejected one could have been.

    One Claim is carved into several components, and neighbouring ones can read
    almost alike while only one carries the Registry link. Saying only that the
    chosen component has no link reads as though the Registry were missing it,
    and sends the reader off to check the store -- where the link is, on the
    component next door. Give the usable keys instead.
    """

    linked = sorted(
        (key, item.target_viewpoint_revision_id)
        for key, item in component_bindings.items()
        if item.claim_id == claim_id
        and item.disposition in ROUTE_TARGETED_DISPOSITIONS - {"tension_existing"}
    )
    usable = [
        (key, revision)
        for key, revision in linked
        if wanted_revision is None or revision == wanted_revision
    ]
    if usable:
        rendered = ", ".join(f"{key} -> {revision}" for key, revision in usable)
        return f"; on this Claim: {rendered}"
    if linked:
        # Saying "none carries a link" would be false and would send the reader
        # to the Registry again; the links exist, they reach elsewhere.
        rendered = ", ".join(f"{key} -> {revision}" for key, revision in linked)
        return (
            f"; Claim {claim_id} links only to other viewpoints: {rendered}"
        )
    return f"; no component of Claim {claim_id} carries one"


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
            route.proposed_action in ("match_existing", "revise_existing")
            and route.target_argument_route_revision_id not in known_route_revisions
        ):
            findings.append(
                f"{where}: existing route revision "
                f"{route.target_argument_route_revision_id} was not in the packet"
            )
        elif route.proposed_action in ("match_existing", "revise_existing") and (
            known_route_conclusions.get(str(route.target_argument_route_revision_id))
            != conclusion
        ):
            findings.append(
                # A revision corrects one route's steps. Moving it to a
                # different conclusion is not a correction, it is a different
                # route wearing the old one's id.
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
            # Same-source is not close enough: a fragment has to belong to one
            # of the EvidenceSteps this binding names, which is what the runtime
            # projection requires. Checking only the source here let a binding
            # that quoted a neighbouring step's fragment pass deterministic
            # validation and die at the store, where the finding arrives as a
            # failed write instead of something the review round can correct.
            selected = set(binding.source_fragment_ids)
            union: set[str] = set()
            for step_id in binding.evidence_step_ids:
                fragments_of_step = {
                    pair.source_fragment_id
                    for claim in claims
                    if claim.source_id == attestation.source_id
                    for pair in claim.evidence
                    if pair.evidence_step_id == step_id
                }
                union |= fragments_of_step
                if fragments_of_step and not selected & fragments_of_step:
                    findings.append(
                        f"{where}: step {binding.route_step_key} names EvidenceStep "
                        f"{step_id} but binds none of its SourceFragments"
                    )
            if union and not selected <= union:
                findings.append(
                    f"{where}: step {binding.route_step_key} binds SourceFragments "
                    "outside the EvidenceSteps it names"
                )
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
            elif terminal.disposition not in ROUTE_TARGETED_DISPOSITIONS - {
                "tension_existing"
            }:
                findings.append(
                    f"{where}: terminal Claim component has no positive Registry link"
                    + _sibling_hint(
                        component_bindings,
                        claim_id=terminal.claim_id,
                        wanted_revision=route.conclusion_ref.key() if route else None,
                    )
                )
            elif route is not None and (
                terminal.target_viewpoint_revision_id != route.conclusion_ref.key()
            ):
                findings.append(
                    f"{where}: terminal Claim component belongs to another conclusion viewpoint"
                    + _sibling_hint(
                        component_bindings,
                        claim_id=terminal.claim_id,
                        wanted_revision=route.conclusion_ref.key(),
                    )
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

    # Revising committed wording is the one operation that reaches outside this
    # batch, so it is confined twice: to viewpoints the packet actually offered,
    # and to viewpoints some component of this batch attaches to. A batch may
    # not rewrite a viewpoint it merely read about.
    attached_revision_ids = {
        str(component.target_viewpoint_revision_id)
        for decision in proposal.claim_decisions
        for component in decision.components
        if component.disposition in EXISTING_DISPOSITIONS
        and component.target_viewpoint_revision_id
    }
    for index, revised in enumerate(proposal.viewpoint_revisions):
        where = f"viewpoint_revisions#{index}"
        if revised.target_viewpoint_revision_id not in known_revisions:
            findings.append(
                f"{where}: revision {revised.target_viewpoint_revision_id} was not in the packet"
            )
        elif revised.target_viewpoint_revision_id not in attached_revision_ids:
            findings.append(
                f"{where}: {revised.target_viewpoint_revision_id} is not attached to by any "
                "component in this batch"
            )

    for index, relation in enumerate(proposal.viewpoint_relations):
        where = f"viewpoint_relations#{index}"
        for kind, key in relation.endpoints():
            if kind == "new":
                if key not in candidate_keys:
                    findings.append(f"{where}: local key {key} has no candidate")
            elif key not in known_revisions:
                findings.append(f"{where}: revision {key} was not in the packet")

    seen_edges: set[Any] = set()
    for index, relation in enumerate(proposal.viewpoint_relations):
        edge = (*relation.endpoints(), relation.relation_type)
        if edge in seen_edges:
            findings.append(f"viewpoint_relations#{index}: duplicate {relation.relation_type} edge")
        seen_edges.add(edge)

    for index, structure in enumerate(proposal.structures):
        where = f"structures#{index}"
        for focal in structure.focal:
            kind, key = focal.endpoint()
            if kind == "new":
                if key not in candidate_keys:
                    findings.append(f"{where}: local key {key} has no candidate")
            elif key not in known_revisions:
                findings.append(f"{where}: revision {key} was not in the packet")

    # A relation or structure endpoint alone does not justify a candidate: every
    # new viewpoint still needs at least one Claim component of its own.
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
        "viewpoint_revision_count": len(proposal.viewpoint_revisions),
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
            "revision_target_attached_in_batch",
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

    proposed_revisions = {
        item.target_viewpoint_revision_id for item in proposal.viewpoint_revisions
    }
    reviewed_revisions = {
        item.target_viewpoint_revision_id for item in review.revision_reviews
    }
    for missing in sorted(proposed_revisions - reviewed_revisions):
        findings.append(f"{missing}: proposed viewpoint revision has no review decision")
    for extra in sorted(reviewed_revisions - proposed_revisions):
        findings.append(f"{extra}: review points at no proposed viewpoint revision")

    # Exact-once, the same as components. A prompt asking for a judgment the
    # schema does not require is not a rule -- the reviewer that skipped the
    # structure question passed anyway, which is how 4 structures and 12
    # relations reached the Registry `system_approved` and unread.
    proposed_structures = set(range(len(proposal.structures)))
    reviewed_structures = {item.structure_index for item in review.structure_reviews}
    for missing in sorted(proposed_structures - reviewed_structures):
        findings.append(f"structures#{missing}: proposed structure has no review decision")
    for extra in sorted(reviewed_structures - proposed_structures):
        findings.append(f"structures#{extra}: review points at no proposed structure")

    proposed_edges = {
        (
            str(item.source_viewpoint_revision_id or item.source_local_key),
            str(item.target_viewpoint_revision_id or item.target_local_key),
            item.relation_type,
        )
        for item in proposal.viewpoint_relations
    }
    reviewed_edges = {item.edge() for item in review.relation_reviews}
    for missing in sorted(proposed_edges - reviewed_edges):
        findings.append(
            f"relation {missing[0]} --{missing[2]}--> {missing[1]}: no review decision"
        )
    for extra in sorted(reviewed_edges - proposed_edges):
        findings.append(
            f"relation {extra[0]} --{extra[2]}--> {extra[1]}: review points at no proposed relation"
        )

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
        "reviewed_revision_count": len(reviewed_revisions),
        "reviewed_structure_count": len(reviewed_structures),
        "reviewed_relation_count": len(reviewed_edges),
        "structure_decision_counts": {
            name: sum(1 for item in review.structure_reviews if item.decision == name)
            for name in ("pass", "correct", "reject", "defer")
        },
        "relation_decision_counts": {
            name: sum(1 for item in review.relation_reviews if item.decision == name)
            for name in ("pass", "correct", "reject", "defer")
        },
        "revision_decision_counts": {
            name: sum(1 for item in review.revision_reviews if item.decision == name)
            for name in ("pass", "correct", "reject", "defer")
        },
        "reconsideration_required": outcome != "pass",
        # Every kind of finding that can reach `outcome` has to reach this too.
        # Left off the structure and relation reviews, a relation-only rejection
        # made the batch `findings` while `correction_required` stayed False --
        # so no correction round ran, and the scope stopped with no way forward.
        "correction_required": any(
            item.decision == "correct"
            for item in (
                *review.change_reviews,
                *review.revision_reviews,
                *review.structure_reviews,
                *review.relation_reviews,
            )
        )
        or any(not item.synthesis_entailed_by_focal for item in review.structure_reviews)
        or any(not item.direction_correct for item in review.relation_reviews),
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


GROUP_COVERAGE_VERSION = "wang_canonical_viewpoint_group_coverage_v1"


def group_coverage_report(
    *,
    grouping: ClaimGroupingResponse,
    linked_claim_ids: Sequence[str],
    blocked_claims: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Measure Registry coverage against the grouping plan, not against a batch.

    A batch reports the Claims it was handed, so a batch that ran on part of a
    group still reports success, and the group looks finished.  That is how
    ``rock_referent`` came to hold 13 links for 14 planned Claims with nothing
    saying so: the run's "13 Claims" came from the batch, and no check ever put
    the plan on the other side of the comparison.

    The plan is the denominator, active claim links are the numerator, and a
    group is only ``covered`` when every Claim it plans for has one.  Nothing
    here decides what a Claim means -- an ``uncovered`` group may well end in a
    reasoned decision that some Claim carries no viewpoint, but that decision
    has to be made rather than skipped into.
    """

    linked = set(linked_claim_ids)
    groups: list[dict[str, Any]] = []
    for group in sorted(grouping.groups, key=lambda item: item.group_key):
        unlinked = sorted(set(group.claim_ids) - linked)
        covered = len(group.claim_ids) - len(unlinked)
        if not unlinked:
            status = "covered"
        elif covered:
            status = "partial"
        else:
            status = "uncovered"
        groups.append(
            {
                "group_key": group.group_key,
                "claim_count": len(group.claim_ids),
                "linked_claim_count": covered,
                "unlinked_claim_ids": unlinked,
                "status": status,
            }
        )

    planned = {claim_id for group in grouping.groups for claim_id in group.claim_ids}
    report = {
        "schema_version": GROUP_COVERAGE_VERSION,
        "scope_label": grouping.scope_label,
        "group_count": len(groups),
        "planned_claim_count": len(planned),
        "linked_claim_count": len(planned & linked),
        "covered_group_count": sum(1 for item in groups if item["status"] == "covered"),
        "partial_group_count": sum(1 for item in groups if item["status"] == "partial"),
        "groups": groups,
        # A link to a Claim outside the plan is not this report's business to
        # fix, but leaving it unnamed would let the two sides drift apart
        # silently -- the plan is the scope's, and both are rebuilt from it.
        "linked_claims_outside_plan": sorted(linked - planned),
        # Claims the packet stopped before grouping ever saw them: no evidence
        # bindings, an ineligible source, no reviewed candidate. They can never
        # reach a batch, so a denominator of planned Claims alone reports full
        # coverage while they sit unaccounted for -- which is the shape of the
        # gap this whole report exists to close, one stage earlier.
        "blocked_claims": sorted(
            (
                {
                    "claim_id": str(item["claim_id"]),
                    "reason_code": str(item.get("reason_code") or "unspecified"),
                }
                for item in blocked_claims
            ),
            key=lambda item: item["claim_id"],
        ),
        "blocked_claim_counts": dict(
            sorted(
                Counter(
                    str(item.get("reason_code") or "unspecified")
                    for item in blocked_claims
                ).items()
            )
        ),
        "scope_claim_count": len(planned) + len(blocked_claims),
    }
    report["artifact_sha256"] = sha256_json(report)
    return report


#: Fields whose order carries no meaning. The model emits them in narrative
#: order; rejecting that would throw away a ten-minute call over presentation.
_SET_FIELDS = (
    "approved_viewpoint_revision_ids",
    "claim_ids",
    "claim_component_keys",
    "evidence_step_ids",
    "evidence_claim_component_keys",
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
    "cvp_re_review_exceptions": lambda item: (
        str(item.get("viewpoint_revision_id") or ""),
        str(item.get("finding_code") or ""),
        str(item.get("triggering_target_kind") or ""),
        str(item.get("triggering_target_key") or ""),
    ),
    "argument_route_candidates": lambda item: str(item.get("local_route_key", "")),
    "source_route_attestations": lambda item: str(item.get("local_attestation_key", "")),
    "viewpoints_with_no_route": lambda item: str(item.get("viewpoint_revision_id", "")),
    "groups": lambda item: str(item.get("group_key", "")),
    "component_patches": lambda item: (
        str(item.get("claim_id", "")),
        int(item.get("component_index") or 0),
    ),
    "candidate_patches": lambda item: str(item.get("local_key", "")),
    "finding_dispositions": lambda item: (
        str(item.get("claim_id", "")),
        int(item.get("component_index") or 0),
    ),
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
    # Patch-only reconsideration does not carry Claim decisions. Anchor only
    # replacement payloads; merge operations reuse already-validated original
    # spans and therefore need no model-supplied offsets.
    for patch_index, patch in enumerate(payload.get("component_patches") or []):
        claim_id = str(patch.get("claim_id") or "")
        statement = claim_statements.get(claim_id)
        if statement is None:
            continue
        for component_index, component in enumerate(
            patch.get("replacement_components") or []
        ):
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
                where = f"{claim_id}#patch-{patch_index}/component-{component_index}/span-{span_index}"
                if len(occurrences) != 1:
                    findings.append(
                        f"{where}: exact_text has {len(occurrences)} matches in the pinned statement"
                    )
                    continue
                anchored_start = occurrences[0]
                span["start_char"] = anchored_start
                span["end_char"] = anchored_start + len(exact_text)
                changes.append(
                    f"/component_patches/{patch_index}/replacement_components/"
                    f"{component_index}/spans/{span_index}/offsets"
                )
    if findings:
        raise BatchResolutionError(findings)
    return payload, sorted(changes)


RECONSIDERATION_VERSION = "wang_canonical_viewpoint_reconsideration_v3"


class FindingDisposition(StrictBatchModel):
    claim_id: str
    component_index: int = Field(ge=0)
    disposition: Literal["accepted", "rebutted", "deferred"]
    reason: str = Field(min_length=1)


class StructureFindingDisposition(StrictBatchModel):
    """The proposer's answer to one finding about a proposed structure."""

    structure_index: int = Field(ge=0)
    disposition: Literal["accepted", "rebutted", "deferred"]
    reason: str = Field(min_length=1)


class RelationFindingDisposition(StrictBatchModel):
    """The proposer's answer to one finding about a proposed relation."""

    source_ref: str = Field(min_length=1)
    target_ref: str = Field(min_length=1)
    relation_type: str = Field(min_length=1)
    disposition: Literal["accepted", "rebutted", "deferred"]
    reason: str = Field(min_length=1)

    def edge(self) -> tuple[str, str, str]:
        return (self.source_ref, self.target_ref, self.relation_type)


class RevisionFindingDisposition(StrictBatchModel):
    """The proposer's answer to one finding about a proposed viewpoint revision."""

    target_viewpoint_revision_id: str = Field(min_length=1)
    disposition: Literal["accepted", "rebutted", "deferred"]
    reason: str = Field(min_length=1)


class ViewpointRevisionCorrectionPatch(StrictBatchModel):
    """Rewrite or withdraw one reviewer-flagged viewpoint revision.

    Withdrawing leaves the committed wording untouched and the batch still
    resolves; that is the correct answer when the reviewer shows the proposed
    wording would swallow a neighbouring viewpoint.
    """

    target_viewpoint_revision_id: str = Field(min_length=1)
    action: Literal["upsert", "withdraw"]
    revision: ProposedViewpointRevision | None = None

    @model_validator(mode="after")
    def validate_operation(self) -> "ViewpointRevisionCorrectionPatch":
        if self.action == "upsert":
            if (
                self.revision is None
                or self.revision.target_viewpoint_revision_id
                != self.target_viewpoint_revision_id
            ):
                raise ValueError("revision upsert must carry the same target revision id")
        elif self.revision is not None:
            raise ValueError("revision withdraw does not carry a revision payload")
        return self


class ComponentCorrectionPatch(StrictBatchModel):
    """One correction to one reviewer-flagged component.

    A replacement may contain zero components (delete), one component
    (replace), or several components (split).  ``merge_into_component_index``
    is the narrow structural exception used when the reviewer asks for the
    flagged component's exact spans to be attached to an otherwise unchanged
    sibling.  The deterministic merger performs that attachment; the model
    never re-emits or edits the sibling.
    """

    claim_id: str
    component_index: int = Field(ge=0)
    replacement_components: list[ProposedComponent] | None = None
    merge_into_component_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_operation(self) -> "ComponentCorrectionPatch":
        if (self.replacement_components is None) == (
            self.merge_into_component_index is None
        ):
            raise ValueError(
                "a component patch supplies exactly one of replacement_components "
                "or merge_into_component_index"
            )
        if self.merge_into_component_index == self.component_index:
            raise ValueError("a component cannot merge into itself")
        return self


class CandidateCorrectionPatch(StrictBatchModel):
    """Upsert or delete a candidate reachable from a flagged component."""

    local_key: str = Field(min_length=1)
    action: Literal["upsert", "delete"]
    candidate: NewViewpointCandidate | None = None

    @model_validator(mode="after")
    def validate_operation(self) -> "CandidateCorrectionPatch":
        if self.action == "upsert":
            if self.candidate is None or self.candidate.local_key != self.local_key:
                raise ValueError("candidate upsert must carry the same local_key")
        elif self.candidate is not None:
            raise ValueError("candidate delete does not carry a candidate payload")
        return self


class RelationCorrectionPatch(StrictBatchModel):
    """Upsert or delete one typed edge reachable from a flagged component.

    A reviewer who accepts a new viewpoint but calls its boundary against a
    neighbouring one unclear asks for that boundary to be recorded as a
    relation.  Without this patch the proposer can only rebut the finding —
    the correction is unsatisfiable, and one unsatisfiable finding fails the
    whole batch.

    The edge carries no id of its own, so it is keyed the way
    :func:`validate_proposal` already keys it: both endpoints plus the type.
    """

    action: Literal["upsert", "delete"]
    relation: ProposedViewpointRelation

    def edge(self) -> tuple[Any, ...]:
        return (*self.relation.endpoints(), self.relation.relation_type)


class StructureCorrectionPatch(StrictBatchModel):
    """Replace or drop one proposed structure reachable from a flagged component.

    Deleting a candidate strands every focal that named it, and a structure
    whose focal list no longer resolves fails validation with nothing the
    proposer can do about it.  The whole structure is re-emitted rather than
    the one focal removed, because ``central_synthesis`` has to be entailed by
    the focal viewpoints that remain — dropping a focal silently would leave
    the synthesis asserting a viewpoint the batch no longer holds.
    """

    structure_index: int = Field(ge=0)
    action: Literal["upsert", "delete"]
    structure: ProposedViewpointStructure | None = None

    @model_validator(mode="after")
    def validate_operation(self) -> "StructureCorrectionPatch":
        if self.action == "upsert" and self.structure is None:
            raise ValueError("structure upsert must carry a structure")
        if self.action == "delete" and self.structure is not None:
            raise ValueError("structure delete does not carry a structure payload")
        return self


class CanonicalViewpointReconsiderationResponse(StrictBatchModel):
    """The proposer's single, patch-only answer to reviewer findings."""

    schema_version: Literal["wang_canonical_viewpoint_reconsideration_v3"] = (
        RECONSIDERATION_VERSION
    )
    proposal_sha256: str
    review_sha256: str
    # Not min_length=1: a review whose only finding is on a proposed revision
    # has no component to dispose of, and requiring one would make that
    # correction impossible to answer.
    finding_dispositions: list[FindingDisposition] = Field(default_factory=list)
    component_patches: list[ComponentCorrectionPatch] = Field(default_factory=list)
    candidate_patches: list[CandidateCorrectionPatch] = Field(default_factory=list)
    relation_patches: list[RelationCorrectionPatch] = Field(default_factory=list)
    structure_patches: list[StructureCorrectionPatch] = Field(default_factory=list)
    revision_dispositions: list[RevisionFindingDisposition] = Field(default_factory=list)
    revision_patches: list[ViewpointRevisionCorrectionPatch] = Field(default_factory=list)
    structure_dispositions: list[StructureFindingDisposition] = Field(default_factory=list)
    relation_dispositions: list[RelationFindingDisposition] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_reconsideration(self) -> "CanonicalViewpointReconsiderationResponse":
        if not any(
            (
                self.finding_dispositions,
                self.revision_dispositions,
                self.structure_dispositions,
                self.relation_dispositions,
            )
        ):
            raise ValueError("a reconsideration must answer at least one finding")
        keys = [(item.claim_id, item.component_index) for item in self.finding_dispositions]
        if len(keys) != len(set(keys)):
            raise ValueError("finding dispositions must be unique")
        patch_keys = [(item.claim_id, item.component_index) for item in self.component_patches]
        if len(patch_keys) != len(set(patch_keys)):
            raise ValueError("component patches must be unique")
        candidate_keys = [item.local_key for item in self.candidate_patches]
        if len(candidate_keys) != len(set(candidate_keys)):
            raise ValueError("candidate patches must be unique")
        relation_keys = [item.edge() for item in self.relation_patches]
        if len(relation_keys) != len(set(relation_keys)):
            raise ValueError("relation patches must be unique")
        structure_keys = [item.structure_index for item in self.structure_patches]
        if len(structure_keys) != len(set(structure_keys)):
            raise ValueError("structure patches must be unique")
        revision_keys = [
            item.target_viewpoint_revision_id for item in self.revision_dispositions
        ]
        if len(revision_keys) != len(set(revision_keys)):
            raise ValueError("revision dispositions must be unique")
        revision_patch_keys = [
            item.target_viewpoint_revision_id for item in self.revision_patches
        ]
        if len(revision_patch_keys) != len(set(revision_patch_keys)):
            raise ValueError("revision patches must be unique")
        structure_keys = [item.structure_index for item in self.structure_dispositions]
        if len(structure_keys) != len(set(structure_keys)):
            raise ValueError("structure dispositions must be unique")
        relation_keys = [item.edge() for item in self.relation_dispositions]
        if len(relation_keys) != len(set(relation_keys)):
            raise ValueError("relation dispositions must be unique")
        return self


def _candidate_patch_strands_referrers(patch: "CandidatePatch") -> bool:
    """Whether a candidate patch leaves its other components pointing at nothing.

    Only deletion does. Restricting *which fields* a patch may touch when some
    other component refers to the candidate looked prudent and was wrong twice
    over. A candidate with one member component and one support is the ordinary
    shape, so almost every correction has an unflagged referrer by construction;
    and the reviewer read every component of the batch in one pass before
    writing the correction, so the components that were passed were passed in
    the knowledge of it. Two real findings died on that restriction in one day:
    a signature triple inverting a necessary condition into a sufficient one,
    and a core_proposition the reviewer required be narrowed because it had lost
    its source's qualifier ("不得保留無限定的絕對否定措辭").

    What still authorises a patch is reachability from an accepted finding, and
    unflagged components are still copied verbatim by construction.
    """

    return patch.action == "delete"


def apply_reconsideration_patches(
    *,
    reconsideration: CanonicalViewpointReconsiderationResponse,
    proposal: CanonicalViewpointProposalResponse,
    review: CanonicalViewpointReviewResponse,
) -> CanonicalViewpointProposalResponse:
    """Apply only reviewer-authorized patches to the immutable proposal.

    This function, not the model, copies every unflagged Claim decision and
    candidate.  Consequently collateral edits are impossible by construction
    rather than merely detected after an expensive full-proposal rewrite.
    """

    findings: list[str] = []
    flagged = {
        (item.claim_id, item.component_index)
        for item in review.change_reviews
        if item.decision == "correct"
    }
    accepted = {
        (item.claim_id, item.component_index)
        for item in reconsideration.finding_dispositions
        if item.disposition == "accepted"
    }
    authorized_candidate_referrers = set(accepted)
    patches = {
        (item.claim_id, item.component_index): item
        for item in reconsideration.component_patches
    }
    for missing in sorted(accepted - set(patches)):
        findings.append(f"{missing[0]}#{missing[1]}: accepted finding has no patch")
    for extra in sorted(set(patches) - accepted):
        findings.append(f"{extra[0]}#{extra[1]}: patch is not an accepted finding")
    if not set(patches) <= flagged:
        findings.append("component patches must be confined to reviewer findings")

    payload = proposal.model_dump(mode="json")
    decisions = {item["claim_id"]: item for item in payload["claim_decisions"]}
    affected_candidate_keys: set[str] = set()
    candidate_referrers: dict[str, set[tuple[str, int]]] = {}
    for decision in proposal.claim_decisions:
        for index, component in enumerate(decision.components):
            if component.local_new_viewpoint_key:
                candidate_referrers.setdefault(
                    component.local_new_viewpoint_key, set()
                ).add((decision.claim_id, index))
    affected_revision_ids: set[str] = set()
    for claim_id, component_index in sorted(accepted):
        decision = decisions.get(claim_id)
        if decision is None or component_index >= len(decision["components"]):
            findings.append(f"{claim_id}#{component_index}: patch target does not exist")
            continue
        original = decision["components"][component_index]
        if original.get("local_new_viewpoint_key"):
            affected_candidate_keys.add(str(original["local_new_viewpoint_key"]))
        if original.get("target_viewpoint_revision_id"):
            affected_revision_ids.add(str(original["target_viewpoint_revision_id"]))

    patches_by_claim: dict[str, dict[int, ComponentCorrectionPatch]] = {}
    for key, patch in patches.items():
        patches_by_claim.setdefault(key[0], {})[key[1]] = patch

    for claim_id, claim_patches in patches_by_claim.items():
        decision = decisions.get(claim_id)
        if decision is None:
            continue
        original_components = decision["components"]
        replacements: dict[int, list[dict[str, Any]]] = {}
        merge_spans: dict[int, list[dict[str, Any]]] = {}
        for component_index, patch in claim_patches.items():
            if component_index >= len(original_components):
                continue
            if patch.replacement_components is not None:
                replacements[component_index] = [
                    item.model_dump(mode="json")
                    for item in patch.replacement_components
                ]
                for item in patch.replacement_components:
                    if item.local_new_viewpoint_key:
                        affected_candidate_keys.add(item.local_new_viewpoint_key)
                    if item.target_viewpoint_revision_id:
                        affected_revision_ids.add(item.target_viewpoint_revision_id)
                continue
            target = patch.merge_into_component_index
            if target is None or target >= len(original_components):
                findings.append(
                    f"{claim_id}#{component_index}: merge target does not exist"
                )
                continue
            if target in claim_patches:
                findings.append(
                    f"{claim_id}#{component_index}: merge target must be an unpatched sibling"
                )
                continue
            authorized_candidate_referrers.add((claim_id, target))
            target_candidate_key = original_components[target].get(
                "local_new_viewpoint_key"
            )
            if target_candidate_key:
                affected_candidate_keys.add(str(target_candidate_key))
            replacements[component_index] = []
            merge_spans.setdefault(target, []).extend(
                deepcopy(original_components[component_index]["spans"])
            )

        revised_components: list[dict[str, Any]] = []
        for index, original in enumerate(original_components):
            if index in replacements:
                revised_components.extend(replacements[index])
                continue
            copied = deepcopy(original)
            if index in merge_spans:
                copied["spans"] = sorted(
                    [*copied["spans"], *merge_spans[index]],
                    key=lambda item: (item["start_char"], item["end_char"]),
                )
            revised_components.append(copied)
        decision["components"] = revised_components

    candidates = {
        item["local_key"]: item for item in payload["new_viewpoint_candidates"]
    }
    for patch in reconsideration.candidate_patches:
        if patch.local_key not in affected_candidate_keys:
            findings.append(
                f"{patch.local_key}: candidate patch is not reachable from an accepted finding"
            )
            continue
        unflagged_referrers = (
            candidate_referrers.get(patch.local_key, set())
            - authorized_candidate_referrers
        )
        if unflagged_referrers and _candidate_patch_strands_referrers(patch):
            rendered = ", ".join(
                f"{claim_id}#{component_index}"
                for claim_id, component_index in sorted(unflagged_referrers)
            )
            findings.append(
                f"{patch.local_key}: candidate patch would alter unflagged referrers {rendered}"
            )
            continue
        if patch.action == "delete":
            candidates.pop(patch.local_key, None)
        else:
            assert patch.candidate is not None
            candidates[patch.local_key] = patch.candidate.model_dump(mode="json")
    payload["new_viewpoint_candidates"] = [
        candidates[key] for key in sorted(candidates)
    ]

    # A relation is authorized by the finding at one of its ends: the reviewer
    # asking for a boundary names the flagged viewpoint on one side and the
    # neighbour it is confusable with on the other, and the neighbour is not
    # itself under review.  Requiring both ends would refuse exactly the edge
    # the finding exists to obtain.
    relations = {
        (*relation.endpoints(), relation.relation_type): item
        for relation, item in zip(
            proposal.viewpoint_relations, payload["viewpoint_relations"]
        )
    }
    for patch in reconsideration.relation_patches:
        endpoints = patch.relation.endpoints()
        reachable = any(
            key in affected_candidate_keys if kind == "new" else key in affected_revision_ids
            for kind, key in endpoints
        )
        if not reachable:
            findings.append(
                f"{patch.relation.relation_type} {endpoints[0][1]}->{endpoints[1][1]}: "
                "relation patch is not reachable from an accepted finding"
            )
            continue
        if patch.action == "delete":
            relations.pop(patch.edge(), None)
        else:
            relations[patch.edge()] = patch.relation.model_dump(mode="json")
    payload["viewpoint_relations"] = list(relations.values())

    # Same reachability rule, read off the structure's own focal list: the
    # structure a finding strands is the one that named the viewpoint under
    # review.  Patches are resolved against the original indices and the list
    # is rebuilt once, so a delete cannot shift a later patch's target.
    # A structure the reviewer flagged directly authorises its own patch. The
    # rule below reaches a structure through the candidates a component finding
    # disturbed, which covers the structure stranded by someone else's
    # correction but not the commonest case of all: the reviewer says the
    # synthesis or a role is wrong, names the fix, the proposer accepts, and the
    # patch is refused as unauthorised because authorisation only understood
    # component findings.
    accepted_structures = {
        item.structure_index
        for item in reconsideration.structure_dispositions
        if item.disposition == "accepted"
    }
    structure_replacements: dict[int, dict[str, Any] | None] = {}
    for patch in reconsideration.structure_patches:
        if patch.structure_index >= len(proposal.structures):
            findings.append(
                f"structures#{patch.structure_index}: patch target does not exist"
            )
            continue
        original = proposal.structures[patch.structure_index]
        reachable = patch.structure_index in accepted_structures or any(
            key in affected_candidate_keys if kind == "new" else key in affected_revision_ids
            for kind, key in (focal.endpoint() for focal in original.focal)
        )
        if not reachable:
            findings.append(
                f"structures#{patch.structure_index}: "
                "structure patch is not reachable from an accepted finding"
            )
            continue
        structure_replacements[patch.structure_index] = (
            None if patch.action == "delete" else patch.structure.model_dump(mode="json")
        )
    payload["structures"] = [
        structure_replacements.get(index, item)
        for index, item in enumerate(payload["structures"])
        if structure_replacements.get(index, item) is not None
    ]

    # `reject` had no legal answer. Only `correct` counted as a finding, so a
    # disposition answering a rejection was refused as answering nothing --
    # while leaving it unanswered kept the revision in the effective proposal,
    # where the approval gate refused it in turn. The proposer's only move,
    # withdrawing, was unreachable from either side.
    flagged_revisions = {
        item.target_viewpoint_revision_id
        for item in review.revision_reviews
        if item.decision != "pass"
    }
    accepted_revisions = {
        item.target_viewpoint_revision_id
        for item in reconsideration.revision_dispositions
        if item.disposition == "accepted"
    }
    revision_patches = {
        item.target_viewpoint_revision_id: item
        for item in reconsideration.revision_patches
    }
    for missing in sorted(accepted_revisions - set(revision_patches)):
        findings.append(f"{missing}: accepted revision finding has no patch")
    for extra in sorted(set(revision_patches) - accepted_revisions):
        findings.append(f"{extra}: revision patch is not an accepted finding")
    if not set(revision_patches) <= flagged_revisions:
        findings.append("revision patches must be confined to reviewer findings")

    revised_by_target = {
        item["target_viewpoint_revision_id"]: item
        for item in payload["viewpoint_revisions"]
    }
    for target, patch in revision_patches.items():
        if target not in revised_by_target:
            findings.append(f"{target}: patch target is not a proposed revision")
            continue
        if patch.action == "withdraw":
            revised_by_target.pop(target)
        else:
            assert patch.revision is not None
            revised_by_target[target] = patch.revision.model_dump(mode="json")
    payload["viewpoint_revisions"] = list(revised_by_target.values())

    if findings:
        raise BatchResolutionError(findings)
    try:
        return CanonicalViewpointProposalResponse.model_validate(payload)
    except ValueError as exc:
        raise BatchResolutionError([f"patch application produced invalid proposal: {exc}"]) from exc


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

    flagged_revisions = {
        item.target_viewpoint_revision_id
        for item in review.revision_reviews
        if item.decision != "pass"
    }
    answered_revisions = {
        item.target_viewpoint_revision_id
        for item in reconsideration.revision_dispositions
    }
    for missing in sorted(flagged_revisions - answered_revisions):
        findings.append(f"{missing}: revision finding has no disposition")
    for extra in sorted(answered_revisions - flagged_revisions):
        findings.append(f"{extra}: revision disposition answers no finding")

    # A structure or relation the reviewer would not pass has to be answered
    # too. Without this its rejection reached neither `escalations` nor the
    # correction round, and the batch resolved as though the reviewer had
    # agreed -- writing the record `system_approved` against a review that
    # said no.
    flagged_structures = {
        item.structure_index
        for item in review.structure_reviews
        if item.decision != "pass" or not item.synthesis_entailed_by_focal
    }
    answered_structures = {item.structure_index for item in reconsideration.structure_dispositions}
    for missing in sorted(flagged_structures - answered_structures):
        findings.append(f"structures#{missing}: finding has no disposition")
    for extra in sorted(answered_structures - flagged_structures):
        findings.append(f"structures#{extra}: disposition answers no finding")

    flagged_relations = {
        item.edge()
        for item in review.relation_reviews
        if item.decision != "pass" or not item.direction_correct
    }
    answered_relations = {item.edge() for item in reconsideration.relation_dispositions}
    for missing in sorted(flagged_relations - answered_relations):
        findings.append(
            f"relation {missing[0]} --{missing[2]}--> {missing[1]}: finding has no disposition"
        )
    for extra in sorted(answered_relations - flagged_relations):
        findings.append(
            f"relation {extra[0]} --{extra[2]}--> {extra[1]}: disposition answers no finding"
        )

    if findings:
        raise BatchResolutionError(findings)

    effective_proposal = apply_reconsideration_patches(
        reconsideration=reconsideration,
        proposal=proposal,
        review=review,
    )

    escalations = sorted(
        [
            *(
                f"structures#{item.structure_index}:{item.disposition}"
                for item in reconsideration.structure_dispositions
                if item.disposition != "accepted"
            ),
            *(
                f"relation {item.source_ref}--{item.relation_type}->{item.target_ref}"
                f":{item.disposition}"
                for item in reconsideration.relation_dispositions
                if item.disposition != "accepted"
            ),
            *(
                f"{item.target_viewpoint_revision_id}:{item.disposition}"
                for item in reconsideration.revision_dispositions
                if item.disposition != "accepted"
            ),
            *(
                f"{item.claim_id}#{item.component_index}:{item.disposition}"
                for item in reconsideration.finding_dispositions
                if item.disposition != "accepted"
            ),
        ]
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
        for decision in effective_proposal.claim_decisions
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
        "effective_proposal_sha256": sha256_json(
            effective_proposal.model_dump(mode="json")
        ),
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


class CvpReReviewException(StrictBatchModel):
    """Route review evidence that questions an approved CVP identity."""

    viewpoint_revision_id: str = Field(min_length=1)
    finding_code: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    triggering_target_kind: Literal["route", "attestation", "no_route"]
    triggering_target_key: str = Field(min_length=1)
    evidence_claim_component_keys: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_exception(self) -> "CvpReReviewException":
        if self.evidence_claim_component_keys != sorted(
            set(self.evidence_claim_component_keys)
        ):
            raise ValueError("CVP re-review evidence keys must be sorted and unique")
        return self


class ArgumentRouteReviewResponse(StrictBatchModel):
    schema_version: Literal["wang_argument_route_review_v1"] = ROUTE_REVIEW_VERSION
    route_proposal_sha256: str
    route_evidence_packet_sha256: str
    change_reviews: list[ReviewedRouteChange] = Field(min_length=1)
    cvp_re_review_exceptions: list[CvpReReviewException] = Field(default_factory=list)
    cross_source_composition_found: bool
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_response(self) -> "ArgumentRouteReviewResponse":
        keys = [(item.target_kind, item.target_key) for item in self.change_reviews]
        if len(keys) != len(set(keys)):
            raise ValueError("route review changes must be unique")
        exception_keys = [
            (
                item.viewpoint_revision_id,
                item.finding_code,
                item.triggering_target_kind,
                item.triggering_target_key,
            )
            for item in self.cvp_re_review_exceptions
        ]
        if len(exception_keys) != len(set(exception_keys)):
            raise ValueError("CVP re-review exceptions must be unique")
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
        "schema_version": ROUTE_VALIDATION_VERSION,
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
            "full_terminal_has_positive_conclusion_link",
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
    expected_targets: set[tuple[str, str]] | None = None,
    allowed_claim_component_keys: set[str] | None = None,
) -> dict[str, Any]:
    """Require exact review coverage and bind it to both semantic inputs."""

    findings: list[str] = []
    if review.route_proposal_sha256 != route_proposal_sha256:
        findings.append("route review is bound to a different proposal")
    if review.route_evidence_packet_sha256 != route_evidence_packet_sha256:
        findings.append("route review is bound to a different evidence packet")
    full_expected = {
        ("route", item.local_route_key)
        for item in proposal.argument_route_candidates
    } | {
        ("attestation", item.local_attestation_key)
        for item in proposal.source_route_attestations
    } | {
        ("no_route", item.viewpoint_revision_id)
        for item in proposal.viewpoints_with_no_route
    }
    expected = expected_targets if expected_targets is not None else full_expected
    if not expected <= full_expected:
        findings.append("route review batch contains target outside the proposal")
    reviewed = {(item.target_kind, item.target_key) for item in review.change_reviews}
    for kind, key in sorted(expected - reviewed):
        findings.append(f"{kind}:{key}: no route review decision")
    for kind, key in sorted(reviewed - expected):
        findings.append(f"{kind}:{key}: review points at no proposed route change")
    approved_revisions = set(proposal.approved_viewpoint_revision_ids)
    for item in review.cvp_re_review_exceptions:
        if item.viewpoint_revision_id not in approved_revisions:
            findings.append(
                f"CVP re-review names unapproved revision {item.viewpoint_revision_id}"
            )
        if (item.triggering_target_kind, item.triggering_target_key) not in reviewed:
            findings.append(
                "CVP re-review trigger is not a decision target in this review: "
                f"{item.triggering_target_kind}:{item.triggering_target_key}"
            )
        if allowed_claim_component_keys is not None:
            unknown = sorted(
                set(item.evidence_claim_component_keys) - allowed_claim_component_keys
            )
            if unknown:
                findings.append(
                    "CVP re-review cites unknown Claim components: " + ", ".join(unknown)
                )
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


# --- identity consolidation ----------------------------------------------------

CONSOLIDATION_VERSION = "wang_canonical_viewpoint_identity_consolidation_v1"


class ConsolidationVerdict(StrictBatchModel):
    """One ruling on whether a proposed viewpoint is already in the Registry."""

    local_key: str = Field(min_length=1)
    verdict: Literal["new", "matches_existing", "matches_but_wording_too_narrow"]
    target_viewpoint_revision_id: str | None = None
    revised_core_proposition: str | None = None
    revised_subject: str | None = None
    revised_predicate: str | None = None
    revised_object: str | None = None
    revised_polarity: Literal["affirmed", "denied"] | None = None
    revised_modality: str | None = None
    revised_scripture_scope: list[str] = Field(default_factory=list)
    revised_conditions: list[str] = Field(default_factory=list)
    revised_population_scope: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_verdict(self) -> "ConsolidationVerdict":
        if self.verdict == "new":
            if self.target_viewpoint_revision_id:
                raise ValueError("a new viewpoint has no Registry target")
            return self
        if not self.target_viewpoint_revision_id:
            raise ValueError(f"{self.verdict} requires a target viewpoint revision")
        if self.verdict == "matches_but_wording_too_narrow":
            required = (
                self.revised_core_proposition,
                self.revised_subject,
                self.revised_predicate,
                self.revised_polarity,
                self.revised_modality,
            )
            if not all(required) or self.revised_object is None:
                raise ValueError(
                    "a wording-too-narrow verdict must carry the full revised proposition"
                )
        elif self.revised_core_proposition:
            raise ValueError("matches_existing keeps the committed wording")
        return self


class IdentityConsolidationResponse(StrictBatchModel):
    """The identity-only pass over one batch's proposed viewpoints.

    Split out of the proposal because the proposer decides identity while also
    carving components, assigning roles, writing relations and building a
    structure -- and on 2026-08-25 it created a duplicate of a viewpoint it had
    explicitly compared against, in all three runs. Asked on its own, with the
    same information, Opus got the same case right in three of three.
    """

    schema_version: Literal[
        "wang_canonical_viewpoint_identity_consolidation_v1"
    ] = CONSOLIDATION_VERSION
    verdicts: list[ConsolidationVerdict] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_response(self) -> "IdentityConsolidationResponse":
        keys = [item.local_key for item in self.verdicts]
        if len(keys) != len(set(keys)):
            raise ValueError("consolidation verdicts must be unique")
        return self


def validate_consolidation(
    *,
    consolidation: IdentityConsolidationResponse,
    proposal: CanonicalViewpointProposalResponse,
    registry_revision_ids: Sequence[str],
) -> dict[str, Any]:
    """Require a ruling on every candidate, and only on candidates."""

    findings: list[str] = []
    candidates = {item.local_key for item in proposal.new_viewpoint_candidates}
    ruled = {item.local_key for item in consolidation.verdicts}
    for missing in sorted(candidates - ruled):
        findings.append(f"{missing}: candidate has no consolidation verdict")
    for extra in sorted(ruled - candidates):
        findings.append(f"{extra}: verdict names no candidate in this proposal")

    known = set(registry_revision_ids)
    merged_targets: dict[str, str] = {}
    for verdict in consolidation.verdicts:
        if verdict.verdict == "new":
            continue
        target = str(verdict.target_viewpoint_revision_id)
        if target not in known:
            findings.append(f"{verdict.local_key}: revision {target} was not in the packet")
            continue
        # Two candidates collapsing onto one viewpoint would need their
        # components merged and, for a revision, one wording chosen between
        # them. Neither has been observed; fail closed rather than guess.
        owner = merged_targets.get(target)
        if owner is not None:
            findings.append(
                f"{verdict.local_key}: {target} is already claimed by {owner}"
            )
        else:
            merged_targets[target] = verdict.local_key

    if findings:
        raise BatchResolutionError(findings)

    counts = Counter(item.verdict for item in consolidation.verdicts)
    report = {
        "schema_version": "wang_canonical_viewpoint_consolidation_validation_v1",
        "candidate_count": len(candidates),
        "verdict_counts": {
            name: counts.get(name, 0)
            for name in ("new", "matches_existing", "matches_but_wording_too_narrow")
        },
        "merged_local_keys": sorted(merged_targets.values()),
        "checks_passed": [
            "exact_once_candidate_coverage",
            "merge_target_in_packet",
            "merge_target_single_owner",
        ],
    }
    report["artifact_sha256"] = sha256_json(report)
    return report


def apply_consolidation(
    *,
    consolidation: IdentityConsolidationResponse,
    proposal: CanonicalViewpointProposalResponse,
) -> CanonicalViewpointProposalResponse:
    """Fold merged candidates into the Registry viewpoints they duplicate.

    Deterministic: the model rules on identity, this function rewrites the
    proposal.  A merged candidate's components retarget to the committed
    revision, the candidate disappears, and a wording-too-narrow verdict
    becomes the ``viewpoint_revisions`` entry the reviewer then has to pass.
    """

    merges = {
        item.local_key: item
        for item in consolidation.verdicts
        if item.verdict != "new"
    }
    if not merges:
        return proposal

    findings: list[str] = []
    payload = proposal.model_dump(mode="json")

    for decision in payload["claim_decisions"]:
        for component in decision["components"]:
            key = component.get("local_new_viewpoint_key")
            verdict = merges.get(str(key)) if key else None
            if verdict is None:
                continue
            component["local_new_viewpoint_key"] = None
            component["target_viewpoint_revision_id"] = verdict.target_viewpoint_revision_id
            if component["disposition"] == "new_viewpoint":
                component["disposition"] = "member_existing"

    payload["new_viewpoint_candidates"] = [
        item for item in payload["new_viewpoint_candidates"]
        if item["local_key"] not in merges
    ]

    for relation in payload["viewpoint_relations"]:
        for side in ("source", "target"):
            key = relation.get(f"{side}_local_key")
            verdict = merges.get(str(key)) if key else None
            if verdict is None:
                continue
            relation[f"{side}_local_key"] = None
            relation[f"{side}_viewpoint_revision_id"] = verdict.target_viewpoint_revision_id
    # A relation whose two ends just became the same viewpoint says nothing.
    payload["viewpoint_relations"] = [
        item for item in payload["viewpoint_relations"]
        if (item.get("source_local_key"), item.get("source_viewpoint_revision_id"))
        != (item.get("target_local_key"), item.get("target_viewpoint_revision_id"))
    ]

    for index, structure in enumerate(payload["structures"]):
        # A proposal can name one viewpoint twice without noticing: once by
        # citing the committed revision it can see, and once as a candidate it
        # believes is new. Consolidation catches that the two are one, and the
        # structure is then holding it in two roles.
        directly_cited = {
            str(item.get("viewpoint_revision_id"))
            for item in structure["focal"]
            if item.get("viewpoint_revision_id")
        }
        surviving_focal: list[dict[str, Any]] = []
        for focal in structure["focal"]:
            key = focal.get("local_key")
            verdict = merges.get(str(key)) if key else None
            if verdict is None:
                surviving_focal.append(focal)
                continue
            target = str(verdict.target_viewpoint_revision_id)
            if target in directly_cited:
                # The direct citation is the deliberate placement: it was made
                # against the committed viewpoint itself. The merged focal's
                # role was assigned to a viewpoint that turned out not to exist,
                # so it has no claim on a role here.
                continue
            focal["local_key"] = None
            focal["viewpoint_revision_id"] = target
            surviving_focal.append(focal)
        structure["focal"] = surviving_focal
        seen = [
            (item.get("viewpoint_revision_id"), item.get("local_key"))
            for item in surviving_focal
        ]
        if len(seen) != len(set(seen)):
            # Two merged focals colliding leaves no basis to prefer either role.
            findings.append(
                f"structures#{index}: consolidation gave one viewpoint two focal roles"
            )

    revisions = {
        item["target_viewpoint_revision_id"]: item
        for item in payload["viewpoint_revisions"]
    }
    for verdict in merges.values():
        if verdict.verdict != "matches_but_wording_too_narrow":
            continue
        target = str(verdict.target_viewpoint_revision_id)
        if target in revisions:
            findings.append(f"{target}: proposal already revises this viewpoint")
            continue
        revisions[target] = {
            "target_viewpoint_revision_id": target,
            "core_proposition": verdict.revised_core_proposition,
            "subject": verdict.revised_subject,
            "predicate": verdict.revised_predicate,
            "object": verdict.revised_object,
            "polarity": verdict.revised_polarity,
            "modality": verdict.revised_modality,
            "scripture_scope": list(verdict.revised_scripture_scope),
            "conditions": list(verdict.revised_conditions),
            "population_scope": list(verdict.revised_population_scope),
            "revision_reason": verdict.reason,
        }
    payload["viewpoint_revisions"] = list(revisions.values())

    if findings:
        raise BatchResolutionError(findings)
    try:
        return CanonicalViewpointProposalResponse.model_validate(payload)
    except ValueError as exc:
        raise BatchResolutionError(
            [f"consolidation produced an invalid proposal: {exc}"]
        ) from exc


def validate_consolidation_fallback(
    *,
    consolidation: IdentityConsolidationResponse,
    proposal: CanonicalViewpointProposalResponse,
) -> dict[str, Any]:
    """A refused merge must still leave the two viewpoints connected.

    Consolidation ruling a candidate the same viewpoint as a committed one is a
    finding about the material, and it survives the merge being refused: the
    reviewer may show the wording cannot widen without stranding a route, and
    the proposer may withdraw on that ground, but the two propositions are
    still neighbours. Dropping the merge and saying nothing leaves the Registry
    with the duplicate the pass exists to catch and no record that anyone
    noticed. So the edge is required wherever the merge did not stick.
    """

    # Keyed on the matched revision, never on the candidate's local key: the
    # correction round may rename or replace a candidate, and an earlier version
    # of this check keyed on the key, so a rename slipped the whole rule.
    # A merge lands as `member_existing` -- that is what `apply_consolidation`
    # writes, and every candidate is required to own a member component. A
    # `support_existing` aimed at the same viewpoint is a different claim about
    # it and does not mean the identity was folded in.
    merged_targets = {
        str(component.target_viewpoint_revision_id)
        for decision in proposal.claim_decisions
        for component in decision.components
        if component.disposition == "member_existing"
        and component.target_viewpoint_revision_id
    }
    # A typed relation is one way to record the connection; sharing a structure
    # is the other, and for some pairs it is the only honest one. Every
    # `relation_type` is directed -- one viewpoint applies, extends or
    # specializes another -- so two parallel conclusions of the same critique
    # fit none of them. Requiring an edge anyway made a batch invent one, and
    # the review threw it out as `REL_NOT_LOAD_BEARING`: "互为兄弟而非父子,
    # 谁应用谁都读不通". A structure is where "these belong together" lives.
    connected_targets = {
        key
        for relation in proposal.viewpoint_relations
        for kind, key in relation.endpoints()
        if kind == "existing"
        and any(side == "new" for side, _ in relation.endpoints())
    }
    for structure in proposal.structures:
        endpoints = [focal.endpoint() for focal in structure.focal]
        if any(kind == "new" for kind, _ in endpoints):
            connected_targets |= {key for kind, key in endpoints if kind == "existing"}
    findings: list[str] = []
    unmerged: list[dict[str, str]] = []
    for verdict in consolidation.verdicts:
        if verdict.verdict == "new":
            continue
        target = str(verdict.target_viewpoint_revision_id)
        if target in merged_targets:
            continue
        unmerged.append({"matched_revision_id": target, "ruled_local_key": verdict.local_key})
        if target not in connected_targets:
            findings.append(
                f"{target}: consolidation matched it but the merge did not stick and no "
                "relation connects it to a viewpoint this batch proposes"
            )

    if findings:
        raise BatchResolutionError(findings)

    report = {
        "schema_version": "wang_canonical_viewpoint_consolidation_fallback_v1",
        "unmerged_matches": unmerged,
        "checks_passed": ["refused_merge_stays_connected"],
    }
    report["artifact_sha256"] = sha256_json(report)
    return report
