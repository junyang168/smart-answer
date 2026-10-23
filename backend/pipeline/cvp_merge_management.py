"""Reviewed CVP identity merge, using the common model/SQL/audit lifecycle.

Old identities, revisions, Claims and decisions are historical facts, not a
template to mechanically move members. Every new member and affected relation
is proposed and independently reviewed with the full own-source context.
"""
from copy import deepcopy
from pathlib import Path
from typing import Literal
import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from backend.api.canonical_repository.postgres_store import canonical_json, record_content_sha, sha256_json
from backend.api.canonical_repository.knowledge_models import (
    ViewpointPropositionSignature, ViewpointScope, ViewpointComponentSpan,
    ViewpointClaimEvidenceBinding, KNOWLEDGE_COLLECTIONS,
)
from backend.pipeline import source_reconciliation as content
from backend.pipeline.source_reconciliation_runner import Runner, Stop, body, read, write
from backend.pipeline.cvp_link_replacement import saved_raw
from backend.pipeline.viewpoint_authorized_source_repair_357 import Review

SCHEMA = 'wang_cvp_merge_management_v1'
DIRECTION = 'wang_cvp_merge_direction_v1'
POLICY_PATH = Path(__file__).with_name('policies')/'wang_cvp_merge_management_v1.json'
PROMPT = '''本次是既有观点身份 merge，不是重新判读原审计，也不是自动拼接摘要。
读取全部旧 CVP/CVR、全部当前成员及支持 Claim、完整自身原文、实际证据、原身份判定和审核依据、所有受影响关系及历史结构。
负责人要求提议一个完整观点及全部成员重组，并非批准待写后像。不同讲道的 Claim、Evidence、fragment 和原文绝不修改或拼成一条新 Claim。
core_proposition 必须保留教授的完整对象、条件、否定、因果、模态、归属及真实范围；共享主题不是合并依据。
每个唯一 Claim 恰好一项，不能因两条旧连接而计为两个 Claim。完整同义才 equivalent_full；成分同义才 equivalent_component；支持必须说明实际论证作用。
spans 是当前 Claim 的精确非空半开区间；evidence_bindings 只能选择原已批准连接中该 Claim 自身仍可用的绑定，不借背景、不升 context_only/hold。
同 Claim 最多一个等价结论及一个不重叠 supports 理由；不能把正反两半机械当两个新成员。没有合法作用或 Claim 本身有问题返回 unresolved，不通过改 Claim 掩盖它。
每条旧关系恰好处置一次：reconnect 仅重接合并端点，不改 relation_type、其他端点或支持依据；相同投影端点/类型可显式合为一条；internalize 仅限两端均在本次合并集合；withdraw 必须有独立明确理由。
旧身份改为 merged 并指向程序分配的新身份；旧 CVR/结构/判定完整保留为历史，产品资格失效但不生成路线、文章或发布。
不得批准 unknown、过时、hold、缺全文、缺成员、未批准证据或未声明 consumer。不能为了推进组号批准。
每项须给自身原文引用和理由；后续独立审核完整编译后像，而不是只看新命题。'''


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Citation(Strict):
    source_id: str
    paragraph_index: int
    exact_quote: str = Field(min_length=1)


class MemberLink(Strict):
    link_type: Literal['equivalent_full', 'equivalent_component', 'supports', 'qualifies', 'extends', 'applies']
    spans: list[ViewpointComponentSpan] = Field(min_length=1)
    evidence_bindings: list[ViewpointClaimEvidenceBinding] = Field(min_length=1)
    reason: str = Field(min_length=1)


class Member(Strict):
    claim_id: str
    disposition: Literal['linked', 'unresolved']
    links: list[MemberLink]
    citations: list[Citation] = Field(min_length=1)
    reason: str = Field(min_length=1)


class Relation(Strict):
    old_relation_ids: list[str] = Field(min_length=1)
    disposition: Literal['reconnect', 'internalize', 'withdraw', 'unresolved']
    reason: str = Field(min_length=1)


class Proposal(Strict):
    outcome: Literal['merge', 'unresolved']
    core_proposition: str = Field(min_length=1)
    proposition_signature: ViewpointPropositionSignature
    scope: ViewpointScope
    members: list[Member] = Field(min_length=1)
    relations: list[Relation]
    reason: str = Field(min_length=1)


def adapter(stage):
    from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
    from backend.pipeline.viewpoint_resolution_runtime import subscription_client
    from backend.pipeline.viewpoint_semantic_contract import semantic_prompt
    if stage not in {'proposal', 'initial_review', 'correction', 'final_review'}:
        raise ValueError('merge permits only one proposal/review and one correction/final')
    proposing = stage in {'proposal', 'correction'}
    prompt = PROMPT if proposing else PROMPT + '''\n独立审核整个 compiled_postimage 与保留历史/失效范围。
按 required_verdict_ids 每个精确 key 返回一个 verdict（沿用 claim_id 字段作为审核 key），不能漏项、重复或加外来项。
merge_identity 核完整身份/命题/范围与两个旧身份替代；member:<ID> 核该 Claim 所有新连接/全文/证据；relation:<ID> 核原关系的完整处置。
draft 中 provisional provenance 只是程序显示，不是已有批准。只有全部真实成立才 approved=true/problems=[]。'''
    obj = StructuredJsonReviewerAdapter(client=subscription_client(
        'codex' if proposing else 'claude', 'gpt-6.1-sol' if proposing else 'claude-opus-5-5', 'high'),
        prompt=semantic_prompt(prompt), response_model=Proposal if proposing else Review,
        schema_name=SCHEMA+'_'+stage, compact_payload=True, shared_text_payload=True, shared_record_payload=True)
    obj.max_request_bytes = 500000  # existing execution request-limit binding supplies the authorized limit
    return obj


def production_ports():
    from backend.pipeline.source_reconciliation_runtime import ProductionPorts
    class Ports(ProductionPorts):
        def adapter(self, stage): return adapter(stage)
    return Ports()


def action_id(direction):
    # Neither finder, output root, owner wording nor policy name creates a new budget.
    return 'CVPA-'+sha256_json({'contract':SCHEMA, 'viewpoint_pins':direction['viewpoint_pins']})


def owner_direction(store,run_id,viewpoint_ids,*,authorization_quote,requested_proposition,original_artifacts=()):
    """Record an explicit owner operation, never an accepted AI afterimage."""
    group=store.get_cvp_group(run_id)
    ids=sorted(set(viewpoint_ids))
    rows=[]
    for vid in ids:
        h=store.get_record('canonical_viewpoints',vid)
        if not h:raise ValueError('merge owner identity absent')
        r=store.get_record('viewpoint_revisions',h['current_revision_id'])
        rows.append({'viewpoint_id':vid,'head_sha256':sha256_json(h),
            'revision_id':h['current_revision_id'],'revision_sha256':sha256_json(r)})
    from backend.pipeline.cvp_sql_commit import binding
    d={'schema_version':DIRECTION,'run_id':run_id,'campaign_sha256':group['campaign_sha256'],
        'viewpoint_pins':rows,'authorization_quote':authorization_quote,'requested_proposition':requested_proposition,
        'authorized_by':'owner','human_content_approval':False,'policy':binding(POLICY_PATH),
        'original_artifacts':list(original_artifacts)}
    d['artifact_sha256']=sha256_json(d)
    return d


def validate_direction(d, m):
    if (set(d) != {'schema_version', 'run_id', 'campaign_sha256', 'viewpoint_pins',
            'authorization_quote', 'requested_proposition', 'authorized_by', 'human_content_approval', 'artifact_sha256', 'policy', 'original_artifacts'}
            or d['schema_version'] != DIRECTION or d['authorized_by'] != 'owner'
            or d['human_content_approval'] is not False or not d['authorization_quote']
            or not d['requested_proposition'] or d['run_id'] != m['run_id']
            or d['campaign_sha256'] != m['campaign_sha256']
            or d['artifact_sha256'] != sha256_json({k:v for k,v in d.items() if k!='artifact_sha256'})):
        raise ValueError('merge exact owner direction/campaign/seal drift')
    from backend.pipeline.cvp_sql_commit import check_binding
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    check_binding(d['policy'])
    if read_artifact(d['policy']['path'])!=read_artifact(POLICY_PATH):
        raise ValueError('merge policy identity drift')
    for b in d['original_artifacts']:check_binding(b)
    rows=d['viewpoint_pins']
    if len(rows)<2 or [r['viewpoint_id'] for r in rows] != sorted({r['viewpoint_id'] for r in rows}):
        raise ValueError('merge needs distinct sorted current identities')
    states=content.index(m['records'])
    for row in rows:
        head=states[content.key('canonical_viewpoints',row['viewpoint_id'])]
        revision=states[content.key('viewpoint_revisions',head['current_revision_id'])]
        if (set(row)!={'viewpoint_id','head_sha256','revision_id','revision_sha256'}
                or row['head_sha256']!=sha256_json(head) or row['revision_id']!=head['current_revision_id']
                or row['revision_sha256']!=sha256_json(revision)
                or revision['viewpoint_id']!=head['viewpoint_id'] or head['identity_status']!='active'
                or head.get('redirect_to_viewpoint_id') or head.get('review_status')!='system_approved'
                or revision.get('review_status')!='system_approved'):
            raise ValueError('merge active approved current identity/hold drift')


def prepare_material(store, run_id, authorization):
    from backend.pipeline.cvp_sql_commit import check_binding, pins, pin_rows
    from backend.pipeline.viewpoint_evidence_source_context import load_context_source
    check_binding(authorization)
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    d=read_artifact(authorization['path']);group=store.get_cvp_group(run_id)
    if not group or group['campaign_sha256']!=d['campaign_sha256'] or group['checkpoint'] not in {'applied','audited'}:
        raise ValueError('merge requires actual admitted applied parent')
    e=group.get('reviewed',{}).get('evidence',{})
    if e.get('pending_cvp_claim_ids') or e.get('pending_cvp_management_record_keys'):
        raise ValueError('merge cannot discard unfinished source/Claim obligations')
    vids=[p['viewpoint_id'] for p in d['viewpoint_pins']]
    records=store.read_affected_context([('canonical_viewpoints',v) for v in vids])
    endpoint_views=set(vids)
    for (col,_),row in records.items():
        if (col=='viewpoint_relations' and not row.get('retired') and row['payload'].get('effective_state')=='active'
                and set(vids)&{row['payload']['source_viewpoint_id'],row['payload']['target_viewpoint_id']}):
            endpoint_views.update((row['payload']['source_viewpoint_id'],row['payload']['target_viewpoint_id']))
    records.update(store.read_cvp_context([],viewpoint_ids=sorted(endpoint_views)))
    content_keys=sorted(content.key(c,i) for (c,i),row in records.items()
        if not row.get('retired') and c not in {'composition_plans','composition_decisions','topic_nodes','position_nodes','product_dependencies'})
    records.update(store.read_cvp_context(group['claim_ids'],viewpoint_ids=vids+e['viewpoint_ids']))
    active={k:r['payload'] for k,r in records.items() if not r.get('retired')}
    for k,r in records.items():
        if record_content_sha(r['payload'])!=r['content_sha256']:
            raise ValueError('merge current SQL payload/SHA mismatch')
    links=sorted([p for (col,_),p in active.items() if col=='viewpoint_claim_links'
        and p['viewpoint_id'] in vids and p.get('effective_state')=='active'], key=lambda p:p['viewpoint_claim_link_id'])
    claims=sorted({p['claim_id'] for p in links})
    if not set(claims)&set(group['claim_ids']):raise ValueError('merge is unrelated to parent group')
    from backend.pipeline.cvp_sql_claim_authority import current_claim_authority
    claim_authority=[{'claim_id':cid,'pin':[records['claims',cid]['revision'],records['claims',cid]['content_sha256'],False],
        'authority':current_claim_authority(store,cid,records['claims',cid])} for cid in claims]
    for link in links:
        claim=active['claims',link['claim_id']]
        if (link['pinned_claim_revision']!=claim['revision'] or link['validated_against_viewpoint_revision_id']!=
                active['canonical_viewpoints',link['viewpoint_id']]['current_revision_id']
                or link.get('review_status')!='system_approved' or claim.get('review_status') in content.HUMAN_HOLDS
                or link.get('supporting_relation_ids')):
            raise ValueError('merge member generation/approval/hold/relation-bound role drift')
    relations=sorted([p for (col,_),p in active.items() if col=='viewpoint_relations'
        and set(vids)&{p['source_viewpoint_id'],p['target_viewpoint_id']} and p.get('effective_state')=='active'],
        key=lambda p:p['viewpoint_relation_id'])
    for rel in relations:
        if rel.get('review_status')!='system_approved' or rel.get('temporal_assertion'):
            raise ValueError('merge cannot reinterpret human/temporal/held relations')
    referenced_ids=vids+[p['revision_id'] for p in d['viewpoint_pins']]+[p['viewpoint_claim_link_id'] for p in links]
    with store.connect() as conn,conn.cursor() as cur:
        all_collections=sorted(KNOWLEDGE_COLLECTIONS)
        consumers=store._reference_consumers(cur,referenced_ids,all_collections)
        cur.execute("SELECT object_id,payload FROM wang_knowledge.objects WHERE collection='viewpoint_claim_links' AND retired_at IS NULL AND payload->>'viewpoint_id'=ANY(%s) AND payload->>'effective_state'='active' ORDER BY object_id",(vids,))
        if [p for _,p in cur.fetchall()]!=links:raise ValueError('merge omitted active members')
    allowed={'canonical_viewpoints','viewpoint_revisions','viewpoint_claim_links','viewpoint_relations',
        'viewpoint_identity_candidates','viewpoint_identity_decisions','viewpoint_structure_revisions',
        'composition_plans','composition_decisions','topic_nodes','position_nodes','product_dependencies'}
    if any(col not in allowed or (col,oid) not in records for col,oid,p in consumers):
        raise ValueError('merge has unhandled route/business consumer; no partial write')
    if any(col=='canonical_viewpoints' and oid not in vids and p.get('redirect_to_viewpoint_id') in vids
           for col,oid,p in consumers):
        raise ValueError('merge would create an unhandled existing redirect chain')
    sources=[]
    for (col,_),doc in sorted(active.items()):
        if col=='source_documents':
            path,physical,projection=load_context_source(doc)
            sources.append({'source_id':doc['source_id'],'path':str(path),'file_sha256':physical,
                'body_sha256':projection.body_sha256,'paragraphs':deepcopy(projection.spoken_rows)})
    # Original approval is disclosed as history, not approval of this merge.
    # Direct committed decision metadata and explicitly named leaf artifacts
    # only; no recursive history or filesystem discovery in runtime.
    from backend.pipeline.cvp_retained_review_separation import generation_artifacts,prior_review_context
    paths={b['path'] for b in d['original_artifacts']}
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    with store.connect() as conn,conn.cursor() as cur:
        for did in sorted({l['decision_id'] for l in links}):
            dec=records['viewpoint_identity_decisions',did]
            rows=cur.execute("SELECT c.metadata FROM wang_knowledge.object_versions v JOIN wang_knowledge.change_sets c USING(change_set_id) WHERE v.collection='viewpoint_identity_decisions' AND v.object_id=%s AND v.revision=%s AND c.status='applied'",(did,dec['revision'])).fetchall()
            if len(rows)!=1:raise ValueError('merge original decision commit is missing/ambiguous')
            for b in rows[0][0].get('approval',{}).values():
                if isinstance(b,dict) and {'path','file_sha256'}<=set(b):
                    check_binding(b);raw=read_artifact(b['path'])
                    if 'model_id' in raw and 'response' in raw:paths.add(b['path'])
    leaves=generation_artifacts(sorted(paths))
    original_context=[]
    for link in links:
        dec=records['viewpoint_identity_decisions',link['decision_id']]['payload']
        original_context.append({'link_id':link['viewpoint_claim_link_id'],'decision':dec,
            'original_review_context':prior_review_context(dec,{'generation_artifacts':leaves},link['claim_id'])})
    m={'schema_version':SCHEMA,'run_id':run_id,'campaign_sha256':group['campaign_sha256'],
        'authorization':authorization,'viewpoint_ids':vids,'before_links':links,'claim_ids':claims,
        'claim_authority':claim_authority,
        'relations':relations,'sources':sources,'original_claim_ids':group['claim_ids'],
        'prior_viewpoint_ids':e['viewpoint_ids'],'prior_disposition':e.get('disposition'),
        'claim_management_resolution':e.get('action',{}).get('materials',{}).get('claim_management_resolution'),
        'records':[{'collection':col,'object_id':oid,'payload':p} for (col,oid),p in sorted(active.items())],
        'semantic_record_keys':content_keys,
        'provenance_templates':[{'collection':col,'object_id':oid,'payload':r['payload']}
            for (col,oid),r in sorted(records.items()) if r.get('retired')
                and col in {'viewpoint_identity_candidates','viewpoint_identity_decisions'}],
        'read_pins':pin_rows(pins(records)),
        'sql_scope':{'consumer_roots':[['canonical_viewpoints',v] for v in vids]},
        'consumer_keys':sorted(content.key(col,oid) for col,oid,p in consumers),
        'consumer_accounting':'Original approved decisions/revisions/structures stay historical; products invalidated by common SQL, no route/article work.',
        'program_provenance_contract':'IDs/SHA/review fields computed after actual independent approval; no model SHA backfill.',
        'owner_direction':d['requested_proposition']}
    m['original_generation_context']=original_context
    validate_direction(d,m)
    return json.loads(canonical_json(m))


def required_verdict_ids(m):
    return ['merge_identity']+['member:'+i for i in m['claim_ids']]+['relation:'+p['viewpoint_relation_id'] for p in m['relations']]


def proposal_packet(m):
    """Complete content review projection, separate from the SQL binding plane.

    Every scoped payload/source/original judgment is identical. The program
    verifies the complete readset/approval/consumer ledger itself; a model
    cannot approve or modify those ledger pins by repeating their hashes.
    The immutable materials, packet and real wire each have independent SHAs.
    """
    fields=('schema_version','viewpoint_ids','before_links','claim_ids','relations','sources','records',
            'original_generation_context','owner_direction','program_provenance_contract')
    packet={k:deepcopy(m[k]) for k in fields}
    selected=set(m.get('semantic_record_keys',[content.key(r['collection'],r['object_id']) for r in m['records']]))
    states=content.index(m['records']);required=set()
    for link in m['before_links']:
        required.update((content.key('viewpoint_claim_links',link['viewpoint_claim_link_id']),
            content.key('claims',link['claim_id']),content.key('canonical_viewpoints',link['viewpoint_id']),
            content.key('viewpoint_revisions',states[content.key('canonical_viewpoints',link['viewpoint_id'])]['current_revision_id'])))
        for b in link['evidence_bindings']:
            required.update((content.key('evidence_steps',b['evidence_step_id']),content.key('source_fragments',b['source_fragment_id'])))
    for rel in m['relations']:
        required.add(content.key('viewpoint_relations',rel['viewpoint_relation_id']))
        for vid in (rel['source_viewpoint_id'],rel['target_viewpoint_id']):
            required.update((content.key('canonical_viewpoints',vid),
                content.key('viewpoint_revisions',states[content.key('canonical_viewpoints',vid)]['current_revision_id'])))
            for row in m['records']:
                p=row['payload']
                if row['collection']=='viewpoint_claim_links' and p['viewpoint_id']==vid and p.get('effective_state')=='active':
                    required.update((content.key(row['collection'],row['object_id']),content.key('claims',p['claim_id'])))
    if not required<=selected or not selected<=set(states):
        raise ValueError('merge content projection omits semantic write/member/endpoint denominator: '+str(sorted(required-selected))+
            '; foreign to active graph: '+str(sorted(selected-set(states))))
    packet['records']=[deepcopy(r) for r in m['records'] if content.key(r['collection'],r['object_id']) in selected]
    retained=[r for r in m['records'] if content.key(r['collection'],r['object_id']) not in selected]
    packet['program_binding_receipt']={'schema_version':'wang_cvp_merge_content_projection_v1',
        'materials_sha256':sha256_json(m),'complete_readset_sha256':sha256_json(m['read_pins']),
        'complete_readset_count':len(m['read_pins']),'consumer_keys_sha256':sha256_json(m['consumer_keys']),
        'qualification_authority_sha256':sha256_json(m['claim_authority']),
        'retained_accounting_records_sha256':sha256_json(retained),
        'retained_accounting_record_keys':[content.key(r['collection'],r['object_id']) for r in retained],
        'binding_write_authority':False,
        'boundary':'SQL pins/consumer closure/prior group accounting and product/navigation consumers are program-verified, not model approvals. Complete semantic reverse-impact graph, all current members, original judgments and ALL full own bodies are unchanged. Unrelated parent-group CVPs and product/navigation payloads remain unchanged in the sealed transaction plane; this operation grants them no content write.'}
    packet['claim_qualification_receipt']=[{'claim_id':x['claim_id'],'claim_revision':x['pin'][0],
        'approved_generation_verified':True,'authority_sha256':sha256_json(x['authority'])} for x in m['claim_authority']]
    return packet


def own_source_ids(states,claim):
    """Actual bound graph supports legacy occurrences without a source_id."""
    ids=set()
    for eid in claim['evidence_step_ids']:
        e=states[content.key('evidence_steps',eid)]
        fids=set(e.get('source_fragment_ids',[]))
        if e.get('source_fragment_id'):fids.add(e['source_fragment_id'])
        ids.update(states[content.key('source_fragments',fid)]['source_id'] for fid in fids)
    if not ids:raise ValueError('merge Claim lacks bound own-source graph')
    return ids


def approved(m, raw):
    review=Review.model_validate(raw['response'])
    if sorted(v.claim_id for v in review.verdicts)!=sorted(required_verdict_ids(m)):
        raise ValueError('merge independent review denominator missing/duplicate/foreign')
    if any(not v.approved or v.problems for v in review.verdicts):
        raise Stop('merge complete independent review rejected; original verdict preserved')


def postimage(m, response):
    """Pure exact write compiler; semantic correctness is the independent review."""
    p=Proposal.model_validate(response)
    if p.outcome!='merge':raise Stop('merge identity unresolved; no write')
    if sorted(x.claim_id for x in p.members)!=m['claim_ids']:
        raise ValueError('merge must account for every unique original Claim exactly once')
    states=content.index(m['records']);vids=set(m['viewpoint_ids'])
    seed=sha256_json({'contract':SCHEMA,'heads':[(v,states[content.key('canonical_viewpoints',v)]) for v in sorted(vids)]})
    vid='CV-'+seed[:20];rid='CVR-'+sha256_json({'merge':vid,'proposal':response})[:20]
    if content.key('canonical_viewpoints',vid) in states or content.key('viewpoint_revisions',rid) in states:
        raise ValueError('merge allocated identity already exists')
    newlinks=[]
    for member in p.members:
        claim=states[content.key('claims',member.claim_id)]
        if member.disposition!='linked' or not member.links:raise Stop('merge Claim role unresolved: '+member.claim_id)
        own=own_source_ids(states,claim)
        for cite in member.citations:
            src=[s for s in m['sources'] if s['source_id']==cite.source_id]
            rows=[r for s in src for r in s['paragraphs'] if r['index']==cite.paragraph_index]
            if cite.source_id not in own or len(rows)!=1 or cite.exact_quote not in rows[0]['text']:
                raise ValueError('merge citation must come from complete own source')
        choices=member.links
        if len(choices)>1 and sorted(x.link_type for x in choices)!=['equivalent_component','supports']:
            raise ValueError('merge cannot duplicate Claim role/member')
        previous=[l for l in m['before_links'] if l['claim_id']==member.claim_id]
        permitted={(b['evidence_step_id'],b['source_fragment_id']) for l in previous for b in l['evidence_bindings']}
        occupied=[]
        for n,choice in enumerate(choices):
            spans=[s.model_dump() for s in choice.spans];text=claim['statement']
            if spans!=sorted(spans,key=lambda s:s['start_char']):raise ValueError('merge span order')
            for s in spans:
                a,b=s['start_char'],s['end_char']
                if not 0<=a<b<=len(text) or text[a:b]!=s['exact_text'] or any(a<y and b>x for x,y in occupied):
                    raise ValueError('merge spans must be exact, nonoverlapping current Claim components')
                occupied.append((a,b))
            if choice.link_type=='equivalent_full' and spans!=[{'start_char':0,'end_char':len(text),'exact_text':text}]:
                raise ValueError('merge equivalent_full must cover whole Claim')
            bindings=[b.model_dump() for b in choice.evidence_bindings]
            pairs=[(b['evidence_step_id'],b['source_fragment_id']) for b in bindings]
            if pairs!=sorted(set(pairs)) or not set(pairs)<=permitted:raise ValueError('merge foreign/unbound/duplicate Evidence')
            for eid,fid in pairs:
                e=states[content.key('evidence_steps',eid)];frag=states[content.key('source_fragments',fid)]
                # Normal CVP production also consumes eligible_candidate only
                # through the actual independently reviewed Claim generation.
                # It is not context_only and this merge does not change its status.
                attested=any(x['claim_id']==member.claim_id and x['pin']==[
                    claim['revision'],record_content_sha(claim),False] and x.get('authority')
                    for x in m.get('claim_authority',[]))
                if (eid not in claim['evidence_step_ids'] or frag['source_id'] not in own
                        or e.get('support_eligibility') not in ({'eligible','eligible_with_label'}|({'eligible_candidate'} if attested else set()))
                        or e.get('valid_for_identity_review') is False or e.get('review_status') in content.HUMAN_HOLDS):
                    raise ValueError('merge cannot promote context-only/held/foreign Evidence')
            old=previous[0];link=deepcopy(old)
            link.update(viewpoint_claim_link_id='VCL-'+sha256_json({'merge':vid,'claim':member.claim_id,'role':n})[:20],
                revision=1,viewpoint_id=vid,validated_against_viewpoint_revision_id=rid,
                pinned_claim_revision=claim['revision'],link_type=choice.link_type,
                component_locator=None if choice.link_type=='equivalent_full' else {
                    'claim_sha256':content.semantic_record_sha(claim),'canonical_spans':spans,
                    'statement_component':''.join(s['exact_text'] for s in spans)},evidence_bindings=bindings,
                occurrence_refs=sorted({i for l in previous for i in l['occurrence_refs']}),supporting_relation_ids=[])
            newlinks.append(link)
    if not any(l['link_type'].startswith('equivalent_') for l in newlinks):raise Stop('merged CVP lacks equivalent members')
    rels={r['viewpoint_relation_id']:r for r in m['relations']};seen=[];newrels=[];projected=set()
    for choice in p.relations:
        if choice.old_relation_ids!=sorted(set(choice.old_relation_ids)) or not set(choice.old_relation_ids)<=set(rels):
            raise ValueError('merge foreign/duplicate relation group')
        seen+=choice.old_relation_ids
        old=[rels[i] for i in choice.old_relation_ids]
        if choice.disposition=='unresolved':raise Stop('merge relation unresolved')
        if choice.disposition=='internalize':
            if any({r['source_viewpoint_id'],r['target_viewpoint_id']}-vids for r in old):
                raise ValueError('merge cannot internalize external relation')
            continue
        if choice.disposition=='withdraw':continue
        targets={(vid if r['source_viewpoint_id'] in vids else r['source_viewpoint_id'],
            vid if r['target_viewpoint_id'] in vids else r['target_viewpoint_id'],r['relation_type']) for r in old}
        if len(targets)!=1:raise ValueError('merge cannot fuse different relation semantics')
        src,dst,typ=next(iter(targets))
        if src==dst or (src,dst,typ) in projected:raise ValueError('merge self/duplicate relation must be explicitly accounted')
        projected.add((src,dst,typ));r=deepcopy(old[0])
        r.update(viewpoint_relation_id='VREL-'+sha256_json({'merge':vid,'old':choice.old_relation_ids})[:20],revision=1,
            source_viewpoint_id=src,target_viewpoint_id=dst,reason=choice.reason,
            validated_source_viewpoint_revision_id=rid if src==vid else states[content.key('canonical_viewpoints',src)]['current_revision_id'],
            validated_target_viewpoint_revision_id=rid if dst==vid else states[content.key('canonical_viewpoints',dst)]['current_revision_id'],
            supporting_claim_ids=sorted({i for r in old for i in r.get('supporting_claim_ids',[])}),
            supporting_claim_relation_ids=sorted({i for r in old for i in r.get('supporting_claim_relation_ids',[])}),review_provenance=None)
        newrels.append(r)
    if sorted(seen)!=sorted(rels):raise ValueError('merge relation disposition denominator mismatch')
    heads=[]
    for old_id in sorted(vids):
        head=deepcopy(states[content.key('canonical_viewpoints',old_id)])
        head.update(revision=head['revision']+1,identity_status='merged',redirect_to_viewpoint_id=vid)
        heads.append(head)
    original_head=states[content.key('canonical_viewpoints',m['viewpoint_ids'][0])]
    old_r=states.get(content.key('viewpoint_revisions',original_head['current_revision_id']), {})
    new_head=deepcopy(original_head)
    new_head.update(viewpoint_id=vid,revision=1,current_revision_id=rid,identity_status='active',redirect_to_viewpoint_id=None,
        created_from_candidate_id='PROGRAM_PROVENANCE_PENDING')
    # Exact semantic identity is visible before review; approval IDs/time/SHA
    # are explicitly provisional, computed only from the actual final raw.
    new_revision=deepcopy(old_r)
    new_revision.update(viewpoint_revision_id=rid,viewpoint_id=vid,revision=1,revision_number=1,
        core_proposition=p.core_proposition,proposition_signature=p.proposition_signature.model_dump(mode='json'),
        scope=p.scope.model_dump(mode='json'),supersedes_revision_id=None,editorial_aliases=[],
        approved_at=None,approved_by='PROGRAM_PROVENANCE_PENDING',provenance=None)
    return {'viewpoint_id':vid,'revision_id':rid,'new_head':new_head,'new_revision':new_revision,
        'old_heads':heads,'links':newlinks,'relations':newrels,
        'retiring_keys':sorted([('viewpoint_claim_links',l['viewpoint_claim_link_id']) for l in m['before_links']]+
            [('viewpoint_relations',i) for i in rels]),'proposal':p.model_dump(mode='json')}


def review_packet(m, p):
    return {'schema_version':SCHEMA,'materials':proposal_packet(m),'proposal':p['response'],
        'compiled_postimage':postimage(m,p['response']),'required_verdict_ids':required_verdict_ids(m),
        'provenance_receipt':'Draft provenance not approval; final program binds actual reviewed effective proposal and terminal raw.'}


def initial_review_packet(m, p):
    """An invalid locator draft may be reviewed, never compiled or approved.

    Preserve the exact proposal. Only the initial, unused review can diagnose
    component length errors; the original single correction must supply the
    complete valid postimage before the strict final review and SQL compiler.
    Other schema, evidence, hold and source failures still fail closed.
    """
    try:
        return review_packet(m,p)
    except ValidationError as exc:
        errors=exc.errors(include_url=False,include_context=False)
        for error in errors:
            loc=error['loc']
            # Pydantic reports the model-level validator at the complete span.
            if not (len(loc)==6 and loc[0]=='members' and loc[2]=='links' and loc[4]=='spans'
                    and all(isinstance(loc[i],int) for i in (1,3,5))):
                raise
            if (error['type']!='value_error' or
                    error['msg']!='Value error, component span exact_text length does not match its range'):
                raise
        return {'schema_version':SCHEMA,'materials':proposal_packet(m),'proposal':p['response'],
            'compiled_postimage':None,'required_verdict_ids':required_verdict_ids(m),
            'initial_draft_validation':{'schema_version':'wang_cvp_merge_locator_draft_v1',
                'proposal_artifact_sha256':p['artifact_sha256'],'errors':errors,
                'content_write_authority':False,'mandatory_original_single_correction':True,
                'instruction':'Review the complete unchanged draft and own sources. These locators are invalid, '
                    'not program-normalized. No approval here authorizes a write. The original single GPT '
                    'correction must provide all complete valid postimages for the strict Opus final review.'}}


def needs_correction(m,p,r):
    try:approved(m,r)
    except Stop:return True
    return 'initial_draft_validation' in initial_review_packet(m,p)


def correction_packet(m,p,r):
    packet={'schema_version':SCHEMA,'materials':proposal_packet(m),'proposal':p['response'],'initial_review':r['response'],
        'prior_artifacts':{'proposal':p['artifact_sha256'],'review':r['artifact_sha256']},
        'correction_boundary':'One correction only; no Claim/source edit or expanded identity set.'}
    draft=initial_review_packet(m,p)
    if 'initial_draft_validation' in draft:packet['initial_draft_validation']=draft['initial_draft_validation']
    return packet


def effective_saved(root,m):
    p=saved_raw(root,'proposal',proposal_packet(m),adapter_factory=adapter)
    r=saved_raw(root,'initial_review',initial_review_packet(m,p),adapter_factory=adapter)
    if needs_correction(m,p,r):
        p=saved_raw(root,'correction',correction_packet(m,p,r),adapter_factory=adapter)
        r=saved_raw(root,'final_review',review_packet(m,p),adapter_factory=adapter);approved(m,r)
    else:
        if any((root/(s+suffix)).exists() for s in ('correction','final_review')
               for suffix in ('.raw.json','.reservation.json','.call-intent.json')):
            raise ValueError('approved merge cannot acquire another review budget')
    return p,r


def compile_package(m,p,r,root):
    from backend.pipeline.cvp_link_retarget import fresh_identity_records
    approved(m,r);draft=postimage(m,p['response']);states=content.index(m['records']+m.get('provenance_templates',[]))
    candidates=[];decisions=[];links=deepcopy(draft['links']);seed=sha256_json({'material':sha256_json(m),
        'proposal':p['artifact_sha256'],'review':r['artifact_sha256']})
    for link in links:
        old=next(l for l in m['before_links'] if l['claim_id']==link['claim_id'])
        member=next(x for x in p['response']['members'] if x['claim_id']==link['claim_id'])
        # Reuse the existing identity provenance compiler, not prose-to-content parsing.
        choices=member['links']
        choice=next(c for n,c in enumerate(choices) if link['viewpoint_claim_link_id']=='VCL-'+
            sha256_json({'merge':draft['viewpoint_id'],'claim':member['claim_id'],'role':n})[:20])
        wrapped={**p,'response':{'selection':{'reason':choice['reason']}}}
        c,d=fresh_identity_records(states,old,link,p['response']['proposition_signature'],wrapped,r,
            seed=sha256_json({'merge':seed,'link':link['viewpoint_claim_link_id']}),policy=SCHEMA)
        d['decision']='merge_identities';link['decision_id']=d['identity_decision_id'];candidates.append(c);decisions.append(d)
    template=deepcopy(draft['new_head'])
    origin=next(c['identity_candidate_id'] for c,l in zip(candidates,links) if l['link_type'].startswith('equivalent_'))
    template.update(viewpoint_id=draft['viewpoint_id'],revision=1,current_revision_id=draft['revision_id'],
        identity_status='active',redirect_to_viewpoint_id=None,created_from_candidate_id=origin)
    revision=deepcopy(draft['new_revision'])
    revision.update(viewpoint_revision_id=draft['revision_id'],viewpoint_id=draft['viewpoint_id'],revision=1,
        revision_number=1,core_proposition=p['response']['core_proposition'],proposition_signature=p['response']['proposition_signature'],
        scope=p['response']['scope'],supersedes_revision_id=None,editorial_aliases=[],approved_at=r['completed_at'],approved_by=SCHEMA,
        provenance={'basis_identity_decision_ids':sorted(d['identity_decision_id'] for d in decisions),'review_artifact_sha256':r['artifact_sha256']})
    for rel in draft['relations']:
        rel['review_provenance']={'basis_identity_decision_ids':sorted(d['identity_decision_id'] for d in decisions),
            'review_artifact_sha256':r['artifact_sha256']}
    return {'package_id':'CVP-MERGE-'+seed[:24],'canonical_viewpoints':[template,*draft['old_heads']],
        'viewpoint_revisions':[revision],'viewpoint_claim_links':links,'viewpoint_relations':draft['relations'],
        'viewpoint_identity_candidates':candidates,'viewpoint_identity_decisions':decisions,
        'source_repair_application':{'schema_version':SCHEMA,'run_root':str(Path(root).resolve()),
            'materials_sha256':sha256_json(m),'retiring_keys':[list(k) for k in draft['retiring_keys']]}}


def validate_package(package):
    from backend.pipeline.cvp_sql_commit import check_binding
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    app=package['source_repair_application'];root=Path(app['run_root']);m=body(read(root/'materials.json'))
    if app['schema_version']!=SCHEMA or not root.is_absolute() or app['materials_sha256']!=sha256_json(m):
        raise ValueError('merge package material/root authority drift')
    check_binding(m['authorization']);validate_direction(read_artifact(m['authorization']['path']),m)
    for s in m['sources']:check_binding({'path':s['path'],'file_sha256':s['file_sha256']})
    from backend.pipeline.cvp_retained_review_separation import generation_artifacts,prior_review_context
    for item in m.get('original_generation_context',[]):
        leaves=generation_artifacts([x['raw_binding']['path'] for x in item['original_review_context']])
        if prior_review_context(item['decision'],{'generation_artifacts':leaves},
                next(l['claim_id'] for l in m['before_links'] if l['viewpoint_claim_link_id']==item['link_id']))!=item['original_review_context']:
            raise ValueError('merge original review context drift')
    p,r=effective_saved(root,m)
    if package!=compile_package(m,p,r,root):raise ValueError('merge package differs from exact reviewed full postimage')
    return tuple(tuple(k) for k in app['retiring_keys'])


def execute(store,run_id,*,authorization,root,ports):
    from backend.pipeline.cvp_sql_commit import binding, check_binding, decoded_pins, commit_package, verified_group_backup
    from backend.pipeline.cvp_sql_actions import initialize_subscription_runtime,resume_bound_action
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    check_binding(authorization);d=read_artifact(authorization['path']);aid=action_id(d);root=Path(root)/aid
    store.reserve_cvp_membership_identity(identity='merge-'+aid,parent_run_id=run_id,root=root,handoff_id=d['artifact_sha256'])
    store.bind_cvp_execution_root(run_id,identity=aid,root=root)
    prior=store.get_cvp_action(run_id,aid)
    if prior and prior['authorization']!=authorization:raise ValueError('merge direction cannot reset stable once identity')
    resumed=resume_bound_action(store,run_id,aid)
    if resumed:return resumed
    m=prior['materials'] if prior else prepare_material(store,run_id,authorization);validate_direction(d,m)
    action=store.prepare_cvp_action(run_id,action_id=aid,kind='cvp_identity_merge',authorization=authorization,materials=m)
    write(root/'materials.json',m);expected=decoded_pins(m['read_pins'])
    def guard():
        for s in m['sources']:check_binding({'path':s['path'],'file_sha256':s['file_sha256']})
        with store.connect() as conn,conn.cursor() as cur:store._assert_transaction_read_pins(cur,expected)
    guard();initialize_subscription_runtime(ports);runner=Runner(root,ports);runner.root=root
    p=runner.call('proposal',proposal_packet(m));guard();r=runner.call('initial_review',initial_review_packet(m,p));guard();ps,rs='proposal','initial_review'
    if needs_correction(m,p,r):
        p=runner.call('correction',correction_packet(m,p,r));guard()
        r=runner.call('final_review',review_packet(m,p));approved(m,r);guard();ps,rs='correction','final_review'
    package=compile_package(m,p,r,root);validate_package(package)
    views=sorted((set(m['prior_viewpoint_ids'])-set(m['viewpoint_ids']))|{postimage(m,p['response'])['viewpoint_id']})
    # Source/Claim management completion is preserved, not re-opened or inferred from merge.
    if m.get('claim_management_resolution'):
        action=deepcopy(action);action['materials']['claim_management_resolution']=m['claim_management_resolution']
    return commit_package(store,run_id,package=package,
        approval={'gpt':binding(root/(ps+'.raw.json')),'opus':binding(root/(rs+'.raw.json'))},authorization=authorization,
        backup=verified_group_backup(root,package,ports),sources=[{'path':s['path'],'file_sha256':s['file_sha256']} for s in m['sources']],
        read_pins=expected,reason='independently reviewed complete CVP merge and member reorganization',
        actor='owner-authorized-pipeline',source_kind=SCHEMA,viewpoint_ids=views,disposition=m['prior_disposition'],action=action)


def current_viewpoint_ids(cursor, ids):
    """Exact single committed merge transition, shared by audit and completion.

    Old group declarations are not edited. No recursive redirect search or
    inferred approval; a subsequent unhandled merge chain fails closed.
    """
    result=set()
    for vid in sorted(set(ids)):
        row=cursor.execute("SELECT payload,revision,content_sha256,retired_at FROM wang_knowledge.objects WHERE collection='canonical_viewpoints' AND object_id=%s",(vid,)).fetchone()
        if not row or row[3] is not None:raise ValueError('declared CVP missing/retired')
        head,rev,sha,_=row
        if head.get('identity_status')!='merged':result.add(vid);continue
        target=head.get('redirect_to_viewpoint_id')
        history=cursor.execute("SELECT v.payload,v.content_sha256,c.source_kind,c.status,c.change_set_id FROM wang_knowledge.object_versions v JOIN wang_knowledge.change_sets c USING(change_set_id) WHERE v.collection='canonical_viewpoints' AND v.object_id=%s AND v.revision=%s",(vid,rev)).fetchone()
        other=cursor.execute("SELECT payload,retired_at,content_sha256 FROM wang_knowledge.objects WHERE collection='canonical_viewpoints' AND object_id=%s",(target,)).fetchone()
        creation=cursor.execute("SELECT payload FROM wang_knowledge.object_versions WHERE collection='canonical_viewpoints' AND object_id=%s AND revision=1 AND change_set_id=%s",(target,history[4] if history else None)).fetchone()
        if (not history or history[:4]!=(head,sha,SCHEMA,'applied') or record_content_sha(head)!=sha
                or not creation or creation[0].get('identity_status')!='active' or creation[0].get('viewpoint_id')!=target
                or not other or other[1] is not None or record_content_sha(other[0])!=other[2]
                or other[0].get('identity_status')!='active' or other[0].get('review_status')!='system_approved'):
            raise ValueError('declared CVP merge lacks exact committed current identity proof')
        result.add(target)
    return sorted(result)


def current_dependency_pins(cursor,pins):
    """One explicit approved merge after an unchanged source/Claim approval.

    Do not rewrite that approval. Only immediate old head updates and exact
    payload-preserving link/relation retirements may transition. The complete
    actual merge plan and cached independent review are authenticated once.
    All source/Claim/Evidence/unrelated pins still require exact equality.
    """
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    updated=dict(pins);transitions={};commits={}
    for key,pin in pins.items():
        row=cursor.execute('SELECT revision,content_sha256,retired_at IS NOT NULL,payload FROM wang_knowledge.objects WHERE collection=%s AND object_id=%s',key).fetchone()
        if row and tuple(row[:3])==tuple(pin):continue
        if not row or pin[2] or key[0] not in {'canonical_viewpoints','viewpoint_claim_links','viewpoint_relations'}:
            raise ValueError('merge dependency transition cannot alter source/Claim/unrelated generation')
        found=cursor.execute('''SELECT c.change_set_id,c.source_kind,c.status,c.metadata,o.operation,o.before_revision,o.before_sha256,
            o.after_revision,o.after_sha256,v.payload FROM wang_knowledge.object_versions v
            JOIN wang_knowledge.change_sets c USING(change_set_id) JOIN wang_knowledge.change_operations o
            ON o.change_set_id=v.change_set_id AND o.collection=v.collection AND o.object_id=v.object_id
              AND o.after_revision=v.revision WHERE v.collection=%s AND v.object_id=%s AND v.revision=%s''',(*key,row[0])).fetchall()
        if len(found)!=1:raise ValueError('merge immediate transition history missing/ambiguous')
        ident,kind,status,meta,operation,prev_rev,prev_sha,rev,sha,payload=found[0]
        # Existing logical retirement preserves the object's original payload
        # while history carries the next storage revision. No semantic field
        # is changed; verify both actual representations, not a fabricated one.
        expected_history={**row[3],'revision':row[0]} if operation=='retire' else row[3]
        old=cursor.execute('SELECT payload,content_sha256 FROM wang_knowledge.object_versions WHERE collection=%s AND object_id=%s AND revision=%s',(*key,pin[0])).fetchone()
        if (kind!=SCHEMA or status!='applied' or (prev_rev,prev_sha)!=tuple(pin[:2])
                or (rev,sha,payload)!=(row[0],row[1],expected_history) or record_content_sha(payload)!=sha
                or not old or old[1]!=pin[1] or record_content_sha(old[0])!=pin[1]):
            raise ValueError('merge dependency transition lacks exact immediate committed before/current proof: '+str((key,
                kind==SCHEMA,status=='applied',(prev_rev,prev_sha)==tuple(pin[:2]),
                (rev,sha,payload)==(row[0],row[1],expected_history),record_content_sha(payload)==sha,
                bool(old),bool(old and old[1]==pin[1]),bool(old and record_content_sha(old[0])==pin[1]))))
        if key[0]=='canonical_viewpoints':
            if (operation!='update' or row[2] or payload.get('identity_status')!='merged'
                    or {k:v for k,v in payload.items() if k not in {'revision','identity_status','redirect_to_viewpoint_id'}}
                    !={k:v for k,v in old[0].items() if k not in {'revision','identity_status','redirect_to_viewpoint_id'}}):
                raise ValueError('merge dependency head changed unrelated semantics')
        elif (operation!='retire' or not row[2] or {k:v for k,v in payload.items() if k!='revision'}
                !={k:v for k,v in old[0].items() if k!='revision'} or row[3]!=old[0]):
            raise ValueError('merge dependency retirement changed old role/payload')
        commits[ident]=meta;updated[key]=tuple(row[:3]);transitions[key]=ident
    if not commits:return updated,[]
    if len(commits)!=1:raise ValueError('merge dependency transition cannot recurse across commits')
    ident,meta=next(iter(commits.items()))
    parent=cursor.execute("SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s AND stage='cvp_group'",(meta.get('run_id'),)).fetchone()
    if not parent:raise ValueError('merge dependency actual parent absent')
    from backend.pipeline.cvp_source_impact_resolution import applied_review
    review=applied_review(None,{**parent[0],'run_id':meta['run_id']},meta['reviewed_sha256'],cursor=cursor)
    package=review['evidence']['package']['payload'];validate_package(package)
    plan=review['plan']
    if plan['change_set_id']!=ident or plan['source_kind']!=SCHEMA:raise ValueError('merge dependency foreign plan')
    fields=('operation','collection','object_id','before_revision','after_revision','before_sha256','after_sha256')
    actual=cursor.execute('SELECT '+','.join(fields)+' FROM wang_knowledge.change_operations WHERE change_set_id=%s ORDER BY operation_index',(ident,)).fetchall()
    if actual!=[tuple(o[k] for k in fields) for o in plan['operations']]:raise ValueError('merge dependency complete actual write set differs')
    all_after={(o['collection'],o['object_id']):(o['after_revision'],o['after_sha256'],o['operation']=='retire') for o in plan['operations']}
    PostgresKnowledgeStore._assert_transaction_read_pins(cursor,all_after)
    return updated,[{'change_set_id':ident,'reviewed_sha256':meta['reviewed_sha256'],
        'changed_keys':['/'.join(k) for k in sorted(transitions)],'old_source_verdicts_changed':False,
        'current_cvp_audit_still_required':True}]
