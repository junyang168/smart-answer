"""Claim business operations on the existing source/SQL execution boundary.

This is a versioned content contract, not another runner, persistence store or
call ledger. Old Claim issue contracts keep their original identities/budgets.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Literal
from pydantic.json_schema import SkipJsonSchema
import json
import hashlib
from pathlib import Path
from collections import Counter
from difflib import SequenceMatcher

from pydantic import Field, model_validator, model_serializer

from backend.pipeline import source_reconciliation as source
from backend.api.canonical_repository.postgres_store import (
    KNOWLEDGE_COLLECTIONS, sha256_json, canonical_json,
)

VERSION = "wang_claim_management_v2"
DEFAULT_POLICY = Path(__file__).parent / 'policies/wang_claim_management_opus_only_v3.json'
Operation = Literal["Read", "Create", "Review", "Update", "Retire", "Restore", "Split", "Merge"]
Role = Literal["gpt", "opus", "gemini"]
Disposition = Literal["retain", "update", "create", "retire", "restore"]
CLAIM_COLLECTIONS = frozenset({
    "claims", "evidence_steps", "source_fragments", "claim_relations",
    "claim_relation_constraints", "knowledge_relations", "position_nodes",
    "observations", "questions",
})
HELD = frozenset({"human_hold", "human_review_required", "withheld_unreviewed", "rejected"})
MECHANICAL_FIELDS = source.PROVENANCE | {"revision", "source_reconciliation_review_sha256"}
FRAGMENT_HASH_FIELDS = frozenset({'source_sha256', 'paragraph_text_sha256', 'verbatim_excerpt_sha256'})
MODELS = {
    "gpt": ("gpt-6.1-sol", "codex_subscription"),
    "opus": ("claude-opus-5-5", "claude_subscription"),
    "gemini": ("gemini-3.8-flash", "gemini_api"),
}
AUDIT_MODELS = frozenset({'gemini-3.8-flash', 'gemini-4-argon'})
QUESTIONS = {
    "Create": {"own_source", "attribution", "limitations", "evidence", "relations", "not_duplicate"},
    "Update": {"own_source", "attribution", "limitations", "evidence", "relations", "same_judgment", "shared_owners"},
    "Retire": {"retirement_reason", "original_teaching_disposition", "consumer_impacts"},
    "Restore": {"own_source", "attribution", "limitations", "evidence", "relations", "retirement_cause_resolved", "not_duplicate"},
    "Split": {"own_source", "attribution", "limitations", "evidence", "relations", "original_complete_mapping", "no_added_teaching", "consumer_impacts"},
    "Merge": {"own_source", "attribution", "limitations", "evidence", "relations", "original_complete_mapping", "same_source_judgment", "no_added_teaching", "consumer_impacts"},
    "Review": {"own_source", "attribution", "limitations", "evidence", "relations"},
    "Read": set(),
}


class Budget(source.Contract):
    max_revisions: int = Field(ge=0)
    gpt_calls: int = Field(ge=0)
    opus_calls: int = Field(ge=0)
    gemini_calls: int = Field(ge=0)
    max_request_bytes: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)

    def limit(self, role: Role) -> int:
        return getattr(self, role + "_calls")


class Policy(source.Contract):
    schema_version: Literal["wang_claim_management_v2"] = VERSION
    policy_id: str = Field(min_length=1)
    budget: Budget
    models: dict[str, dict[str, str]]
    clarification_per_problem: Literal[1] = 1
    review_purpose: Literal['content', 'source_impact'] = 'content'
    source_impact_batch_size: int | None = Field(default=None, gt=0)
    independent_audit: bool | None = None
    initial_candidate: Literal['existing','proposal'] | None = None
    all_findings_to_gpt: bool | None = None

    evidence_review_contract: Literal['claim_own_evidence_v1'] | None = None
    claim_scope_contract: Literal['claim_direct_neighbours_v1'] | None = None
    model_output_contract: Literal['claim_changes_v1', 'claim_changes_v2', 'claim_changes_v3', 'claim_changes_v4'] | None = None
    legacy_ai_supersession_restore: Literal[True] | None = None

    joint_update_contract: Literal['wang_claim_joint_update_v1'] | None = None
    joint_update_max_targets: int | None = Field(default=None, gt=0, strict=True)

    @model_serializer(mode='wrap')
    def serialize_policy(self, handler):
        value = handler(self)
        for name in ('evidence_review_contract', 'joint_update_contract', 'joint_update_max_targets', 'model_output_contract', 'legacy_ai_supersession_restore', 'claim_scope_contract'):
            if value[name] is None:
                value.pop(name)
        if self.independent_audit is None:
            value.pop('independent_audit', None)
        if self.initial_candidate is None:
            value.pop('initial_candidate', None)
        if self.all_findings_to_gpt is None:
            value.pop('all_findings_to_gpt', None)
        return value

    @property
    def independent_audit_required(self):
        return self.independent_audit is not False

    @model_validator(mode="after")
    def exact_roles(self):
        if set(self.models) != set(MODELS):
            raise ValueError("all three independent model roles must be bound")
        for role, (model, backend) in MODELS.items():
            selected = self.models[role].get('model_id')
            allowed = AUDIT_MODELS if role == 'gemini' else {model}
            if selected not in allowed or self.models[role] != {"model_id": selected, "backend": backend, "effort": "high"}:
                raise ValueError("Claim policy must bind the exact model/backend/effort")
        if self.source_impact_batch_size and self.review_purpose != 'source_impact':
            raise ValueError('source impact batching belongs to its explicitly bound Review policy')
        if self.model_output_contract and (self.joint_update_contract is None or not self.all_findings_to_gpt or self.review_purpose != 'content'):
            raise ValueError('compact model decisions require the joint content Update contract')
        if self.evidence_review_contract and (self.model_output_contract not in {'claim_changes_v2', 'claim_changes_v3', 'claim_changes_v4'} or self.independent_audit_required or self.claim_scope_contract != 'claim_direct_neighbours_v1'):
            raise ValueError('own Evidence review requires scoped compact Opus and program readback')
        return self


class CreationPermission(source.Contract):
    collection: str
    source_id: str
    fields: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def claim_domain(self):
        if self.collection not in CLAIM_COLLECTIONS:
            raise ValueError("Claim creation cannot authorize CVP/source-document writes")
        return self


class Request(source.Contract):
    schema_version: Literal["wang_claim_management_v2"] = VERSION
    action_id: str = Field(min_length=1)
    operation: Operation
    allowed_operations: list[Operation] = Field(min_length=1)
    target_keys: list[str] = Field(default_factory=list)
    source_bindings: dict[str, source.FileBinding]
    authorization: source.FileBinding
    policy: source.FileBinding
    permissions: list[source.Permission] = Field(default_factory=list)
    creation_permissions: list[CreationPermission] = Field(default_factory=list)
    confirm_current: bool = False
    predecessor_action_id: str | None = None
    source_transition: source.FileBinding | None = None

    @model_validator(mode="after")
    def bounded_scope(self):
        if self.operation not in self.allowed_operations or len(set(self.allowed_operations)) != len(self.allowed_operations):
            raise ValueError("operation must be explicitly authorized exactly once")
        if len(set(self.target_keys)) != len(self.target_keys):
            raise ValueError("original target denominator cannot contain duplicates")
        if self.operation != "Create" and not self.target_keys:
            raise ValueError("existing Claim operation requires an original target denominator")
        for key in self.target_keys:
            collection, _ = source.split_key(key)
            if collection != "claims":
                raise ValueError("business targets must be Claims")
        if self.operation != "Read" and not self.source_bindings:
            raise ValueError("complete own sources are mandatory before reviewing content")
        if len({p.key for p in self.permissions}) != len(self.permissions):
            raise ValueError("duplicate field permission")
        if any(p.collection not in CLAIM_COLLECTIONS for p in self.permissions):
            raise ValueError("Claim permission cannot authorize CVP/source-document writes")
        if any(p.create for p in self.permissions):
            raise ValueError('new Claim objects use explicit source-scoped creation permission, never old flags')
        if any(p.source_id not in self.source_bindings for p in self.creation_permissions):
            raise ValueError("creation scope requires its exact own source")
        if self.operation in {"Read", "Review"} and (self.permissions or self.creation_permissions):
            raise ValueError("Read/Review cannot acquire content write permission")
        if self.operation == "Create" and not self.creation_permissions:
            raise ValueError("Create requires explicit source-scoped creation permission")
        if self.source_transition and (self.operation == 'Read'
                or self.operation != 'Review' and not self.predecessor_action_id):
            raise ValueError('source transition maintenance must link its predecessor Review')
        return self


def is_source_impact(request):
    if isinstance(request, Request):
        return request.operation == 'Review' and bool(request.source_transition)
    return request['operation'] == 'Review' and bool(request.get('source_transition'))


class Citation(source.Contract):
    source_id: str
    paragraph_key: str
    exact_quote: str = Field(min_length=1)
    source_epoch: Literal['current', 'before'] = 'current'


class Finding(source.Contract):
    target_key: str
    component: str = Field(min_length=1)
    category: Literal["content", "attribution", "limitation", "evidence", "mapping", "source_uncertain", "engineering"]
    citations: list[Citation] = Field(min_length=1)
    reason: str = Field(min_length=1)
    acceptance_condition: str = Field(min_length=1)
    human_question: str | None = None
    affected_target_keys: list[str] | None = None
    affected_write_keys: SkipJsonSchema[list[str] | None] = None
    impact_reason: str | None = None

    @model_validator(mode='after')
    def actionable_source_question(self):
        if self.category == 'source_uncertain' and not (self.human_question or '').strip():
            raise ValueError('source_uncertain requires a concrete human question')
        return self

    @model_serializer(mode='wrap')
    def historical_shape(self, handler):
        result = handler(self)
        for name in ('affected_target_keys', 'affected_write_keys', 'impact_reason'):
            if result[name] is None:
                result.pop(name)
        return result

    def identity(self, action_id: str) -> str:
        # Version and explanation remain occurrence data. A correction cannot
        # rename the same underlying problem by changing SHA or prose.
        return "CMF-" + sha256_json({
            "action": action_id, "target": self.target_key,
            "component": self.component, "category": self.category,
            "locations": sorted({(c.source_id, c.paragraph_key) for c in self.citations}),
        })


class ObjectDisposition(source.Contract):
    record_key: str
    action: Disposition
    reason: str = Field(min_length=1)
    citations: list[Citation] = Field(default_factory=list)


class Candidate(source.Contract):
    records: list[source.Row]
    dispositions: list[ObjectDisposition]
    replacements: dict[str, list[str]] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)
    operation: Operation | None = None
    finding_resolutions: list['FindingResolution'] | None = None

    @model_serializer(mode='wrap')
    def historical_candidate(self, handler):
        value = handler(self)
        if self.finding_resolutions is None:
            value.pop('finding_resolutions', None)
        return value

    @property
    def digest(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


class JsonCandidateRow(source.Contract):
    collection: str
    object_id: str
    payload_json: str


class JsonCandidate(source.Contract):
    """Closed provider schema; arbitrary record fields travel as lossless JSON."""
    records: list[JsonCandidateRow]
    dispositions: list[ObjectDisposition]
    replacements_json: str
    findings: list[Finding]
    operation: Operation | None = None


def decode_candidate(raw):
    if 'replacements_json' not in raw:
        return raw
    wire = (AdjudicatingJsonCandidate if 'finding_resolutions' in raw else JsonCandidate).model_validate(raw)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate Candidate JSON field: ' + key)
            result[key] = value
        return result
    def decode(value):
        return json.loads(value, object_pairs_hook=unique)
    return {'records': [{'collection': r.collection, 'object_id': r.object_id,
                         'payload': decode(r.payload_json)} for r in wire.records],
            'dispositions': [d.model_dump(mode='json') for d in wire.dispositions],
            'replacements': decode(wire.replacements_json),
            'findings': [f.model_dump(mode='json') for f in wire.findings], 'operation': wire.operation,
            **({'finding_resolutions':[r.model_dump(mode='json') for r in wire.finding_resolutions]}
               if isinstance(wire, AdjudicatingJsonCandidate) else {})}


class FindingResolution(source.Contract):
    finding_id: str
    disposition: Literal["resolved", "persistent", "reopened"]
    reason: str = Field(min_length=1)
    citations: list[Citation] = Field(min_length=1)
    affected_target_keys: list[str] | None = None
    affected_write_keys: SkipJsonSchema[list[str] | None] = None
    impact_reason: str | None = None

    @model_serializer(mode='wrap')
    def historical_shape(self, handler):
        result = handler(self)
        for name in ('affected_target_keys', 'affected_write_keys', 'impact_reason'):
            if result[name] is None:
                result.pop(name)
        return result


class AdjudicatingJsonCandidate(JsonCandidate):
    finding_resolutions: list[FindingResolution]


Candidate.model_rebuild()

class Review(source.Contract):
    proposal_sha256: str
    verdicts: list[source.Verdict]
    findings: list[Finding]
    prior_findings: list[FindingResolution]
    answered_questions: list[str]
    source_impacts: list['SourceImpact'] = Field(default_factory=list)


class JointFinding(Finding):
    affected_target_keys: list[str]
    affected_write_keys: list[str]
    impact_reason: str = Field(min_length=1)


class JointFindingResolution(FindingResolution):
    affected_target_keys: list[str]
    affected_write_keys: list[str]
    impact_reason: str = Field(min_length=1)


class JointJsonCandidate(AdjudicatingJsonCandidate):
    findings: list[JointFinding]
    finding_resolutions: list[JointFindingResolution]


class JointReview(Review):
    findings: list[JointFinding]
    prior_findings: list[JointFindingResolution]


class SourceTransition(source.Contract):
    schema_version: Literal['wang_claim_source_transition_v2'] = 'wang_claim_source_transition_v2'
    before_sources: dict[str, source.FileBinding]
    source_change_set_ids: dict[str, str]

    @model_validator(mode='after')
    def complete(self):
        if not self.before_sources or set(self.before_sources) != set(self.source_change_set_ids):
            raise ValueError('source transition needs exact old files and committed source versions')
        return self


class SourceImpact(source.Contract):
    record_key: str
    conclusion: Literal['unaffected', 'maintenance', 'source_uncertain']
    reason: str = Field(min_length=1)
    citations: list[Citation] = Field(min_length=1)
    operation: Operation | None = None


IMPACT_QUESTIONS = frozenset({'source_delta', 'context_impact', 'basis_preservation',
                              'locator_mapping', 'coverage_new_teaching'})
Review.model_rebuild()
JointReview.model_rebuild()


def audit_contract(root, policy=None):
    """Freeze actual audit instructions/schema and the implementation version."""
    from backend.pipeline.source_reconciliation_runner import archive_source_version, binding
    pipeline = Path(__file__).parent
    repository = pipeline.parents[1]
    joint = policy is not None and policy.joint_update_contract is not None
    prompt = pipeline / ('prompts/claim_management_audit_joint_v1.md' if joint else 'prompts/claim_management_audit_v2.md')
    files = [Path(__file__), pipeline/'claim_joint_update.py', pipeline/'prompts/claim_management_joint_update_v1.md', pipeline/'prompts/claim_management_audit_joint_v1.md', pipeline/'claim_review_recovery.py', pipeline/'claim_qualification_reuse.py', pipeline/'claim_human_source.py', pipeline/'claim_direct_scope.py', pipeline/'cvp_sql_actions.py', pipeline/'cvp_sql_flow.py', pipeline/'cvp_sql_commit.py', pipeline/'cvp_sql_claim_authority.py',
        pipeline/'source_reconciliation_runner.py', pipeline/'source_reconciliation_runtime.py',
        pipeline/'claim_evidence_review.py', pipeline/'prompts/claim_own_evidence_v1.md', pipeline/'claim_model_output.py', pipeline/'prompts/claim_management_compact_v1.md', pipeline/'prompts/claim_management_compact_v2.md', pipeline/'subscription_process.py', pipeline/'codex_subscription_client.py', pipeline/'claude_subscription_client.py',
        repository/'backend/api/canonical_repository/postgres_store.py', repository/'backend/api/canonical_repository/knowledge_models.py', repository/'scripts/audit-library.py',
        *[pipeline/'prompts'/('claim_management_'+role+'_v2.md') for role in ('proposal','review','audit')]]
    if policy is not None and policy.model_output_contract == 'claim_changes_v4':
        files += [pipeline/'prompts/claim_management_compact_v4.md',
                  repository/'backend/api/canonical_repository/claim_identity_allocation.py']
    return {'instructions': archive_source_version(source.FileBinding.model_validate(binding(prompt)), Path(root)/'audit-contract'),
        'response_schema': (JointReview if joint else Review).model_json_schema(),
        'implementation': {str(path.relative_to(repository)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}}


def require_current_implementation(contract):
    repository = Path(__file__).parents[2]
    if any(hashlib.sha256((repository/path).read_bytes()).hexdigest() != digest
           for path, digest in contract['implementation'].items()):
        raise ValueError('Claim implementation changed during its bounded action; explicit recovery is required')


def validate_citations(citations, sources):
    for citation in citations:
        snapshot = sources.get(citation.source_id)
        if citation.source_epoch == 'before':
            # an unchanged source has no separate before: it is the current text
            snapshot = (snapshot or {}).get('before', snapshot)
        paragraph = (snapshot or {}).get("paragraphs", {}).get(citation.paragraph_key)
        if not paragraph or citation.exact_quote not in paragraph["text"]:
            raise ValueError("citation is not an exact quote from the complete own source")


RELATION_COLLECTIONS = frozenset({'claim_relations', 'claim_relation_constraints', 'knowledge_relations'})


def domain_collection(payload):
    return next((col for col in CLAIM_COLLECTIONS if KNOWLEDGE_COLLECTIONS[col][1] in payload), None)


def domain_dependencies(collection, payload, records):
    """Named content references only; relation source_id is an endpoint.

    Source ownership of a Claim follows its occurrences and Evidence, never
    another Claim's relation. Audit traversal includes both relation endpoints.
    """
    refs = set()
    if collection == 'claims':
        refs.update(source.key('evidence_steps', eid) for eid in payload.get('evidence_step_ids', []))
        refs.update(source.key('position_nodes', oid) for oid in payload.get('opposed_position_ids', []))
    if collection in RELATION_COLLECTIONS:
        for field in ('from_id', 'to_id', 'source_id', 'target_id', 'from_claim_id', 'to_claim_id'):
            oid = payload.get(field)
            if not oid:
                continue
            matches = [k for k in records if source.split_key(k)[1] == oid
                       and source.split_key(k)[0] in CLAIM_COLLECTIONS]
            if len(matches) != 1:
                raise ValueError('relation endpoint is missing or ambiguous: ' + str(oid))
            refs.update(matches)
    if collection in {'evidence_steps', 'observations', 'questions', 'claims', 'position_nodes'}:
        fragments = set(payload.get('source_fragment_ids', []))
        if payload.get('source_fragment_id'):
            fragments.add(payload['source_fragment_id'])
        refs.update(source.key('source_fragments', fid) for fid in fragments)
    if collection == 'questions':
        refs.update(source.key('claims', cid) for cid in payload.get('answer_claim_ids', []))
    return refs


def own_source_ids(payload, records):
    """Resolve own source without confusing Claim endpoints with SourceDocs."""
    documents = {source.split_key(key)[1]: value for key, value in records.items()
                 if key.startswith('source_documents/')}
    def canonical(sid):
        if sid in documents:
            return sid
        matches = [key for key, doc in documents.items()
                   if sid in {doc.get('transcript_id'), doc.get('canonical_source_id')}]
        if len(matches) > 1:
            raise ValueError('own source alias is ambiguous: ' + str(sid))
        return matches[0] if matches else sid
    def resolve(value, seen):
        collection = domain_collection(value)
        ids = {canonical(o.get('source_id') or o.get('transcript_id')) for o in value.get('occurrences', [])
               if o.get('source_id') or o.get('transcript_id')}
        if value.get('source_id') and collection not in RELATION_COLLECTIONS:
            ids.add(canonical(value['source_id']))
        dependencies = domain_dependencies(collection, value, records)
        if collection == 'claims':
            dependencies = {key for key in dependencies if not key.startswith('position_nodes/')}
        for key in dependencies - seen:
            if key not in records:
                raise ValueError('own source has a missing dependency: ' + key)
            ids.update(resolve(records[key], seen | {key}))
        # External positions acquire context through their explicit relations,
        # not a fabricated source_id field. A cycle does not invent ownership.
        if collection == 'position_nodes':
            oid = value.get(KNOWLEDGE_COLLECTIONS[collection][1])
            for key, relation in records.items():
                if key in seen or source.split_key(key)[0] not in RELATION_COLLECTIONS:
                    continue
                if oid in {relation.get(f) for f in ('from_id', 'to_id', 'source_id', 'target_id')}:
                    ids.update(resolve(relation, seen | {key}))
        return ids
    return resolve(payload, set())


def validate_candidate(request: Request, before, candidate: Candidate, sources, *, created_keys=(), original_replacements=None):
    """Validate a complete candidate, never infer semantic or write approval."""
    from backend.pipeline.claim_joint_update import enabled, validate_candidate_scope
    joint = enabled(request)
    if joint:
        validate_candidate_scope(request, before, candidate)
    proposed = source.index(candidate.records)
    current_operation = candidate.operation or request.operation
    internal_revision = current_operation == "Update" and request.operation in {"Create", "Restore", "Split", "Merge"}
    if current_operation not in request.allowed_operations and not internal_revision:
        raise ValueError("candidate operation exceeds original authorization")
    dispositions = {d.record_key: d for d in candidate.dispositions}
    if len(dispositions) != len(candidate.dispositions):
        raise ValueError("candidate repeats an object disposition")
    required = set(before) | set(proposed)
    if set(dispositions) != required:
        raise ValueError("candidate omitted/added a current dependency disposition")
    if not set(request.target_keys) <= set(before):
        raise ValueError("original Claim target missing from the complete input")
    post = {key: deepcopy(row["payload"]) for key, row in before.items()}
    permissions = {p.key: p for p in request.permissions}
    writes = {}
    for key, disposition in dispositions.items():
        validate_citations(disposition.citations, sources)
        old = before.get(key)
        payload = proposed.get(key)
        collection, oid = source.split_key(key)
        if disposition.action == "retain":
            if old is None or (payload is not None and payload != old["payload"]):
                raise ValueError("retained candidate changed or refers to an absent object")
            continue
        if request.operation in {"Read", "Review"}:
            raise ValueError("review is not permission to modify content")
        operation = {"update": "Update", "create": "Create", "retire": "Retire", "restore": "Restore"}[disposition.action]
        structural = current_operation in {"Split", "Merge"} and disposition.action in {"create", "retire"}
        if operation not in request.allowed_operations and not structural and not (internal_revision and operation == "Update"):
            raise ValueError("object disposition exceeds authorized operations")
        if collection not in CLAIM_COLLECTIONS or not disposition.citations:
            raise ValueError("every content disposition requires own-source evidence")
        if old and old["payload"].get("review_status") in HELD:
            raise ValueError("Claim operation cannot override a protected review hold")
        if disposition.action == "create":
            if old is not None or payload is None:
                raise ValueError("create requires an absent identity and complete postimage")
            if payload.get("review_status", "candidate") != "candidate" or any(payload.get(k) for k in ("reviewed_by", "reviewed_at", "review_note")):
                raise ValueError("proposed creation cannot invent review provenance")
            provisional = {**post, **proposed}
            source_ids = own_source_ids(payload, provisional)
            if len(source_ids) != 1 or not source_ids <= set(request.source_bindings):
                raise ValueError("new Claim domain object must have one complete own source")
            grants = [p for p in request.creation_permissions if p.collection == collection and p.source_id in source_ids]
            if len(grants) != 1:
                raise ValueError("new object lacks unique source-scoped creation permission")
            id_field = KNOWLEDGE_COLLECTIONS[collection][1]
            mechanical = MECHANICAL_FIELDS | (FRAGMENT_HASH_FIELDS if collection == 'source_fragments' else set())
            if set(payload) - set(grants[0].fields) - mechanical - {id_field}:
                raise ValueError("created fields exceed creation permission")
        else:
            permission = permissions.get(key)
            if permission is None and key in created_keys and disposition.action == "update":
                own = own_source_ids(old["payload"], post) if old else set()
                grants = [p for p in request.creation_permissions if p.collection == collection and p.source_id in own]
                if len(grants) == 1:
                    permission = source.Permission(collection=collection, object_id=oid, fields=grants[0].fields)
            if old is None or permission is None:
                raise ValueError("write to a read-only owner or absent object")
            legacy_restore = (collection == 'claims' and disposition.action == 'restore'
                and legacy_restore_enabled(request) and legacy_ai_supersession(old))
            if disposition.action == "restore":
                if (not old["retired"] and not legacy_restore) or payload is None:
                    raise ValueError("Restore requires retired current state and full postimage")
            elif old["retired"]:
                raise ValueError("retired object requires explicit Restore")
            if disposition.action == "retire":
                if payload is not None and payload != old["payload"]:
                    raise ValueError("retirement preserves the exact historical payload")
                payload = deepcopy(old["payload"])
            elif payload is None:
                raise ValueError("update/restore requires a complete postimage")
            allowed_provenance = {'review_status'} if legacy_restore and payload.get('review_status') == 'candidate' else set()
            if legacy_restore and (payload.get('review_status') != 'candidate' or payload.get('superseded_by')):
                raise ValueError('legacy Restore must clear supersession without manufacturing approval')
            if any(payload.get(k) != old["payload"].get(k) for k in source.PROVENANCE - allowed_provenance):
                raise ValueError("proposed update cannot manufacture review provenance")
            changed = {k for k in set(old["payload"]) | set(payload) if old["payload"].get(k) != payload.get(k)}
            mechanical = MECHANICAL_FIELDS | (FRAGMENT_HASH_FIELDS if collection == 'source_fragments' else set())
            if changed - set(permission.fields) - mechanical:
                raise ValueError("updated fields exceed original field permission")
        if disposition.action != "retire":
            post[key] = deepcopy(payload)
            if collection == "claims":
                own = own_source_ids(payload, {**post, **proposed})
                if len(own) != 1 or not own <= set(request.source_bindings):
                    raise ValueError("active Claim postimage requires a complete single own source")
                if disposition.action == "update" and own != own_source_ids(old["payload"], post):
                    raise ValueError("Update cannot change the original Claim source identity; use Split")
        writes[key] = {"action": disposition.action, "payload": deepcopy(payload)}
    for finding in candidate.findings:
        if joint:
            finding_blocks(finding.model_dump(mode='json'), request, candidate)
        if finding.target_key not in required:
            raise ValueError("finding outside the complete current input")
        validate_citations(finding.citations, sources)
    targets = set(request.target_keys)
    if legacy_restore_enabled(request) and any(
            legacy_ai_supersession(before[k]) and dispositions[k].action != 'restore' for k in targets):
        raise ValueError('legacy Restore must explicitly restore every original superseded target')
    retired_claims = {k for k, v in writes.items() if k.startswith("claims/") and v["action"] == "retire"}
    new_claims = {k for k, v in writes.items() if k.startswith("claims/") and v["action"] == "create"}
    if current_operation == 'Create' and not new_claims:
        raise ValueError('Create requires at least one actual new Claim')
    if current_operation in {"Split", "Merge"}:
        if set(candidate.replacements) != targets or not targets <= retired_claims:
            raise ValueError("structural operation must dispose every original target")
        mapped = [v for values in candidate.replacements.values() for v in values]
        if not mapped or set(mapped) != new_claims or any(not values or len(set(values)) != len(values) for values in candidate.replacements.values()):
            raise ValueError("structural operation omitted a new target or replacement mapping")
        if current_operation == "Split" and (len(targets) != 1 or len(new_claims) < 2):
            raise ValueError("Split requires one original Claim and multiple new identities")
        if current_operation == "Merge" and (len(targets) < 2 or len(new_claims) != 1):
            raise ValueError("Merge requires multiple original Claims and one new identity")
        if current_operation == "Merge":
            own = set().union(*(own_source_ids(before[k]["payload"], post) for k in targets),
                              *(own_source_ids(post[k], post) for k in new_claims))
            if len(own) != 1:
                raise ValueError("Merge cannot synthesize across sources")
    elif candidate.replacements and candidate.replacements != original_replacements:
        raise ValueError("replacement mapping requires an authorized structural operation")
    from backend.api.canonical_repository.knowledge_models import knowledge_relation_endpoints
    for key,write in writes.items():
        if not key.startswith('knowledge_relations/') or write['action']=='retire':continue
        relation=write['payload']
        for field,allowed in zip(('from_id','to_id'),knowledge_relation_endpoints(relation['relation_type'])):
            owners={source.split_key(k)[0] for k in post if source.split_key(k)[1]==relation[field]}
            if len(owners)!=1 or not owners<=allowed:
                raise ValueError(f'knowledge relation {key} {field} has incompatible endpoint collection')
    active_before = {k for k, v in before.items() if not v['retired']}
    active_after = (active_before | {k for k, v in writes.items() if v['action'] in {'create', 'restore'}}) - retired_claims - {
        k for k, v in writes.items() if v['action'] == 'retire'}
    introduced = (one_sided_owner_pairs(post, active_after)
                  - one_sided_owner_pairs({k: v['payload'] for k, v in before.items()}, active_before))
    if any(source.key('claims', c) in writes or source.key('evidence_steps', e) in writes for c, e in introduced):
        raise ValueError('Claim/Evidence ownership must stay reciprocal: ' + ', '.join(
            f'{c}<->{e}' for c, e in sorted(introduced)))
    return post, writes


def one_sided_owner_pairs(records, active):
    """Claim.evidence_step_ids and EvidenceStep.produced_claim_ids are one link.

    A sibling Claim that takes over an existing EvidenceStep has to appear on
    both sides; otherwise the route and CVP layers, which read either side,
    disagree about who the step supports. Only pairs whose ends are both active
    count, and only pairs a candidate introduces are refused -- legacy one-sided
    pairs belong to the reciprocity repair, not to this write.
    """
    def ids(collection):
        return {source.split_key(k)[1]: records[k] for k in active
                if k in records and source.split_key(k)[0] == collection}
    claims, steps = ids('claims'), ids('evidence_steps')
    forward = {(c, str(e)) for c, p in claims.items() for e in p.get('evidence_step_ids') or [] if str(e) in steps}
    backward = {(str(c), e) for e, p in steps.items() for c in p.get('produced_claim_ids') or [] if str(c) in claims}
    return forward ^ backward


def validate_proposer_resolutions(candidate, previous, sources, *, request=None):
    resolutions = candidate.finding_resolutions or []
    keys = [r.finding_id for r in resolutions]
    if len(keys) != len(set(keys)) or set(keys) != set(previous):
        raise ValueError('GPT must adjudicate every audit finding exactly once')
    for resolution in resolutions:
        validate_citations(resolution.citations, sources)
        if request is not None:
            from backend.pipeline.claim_joint_update import enabled
            if enabled(request) and resolution.disposition != 'resolved':
                finding_blocks({**previous[resolution.finding_id], 'affected_target_keys': resolution.affected_target_keys,
                    'affected_write_keys': resolution.affected_write_keys, 'impact_reason': resolution.impact_reason}, request, candidate)


def pending_context_adjudications(previous, candidate, context, *, request):
    """All context objections need GPT; agreed unrelated holds stay in the report."""
    decisions = {r.finding_id: r for r in candidate.finding_resolutions or []}
    return {fid: finding for fid, finding in context.items()
        if fid not in decisions or fid not in previous
        or decisions[fid].disposition == 'resolved'
        or finding_blocks(resolution_finding(decisions[fid], previous), request, candidate)}


def review_findings(action_id, previous, candidate):
    """Queue proposer doubts for independent resolution, retaining stable IDs."""
    result = deepcopy(previous)
    if not candidate.findings:
        return result
    identities = {Finding.model_validate(f).identity('review') for f in result.values()}
    for finding in candidate.findings:
        identity = finding.identity('review')
        if identity not in identities:
            result[finding.identity(action_id)] = finding.model_dump(mode='json')
            identities.add(identity)
    return result


def validate_review(candidate, review, expected_keys, questions, unresolved, sources, *, request=None, records=None):
    unresolved = review_findings(request.action_id if request else 'proposal', unresolved, candidate)
    if review.proposal_sha256 != candidate.digest:
        raise ValueError("review belongs to a different complete candidate")
    if request is not None:
        from backend.pipeline.claim_joint_update import enabled, required_keys
        if enabled(request) and not (set(request.target_keys) | set(required_keys(request))) <= set(expected_keys):
            raise ValueError('joint review omitted original targets or required maintenance denominator')
    keys = [v.record_key for v in review.verdicts]
    if len(keys) != len(set(keys)) or set(keys) != set(expected_keys):
        raise ValueError("review denominator omitted/repeated an object")
    if not set(questions) <= set(review.answered_questions) or len(review.answered_questions) != len(set(review.answered_questions)):
        raise ValueError("review did not cover the original operation requirements")
    resolved = [r.finding_id for r in review.prior_findings]
    if len(resolved) != len(set(resolved)) or set(resolved) != set(unresolved):
        raise ValueError("review must reconcile every prior stable finding")
    covered_proposer_issues = {f.identity('review') for f in review.findings}
    covered_proposer_issues.update(Finding.model_validate(unresolved[r.finding_id]).identity('review')
        for r in review.prior_findings)
    omitted = [f.target_key for f in candidate.findings if f.identity('review') not in covered_proposer_issues]
    if omitted:
        raise ValueError('review omitted a proposer-reported issue: ' + ', '.join(sorted(set(omitted))))
    identities = [f.identity('review') for f in review.findings]
    prior_identities = {Finding.model_validate(f).identity('review') for f in unresolved.values()}
    if len(identities) != len(set(identities)) or set(identities) & prior_identities:
        raise ValueError('review repeats a stable finding; reconcile existing issues in prior_findings')
    for finding in review.findings:
        if finding.target_key not in expected_keys:
            raise ValueError("review finding outside its declared denominator")
        validate_citations(finding.citations, sources)
        finding_blocks(finding.model_dump(mode='json'), request, candidate)
    for resolution in review.prior_findings:
        validate_citations(resolution.citations, sources)
        if resolution.disposition != 'resolved':
            finding_blocks(resolution_finding(resolution, unresolved), request, candidate)
    rejected = {v.record_key for v in review.verdicts if not v.approved or v.problems}
    explained = {f.target_key for f in review.findings}
    explained.update(unresolved[r.finding_id]["target_key"] for r in review.prior_findings
                     if r.disposition != "resolved")
    if rejected - explained:
        raise ValueError("rejected objects require concrete current or persistent findings: " + ", ".join(sorted(rejected - explained)))
    if request is not None:
        from backend.pipeline.claim_joint_update import enabled, validate_scope_agreement
        if enabled(request):
            validate_scope_agreement(candidate, review, unresolved, request)
    if request is not None and is_source_impact(request):
        return validate_source_impacts(review, expected_keys, sources, request=request, records=records, unresolved=unresolved)
    approved = {v.record_key for v in review.verdicts if v.approved and not v.problems}
    explicit_issues = {f.target_key for f in review.findings if f.affected_target_keys is not None}
    explicit_issues.update(unresolved[r.finding_id]['target_key'] for r in review.prior_findings
        if r.disposition != 'resolved' and resolution_finding(r, unresolved).get('affected_target_keys') is not None)
    if explicit_issues & approved:
        raise ValueError('finding contradicts an approved object: ' + ', '.join(sorted(explicit_issues & approved)))
    blocking = [f for f in review.findings if finding_blocks(f.model_dump(mode='json'), request, candidate)]
    context_rejections = {f.target_key for f in review.findings if f not in blocking}
    persistent = [r for r in review.prior_findings if r.disposition != 'resolved']
    context_rejections.update(unresolved[r.finding_id]['target_key'] for r in persistent
        if not finding_blocks(resolution_finding(r, unresolved), request, candidate))
    return (all(v.approved and not v.problems or v.record_key in context_rejections for v in review.verdicts)
            and not blocking
            and not any(finding_blocks(resolution_finding(r, unresolved), request, candidate) for r in persistent))


def finding_blocks(finding, request=None, candidate=None):
    """Reviewer explains target impact; absence preserves conservative old reviews."""
    from backend.pipeline.claim_joint_update import enabled, finding_blocks as joint_blocks
    if request is not None and enabled(request):
        return joint_blocks(finding, request, candidate)
    affected = finding.get('affected_target_keys')
    if request is None or is_source_impact(request) or affected is None:
        return True
    targets = set(request.target_keys)
    if candidate:
        targets.update(d.record_key for d in candidate.dispositions if d.action == 'create' and d.record_key.startswith('claims/'))
    if len(affected) != len(set(affected)) or not set(affected) <= targets or not (finding.get('impact_reason') or '').strip():
        raise ValueError('finding requires exact authorized target impact and explanation')
    written = {d.record_key for d in candidate.dispositions if d.action != 'retain'} if candidate else set()
    if not affected and finding['target_key'] in targets:
        raise ValueError('target or actual write finding cannot be dismissed as readonly context')
    if finding['target_key'] in targets and finding['target_key'] not in affected:
        raise ValueError('finding on a target must include that target in its impact')
    # Explicitly authorized maintenance objects are part of this Update's
    # obligations even when the proposer keeps them unchanged.
    return bool(affected) or finding['target_key'] in written | {p.key for p in request.permissions}


def resolution_finding(resolution, unresolved):
    finding = deepcopy(unresolved[resolution.finding_id])
    if resolution.affected_target_keys is not None:
        finding.update(affected_target_keys=resolution.affected_target_keys, impact_reason=resolution.impact_reason)
    if resolution.affected_write_keys is not None:
        finding['affected_write_keys'] = resolution.affected_write_keys
    return finding


def context_findings_after(action_id, previous, review, *, request, candidate):
    previous = review_findings(action_id, previous, candidate)
    result = {f.identity(action_id): f.model_dump(mode='json') for f in review.findings
        if not finding_blocks(f.model_dump(mode='json'), request, candidate)}
    result.update({r.finding_id: resolution_finding(r, previous) for r in review.prior_findings
        if r.disposition != 'resolved' and not finding_blocks(resolution_finding(r, previous), request, candidate)})
    return result


def unresolved_after(action_id, previous, review, *, request=None, candidate=None):
    if candidate:
        previous = review_findings(action_id, previous, candidate)
    result = deepcopy(previous)
    for resolution in review.prior_findings:
        if resolution.disposition == "resolved":
            result.pop(resolution.finding_id, None)
        else:
            finding = resolution_finding(resolution, previous)
            if finding_blocks(finding, request, candidate):
                result[resolution.finding_id] = finding
            else:
                result.pop(resolution.finding_id, None)
    for finding in review.findings:
        if finding_blocks(finding.model_dump(mode='json'), request, candidate):
            result[finding.identity(action_id)] = finding.model_dump(mode="json")
    return result


def subtitle_only_generation(document, current):
    """Subtitles are metadata: the registered file may differ by inserted headings alone.

    #436 inserted subtitle rows into transcripts after registration, so the
    file SHA moved while body, coordinates and visual content did not, and
    every Claim action on those sermons was refused as source drift. This
    accepts only the #436 proof chain (source_editorial_transition), which
    re-reads both generations and every original row -- never a body-SHA
    comparison on its own.
    """
    from backend.pipeline.source_editorial_transition import verify_current_source_binding
    try:
        live = verify_current_source_binding({'path': str(Path(document['source_path']).resolve()),
                                              'file_sha256': document['source_file_sha256']})
    except (ValueError, KeyError):
        return False
    return (live['file_sha256'] == current['file_sha256']
            and str(Path(live['path']).resolve()) == str(Path(current['path']).resolve()))


def load_authority(request: Request, *, source_documents=None, archived_sources=None, selected_source_ids=None, human_source_decisions=None):
    """Bind a new action to an actual direction and policy, never old v1 flags."""
    policy = Policy.model_validate_json(source.verify_file(request.policy).read_text())
    if policy.joint_update_contract and request.operation != 'Update':
        raise ValueError('joint Update policy belongs only to ordinary Update')
    if policy.legacy_ai_supersession_restore and request.operation != 'Restore':
        raise ValueError('legacy AI supersession capability belongs only to explicit Restore')
    if policy.legacy_ai_supersession_restore and (policy.joint_update_contract or policy.model_output_contract):
        raise ValueError('legacy Restore uses the existing full structural-operation contract')
    if (policy.review_purpose == 'source_impact') != is_source_impact(request):
        raise ValueError('source impact requires its explicitly bound Review policy and transition')
    authority = json.loads(source.verify_file(request.authorization).read_text())
    expected = request.model_dump(mode="json", exclude={"authorization", "policy"})
    if (authority.get("schema_version") != VERSION
            or authority.get("authorized_by") != "owner"
            or not authority.get("authorization_quote")
            or authority.get("human_content_approval") is not False
            or authority.get("request") != expected
            or authority.get("policy") != request.policy.model_dump()):
        raise ValueError("Claim action lacks its exact new policy and original authority")
    if authority.get('human_source_verification'):
        source.verify_file(source.FileBinding.model_validate(authority['human_source_verification']))
    sources = {}
    selected = set(request.source_bindings) if selected_source_ids is None else set(selected_source_ids)
    if not selected <= set(request.source_bindings):
        raise ValueError('unit cannot expand the authorized full own source scope')
    for sid, binding in request.source_bindings.items():
        if sid not in selected:
            continue
        observed = source.FileBinding.model_validate((archived_sources or {}).get(sid, binding))
        if observed.file_sha256 != binding.file_sha256:
            raise ValueError('archived source differs from the actual original source bytes')
        sources[sid] = source.source_snapshot(observed, source=(source_documents or {}).get(sid))
        sources[sid]['file'] = binding.model_dump(mode='json')
    for sid, snapshot in sources.items():
        document = (source_documents or {}).get(sid, {})
        declared = document.get('source_file_sha256')
        if declared and declared != snapshot['file']['file_sha256'] and not subtitle_only_generation(document, snapshot['file']):
            raise ValueError('registered source physical version drift; new source version/action required')
    source_context = request.source_transition
    if source_context:
        transition = SourceTransition.model_validate_json(source.verify_file(source_context).read_text())
        if not set(transition.before_sources) <= set(request.source_bindings):
            raise ValueError('source transition omits a complete current own source')
        for sid, bound in transition.before_sources.items():
            if sid not in sources:
                continue
            previous = source.source_snapshot(bound, source=(source_documents or {}).get(sid))
            if previous['file']['file_sha256'] == sources[sid]['file']['file_sha256']:
                raise ValueError('source impact requires an actual source version transition')
            sources[sid]['before'] = previous
            sources[sid]['delta'] = source_delta(previous, sources[sid])
    # A context finding can become a new explicit target after a human has
    # clarified the source. Keep that evidence in the same frozen source packet
    # for proposal, review and commit validation; it never edits the transcript
    # or substitutes for independent content review. Linked continuations retain
    # their existing, separately validated evidence contract.
    if authority.get('human_source_verification') and not request.predecessor_action_id:
        bound = source.FileBinding.model_validate(authority['human_source_verification'])
        record = json.loads(source.verify_file(bound).read_text())
        answers = record.get('answers', [])
        ids = [a.get('finding_id') for a in answers]
        if (record.get('schema_version') != 'wang_claim_human_source_verification_v1'
                or record.get('verified_by') != 'user' or record.get('human_content_approval') is not False
                or record.get('source_bindings') != request.model_dump(mode='json')['source_bindings']
                or not record.get('user_confirmation') or not answers
                or any(not i for i in ids) or len(set(ids)) != len(ids)):
            raise ValueError('new target source clarification requires bound human facts, not content approval')
        for answer in answers:
            citation = Citation.model_validate(answer.get('citation'))
            if (not answer.get('question') or not answer.get('answer')
                    or citation.source_id not in request.source_bindings):
                raise ValueError('human clarification omitted its source question or answer')
            if citation.source_id in sources:
                validate_citations([citation], sources)
        for sid in sources:
            relevant = [a for a in answers if a['citation']['source_id'] == sid]
            if relevant:
                sources[sid]['human_source_verification'] = {
                    'artifact': bound.model_dump(mode='json'), 'record': record,
                    'source_text_unchanged': True, 'content_review_still_required': True}
    if human_source_decisions is not None:
        from backend.pipeline.claim_human_source import project
        project(sources, human_source_decisions)
    return policy, sources


def source_delta(before, current):
    """Complete paragraph accounting, never a semantic impact decision.

    Only a unique exact paragraph can receive an automatic old/new identity
    match. Duplicate text, replacements and splits remain explicit groups.
    """
    old, new = before['paragraphs'], current['paragraphs']
    old_keys, new_keys = list(old), list(new)
    def identity(row):
        return canonical_json({k: v for k, v in row.items() if k != 'index'})
    left, right = [identity(old[k]) for k in old_keys], [identity(new[k]) for k in new_keys]
    lc, rc = Counter(left), Counter(right)
    positions = {value: i for i, value in enumerate(right) if rc[value] == 1}
    mapping = {old_keys[i]: new_keys[positions[value]] for i, value in enumerate(left)
               if lc[value] == rc[value] == 1 and value in positions}
    groups = []
    for kind, a, b, c, d in SequenceMatcher(None, left, right, autojunk=False).get_opcodes():
        old_group, new_group = old_keys[a:b], new_keys[c:d]
        if kind == 'equal':
            ambiguous = [k for k in old_group if k not in mapping]
            if not ambiguous:
                continue
            kind = 'ambiguous'
        groups.append({'kind': kind, 'before': old_group, 'current': new_group})
    return {'schema_version': 'wang_claim_source_delta_v2', 'before_file': before['file'],
            'current_file': current['file'], 'before_body_sha256': before['body_sha256'],
            'current_body_sha256': current['body_sha256'], 'unique_exact_mapping': mapping,
            'change_groups': groups, 'semantic_approval': False}


def validate_source_impacts(review, keys, sources, *, request, records, unresolved):
    items = {item.record_key: item for item in review.source_impacts}
    if len(items) != len(review.source_impacts) or set(items) != {k for k in keys if k.startswith('claims/')}:
        raise ValueError('source impact Review must account for every current Claim exactly once')
    findings = {f.target_key for f in review.findings}
    findings.update(unresolved[r.finding_id]['target_key'] for r in review.prior_findings
                    if r.disposition != 'resolved')
    for item in items.values():
        validate_citations(item.citations, sources)
        if item.conclusion == 'unaffected' and (item.operation or item.record_key in findings):
            raise ValueError('unaffected source impact cannot hide a maintenance finding')
        if item.conclusion == 'unaffected':
            own = request.model_copy(update={'target_keys': [item.record_key], 'permissions': []})
            closure = set(audit_keys(own, records))
            if closure & findings:
                raise ValueError('unaffected source impact cannot hide an unresolved dependency finding')
            for key in closure:
                if not key.startswith('source_fragments/') or records[key]['retired']:
                    continue
                fragment = records[key]['payload']
                snapshot = sources.get(fragment.get('source_id'), {})
                paragraph = snapshot.get('paragraphs', {}).get(str(fragment.get('paragraph_key')), {})
                excerpt = fragment.get('verbatim_excerpt')
                if (not excerpt or excerpt not in paragraph.get('text', '')
                        or fragment.get('source_sha256') != snapshot.get('body_sha256')):
                    raise ValueError('unaffected source impact lacks an exact current fragment/source binding')
        if item.conclusion != 'unaffected' and item.record_key not in findings:
            raise ValueError('source maintenance/uncertainty requires a concrete finding')
        if item.conclusion == 'maintenance' and item.operation not in {'Update','Retire','Restore','Split','Merge'}:
            raise ValueError('source impact needs an explicit ordinary maintenance operation')
        if item.conclusion == 'source_uncertain' and not any(f.target_key == item.record_key
                and f.category == 'source_uncertain' and f.human_question for f in review.findings):
            raise ValueError('source uncertainty needs its exact human question')
    return (all(v.approved and not v.problems for v in review.verdicts)
            and not any(f.category == 'engineering' for f in review.findings))


def audit_keys(request, before, candidate=None):
    from backend.pipeline.claim_direct_scope import enabled, scope
    if enabled(request):
        return scope(request, before, candidate)['record_keys']
    return legacy_audit_keys(request, before, candidate)


def legacy_audit_keys(request, before, candidate=None):
    """Claim evidence denominator, without turning CVP context into approval.

    A shared Evidence write expands to every owner. Unchanged CVP memberships
    remain transaction read pins and pending consumers, not Claim audit proofs.
    """
    roots = set(request.target_keys)
    from backend.pipeline.claim_joint_update import enabled, required_keys
    if enabled(request):
        roots.update(k for k in required_keys(request, strict=False) if k in before)
    records = {k: v["payload"] for k, v in before.items()}
    original_records = dict(records)
    if candidate:
        records.update(source.index(candidate.records))
        roots.update(d.record_key for d in candidate.dispositions if d.action != "retain")
    else:
        roots.update(p.key for p in request.permissions if not enabled(request) or p.key in before)
    if candidate and enabled(request):
        # Traverse both graphs independently, then require dispositions for the
        # union. In particular removing an owner/endpoint must not erase it.
        roots.update(legacy_audit_keys(request, before))
        for key, old in original_records.items():
            if key in records and records[key] != old:
                roots.update(domain_dependencies(source.split_key(key)[0], old, original_records))
                if key.startswith('evidence_steps/'):
                    roots.update(source.key('claims', cid) for cid in old.get('produced_claim_ids', []))
    owners = set()
    for key in roots:
        if key.startswith("evidence_steps/"):
            owners.update(source.key("claims", cid) for cid in records.get(key, {}).get("produced_claim_ids", []))
    roots.update(owners)
    # Exact named Claim/Evidence/fragment references, not provenance or all
    # Claims that happen to be members of the same CVP.
    related = {}
    endpoints_by_relation = {}
    for key, payload in records.items():
        collection, _ = source.split_key(key)
        if collection in CLAIM_COLLECTIONS:
            for dependency in domain_dependencies(collection, payload, records):
                related.setdefault(dependency, set()).add(key)
        if collection in RELATION_COLLECTIONS:
            endpoints = domain_dependencies(collection, payload, records)
            endpoints_by_relation[key] = endpoints
            for endpoint in endpoints:
                related.setdefault(endpoint, set()).add(key)
    todo = list(roots)
    seen = set()
    while todo:
        key = todo.pop()
        if key in seen:
            continue
        seen.add(key)
        payload = records.get(key)
        if payload is None:
            raise ValueError("Claim audit denominator has a missing dependency: " + key)
        collection, _ = source.split_key(key)
        refs = domain_dependencies(collection, payload, records)
        refs.update(related.get(key, ()))
        refs.update(endpoints_by_relation.get(key, ()))
        if collection == "claims":
            refs.update(source.key("source_documents", sid) for sid in own_source_ids(payload, records))
        if collection == "evidence_steps":
            refs.update(source.key("claims", cid) for cid in payload.get("produced_claim_ids", []))
        if collection == "source_fragments" and payload.get("source_id"):
            refs.add(source.key("source_documents", payload["source_id"]))
        todo.extend(refs - seen)
    return sorted(seen)


def unlinkable_questions(request, before):
    """Questions an Update reads but could not point at a Claim it creates.

    2017 NYSC 4 created 「人子或人的形象代表神」 as its own Claim; Q019 asks exactly
    that and was answered by the professor in those words, but the request had
    no answer_claim_ids grant, so the reviewer could only note the gap.
    """
    if request.operation != 'Update' or not any(p.collection == 'claims' for p in request.creation_permissions):
        return []
    granted = {p.key for p in request.permissions if 'answer_claim_ids' in p.fields}
    read = set(before)
    from backend.pipeline.claim_joint_update import enabled
    if enabled(request):
        # A joint Update reviews only its targets' closure; a question read from
        # elsewhere in the sermon is outside it, and a grant on it would be
        # refused as unreachable (#459: 30 questions, none in CL003's closure).
        read = set(audit_keys(request.model_copy(deep=True, update={'permissions': []}), before))
    return sorted(k for k, v in before.items() if k in read and k.startswith('questions/') and not v['retired'] and k not in granted)


def original_goal(request, before):
    missing = unlinkable_questions(request, before)
    if missing:
        raise ValueError('Claim creation requires answer_claim_ids on every question it reads: ' + ', '.join(missing))
    original_keys = set(audit_keys(request, before)) | {source.key('source_documents', sid) for sid in request.source_bindings}
    result = {
        "operation": request.operation, "target_keys": list(request.target_keys),
        "source_bindings": {k: v.model_dump() for k, v in request.source_bindings.items()},
        "original_objects": {k: model_state(before[k]) for k in sorted(original_keys)},
        "required_questions": sorted(IMPACT_QUESTIONS if is_source_impact(request) else QUESTIONS[request.operation]),
        "units": {"CMU-" + sha256_json(keys)[:20]: keys for keys in partition_targets(request, before)},
    }

    # A maintenance request needs its actual question, not merely a target
    # identity and write grants. Authority bytes are frozen independently;
    # old actions without questions keep their original packet exactly.
    if Path(request.authorization.path).is_file():
        authority = json.loads(source.verify_file(request.authorization).read_text())
        if 'review_questions' in authority:
            questions = authority['review_questions']
            if (not isinstance(questions, list) or not questions
                    or any(not isinstance(q, str) or not q.strip() for q in questions)
                    or len(questions) != len(set(questions))):
                raise ValueError('explicit maintenance review_questions require distinct nonempty questions')
            result['review_questions'] = list(questions)
        policy = json.loads(source.verify_file(request.policy).read_text()) if Path(request.policy.path).is_file() else {}
        if policy.get('model_output_contract') == 'claim_changes_v4':
            allocations = authority.get('claim_creation_allocations', {})
            if (not isinstance(allocations, dict) or len(allocations) > 1
                    or any(not isinstance(cid, str) or not cid for cid in allocations.values())
                    or set(allocations) != {p.source_id for p in request.creation_permissions}
                    or any(source.key('claims', cid) in before for cid in allocations.values())):
                raise ValueError('compact v4 requires one program-reserved identity per granted own source')
            result['claim_creation_allocations'] = dict(allocations)
            result['target_disposition_contract'] = 'reviewed_update_or_retirement_v1'
    from backend.pipeline.claim_evidence_review import enabled as evidence_enabled, QUESTIONS as evidence_questions
    if evidence_enabled(request):
        result['required_questions'] = sorted(set(result['required_questions']) | evidence_questions)
    from backend.pipeline.claim_joint_update import enabled, required_keys, CONTRACT
    if enabled(request):
        result.update(joint_update_contract=CONTRACT, required_maintenance_keys=required_keys(request, strict=False))
    return result


def unit_goal(goal, request):
    """Derive one unit's obligations from the action's single saved goal."""
    result = {k: deepcopy(v) for k, v in goal.items() if k not in {'units','original_objects','source_bindings'}}
    result['target_keys'] = list(request.target_keys)
    result['source_bindings'] = {sid: bound.model_dump(mode='json') for sid, bound in request.source_bindings.items()}
    keys = set(audit_keys(request, goal['original_objects'])) | {
        source.key('source_documents', sid) for sid in request.source_bindings}
    result['original_objects'] = {k: deepcopy(v) for k, v in goal['original_objects'].items() if k in keys}
    if result.get('source_transition_context'):
        context = result['source_transition_context']
        context['histories'] = {sid: h for sid, h in context['histories'].items() if sid in request.source_bindings}
    return result


def model_state(state):
    """Only exact current row facts; SQL timestamps are not model evidence."""
    return {'revision': state['revision'], 'content_sha256': state['content_sha256'],
            'retired': bool(state.get('retired') or state.get('retired_at')), 'payload': deepcopy(state['payload'])}


def source_documents(before):
    return {source.split_key(k)[1]: v['payload'] for k, v in before.items() if k.startswith('source_documents/')}


def partition_targets(request, before):
    """Group only coordinated writes; sharing a source/read pin is not a join."""
    if is_source_impact(request):
        policy = Policy.model_validate_json(source.verify_file(request.policy).read_text())
        payloads = {k: v['payload'] for k, v in before.items()}
        groups = {}
        for key in request.target_keys:
            groups.setdefault(tuple(sorted(own_source_ids(payloads[key], payloads))), []).append(key)
        size = policy.source_impact_batch_size
        return [keys[i:i + (size or len(keys))] for keys in groups.values()
                for i in range(0, len(keys), size or len(keys))]
    if request.operation in {"Create", "Split", "Merge"}:
        return [list(request.target_keys)]
    from backend.pipeline.claim_joint_update import enabled
    if request.operation == 'Update' and enabled(request):
        return [list(request.target_keys)]
    targets = set(request.target_keys)
    parent = {k: k for k in targets}

    def root(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def join(keys):
        keys = sorted(set(keys) & targets)
        for key in keys[1:]:
            parent[root(key)] = root(keys[0])

    for permission in request.permissions:
        payload = before.get(permission.key, {}).get("payload", {})
        if permission.collection == "evidence_steps":
            join(source.key("claims", cid) for cid in payload.get("produced_claim_ids", []))
        elif permission.collection in {"claim_relations", "claim_relation_constraints", "knowledge_relations"}:
            join(source.key("claims", payload[k]) for k in ("from_id", "to_id", "source_id", "target_id", "from_claim_id", "to_claim_id") if payload.get(k))
    # New shared Evidence/fragment/relationship writes can coordinate owners
    # which were disjoint in the old graph. The original source-scoped create
    # grants already authorize that scope; group these targets before calling.
    payloads = {k: v['payload'] for k, v in before.items()}
    for grant in request.creation_permissions:
        join(k for k in targets if grant.source_id in own_source_ids(payloads[k], payloads))
    groups = {}
    for key in sorted(targets):
        groups.setdefault(root(key), []).append(key)
    return list(groups.values())


def existing_candidate(before, *, replacements=None):
    """Real existing content enters the same review stage without fake origin."""
    return Candidate(records=[], dispositions=[ObjectDisposition(record_key=k, action="retain",
                     reason="Review the actual existing SQL candidate") for k in sorted(before)],
                     replacements=replacements or {})


def goal_packet(goal):
    """Unit semantic obligations, without unrelated objects or proof opinions."""
    packet = deepcopy(goal)
    packet.pop('units', None)
    return packet


def model_packet(materials):
    packet = {k: deepcopy(v) for k, v in materials.items()
              if k not in {'transaction_before', 'source_archives', 'audit_contract', 'candidate_identity_recovery', 'human_source_decisions'}}
    packet['original_goal'] = goal_packet(materials['original_goal'])
    from backend.pipeline.claim_direct_scope import enabled, scope
    request = Request.model_validate(materials.get('unit_request', materials['request']))
    if enabled(request):
        packet['claim_scope'] = scope(request, materials['before'])
    packet['action_obligations'] = {'operation': materials['request']['operation'],
        'target_keys': list(materials['request']['target_keys']),
        'required_questions': list(materials['original_goal']['required_questions'])}
    if materials.get('packet_profile') in {'claim_single_record_table_v1', 'claim_single_record_table_v2'}:
        before = packet['before']
        def references(goal):
            goal['original_objects'] = {key:({'__claim_record_ref__':key} if before.get(key)==state else state)
                for key,state in goal['original_objects'].items()}
        references(packet['original_goal'])
        if packet.get('continuation'):
            references(packet['continuation']['original_goal'])
            packet['continuation']['prior_call_reservations'] = {key:{k:slot[k] for k in ('role','state','round_id')}
                for key,slot in packet['continuation']['prior_call_reservations'].items()}
        packet['record_reference_contract'] = (
            'Each {"__claim_record_ref__": key} in an original_objects table denotes exactly packet.before[key], '
            'including its complete payload and version. Expand it when comparing original obligations; '
            'do not substitute candidate postimages. Source text and every current record are complete. '
            'Retain dispositions and mechanical fragment verdicts may use concise reasons; do not repeat whole source paragraphs. '
            'Findings and actual modifications still require concrete exact citations and full explanations.')
        # Verify the projection carries every original record unchanged.
        for projected, original in [(packet['original_goal'],materials['original_goal'])] + (
                [(packet['continuation']['original_goal'],materials['continuation']['original_goal'])] if packet.get('continuation') else []):
            restored = {key:(before[state['__claim_record_ref__']] if set(state)=={'__claim_record_ref__'} else state)
                for key,state in projected['original_objects'].items()}
            if restored != original['original_objects']:
                raise ValueError('Claim record-table projection changed original facts')
        if materials['packet_profile'] == 'claim_single_record_table_v2':
            requests = [packet['request']]
            if packet['unit_request'] == packet['request']:
                packet['unit_request'] = {'__claim_request_ref__': 'request'}
            else:
                requests.append(packet['unit_request'])
            counts = Counter(tuple(grant['fields']) for request in requests for grant in request['permissions'])
            shared = {fields: 'pf' + str(i) for i,fields in enumerate(sorted(fields for fields,n in counts.items() if n > 1))}
            packet['permission_fields'] = {ref:list(fields) for fields,ref in shared.items()}
            for request in requests:
                for grant in request['permissions']:
                    fields = tuple(grant['fields'])
                    if fields in shared:
                        grant['fields'] = {'__claim_fields_ref__':shared[fields]}
            packet['record_reference_contract'] += (
                ' Each {"__claim_request_ref__":"request"} denotes the complete packet.request. '
                ' In permissions, {"__claim_fields_ref__":id} denotes exactly packet.permission_fields[id]. '
                'Every target, object, field, source, policy and authorization binding remains authoritative.')
            for name in ('request','unit_request'):
                restored = deepcopy(packet['request'] if packet[name] == {'__claim_request_ref__':'request'} else packet[name])
                for grant in restored['permissions']:
                    if isinstance(grant['fields'],dict):
                        grant['fields'] = packet['permission_fields'][grant['fields']['__claim_fields_ref__']]
                if restored != materials[name]:
                    raise ValueError('Claim request reference changed authority')
    from backend.pipeline.claim_evidence_review import add_packet
    return add_packet(packet)


def required_roles(request, round_number, policy=None):
    if request.operation == 'Read':
        return []
    roles = ['opus']
    if ((policy is None or policy.independent_audit_required)
            and (request.operation != 'Review' or request.confirm_current or request.source_transition)):
        roles.append('gemini')
    adjudicating = bool(policy and policy.all_findings_to_gpt and request.operation == 'Review' and round_number)
    proposal = bool(policy and policy.initial_candidate == 'proposal' and request.operation == 'Update')
    if adjudicating or proposal or request.operation not in {'Review','Read'} and (request.operation != 'Update' or round_number
            or request.predecessor_action_id and not request.source_transition
            and (policy is None or policy.initial_candidate != 'existing')):
        roles.insert(0,'gpt')
    return roles


def candidate_state(before, candidate):
    """Progress is the semantic post-state, not disposition/reason formatting."""
    def semantic(key, payload):
        result = source._semantic(payload)
        return {k: v for k, v in result.items() if not key.startswith('source_fragments/') or k not in FRAGMENT_HASH_FIELDS}
    records = {k: {"payload": semantic(k, v["payload"]), "retired": bool(v["retired"])}
               for k, v in before.items()}
    proposed = source.index(candidate.records)
    for d in candidate.dispositions:
        if d.action == "retain":
            continue
        payload = proposed.get(d.record_key, before.get(d.record_key, {}).get("payload", {}))
        records[d.record_key] = {"payload": semantic(d.record_key, payload),
                                  "retired": d.action == "retire"}
    return sha256_json({"records": records, "replacements": candidate.replacements})


def proposer_reported_finding_ids(findings, candidate):
    """Already stated by GPT, retained as unresolved by independent review.

    This only avoids asking GPT to restate its own exact objection before a
    permission pause. It resolves nothing and creates no approval or budget.
    Match the complete original report, not merely its stable identity.
    """
    reports = [f.model_dump(mode='json') for f in candidate.findings]
    return {fid for fid, finding in findings.items() if finding in reports}


def adjudication_outcome(findings, candidate):
    """New objections go to GPT before permission/source escalation."""
    decisions = {r.finding_id: r for r in candidate.finding_resolutions or []}
    if set(findings) - set(decisions) - proposer_reported_finding_ids(findings, candidate):
        return 'revision_required'
    if any(r.disposition == 'resolved' for r in decisions.values()
           if r.finding_id in findings):
        return 'disagreement'
    return failure_reason(findings)


def failure_reason(findings):
    if any(f.get("category") == "source_uncertain" for f in findings.values()):
        return "human_required"
    if any(f.get("category") == "engineering" for f in findings.values()):
        return "engineering_required"
    return "revision_required"


def unaccepted_initial_review(previous, key, slot, proof):
    """Authenticate a returned initial review without accepting its judgement."""
    from backend.pipeline.source_reconciliation_runner import read, body, binding, validate_bound_call
    from backend.pipeline.source_reconciliation_runtime import claim_management_model_adapter
    from backend.pipeline.viewpoint_semantic_contract import semantic_prompt
    from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
    from backend.pipeline.cvp_sql_actions import claim_management_review_packet, validate_claim_candidate_origin
    if set(proof) != {'raw','role_prompt'} or slot['role'] != 'opus' or slot['state'] != 'started':
        raise ValueError('unaccepted review proof requires one returned initial Opus call')
    root = Path(previous['execution_root']) / slot['round_id']
    if proof['raw'] != binding(root/'opus.raw.json'):
        raise ValueError('unaccepted review raw belongs to a different original call')
    raw_path = source.verify_file(source.FileBinding.model_validate(proof['raw']))
    prompt_path = source.verify_file(source.FileBinding.model_validate(proof['role_prompt']))
    contract = previous['audit_contract']
    if proof['role_prompt']['file_sha256'] != contract['implementation']['backend/pipeline/prompts/claim_management_review_v2.md']:
        raise ValueError('unaccepted review prompt differs from its frozen implementation')
    materials = body(read(root/'materials.json'))
    policy = Policy.model_validate(previous['policy'])
    if (materials['request'] != previous['request'] or materials['policy'] != previous['policy']
            or materials['audit_contract'] != contract or materials['round_id'] != slot['round_id']
            or materials['round_number'] != 0 or materials['roles'] != ['opus']
            or key != slot['round_id'] + ':opus'):
        raise ValueError('unaccepted review is not the unchanged initial Opus-only Update')
    intent = body(read(root/'opus.reservation.json'))
    if intent != slot['intent'] or sha256_json(intent) != slot['intent_sha256']:
        raise ValueError('unaccepted review original reservation drift')
    candidate = Candidate.model_validate(body(read(root/'candidate.json')))
    validate_claim_candidate_origin(root, materials, policy, materials['sources'], candidate)
    base = claim_management_model_adapter('opus', policy)
    adapter = StructuredJsonReviewerAdapter(client=base._client, prompt=semantic_prompt(prompt_path.read_text()),
        response_model=Review, schema_name='claim_management_review_v2', compact_payload=True,
        shared_text_payload=True, shared_record_payload=True)
    adapter.max_request_bytes = policy.budget.max_request_bytes
    packet = claim_management_review_packet(materials, candidate)
    raw = validate_bound_call(root, 'opus', packet, adapter)
    review = Review.model_validate(raw['response'])
    try:
        validate_review(candidate, review, packet['denominator'], packet['required_questions'], materials['unresolved'],
            materials['sources'], request=Request.model_validate(materials['unit_request']), records=materials['before'])
    except ValueError as exc:
        rejection = str(exc)
        allowed = ('review denominator omitted/repeated', 'review did not cover', 'review must reconcile',
            'review repeats a stable finding', 'rejected objects require concrete', 'finding contradicts an approved object')
        if not rejection.startswith(allowed):
            raise ValueError('unaccepted review is not a proved response contract failure') from exc
    else:
        raise ValueError('a valid review must use its ordinary recorded stage, not engineering continuation')
    return {'raw':proof['raw'], 'role_prompt':proof['role_prompt'], 'rejection':rejection,
        'review':review.model_dump(mode='json'), 'review_not_accepted':True, 'generation_consumed':True}



def unaccepted_initial_proposal(previous, key, slot, proof):
    """Account for one returned, uncommitted proposal with invalid coverage.

    This authenticates a spent generation, never accepts a partial candidate.
    The existing continuation retains the scope and only the unspent budget.
    """
    from backend.pipeline.source_reconciliation_runner import read, body, binding, validate_bound_call
    from backend.pipeline.source_reconciliation_runtime import claim_management_model_adapter
    if (set(proof) != {'raw','role_prompt'} or slot['role'] != 'gpt' or slot['state'] != 'started'
            or previous['result'].get('reason') != 'engineering_required'
            or previous['request']['operation'] != 'Update' or len(previous['latest_rounds']) != 1
            or slot['round_id'] not in previous['latest_rounds'].values()):
        raise ValueError('unaccepted proposal requires one returned initial Update generation')
    root = Path(previous['execution_root']) / slot['round_id']
    if proof['raw'] != binding(root/'gpt.raw.json'):
        raise ValueError('unaccepted proposal raw belongs to another call')
    source.verify_file(source.FileBinding.model_validate(proof['role_prompt']))
    prompt_key = 'backend/pipeline/prompts/claim_management_proposal_v2.md'
    expected_prompt = previous['audit_contract']['implementation'][prompt_key]
    if (proof['role_prompt']['file_sha256'] != expected_prompt
            or hashlib.sha256((Path(__file__).parents[2]/prompt_key).read_bytes()).hexdigest() != expected_prompt):
        raise ValueError('unaccepted proposal prompt differs from its frozen instructions')
    materials = body(read(root/'materials.json'))
    if (materials['request'] != previous['request'] or materials['policy'] != previous['policy']
            or materials['audit_contract'] != previous['audit_contract'] or materials['round_number'] != 0
            or materials['round_id'] != slot['round_id'] or 'gpt' not in materials['roles']
            or key != slot['round_id'] + ':gpt' or (root/'candidate-result.json').exists()
            or any(k != key and v['state'] not in {'planned','cancelled'} for k,v in previous['call_reservations'].items())):
        raise ValueError('unaccepted proposal is not an unchanged uncommitted initial candidate')
    intent = body(read(root/'gpt.reservation.json'))
    if intent != slot['intent'] or sha256_json(intent) != slot['intent_sha256']:
        raise ValueError('unaccepted proposal reservation drift')
    policy = Policy.model_validate(previous['policy'])
    raw = validate_bound_call(root,'gpt',model_packet(materials),claim_management_model_adapter('gpt',policy))
    candidate, _ = normalize_candidate(raw['response'],materials['sources'])
    try:
        validate_candidate(Request.model_validate(materials['unit_request']),materials['before'],candidate,materials['sources'],
            created_keys=materials['created_keys'],original_replacements=materials['replacements'])
    except ValueError as exc:
        rejection = str(exc)
        if rejection not in {'candidate omitted/added a current dependency disposition','candidate repeats an object disposition'}:
            raise ValueError('unaccepted proposal is not a proved exact-once coverage failure') from exc
    else:
        raise ValueError('valid candidate must use its ordinary recorded stage')
    for finding in candidate.findings:
        if finding.target_key not in materials['before']:
            raise ValueError('unaccepted proposal finding is outside its frozen scope')
        validate_citations(finding.citations,materials['sources'])
    return {'raw':proof['raw'],'role_prompt':proof['role_prompt'],'rejection':rejection,
        'candidate_sha256':candidate.digest,'findings':[f.model_dump(mode='json') for f in candidate.findings],
        'proposal_not_accepted':True,'generation_consumed':True}


def human_source_follow_up(previous, request, authority, prior_findings):
    """New human source evidence authorizes one bounded, linked follow-up.

    This does not replenish the predecessor's ledger or approve any candidate.
    """
    bound = authority.get('human_source_verification')
    if not bound:
        return None
    verification = json.loads(source.verify_file(source.FileBinding.model_validate(bound)).read_text())
    old = Request.model_validate(previous['request'])
    used = set(previous.get('continuation', {}).get('human_verified_finding_ids', []))
    answers = verification.get('answers', [])
    ids = [answer.get('finding_id') for answer in answers]
    if (verification.get('schema_version') != 'wang_claim_human_source_verification_v1'
            or verification.get('verified_by') != 'user'
            or verification.get('human_content_approval') is not False
            or verification.get('predecessor_action_id') != old.action_id
            or verification.get('source_bindings') != old.model_dump(mode='json')['source_bindings']
            or not verification.get('user_confirmation')
            or previous['result'].get('reason') != 'human_required'
            or request.permissions != old.permissions
            or not ids or len(set(ids)) != len(ids) or set(ids) & used):
        raise ValueError('human source follow-up must bind new source evidence to the paused original scope')
    for answer in answers:
        finding = prior_findings.get(answer['finding_id'])
        if (not finding or finding['category'] != 'source_uncertain'
                or answer.get('question') != finding.get('human_question')
                or not answer.get('answer')
                or answer.get('citation') not in finding['citations']):
            raise ValueError('human source answer does not bind an unresolved source question')
    for binding in old.source_bindings.values():
        source.verify_file(binding)
    return {'artifact':bound, 'record':verification, 'finding_ids':ids,
            'human_verified_finding_ids':sorted(used | set(ids))}


CAPACITY_REJECTION = 'Selected model is at capacity. Please try a different model.'


def pre_generation_rejection(raw):
    """Which provider refusal, if any, proves the call generated nothing.

    Two refusals qualify: the HTTP 400 schema rejection, and the subscription
    capacity refusal seen twice on 2026-10-23 (「Selected model is at
    capacity」), returned within seconds with no stdout and no last message.
    Anything partial, mixed or unrecognised stays an uncertain call.
    """
    if raw.get('last_message') is not None or raw.get('stdout'):
        return None
    kinds = set()
    for line in raw.get('stderr', '').splitlines():
        if not line.startswith('ERROR: '):
            continue
        text = line[7:]
        if text.strip() == CAPACITY_REJECTION:
            kinds.add('capacity')
            continue
        try:
            error = json.loads(text)
        except ValueError:
            return None
        if (not isinstance(error, dict) or error.get('status') != 400
                or error.get('error', {}).get('code') != 'invalid_json_schema'
                or error.get('error', {}).get('param') != 'text.format.schema'):
            return None
        kinds.add('schema')
    return kinds.pop() if len(kinds) == 1 else None


def continuation_budget(previous, request, policy):
    """One explicitly authorized successor carries history and unspent budget.

    A bound human source answer can instead authorize one new proposal/review
    pair in a linked action; the predecessor's spent budget remains unchanged.
    A proven HTTP 400 schema rejection or capacity refusal generated nothing.
    Its failed attempt remains in the predecessor ledger and is never
    represented as a completion. A successor may carry the predecessor's exact
    source-scoped creation grants -- a sibling-Claim repair paused before any
    commit -- but never new grants, and nothing it already created.
    Unknown calls require a separately bound explicit owner call extension;
    they cannot use the ordinary engineering-input continuation. Completed
    writes cannot be replayed. An explicitly bound qualification refresh instead
    reviews the current SQL candidate while retaining the original history.
    """
    from backend.pipeline.source_reconciliation_runner import read, body
    old = Request.model_validate(previous['request'])
    original = Policy.model_validate(previous['policy'])
    from backend.pipeline.claim_joint_update import CONTRACT, enabled, required_keys, finding_permission_extension
    authority = json.loads(source.verify_file(request.authorization).read_text())
    extension = authority.get('owner_call_extension')
    read_only_refresh = bool(isinstance(extension,dict) and extension.get('basis') == 'qualification_refresh' and not request.permissions)
    if previous['original_goal'].get('joint_update_contract') == CONTRACT and (
            not enabled(request, policy)
            or required_keys(request) != previous['original_goal']['required_maintenance_keys']
            or (request.permissions != old.permissions and not read_only_refresh
                and not finding_permission_extension(previous, old, request))
            or policy.joint_update_max_targets != original.joint_update_max_targets
            or len(previous['original_goal']['units']) != 1):
        raise ValueError('joint Update continuation must retain one complete original unit, required maintenance and authority')
    if (previous.get('kind') != 'claim_management_action' or previous['result']['status'] not in {'paused','completed'}
            or old.operation != 'Update' or request.operation != 'Update'
            or old.target_keys != request.target_keys or old.source_bindings != request.source_bindings
            or (request.creation_permissions != old.creation_permissions
                and not (read_only_refresh and not request.creation_permissions))
            or previous.get('created_keys') or previous.get('replacements')):
        raise ValueError('engineering continuation must retain original Update targets and source without structural writes')
    extension_basis = extension.get('basis', 'unknown_call') if isinstance(extension, dict) else None
    permission_follow_up = (extension_basis == 'permission_required'
        and previous['result'].get('reason') == 'permission_required'
        and finding_permission_extension(previous, old, request))
    qualification_refresh = extension_basis == 'qualification_refresh'
    completed_cleanup = previous['result']['status'] == 'completed' and not qualification_refresh
    old_permissions = {p.key:set(p.fields) for p in old.permissions}
    permissions = {p.key:set(p.fields) for p in request.permissions}
    if completed_cleanup and (not previous.get('context_findings') or previous.get('findings')
            or not permissions or not set(permissions) <= {f['target_key'] for f in previous['context_findings'].values()}
            or set(permissions) & set(request.target_keys)):
        raise ValueError('completed follow-up must address only its saved context issues without rewriting the completed Claims')
    if not completed_cleanup and not read_only_refresh and any(not fields <= permissions.get(key,set()) for key,fields in old_permissions.items()):
        raise ValueError('continuation cannot discard original write obligations')
    spent = {role:0 for role in MODELS}
    failures = {}
    unaccepted = {}
    prior_findings = {**deepcopy(previous.get('context_findings', {})), **deepcopy(previous['findings'])}
    human_follow_up = human_source_follow_up(previous, request, authority, prior_findings)
    unknown = {}
    if extension is not None:
        extension_keys = {'predecessor_action_id','prior_metadata_sha256','budget','authorization_quote'}
        if extension_basis != 'unknown_call':
            extension_keys |= {'basis','current_objects_sha256','related_action_metadata_sha256'}
        valid_basis = (extension_basis == 'unknown_call' and previous['result'].get('reason') == 'call_uncertain'
            or permission_follow_up
            or extension_basis == 'remaining_findings' and previous['result'].get('reason') == 'budget_exhausted' and bool(previous['findings'])
            or qualification_refresh and previous['result']['status'] == 'completed' and bool(previous.get('context_findings'))
            or read_only_refresh and previous['result'].get('reason') == 'disagreement'
                and bool(previous['findings'])
                and all(f.get('affected_target_keys') == [] and f['target_key'] not in set(old.target_keys) | set(previous['original_goal'].get('required_maintenance_keys',[])) for f in previous['findings'].values()))
        if (not isinstance(extension,dict) or set(extension) != extension_keys or not valid_basis
                or extension['predecessor_action_id'] != old.action_id
                or extension['prior_metadata_sha256'] != sha256_json(previous)
                or extension['budget'] != policy.budget.model_dump(mode='json')
                or not (extension['authorization_quote'] or '').strip()
                or authority.get('authorized_by') != 'owner' or authority.get('human_content_approval') is not False
                or completed_cleanup or human_follow_up
                or (request.permissions != old.permissions and not read_only_refresh and not permission_follow_up) or policy.initial_candidate != 'proposal'
                or policy.all_findings_to_gpt != original.all_findings_to_gpt):
            raise ValueError('owner call extension requires exact paused action, authority and explicit new budget')
        if permission_follow_up and (policy.budget.gpt_calls != 1 or policy.budget.opus_calls != 1
                or policy.budget.gemini_calls != int(policy.independent_audit_required)
                or policy.budget.max_revisions != 1):
            raise ValueError('permission follow-up permits exactly one bounded proposal and review')
    recovery = authority.get('unaccepted_review_recovery', {})
    proposal_recovery = authority.get('unaccepted_proposal_recovery', {})
    if not isinstance(proposal_recovery,dict):
        raise ValueError('unaccepted proposal recovery must bind exact returned calls')
    unaccepted_proposals = {}
    root = Path(previous['execution_root'])
    for key, slot in previous['call_reservations'].items():
        if slot['state'] in {'planned','cancelled'}:
            continue
        if slot['state'] == 'completed':
            spent[slot['role']] += 1
            continue
        from backend.pipeline.claim_review_recovery import consumed_invalid_review
        if consumed_invalid_review(previous, key, slot):
            spent[slot['role']] += 1
            continue
        if key in recovery:
            value = unaccepted_initial_review(previous, key, slot, recovery[key])
            unaccepted[key] = value
            spent[slot['role']] += 1
            for finding in Review.model_validate(value['review']).findings:
                prior_findings[finding.identity(old.action_id)] = finding.model_dump(mode='json')
            continue
        if key in proposal_recovery:
            value = unaccepted_initial_proposal(previous,key,slot,proposal_recovery[key])
            unaccepted_proposals[key] = value
            spent[slot['role']] += 1
            for finding in value['findings']:
                finding = Finding.model_validate(finding)
                prior_findings[finding.identity(old.action_id)] = finding.model_dump(mode='json')
            continue
        if extension is not None:
            if extension_basis != 'unknown_call':
                raise ValueError('remaining-findings/qualification follow-up cannot discard an uncertain call')
            from backend.pipeline.cvp_sql_commit import binding
            stage = root / slot['round_id']
            intent = stage / (slot['role'] + '.reservation.json')
            if (slot['state'] != 'started' or slot['role'] != 'gpt'
                    or not intent.is_file() or body(read(intent)) != slot.get('intent')
                    or sha256_json(slot['intent']) != slot.get('intent_sha256')
                    or (stage/'gpt.raw.json').exists() or (stage/'gpt.transport/exit.json').exists()):
                raise ValueError('owner extension must preserve an exact unknown GPT call, not discard a returned result')
            unknown[key] = {'reservation':binding(intent),'intent_sha256':slot['intent_sha256'],'state':'started'}
            spent[slot['role']] += 1
            continue
        failure_path = root / slot['round_id'] / (slot['role'] + '.failure.json')
        if slot['role'] != 'gpt' or not failure_path.is_file() or (failure_path.parent / 'gpt.raw.json').exists():
            raise ValueError('uncertain original call cannot be repeated in a continuation')
        failure = body(read(failure_path))
        kind = pre_generation_rejection(failure.get('provider_raw') or {})
        if kind is None:
            raise ValueError('not a proven pre-generation Claim schema or capacity rejection')
        failures[key] = {'path':str(failure_path), 'file_sha256':hashlib.sha256(failure_path.read_bytes()).hexdigest(),
                         **({'kind': kind} if kind != 'schema' else {})}
    if set(recovery) != set(unaccepted):
        raise ValueError('unaccepted recovery contains an unbound or already recorded call')
    if set(proposal_recovery) != set(unaccepted_proposals):
        raise ValueError('unaccepted proposal recovery contains an unbound or already recorded call')
    remaining = {role:original.budget.limit(role)-spent[role] for role in MODELS}
    if extension_basis == 'unknown_call' and not unknown:
        raise ValueError('owner unknown-call extension requires an actual unknown generation')
    if (policy.models != original.models or policy.independent_audit != original.independent_audit
            or policy.review_purpose != original.review_purpose
            or (not human_follow_up and extension is None and any(policy.budget.limit(role) > remaining[role] for role in MODELS))
            or (not human_follow_up and extension is None and policy.budget.max_revisions > original.budget.max_revisions - sum(previous['revision_rounds'].values()))
            or policy.budget.max_request_bytes > original.budget.max_request_bytes
            or policy.budget.max_output_tokens > original.budget.max_output_tokens):
        raise ValueError('continuation cannot reset or increase original Claim budget or change review policy')
    if human_follow_up and (policy.initial_candidate != 'proposal'
            or policy.all_findings_to_gpt != original.all_findings_to_gpt
            or policy.budget.gpt_calls != 1 or policy.budget.opus_calls != 1
            or policy.budget.gemini_calls != int(policy.independent_audit_required)
            or policy.budget.max_revisions != 1):
        raise ValueError('human source follow-up permits exactly one bounded proposal and review')
    if not policy.budget.gpt_calls or not policy.budget.opus_calls:
        raise ValueError('original remaining budget cannot fund correction and review')
    return {'predecessor_action_id':old.action_id, 'completed_context_cleanup':completed_cleanup, 'qualification_refresh':qualification_refresh, 'read_only_refresh':read_only_refresh, 'original_goal':deepcopy(previous['original_goal']),
        'prior_findings':prior_findings, 'unaccepted_reviews':unaccepted, 'prior_call_reservations':deepcopy(previous['call_reservations']),
        'pre_generation_schema_rejections':failures, 'spent_generations':spent, 'remaining_generations':remaining,
        **({'unaccepted_proposals':unaccepted_proposals} if unaccepted_proposals else {}),
        **({'owner_call_extension':deepcopy(extension),'preserved_unknown_calls':unknown,
            'budget_basis':'explicit_owner_call_extension','follow_up_budget':policy.budget.model_dump(mode='json')} if extension is not None else {}),
        **({'human_source_verification':human_follow_up,
            'human_verified_finding_ids':human_follow_up['human_verified_finding_ids'],
            'budget_basis':'new_human_source_evidence_follow_up', 'follow_up_budget':policy.budget.model_dump()} if human_follow_up else {})}


def inherited_readonly_context(value):
    """Keep prior nonblocking notes outside a read-only successor's scope.

    No issue is resolved or rejudged here. In-scope, changed or newly blocking
    findings still need this action's own GPT/Opus adjudication.
    """
    continuation = value.get('continuation', {})
    request = value['request']
    if (not continuation.get('read_only_refresh') or request['permissions']
            or request['creation_permissions'] or value.get('created_keys')):
        return {}
    prior = continuation.get('prior_findings', {})
    scope = set(value['original_goal']['original_objects'])
    result = {}
    for fid, finding in value['findings'].items():
        if (finding == prior.get(fid) and finding['target_key'] not in scope
                and finding.get('affected_target_keys') == []
                and finding.get('affected_write_keys') == []
                and (finding.get('impact_reason') or '').strip()):
            result[fid] = finding
    return result


def operation_questions(goal, candidate):
    if set(goal['required_questions']) == IMPACT_QUESTIONS:
        return sorted(IMPACT_QUESTIONS)
    questions = set(goal['required_questions']) | QUESTIONS[candidate.operation or goal['operation']]
    for disposition in candidate.dispositions:
        operation = {'create': 'Create', 'retire': 'Retire', 'restore': 'Restore', 'update': 'Update'}.get(disposition.action)
        if operation and disposition.record_key.startswith('claims/'):
            questions |= QUESTIONS[operation]
    return sorted(questions)


def require_full_sources(request, before, candidate=None, *, qualification=False):
    records = {k: v['payload'] for k, v in before.items()}
    if candidate:
        records.update(source.index(candidate.records))
    from backend.pipeline.claim_direct_scope import enabled, scope
    neighbours = set(scope(request, before, candidate)['neighbour_claim_keys']) if enabled(request) else set()
    for key in audit_keys(request, before, candidate):
        if not key.startswith('claims/') or key in neighbours:
            continue
        own = own_source_ids(records[key], records)
        if not own or not own <= set(request.source_bindings):
            raise ValueError('Claim review lacks a complete own source for a required owner')
        if qualification and not before[key].get('retired') and len(own) != 1:
            raise ValueError('current 2.0 qualification requires a single own source; structural repair is required')


def normalize_candidate(raw, sources):
    """Program owns verifiable hashes; never repair source words or judgement.

    The original GPT response stays immutable. This projection changes only
    three derived hash fields of explicitly proposed own-source fragments.
    """
    original = Candidate.model_validate(decode_candidate(raw))
    result = original.model_copy(deep=True)
    changes = []
    for row in result.records:
        if row.collection != 'source_fragments':
            continue
        disposition = next((d for d in result.dispositions if d.record_key == row.key), None)
        if not disposition or disposition.action in {'retain', 'retire'}:
            continue
        payload = row.payload
        snapshot = sources.get(payload.get('source_id'))
        paragraph = (snapshot or {}).get('paragraphs', {}).get(str(payload.get('paragraph_key')))
        excerpt = payload.get('verbatim_excerpt')
        if not snapshot or not paragraph or not excerpt or excerpt not in paragraph['text']:
            raise ValueError('new/updated fragment lacks an exact complete own source paragraph/excerpt')
        derived = {'source_sha256': snapshot['body_sha256'],
                   'paragraph_text_sha256': hashlib.sha256(paragraph['text'].encode()).hexdigest(),
                   'verbatim_excerpt_sha256': hashlib.sha256(excerpt.encode()).hexdigest()}
        for field, value in derived.items():
            if payload.get(field) != value:
                changes.append({'record_key': row.key, 'field': field, 'before': payload.get(field), 'after': value})
                payload[field] = value
    return result, {'schema_version': VERSION, 'raw_candidate_sha256': original.digest,
                    'candidate_sha256': result.digest, 'changes': changes,
                    'reader_visible_text_unchanged': True, 'semantic_decisions_unchanged': True}


def legacy_ai_supersession(state):
    payload = state.get('payload', {})
    decision = payload.get('ai_adjudication') or {}
    if not isinstance(decision, dict):
        return False
    return (payload.get('review_status') == 'superseded' and bool(payload.get('superseded_by'))
        and decision.get('status') == 'ai_consensus_applied'
        and decision.get('approval_status') == 'not_human_approved' and bool(decision.get('fingerprint')))


def legacy_restore_enabled(request):
    return request.operation == 'Restore' and bool(Policy.model_validate_json(
        source.verify_file(request.policy).read_text()).legacy_ai_supersession_restore)


def normalize_legacy_restorations(candidate, receipt, materials):
    """Project an explicit Restore into an unapproved active postimage.

    This does not choose what to restore or edit teaching. Opus sees the final
    projected candidate; the SQL boundary separately protects human decisions.
    """
    if not materials['policy'].get('legacy_ai_supersession_restore'):
        return candidate, receipt
    request = Request.model_validate(materials['unit_request'])
    if request.operation != 'Restore':
        raise ValueError('legacy restoration projection requires explicit Restore')
    result = candidate.model_copy(deep=True)
    projected = []
    for row in result.records:
        old = materials['before'].get(row.key)
        disposition = next((d for d in result.dispositions if d.record_key == row.key), None)
        if row.collection != 'claims' or not old or not disposition or disposition.action != 'restore' or not legacy_ai_supersession(old):
            continue
        if (row.key not in request.target_keys or row.payload.get('review_status') not in {'superseded', 'candidate'}
                or any(row.payload.get(k) != old['payload'].get(k) for k in source.PROVENANCE - {'review_status'})):
            raise ValueError('legacy Restore must preserve raw provenance and name every restored Claim target')
        row.payload['review_status'] = 'candidate'
        row.payload['superseded_by'] = None
        projected.append(row.key)
    if projected:
        receipt = {**receipt, 'candidate_sha256': result.digest,
            'legacy_ai_supersession_restorations': sorted(projected),
            'restoration_status': 'candidate_not_human_approved'}
    return result, receipt


def goal_satisfied(goal, current, replacements):
    """Mechanical original task obligations survive a later Update round."""
    operation = goal["operation"]
    targets = set(goal["target_keys"])
    if operation in {"Split", "Merge"}:
        if set(replacements) != targets or any(not current.get(k, {}).get("retired") for k in targets):
            raise ValueError("original structural retirement/mapping obligation is unfinished")
        new = set().union(*(set(values) for values in replacements.values()))
        if not new or any(k in targets or not current.get(k) or current[k]["retired"] for k in new):
            raise ValueError("original replacement obligation is not currently active")
        if (operation == "Split" and (len(targets) != 1 or len(new) < 2)
                or operation == "Merge" and (len(targets) < 2 or len(new) != 1)):
            raise ValueError("original structural cardinality is unfinished")
    elif operation == "Retire" and any(not current.get(k, {}).get("retired") for k in targets):
        raise ValueError("original retirement obligation is unfinished")
    elif operation == 'Update' and goal.get('target_disposition_contract') == 'reviewed_update_or_retirement_v1':
        if any(not current.get(k) for k in targets):
            raise ValueError('original Claim disposition is missing from current readback')
        # Joint verify_completion checks each approved disposition against its
        # exact active/retired readback before this action-level coverage check.
    elif operation in {"Update", "Restore", "Review"} and any(not current.get(k) or current[k]["retired"] for k in targets):
        raise ValueError("original active Claim obligation is unfinished")
    if operation == 'Restore':
        for key in targets:
            if legacy_ai_supersession(goal['original_objects'][key]):
                payload = current[key]['payload']
                if payload.get('review_status') in HELD | {'superseded'} or payload.get('superseded_by'):
                    raise ValueError('original legacy supersession is not actually restored')
    return True
