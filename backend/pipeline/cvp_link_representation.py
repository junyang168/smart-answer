"""Owner-scoped same-target link role/span revision, common SQL and once budget.

The owner authorizes a proposal, not its unwritten postimage. No target,
source, Claim, Evidence, CVP text, withdrawal or new membership is permitted.
"""
from copy import deepcopy
from pathlib import Path
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from backend.api.canonical_repository.postgres_store import canonical_json, sha256_json, record_content_sha
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.pipeline import source_reconciliation as content
from backend.pipeline.source_reconciliation_runner import Runner, Stop, body, read, write
from backend.pipeline.viewpoint_resolution_runtime import subscription_client, read_artifact
from backend.pipeline.viewpoint_semantic_contract import semantic_prompt
from backend.pipeline.viewpoint_authorized_source_repair_357 import Review
from backend.pipeline.cvp_link_replacement import saved_raw

SCHEMA = 'wang_cvp_sql_link_representation_v1'
DIRECTION = 'wang_cvp_link_representation_direction_v1'
HUMAN = 'wang_cvp_link_representation_human_postimage_v1'
GENERATION = 'wang_cvp_reviewed_claim_link_generation_v1'
GENERATION_HANDOFF = 'wang_reviewed_claim_owned_link_transition_v1'


def generation_handoff(value):
    """Identify a typed domain, not approval or completion authority."""
    m=value.get('reviewed',{}).get('evidence',{}).get('action',{}).get('materials',{})
    if not m.get('generation'):return None
    return {'schema_version':GENERATION_HANDOFF,'claim_id':m['claim']['claim_id'],
            'viewpoint_ids':m['viewpoint_ids']}


def committed_generation_facts(store,value,*,cursor=None):
    """One source commit -> one jointly reviewed ALL-owned-link commit.

    This SAME authority is consumed at startup, audit scope, pending accounting
    and short SQL completion. Original Claim audit pins remain old facts; only
    actual approved before/after operations may bridge them. No ancestry search,
    implicit reapproval, model calls, or pending-list mutation.
    """
    from backend.pipeline.cvp_sql_commit import check_binding, decoded_pins, pin_rows
    from backend.pipeline.cvp_source_impact_resolution import applied_review,read_policy_slice_item
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    if generation_handoff(value) is None:return None
    if cursor is None:
        with store.connect() as conn,conn.cursor() as cur:
            facts=committed_generation_facts(store,value,cursor=cur)
        # Expensive complete model/compiler validation is outside transactions.
        validate_package(value['reviewed']['evidence']['package']['payload'])
        return facts
    e=value['reviewed']['evidence'];m=e['action']['materials'];auth=e['authorization']
    check_binding(auth);d=read_artifact(auth['path'])
    if (d.get('schema_version')!=GENERATION or d.get('artifact_sha256')!=sha256_json({k:v for k,v in d.items() if k!='artifact_sha256'})
            or d.get('allowed_fields')!=['pinned_claim_revision','component_locator']
            or d.get('roles_targets_states_evidence_unchanged') is not True
            or d.get('human_content_approval') is not False or d.get('authorized_by')!='bound_claim_policy'):
        raise ValueError('joint generation policy direction drift')
    ids=value['change_set_ids'];receipt=read_artifact(d['claim_layer_completion']['path'])
    if (value['checkpoint'] not in {'applied','audited'} or len(ids)<2
            or ids[-2]!=d['source_change_set_id'] or ids[:-1]!=receipt['change_set_ids']
            or value['campaign_sha256']!=d['campaign_sha256'] or value['run_id']!=d['run_id']
            or value['claim_ids']!=receipt['claim_ids'] or len(value['claim_ids'])!=len(set(value['claim_ids']))
            or value.get('claim_layer_audit',{}).get('receipt')!=d['claim_layer_completion']
            or e.get('pending_cvp_claim_ids') or e.get('viewpoint_ids')!=d['viewpoint_ids']
            or e.get('pending_cvp_management_record_keys')!=d['pending_record_keys']
            or e['action']['action_id']!=representation_action_id(d)
            or m['authorization']!=auth or set(e['approval'])!={'gpt','opus'}):
        raise ValueError('joint generation original roster/receipt/pending/commit drift')
    current=applied_review(None,value,value['reviewed_sha256'],cursor=cursor)
    source=applied_review(None,value,d['source_reviewed_sha256'],cursor=cursor)
    se=source['evidence'];package=e['package']['payload'];app=package['source_repair_application']
    if (current!=value['reviewed'] or current['plan']['change_set_id']!=ids[-1]
            or source['plan']['change_set_id']!=ids[-2]
            or se.get('pending_cvp_management_record_keys')!=d['pending_record_keys']
            or se.get('pending_cvp_claim_ids')!=value['claim_ids']
            or se['package']['sha256']!=sha256_json(body(read(Path(d['source_package']['path']))))
            or e['package']['sha256']!=sha256_json(package)
            or app.get('schema_version')!=SCHEMA or app.get('materials_sha256')!=sha256_json(m)
            or body(read(Path(app['run_root'])/'materials.json'))!=m):
        raise ValueError('joint generation exact named source/current package drift')
    ident='CVPA-'+sha256_json({'parent':value['run_id'],'action':e['action']['action_id']})
    registered=cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s',(ident,)).fetchone()
    if (not registered or registered[0].get('parent_run_id')!=value['run_id']
            or registered[0].get('action')!=e['action']):
        raise ValueError('joint generation registered action/material authority drift')
    bindings=[auth,d['claim_policy'],d['claim_layer_completion'],d['source_package'],receipt['audit'],
              *receipt['source_proofs'],*e['approval'].values(),
              *[b for b in se['approval'].values() if isinstance(b,dict) and 'path' in b]]
    bindings.extend({'path':s['path'],'file_sha256':s['file_sha256']} for s in m['sources'])
    for b in bindings:check_binding(b)
    for rev in (source,current):
        plan=rev['plan'];ops=plan['operations']
        fields=('collection','object_id','operation','before_revision','after_revision','before_sha256','after_sha256')
        actual=cursor.execute('SELECT '+','.join(fields)+' FROM wang_knowledge.change_operations WHERE change_set_id=%s ORDER BY operation_index',
            (plan['change_set_id'],)).fetchall()
        if not ops or actual!=[tuple(o[k] for k in fields) for o in ops] or len({(o['collection'],o['object_id']) for o in ops})!=len(ops):
            raise ValueError('joint generation complete actual write set drift')
        for op in ops:
            history=cursor.execute('SELECT payload,content_sha256,change_set_id FROM wang_knowledge.object_versions WHERE collection=%s AND object_id=%s AND revision=%s',
                (op['collection'],op['object_id'],op['after_revision'])).fetchone()
            if not history or history!=(op['payload'],op['after_sha256'],plan['change_set_id']) or record_content_sha(history[0])!=history[1]:
                raise ValueError('joint generation committed full afterimage drift')
    source_ops=source['plan']['operations'];ops=current['plan']['operations']
    if (len(source_ops)!=1 or source_ops[0]['operation']!='update' or source_ops[0]['collection']!='claims'
            or source_ops[0]['object_id']!=d['claim_id'] or source_ops[0]['payload']!=m['claim']):
        raise ValueError('joint generation source exceeds independently audited Claim')
    old_claim=cursor.execute("SELECT payload,content_sha256 FROM wang_knowledge.object_versions WHERE collection='claims' AND object_id=%s AND revision=%s",
        (d['claim_id'],source_ops[0]['before_revision'])).fetchone()
    if old_claim!=(m['old_claim'],source_ops[0]['before_sha256']):raise ValueError('joint generation original complete Claim drift')
    before={p['viewpoint_claim_link_id']:p for p in m['before_links']}
    new={p['viewpoint_claim_link_id']:p for p in package['viewpoint_claim_links']}
    declared=content.index([{'collection':col,'object_id':p[field],'payload':p}
        for col,field in [('viewpoint_claim_links','viewpoint_claim_link_id'),
            ('viewpoint_identity_candidates','identity_candidate_id'),('viewpoint_identity_decisions','identity_decision_id')]
        for p in package[col]])
    if (set(before)!=set(new) or len(before)!=len(m['before_links']) or len(new)!=len(package['viewpoint_claim_links'])
            or len(ops)!=3*len(before) or len(declared)!=len(ops)
            or any(declared.get(content.key(o['collection'],o['object_id']))!=o['payload'] for o in ops)
            or {(o['operation'],o['collection']) for o in ops}!={('update','viewpoint_claim_links'),('create','viewpoint_identity_candidates'),('create','viewpoint_identity_decisions')}):
        raise ValueError('joint generation incomplete/foreign write domain')
    old_pins=decoded_pins(receipt['read_pins']);pins=dict(old_pins)
    for op in ops:
        key=op['collection'],op['object_id']
        if key[0]=='viewpoint_claim_links':
            old=before.get(key[1]);after=new.get(key[1])
            previous=cursor.execute('SELECT payload,content_sha256 FROM wang_knowledge.object_versions WHERE collection=%s AND object_id=%s AND revision=%s',
                (*key,op['before_revision'])).fetchone()
            allowed={'revision','pinned_claim_revision','component_locator','decision_id'}
            if (not old or not after or previous!=(old,op['before_sha256'])
                    or old_pins.get(key)!=(op['before_revision'],op['before_sha256'],False)
                    or op['payload']!=after or after['pinned_claim_revision']!=m['claim']['revision']
                    or {k:v for k,v in old.items() if k not in allowed}!={k:v for k,v in after.items() if k not in allowed}):
                raise ValueError('joint generation changed role/target/Evidence or old/new full payload drift')
        elif op['before_revision'] is not None or key in old_pins:
            raise ValueError('joint generation identity must be genuinely fresh')
        pins[key]=(op['after_revision'],op['after_sha256'],False)
    if pins.get(('claims',d['claim_id']))!=tuple(d['claim_pin']):
        raise ValueError('joint generation Claim audit pin drift')
    actual_links=cursor.execute("SELECT object_id FROM wang_knowledge.objects WHERE collection='viewpoint_claim_links' AND retired_at IS NULL AND payload->>'claim_id'=%s AND payload->>'effective_state'='active'",(d['claim_id'],)).fetchall()
    if {i for i, in actual_links}!=set(new):raise ValueError('joint generation omitted active owned link')
    PostgresKnowledgeStore._assert_transaction_read_pins(cursor,pins)
    items=[]
    for key in d['pending_record_keys']:
        col,oid=content.split_key(key)
        if col=='viewpoint_claim_links':
            if oid not in new:raise ValueError('joint generation foreign pending link')
            items.append({'record_key':key,'kind':'approved_current_cvp_impact_independently_audited',
                'pin':list(pins[col,oid]),'change_set_id':ids[-1]})
        else:
            item=read_policy_slice_item(cursor,key)
            if old_pins.get((col,oid))!=tuple(item['pin']):raise ValueError('joint generation candidate context source pin drift')
            items.append(item)
    return {'source_handoff':generation_handoff(value),'pending_record_keys':list(d['pending_record_keys']),
        'items':items,'read_pins':pin_rows(pins),'bindings':bindings,
        'model_verdicts_changed':False,'original_pending_list_preserved':True}
PROMPT = '''负责人授权指定既有连接的成分范围及作用重新提议，不是批准新后像。
完整自身正文、Claim、实际 current CVR、全部成员、原身份判定、原审计及 GPT 异议均提供。
只返回本 Claim 中精确非空半开区间 spans 及准确 link_type。SHA 由程序计算。
不能预定等价或支持，解释完整语境的实际意思与具体论证作用；结论重述不能当论据。
保持同一连接ID、Claim、目标、Evidence绑定、active状态及所有其他记录。
不得借正文另一独立结论补 Claim，不截断限定，不改 Claim/Evidence/source/CVP/core/scope/端点/关系。
无法形成合法连接时明确 unresolved，不随意删连接或改正文以通过。'''

class Span(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start_char: int
    end_char: int
    exact_text: str

class Choice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    claim_id: str
    diagnosis: Literal['representation_revision', 'unresolved']
    link_type: Literal['equivalent_full','equivalent_component','supports','qualifies'] | None
    spans: list[Span]
    reason: str = Field(min_length=1)

class Response(BaseModel):
    model_config = ConfigDict(extra='forbid')
    selection: Choice

class GenerationChoice(Choice):
    viewpoint_claim_link_id: str

class GenerationResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    selections: list[GenerationChoice]

def adapter(stage, *, generation=False):
    if stage not in {'proposal','initial_review','correction','final_review'}:
        raise ValueError('one proposal/review and at most one correction/final review')
    proposal = stage in {'proposal','correction'}
    prompt = PROMPT if proposal else PROMPT + '''\n独立审核完整 after_link 后像，不重新提议。
每指定 Claim 恰好一个 verdict，核具体语义作用、准确 spans、其他成员不重叠与所有保留项。
decision_id 旧值仅显示，提交后程序以本轮真实审核产生 fresh VIC/VID。
全部成立才 approved=true 且 problems=[]，不能把 owner 执行许可当内容批准。'''
    obj = StructuredJsonReviewerAdapter(client=subscription_client(
        'codex' if proposal else 'claude','gpt-6.1-sol' if proposal else 'claude-opus-5-5','high'),
        prompt=semantic_prompt(prompt),response_model=Response if proposal else Review,
        schema_name=SCHEMA+'_'+stage,compact_payload=True,shared_text_payload=True,shared_record_payload=True)
    obj.max_request_bytes=500000
    if generation:
        prompt=semantic_prompt(PROMPT + '\n这是实际新Claim已独立批准并完成Claim原文审计后的必要连接版本管理，不是重新纠正Claim。'
            '\n必须返回每个 before_links 的精确ID、完整新Claim成分spans及理由，角色保持原link_type；无法成立明确unresolved。'
            '\nClaim、Evidence、原文、CVP核心、端点、状态全部不改。旧连接与全部原source回答/未决保留。'
            '\n审核时独立检查全部 after_links 与所有完整当前成员及自身正文，每个不同Claim一个联合verdict，全部连接都成立才批准。')
        obj=StructuredJsonReviewerAdapter(client=subscription_client('codex' if proposal else 'claude',
            'gpt-6.1-sol' if proposal else 'claude-opus-5-5','high'),prompt=prompt,
            response_model=GenerationResponse if proposal else Review,schema_name=GENERATION+'_'+stage,
            compact_payload=True,shared_text_payload=True,shared_record_payload=True)
        obj.max_request_bytes=500000
    return obj

def production_ports(*, generation=False):
    from backend.pipeline.source_reconciliation_runtime import ProductionPorts
    class Ports(ProductionPorts):
        def adapter(self,stage): return adapter(stage,generation=generation)
    return Ports()

def representation_action_id(d):
    if d.get('schema_version')==GENERATION:
        return 'CVPA-'+sha256_json({'contract':GENERATION,'claim_pin':d['claim_pin'],
            'link_pins':d['link_pins'],'source_change_set_id':d['source_change_set_id']})
    return 'CVPA-'+sha256_json({'contract':SCHEMA,'link':d['link_id'],'before':d['before_link_sha256']})

def generation_direction(store,run_id):
    """Program bound policy direction, never human afterimage approval."""
    from backend.pipeline.cvp_sql_commit import binding, check_binding
    from backend.pipeline.claim_first_management import validate_claim_audit_report
    from backend.pipeline.claim_source_write_slice import prepare
    from backend.pipeline.viewpoint_content_blocked_fallback import verify_saved_full_audit
    saved=store.get_cvp_group(run_id);e=saved['reviewed']['evidence']
    app=e['package']['payload']['source_repair_application'];root=Path(app['run_root'])
    proof=saved['claim_layer_audit']['receipt'];check_binding(proof);receipt=read_artifact(proof['path'])
    audit=read_artifact(receipt['audit']['path']);check_binding(receipt['audit'])
    accounting=validate_claim_audit_report(audit,saved['claim_ids'],store=store,saved=saved,root=root)
    if (not accounting or receipt.get('pending_cvp_locator_accounting')!=accounting
            or receipt.get('group_complete') is not False or receipt['change_set_ids']!=saved['change_set_ids']
            or verify_saved_full_audit(Path(receipt['audit']['path']),layers=('3',))!=receipt['source_proofs']):
        raise ValueError('link generation lacks exact complete source Claim audit')
    _,frozen,_,proposal,_=prepare(app['source_write_slice'])
    claims=content.index(proposal.records)
    if len(claims)!=1:raise ValueError('link generation requires one exact completed Claim')
    claim_id=next(iter(claims)).split('/',1)[1]
    claim=store.get_record_state('claims',claim_id)
    links=accounting['pending_owned_link_keys']
    policy=frozen['claim_issue_context']['policy'];check_binding(policy)
    from backend.pipeline.cvp_source_impact_resolution import applied_review
    views=set()
    for cid in saved['change_set_ids']:
        history=store.get_change_history(cid)
        if not history or history['status']!='applied' or history['metadata'].get('run_id')!=run_id:
            raise ValueError('link generation original committed group history missing')
        if history['source_kind']=='cvp_batch_resolution':
            review=applied_review(store,saved,history['metadata']['reviewed_sha256'])
            if review['plan']['change_set_id']!=cid:raise ValueError('link generation normal archive identity drift')
            views.update(review['evidence']['viewpoint_ids'])
    if not views:raise ValueError('link generation original normal CVP population missing')
    views.update(store.get_record_state(*content.split_key(k))['payload']['viewpoint_id'] for k in links)
    result={'schema_version':GENERATION,'run_id':run_id,'campaign_sha256':saved['campaign_sha256'],
        'claim_id':claim_id,'claim_pin':[claim['revision'],claim['content_sha256'],False],
        'link_pins':[p for p in accounting['current_domain_pins'] if p['collection']=='viewpoint_claim_links'],
        'link_keys':links,'claim_policy':policy,'claim_layer_completion':proof,
        'source_package':binding(root/'source-write-slice-package.json'),
        'source_change_set_id':accounting['change_set_id'],'source_reviewed_sha256':accounting['reviewed_sha256'],
        'viewpoint_ids':sorted(views),'pending_record_keys':e['pending_cvp_management_record_keys'],
        'authorized_by':'bound_claim_policy','human_content_approval':False,
        'allowed_fields':['pinned_claim_revision','component_locator'],
        'roles_targets_states_evidence_unchanged':True}
    result['artifact_sha256']=sha256_json(result)
    return result

def validate_generation_direction(d,m):
    from backend.pipeline.cvp_sql_commit import check_binding
    from backend.pipeline.claim_source_write_slice import validate_package as source_validate
    if d.get('artifact_sha256')!=sha256_json({k:v for k,v in d.items() if k!='artifact_sha256'}):
        raise ValueError('link generation direction seal drift')
    if (d.get('schema_version')!=GENERATION or d.get('authorized_by')!='bound_claim_policy'
            or d.get('human_content_approval') is not False
            or d.get('allowed_fields')!=['pinned_claim_revision','component_locator']
            or d.get('roles_targets_states_evidence_unchanged') is not True
            or d['run_id']!=m['run_id'] or d['campaign_sha256']!=m['campaign_sha256']
            or d['claim_pin']!=[m['claim']['revision'],record_content_sha(m['claim']),False]):
        raise ValueError('link generation policy/current Claim identity drift')
    for field in ('claim_policy','claim_layer_completion','source_package'):check_binding(d[field])
    policy=read_artifact(d['claim_policy']['path'])
    fields=policy.get('connection_fields',policy.get('link_fields',[]))
    if not {'pinned_claim_revision','component_locator'}<=set(fields):
        raise ValueError('link generation fields outside original Claim policy')
    package=body(read(Path(d['source_package']['path'])));source_validate(package)
    app=package['source_repair_application']
    if (app['run_id']!=d['run_id'] or app['source_write_slice']['mode']!='policy_claim_source_revision'
            or app['source_write_slice'].get('defer_owned_links') is not True
            or package['claims']!=[m['claim']]):raise ValueError('link generation completed source postimage drift')
    source_root=Path(app['run_root'])
    original_answers={stage:read(source_root/(stage+'.raw.json'))['response']
        for stage in ('proposal','initial_review','correction','final_review')}
    if m.get('original_source_responses')!=original_answers:
        raise ValueError('link generation omitted or changed original source decisions')
    receipt=read_artifact(d['claim_layer_completion']['path'])
    check_binding(receipt['audit'])
    for proof in receipt['source_proofs']:check_binding(proof)
    accounting=receipt.get('pending_cvp_locator_accounting',{})
    if (accounting.get('change_set_id')!=d['source_change_set_id']
            or accounting.get('reviewed_sha256')!=d['source_reviewed_sha256']
            or accounting.get('pending_owned_link_keys')!=d['link_keys']
            or receipt.get('status')!='claim_layer_audited_cvp_pending'
            or receipt.get('group_complete') is not False):raise ValueError('link generation completed audit authority drift')
    links=m['before_links']
    expected=[{'collection':'viewpoint_claim_links','object_id':p['viewpoint_claim_link_id'],
        'pin':[p['revision'],record_content_sha(p),False]} for p in links]
    if (expected!=d['link_pins'] or sorted(content.key('viewpoint_claim_links',p['viewpoint_claim_link_id']) for p in links)!=d['link_keys']
            or any(p['claim_id']!=d['claim_id'] or p['effective_state']!='active' or p['supporting_relation_ids']
                or p['pinned_claim_revision']!=m['old_claim']['revision'] for p in links)):
        raise ValueError('link generation incomplete/stale/foreign/held scope')

def prepare_generation_material(store,group,authorization):
    from backend.pipeline.cvp_sql_commit import pins, pin_rows
    from backend.pipeline.cvp_sql_actions import supports_identity_dependencies
    from backend.pipeline.cvp_link_target_selection import _approved
    from backend.pipeline.viewpoint_evidence_source_context import load_context_source
    d=read_artifact(authorization['path'])
    if generation_direction(store,group['run_id'])!=d:raise ValueError('link generation actual source/current proof drift')
    history=store.get_change_history(d['source_change_set_id'])
    old_claim=next(o['before'] for o in history['operations'] if o['collection']=='claims' and o['object_id']==d['claim_id'])
    records=store.read_cvp_context(group['claim_ids'],viewpoint_ids=d['viewpoint_ids'])
    active={k:r['payload'] for k,r in records.items() if not r['retired']}
    claim=active['claims',d['claim_id']]
    links=[active[tuple(content.split_key(k))] for k in d['link_keys']]
    actual={content.key(col,oid) for (col,oid),p in active.items() if col=='viewpoint_claim_links'
        and p['claim_id']==d['claim_id'] and p['effective_state']=='active'}
    if actual!=set(d['link_keys']):raise ValueError('link generation omitted active owned connection')
    extra=supports_identity_dependencies(store,records,[{'before_link':p} for p in links]);records.update(extra)
    for p in [claim,*links]:_approved(p)
    for link in links:
        head=active['canonical_viewpoints',link['viewpoint_id']];revision=active['viewpoint_revisions',head['current_revision_id']]
        _approved(head);_approved(revision)
        if head['current_revision_id']!=link['validated_against_viewpoint_revision_id']:raise ValueError('link generation current target drift')
    sources=[]
    for (col,_),doc in sorted(active.items()):
        if col=='source_documents':
            path,physical,projection=load_context_source(doc)
            sources.append({'source_id':doc['source_id'],'path':str(path),'file_sha256':physical,
                'body_sha256':projection.body_sha256,'paragraphs':deepcopy(projection.spoken_rows)})
    m={'generation':True,'run_id':group['run_id'],'campaign_sha256':group['campaign_sha256'],
        'authorization':authorization,'claim':claim,'old_claim':old_claim,'before_links':links,
        'viewpoint_ids':d['viewpoint_ids'],'sources':sources,'original_reports':[d['source_package'],d['claim_layer_completion']],
        'records':[{'collection':c,'object_id':i,'payload':p} for (c,i),p in sorted(active.items())],
        'provenance_templates':[{'collection':c,'object_id':i,'payload':r['payload']} for (c,i),r in sorted(extra.items()) if (c,i) not in active],
        'read_pins':pin_rows(pins(records))}
    source_root=Path(body(read(Path(d['source_package']['path'])))['source_repair_application']['run_root'])
    m['original_source_responses']={stage:read(source_root/(stage+'.raw.json'))['response']
        for stage in ('proposal','initial_review','correction','final_review')}
    validate_generation_direction(d,m)
    return json.loads(canonical_json(m))

def generation_postimages(m,response):
    choices=GenerationResponse.model_validate(response).selections
    indexed={p.viewpoint_claim_link_id:p for p in choices}
    if len(indexed)!=len(choices) or set(indexed)!={p['viewpoint_claim_link_id'] for p in m['before_links']}:
        raise ValueError('link generation requires ALL exact owned links once')
    result=[]
    for old in m['before_links']:
        choice=indexed[old['viewpoint_claim_link_id']]
        if choice.link_type!=old['link_type']:raise ValueError('generation cannot change old connection role')
        local={'before_link':old,'item':{'claim':m['claim']}}
        link=postimage(local,{'selection':choice.model_dump(exclude={'viewpoint_claim_link_id'})})
        link['pinned_claim_revision']=m['claim']['revision'];result.append(link)
    return result

def compile_generation_package(m,p,r,root):
    from backend.pipeline.cvp_link_retarget import fresh_identity_records
    approved(m,r);links=generation_postimages(m,p['response']);states=content.index(m['records']+m['provenance_templates'])
    candidates=[];decisions=[];choices={x['viewpoint_claim_link_id']:x for x in p['response']['selections']}
    for old,link in zip(m['before_links'],links):
        seed=sha256_json({'contract':GENERATION,'material':sha256_json(m),'link':old['viewpoint_claim_link_id'],
            'proposal':p['artifact_sha256'],'review':r['artifact_sha256']})
        projected={**p,'response':{'selection':choices[old['viewpoint_claim_link_id']]}}
        revision=states[content.key('viewpoint_revisions',link['validated_against_viewpoint_revision_id'])]
        candidate,decision=fresh_identity_records(states,old,link,revision['proposition_signature'],projected,r,seed=seed,policy=GENERATION)
        link['decision_id']=decision['identity_decision_id'];candidates.append(candidate);decisions.append(decision)
    return {'package_id':'CVP-LINK-GENERATION-'+sha256_json(m)[:24],'viewpoint_claim_links':links,
        'viewpoint_identity_candidates':candidates,'viewpoint_identity_decisions':decisions,
        'source_repair_application':{'schema_version':SCHEMA,'run_root':str(Path(root).resolve()),'materials_sha256':sha256_json(m)}}

def validate_direction(d,m):
    if m.get('generation'):
        return validate_generation_direction(d,m)
    expected={'schema_version':DIRECTION,'run_id':m['run_id'],'campaign_sha256':m['campaign_sha256'],
        'link_id':m['before_link']['viewpoint_claim_link_id'],'before_link_sha256':sha256_json(m['before_link']),
        'adjudication_root':m['adjudication_root'],'original_reports':m['original_reports'],
        'allowed_fields':['link_type','component_locator'],'authorized_by':'owner',
        'human_content_approval':False,'automatic_future_repairs_authorized':False}
    if any(d.get(k)!=v for k,v in expected.items()) or not d.get('authorization_quote'):
        raise ValueError('representation exact owner scope drift')
    if d.get('artifact_sha256')!=sha256_json({k:v for k,v in d.items() if k!='artifact_sha256'}):
        raise ValueError('representation direction seal drift')

def prepare_material(store,run_id,authorization):
    from backend.pipeline.cvp_sql_commit import check_binding, pins, pin_rows
    from backend.pipeline.cvp_sql_actions import supports_identity_dependencies
    from backend.pipeline.viewpoint_adjudication_history import validated_history
    from backend.pipeline.viewpoint_evidence_source_context import load_context_source
    from backend.pipeline.cvp_link_target_selection import _approved
    check_binding(authorization);d=read_artifact(authorization['path']);group=store.get_cvp_group(run_id)
    if d.get('schema_version')==GENERATION:
        return prepare_generation_material(store,group,authorization)
    packet,findings=validated_history(Path(d['adjudication_root']))
    items=[i for i in packet['items'] if any(l['viewpoint_claim_link_id']==d['link_id'] for l in i['connections'])]
    if len(items)!=1 or findings[items[0]['item_id']]['verdict']!='audit_objection_upheld':
        raise ValueError('representation requires exact current upheld finding')
    item=items[0];old=next(l for l in item['connections'] if l['viewpoint_claim_link_id']==d['link_id'])
    if old.get('effective_state')!='active' or old.get('supporting_relation_ids'):
        raise ValueError('inactive or relation-bound representation outside scope')
    records=store.read_cvp_context([old['claim_id']],viewpoint_ids=[old['viewpoint_id']])
    if records['viewpoint_claim_links',d['link_id']]['payload']!=old or records['claims',old['claim_id']]['payload']!=item['claim']:
        raise ValueError('representation current Claim/link changed')
    extra=supports_identity_dependencies(store,records,[{'before_link':old}]);records.update(extra)
    active={k:r['payload'] for k,r in records.items() if not r.get('retired')}
    for p in (old,item['claim'],active['canonical_viewpoints',old['viewpoint_id']],item['viewpoint_revision']): _approved(p)
    if active['canonical_viewpoints',old['viewpoint_id']]['current_revision_id']!=old['validated_against_viewpoint_revision_id']:
        raise ValueError('representation current CVR drift')
    sources=[]
    for (col,_),doc in sorted(active.items()):
        if col=='source_documents':
            path,physical,projection=load_context_source(doc)
            sources.append({'source_id':doc['source_id'],'path':str(path),'file_sha256':physical,
                'body_sha256':projection.body_sha256,'paragraphs':deepcopy(projection.spoken_rows)})
    m={'run_id':run_id,'campaign_sha256':group['campaign_sha256'],'authorization':authorization,
       'before_link':old,'item':item,'sources':sources,'adjudication_root':d['adjudication_root'],
       'original_reports':d['original_reports'],'records':[{'collection':c,'object_id':i,'payload':p} for (c,i),p in sorted(active.items())],
       'provenance_templates':[{'collection':c,'object_id':i,'payload':r['payload']} for (c,i),r in sorted(extra.items()) if (c,i) not in active],
       'read_pins':pin_rows(pins(records))}
    validate_direction(d,m)
    for b in m['original_reports']:check_binding(b)
    m['original_model_responses']=[{'binding':b,'model_id':r['model_id'],'backend':r['backend'],'response':r['response']}
        for b in m['original_reports'] for r in [read_artifact(b['path'])] if 'model_id' in r and 'response' in r]
    return json.loads(canonical_json(m))

def postimage(m,response):
    choice=Response.model_validate(response).selection;old=m['before_link'];claim=m['item']['claim']
    if choice.claim_id!=old['claim_id']:raise ValueError('foreign representation Claim')
    if choice.diagnosis!='representation_revision' or choice.link_type is None:
        raise Stop('representation unresolved; no write or additional semantic retry')
    spans=[s.model_dump() for s in choice.spans];text=claim['statement'];last=-1
    for s in spans:
        a,b=s['start_char'],s['end_char']
        if not 0<=a<b<=len(text) or a<last or text[a:b]!=s['exact_text']:
            raise ValueError('representation span/quote/order invalid')
        last=b
    if not spans:raise ValueError('representation requires explicit nonempty Claim spans')
    if choice.link_type=='equivalent_full' and spans!=[{'start_char':0,'end_char':len(text),'exact_text':text}]:
        raise ValueError('full equivalent requires exact full Claim')
    new=deepcopy(old);new.update(revision=old['revision']+1,link_type=choice.link_type,
        component_locator=None if choice.link_type=='equivalent_full' else {
          'claim_sha256':content.semantic_record_sha(claim),'canonical_spans':spans,
          'statement_component':''.join(s['exact_text'] for s in spans)})
    if new=={**old,'revision':old['revision']+1}:raise ValueError('representation cannot manufacture a no-change revision')
    return new

def review_packet(m,raw):
    if m.get('generation'):
        return {'schema_version':GENERATION,'materials':m,'proposal':raw['response'],
            'after_links':generation_postimages(m,raw['response']),
            'provenance_receipt':'Full current Claim generation and ALL owned links jointly reviewed; fresh VIC/VID only after actual approval.'}
    return {'schema_version':SCHEMA,'materials':m,'proposal':raw['response'],'after_link':postimage(m,raw['response']),
        'provenance_receipt':'Only role/spans change; fresh VIC/VID bind actual new approved model answers.'}

def approved(m,raw):
    r=Review.model_validate(raw['response']);cid=m['claim']['claim_id'] if m.get('generation') else m['before_link']['claim_id']
    if len(r.verdicts)!=1 or r.verdicts[0].claim_id!=cid or not r.verdicts[0].approved or r.verdicts[0].problems:
        raise Stop('representation independently rejected')

def correction_packet(m,p,r):
    return {'schema_version':SCHEMA,'materials':m,'proposal':p['response'],'initial_review':r['response'],
        'prior_artifacts':{'proposal':p['artifact_sha256'],'review':r['artifact_sha256']},'correction_boundary':'One correction only, same exact role/span scope.'}

def effective_saved(root,m):
    factory=(lambda stage:adapter(stage,generation=True)) if m.get('generation') else adapter
    p=saved_raw(root,'proposal',m,adapter_factory=factory)
    r=saved_raw(root,'initial_review',review_packet(m,p),adapter_factory=factory)
    try:approved(m,r)
    except Stop:
        p=saved_raw(root,'correction',correction_packet(m,p,r),adapter_factory=factory)
        r=saved_raw(root,'final_review',review_packet(m,p),adapter_factory=factory);approved(m,r)
    else:
        if any((root/(s+suffix)).exists() for s in ('correction','final_review') for suffix in ('.raw.json','.reservation.json','.call-intent.json')):
            raise ValueError('approved representation cannot acquire another round')
    return p,r

def compile_package(m,p,r,root):
    if m.get('generation'):
        return compile_generation_package(m,p,r,root)
    from backend.pipeline.cvp_link_retarget import fresh_identity_records
    approved(m,r);link=postimage(m,p['response']);states=content.index(m['records']+m['provenance_templates'])
    seed=sha256_json({'contract':SCHEMA,'materials':sha256_json(m),'proposal':p['artifact_sha256'],'review':r['artifact_sha256']})
    candidate,decision=fresh_identity_records(states,m['before_link'],link,m['item']['viewpoint_revision']['proposition_signature'],p,r,seed=seed,policy=SCHEMA)
    link['decision_id']=decision['identity_decision_id']
    return {'package_id':'CVP-LINK-REPRESENTATION-'+seed[:24],'viewpoint_claim_links':[link],
      'viewpoint_identity_candidates':[candidate],'viewpoint_identity_decisions':[decision],
      'source_repair_application':{'schema_version':SCHEMA,'run_root':str(Path(root).resolve()),'materials_sha256':sha256_json(m)}}

def validate_package(package):
    from backend.pipeline.cvp_sql_commit import check_binding
    app=package['source_repair_application'];root=Path(app['run_root']);m=body(read(root/'materials.json'))
    if app['schema_version']!=SCHEMA or not root.is_absolute() or app['materials_sha256']!=sha256_json(m):raise ValueError('representation authority drift')
    check_binding(m['authorization']);validate_direction(read_artifact(m['authorization']['path']),m)
    for b in m['original_reports']:check_binding(b)
    for s in m['sources']:check_binding({'path':s['path'],'file_sha256':s['file_sha256']})
    if app.get('human_disposition'):
        check_binding(app['human_disposition'])
        expected=human_package(m,app['human_disposition'],root)
    else:
        p,r=effective_saved(root,m)
        expected=compile_package(m,p,r,root)
    if package!=expected:raise ValueError('representation differs from actual approved package')

def human_saved(root,m,d):
    """Consume the original failed round, never manufacture an approved raw."""
    p=saved_raw(root,'proposal',m,adapter_factory=adapter)
    r=saved_raw(root,'initial_review',review_packet(m,p),adapter_factory=adapter)
    c=saved_raw(root,'correction',correction_packet(m,p,r),adapter_factory=adapter)
    if any(root.glob('final_review*')):
        raise ValueError('human representation requires the exact unresolved terminal boundary')
    if c['response']['selection']['diagnosis']!='unresolved':
        raise ValueError('human representation must preserve actual unresolved correction')
    try: approved(m,r)
    except Stop: pass
    else: raise ValueError('already approved representation is not a human exception')
    expected={'schema_version':HUMAN,'run_id':m['run_id'],'campaign_sha256':m['campaign_sha256'],
        'materials_sha256':sha256_json(m),'before_link_sha256':sha256_json(m['before_link']),
        'accepted_after_link':postimage(m,p['response']),
        'original_raw_artifact_sha256s':[p['artifact_sha256'],r['artifact_sha256'],c['artifact_sha256']],
        'authorized_by':'owner','human_content_approval':True,'automatic_future_repairs_authorized':False}
    if any(d.get(k)!=v for k,v in expected.items()) or not d.get('authorization_quote') or not d.get('decided_at'):
        raise ValueError('human representation exact postimage/round/owner binding drift')
    if d.get('artifact_sha256')!=sha256_json({k:v for k,v in d.items() if k!='artifact_sha256'}):
        raise ValueError('human representation seal drift')
    quotes=d.get('context_citations',[])
    if not quotes:raise ValueError('human contextual judgement requires physical source citations')
    own={o['source_id'] for o in m['item']['claim']['occurrences']}
    own_citation=False
    for citation in quotes:
        sources=[s for s in m['sources'] if s['source_id']==citation.get('source_id')]
        if len(sources)!=1:raise ValueError('foreign human contextual source')
        s=sources[0]
        if any(citation.get(k)!=s[k] for k in ('path','file_sha256','body_sha256')):
            raise ValueError('human contextual physical source drift')
        rows=[x for x in s['paragraphs'] if x['index']==citation.get('paragraph_index')]
        if len(rows)!=1 or not citation.get('exact_quote') or citation['exact_quote'] not in rows[0]['text']:
            raise ValueError('human context quotation is not exact original text')
        own_citation |= s['source_id'] in own
    if not own_citation:raise ValueError('human context cannot borrow only another Claim source')
    return p,r,c

def human_package(m,authorization,root):
    from backend.pipeline.cvp_link_retarget import fresh_identity_records
    from backend.pipeline.cvp_sql_commit import check_binding
    check_binding(authorization);d=read_artifact(authorization['path']);p,r,c=human_saved(root,m,d)
    link=postimage(m,p['response']);states=content.index(m['records']+m['provenance_templates'])
    seed=sha256_json({'contract':HUMAN,'materials':sha256_json(m),'disposition':d['artifact_sha256']})
    candidate,decision=fresh_identity_records(states,m['before_link'],link,
        m['item']['viewpoint_revision']['proposition_signature'],p,r,seed=seed,policy=HUMAN)
    decision.update(reviewer_kind='human_editor',reviewer_id='owner',reviewer_model_ids=[],
        approval_basis='human_exception_review',review_status='human_approved',
        review_artifact_sha256=d['artifact_sha256'],
        semantic_call_artifact_sha256s=sorted({p['artifact_sha256'],r['artifact_sha256'],c['artifact_sha256']}),
        created_at=d['decided_at'],reason=d['authorization_quote'])
    link.update(decision_id=decision['identity_decision_id'],review_status='human_approved')
    return {'package_id':'CVP-LINK-REPRESENTATION-HUMAN-'+seed[:24],
        'viewpoint_claim_links':[link],'viewpoint_identity_candidates':[candidate],
        'viewpoint_identity_decisions':[decision],
        'source_repair_application':{'schema_version':SCHEMA,'run_root':str(Path(root).resolve()),
            'materials_sha256':sha256_json(m),'human_disposition':authorization}}

def execute_human(store,run_id,*,authorization,disposition,root,ports):
    """Same registered action/root, zero new calls, common commit/readback/audit."""
    from backend.pipeline.cvp_sql_commit import check_binding, decoded_pins, commit_package, verified_group_backup
    from backend.pipeline.cvp_sql_actions import resume_bound_action
    check_binding(authorization);check_binding(disposition);d=read_artifact(authorization['path'])
    if d.get('schema_version')==GENERATION:raise ValueError('generation requires normal independent model review')
    aid=representation_action_id(d)
    root=Path(root)/aid;previous=store.get_cvp_action(run_id,aid)
    if not previous or previous['authorization']!=authorization:
        raise ValueError('human representation requires original registered action and authority')
    receipt=root/'human-disposition-binding.json'
    if receipt.exists() and read_artifact(receipt).get('disposition')!=disposition:
        raise ValueError('human representation disposition cannot change across recovery')
    resumed=resume_bound_action(store,run_id,aid)
    if resumed:
        if not receipt.exists():raise ValueError('non-human committed action cannot acquire human provenance')
        return resumed
    m=previous['materials'];validate_direction(d,m)
    if body(read(root/'materials.json'))!=m:raise ValueError('human representation original materials drift')
    for s in m['sources']:check_binding({'path':s['path'],'file_sha256':s['file_sha256']})
    expected=decoded_pins(m['read_pins'])
    with store.connect() as conn,conn.cursor() as cur:store._assert_transaction_read_pins(cur,expected)
    package=human_package(m,disposition,root);validate_package(package)
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    write_immutable(receipt,{'disposition':disposition})
    plan=store.plan_package(package,source_kind=SCHEMA,transactional=True)
    if len(plan.operations)!=3 or {(o.operation,o.collection) for o in plan.operations}!={('update','viewpoint_claim_links'),('create','viewpoint_identity_candidates'),('create','viewpoint_identity_decisions')}:
        raise ValueError('human representation exact three-operation write set violated')
    e=store.get_cvp_group(run_id)['reviewed']['evidence']
    return commit_package(store,run_id,package=package,approval={'human_exact_disposition':disposition},
        authorization=authorization,backup=verified_group_backup(root,package,ports),
        sources=[{'path':s['path'],'file_sha256':s['file_sha256']} for s in m['sources']],read_pins=expected,
        reason='human exact contextual equivalence; original model rejection retained',actor='owner',
        source_kind=SCHEMA,viewpoint_ids=e['viewpoint_ids'],disposition=e.get('disposition'),action=previous)

def execute(store,run_id,*,authorization,root,ports):
    from backend.pipeline.cvp_sql_commit import binding, check_binding, decoded_pins, commit_package, verified_group_backup
    from backend.pipeline.cvp_sql_actions import initialize_subscription_runtime,resume_bound_action
    check_binding(authorization);d=read_artifact(authorization['path'])
    aid=representation_action_id(d)
    root=Path(root)/aid;store.bind_cvp_execution_root(run_id,identity=aid,root=root)
    previous=store.get_cvp_action(run_id,aid)
    if previous and previous['authorization']!=authorization:raise ValueError('representation direction cannot reset once budget')
    resumed=resume_bound_action(store,run_id,aid)
    if resumed:return resumed
    m=previous['materials'] if previous else prepare_material(store,run_id,authorization);validate_direction(d,m)
    action=store.prepare_cvp_action(run_id,action_id=aid,kind='owner_link_correction',authorization=authorization,materials=m)
    write(root/'materials.json',m);expected=decoded_pins(m['read_pins'])
    def guard():
        for s in m['sources']:check_binding({'path':s['path'],'file_sha256':s['file_sha256']})
        with store.connect() as conn,conn.cursor() as cur:store._assert_transaction_read_pins(cur,expected)
    guard();initialize_subscription_runtime(ports);runner=Runner(root,ports);runner.root=root
    p=runner.call('proposal',m);guard();r=runner.call('initial_review',review_packet(m,p));guard();ps,rs='proposal','initial_review'
    try:approved(m,r)
    except Stop:
        p=runner.call('correction',correction_packet(m,p,r));guard()
        r=runner.call('final_review',review_packet(m,p));approved(m,r);guard();ps,rs='correction','final_review'
    package=compile_package(m,p,r,root);validate_package(package)
    plan=store.plan_package(package,source_kind=SCHEMA,transactional=True)
    expected_count=3*len(m['before_links']) if m.get('generation') else 3
    if len(plan.operations)!=expected_count or {(o.operation,o.collection) for o in plan.operations}!={('update','viewpoint_claim_links'),('create','viewpoint_identity_candidates'),('create','viewpoint_identity_decisions')}:
        raise ValueError('representation exact three-operation write set violated')
    e=store.get_cvp_group(run_id)['reviewed']['evidence']
    return commit_package(store,run_id,package=package,approval={'gpt':binding(root/(ps+'.raw.json')),'opus':binding(root/(rs+'.raw.json'))},
      authorization=authorization,backup=verified_group_backup(root,package,ports),
      sources=[{'path':s['path'],'file_sha256':s['file_sha256']} for s in m['sources']],read_pins=expected,
      reason=('bound Claim policy reviewed generation rebind; prior source decisions unchanged' if m.get('generation')
              else 'owner scoped same-target role/span revision; prior objections unchanged'),
      actor='policy-authorized-pipeline' if m.get('generation') else 'owner-authorized-pipeline',
      source_kind=SCHEMA,viewpoint_ids=m['viewpoint_ids'] if m.get('generation') else e['viewpoint_ids'],disposition=e.get('disposition'),action=action)
