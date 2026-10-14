"""Claim business operations on the existing source/SQL execution boundary.

This is a versioned content contract, not another runner, persistence store or
call ledger. Old Claim issue contracts keep their original identities/budgets.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Literal
import json
import hashlib
from pathlib import Path
from collections import Counter
from difflib import SequenceMatcher

from pydantic import Field, model_validator

from backend.pipeline import source_reconciliation as source
from backend.api.canonical_repository.postgres_store import (
    KNOWLEDGE_COLLECTIONS, sha256_json, canonical_json,
)

VERSION = "wang_claim_management_v2"
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

    @model_validator(mode="after")
    def exact_roles(self):
        if set(self.models) != set(MODELS):
            raise ValueError("all three independent model roles must be bound")
        for role, (model, backend) in MODELS.items():
            if self.models[role] != {"model_id": model, "backend": backend, "effort": "high"}:
                raise ValueError("Claim policy must bind the exact model/backend/effort")
        if self.source_impact_batch_size and self.review_purpose != 'source_impact':
            raise ValueError('source impact batching belongs to its explicitly bound Review policy')
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

    @property
    def digest(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


class FindingResolution(source.Contract):
    finding_id: str
    disposition: Literal["resolved", "persistent", "reopened"]
    reason: str = Field(min_length=1)
    citations: list[Citation] = Field(min_length=1)


class Review(source.Contract):
    proposal_sha256: str
    verdicts: list[source.Verdict]
    findings: list[Finding]
    prior_findings: list[FindingResolution]
    answered_questions: list[str]
    source_impacts: list['SourceImpact'] = Field(default_factory=list)


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


def audit_contract(root):
    """Freeze actual audit instructions/schema and the implementation version."""
    from backend.pipeline.source_reconciliation_runner import archive_source_version, binding
    pipeline = Path(__file__).parent
    repository = pipeline.parents[1]
    prompt = pipeline / 'prompts/claim_management_audit_v2.md'
    files = [Path(__file__), pipeline/'cvp_sql_actions.py', pipeline/'cvp_sql_flow.py',
        pipeline/'source_reconciliation_runner.py', pipeline/'source_reconciliation_runtime.py',
        repository/'backend/api/canonical_repository/postgres_store.py', repository/'scripts/audit-library.py',
        *[pipeline/'prompts'/('claim_management_'+role+'_v2.md') for role in ('proposal','review','audit')]]
    return {'instructions': archive_source_version(source.FileBinding.model_validate(binding(prompt)), Path(root)/'audit-contract'),
        'response_schema': Review.model_json_schema(),
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
            snapshot = (snapshot or {}).get('before')
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
        for key in domain_dependencies(collection, value, records) - seen:
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
            if disposition.action == "restore":
                if not old["retired"] or payload is None:
                    raise ValueError("Restore requires retired current state and full postimage")
            elif old["retired"]:
                raise ValueError("retired object requires explicit Restore")
            if disposition.action == "retire":
                if payload is not None and payload != old["payload"]:
                    raise ValueError("retirement preserves the exact historical payload")
                payload = deepcopy(old["payload"])
            elif payload is None:
                raise ValueError("update/restore requires a complete postimage")
            if any(payload.get(k) != old["payload"].get(k) for k in source.PROVENANCE):
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
        if finding.target_key not in required:
            raise ValueError("finding outside the complete current input")
        validate_citations(finding.citations, sources)
    targets = set(request.target_keys)
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
    return post, writes


def validate_review(candidate, review, expected_keys, questions, unresolved, sources, *, request=None, records=None):
    if review.proposal_sha256 != candidate.digest:
        raise ValueError("review belongs to a different complete candidate")
    keys = [v.record_key for v in review.verdicts]
    if len(keys) != len(set(keys)) or set(keys) != set(expected_keys):
        raise ValueError("review denominator omitted/repeated an object")
    if not set(questions) <= set(review.answered_questions) or len(review.answered_questions) != len(set(review.answered_questions)):
        raise ValueError("review did not cover the original operation requirements")
    resolved = [r.finding_id for r in review.prior_findings]
    if len(resolved) != len(set(resolved)) or set(resolved) != set(unresolved):
        raise ValueError("review must reconcile every prior stable finding")
    for finding in review.findings:
        if finding.target_key not in expected_keys:
            raise ValueError("review finding outside its declared denominator")
        validate_citations(finding.citations, sources)
    for resolution in review.prior_findings:
        validate_citations(resolution.citations, sources)
    rejected = {v.record_key for v in review.verdicts if not v.approved or v.problems}
    explained = {f.target_key for f in review.findings}
    explained.update(unresolved[r.finding_id]["target_key"] for r in review.prior_findings
                     if r.disposition != "resolved")
    if rejected - explained:
        raise ValueError("rejected objects require concrete current or persistent findings")
    if request is not None and is_source_impact(request):
        return validate_source_impacts(review, expected_keys, sources, request=request, records=records, unresolved=unresolved)
    return (all(v.approved and not v.problems for v in review.verdicts)
            and not review.findings
            and all(r.disposition == "resolved" for r in review.prior_findings))


def unresolved_after(action_id, previous, review):
    result = deepcopy(previous)
    for resolution in review.prior_findings:
        if resolution.disposition == "resolved":
            result.pop(resolution.finding_id, None)
    for finding in review.findings:
        result[finding.identity(action_id)] = finding.model_dump(mode="json")
    return result


def load_authority(request: Request, *, source_documents=None, archived_sources=None, selected_source_ids=None):
    """Bind a new action to an actual direction and policy, never old v1 flags."""
    policy = Policy.model_validate_json(source.verify_file(request.policy).read_text())
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
        if declared and declared != snapshot['file']['file_sha256']:
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
    """Claim evidence denominator, without turning CVP context into approval.

    A shared Evidence write expands to every owner. Unchanged CVP memberships
    remain transaction read pins and pending consumers, not Claim audit proofs.
    """
    roots = set(request.target_keys)
    records = {k: v["payload"] for k, v in before.items()}
    if candidate:
        records.update(source.index(candidate.records))
        roots.update(d.record_key for d in candidate.dispositions if d.action != "retain")
    else:
        roots.update(p.key for p in request.permissions)
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


def original_goal(request, before):
    original_keys = set(audit_keys(request, before)) | {source.key('source_documents', sid) for sid in request.source_bindings}
    return {
        "operation": request.operation, "target_keys": list(request.target_keys),
        "source_bindings": {k: v.model_dump() for k, v in request.source_bindings.items()},
        "original_objects": {k: model_state(before[k]) for k in sorted(original_keys)},
        "required_questions": sorted(IMPACT_QUESTIONS if is_source_impact(request) else QUESTIONS[request.operation]),
        "units": {"CMU-" + sha256_json(keys)[:20]: keys for keys in partition_targets(request, before)},
    }


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
              if k not in {'transaction_before', 'source_archives', 'audit_contract'}}
    packet['original_goal'] = goal_packet(materials['original_goal'])
    packet['action_obligations'] = {'operation': materials['request']['operation'],
        'target_keys': list(materials['request']['target_keys']),
        'required_questions': list(materials['original_goal']['required_questions'])}
    return packet


def required_roles(request, round_number):
    if request.operation == "Read":
        return []
    roles = ["opus"]
    if request.operation != "Review" or request.confirm_current or request.source_transition:
        roles.append("gemini")
    if request.operation not in {"Review", "Read"} and (request.operation != "Update" or round_number):
        roles.insert(0, "gpt")
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


def failure_reason(findings):
    if any(f.get("category") == "source_uncertain" for f in findings.values()):
        return "human_required"
    if any(f.get("category") == "engineering" for f in findings.values()):
        return "engineering_required"
    return "revision_required"


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
    for key in audit_keys(request, before, candidate):
        if not key.startswith('claims/'):
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
    original = Candidate.model_validate(raw)
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
    elif operation in {"Update", "Restore", "Review"} and any(not current.get(k) or current[k]["retired"] for k in targets):
        raise ValueError("original active Claim obligation is unfinished")
    return True
