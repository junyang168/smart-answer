"""Owner-scoped same-target link role/span revision, common SQL and once budget.

The owner authorizes a proposal, not its unwritten postimage. No target,
source, Claim, Evidence, CVP text, withdrawal or new membership is permitted.
"""
from copy import deepcopy
from pathlib import Path
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from backend.api.canonical_repository.postgres_store import canonical_json, sha256_json
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.pipeline import source_reconciliation as content
from backend.pipeline.source_reconciliation_runner import Runner, Stop, body, read, write
from backend.pipeline.viewpoint_resolution_runtime import subscription_client, read_artifact
from backend.pipeline.viewpoint_semantic_contract import semantic_prompt
from backend.pipeline.viewpoint_authorized_source_repair_357 import Review
from backend.pipeline.cvp_link_replacement import saved_raw

SCHEMA = 'wang_cvp_sql_link_representation_v1'
DIRECTION = 'wang_cvp_link_representation_direction_v1'
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

def adapter(stage):
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
    return obj

def production_ports():
    from backend.pipeline.source_reconciliation_runtime import ProductionPorts
    class Ports(ProductionPorts):
        def adapter(self,stage): return adapter(stage)
    return Ports()

def validate_direction(d,m):
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
    from backend.pipeline.cvp_sql_flow import check_binding,pins,pin_rows
    from backend.pipeline.cvp_sql_actions import supports_identity_dependencies
    from backend.pipeline.viewpoint_adjudication_history import validated_history
    from backend.pipeline.viewpoint_evidence_source_context import load_context_source
    from backend.pipeline.cvp_link_target_selection import _approved
    check_binding(authorization);d=read_artifact(authorization['path']);group=store.get_cvp_group(run_id)
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
    return {'schema_version':SCHEMA,'materials':m,'proposal':raw['response'],'after_link':postimage(m,raw['response']),
        'provenance_receipt':'Only role/spans change; fresh VIC/VID bind actual new approved model answers.'}

def approved(m,raw):
    r=Review.model_validate(raw['response']);cid=m['before_link']['claim_id']
    if len(r.verdicts)!=1 or r.verdicts[0].claim_id!=cid or not r.verdicts[0].approved or r.verdicts[0].problems:
        raise Stop('representation independently rejected')

def correction_packet(m,p,r):
    return {'schema_version':SCHEMA,'materials':m,'proposal':p['response'],'initial_review':r['response'],
        'prior_artifacts':{'proposal':p['artifact_sha256'],'review':r['artifact_sha256']},'correction_boundary':'One correction only, same exact role/span scope.'}

def effective_saved(root,m):
    p=saved_raw(root,'proposal',m,adapter_factory=adapter)
    r=saved_raw(root,'initial_review',review_packet(m,p),adapter_factory=adapter)
    try:approved(m,r)
    except Stop:
        p=saved_raw(root,'correction',correction_packet(m,p,r),adapter_factory=adapter)
        r=saved_raw(root,'final_review',review_packet(m,p),adapter_factory=adapter);approved(m,r)
    else:
        if any((root/(s+suffix)).exists() for s in ('correction','final_review') for suffix in ('.raw.json','.reservation.json','.call-intent.json')):
            raise ValueError('approved representation cannot acquire another round')
    return p,r

def compile_package(m,p,r,root):
    from backend.pipeline.cvp_link_retarget import fresh_identity_records
    approved(m,r);link=postimage(m,p['response']);states=content.index(m['records']+m['provenance_templates'])
    seed=sha256_json({'contract':SCHEMA,'materials':sha256_json(m),'proposal':p['artifact_sha256'],'review':r['artifact_sha256']})
    candidate,decision=fresh_identity_records(states,m['before_link'],link,m['item']['viewpoint_revision']['proposition_signature'],p,r,seed=seed,policy=SCHEMA)
    link['decision_id']=decision['identity_decision_id']
    return {'package_id':'CVP-LINK-REPRESENTATION-'+seed[:24],'viewpoint_claim_links':[link],
      'viewpoint_identity_candidates':[candidate],'viewpoint_identity_decisions':[decision],
      'source_repair_application':{'schema_version':SCHEMA,'run_root':str(Path(root).resolve()),'materials_sha256':sha256_json(m)}}

def validate_package(package):
    from backend.pipeline.cvp_sql_flow import check_binding
    app=package['source_repair_application'];root=Path(app['run_root']);m=body(read(root/'materials.json'))
    if app['schema_version']!=SCHEMA or not root.is_absolute() or app['materials_sha256']!=sha256_json(m):raise ValueError('representation authority drift')
    check_binding(m['authorization']);validate_direction(read_artifact(m['authorization']['path']),m)
    for b in m['original_reports']:check_binding(b)
    for s in m['sources']:check_binding({'path':s['path'],'file_sha256':s['file_sha256']})
    p,r=effective_saved(root,m)
    if package!=compile_package(m,p,r,root):raise ValueError('representation differs from actual approved package')

def execute(store,run_id,*,authorization,root,ports):
    from backend.pipeline.cvp_sql_flow import binding,check_binding,decoded_pins,commit_package,verified_group_backup
    from backend.pipeline.cvp_sql_actions import initialize_subscription_runtime,resume_bound_action
    check_binding(authorization);d=read_artifact(authorization['path'])
    aid='CVPA-'+sha256_json({'contract':SCHEMA,'link':d['link_id'],'before':d['before_link_sha256']})
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
    if len(plan.operations)!=3 or {(o.operation,o.collection) for o in plan.operations}!={('update','viewpoint_claim_links'),('create','viewpoint_identity_candidates'),('create','viewpoint_identity_decisions')}:
        raise ValueError('representation exact three-operation write set violated')
    e=store.get_cvp_group(run_id)['reviewed']['evidence']
    return commit_package(store,run_id,package=package,approval={'gpt':binding(root/(ps+'.raw.json')),'opus':binding(root/(rs+'.raw.json'))},
      authorization=authorization,backup=verified_group_backup(root,package,ports),
      sources=[{'path':s['path'],'file_sha256':s['file_sha256']} for s in m['sources']],read_pins=expected,
      reason='owner scoped same-target role/span revision; prior objections unchanged',actor='owner-authorized-pipeline',
      source_kind=SCHEMA,viewpoint_ids=e['viewpoint_ids'],disposition=e.get('disposition'),action=action)
