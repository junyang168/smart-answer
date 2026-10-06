"""Exact owner-authorized g037 Claim revision; never a general Claim editor."""
from copy import deepcopy
import json
from pathlib import Path

from backend.api.canonical_repository.viewpoint_foundation import sha256_json, semantic_record_sha
from backend.api.canonical_repository.viewpoint_production_safety import file_sha256
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable, stable_decided_at
from backend.pipeline.viewpoint_validation_context import validated_history
from backend.pipeline.viewpoint_g037_context_review import CLAIM, LINK, VIEWPOINT, OLD_ROOT_NAME, PACKET_SHA, QUOTE

VERSION='wang_g037_claim_statement_repair_v2'
SECOND='VCL-e80b942b86b312d3ea46'
OLD='馬太福音所說的內心姦淫，不是單指看見異性並覺得對方漂亮，而是包含意志上決定與對方發生關係。'
NEW=OLD[:-1]+'；當人在意志上決定與對方行淫時，即使尚未實行，也已犯罪。'
SUPPORTS={'VCL-2254b81c9fefb3fec6d4','VCL-26189c856d2f28dd1abe','VCL-5337f7e2da0a2d268a84','VCL-c4644dafb6976949f4a0'}
PROMPT='''独立审核一条负责人授权的新Claim修订及六条精确连接后像。不是人工批准，旧拒绝全部保留，不要求批准。
保留旧statement完整必要条件“而是包含意志上決定與對方發生關係”，并追加决定时即已犯罪的充分条件/时间结论。负向component包含原来的意志决定限制，正向component包含追加结论；分别核查是否忠于自身上下文，不从充分条件倒推必要条件。
Claim原文S0003明确说“你還沒有做，你第一次決定的時候你就犯罪了。”检查新statement是否只补回同篇自身既有Evidence的明确结论，且保留觉得漂亮本身非犯罪的边界；全部旧Evidence/anchors保留，不能添加教授没说的意思。
指定Claim有两条equivalent_component：正向“意志决定时即犯罪”与负向“看见觉得漂亮本身非犯罪”，定位须互不重叠、ClaimSHA/revision精确更新、原目标及证据不变。其他四条equivalent改supports仍须成立。一个Claim verdict必须检查它的全部targets。
after_claim/after_link为实际内容后像，fresh审核provenance在双审通过后才写入，不继承旧批准。旧identity decision仅历史保留。关联candidate Claim关系及问题不改、不冒充已批准支持事实，检查修订对其影响；如需额外内容修改拒绝。
不改其他Claim/Evidence/source/CVP/结构/关系或端点，不评价神学对错。每指定Claim恰好一个verdict；全部精确后像忠于来源才approved=true且problems=[]。'''


def review_adapter(client):
    from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
    from backend.pipeline.viewpoint_authorized_source_repair_357 import Review
    adapter=StructuredJsonReviewerAdapter(client=client,prompt=PROMPT,response_model=Review,
        schema_name='audit_link_repair_review_v1',compact_payload=True,shared_text_payload=True,shared_record_payload=True)
    adapter.max_request_bytes=500000
    return adapter


def locator(claim, text):
    if claim['statement'].count(text)!=1:
        raise ValueError('authorized Claim component is not exact unique text')
    start=claim['statement'].index(text)
    return {'claim_sha256':semantic_record_sha(claim),'statement_component':text,
        'canonical_spans':[{'start_char':start,'end_char':start+len(text),'exact_text':text}]}


def prepare(packet,states,root,base):
    validate_previous_refusal(base)
    from backend.pipeline.viewpoint_g037_context_review import prepare as validate_frozen
    # Validate old full review identity/source/Registry; do not call old models.
    validate_frozen(packet,states,base/OLD_ROOT_NAME,base)
    old=states['claims',CLAIM]
    if old['revision']!=2 or old['statement']!=OLD:
        raise ValueError('authorized Claim predecessor drift')
    links=[p for (c,_),p in states.items() if c=='viewpoint_claim_links' and p.get('claim_id')==CLAIM]
    if {p['viewpoint_claim_link_id'] for p in links}!={LINK,SECOND} or any(p['revision']!=1 or p['pinned_claim_revision']!=2 or p['link_type']!='equivalent_component' for p in links):
        raise ValueError('authorized Claim dependent link population drift')
    if any(CLAIM in json.dumps(p) for (c,_),p in states.items() if c in {'argument_routes','argument_route_revisions','argument_route_attestations','product_dependencies','claim_concept_units','viewpoint_resolution_ledgers'}):
        raise ValueError('unplanned pinned Claim dependents')
    new=deepcopy(old);new.update(revision=3,statement=NEW,
        reviewed_at=stable_decided_at(root),reviewed_by='gpt-6.1-sol+claude-opus-5-5',
        review_note=f'Owner-authorized g037 statement completion; fresh exact dual review: {root}/review-report.json. Old revision/refusals and all evidence retained.')
    out=deepcopy(packet)
    t=next(t for t in out['targets'] if t['claim']['claim_id']==CLAIM)
    t['claim']=new
    second=deepcopy(t);second['item_id']='owner-version-rebind:'+SECOND
    second['before_link']=deepcopy(states['viewpoint_claim_links',SECOND])
    second['viewpoint_revision']=deepcopy(states['viewpoint_revisions',second['before_link']['validated_against_viewpoint_revision_id']])
    second['adjacent_links']=[deepcopy(p) for (c,_),p in states.items() if c=='viewpoint_claim_links' and p.get('viewpoint_id')==second['before_link']['viewpoint_id'] and p.get('effective_state')=='active']
    second['adjacent_claims']=[deepcopy(states['claims',cid]) for cid in sorted({p['claim_id'] for p in second['adjacent_links']})]
    # Exact evidence/fragment pairs for the second retained negative component.
    second['evidence']=[{'step':deepcopy(states['evidence_steps',b['evidence_step_id']]),'fragment':deepcopy(states['source_fragments',b['source_fragment_id']])} for b in second['before_link']['evidence_bindings']]
    out['targets'].append(second)
    for item in out['targets']:
        if item['claim']['claim_id']==CLAIM:
            after=deepcopy(item['before_link']);after.update(revision=2,pinned_claim_revision=3,
                component_locator=locator(new,NEW.split('；')[1] if after['viewpoint_claim_link_id']==LINK else NEW.split('；')[0]+'；'))
            item['after_link']=after
        item['adjacent_claims']=[new if c['claim_id']==CLAIM else c for c in item['adjacent_claims']]
    out['claim_revision']={'before':old,'after':new}
    out['preserved_claim_dependents']=[{'collection':c,'object_id':o,'payload':p} for (c,o),p in states.items()
        if c in {'claim_relations','questions'} and CLAIM in json.dumps(p)]
    if any(r['payload'].get('review_status')!='candidate' for r in out['preserved_claim_dependents']):
        raise ValueError('approved Claim dependent requires new scope')
    out['policy']={'policy_id':VERSION,'authorized_by':'owner','authorization_quote':'同意',
        'authorized_statement':NEW,'future_claim_edits':False,'evidence_source_CVP_text_writes':0}
    out['claim_source_viewpoint_text_writes']=1
    # Capture immutable identity predecessors so package validation can rebuild
    # fresh decisions without trusting the live Registry or historical approvals.
    out['identity_predecessors']=[]
    for t in out['targets']:
        d=states['viewpoint_identity_decisions',t['before_link']['decision_id']]
        out['identity_predecessors'].append({'decision':d,'candidate':states['viewpoint_identity_candidates',d['identity_candidate_id']]})
    validate_packet(out)
    return out


def validate_packet(packet):
    old,new=packet['claim_revision']['before'],packet['claim_revision']['after']
    mutable={'revision','statement','review_note','reviewed_at','reviewed_by'}
    if old['claim_id']!=CLAIM or old['revision']!=2 or old['statement']!=OLD or new['statement']!=NEW or new['revision']!=3 or {k:v for k,v in old.items() if k not in mutable}!={k:v for k,v in new.items() if k not in mutable}:
        raise ValueError('Claim repair outside exact owner statement scope')
    ts=packet['targets']
    if len(ts)!=6 or {t['before_link']['viewpoint_claim_link_id'] for t in ts}!=SUPPORTS|{LINK,SECOND}:
        raise ValueError('Claim repair link population drift')
    if packet.get('policy')!={'policy_id':VERSION,'authorized_by':'owner','authorization_quote':'同意',
        'authorized_statement':NEW,'future_claim_edits':False,'evidence_source_CVP_text_writes':0} or packet.get('claim_source_viewpoint_text_writes')!=1:
        raise ValueError('Claim repair authority drift')
    verify_frozen_context(packet)
    for t in ts:
        before,after=t['before_link'],t['after_link']; expected=deepcopy(before);expected['revision']+=1
        lid=before['viewpoint_claim_link_id']
        if lid in {LINK,SECOND}:
            if before['claim_id']!=CLAIM or t['claim']!=new:
                raise ValueError('Claim repair foreign component')
            expected.update(pinned_claim_revision=3,component_locator=locator(new,NEW.split('；')[1] if lid==LINK else NEW.split('；')[0]+'；'))
        else:expected.update(link_type='supports',component_locator=None)
        if after!=expected:raise ValueError('Claim repair unauthorized link change')


def verify_frozen_context(packet):
    from backend.pipeline.viewpoint_audit_link_repair import BASE
    frozen=read_artifact(BASE/OLD_ROOT_NAME/'packet.json')
    if sha256_json(frozen)!=PACKET_SHA:
        raise ValueError('Claim repair frozen predecessor SHA drift')
    old,new=packet['claim_revision']['before'],packet['claim_revision']['after']
    own=next(t for t in frozen['targets'] if t['claim']['claim_id']==CLAIM)
    if old!=own['claim']:raise ValueError('Claim repair predecessor content drift')
    for t in packet['targets']:
        lid=t['before_link']['viewpoint_claim_link_id']
        if lid!=SECOND:
            original=next(o for o in frozen['targets'] if o['before_link']['viewpoint_claim_link_id']==lid)
            expected=deepcopy(original);expected['after_link']=t['after_link']
            if expected['claim']['claim_id']==CLAIM:expected['claim']=new
            expected['adjacent_claims']=[new if c['claim_id']==CLAIM else c for c in expected['adjacent_claims']]
            if t!=expected:raise ValueError('Claim repair frozen review context drift')
        else:
            original=next(o for o in frozen['targets'] if o['before_link']['viewpoint_id']=='CV-84ff210efbba6aeff1e0')
            bound=[l for l in original['adjacent_links'] if l['viewpoint_claim_link_id']==SECOND]
            if len(bound)!=1 or t['before_link']!=bound[0] or t['sources']!=own['sources'] or t['claim']!=new or t['viewpoint_revision']!=original['viewpoint_revision']:
                raise ValueError('Claim repair negative component context drift')
            if t['adjacent_links']!=original['adjacent_links'] or t['adjacent_claims']!=[new if c['claim_id']==CLAIM else c for c in original['adjacent_claims']]:
                raise ValueError('Claim repair negative adjacent membership drift')
            pairs={(b['evidence_step_id'],b['source_fragment_id']) for b in bound[0]['evidence_bindings']}
            if {(r['step']['evidence_step_id'],r['fragment']['fragment_id']) for r in t['evidence']}!=pairs:
                raise ValueError('Claim repair negative evidence population drift')
        for source in t['sources']:
            if file_sha256(Path(source['path']))!=source['file_sha256']:
                raise ValueError('Claim repair physical source drift')
    deps=packet['preserved_claim_dependents']
    wanted={('claim_relations','DK-78f4b0b8eb95-P01-CR001'),*[('questions',f'DK-78f4b0b8eb95-P01-Q{n:03}') for n in (2,3,7)]}
    if len(deps)!=4 or {(r['collection'],r['object_id']) for r in deps}!=wanted or any(r['payload'].get('review_status')!='candidate' for r in deps):
        raise ValueError('Claim repair dependent scope drift')
    if len(packet['identity_predecessors'])!=6:
        raise ValueError('Claim repair identity predecessor population drift')
    for t,row in zip(packet['targets'],packet['identity_predecessors']):
        d,c=row['decision'],row['candidate']
        if d['identity_decision_id']!=t['before_link']['decision_id'] or d['identity_candidate_id']!=c['identity_candidate_id'] or t['before_link']['claim_id'] not in c['candidate_claim_ids'] or d.get('resolved_viewpoint_id')!=t['before_link']['viewpoint_id']:
            raise ValueError('Claim repair identity predecessor binding drift')


def reserve(root,base):
    validate_previous_refusal(base)
    source=base/'g037-owner-claim-statement-repair-20261005-v2'
    reservation=source/'owner-claim-condition-revision-start.json'
    if reservation.exists():raise ValueError('Claim revision already reserved; no repeat')
    authority={'policy_id':VERSION,'authorization_quote':'同意','statement':NEW,'claim_id':CLAIM,
        'output_root':str(root),'fresh_reviews_per_provider':1,'human_content_approval':False,
        'historical_bindings':[{'path':str(source/n),'file_sha256':file_sha256(source/n)} for n in ('packet.json','before.json','raw-gpt.json','raw-gpt.request-encoding.json','failure.json')]}
    write_immutable(reservation,authority);write_immutable(root/'owner-authorization.json',authority)


def package_for(packet,reviews,root):
    from backend.pipeline.viewpoint_audit_link_repair import package_for as links_package
    validate_packet(packet)
    states={}
    for row in packet['identity_predecessors']:
        d,c=row['decision'],row['candidate'];states['viewpoint_identity_decisions',d['identity_decision_id']]=d;states['viewpoint_identity_candidates',c['identity_candidate_id']]=c
    package=links_package(states,packet,reviews,root)
    package['claims']=[deepcopy(packet['claim_revision']['after'])]
    for d in package['viewpoint_identity_decisions']:
        d.update(policy_version=VERSION,reason='Exact owner-authorized Claim statement revision and dependency revalidation; fresh dual reviews, old semantic refusals retained, source/Evidence/CVP unchanged.')
    package['source_repair_application']={'schema_version':VERSION,'user_authorization':'同意',
        'approval_status':'not_human_approved','review_packet':packet,
        'review_artifacts':[read_artifact(root/f'raw-{label}.json') for label in ('gpt','opus')],
        'package_sha256':sha256_json(package),'review_root':str(root)}
    return package


@validated_history
def validate_package(package):
    from backend.pipeline.viewpoint_audit_link_repair import link_review_adapter
    from backend.pipeline.viewpoint_resolution_runtime import subscription_client
    app=package['source_repair_application'];p=app['review_packet'];validate_packet(p)
    body={k:v for k,v in package.items() if k!='source_repair_application'}
    if app.get('schema_version')!=VERSION or set(body)!={'package_id','claims','viewpoint_claim_links','viewpoint_identity_candidates','viewpoint_identity_decisions'} or app.get('approval_status')!='not_human_approved' or app.get('user_authorization')!='同意' or app.get('package_sha256')!=sha256_json(body) or body['claims']!=[p['claim_revision']['after']]:
        raise ValueError('Claim repair reviewed package drift')
    ids={t['claim']['claim_id'] for t in p['targets']};raws=app['review_artifacts'];root=Path(app['review_root'])
    if len(raws)!=2:raise ValueError('Claim repair needs two fresh reviews')
    for raw,(label,provider,model) in zip(raws,[('gpt','codex','gpt-6.1-sol'),('opus','claude','claude-opus-5-5')]):
        adapter=review_adapter(subscription_client(provider,model,'high'))
        if any(raw.get(k)!=v for k,v in {'model_id':model,'backend':adapter.backend,'prompt_sha256':adapter.prompt_sha256,'generation_config_sha256':adapter.generation_config_sha256,'request_payload_sha256':sha256_json(p)}.items()) or raw['artifact_sha256']!=sha256_json({k:v for k,v in raw.items() if k!='artifact_sha256'}) or raw['response_sha256']!=sha256_json(raw['response']) or read_artifact(root/f'raw-{label}.json')!=raw:
            raise ValueError('Claim repair actual review identity drift')
        proof=read_artifact(root/f'raw-{label}.request-encoding.json')
        expected={'schema_version':'wang_model_request_encoding_proof_v1',**adapter.request_encoding_proof(p)}
        if proof!={**expected,'artifact_sha256':sha256_json(expected)} or raw.get('request_encoding_proof_sha256')!=sha256_json(adapter.request_encoding_proof(p)) or adapter.request_bytes(p)>500000:
            raise ValueError('Claim repair actual transport proof drift')
        rows=raw['response']['verdicts']
        if len(rows)!=len(ids) or {v['claim_id'] for v in rows}!=ids or any(v['approved'] is not True or v['problems'] for v in rows):
            raise ValueError('Claim repair lacks unanimous complete approval')
    # Rebuild exact fresh identity records from the bound old records/reviews.
    rebuilt=package_for(p,{label:r['response'] for label,r in zip(('gpt','opus'),raws)},root)
    if rebuilt!=package:raise ValueError('Claim repair identity materialization drift')
    authority=read_artifact(root/'owner-authorization.json')
    from backend.pipeline.viewpoint_audit_link_repair import BASE
    validate_previous_refusal(BASE)
    reserved=read_artifact(BASE/'g037-owner-claim-statement-repair-20261005-v2/owner-claim-condition-revision-start.json')
    if authority!=reserved or authority.get('output_root')!=str(root) or authority.get('policy_id')!=VERSION or authority.get('statement')!=NEW or authority.get('human_content_approval') is not False:
        raise ValueError('Claim repair single-use authorization drift')
    for b in authority['historical_bindings']:
        if file_sha256(Path(b['path']))!=b['file_sha256']:raise ValueError('Claim repair refusal history drift')

def validate_previous_refusal(base):
    """Bind the new authority to the actual preserved previous semantic refusal."""
    from backend.pipeline import viewpoint_g037_claim_statement_repair as previous
    from backend.pipeline.viewpoint_resolution_runtime import subscription_client
    source=base/'g037-owner-claim-statement-repair-20261005-v2'
    packet=read_artifact(source/'packet.json')
    raw=read_artifact(source/'raw-gpt.json')
    adapter=previous.review_adapter(subscription_client('codex','gpt-6.1-sol','high'))
    if raw.get('artifact_sha256')!='5b564f057bb2db57dbcf73b1642fcddcac44a79f43aa9510a719eee42331da65':
        raise ValueError('Previous Claim refusal physical identity drift')
    if raw['artifact_sha256']!=sha256_json({k:v for k,v in raw.items() if k!='artifact_sha256'}) or raw['response_sha256']!=sha256_json(raw['response']):
        raise ValueError('Previous Claim refusal integrity drift')
    expected={'model_id':'gpt-6.1-sol','backend':adapter.backend,'prompt_sha256':adapter.prompt_sha256,
        'generation_config_sha256':adapter.generation_config_sha256,'request_payload_sha256':sha256_json(packet),
        'request_encoding_proof_sha256':sha256_json(adapter.request_encoding_proof(packet))}
    if any(raw.get(k)!=v for k,v in expected.items()):
        raise ValueError('Previous Claim refusal request identity drift')
    proof={'schema_version':'wang_model_request_encoding_proof_v1',**adapter.request_encoding_proof(packet)}
    if read_artifact(source/'raw-gpt.request-encoding.json')!={**proof,'artifact_sha256':sha256_json(proof)}:
        raise ValueError('Previous Claim refusal transport drift')
    verdicts=raw['response']['verdicts']
    rejected=[v for v in verdicts if not v['approved'] or v['problems']]
    if len(verdicts)!=5 or len(rejected)!=1 or rejected[0]['claim_id']!=CLAIM or rejected[0]['approved'] is not False:
        raise ValueError('Previous Claim refusal verdict drift')
    if any((source/n).exists() for n in ('apply-intent.json','apply-receipt.json','raw-opus.json')):
        raise ValueError('Previous Claim refusal execution boundary drift')
