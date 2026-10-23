"""Pure CVP 2.0 candidate compilation and finite independent review rules.

No files, model dispatch, SQL, lifecycle state, or source modification belongs
here. Compact selections expand only against the exact supplied material.
"""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .cvp_claim_input import compile_positions
from .cvp_recall import seal, verify
from .knowledge_models import KNOWLEDGE_COLLECTIONS, evidence_fragment_ids
from .viewpoint_foundation import semantic_record_sha, sha256_json

VERSION = 'wang_cvp_management_v2'
MATERIAL_VERSION = 'wang_cvp_common_material_v1'
COLLECTIONS = {'canonical_viewpoints', 'viewpoint_revisions', 'viewpoint_claim_links', 'viewpoint_relations'}
PREFIXES = {'canonical_viewpoints': 'v', 'viewpoint_revisions': 'r',
            'viewpoint_claim_links': 'l', 'viewpoint_relations': 'q'}
FIELDS = {
    'canonical_viewpoints': {'current_revision_id', 'identity_status', 'redirect_to_viewpoint_id', 'summary', 'summary_basis'},
    'viewpoint_revisions': {'viewpoint_id', 'core_proposition', 'proposition_signature', 'scope', 'editorial_aliases'},
    'viewpoint_claim_links': {'viewpoint_id', 'claim_id', 'link_type', 'effective_state', 'component_ref',
                             'validated_against_viewpoint_revision_id', 'pinned_claim_revision', 'evidence_bindings',
                             'component_locator', 'component_position_locator'},
    'viewpoint_relations': {'source_viewpoint_id', 'target_viewpoint_id', 'relation_type', 'reason',
                           'supporting_claim_ids', 'supporting_claim_relation_ids', 'temporal_assertion', 'effective_state',
                           'validated_source_viewpoint_revision_id', 'validated_target_viewpoint_revision_id'},
}
PROGRAM_ONLY_FIELDS = {'summary', 'summary_basis', 'pinned_claim_revision', 'evidence_bindings',
                       'component_locator', 'component_position_locator', 'validated_against_viewpoint_revision_id',
                       'validated_source_viewpoint_revision_id', 'validated_target_viewpoint_revision_id'}
REF_FIELDS = {'current_revision_id': 'viewpoint_revisions', 'viewpoint_id': 'canonical_viewpoints',
    'redirect_to_viewpoint_id': 'canonical_viewpoints', 'claim_id': 'claims',
    'source_viewpoint_id': 'canonical_viewpoints', 'target_viewpoint_id': 'canonical_viewpoints',
    'supporting_claim_ids': 'claims', 'supporting_claim_relation_ids': 'claim_relations'}


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Binding(Contract):
    path: str = Field(min_length=1)
    file_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class Budget(Contract):
    max_calls: int = Field(gt=0, strict=True)
    max_revisions: int = Field(ge=0, strict=True)
    max_request_bytes: int = Field(gt=0, strict=True)
    max_output_tokens: int = Field(default=64000, gt=0, strict=True)


class Permission(Contract):
    collection: str
    object_id: str = Field(min_length=1)
    fields: list[str] = Field(min_length=1)

    @model_validator(mode='after')
    def typed_fields(self):
        if self.collection == 'viewpoint_claim_links' and 'claim_id' in self.fields:
            raise ValueError('existing link source Claim identity is immutable; create a replacement link')
        if (self.collection not in COLLECTIONS or len(set(self.fields)) != len(self.fields)
                or not set(self.fields) <= FIELDS[self.collection]):
            raise ValueError('CVP permission contains a forbidden collection or field')
        return self


class Request(Contract):
    schema_version: Literal['wang_cvp_management_v2'] = VERSION
    action_id: str = Field(min_length=1)
    input: dict[str, Any]
    authorization: Binding
    budget: Budget
    mode: Literal['create', 'maintain'] = 'create'
    maintenance_viewpoint_ids: list[str] = Field(default_factory=list)
    permissions: list[Permission] = Field(default_factory=list)
    creation_slots: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode='after')
    def bounded(self):
        verify(self.input, 'wang_cvp_recall_preflight_v1')
        if len(set(self.maintenance_viewpoint_ids)) != len(self.maintenance_viewpoint_ids):
            raise ValueError('duplicate maintenance viewpoint')
        if self.mode == 'create' and (self.maintenance_viewpoint_ids or self.permissions):
            raise ValueError('create cannot acquire existing object maintenance authority')
        if len({(p.collection, p.object_id) for p in self.permissions}) != len(self.permissions):
            raise ValueError('duplicate object permission')
        if any(col not in COLLECTIONS or type(count) is not int or not 0 <= count <= 100
               for col, count in self.creation_slots.items()):
            raise ValueError('creation slots require explicit bounded CVP collections')
        return self


class Span(Contract):
    text_ref: str
    start_char: int = Field(ge=0, strict=True)
    end_char: int = Field(gt=0, strict=True)


class Component(Contract):
    claim_ref: str
    spans: list[Span] = Field(min_length=1)


class ComponentMapping(Contract):
    old_ref: str
    new_refs: list[str] = Field(min_length=1)


class Disposition(Contract):
    component_ref: str
    disposition: Literal['connected', 'existing_coverage', 'background', 'unresolved']
    target_ref: str | None = None
    link_ref: str | None = None
    reason: str = Field(min_length=1)


class Change(Contract):
    ref: str
    fields_json: str


class Summary(Contract):
    ref: str
    summary: str = Field(min_length=1, max_length=149)


class Summary500(Contract):
    ref: str
    summary: str = Field(min_length=1, max_length=500)


class Finding(Contract):
    slot: str = Field(min_length=1)
    target_refs: list[str] = Field(min_length=1)
    category: Literal['identity', 'coverage', 'wording', 'link_role', 'member', 'source', 'permission']
    reason: str = Field(min_length=1)


class MemberDisposition(Contract):
    member_ref: str
    disposition: Literal['retained', 'changed', 'background', 'unresolved']
    reason: str = Field(min_length=1)


class Candidate(Contract):
    components: list[Component] | None = None
    component_mappings: list[ComponentMapping] = Field(default_factory=list)
    component_dispositions: list[Disposition] = Field(default_factory=list)
    changes: list[Change] = Field(default_factory=list)
    discard_refs: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    summaries: list[Summary] = Field(default_factory=list)
    member_dispositions: list[MemberDisposition] = Field(default_factory=list)


class IncrementalCandidate(Candidate):
    summaries: list[Summary500] = Field(default_factory=list)


class Verdict(Contract):
    ref: str
    decision: Literal['pass', 'revise', 'unresolved']
    finding_refs: list[str]


class PriorFinding(Contract):
    ref: str
    disposition: Literal['resolved', 'persistent', 'reopened']
    reason: str = Field(min_length=1)


class Review(Contract):
    component_reviews: list[Verdict]
    object_reviews: list[Verdict]
    prior_findings: list[PriorFinding]
    findings: list[Finding]
    novelty_approved: bool


def statement_recall(request):
    from .cvp_recall import STATEMENT_SCOPE_PROFILE
    return _request(request).input.get('recall_profile')==STATEMENT_SCOPE_PROFILE


def _request(value):
    return value if isinstance(value, Request) else Request.model_validate(value)


def _records(bundle):
    verify(bundle, MATERIAL_VERSION)
    rows = {}
    for row in bundle['records']:
        key = row['collection'], row['object_id']
        if (key in rows or row.get('retired') or
                semantic_record_sha(row['payload']) != row['content_sha256']):
            raise ValueError('material contains duplicate, retired or inconsistent records')
        rows[key] = row
    return rows


def _id(request, kind, ref, group_key):
    return 'CVP2-' + kind + '-' + sha256_json({'action': request.action_id, 'group_key': group_key, 'ref': ref})[:24]


def prepare_domain(request, bundle):
    request = _request(request)
    records = _records(bundle)
    if not set(request.maintenance_viewpoint_ids) <= set(bundle.get('maintenance_viewpoint_ids', [])):
        raise ValueError('maintenance needs complete own member support materials')
    refs = {}
    for col, values in bundle['references'].items():
        for oid, ref in values.items():
            if ref in refs or (col, oid) not in records:
                raise ValueError('foreign or duplicate program short reference')
            refs[ref] = {'collection': col, 'object_id': oid}
    slots = []
    for col, count in sorted(request.creation_slots.items()):
        for i in range(1, count + 1):
            ref = 'n' + PREFIXES[col] + str(i)
            if ref in refs:
                raise ValueError('creation slot collides with existing short reference')
            value = {'ref': ref, 'collection': col, 'object_id': _id(request, PREFIXES[col], ref, bundle['group_key'])}
            if (col, value['object_id']) in records:
                raise ValueError('new creation slot collides with an existing canonical object')
            refs[ref] = {k: v for k, v in value.items() if k != 'ref'}
            slots.append(value)
    return seal({'schema_version': VERSION + '_context',
        'request_sha256': sha256_json(request.model_dump(mode='json')),
        'material_sha256': bundle['artifact_sha256'], 'group_key': bundle['group_key'], 'references': refs, 'creation_slots': slots,
        'target_input': bundle['claim_input'], 'maintenance_viewpoint_ids': request.maintenance_viewpoint_ids,
        **({'human_constraints': bundle['human_constraints']} if bundle.get('human_constraints') else {})})


def _json_fields(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate compact JSON field')
            result[key] = value
        return result
    result = json.loads(text, object_pairs_hook=unique)
    if not isinstance(result, dict) or not result:
        raise ValueError('compact change must contain changed fields')
    return result


def _positions(bundle, raw, previous):
    if raw.components is None:
        if previous is not None:
            return deepcopy(previous['component_plan'])
        grouped = {}
        for unit in bundle['claim_input']['text_units']:
            grouped.setdefault(unit['claim_ref'], []).append({
                'text_ref': unit['text_ref'], 'start_char': 0, 'end_char': len(unit['text'])})
        components = [{'claim_ref': ref, 'spans': spans} for ref, spans in grouped.items()]
    else:
        components = [component.model_dump(mode='json') for component in raw.components]
    plan = compile_positions(bundle['claim_input'], components)
    claims = {ref: oid for oid, ref in bundle['references']['claims'].items()}
    records = _records(bundle)
    for component in plan['components']:
        cid = claims[component['claim_ref']]
        component['component_id'] = 'CVPC-' + sha256_json({
            'claim_id': cid, 'claim_sha256': records['claims', cid]['content_sha256'],
            'spans': component['spans']})[:24]
    plan = seal({k: v for k, v in plan.items() if k != 'artifact_sha256'})
    if previous is not None and plan['components'] != previous['component_plan']['components']:
        old = {row['component_ref']: row for row in previous['component_plan']['components']}
        mappings = {row.old_ref: row.new_refs for row in raw.component_mappings}
        new = {row['component_ref']: row for row in plan['components']}
        if len(mappings) != len(raw.component_mappings) or set(mappings) != set(old):
            raise ValueError('changed frozen component plan requires every old-to-new mapping')
        same_input = previous['component_plan']['input_sha256'] == bundle['claim_input']['artifact_sha256']
        for ref, targets in mappings.items():
            if len(set(targets)) != len(targets) or not set(targets) <= set(new):
                raise ValueError('component mapping names unknown or duplicate new components')
            if any(new[t]['claim_ref'] != old[ref]['claim_ref'] for t in targets):
                raise ValueError('component split/merge cannot move between Claims')
            # A wording-version transition maps old components to new positions;
            # within one wording version every old character must survive.
            if not same_input:
                continue
            for span in old[ref]['spans']:
                intervals = sorted((max(span['start_char'], s['start_char']), min(span['end_char'], s['end_char']))
                    for t in targets for s in new[t]['spans'] if s['text_ref'] == span['text_ref']
                    and max(span['start_char'], s['start_char']) < min(span['end_char'], s['end_char']))
                cursor = span['start_char']
                for a, b in intervals:
                    if a > cursor:
                        raise ValueError('component mapping drops an old position')
                    cursor = max(cursor, b)
                if cursor != span['end_char']:
                    raise ValueError('component mapping drops an old position')
        plan = seal({**{k: v for k, v in plan.items() if k != 'artifact_sha256'},
                     'previous_plan_sha256': previous['component_plan']['artifact_sha256'],
                     'old_components': previous['component_plan']['components'],
                     'mappings': [row.model_dump(mode='json') for row in raw.component_mappings]})
    elif raw.component_mappings:
        raise ValueError('unchanged component plan cannot invent split/merge history')
    elif previous is not None:
        return deepcopy(previous['component_plan'])
    return plan


def _resolve_fields(fields, refs):
    result = deepcopy(fields)
    for field, col in REF_FIELDS.items():
        if field not in result or result[field] is None:
            continue
        values = result[field] if isinstance(result[field], list) else [result[field]]
        if any(v not in refs or refs[v]['collection'] != col for v in values):
            raise ValueError('identity fields must select typed program short references')
        converted = [refs[v]['object_id'] for v in values]
        if field in {'supporting_claim_ids', 'supporting_claim_relation_ids'}:
            converted = sorted(set(converted))
        result[field] = converted if isinstance(result[field], list) else converted[0]
    if (result.get('relation_type') == 'tensions_with'
            and 'source_viewpoint_id' in result and 'target_viewpoint_id' in result
            and result['source_viewpoint_id'] > result['target_viewpoint_id']):
        result['source_viewpoint_id'], result['target_viewpoint_id'] = (
            result['target_viewpoint_id'], result['source_viewpoint_id'])
    return result


def _held(payload):
    return (payload.get('review_status') in {'human_hold', 'human_review_required', 'rejected'}
            or any(payload.get(field) is True for field in ('human_hold', 'human_retained', 'human_preserve', 'protected')))


def _members(records, vids):
    return {key for key, row in records.items() if key[0] == 'viewpoint_claim_links'
            and row['payload'].get('effective_state') == 'active'
            and row['payload'].get('viewpoint_id') in vids}


def summary_basis_for_records(payloads, viewpoint_id, *, human_source_decisions=()):
    """Bind a summary to the complete current members and their source chain.

    ``payloads`` is a mapping from (collection, object_id) to record payload.
    The CVR projection omits approval provenance, which is added only after
    review; the approved review is bound separately to the exact candidate.
    """
    view = payloads['canonical_viewpoints', viewpoint_id]
    revision_id = view['current_revision_id']
    revision = payloads['viewpoint_revisions', revision_id]
    if revision['viewpoint_id'] != viewpoint_id:
        raise ValueError('summary basis has a foreign current revision')
    pins = [('viewpoint_revisions', revision_id, sha256_json({
        field: revision.get(field) for field in
        ('viewpoint_id', 'core_proposition', 'proposition_signature', 'scope', 'editorial_aliases')}))]
    links = sorted((oid, row) for (col, oid), row in payloads.items()
                   if col == 'viewpoint_claim_links' and row.get('viewpoint_id') == viewpoint_id
                   and row.get('effective_state') == 'active')
    dependencies = {}
    source_ids = set()
    for link_id, link in links:
        if link['validated_against_viewpoint_revision_id'] != revision_id:
            raise ValueError('summary basis contains a stale member pin')
        pins.append(('viewpoint_claim_links', link_id, semantic_record_sha(link)))
        claim_id = link['claim_id']
        claim = payloads['claims', claim_id]
        if link['pinned_claim_revision'] != claim['revision']:
            raise ValueError('summary basis contains a stale Claim pin')
        dependencies['claims', claim_id] = claim
        for evidence_id in claim.get('evidence_step_ids', []):
            evidence = payloads['evidence_steps', evidence_id]
            dependencies['evidence_steps', evidence_id] = evidence
            for fragment_id in evidence_fragment_ids(evidence):
                fragment = payloads['source_fragments', fragment_id]
                dependencies['source_fragments', fragment_id] = fragment
                source_id = fragment['source_id']
                source_ids.add(source_id)
                dependencies['source_documents', source_id] = payloads['source_documents', source_id]
    pins.extend((col, oid, semantic_record_sha(row)) for (col, oid), row in sorted(dependencies.items()))
    return {'schema_version': 'wang_cvp_summary_basis_v1',
            'viewpoint_revision_id': revision_id,
            'member_link_ids': [oid for oid, _ in links],
            'dependency_sha256': sha256_json(pins),
            'human_source_sha256': sha256_json(sorted(
                (row for row in human_source_decisions if row['source_id'] in source_ids),
                key=sha256_json))}


def _maintenance_scope(col, payload, records):
    if col == 'canonical_viewpoints':
        return {payload['viewpoint_id']}
    if col in {'viewpoint_revisions', 'viewpoint_claim_links'}:
        return {payload['viewpoint_id']}
    return {payload['source_viewpoint_id'], payload['target_viewpoint_id']}


def compile_candidate(request, bundle, raw, *, previous=None, prior_findings=()):
    request = _request(request)
    if previous is not None and previous.get('schema_version') == VERSION + '_rebase_frame':
        verify(previous, VERSION + '_rebase_frame')
        expected = prepare_rebase_frame(request, previous['old_bundle'], bundle,
            previous['previous_candidate'], prior_findings=previous['carried_prior_findings'])
        if expected != previous:
            raise ValueError('pending rebase frame differs from its exact saved dependencies')
        return rebind_candidate(request, previous['old_bundle'], bundle, previous['previous_candidate'],
            replacement_raw=raw, prior_findings=prior_findings or previous['carried_prior_findings'])
    raw_type = IncrementalCandidate if statement_recall(request) else Candidate
    raw = raw if isinstance(raw, raw_type) else raw_type.model_validate(raw)
    context = prepare_domain(request, bundle)
    original = _records(bundle)
    refs = context['references']
    if previous is not None:
        verify(previous, VERSION + '_candidate')
        if (previous['request_sha256'], previous['material_sha256']) != (
                context['request_sha256'], bundle['artifact_sha256']):
            raise ValueError('candidate cannot resume on changed request/material')
    # Discard only edits in this uncommitted candidate. Existing records come
    # back from the original material; new slots disappear. Findings and the
    # frozen component plan remain and the complete afterimage is revalidated.
    discarded = set(raw.discard_refs)
    prior_write_refs = {row['ref'] for row in previous['writes']} if previous else set()
    prior_summary_refs = {row['ref'] for row in previous.get('summaries', [])} if previous else set()
    if (len(discarded) != len(raw.discard_refs)
            or not discarded <= prior_write_refs | prior_summary_refs
            or discarded & {row.ref for row in raw.changes}
            or discarded & {row.ref for row in raw.summaries}):
        raise ValueError('discard requires unique previous candidate edits without a conflicting new delta')
    plan = _positions(bundle, raw, previous)
    parts = {row['component_ref']: row for row in plan['components']}
    changed_plan = previous is not None and plan['artifact_sha256'] != previous['component_plan']['artifact_sha256']
    dispositions = {} if previous is None or changed_plan else {
        row['component_ref']: deepcopy(row) for row in previous['component_dispositions']}
    for row in raw.component_dispositions:
        if row.component_ref not in parts:
            raise ValueError('disposition refers to a foreign component')
        dispositions[row.component_ref] = row.model_dump(mode='json')
    if len({r.component_ref for r in raw.component_dispositions}) != len(raw.component_dispositions) or set(dispositions) != set(parts):
        raise ValueError('every component requires exactly one disposition')
    states = {key: deepcopy(row['payload']) for key, row in original.items()}
    if previous:
        states.update({(row['collection'], row['object_id']): deepcopy(row['payload'])
                       for row in previous['writes'] if row['ref'] not in discarded})
    changes = {}
    link_evidence_choices = {}
    link_fragment_choices = {}
    permission = {(p.collection, p.object_id): set(p.fields) for p in request.permissions}
    for change in raw.changes:
        if change.ref not in refs or change.ref in changes:
            raise ValueError('foreign or repeated compact change reference')
        col, oid = refs[change.ref]['collection'], refs[change.ref]['object_id']
        key = col, oid
        fields = _json_fields(change.fields_json)
        evidence_refs = fields.pop('evidence_step_refs', None)
        fragment_refs = fields.pop('source_fragment_refs', None)
        if fragment_refs is not None:
            if (bundle.get('link_evidence_contract')!='source_fragment_refs_v1'
                    or col!='viewpoint_claim_links'
                    or key in original and (request.mode!='maintain' or 'evidence_bindings' not in permission.get(key,set()))
                    or not isinstance(fragment_refs,list) or not fragment_refs
                    or any(not isinstance(ref,str) or ref not in refs
                           or refs[ref]['collection']!='source_fragments' for ref in fragment_refs)
                    or len(set(fragment_refs))!=len(fragment_refs)):
                raise ValueError('new link fragment selection requires its bound contract and unique supplied fragment references')
            link_fragment_choices[change.ref]=[refs[ref]['object_id'] for ref in fragment_refs]
        if evidence_refs is not None:
            if (col != 'viewpoint_claim_links'
                    or key in original and (request.mode!='maintain' or 'evidence_bindings' not in permission.get(key,set()))
                    or not isinstance(evidence_refs, list) or not evidence_refs
                    or any(not isinstance(ref, str) or ref not in refs
                           or refs[ref]['collection'] != 'evidence_steps' for ref in evidence_refs)
                    or len(set(evidence_refs)) != len(evidence_refs)):
                raise ValueError('new link evidence selection requires unique supplied evidence short references')
            link_evidence_choices[change.ref] = [refs[ref]['object_id'] for ref in evidence_refs]
        if col not in COLLECTIONS or set(fields) & PROGRAM_ONLY_FIELDS or not set(fields) <= FIELDS[col]:
            raise ValueError('compact change attempts a mechanical or forbidden field')
        if key in original:
            if (_held(original[key]['payload']) or request.mode != 'maintain'
                    or not set(fields) <= permission.get(key, set())
                    or (not (_maintenance_scope(col, original[key]['payload'], original) & set(request.maintenance_viewpoint_ids))
                        if col == 'viewpoint_relations' else
                        not _maintenance_scope(col, original[key]['payload'], original) <= set(request.maintenance_viewpoint_ids))):
                raise ValueError('existing write lacks exact maintenance permission or crosses a hold')
        elif not change.ref.startswith('n'):
            raise ValueError('creation requires a preallocated slot')
        fields = _resolve_fields(fields, refs)
        if (key in original and col == 'viewpoint_revisions'
                and any(original[key]['payload'].get(k) != value for k, value in fields.items())):
            raise ValueError('approved CVR is immutable; create a revision slot and move the authorized current head')
        comparable = {k: v for k, v in fields.items() if k != 'component_ref'}
        if (key in original and all(states[key].get(k) == value for k, value in comparable.items())
                and 'component_ref' not in fields):
            continue
        states.setdefault(key, {}).update(fields)
        changes[change.ref] = fields
    # Populate only touched/new objects; unchanged old records retain exact bytes.
    keyrefs = {(value['collection'], value['object_id']): ref for ref, value in refs.items()}
    touched = set(changes) | (prior_write_refs - discarded)
    if statement_recall(request) and previous:
        live_member_targets = set()
        for (col, oid), state in states.items():
            if col != 'viewpoint_claim_links':
                continue
            old = original.get((col, oid))
            if old is None or _member_meaning(old['payload']) != _member_meaning(state):
                live_member_targets.add(state['viewpoint_id'])
                if old is not None:
                    live_member_targets.add(old['payload']['viewpoint_id'])
        # A correction can abandon target A for B. Its incidental summary
        # write must disappear with the abandoned uncommitted connection.
        for row in previous['writes']:
            key = row['collection'], row['object_id']
            if (row['collection'] != 'canonical_viewpoints' or key not in original
                    or row['ref'] not in touched or row['ref'] in changes
                    or row['object_id'] in live_member_targets
                    or (request.mode == 'maintain' and row['object_id'] in request.maintenance_viewpoint_ids
                        and 'summary' in permission.get(key, set()))):
                continue
            old = original[key]['payload']
            if {k: v for k, v in row['payload'].items() if k not in {'summary', 'summary_basis', 'revision'}} == {
                    k: v for k, v in old.items() if k not in {'summary', 'summary_basis', 'revision'}}:
                touched.remove(row['ref'])
                states[key] = deepcopy(old)
    for key, row in original.items():
        col, oid = key
        if col not in {'viewpoint_claim_links', 'viewpoint_relations'} or states[key].get('effective_state') != 'active':
            continue
        pending = []
        if col == 'viewpoint_claim_links':
            ends = [('viewpoint_id', 'validated_against_viewpoint_revision_id')]
        else:
            ends = [(end + '_viewpoint_id', 'validated_' + end + '_viewpoint_revision_id') for end in ('source', 'target')]
        for vid_field, pin_field in ends:
            vid = states[key][vid_field]
            head = states.get(('canonical_viewpoints', vid))
            if head and states[key][pin_field] != head['current_revision_id']:
                pending.append((vid, pin_field))
        ref = keyrefs[key]
        if col == 'viewpoint_claim_links':
            claim = original.get(('claims', states[key]['claim_id']))
            if claim is not None and states[key]['pinned_claim_revision'] != claim['revision']:
                pending.append((states[key]['viewpoint_id'], 'pinned_claim_revision'))
            if 'evidence_bindings' in permission.get(key, set()):
                pending.append((states[key]['viewpoint_id'], 'evidence_bindings'))
        if pending and ref not in touched:
            if (request.mode != 'maintain' or _held(row['payload'])
                    or not {vid for vid, _ in pending} <= set(request.maintenance_viewpoint_ids)
                    or not {field for _, field in pending} <= permission.get(key, set())):
                raise ValueError('changed head/Claim needs explicit program-only member/relation repin permission')
            touched.add(ref)
    decision_ids = {ref: _id(request, 'decision', ref, bundle['group_key']) for ref in touched}
    component_by_link = {}
    for ref, value in dispositions.items():
        if value['disposition'] in {'connected', 'existing_coverage'}:
            target, link = value['target_ref'], value['link_ref']
            if (target not in refs or refs[target]['collection'] != 'canonical_viewpoints'
                    or link not in refs or refs[link]['collection'] != 'viewpoint_claim_links'):
                raise ValueError('connected component needs exact target and link short references')
            if link in component_by_link:
                raise ValueError('a link cannot silently represent two separate components')
            component_by_link[link] = ref
        elif value['target_ref'] is not None or value['link_ref'] is not None:
            raise ValueError('background/unresolved disposition cannot imply a connection')
    for ref, fields in changes.items():
        if 'component_ref' in fields and fields['component_ref'] != component_by_link.get(ref):
            raise ValueError('compact link component_ref differs from the actual component disposition')
    for ref in sorted(touched):
        col, oid = refs[ref]['collection'], refs[ref]['object_id']
        key = col, oid
        payload = states[key]
        is_new = key not in original
        id_field = KNOWLEDGE_COLLECTIONS[col][1]
        if is_new:
            payload.update({id_field: oid, 'revision': 1, 'review_status': 'system_approved', 'visibility': 'internal'})
        else:
            payload['revision'] = original[key]['revision'] + 1
        if col == 'canonical_viewpoints' and is_new:
            payload.setdefault('created_from_candidate_id', _id(request, 'candidate', ref, bundle['group_key']))
            payload.setdefault('identity_status', 'active')
        elif col == 'viewpoint_revisions':
            if is_new:
                owners = [vid for (kind, vid), value in states.items()
                          if kind == 'canonical_viewpoints' and value.get('current_revision_id') == oid]
                if len(owners) != 1:
                    raise ValueError('new revision needs exactly one current head owner')
                owner = owners[0]
                if payload.get('viewpoint_id', owner) != owner:
                    raise ValueError('new revision owner contradicts the selected current head')
                old_view = original.get(('canonical_viewpoints', owner))
                if old_view:
                    old_revision = original.get(('viewpoint_revisions', old_view['payload']['current_revision_id']))
                    if old_revision is None:
                        raise ValueError('revision delta requires the complete old current revision')
                    inherited = {field: deepcopy(old_revision['payload'][field])
                                 for field in FIELDS['viewpoint_revisions'] - {'viewpoint_id'}
                                 if field in old_revision['payload']}
                    inherited.update(payload)
                    payload = states[key] = inherited
                payload['viewpoint_id'] = owner
            existing_view = original.get(('canonical_viewpoints', payload.get('viewpoint_id')))
            if is_new and existing_view:
                payload['supersedes_revision_id'] = existing_view['payload']['current_revision_id']
            payload['revision_number'] = payload['revision']
            payload['provenance'] = {'basis_identity_decision_ids': [decision_ids[ref]],
                                     'review_artifact_sha256': context['material_sha256']}
            payload['approved_by'] = VERSION
            payload['approved_at'] = 'pending_exact_review'
        elif col == 'viewpoint_claim_links':
            payload['schema_version'] = 'wang_viewpoint_claim_link_v2'
            cid = payload.get('claim_id')
            if not is_new and cid != original[key]['payload']['claim_id']:
                raise ValueError('existing link source Claim identity cannot be rewritten')
            if ('claims', cid) not in original:
                raise ValueError('link needs its exact current Claim material')
            claim = original['claims', cid]
            if payload.get('effective_state', 'active') != 'active':
                if is_new:
                    raise ValueError('inactive new link has no permitted creation purpose')
                if any(payload.get(field) != original[key]['payload'].get(field)
                       for field in ('claim_id', 'viewpoint_id', 'link_type')):
                    raise ValueError('withdrawal preserves the historical relationship; create an explicit replacement')
                payload.pop('component_ref', None)
                payload['schema_version'] = 'wang_viewpoint_claim_link_v2'
                payload['decision_id'] = decision_ids[ref]
                states[key] = KNOWLEDGE_COLLECTIONS[col][0].model_validate(payload).model_dump(mode='json')
                continue
            cref = keyrefs['claims', cid]
            if ref not in component_by_link:
                # Existing withdrawn links are still explicitly reviewed as objects/members.
                if is_new:
                    raise ValueError('new active link needs an explicit component disposition')
                if payload.get('effective_state', 'active') == 'active' and (col, oid) not in _members(original, set(request.maintenance_viewpoint_ids)):
                    raise ValueError('existing active link is outside complete member maintenance')
            else:
                component = parts[component_by_link[ref]]
                if component['claim_ref'] != cref:
                    raise ValueError('link component belongs to another Claim')
                payload.pop('component_ref', None)
                if payload['link_type'] == 'equivalent_full':
                    own = [u for u in bundle['claim_input']['text_units'] if u['claim_ref'] == cref]
                    if len(component['spans']) != len(own) or any(s['start_char'] != 0 or s['end_char'] != len(next(u['text'] for u in own if u['text_ref'] == s['text_ref'])) for s in component['spans']):
                        raise ValueError('equivalent_full must represent the full Claim text units')
                    payload['component_locator'] = None
                    payload.pop('component_position_locator', None)
                elif any(s['path'] != ['statement'] for s in component['spans']):
                    payload['schema_version'] = 'wang_viewpoint_claim_link_v2'
                    payload['component_locator'] = None
                    payload['component_position_locator'] = {
                        'input_sha256': bundle['claim_input']['artifact_sha256'],
                        'claim_sha256': claim['content_sha256'],
                        'spans': [{k: s[k] for k in ('path', 'start_char', 'end_char', 'exact_text')}
                                  for s in component['spans']]}
                else:
                    payload.pop('component_position_locator', None)
                    spans = [{k: s[k] for k in ('start_char', 'end_char', 'exact_text')} for s in component['spans']]
                    payload['component_locator'] = {'statement_component': ''.join(s['exact_text'] for s in spans),
                        'claim_sha256': claim['content_sha256'], 'canonical_spans': spans}
            vid = payload.get('viewpoint_id')
            if ('canonical_viewpoints', vid) not in states:
                raise ValueError('link target viewpoint is absent')
            payload['validated_against_viewpoint_revision_id'] = states['canonical_viewpoints', vid]['current_revision_id']
            payload['pinned_claim_revision'] = claim['revision']
            payload['decision_id'] = decision_ids[ref]
            payload.setdefault('effective_state', 'active')
            selected_evidence = link_evidence_choices.get(ref)
            selected_fragments = link_fragment_choices.get(ref)
            if (selected_fragments is None and ref not in link_evidence_choices
                    and bundle.get('link_evidence_contract')=='source_fragment_refs_v1'
                    and previous and is_new):
                selected_fragments=sorted({row['source_fragment_id']
                    for row in payload.get('evidence_bindings',[])}) or None
            if selected_evidence is None and previous and is_new:
                # A correction that changes only the link role must retain the
                # previous precise selection instead of widening it again.
                selected_evidence = sorted({row['evidence_step_id']
                    for row in payload.get('evidence_bindings', [])}) or None
            if selected_evidence is not None:
                if not set(selected_evidence) <= set(claim['payload'].get('evidence_step_ids', [])):
                    raise ValueError('link evidence selection is foreign to its source Claim')
                if any(('evidence_steps', eid) not in original for eid in selected_evidence):
                    raise ValueError('link evidence selection is missing from material')
                if any(original['evidence_steps', eid]['payload'].get('support_eligibility') not in
                       {'eligible', 'eligible_candidate', 'eligible_with_label'}
                       for eid in selected_evidence):
                    raise ValueError('link evidence selection includes ineligible support')
            if not is_new and selected_evidence is None:
                # A version-only repin must not silently widen an approved
                # link to every other eligible evidence step on the Claim.
                evidence = deepcopy(original[key]['payload'].get('evidence_bindings', []))
                for binding in evidence:
                    eid, fid = binding['evidence_step_id'], binding['source_fragment_id']
                    if (eid not in claim['payload'].get('evidence_step_ids', [])
                            or ('evidence_steps', eid) not in original
                            or ('source_fragments', fid) not in original
                            or fid not in evidence_fragment_ids(original['evidence_steps', eid]['payload'])
                            or original['evidence_steps', eid]['payload'].get('support_eligibility') not in
                            {'eligible', 'eligible_candidate', 'eligible_with_label'}):
                        raise ValueError('existing link evidence no longer qualifies for version-only repin')
            else:
                evidence = []
                for eid in claim['payload'].get('evidence_step_ids', []):
                    if selected_evidence is not None and eid not in selected_evidence:
                        continue
                    if ('evidence_steps', eid) not in original:
                        raise ValueError('link Claim evidence is missing from material')
                    step = original['evidence_steps', eid]['payload']
                    if step.get('support_eligibility') not in {
                            'eligible', 'eligible_candidate', 'eligible_with_label'}:
                        continue
                    for fid in evidence_fragment_ids(step):
                        if ('source_fragments', fid) not in original:
                            raise ValueError('link evidence fragment is missing')
                        evidence.append({'evidence_step_id': eid, 'source_fragment_id': fid})
                if selected_fragments is not None:
                    if not set(selected_fragments)<={row['source_fragment_id'] for row in evidence}:
                        raise ValueError('link fragment selection is outside the selected eligible own Claim evidence')
                    evidence=[row for row in evidence if row['source_fragment_id'] in selected_fragments]
            payload['evidence_bindings'] = sorted(evidence, key=lambda row: (row['evidence_step_id'], row['source_fragment_id']))
        elif col == 'viewpoint_relations':
            for end in ('source', 'target'):
                vid = payload[end + '_viewpoint_id']
                if ('canonical_viewpoints', vid) not in states:
                    raise ValueError('relation endpoint is absent from full candidate')
                if payload.get('effective_state', 'active') == 'active':
                    payload['validated_' + end + '_viewpoint_revision_id'] = states['canonical_viewpoints', vid]['current_revision_id']
            payload['review_provenance'] = {'review_artifact_sha256': context['material_sha256'],
                                          'basis_identity_decision_ids': [decision_ids[ref]]}
        if not is_new:
            changed_program_fields = {field for field in PROGRAM_ONLY_FIELDS - {'summary', 'summary_basis'}
                                      if payload.get(field) != original[key]['payload'].get(field)}
            if not changed_program_fields <= permission.get(key, set()):
                raise ValueError('derived pin/evidence/locator update lacks exact program-only field permission')
        typed_payload = KNOWLEDGE_COLLECTIONS[col][0].model_validate(payload).model_dump(mode='json')
        if col == 'canonical_viewpoints' and not statement_recall(request):
            for field in ('summary', 'summary_basis'):
                if field not in payload:
                    typed_payload.pop(field, None)
        states[key] = typed_payload
    writes = []
    for ref in sorted(touched):
        col, oid = refs[ref]['collection'], refs[ref]['object_id']
        key = col, oid
        if key in original and semantic_record_sha(states[key]) == original[key]['content_sha256']:
            states[key] = deepcopy(original[key]['payload'])
            continue
        writes.append({'ref': ref, 'collection': col, 'object_id': oid,
            'before_revision': original[key]['revision'] if key in original else None,
            'before_sha256': original[key]['content_sha256'] if key in original else None,
            'payload': states[key]})
    _validate_afterimage(request, original, states, dispositions, parts, refs)
    member_keys = _members(original, set(request.maintenance_viewpoint_ids))
    member_refs = {keyrefs[key]: key for key in member_keys}
    member_dispositions = {row['member_ref']: deepcopy(row) for row in previous.get('member_dispositions', [])} if previous else {}
    if len({row.member_ref for row in raw.member_dispositions}) != len(raw.member_dispositions):
        raise ValueError('duplicate member disposition')
    for row in raw.member_dispositions:
        if row.member_ref not in member_refs:
            raise ValueError('member disposition is outside full maintenance scope')
        member_dispositions[row.member_ref] = row.model_dump(mode='json')
    if set(member_dispositions) != set(member_refs):
        raise ValueError('full maintenance needs every member disposition')
    for ref, key in member_refs.items():
        old, after = original[key]['payload'], states[key]
        disposition = member_dispositions[ref]['disposition']
        unchanged = _member_meaning(old) == _member_meaning(after)
        if disposition == 'retained' and not unchanged:
            raise ValueError('retained member differs from its original relationship')
        if disposition == 'changed' and unchanged:
            raise ValueError('changed member needs a real relationship delta')
        if after.get('effective_state') != 'active':
            replacement = any(p.get('effective_state') == 'active' and p.get('claim_id') == old['claim_id']
                for (col, _), p in states.items() if col == 'viewpoint_claim_links')
            if not replacement and disposition not in {'background', 'unresolved'}:
                raise ValueError('withdrawn member leaves unexplained coverage gap')
    object_refs = {row['ref'] for row in writes} | {keyrefs[key] for key in member_keys}
    history = [*previous.get('prior_findings', []), *previous.get('findings', [])] if previous else []
    constraint_findings = [*_constraint_findings(request, bundle, refs, parts),
                           *_quality_prior_findings(bundle, refs)]
    incoming = {row['finding_id']: deepcopy(row) for row in [*history, *prior_findings, *constraint_findings]}
    prior_refs = {f'pf{i}': row for i, row in enumerate(sorted(incoming.values(), key=lambda row: row['finding_id']), 1)}
    summaries = {row['ref']: row for row in previous.get('summaries', [])
                 if row['ref'] not in discarded} if previous else {}
    if len({row.ref for row in raw.summaries}) != len(raw.summaries):
        raise ValueError('duplicate summary target')
    new_active_targets = {row['payload']['viewpoint_id'] for row in writes
        if row['collection'] == 'viewpoint_claim_links' and row['before_revision'] is None
        and row['payload'].get('effective_state') == 'active'}
    changed_member_targets = set(new_active_targets)
    for row in writes:
        if row['collection'] != 'viewpoint_claim_links' or row['before_revision'] is None:
            continue
        old = original[row['collection'], row['object_id']]['payload']
        if _member_meaning(old) == _member_meaning(row['payload']):
            continue
        changed_member_targets.add(old['viewpoint_id'])
        changed_member_targets.add(row['payload']['viewpoint_id'])
    created_ids = {row['object_id'] for row in writes
                   if row['collection'] == 'canonical_viewpoints' and row['before_revision'] is None}
    changed_head_ids = {oid for (col, oid), state in states.items()
        if col == 'canonical_viewpoints' and (col, oid) in original
        and state.get('current_revision_id') != original[col, oid]['payload'].get('current_revision_id')}
    affected_ids = changed_member_targets | created_ids | changed_head_ids
    # A correction that withdraws a proposed link also withdraws its incidental
    # summary edit. A separately authorized maintenance edit remains in scope.
    if statement_recall(request):
        summaries = {ref: row for ref, row in summaries.items() if
            refs[ref]['object_id'] in affected_ids or
            ('summary' in permission.get(('canonical_viewpoints', refs[ref]['object_id']), set())
             and refs[ref]['object_id'] in request.maintenance_viewpoint_ids)}
    for row in raw.summaries:
        if row.ref not in refs or refs[row.ref]['collection'] != 'canonical_viewpoints':
            raise ValueError('summary target is not a supplied viewpoint')
        key = (refs[row.ref]['collection'], refs[row.ref]['object_id'])
        auto_enrichment = statement_recall(request) and key[1] in changed_member_targets
        explicit_maintenance = (request.mode == 'maintain' and key[1] in request.maintenance_viewpoint_ids
                                and 'summary' in permission.get(key, set()))
        if key in original and (not (auto_enrichment or explicit_maintenance)
                                or _held(original[key]['payload'])):
            raise ValueError('existing summary needs exact metadata maintenance permission')
        if not statement_recall(request) and len(row.summary) > 149:
            raise ValueError('historical summary exceeds 149 Unicode characters')
        summaries[row.ref] = row.model_dump(mode='json')
    summary_material_pending = []
    if statement_recall(request):
        # New links grant only their actual target a bounded summary/basis edit;
        # they do not grant identity, scope, old-member or relation maintenance.
        loaded_claim_ids = {next((oid for oid, short in bundle['references'].get('claims', {}).items()
            if short == claim['claim_ref']), None) for claim in bundle['material'].get('claims', [])
            if claim.get('support_loaded')}
        for vid in sorted(affected_ids | {refs[ref]['object_id'] for ref in summaries}):
            key = 'canonical_viewpoints', vid
            state = states[key]
            if state['identity_status'] != 'active':
                continue
            if key in original and _held(original[key]['payload']):
                raise ValueError('summary enrichment crosses a protected viewpoint')
            ref = keyrefs[key]
            proposed = summaries.get(ref)
            if proposed is None and not state.get('summary'):
                card = next((item for item in bundle['material'].get('viewpoints', [])
                    if item.get('cvp_ref') == ref and item.get('summary_status') == 'current'), None)
                card_summary = card.get('contextual_summary') if card else None
                if card_summary and card_summary.get('attribution') == 'editorial_synthesis':
                    text = card_summary.get('text')
                    if isinstance(text, str) and 1 <= len(text) <= 500 and text.strip():
                        # A source-bound, independently reviewed card is the
                        # initial summary when the CVP row has none. The final
                        # Opus review still sees and approves the full afterimage.
                        proposed = {'ref': ref, 'summary': text}
                        summaries[ref] = proposed
            summary = proposed['summary'] if proposed else state.get('summary')
            old_member_ids = {row['payload']['claim_id'] for (col, _), row in original.items()
                if col == 'viewpoint_claim_links' and row['payload'].get('viewpoint_id') == vid
                and row['payload'].get('effective_state') == 'active'}
            if not summary or not old_member_ids <= loaded_claim_ids:
                summary_material_pending.append(ref)
                continue
            state['summary'] = summary
            try:
                state['summary_basis'] = summary_basis_for_records(states, vid,
                    human_source_decisions=bundle.get('human_source_decisions',[]))
            except KeyError:
                summary_material_pending.append(ref)
                continue
            if key in original:
                state['revision'] = original[key]['revision'] + 1
            states[key] = KNOWLEDGE_COLLECTIONS['canonical_viewpoints'][0].model_validate(state).model_dump(mode='json')
            existing_write = next((write for write in writes if (write['collection'], write['object_id']) == key), None)
            if key in original and semantic_record_sha(states[key]) == original[key]['content_sha256']:
                if existing_write:
                    writes.remove(existing_write)
                states[key] = deepcopy(original[key]['payload'])
            elif existing_write:
                existing_write['payload'] = states[key]
            else:
                writes.append({'ref': ref, 'collection': key[0], 'object_id': vid,
                    'before_revision': original[key]['revision'] if key in original else None,
                    'before_sha256': original[key]['content_sha256'] if key in original else None,
                    'payload': states[key]})
            object_refs.add(ref)
        writes.sort(key=lambda row: row['ref'])
    created_views = {row['ref'] for row in writes if row['collection'] == 'canonical_viewpoints' and row['before_revision'] is None}
    changed_heads = {ref for ref, value in refs.items() if value['collection'] == 'canonical_viewpoints'
        and (value['collection'], value['object_id']) in original
        and states[value['collection'], value['object_id']]['identity_status'] == 'active'
        and states[value['collection'], value['object_id']]['current_revision_id'] !=
            original[value['collection'], value['object_id']]['payload']['current_revision_id']}
    if not statement_recall(request) and not (created_views | changed_heads) <= set(summaries):
        raise ValueError('new or revised viewpoints require compact summaries in the same review')
    valid_refs = set(parts) | set(refs)
    bindings = {ref: value['object_id'] for ref, value in refs.items()}
    bindings.update({ref: part['component_id'] for ref, part in parts.items()})
    findings = _findings(request, raw.findings, valid_refs, bundle['group_key'], bindings)
    object_refs.update(summaries)
    object_refs.update(ref for finding in constraint_findings for ref in finding['target_refs']
                       if ref in refs and refs[ref]['collection'] in COLLECTIONS)
    compiled = seal({'schema_version': VERSION + '_candidate',
        'request_sha256': context['request_sha256'], 'material_sha256': bundle['artifact_sha256'],
        'request_scope_sha256': sha256_json({k: v for k, v in request.model_dump(mode='json').items() if k != 'input'}),
        'group_key': bundle['group_key'], 'component_plan': plan, 'component_dispositions': list(dispositions.values()),
        'records': [{'collection': col, 'object_id': oid, 'payload': p} for (col, oid), p in sorted(states.items())],
        'writes': writes, 'references': refs,
        'review_sets': {'components': sorted(parts), 'objects': sorted(object_refs), 'prior_findings': sorted(prior_refs)},
        'prior_findings': list(incoming.values()), 'prior_finding_refs': prior_refs,
        'findings': findings, 'summaries': list(summaries.values()),
        **({'summary_material_pending': sorted(summary_material_pending)} if statement_recall(request) else {}),
        'member_dispositions': list(member_dispositions.values()),
        'rebase_history': deepcopy(previous.get('rebase_history', [])) if previous else [],
        'new_viewpoint_refs': sorted(created_views), 'independent_audit': 'not_performed'})
    enforce_human_constraints(request, bundle, compiled)
    return compiled


def _findings(request, values, valid_refs, group_key, bindings):
    if len({f.slot for f in values}) != len(values):
        raise ValueError('duplicate local finding slot')
    result = []
    for finding in values:
        if len(set(finding.target_refs)) != len(finding.target_refs) or not set(finding.target_refs) <= valid_refs:
            raise ValueError('finding contains foreign or repeated target reference')
        row = finding.model_dump(mode='json')
        row['finding_id'] = _id(request, 'finding', sha256_json({
            'targets': sorted(bindings[ref] for ref in finding.target_refs), 'category': finding.category}), group_key)
        row['target_bindings'] = {ref: bindings[ref] for ref in finding.target_refs}
        result.append(row)
    return result


def _validate_afterimage(request, original, states, dispositions, parts, refs):
    # Check each declared component connection against the actual complete link.
    for ref, disposition in dispositions.items():
        if disposition['disposition'] not in {'connected', 'existing_coverage'}:
            continue
        link = refs[disposition['link_ref']]
        payload = states.get((link['collection'], link['object_id']))
        target = refs[disposition['target_ref']]['object_id']
        claim = refs[parts[ref]['claim_ref']]['object_id']
        if not payload or (payload['claim_id'], payload['viewpoint_id'], payload['effective_state']) != (claim, target, 'active'):
            raise ValueError('component disposition differs from actual active connection')
        current_claim = original['claims', claim]
        if payload['pinned_claim_revision'] != current_claim['revision']:
            raise ValueError('component link pins a stale Claim version')
        if payload.get('component_position_locator'):
            locator = payload['component_position_locator']
            expected = [{k: s[k] for k in ('path', 'start_char', 'end_char', 'exact_text')} for s in parts[ref]['spans']]
            if locator['claim_sha256'] != current_claim['content_sha256'] or locator['spans'] != expected:
                raise ValueError('existing field locator differs from actual component')
        elif payload.get('component_locator'):
            locator = payload['component_locator']
            expected = [{k: s[k] for k in ('start_char', 'end_char', 'exact_text')} for s in parts[ref]['spans']]
            if (any(s['path'] != ['statement'] for s in parts[ref]['spans'])
                    or locator['claim_sha256'] != current_claim['content_sha256']
                    or locator['canonical_spans'] != expected):
                raise ValueError('existing statement locator differs from actual component')
        view = states['canonical_viewpoints', target]
        if (_held(view) or _held(states['viewpoint_revisions', view['current_revision_id']])
                or view['identity_status'] != 'active' or payload['validated_against_viewpoint_revision_id'] != view['current_revision_id']):
            raise ValueError('active connection uses a noncurrent or inactive viewpoint')
    for (col, oid), payload in states.items():
        if col != 'canonical_viewpoints':
            continue
        rid = payload['current_revision_id']
        revision = states.get(('viewpoint_revisions', rid))
        if revision is None or revision['viewpoint_id'] != oid:
            raise ValueError('viewpoint head has no matching full revision')
        if (('canonical_viewpoints', oid) not in original and payload['identity_status'] == 'active'
                and not any(row.get('viewpoint_id') == oid and row.get('effective_state') == 'active'
                    and row.get('link_type') in {'equivalent_full', 'equivalent_component'}
                    for (c, _), row in states.items() if c == 'viewpoint_claim_links')):
            raise ValueError('new viewpoint lacks an equivalent source Claim member')
        if (payload['identity_status'] in {'merged', 'redirected'}
                and ('canonical_viewpoints', payload['redirect_to_viewpoint_id']) in original
                and payload['redirect_to_viewpoint_id'] not in request.maintenance_viewpoint_ids):
            raise ValueError('merge requires full maintenance material for the existing destination identity')
        if payload['identity_status'] in {'merged', 'redirected'}:
            target = states.get(('canonical_viewpoints', payload['redirect_to_viewpoint_id']))
            if not target or target['identity_status'] != 'active':
                raise ValueError('merge target must be a current active viewpoint')
        if payload['identity_status'] != 'active':
            if any(row.get('effective_state') == 'active' and row.get('viewpoint_id') == oid
                   for (c, _), row in states.items() if c == 'viewpoint_claim_links'):
                raise ValueError('retire/merge leaves an active member on an inactive viewpoint')
            if any(row.get('effective_state') == 'active' and oid in {row['source_viewpoint_id'], row['target_viewpoint_id']}
                   for (c, _), row in states.items() if c == 'viewpoint_relations'):
                raise ValueError('retire/merge leaves an active relation on an inactive viewpoint')
    # Changing a head requires every active member/relationship to follow it.
    for (col, _), row in states.items():
        if col == 'viewpoint_claim_links' and row.get('effective_state') == 'active':
            view = states.get(('canonical_viewpoints', row['viewpoint_id']))
            if view and row['validated_against_viewpoint_revision_id'] != view['current_revision_id']:
                raise ValueError('changed viewpoint head leaves a stale member')
        if col == 'viewpoint_relations' and row.get('effective_state') == 'active':
            for end in ('source', 'target'):
                view = states.get(('canonical_viewpoints', row[end + '_viewpoint_id']))
                if view and row['validated_' + end + '_viewpoint_revision_id'] != view['current_revision_id']:
                    raise ValueError('changed viewpoint head leaves a stale relation')


def validate_review(request, bundle, candidate, raw, *, prior_findings=()):
    request = _request(request)
    verify(candidate, VERSION + '_candidate')
    if candidate.get('summary_material_pending'):
        raise ValueError('summary material or text is incomplete; complete proposal follow-up before review')
    context = prepare_domain(request, bundle)
    if (candidate['request_sha256'], candidate['material_sha256']) != (context['request_sha256'], bundle['artifact_sha256']):
        raise ValueError('review candidate request/material binding differs')
    review = raw if isinstance(raw, Review) else Review.model_validate(raw)
    valid = set(candidate['references']) | set(candidate['review_sets']['components'])
    bindings = {ref: value['object_id'] for ref, value in candidate['references'].items()}
    bindings.update({part['component_ref']: part['component_id'] for part in candidate['component_plan']['components']})
    findings = _findings(request, review.findings, valid, bundle['group_key'], bindings)
    finding_slots = {row['slot']: row for row in findings}
    unresolved_targets = {ref for row in [*candidate['findings'], *findings] for ref in row['target_refs']}
    decisions = []
    for name, values in [('components', review.component_reviews), ('objects', review.object_reviews)]:
        expected = set(candidate['review_sets'][name])
        if len({row.ref for row in values}) != len(values) or {row.ref for row in values} != expected:
            raise ValueError('review omitted, repeated or invented necessary ' + name)
        for row in values:
            if len(set(row.finding_refs)) != len(row.finding_refs) or not set(row.finding_refs) <= set(finding_slots):
                raise ValueError('review verdict cites unknown or repeated finding slots')
            cited = [finding_slots[slot] for slot in row.finding_refs]
            if any(row.ref not in finding['target_refs'] for finding in cited):
                raise ValueError('verdict cites an unrelated finding')
            if row.decision == 'pass' and (row.finding_refs or row.ref in unresolved_targets):
                raise ValueError('pass contradicts an unresolved finding')
            if row.decision != 'pass' and not row.finding_refs:
                raise ValueError('nonpass needs a concrete finding')
            decisions.append(row.decision)
    expected_prior = candidate['prior_finding_refs']
    if (len({row.ref for row in review.prior_findings}) != len(review.prior_findings)
            or {row.ref for row in review.prior_findings} != set(expected_prior)):
        raise ValueError('review must disposition every old finding exactly once')
    prior_ids = {row['finding_id'] for row in candidate['prior_findings']}
    if not {row['finding_id'] for row in prior_findings} <= prior_ids:
        raise ValueError('review lost a supplied prior finding')
    pending = [expected_prior[row.ref] for row in review.prior_findings if row.disposition != 'resolved']
    pending_targets = {ref for row in pending for ref in row['target_refs']}
    for row in [*review.component_reviews, *review.object_reviews]:
        if row.decision == 'pass' and row.ref in pending_targets:
            raise ValueError('pass contradicts a persistent old finding')
    approved = (all(value == 'pass' for value in decisions) and not findings
                and not candidate['findings'] and not pending and review.novelty_approved
                and not any(row['disposition'] == 'unresolved' for row in [*candidate['component_dispositions'], *candidate.get('member_dispositions', [])]))
    return seal({'schema_version': VERSION + '_review', 'candidate_sha256': candidate['artifact_sha256'],
        'review': review.model_dump(mode='json'), 'approved': approved,
        'findings': [*findings, *pending], 'prior_finding_history': candidate['prior_findings'],
        'independent_audit': 'not_performed'})


def proposal_packet(request, bundle, *, context=None, previous=None, prior_findings=()):
    """The caller authenticates this complete packet against the saved model call."""
    request = _request(request)
    expected = prepare_domain(request, bundle)
    if context is not None and context != expected:
        raise ValueError('proposal context differs from exact current material')
    # Durable frames retain old material for SQL replay. Runtime receives current
    # material once plus the old plan/findings, not a second copy of source text.
    if previous is not None and previous.get('schema_version') == VERSION + '_rebase_frame':
        verify(previous, VERSION + '_rebase_frame')
        previous = {**{k: deepcopy(v) for k, v in previous.items()
            if k not in {'old_bundle', 'previous_candidate', 'artifact_sha256'}},
            'schema_version': VERSION + '_rebase_frame_runtime',
            'durable_frame_sha256': previous['artifact_sha256']}
    return {'schema_version': VERSION + '_proposal_packet',
        'request': request.model_dump(mode='json'), 'material': bundle['material'],
        'input': bundle['claim_input'], 'context': expected,
        'previous': previous, 'prior_findings': list(prior_findings),
        'instructions': 'Output only compact field changes and short references. Empty changes/dispositions/summaries retain previous candidate. Never copy identifiers, hashes, old text or unchanged records. All new viewpoints require same-group novelty review.'}


def review_packet(request, bundle, candidate, *, prior_findings=()):
    request = _request(request)
    verify(candidate, VERSION + '_candidate')
    context = prepare_domain(request, bundle)
    if (candidate['request_sha256'], candidate['material_sha256']) != (context['request_sha256'], bundle['artifact_sha256']):
        raise ValueError('review packet has a foreign candidate')
    return {'schema_version': VERSION + '_review_packet',
        'material': bundle['material'], 'input': bundle['claim_input'],
        'candidate': candidate, 'prior_findings': list(prior_findings),
        **({'human_constraints': bundle['human_constraints']} if bundle.get('human_constraints') else {}),
        'instructions': 'Review the complete afterimage, all semantic components including negation and qualifications, affected members and relationships, and all new viewpoints together. Return exact three finite sets using short references, without copying input or SHA. Independent_audit has not been performed.'}


def finalize_package(request, bundle, candidate, approval, provenance):
    """Add authentic program-owned review provenance after exact Opus approval.

    The program metadata below is deterministic, not semantic editing. The SQL
    boundary must authenticate both actual model calls and recompile the same
    candidate/approval against its exact current pins before applying this plan.
    """
    request = _request(request)
    verify(candidate, VERSION + '_candidate')
    verify(approval, VERSION + '_review')
    if approval['candidate_sha256'] != candidate['artifact_sha256'] or approval['approved'] is not True:
        raise ValueError('package requires exact passing complete candidate review')
    # Revalidate every finite review result, not merely an approved boolean.
    rebuilt = validate_review(request, bundle, candidate, approval['review'])
    if rebuilt != approval:
        raise ValueError('review approval does not match deterministic validation')
    required = {'proposal_model', 'review_model', 'proposal_artifact_sha256',
                'review_artifact_sha256', 'created_at'}
    if set(provenance) != required:
        raise ValueError('exact actual two-model provenance required')
    if (provenance['proposal_model'] != 'gpt-6.1-sol' or provenance['review_model'] != 'claude-opus-5-5'
            or not isinstance(provenance['created_at'], str) or not provenance['created_at'].strip()):
        raise ValueError('CVP 2.0 requires exact authorized GPT/Opus roles and actual timestamp')
    hashes = [provenance['proposal_artifact_sha256'], provenance['review_artifact_sha256']]
    if len(set(hashes)) != 2 or any(not isinstance(value, str) or len(value) != 64
                                  or any(char not in '0123456789abcdef' for char in value) for value in hashes):
        raise ValueError('actual distinct proposal and review artifact SHAs required')
    records = _records(bundle)
    refs = candidate['references']
    targets = sorted({value['object_id'] for ref, value in refs.items()
                      if value['collection'] == 'claims'
                      and ref in {row['claim_ref'] for row in candidate['component_plan']['components']}})
    package = {'package_id': _id(request, 'package', candidate['artifact_sha256'], bundle['group_key']),
        'metadata': {'schema_version': VERSION, 'request_sha256': candidate['request_sha256'],
            'candidate_sha256': candidate['artifact_sha256'], 'approval_sha256': approval['artifact_sha256'],
            'independent_audit': 'not_performed'},
        'viewpoint_identity_candidates': [], 'viewpoint_identity_decisions': []}
    for write in candidate['writes']:
        col, oid, ref = write['collection'], write['object_id'], write['ref']
        payload = deepcopy(write['payload'])
        vid = payload.get('viewpoint_id') if col != 'viewpoint_relations' else payload['source_viewpoint_id']
        candidate_id, decision_id = _id(request, 'candidate', ref, bundle['group_key']), _id(request, 'decision', ref, bundle['group_key'])
        if col == 'canonical_viewpoints':
            vid = oid
        if not vid:
            raise ValueError('compiled write lacks a viewpoint identity')
        identity_action = 'create_new' if (col == 'canonical_viewpoints' and write['before_revision'] is None) else 'match_existing'
        if col == 'canonical_viewpoints' and payload['identity_status'] != 'active':
            identity_action = ('merge_identities' if payload['identity_status'] in {'merged', 'redirected'}
                               else 'split_identity' if payload['identity_status'] == 'split' else 'retire_identity')
        view_ids = [] if identity_action == 'create_new' else [vid]
        generation_fingerprint = sha256_json({'policy_version': VERSION, 'group_key': bundle['group_key'],
            'candidate_sha256': candidate['artifact_sha256'], 'write_ref': ref,
            'proposal_artifact_sha256': provenance['proposal_artifact_sha256'],
            'review_artifact_sha256': provenance['review_artifact_sha256']})
        proposed_action = 'create_new' if identity_action == 'create_new' else 'match_existing'
        candidate_identity = {'claims': targets, 'viewpoints': view_ids, 'relations': [],
            'action': proposed_action, 'blockers': [], 'coverage_snapshot_id': None,
            'generation_fingerprint': generation_fingerprint, 'scope_manifest_sha256': candidate['material_sha256']}
        candidate_id = 'VIC-' + sha256_json(candidate_identity)[:20]
        if col == 'canonical_viewpoints' and write['before_revision'] is None:
            payload['created_from_candidate_id'] = candidate_id
        identity_candidate = {
            'identity_candidate_id': candidate_id, 'candidate_claim_ids': targets,
            'candidate_viewpoint_ids': view_ids, 'proposed_action': 'create_new' if identity_action == 'create_new' else 'match_existing',
            'scope_manifest_sha256': candidate['material_sha256'],
            'generation_fingerprint': generation_fingerprint, 'review_status': 'candidate'}
        resolved = payload.get('redirect_to_viewpoint_id', vid) if identity_action == 'merge_identities' else vid
        identity_decision = {
            'identity_decision_id': decision_id, 'identity_candidate_id': candidate_id,
            'decision': identity_action, 'resolved_viewpoint_id': resolved,
            'claim_link_decisions': ([{'claim_id': payload['claim_id'], 'link_type': payload['link_type']}]
                                     if col == 'viewpoint_claim_links' else []),
            'reviewer_kind': 'system', 'reviewer_id': VERSION,
            'approval_basis': 'dual_model_consensus', 'reason': 'Exact compact candidate and complete Opus review.',
            'input_sha256': generation_fingerprint,
            'review_artifact_sha256': provenance['review_artifact_sha256'], 'policy_version': VERSION,
            'reviewer_model_ids': sorted([provenance['proposal_model'], provenance['review_model']]),
            'semantic_call_artifact_sha256s': sorted(hashes), 'created_at': provenance['created_at'],
            'review_status': 'system_approved'}
        if col == 'viewpoint_revisions':
            payload['provenance'] = {'basis_identity_decision_ids': [decision_id],
                                     'review_artifact_sha256': provenance['review_artifact_sha256']}
            payload['approved_by'] = VERSION
            payload['approved_at'] = provenance['created_at']
        elif col == 'viewpoint_relations':
            payload['review_provenance'] = {'basis_identity_decision_ids': [decision_id],
                                           'review_artifact_sha256': provenance['review_artifact_sha256']}
        for collection, row in [('viewpoint_identity_candidates', identity_candidate),
                                ('viewpoint_identity_decisions', identity_decision), (col, payload)]:
            typed = KNOWLEDGE_COLLECTIONS[collection][0].model_validate(row).model_dump(mode='json')
            if collection == 'canonical_viewpoints' and not statement_recall(request):
                for field in ('summary', 'summary_basis'):
                    if field not in row:
                        typed.pop(field, None)
            package.setdefault(collection, []).append(typed)
    # No caller-controlled row or content can bypass this final typed pass.
    for col, rows in package.items():
        if col in KNOWLEDGE_COLLECTIONS:
            for row in rows:
                KNOWLEDGE_COLLECTIONS[col][0].model_validate(row)
    validate_summary_afterimage(request, bundle, candidate, package)
    return package


def validate_summary_afterimage(request, bundle, candidate, package):
    """Recompute each affected persisted summary basis from the exact afterimage."""
    request = _request(request)
    if not statement_recall(request):
        return
    if candidate.get('summary_material_pending'):
        raise ValueError('incomplete summary candidate cannot be submitted')
    if package.get('metadata', {}).get('candidate_sha256') != candidate['artifact_sha256']:
        raise ValueError('summary package is not bound to its reviewed candidate')
    payloads = {(row['collection'], row['object_id']): row['payload'] for row in candidate['records']}
    for col in COLLECTIONS:
        id_field = KNOWLEDGE_COLLECTIONS[col][1]
        for row in package.get(col, []):
            payloads[col, row[id_field]] = row
    for row in package.get('canonical_viewpoints', []):
        if row['identity_status'] != 'active':
            continue
        if not row.get('summary') or len(row['summary']) > 500:
            raise ValueError('persisted viewpoint needs a bounded complete summary')
        if row.get('summary_basis') != summary_basis_for_records(payloads, row['viewpoint_id'],
                human_source_decisions=bundle.get('human_source_decisions',[])):
            raise ValueError('persisted summary basis differs from current post-write members and source')


def revalidate_candidate(request, bundle, candidate, raw, *, previous=None, prior_findings=()):
    """Same compiler for preflight and SQL transaction validation."""
    actual = compile_candidate(request, bundle, raw, previous=previous, prior_findings=prior_findings)
    if actual != candidate:
        raise ValueError('saved candidate differs from deterministic full recompilation')
    return actual



def _rebase_context(request, old_bundle, new_bundle, previous):
    request = _request(request)
    old_records, new_records = _records(old_bundle), _records(new_bundle)
    verify(previous, VERSION + '_candidate')
    stable = sha256_json({k: v for k, v in request.model_dump(mode='json').items() if k != 'input'})
    if (previous['request_scope_sha256'] != stable or old_bundle['group_key'] != new_bundle['group_key']
            or previous['material_sha256'] != old_bundle['artifact_sha256']):
        raise ValueError('rebase cannot change action, group, authority or budget')
    old_constraints = {row['artifact_sha256'] for row in old_bundle.get('human_constraints', [])}
    new_constraints = {row['artifact_sha256'] for row in new_bundle.get('human_constraints', [])}
    if not old_constraints <= new_constraints:
        raise ValueError('rebase cannot discard historical human constraints')
    old_audits = {row['observation']['audit_id'] for row in old_bundle.get('quality_findings', [])}
    new_audits = {row['observation']['audit_id'] for row in new_bundle.get('quality_findings', [])}
    if not old_audits <= new_audits:
        raise ValueError('rebase cannot discard independent audit finding history')
    same_input = old_bundle['claim_input'] == new_bundle['claim_input']
    if same_input and any(new_records.get(key) != row for key, row in old_records.items()
                          if key[0] not in {'evidence_steps', 'source_fragments', 'source_documents',
                                            'citations', 'claim_relations'}):
        raise ValueError('context-only rebase cannot conceal changed semantic dependency records')
    new_context = prepare_domain(request, new_bundle)
    for ref, value in previous['references'].items():
        if ref in new_context['references'] and new_context['references'][ref] != value:
            raise ValueError('rebase moved an existing short reference to another object')
    for row in previous['writes']:
        key = row['collection'], row['object_id']
        if row['before_revision'] is not None:
            current = new_records.get(key)
            if current is None or (current['revision'], current['content_sha256']) != (row['before_revision'], row['before_sha256']):
                raise ValueError('rebase cannot reuse a write against a changed preimage')
    return request, new_context, same_input


def prepare_rebase_frame(request, old_bundle, new_bundle, previous, *, prior_findings=()):
    """Carry qualified upstream changes without inventing semantic revisions.

    This sealed transport is never an approved candidate. Changed wording
    requires actual model positions and complete old-component mappings;
    support-only changes retain the old plan and still require a new review.
    """
    request, context, same_input = _rebase_context(request, old_bundle, new_bundle, previous)
    carried = list(prior_findings)
    return seal({**{k: deepcopy(v) for k, v in previous.items() if k != "artifact_sha256"},
        "schema_version": VERSION + "_rebase_frame",
        "request_sha256": context["request_sha256"], "material_sha256": new_bundle["artifact_sha256"],
        "candidate_valid_for_commit": False, "requires_explicit_component_plan": not same_input,
        "old_bundle": deepcopy(old_bundle), "previous_candidate": deepcopy(previous),
        "carried_prior_findings": carried})


def rebind_candidate(request, old_bundle, new_bundle, previous, *, prior_findings=(), replacement_raw=None):
    """Explicit material/input rebase within the same action and total budget.

    This produces a NEW unapproved candidate. It carries the old full component
    plan, finding identities and candidate lineage; no earlier review can bind
    its new artifact SHA. Source-only expansion retains exact semantic records.
    Changed wording requires model-provided complete positions and all old to
    new mappings, rather than programmatic component deletion.
    """
    request, new_context, same_input = _rebase_context(request, old_bundle, new_bundle, previous)
    parsed = (IncrementalCandidate if statement_recall(request) else Candidate).model_validate(replacement_raw or {})
    if not same_input and (parsed.components is None or not parsed.component_mappings):
        raise ValueError('new Claim wording requires explicit replacement plan and all old mappings')
    rebound = deepcopy(previous)
    rebound.update(request_sha256=new_context['request_sha256'], material_sha256=new_bundle['artifact_sha256'])
    rebound = seal({k: v for k, v in rebound.items() if k != 'artifact_sha256'})
    result = compile_candidate(request, new_bundle, parsed, previous=rebound, prior_findings=prior_findings)
    history = [*previous.get('rebase_history', []), {
        'candidate_sha256': previous['artifact_sha256'], 'material_sha256': old_bundle['artifact_sha256'],
        'component_plan': previous['component_plan'], 'findings': previous['findings'],
        'prior_findings': previous['prior_findings'], 'wording_changed': not same_input}]
    return seal({**{k: v for k, v in result.items() if k != 'artifact_sha256'}, 'rebase_history': history})


# Human review is a narrow, source-grounded exit, not another semantic stage.
class SourcePosition(Contract):
    source_ref: str
    section_ref: str
    paragraph_key: str
    start_char: int = Field(ge=0, strict=True)
    end_char: int = Field(gt=0, strict=True)


class DisputeJudgment(Contract):
    artifact: dict[str, Any]
    position_path: list[str | int] = Field(min_length=1)
    citations: list[SourcePosition] = Field(min_length=1)


class DisputeDiagnostics(Contract):
    material_complete: Literal[True]
    versions_consistent: Literal[True]
    schema_valid: Literal[True]
    engineering_clear: Literal[True]
    evidence_cannot_resolve: Literal[True]
    reason: str = Field(min_length=1)


class HumanOption(Contract):
    ref: str = Field(min_length=1)
    label: str = Field(min_length=1)


class HumanDecision(Contract):
    decision_id: str = Field(min_length=1)
    actor_kind: Literal['human']
    kind: Literal['interpretation', 'hold', 'exact_retain']
    target_refs: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1)
    selected_option: str | None = None
    authorization: Binding
    supersedes_constraint_ids: list[str] = Field(default_factory=list)


def _at(value, path):
    for key in path:
        if isinstance(value, list):
            if type(key) is not int or not 0 <= key < len(value):
                raise ValueError('source or response path is outside its exact bound value')
        elif not isinstance(value, dict) or key not in value:
            raise ValueError('source or response path is outside its exact bound value')
        value = value[key]
    return value


def _source_position(bundle, position):
    source = [row for row in bundle['material'].get('sources', []) if row['source_ref'] == position.source_ref]
    if len(source) != 1:
        raise ValueError('dispute source is not in the common material')
    sections = [row for row in source[0]['sections'] if row['section_ref'] == position.section_ref]
    if len(sections) != 1:
        raise ValueError('dispute cites an unsupplied source section')
    paragraphs = [row for row in sections[0]['paragraphs'] if row['paragraph_key'] == position.paragraph_key]
    if len(paragraphs) != 1:
        raise ValueError('dispute cites an unsupplied source paragraph')
    text = str(paragraphs[0]['row'].get('text') or '')
    if not 0 <= position.start_char < position.end_char <= len(text):
        raise ValueError('dispute citation is outside exact raw Unicode source positions')
    return {**position.model_dump(mode='json'), 'exact_text': text[position.start_char:position.end_char],
            'source_body_sha256': source[0]['source_body_sha256']}


def build_content_dispute(request, bundle, *, candidate, proposal_judgment, review_judgment,
                          diagnostics, options, category, standard, previous=None, prior_findings=()):
    """Build a minimal human question only from actual, opposed model judgments.

    Engineering diagnostics are explicit caller-supplied program facts, bound to
    this candidate. A disagreement, missing context or exhausted quota alone is
    never a qualifying content dispute. SQL admission authenticates the supplied
    raw artifacts against its already returned call slots.
    """
    request = _request(request)
    context = prepare_domain(request, bundle)
    verify(candidate, VERSION + '_candidate')
    if (candidate['request_sha256'], candidate['material_sha256']) != (context['request_sha256'], bundle['artifact_sha256']):
        raise ValueError('content dispute is not bound to this exact current candidate')
    checks = DisputeDiagnostics.model_validate(diagnostics)
    if category not in {'identity', 'granularity', 'link_role'} or not isinstance(standard, str) or not standard.strip():
        raise ValueError('content dispute needs an explicit identity/granularity/link-role standard')
    choices = [HumanOption.model_validate(option) for option in options]
    if len(choices) < 2 or len({option.ref for option in choices}) != len(choices):
        raise ValueError('content dispute needs distinct concrete disposition options')
    judgments = []
    originals = {}
    for role, value in [('proposal', proposal_judgment), ('review', review_judgment)]:
        judgment = DisputeJudgment.model_validate(value)
        artifact = judgment.artifact
        if artifact.get('artifact_sha256') != sha256_json({k: v for k, v in artifact.items() if k != 'artifact_sha256'}):
            raise ValueError('content dispute raw model artifact seal differs')
        expected_role = ('gpt-6.1-sol', 'codex_subscription') if role == 'proposal' else ('claude-opus-5-5', 'claude_subscription')
        if (artifact.get('model_id'), artifact.get('backend')) != expected_role:
            raise ValueError('content dispute needs the two actual independent semantic roles')
        raw = artifact.get('response')
        expected_result = 'candidate' if role == 'proposal' else 'review'
        if (not isinstance(raw, dict) or raw.get('result') != expected_result
                or raw.get('context_requests') or raw.get('member_requests')
                or artifact.get('response_sha256') != sha256_json(raw)):
            raise ValueError('context/resource/unknown outcomes cannot become content judgments')
        if role == 'proposal':
            # Retain the original previous/finding inputs from the caller's saved
            # call identity, rather than guessing a different proposal packet.
            if raw.get('candidate') is None:
                raise ValueError('content dispute proposal is missing')
        elif raw.get('review') is None:
            raise ValueError('content dispute review is missing')
        packet = (proposal_packet(request, bundle, previous=previous, prior_findings=prior_findings)
                  if role == 'proposal' else review_packet(request, bundle, candidate, prior_findings=prior_findings))
        if artifact.get('request_payload_sha256') != sha256_json(packet):
            raise ValueError('content dispute judgment belongs to different material or candidate')
        if judgment.position_path[0] != expected_result or judgment.position_path[-1] not in {'reason', 'fields_json'}:
            raise ValueError('content dispute must select actual substantive candidate/review wording')
        position = _at(raw, judgment.position_path)
        if not isinstance(position, str) or not position.strip():
            raise ValueError('dispute judgment must select the actual substantive model wording')
        judgments.append({'role': role, 'model': expected_role[0],
            'artifact_sha256': artifact['artifact_sha256'], 'position_path': judgment.position_path,
            'position': position, 'citations': [_source_position(bundle, citation) for citation in judgment.citations]})
        originals[role] = artifact
    revalidate_candidate(request, bundle, candidate, originals['proposal']['response']['candidate'],
                         previous=previous, prior_findings=prior_findings)
    reviewed = validate_review(request, bundle, candidate, originals['review']['response']['review'],
                               prior_findings=prior_findings)
    if reviewed['approved'] or not reviewed['findings']:
        raise ValueError('content dispute needs an actual unresolved independent semantic finding')
    if judgments[0]['position'].strip() == judgments[1]['position'].strip():
        raise ValueError('identical judgments are not an unresolved content disagreement')
    review_position = DisputeJudgment.model_validate(review_judgment).position_path
    if len(review_position) != 4 or review_position[:2] != ['review', 'findings'] or review_position[-1] != 'reason':
        raise ValueError('content dispute needs one concrete independent unresolved finding')
    selected_finding = _at(originals['review']['response'], review_position[:-1])
    allowed_categories = {'link_role'} if category == 'link_role' else {'identity', 'coverage', 'wording'}
    if selected_finding.get('category') not in allowed_categories:
        raise ValueError('source/engineering issues cannot be relabeled as an identity disagreement')
    disputed_refs = set(selected_finding['target_refs'])
    for part in candidate['component_dispositions']:
        if part['component_ref'] in disputed_refs:
            disputed_refs.update(ref for ref in (part['target_ref'], part['link_ref']) if ref is not None)
    bindings = {}
    records = _records(bundle)
    for ref, value in candidate['references'].items():
        key = value['collection'], value['object_id']
        if key in records and ref in disputed_refs and value['collection'] in COLLECTIONS:
            row = records[key]
            bindings[ref] = {**value, 'revision': row['revision'], 'content_sha256': row['content_sha256']}
    for part in candidate['component_plan']['components']:
        if part['component_ref'] not in disputed_refs:
            continue
        claim = candidate['references'][part['claim_ref']]
        bindings[part['component_ref']] = {'component_id': part['component_id'],
            'claim_id': claim['object_id'], 'spans': part['spans']}
    if not bindings:
        raise ValueError('content dispute is outside the CVP identity/component domain')
    return seal({'schema_version': VERSION + '_content_dispute', 'action_id': request.action_id,
        'group_key': bundle['group_key'], 'request_sha256': context['request_sha256'],
        'material_sha256': bundle['artifact_sha256'], 'candidate_sha256': candidate['artifact_sha256'],
        'category': category, 'standard': standard, 'diagnostics': checks.model_dump(mode='json'),
        'options': [option.model_dump(mode='json') for option in choices], 'judgments': judgments,
        'judgment_artifacts': originals, 'target_bindings': bindings,
        'automatic_content_approval': False, 'budget_reset': False})


def validate_human_constraint(request, dispute, decision, *, existing_constraints=()):
    """Human interpretation/hold/retain is a constraint, never a model pass."""
    request = _request(request)
    verify(dispute, VERSION + '_content_dispute')
    if dispute['action_id'] != request.action_id or dispute['request_sha256'] != sha256_json(request.model_dump(mode='json')):
        raise ValueError('human decision must return to the exact original task')
    decision = HumanDecision.model_validate(decision)
    if (len(set(decision.target_refs)) != len(decision.target_refs)
            or not set(decision.target_refs) <= set(dispute['target_bindings'])):
        raise ValueError('human constraint targets are foreign or repeated')
    if decision.selected_option is not None and decision.selected_option not in {row['ref'] for row in dispute['options']}:
        raise ValueError('human selected an unknown dispute option')
    bindings = {ref: dispute['target_bindings'][ref] for ref in decision.target_refs}
    old = {}
    for constraint in existing_constraints:
        verify(constraint, VERSION + '_human_constraint')
        if constraint['action_id'] != request.action_id or constraint['group_key'] != dispute['group_key']:
            raise ValueError('human supersession cannot cross original task/group')
        old[constraint['constraint_id']] = constraint
    if (len(set(decision.supersedes_constraint_ids)) != len(decision.supersedes_constraint_ids)
            or not set(decision.supersedes_constraint_ids) <= set(old)):
        raise ValueError('human supersession must name the exact existing protection')
    def owners(values):
        return {(v.get('collection', 'claims'), v.get('object_id', v.get('claim_id'))) for v in values}
    for ident in decision.supersedes_constraint_ids:
        if not owners(old[ident]['target_bindings'].values()) <= owners(bindings.values()):
            raise ValueError('human replacement decision does not cover the protected target')
    if decision.kind == 'exact_retain' and any('collection' not in row for row in bindings.values()):
        raise ValueError('exact retain requires an existing pinned record, not an unknown future component')
    return seal({'schema_version': VERSION + '_human_constraint',
        'constraint_id': 'CVPHC-' + sha256_json({'action': request.action_id, 'decision_id': decision.decision_id})[:24],
        'action_id': request.action_id, 'group_key': dispute['group_key'],
        'dispute_sha256': dispute['artifact_sha256'], 'decision': decision.model_dump(mode='json'),
        'kind': decision.kind, 'reason': decision.reason, 'target_bindings': bindings,
        'supersedes_constraint_ids': decision.supersedes_constraint_ids,
        'original_judgments': dispute['judgments'], 'original_model_judgments_unchanged': True,
        'approval_granted': False, 'budget_reset': False})


def _constraint_findings(request, bundle, candidate_refs, parts):
    """Expose interpretation as an old finding, preserving version applicability."""
    result = []
    supplied = bundle.get('human_constraints', [])
    identities = {row['constraint_id']: row for row in supplied}
    if len(identities) != len(supplied):
        raise ValueError('duplicate human constraint identity')
    superseded = {}
    for row in supplied:
        for ident in row.get('supersedes_constraint_ids', []):
            if ident not in identities or ident == row['constraint_id'] or ident in superseded:
                raise ValueError('human supersession has an unknown, cyclic or repeated protection')
            superseded[ident] = row['constraint_id']
    for ident in superseded:
        seen = set()
        cursor = ident
        while cursor in superseded:
            if cursor in seen:
                raise ValueError('cyclic human protection supersession')
            seen.add(cursor)
            cursor = superseded[cursor]
    for constraint in supplied:
        verify(constraint, VERSION + '_human_constraint')
        if constraint['action_id'] != request.action_id or constraint['group_key'] != bundle['group_key']:
            raise ValueError('human constraint belongs to a different original task/group')
        if constraint.get('approval_granted') is not False or constraint.get('budget_reset') is not False:
            raise ValueError('human constraints cannot approve a candidate or reset a budget')
        affected = set()
        for binding in constraint['target_bindings'].values():
            if 'object_id' in binding:
                affected.update(ref for ref, value in candidate_refs.items() if value == {
                    'collection': binding['collection'], 'object_id': binding['object_id']})
            else:
                affected.update(ref for ref, part in parts.items()
                    if candidate_refs[part['claim_ref']]['object_id'] == binding['claim_id'])
        if not affected:
            raise ValueError('human constraint has no current scope; explicit applicability review is required')
        result.append({'finding_id': constraint['constraint_id'], 'target_refs': sorted(affected),
            'category': 'permission', 'reason': constraint['reason'], 'human_constraint': constraint,
            'not_a_model_rejection': True,
            'superseded_by_human_constraint': superseded.get(constraint['constraint_id'])})
    return result


def enforce_human_constraints(request, bundle, candidate):
    """Reject conflicts with exact human holds/retention; never weaken them."""
    request = _request(request)
    original = _records(bundle)
    states = {(row['collection'], row['object_id']): row['payload'] for row in candidate['records']}
    used = {row['ref'] for row in candidate['writes']}
    for row in candidate['component_dispositions']:
        used.add(row['component_ref'])
        used.update(ref for ref in (row['target_ref'], row['link_ref']) if ref is not None)
    refs = candidate['references']
    parts = {row['component_ref']: row for row in candidate['component_plan']['components']}
    for finding in _constraint_findings(request, bundle, refs, parts):
        constraint = finding['human_constraint']
        if finding.get('superseded_by_human_constraint') is not None:
            continue
        if constraint['kind'] == 'hold' and used & set(finding['target_refs']):
            raise ValueError('candidate depends on an unresolved exact human hold')
        if constraint['kind'] == 'exact_retain':
            for binding in constraint['target_bindings'].values():
                key = binding['collection'], binding['object_id']
                row = original.get(key)
                if (row is None or (row['revision'], row['content_sha256']) != (binding['revision'], binding['content_sha256'])
                        or semantic_record_sha(states[key]) != binding['content_sha256']):
                    raise ValueError('candidate conflicts with exact human retention or its pinned version')
    return True


class QualityPin(Contract):
    collection: str
    object_id: str = Field(min_length=1)
    revision: int = Field(ge=1, strict=True)
    content_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def cvp_scope(self):
        if self.collection not in COLLECTIONS:
            raise ValueError('CVP quality observations target CVP objects; Claim/source repairs stay upstream')
        return self


class QualityReader(Contract):
    model_id: str = Field(min_length=1)
    participating_model_ids: list[str]
    code_path: str = Field(min_length=1)
    participating_code_paths: list[str]
    source_mode: Literal['direct_physical_originals']
    imports_pipeline_readers: Literal[False]

    @model_validator(mode='after')
    def independent(self):
        if self.model_id in self.participating_model_ids or self.code_path in self.participating_code_paths:
            raise ValueError('independent quality audit cannot reuse a participating model/reader')
        return self


class QualityFinding(Contract):
    ref: str = Field(min_length=1)
    category: Literal['identity', 'coverage', 'wording', 'link_role', 'member', 'source']
    target_keys: list[str] = Field(min_length=1)
    report_path: list[str | int] = Field(min_length=1)


class QualityObservation(Contract):
    schema_version: Literal['wang_cvp_management_v2_quality_observation'] = VERSION + '_quality_observation'
    audit_id: str = Field(min_length=1)
    report_binding: Binding
    raw_report: dict[str, Any]
    reader: QualityReader
    cohort_pins: list[QualityPin] = Field(min_length=1)
    sample_keys: list[str] = Field(min_length=1)
    findings: list[QualityFinding]
    cohort_description: str = Field(min_length=1)


def _state_map(current_records):
    if isinstance(current_records, dict):
        items = [(tuple(key.split('/', 1)) if isinstance(key, str) else key, value)
                 for key, value in current_records.items()]
    else:
        items = [((row['collection'], row['object_id']), row) for row in current_records]
    result = {}
    for key, row in items:
        if (len(key) != 2 or key in result or row.get('retired') or row.get('retired_at')
                or semantic_record_sha(row['payload']) != row['content_sha256']):
            raise ValueError('quality admission requires consistent exact current record pins')
        result[key] = row
    return result


def _pin_for(key, row):
    return {'collection': key[0], 'object_id': key[1], 'revision': row['revision'],
            'content_sha256': row['content_sha256']}


def _require_pin(pin, current):
    key = pin['collection'], pin['object_id']
    row = current.get(key)
    if row is None or (row['revision'], row['content_sha256']) != (pin['revision'], pin['content_sha256']):
        raise ValueError('quality finding/admission is bound to a different current version')
    return key, row


def register_quality_findings(audit, current_records):
    """Bind an already executed independent report to its exact current cohort.

    The caller authenticates the original report file and independent reader
    provenance. This pure function keeps the raw report, scope, sample and
    report-path wording, never upgrades Opus operation review into an audit.
    """
    observation = QualityObservation.model_validate(audit)
    current = _state_map(current_records)
    pins = {pin.collection + '/' + pin.object_id: pin.model_dump(mode='json') for pin in observation.cohort_pins}
    if len(pins) != len(observation.cohort_pins):
        raise ValueError('quality cohort repeats an object')
    for pin in pins.values():
        _require_pin(pin, current)
    if (len(set(observation.sample_keys)) != len(observation.sample_keys)
            or not set(observation.sample_keys) <= set(pins)):
        raise ValueError('quality sample must be an explicit unique subset of its cohort')
    if len({row.ref for row in observation.findings}) != len(observation.findings):
        raise ValueError('duplicate independent audit finding reference')
    findings = []
    for finding in observation.findings:
        if (len(set(finding.target_keys)) != len(finding.target_keys)
                or not set(finding.target_keys) <= set(observation.sample_keys)):
            raise ValueError('quality objection names an unsampled or repeated target')
        reason = _at(observation.raw_report, finding.report_path)
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('quality finding must select actual substantive report wording')
        targets = [pins[key] for key in sorted(finding.target_keys)]
        findings.append({'finding_id': 'CVPAF-' + sha256_json({'audit_id': observation.audit_id,
                'targets': sorted(finding.target_keys), 'category': finding.category,
                'report_path': finding.report_path})[:24],
            'category': finding.category, 'reason': reason, 'report_path': finding.report_path,
            'target_pins': targets, 'status': 'open', 'resolution': None})
    return seal({'schema_version': VERSION + '_quality_findings',
        'observation': observation.model_dump(mode='json'), 'findings': findings,
        'independent_audit': {'status': 'findings' if findings else 'completed',
            'sample_keys': observation.sample_keys, 'cohort_description': observation.cohort_description,
            'coverage': 'cohort' if set(observation.sample_keys) == set(pins) else 'sample_only'},
        'operation_completion_changed': False})


def resolve_quality_findings(registration, evidence, readback, current_records):
    """Close reviewed findings through the normal exact maintenance/readback.

    The independent audit's original objections and result remain unchanged.
    This records a subsequent maintenance resolution, not a new audit pass.
    """
    verify(registration, VERSION + '_quality_findings')
    from .cvp_management_authority import validate_readback
    validate_readback(evidence, readback, current_records)
    current = _state_map({key: row for key, row in current_records.items()
                          if not row.get('retired') and not row.get('retired_at')})
    candidate, approval = evidence['candidate'], evidence['approval']
    resolutions = {candidate['prior_finding_refs'][row['ref']]['finding_id']: row['disposition']
                   for row in approval['review']['prior_findings']}
    result = deepcopy(registration)
    for finding in result['findings']:
        # A previously resolved target may have changed again. The compiler
        # reintroduces that finding for current review; retain the original
        # audit but refresh the resolution pins after this approved readback.
        if resolutions.get(finding['finding_id']) != 'resolved':
            continue
        targets = []
        for pin in finding['target_pins']:
            key = pin['collection'], pin['object_id']
            if key not in current:
                raise ValueError('maintenance resolution lacks its exact affected current target')
            targets.append(_pin_for(key, current[key]))
        finding.update(status='resolved', resolution={
            'candidate_sha256': candidate['artifact_sha256'], 'approval_sha256': approval['artifact_sha256'],
            'readback_sha256': readback['artifact_sha256'], 'current_target_pins': targets,
            'basis': 'normal_complete_maintenance_and_program_readback', 'new_independent_audit': False})
    return seal({k: value for k, value in result.items() if k != 'artifact_sha256'})


def validate_scope_admission(document, current_records, *, purpose, target_keys):
    """Authenticate a separately approved quality-scope receipt, not an operation pass."""
    verify(document, VERSION + '_scope_admission')
    if (document.get('purpose') != purpose or document.get('approved') is not True
            or not isinstance(document.get('scope_id'), str) or not document['scope_id']):
        raise ValueError('formal use requires an explicit approved matching quality scope')
    Binding.model_validate(document['authorization'])
    if not document.get('evidence_bindings'):
        raise ValueError('quality scope needs its separately authenticated assessment evidence')
    for binding in document['evidence_bindings']:
        Binding.model_validate(binding)
    current = _state_map(current_records)
    values = [QualityPin.model_validate(value).model_dump(mode='json') for value in document['target_pins']]
    allowed = {value['collection'] + '/' + value['object_id']: value for value in values}
    if len(allowed) != len(values) or not set(target_keys) <= set(allowed):
        raise ValueError('quality scope does not cover the requested formal use')
    for key in target_keys:
        _require_pin(allowed[key], current)
    return True


def assess_usage_admission(target_keys, current_records, quality_findings=(), *,
                           purpose='existing_use', scope_admission=None):
    """Keep current use, independent objections and operation completion separate."""
    if purpose not in {'existing_use', 'expanded_production', 'formal_downstream'}:
        raise ValueError('explicit supported usage purpose required')
    if not target_keys or len(set(target_keys)) != len(target_keys):
        raise ValueError('usage targets must be explicit and unique')
    current = _state_map(current_records)
    blockers = {key: [] for key in target_keys}
    for key in target_keys:
        parts = tuple(key.split('/', 1))
        if len(parts) != 2 or parts not in current:
            blockers[key].append('missing_current_target')
            continue
        payload = current[parts]['payload']
        if _held(payload) or (parts[0] == 'canonical_viewpoints' and payload['identity_status'] != 'active'):
            blockers[key].append('held_or_inactive_target')
    if purpose != 'existing_use' or scope_admission is not None:
        try:
            if scope_admission is None:
                raise ValueError('quality scope missing')
            validate_scope_admission(scope_admission, current, purpose=purpose, target_keys=target_keys)
        except (ValueError, KeyError, TypeError) as exc:
            for key in target_keys:
                blockers[key].append('scope_quality_admission_required: ' + str(exc))
    dependencies = usage_dependency_keys(target_keys, current)
    audits = []
    for registration in quality_findings:
        verify(registration, VERSION + '_quality_findings')
        audits.append(registration['independent_audit'])
        for finding in registration['findings']:
            for pin in finding['target_pins']:
                key = pin['collection'] + '/' + pin['object_id']
                affected = [target for target in target_keys if key in dependencies[target]]
                if not affected:
                    continue
                closed = finding['status'] == 'resolved' and finding.get('resolution') is not None
                if closed:
                    replacement = [value for value in finding['resolution']['current_target_pins']
                                   if (value['collection'], value['object_id']) == (pin['collection'], pin['object_id'])]
                    try:
                        if len(replacement) != 1:
                            raise ValueError('resolution target missing')
                        _require_pin(replacement[0], current)
                    except ValueError:
                        for target in affected:
                            blockers[target].append('quality_resolution_requires_current_revalidation:' + finding['finding_id'])
                else:
                    for target in affected:
                        blockers[target].append('open_independent_quality_finding:' + finding['finding_id'])
    eligible = [key for key in target_keys if not blockers[key]]
    return seal({'schema_version': VERSION + '_usage_admission', 'purpose': purpose,
        'eligible_keys': eligible, 'blocked': [{'target_key': key, 'reasons': reasons}
                                             for key, reasons in blockers.items() if reasons],
        'status': 'eligible' if len(eligible) == len(target_keys) else 'partial' if eligible else 'blocked',
        'independent_audits': audits, 'operation_completion_changed': False,
        'scope_quality_admission': scope_admission['artifact_sha256'] if scope_admission is not None else None})



def _quality_prior_findings(bundle, refs):
    records = _records(bundle)
    reverse = {(value['collection'], value['object_id']): ref for ref, value in refs.items()}
    result = []
    for registration in bundle.get('quality_findings', []):
        verify(registration, VERSION + '_quality_findings')
        for finding in registration['findings']:
            relevant = [(pin, (pin['collection'], pin['object_id'])) for pin in finding['target_pins']
                        if (pin['collection'], pin['object_id']) in records]
            if not relevant:
                continue
            closed = finding['status'] == 'resolved' and finding.get('resolution') is not None
            if closed:
                resolution = {(pin['collection'], pin['object_id']): pin
                              for pin in finding['resolution']['current_target_pins']}
                if all(key in resolution and (records[key]['revision'], records[key]['content_sha256']) ==
                       (resolution[key]['revision'], resolution[key]['content_sha256']) for _, key in relevant):
                    continue
            result.append({'finding_id': finding['finding_id'],
                'target_refs': sorted(reverse[key] for _, key in relevant),
                'category': finding['category'], 'reason': finding['reason'],
                'independent_audit_observation_sha256': registration['artifact_sha256'],
                'original_target_pins': finding['target_pins'],
                'version_applicability_requires_review': any(
                    (records[key]['revision'], records[key]['content_sha256']) !=
                    (pin['revision'], pin['content_sha256']) for pin, key in relevant),
                'original_independent_audit_result': registration['independent_audit']})
    return result



def usage_dependency_keys(target_keys, current_records):
    """Direct current CVP use dependencies; caller supplies complete SQL ownership.

    This is not thematic retrieval. A defective active link/relationship affects
    its actual endpoint's use; unrelated CVPs remain independently eligible.
    """
    current = _state_map(current_records)
    result = {target: {target} for target in target_keys}
    for target in target_keys:
        key = tuple(target.split('/', 1))
        row = current.get(key)
        if not row:
            continue
        payload = row['payload']
        vids = set()
        if key[0] == 'canonical_viewpoints':
            vids.add(key[1])
        elif key[0] == 'viewpoint_revisions':
            vids.add(payload['viewpoint_id'])
        elif key[0] == 'viewpoint_claim_links':
            vids.add(payload['viewpoint_id'])
        elif key[0] == 'viewpoint_relations':
            vids.update([payload['source_viewpoint_id'], payload['target_viewpoint_id']])
        for vid in vids:
            result[target].add('canonical_viewpoints/' + vid)
            view = current.get(('canonical_viewpoints', vid))
            if view:
                result[target].add('viewpoint_revisions/' + view['payload']['current_revision_id'])
        if key[0] == 'canonical_viewpoints':
            for (col, oid), state in current.items():
                value = state['payload']
                if value.get('effective_state') != 'active':
                    continue
                if ((col == 'viewpoint_claim_links' and value['viewpoint_id'] == key[1])
                        or (col == 'viewpoint_relations' and key[1] in
                            {value['source_viewpoint_id'], value['target_viewpoint_id']})):
                    result[target].add(col + '/' + oid)
    return result



def _member_meaning(payload):
    result = {key: deepcopy(payload.get(key)) for key in ('claim_id', 'viewpoint_id', 'link_type',
        'effective_state', 'supporting_relation_ids', 'occurrence_refs')}
    locator = payload.get('component_locator') or payload.get('component_position_locator')
    if locator:
        result['positions'] = locator.get('canonical_spans', locator.get('spans'))
    return result
