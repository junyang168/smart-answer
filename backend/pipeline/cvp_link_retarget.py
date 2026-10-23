"""Exact existing-CVP retarget, shared SQL action/once/transaction/audit.

This is not a fallback from a rejected same-target review. A fresh bounded
owner direction must bind the original disagreement and exact current graph.
Only one existing link and its fresh identity provenance may change.
"""
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from backend.api.canonical_repository.postgres_store import canonical_json, sha256_json
from backend.pipeline import source_reconciliation as content
from backend.pipeline import cvp_link_target_selection as selection
from backend.pipeline.source_reconciliation_runner import Runner, Stop, body, read, write, request_limit
from backend.pipeline.viewpoint_authorized_source_repair_357 import Review
from backend.pipeline.viewpoint_resolution_runtime import (
    response_adapter, bound_model_response, subscription_client, read_artifact,
)

SCHEMA = 'wang_cvp_sql_link_retarget_v1'
DIRECTION = 'wang_cvp_link_retarget_direction_v1'
REVIEW_PROMPT = '''独立核查 before_link/after_link 的完整改挂后像及 GPT 原理由。
对比 Claim 完整自身正文、实际已有证据、旧目标和新目标完整当前 CVR、所有成员与依赖。
只能把指定旧等价连接改挂到批准的既有目标，类型 supports；原 Claim、Claim版本、Evidence/fragment绑定、来源、CVP核心及其他连接原样。
逐字说明 Claim 哪一句以什么论证作用支持新 CVP；不能仅同主题或相容，不能借其他 Claim 或 Evidence 中另一个结论替代本 Claim。
确认旧观点仍有其他独立等价成员；缺必要版本、hold、歧义或语义不成立则拒绝。
after_link 的 decision_id 暂留原 ID 只为展示；提交时程序创建本轮单 Claim/new-target 身份判定并换 decision_id。
provenance_receipt 明确这些新判定由程序绑定本轮真实回答，不让模型回填 SHA，不改回答/理由。
每个指定 Claim 一个 verdict；全部成立才 approved=true 且 problems=[]。不为了续跑批准，不评价教授神学对错。'''


def model_adapter(stage, live=False):
    if stage == 'proposal':
        client = subscription_client('codex','gpt-6.1-sol','high')
        return selection.adapter(client)
    if stage != 'initial_review':
        raise ValueError('retarget has no second correction/review budget')
    from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
    from backend.pipeline.viewpoint_semantic_contract import semantic_prompt
    obj = StructuredJsonReviewerAdapter(
        client=subscription_client('claude','claude-opus-5-5','high'),
        prompt=semantic_prompt(REVIEW_PROMPT), response_model=Review, schema_name=SCHEMA,
        compact_payload=True, shared_text_payload=True, shared_record_payload=True)
    obj.max_request_bytes=500000
    return obj


def production_ports():
    from backend.pipeline.source_reconciliation_runtime import ProductionPorts
    class Ports(ProductionPorts):
        def adapter(self, stage):return model_adapter(stage, True)
    return Ports()


def validate_direction(direction, material):
    packet=material['comparison'];old=packet['before_link']
    expected={'schema_version':DIRECTION,'run_id':material['run_id'],
              'campaign_sha256':material['campaign_sha256'],
              'link_id':old['viewpoint_claim_link_id'],'before_link_sha256':sha256_json(old),
              'comparison_sha256':sha256_json(packet),'allowed_link_type':'supports',
              'candidate_viewpoint_ids':[p['viewpoint']['viewpoint_id'] for p in packet['candidates']],
              'original_reports':material['original_reports'],
              'authorized_by':'owner','human_content_approval':False,
              'automatic_future_repairs_authorized':False}
    if any(direction.get(k)!=v for k,v in expected.items()) or not direction.get('authorization_quote'):
        raise ValueError('retarget direction does not bind exact original disagreement/current scope')
    if not material['original_reports']:
        raise ValueError('retarget cannot discard the original rejected review')


def postimage(packet, response):
    result=selection.validate_selection(packet,response)['selection']
    if result['diagnosis']!='different_target' or result['link_type']!='supports':
        raise Stop('exact retarget proposal rejected/unresolved or requires another scope; no retry')
    old=packet['before_link'];cid=old['claim_id'];target=result['viewpoint_id']
    if old['link_type'] not in {'equivalent_full','equivalent_component'}:
        raise ValueError('retarget requires the original active equivalent link')
    if any(l['claim_id']==cid for p in packet['candidates'] if p['viewpoint']['viewpoint_id']==target for l in p['links']):
        raise ValueError('another target membership exists; merge is not authorized')
    if not any(l['claim_id']!=cid and l['link_type'] in {'equivalent_full','equivalent_component'}
               for l in packet['old_target']['links']):
        raise ValueError('retarget would orphan the old viewpoint')
    if old.get('supporting_relation_ids'):
        raise ValueError('retarget of relation-bound support needs explicit relation scope')
    head=next(p['viewpoint'] for p in packet['candidates'] if p['viewpoint']['viewpoint_id']==target)
    new=deepcopy(old)
    new.update(revision=old['revision']+1,viewpoint_id=target,
               validated_against_viewpoint_revision_id=head['current_revision_id'],
               link_type='supports',component_locator=None)
    return new


def review_packet(material, raw):
    return {'schema_version':SCHEMA,'materials':material,'proposal':raw['response'],
            'after_link':postimage(material['comparison'],raw['response']),
            'provenance_receipt':{'decision_id':'fresh decision after approval, original ID is display only',
                                  'changed_records':['one existing link','one new identity candidate','one new identity decision'],
                                  'claim_evidence_source_cvp_text_changes':0}}


def approved(raw, cid):
    result=Review.model_validate(raw['response'])
    if len(result.verdicts)!=1 or result.verdicts[0].claim_id!=cid or not result.verdicts[0].approved or result.verdicts[0].problems:
        raise Stop('retarget postimage independently rejected; no second correction or write')


def saved_raw(root,stage,packet):
    """Same raw/reservation/codec identity as Runner, never issue a call."""
    import json
    packet=json.loads(canonical_json(packet))
    raw=read(root/(stage+'.raw.json'));obj=response_adapter(model_adapter(stage),raw)
    proof=obj.request_encoding_proof(packet)
    expected={'model_id':obj.model_id,'backend':obj.backend,'prompt_sha256':obj.prompt_sha256,
              'generation_config_sha256':obj.generation_config_sha256,
              'request_payload_sha256':sha256_json(packet),'request_encoding_proof':proof,
              'request_bytes':obj.request_bytes(packet),'stage':stage,'automatic_retry':False}
    limit,budget=request_limit(obj,root/(stage+'.raw.json'),saved=raw)
    if budget is not None:expected['request_budget']=budget
    if (expected!=body(read(root/(stage+'.reservation.json')))
            or any(raw.get(k)!=v for k,v in expected.items()) or expected['request_bytes']>limit
            or not proof.get('exact_roundtrip_verified')
            or raw['response_sha256']!=sha256_json(raw['response'])
            or raw.get('provider_raw_sha256')!=sha256_json(raw.get('provider_raw'))
            or datetime.fromisoformat(raw['completed_at']).utcoffset() is None):
        raise ValueError('actual retarget request/raw/reservation/model identity drift')
    identity=raw.get('runtime_identity')
    if identity is not None and (identity.get('model_id')!=obj.model_id or identity.get('backend')!=obj.backend
            or identity.get('reasoning_effort')!='high' or not identity.get('file_sha256')):
        raise ValueError('retarget actual subscription identity drift')
    bound_model_response(obj,packet,raw['response'],root/(stage+'.raw.json'),persist=False)
    return raw


def fresh_identity_records(states, old, link, signature, proposal, review, *, seed, policy):
    """Reuse exact reviewed identity provenance for a new or retargeted link.

    This derives identifiers only; it does not approve the supplied postimage.
    Each caller must independently validate its own content/write authority.
    """
    prior=states[content.key('viewpoint_identity_decisions',old['decision_id'])]
    candidate=deepcopy(states[content.key('viewpoint_identity_candidates',prior['identity_candidate_id'])])
    candidate.update(revision=1,candidate_claim_ids=[old['claim_id']],candidate_viewpoint_ids=[link['viewpoint_id']],
                     proposed_action='match_existing',seed_relation_ids=[],blocker_codes=[],
                     generation_fingerprint=seed,proposed_proposition_signature=signature)
    identity={'claims':candidate['candidate_claim_ids'],'viewpoints':candidate['candidate_viewpoint_ids'],
              'relations':[],'action':'match_existing','blockers':[],
              'coverage_snapshot_id':candidate.get('coverage_snapshot_id'),'generation_fingerprint':seed}
    if candidate.get('scope_manifest_sha256'):identity['scope_manifest_sha256']=candidate['scope_manifest_sha256']
    candidate['identity_candidate_id']='VIC-'+sha256_json(identity)[:20]
    decision=deepcopy(prior)
    decision.update(revision=1,identity_decision_id='VID-'+seed[:20],identity_candidate_id=candidate['identity_candidate_id'],
                    input_sha256=seed,decision='match_existing',resolved_viewpoint_id=link['viewpoint_id'],
                    claim_link_decisions=[{'claim_id':old['claim_id'],'link_type':link['link_type']}],proposition_unit_link_decisions=[],
                    review_artifact_sha256=review['artifact_sha256'],
                    semantic_call_artifact_sha256s=sorted([proposal['artifact_sha256'],review['artifact_sha256']]),
                    reviewer_model_ids=['claude-opus-5-5','gpt-6.1-sol'],reviewer_kind='system',
                    reviewer_id=policy,approval_basis='dual_model_consensus',review_status='system_approved',
                    created_at=review['completed_at'],policy_version=policy,
                    reason=proposal['response']['selection']['reason'])
    return candidate, decision


def compile_package(material,proposal,review,root):
    packet=material['comparison'];old=packet['before_link'];link=postimage(packet,proposal['response'])
    approved(review,old['claim_id'])
    states=content.index(packet['context_records'])
    seed=sha256_json({'contract':SCHEMA,'material':sha256_json(material),
                     'proposal':proposal['artifact_sha256'],'review':review['artifact_sha256']})
    target=next(p for p in packet['candidates'] if p['viewpoint']['viewpoint_id']==link['viewpoint_id'])
    candidate, decision = fresh_identity_records(states, old, link,
        target['current_revision']['proposition_signature'], proposal, review, seed=seed, policy=SCHEMA)
    link['decision_id']=decision['identity_decision_id']
    return {'package_id':'CVP-RETARGET-'+seed[:24],'viewpoint_claim_links':[link],
            'viewpoint_identity_candidates':[candidate],'viewpoint_identity_decisions':[decision],
            'source_repair_application':{'schema_version':SCHEMA,'run_root':str(Path(root).resolve()),
                                       'materials_sha256':sha256_json(material)}}


def validate_package(package):
    from backend.pipeline.cvp_sql_commit import check_binding
    app=package['source_repair_application'];root=Path(app['run_root'])
    if app['schema_version']!=SCHEMA or not root.is_absolute():raise ValueError('retarget authority missing')
    material=body(read(root/'materials.json'))
    if app['materials_sha256']!=sha256_json(material):raise ValueError('retarget materials drift')
    check_binding(material['authorization'])
    validate_direction(read_artifact(material['authorization']['path']),material)
    for report in material['original_reports']:check_binding(report)
    for source in material['comparison']['sources']:check_binding({'path':source['path'],'file_sha256':source['file_sha256']})
    proposal=saved_raw(root,'proposal',material)
    review=saved_raw(root,'initial_review',review_packet(material,proposal))
    if package!=compile_package(material,proposal,review,root):raise ValueError('retarget package differs from exact approved three-record delta')


def prepare_material(store,run_id,authorization):
    """The same read-only compiler is used by preflight and execution."""
    from backend.pipeline.cvp_sql_commit import check_binding, pins, pin_rows
    check_binding(authorization);direction=read_artifact(authorization['path'])
    group=store.get_cvp_group(run_id)
    packet=selection.build_packet(store,link_id=direction['link_id'],candidate_viewpoint_ids=direction['candidate_viewpoint_ids'])
    selection.validate_group_scope(group,packet)
    old=packet['before_link']
    records=store.read_affected_context([('viewpoint_claim_links',direction['link_id'])])
    records.update(store.read_cvp_context([old['claim_id']],viewpoint_ids=[old['viewpoint_id'],*direction['candidate_viewpoint_ids']]))
    indexed={k:r['payload'] for k,r in records.items() if not r.get('retired')}
    if any(indexed.get((r['collection'],r['object_id']))!=r['payload'] for r in packet['context_records']):
        raise ValueError('retarget graph changed between scoped reads')
    material={'run_id':run_id,'campaign_sha256':group['campaign_sha256'],'comparison':packet,
              'authorization':authorization,'original_reports':direction['original_reports'],'read_pins':pin_rows(pins(records)),
              'affected_records':[{'collection':c,'object_id':i,'payload':p} for (c,i),p in sorted(indexed.items())]}
    validate_direction(direction,material)
    for b in direction['original_reports']:check_binding(b)
    material['original_model_responses']=[{'binding':b,'model_id':r['model_id'],'backend':r['backend'],
        'response':r['response'],'response_sha256':r['response_sha256']}
        for b in direction['original_reports'] for r in [read_artifact(b['path'])] if 'response' in r and 'model_id' in r]
    import json
    return json.loads(canonical_json(material))


def execute(store,run_id,*,authorization,root,ports):
    from backend.pipeline.cvp_sql_actions import initialize_subscription_runtime,resume_bound_action
    from backend.pipeline.cvp_sql_commit import binding, check_binding, pins, pin_rows, decoded_pins, commit_package, verified_group_backup
    direction=read_artifact(authorization['path']);check_binding(authorization)
    old=store.get_record('viewpoint_claim_links',direction['link_id'])
    # The original revision, not a changed direction/root, owns the once budget.
    action_id='CVPA-'+sha256_json({'kind':SCHEMA,'link_id':direction['link_id'],
                                 'before_link_sha256':direction['before_link_sha256']})
    root=Path(root)/action_id
    store.bind_cvp_execution_root(run_id,identity=action_id,root=root)
    previous=store.get_cvp_action(run_id,action_id)
    if previous and previous['materials']['authorization']!=authorization:
        raise ValueError('retarget direction changed; no replay or new budget')
    resumed=resume_bound_action(store,run_id,action_id)
    if resumed:return resumed
    group=store.get_cvp_group(run_id)
    if previous:
        material=previous['materials']
        if material['authorization']!=authorization:raise ValueError('retarget authorization/root change cannot reset budget')
    else:
        material=prepare_material(store,run_id,authorization)
    validate_direction(direction,material)
    for report in material['original_reports']:check_binding(report)
    import json
    material=json.loads(canonical_json(material))
    action=store.prepare_cvp_action(run_id,action_id=action_id,kind='owner_link_retarget',authorization=authorization,materials=material)
    write(root/'materials.json',material)
    read_pins=decoded_pins(material['read_pins'])
    def guard():
        for source in material['comparison']['sources']:check_binding({'path':source['path'],'file_sha256':source['file_sha256']})
        with store.connect() as conn,conn.cursor() as cursor:store._assert_transaction_read_pins(cursor,read_pins)
    guard();initialize_subscription_runtime(ports)
    runner=Runner(root,ports);runner.root=root
    proposal=runner.call('proposal',material)
    proposed=review_packet(material,proposal);guard()
    review=runner.call('initial_review',proposed);approved(review,old['claim_id']);guard()
    package=compile_package(material,proposal,review,root);validate_package(package)
    plan=store.plan_package(package,source_kind=SCHEMA,transactional=True)
    from backend.pipeline.owner_approved_link_correction import assert_plan
    from dataclasses import asdict
    assert_plan(asdict(plan),package,content.index(material['comparison']['context_records']))
    evidence=group['reviewed']['evidence']
    views=sorted(set(evidence['viewpoint_ids'])|set(direction['candidate_viewpoint_ids']))
    return commit_package(store,run_id,package=package,
        approval={'gpt':binding(root/'proposal.raw.json'),'opus':binding(root/'initial_review.raw.json')},
        authorization=authorization,backup=verified_group_backup(root,package,ports),
        sources=[{'path':s['path'],'file_sha256':s['file_sha256']} for s in material['comparison']['sources']],
        read_pins=read_pins,reason='owner-directed exact existing-CVP link retarget; original semantic rejection retained',
        actor='owner-authorized-pipeline',source_kind=SCHEMA,viewpoint_ids=views,
        disposition=evidence.get('disposition'),action=action)
