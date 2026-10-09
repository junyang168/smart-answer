"""Normal CVP scope enrichment, sharing SQL actions and existing model stages.

Scope is not a coverage index. No Claim/source edit, identity merge, semantic
relation change, or old-audit waiver is implied by this domain policy.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field
from backend.pipeline import source_reconciliation as c
from backend.pipeline.deferred_claim_repair import JsonProposal, decode_proposal
from backend.api.canonical_repository.viewpoint_foundation import sha256_json

VERSION = 'wang_cvp_scope_management_v1'


class Policy(c.Contract):
    schema_version: Literal['wang_cvp_scope_management_v1'] = VERSION
    authorization_id: str = Field(min_length=1)
    authorization_quote: str = Field(min_length=1)
    human_content_approval: Literal[False] = False
    scope_fields: list[Literal['scripture_scope']]
    preserve_core_identity: Literal[True] = True
    preserve_claims_evidence_sources: Literal[True] = True
    correction_limit: Literal[1] = 1


class Citation(c.Contract):
    claim_id: str
    source_id: str
    paragraph_key: str
    exact_quote: str = Field(min_length=1)


class Assessment(c.Contract):
    action: Literal['scope_enrichment', 'retain', 'needs_direction']
    reason: str = Field(min_length=1)
    # All current link Claims, including supports; not just equivalent members.
    claim_ids: list[str]
    citations: list[Citation] = Field(min_length=1)


class Proposal(c.Proposal):
    scope_assessment: Assessment


class JsonScopeProposal(JsonProposal):
    scope_assessment: Assessment


def effective_proposal(response):
    value = JsonScopeProposal.model_validate(response).model_dump()
    assessment = value.pop('scope_assessment')
    return Proposal.model_validate({**decode_proposal(value), 'scope_assessment': assessment})


def read_policy(binding, snapshots=None):
    value = json.loads(c.bound_bytes(binding, snapshots))
    if 'artifact_sha256' in value:
        stated = value.pop('artifact_sha256')
        if stated != sha256_json(value):
            raise ValueError('CVP scope policy seal drift')
    policy = Policy.model_validate(value)
    if policy.scope_fields != ['scripture_scope']:
        raise ValueError('scope policy may not change audience/historical conditions')
    return policy


def direction(manifest, snapshots=None):
    return json.loads(c.bound_bytes(manifest.viewpoint_scope_repair, snapshots))


def scope_inventory(states, viewpoint_id):
    head = c.key('canonical_viewpoints', viewpoint_id)
    revision = c.key('viewpoint_revisions', states[head]['current_revision_id'])
    forward, reverse = c.dependency_index(states)
    affected = {head, revision} | reverse[head] | reverse[revision]
    # Historical CVR/decisions remain immutable context. Only current links and
    # current relation references move, with other direct consumers disclosed.
    affected = {k for k in affected if k == revision or not k.startswith('viewpoint_revisions/')}
    return {'affected_keys': sorted(affected), 'context_keys': sorted(set(states)-affected),
            'unknown_keys': sorted(k for k in affected if c.split_key(k)[0] not in c.WRITABLE | c.IMPACT_ONLY),
            'reverse_edges': {k: sorted(reverse[k]) for k in sorted(affected)}}


def active_links(states, vid):
    return {k:p for k,p in states.items() if k.startswith('viewpoint_claim_links/')
            and p['viewpoint_id'] == vid and p.get('effective_state', 'active') == 'active'}


def permissions(states, vid):
    head = states[c.key('canonical_viewpoints', vid)]
    rid = head['current_revision_id']
    values = [c.Permission(collection='canonical_viewpoints', object_id=vid, fields=['current_revision_id']),
              c.Permission(collection='viewpoint_revisions', object_id='CVR-scope-allocation-slot',
                  create=True, parent_id=vid, fields=sorted(c.KNOWLEDGE_COLLECTIONS['viewpoint_revisions'][0].model_fields))]
    values += [c.Permission(collection='viewpoint_claim_links', object_id=c.split_key(k)[1],
                           fields=['validated_against_viewpoint_revision_id']) for k in sorted(active_links(states, vid))]
    for k,p in sorted(states.items()):
        if not k.startswith('viewpoint_relations/') or p.get('effective_state','active') != 'active':
            continue
        fields = [field for field in ('validated_source_viewpoint_revision_id', 'validated_target_viewpoint_revision_id')
                  if p.get(field) == rid]
        if fields:
            values.append(c.Permission(collection='viewpoint_relations', object_id=c.split_key(k)[1],
                                       fields=fields+['review_provenance']))
    return values


def validate_authority(manifest, states, snapshots=None):
    value = direction(manifest, snapshots)
    policy = read_policy(value['policy'], snapshots)
    vid, rid = value['viewpoint_id'], value['before_revision_id']
    if (value.get('schema_version') != VERSION or manifest.claim_issue or manifest.deferred_repair
            or manifest.prior_updates or manifest.current_sources != [manifest.before_source]
            or manifest.authorized_paragraph_keys or value.get('authorization_id') != policy.authorization_id):
        raise ValueError('scope management cannot change source/domain or reuse another budget')
    head, revision = states[c.key('canonical_viewpoints', vid)], states[c.key('viewpoint_revisions', rid)]
    if (head['current_revision_id'] != rid or revision['viewpoint_id'] != vid
            or sha256_json(revision) != value['before_revision_sha256']
            or head.get('identity_status') not in {'active', 'provisional'}
            or head.get('review_status') in c.HUMAN_HOLDS or revision.get('review_status') in c.HUMAN_HOLDS):
        raise ValueError('scope management current identity/hold drift')
    links = active_links(states, vid)
    ids = sorted({p['claim_id'] for p in links.values()})
    if (not ids or ids != value['claim_ids'] or sorted(links) != value['link_keys']
            or manifest.permissions != permissions(states, vid)):
        raise ValueError('scope permission/full current members drift')
    for p in links.values():
        if (p.get('review_status') != 'system_approved'
                or p.get('validated_against_viewpoint_revision_id') != rid):
            raise ValueError('scope current link approval/version missing')
    auth = json.loads(c.bound_bytes(manifest.authorization, snapshots))
    if (auth.get('viewpoint_scope_repair') != manifest.viewpoint_scope_repair.model_dump()
            or auth.get('policy') != value['policy'] or auth['authorization_id'] != policy.authorization_id):
        raise ValueError('scope policy not bound to scoped authority')
    for report in value['reports']:
        c.bound_bytes(report, snapshots)
    return value


def validate_postimages(frozen, proposal, post):
    before = c.index(frozen['before_records'])
    value = frozen['viewpoint_scope_context']
    vid, rid = value['viewpoint_id'], value['before_revision_id']
    proposal = Proposal.model_validate(proposal.model_dump())
    assessment = proposal.scope_assessment
    if sorted(assessment.claim_ids) != value['claim_ids'] or len(assessment.claim_ids) != len(set(assessment.claim_ids)):
        raise ValueError('scope assessment must cover all unique current Claims')
    paragraphs = {frozen['manifest']['source_id']: frozen['after'][0]['paragraphs'],
                  **{s['source_id']:s['paragraphs'] for s in frozen.get('context_sources', [])}}
    linked_sources = {}
    for cid in value['claim_ids']:
        claim = before[c.key('claims', cid)]
        eids = claim['evidence_step_ids']
        fids = {fid for eid in eids for fid in
            (set(before[c.key('evidence_steps',eid)].get('source_fragment_ids',[])) |
             ({before[c.key('evidence_steps',eid)]['source_fragment_id']} if before[c.key('evidence_steps',eid)].get('source_fragment_id') else set()))}
        linked_sources[cid] = {before[c.key('source_fragments', fid)]['source_id'] for fid in fids}
    if {x.claim_id for x in assessment.citations} != set(value['claim_ids']):
        raise ValueError('scope assessment lacks own-source citations for every Claim')
    for cite in assessment.citations:
        if (cite.source_id not in linked_sources.get(cite.claim_id, set())
                or paragraphs.get(cite.source_id, {}).get(cite.paragraph_key, {}).get('text','').count(cite.exact_quote) != 1):
            raise ValueError('scope citation must be exact own body, not an AI title/foreign Claim')
    changed = {k for k in post if before.get(k) != post[k]}
    if assessment.action == 'needs_direction':
        raise ValueError('scope enrichment requires new semantic direction')
    if assessment.action == 'retain':
        if changed:
            raise ValueError('retained scope cannot write data')
        return
    hk, rk = c.key('canonical_viewpoints',vid), c.key('viewpoint_revisions',rid)
    new_id = post[hk]['current_revision_id']
    nk = c.key('viewpoint_revisions',new_id)
    if nk in before or nk not in post or post[rk] != before[rk]:
        raise ValueError('scope enrichment requires one new CVR, preserving the old revision')
    old, new = before[rk], post[nk]
    exceptions = {'viewpoint_revision_id','supersedes_revision_id','scope','revision','revision_number',
                  'review_status','provenance','approved_at','approved_by'}
    if (any(old.get(f) != new.get(f) for f in (set(old)|set(new))-exceptions)
            or new['viewpoint_id'] != vid or new['supersedes_revision_id'] != rid
            or new['revision'] != 1 or new['revision_number'] != 1):
        raise ValueError('scope policy cannot change core/signature/attribution/identity')
    oscope, nscope = old['scope'], new['scope']
    if (set(oscope) != set(nscope) or any(oscope[f] != nscope[f] for f in oscope if f != 'scripture_scope')
            or oscope['scripture_scope'] == nscope['scripture_scope']):
        raise ValueError('scope enrichment must preserve all other real restrictions')
    refs = nscope['scripture_scope']
    allowed_refs = set(oscope['scripture_scope']) | {ref for cid in value['claim_ids']
                    for ref in before[c.key('claims',cid)].get('scripture_refs',[])}
    # [] is an explicit general-identity proposal, not a union-of-passages.
    # The Opus review must decide whether the old list was a true limitation.
    if refs and (not set(oscope['scripture_scope']) <= set(refs) or not set(refs) <= allowed_refs):
        raise ValueError('expanded scope lacks current bound Claim references or drops an old passage')
    expected = {hk, nk} | set(active_links(before,vid))
    expected |= {k for k,p in before.items() if k.startswith('viewpoint_relations/')
                 and p.get('effective_state','active') == 'active'
                 and rid in {p.get('validated_source_viewpoint_revision_id'),p.get('validated_target_viewpoint_revision_id')}}
    if changed != expected:
        raise ValueError('scope write set must include exactly the new CVR/head/current consumers')
    for k in expected-{nk}:
        col, _ = c.split_key(k)
        allowed = {'revision','review_status'} | c.PROVENANCE
        if col == 'canonical_viewpoints':
            allowed |= {'current_revision_id'}
        elif col == 'viewpoint_claim_links':
            allowed |= {'validated_against_viewpoint_revision_id'}
            if post[k]['validated_against_viewpoint_revision_id'] != new_id:
                raise ValueError('scope link must pin the new revision')
        else:
            allowed |= {'review_provenance','validated_source_viewpoint_revision_id','validated_target_viewpoint_revision_id'}
            for field in ('validated_source_viewpoint_revision_id','validated_target_viewpoint_revision_id'):
                if post[k].get(field) != (new_id if before[k].get(field)==rid else before[k].get(field)):
                    raise ValueError('scope relation version/endpoint drift')
            if post[k].get('review_provenance') is not None:
                raise ValueError('scope proposal relation must not inherit historical approval')
        if any(before[k].get(f) != post[k].get(f) for f in (set(before[k])|set(post[k]))-allowed):
            raise ValueError('scope management cannot change Claim/link role/relation meaning')


def manifest_for_scope(store, run_id, vid, policy_binding, reports, root):
    from backend.pipeline.source_reconciliation_runner import write, binding
    policy = read_policy(policy_binding)
    saved = store.get_cvp_group(run_id)
    records = store.read_affected_context([('canonical_viewpoints',vid)])
    states = {c.key(col,oid):r['payload'] for (col,oid),r in records.items() if not r.get('retired')}
    links = active_links(states,vid)
    ids = sorted({p['claim_id'] for p in links.values()})
    if not set(ids) & set(saved['claim_ids']):
        raise ValueError('scope management target is unrelated to current group')
    first = states[c.key('claims',ids[0])]
    eid = first['evidence_step_ids'][0]
    evidence = states[c.key('evidence_steps',eid)]
    fid = evidence.get('source_fragment_id') or evidence['source_fragment_ids'][0]
    sid = states[c.key('source_fragments',fid)]['source_id']
    source = states[c.key('source_documents',sid)]
    physical = c.FileBinding(path=source['source_path'],file_sha256=source['source_file_sha256'])
    head = states[c.key('canonical_viewpoints',vid)]
    rid = head['current_revision_id']
    groups = [c.Group(group_key=g['group_key'],claim_ids=g['claim_ids'])
              for g in store.get_cvp_campaign(saved['campaign_sha256'])['groups']]
    progress = {r['group_key']:r for r in store.cvp_campaign_progress(saved['campaign_sha256'])}
    for g in groups:
        g.revisit_required = progress.get(g.group_key,{}).get('revisit_required',False)
        g.independent_audit_complete = progress.get(g.group_key,{}).get('independent_audit_complete',True)
    write(root/'membership.json',{'groups':[{'group_key':g.group_key,'claim_ids':g.claim_ids} for g in groups]})
    fields = permissions(states,vid)
    context = {'schema_version':VERSION, 'authorization_id':policy.authorization_id,
        'policy':policy_binding,'viewpoint_id':vid,'before_revision_id':rid,
        'before_revision_sha256':sha256_json(states[c.key('viewpoint_revisions',rid)]),
        'claim_ids':ids,'link_keys':sorted(links),'reports':reports}
    write(root/'scope-context.json',context)
    context_binding = binding(root/'scope-context.json')
    write(root/'scoped-authorization.json',{'source_id':sid,'authorization_id':policy.authorization_id,
        'authorization_quote':policy.authorization_quote,'human_content_approval':False,'policy':policy_binding,
        'viewpoint_scope_repair':context_binding,'permissions':[p.model_dump() for p in fields]})
    return c.Manifest(source_id=sid,before_source=physical,current_sources=[physical],
        authorization=binding(root/'scoped-authorization.json'),authorized_paragraph_keys=[],permissions=fields,
        groups=groups,campaign_claim_ids=[cid for g in groups for cid in g.claim_ids],
        accepted_membership=binding(root/'membership.json'),created_at='program-scoped-CVP-management-policy',
        viewpoint_scope_repair=context_binding)


def audit_targets(packet, report):
    """Typed scope findings only; rejected judgements need no content action."""
    actions = {a['item_id']:a for a in report['actions']}
    return sorted({i['viewpoint']['viewpoint_id'] for i in packet['items']
        if i['item_id'].startswith('4:') and actions[i['item_id']]['verdict'] in {'unresolved','audit_objection_upheld'}
        and i.get('audit_finding',{}).get('issue') == 'scope_mismatch'})


def handle_scope(store, run_id, vid, *, policy_binding, reports, root, ports):
    from backend.pipeline.source_reconciliation_runner import read,write,body
    from backend.pipeline.cvp_sql_actions import execute_source_action
    saved = store.get_cvp_group(run_id)
    previous = saved.get('reviewed',{}).get('evidence',{}).get('action',{}).get('materials',{}).get('viewpoint_scope_context')
    if previous and previous['viewpoint_id'] == vid:
        raise ValueError('new scope objection after this group scope revision; no second automatic repair cycle')
    head = store.get_record('canonical_viewpoints',vid)
    identity = sha256_json({'viewpoint_id':vid,'before_revision_id':head['current_revision_id']})
    work = Path(root)/identity
    path = work/'manifest.json'
    if path.exists():
        manifest = c.Manifest.model_validate(body(read(path)))
        if direction(manifest)['policy'] != policy_binding:
            raise ValueError('scope management policy drift, no new retry budget')
    else:
        manifest = manifest_for_scope(store,run_id,vid,policy_binding,reports,work)
        write(path,manifest.model_dump(mode='json'))
    result = execute_source_action(store,run_id,manifest.model_dump(mode='json'),root=work/'action',ports=ports)
    if result.get('status') == 'already_applied':
        raise ValueError('same scope issue remains after its committed action; no repair loop')
    return result
