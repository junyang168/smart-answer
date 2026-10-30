"""Explicit owner permission can continue a lost call without erasing it."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from backend.pipeline import claim_management as cm
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.cvp_sql_flow import binding
from backend.tests.test_claim_management_postgres import action,postgres_store,_temporary_postgres
from backend.tests.test_claim_management_execution_postgres import operation_request,seed_claims,SyntheticPorts,rejected


def authorize(request,policy,path,extension=None):
    path.write_text(policy.model_dump_json())
    request.policy=cm.source.FileBinding.model_validate(binding(path))
    auth_path=Path(request.authorization.path);auth=json.loads(auth_path.read_text())
    auth.update(request=request.model_dump(mode='json',exclude={'authorization','policy'}),policy=request.policy.model_dump(mode='json'),required_maintenance_keys=[])
    if extension is not None:auth['owner_call_extension']=extension
    auth_path.write_text(json.dumps(auth));request.authorization=cm.source.FileBinding.model_validate(binding(auth_path))


@pytest.fixture
def lost_update(action,monkeypatch,request):
    lost_role = getattr(request, "param", "gpt")
    store=action[0];seed_claims(store,['C'])
    # Transaction consumers are protected but are outside the Claim model's
    # content denominator. The real 2021 input has both scopes.
    from backend.api.canonical_repository.postgres_store import record_content_sha
    payload={'viewpoint_id':'TRANSACTION-ONLY','revision':1}
    with store.connect() as conn:
        conn.execute("INSERT INTO wang_knowledge.objects (collection,object_id,revision,content_sha256,payload) VALUES ('canonical_viewpoints','TRANSACTION-ONLY',1,%s,%s::jsonb)",
            (record_content_sha(payload),json.dumps(payload)))
    read_context=store.read_claim_context
    def complete_context(*args,**kwargs):
        records=read_context(*args,**kwargs)
        records['canonical_viewpoints','TRANSACTION-ONLY']=store.get_record_state('canonical_viewpoints','TRANSACTION-ONLY')
        return records
    monkeypatch.setattr(store,'read_claim_context',complete_context)
    request,root=operation_request(action,'Update',['C'])
    policy=cm.Policy.model_validate_json((Path(cm.__file__).parent/'policies/wang_claim_management_joint_update_v2.json').read_text())
    authorize(request,policy,root.with_suffix('.policy.json'))
    def fail(packet):raise RuntimeError('synthetic lost reply')
    def review(packet):
        value=rejected(packet)
        value['findings'][0].update(affected_target_keys=['claims/C'],affected_write_keys=[],impact_reason='target wording')
        return value
    ports=SyntheticPorts(store,root,proposal=fail,opus=review) if lost_role=='gpt' else SyntheticPorts(store,root,opus=fail)
    assert execute_claim_management(store,request,root=root,ports=ports)['reason']=='model_failed'
    assert execute_claim_management(store,request,root=root,ports=ports)['reason']=='call_uncertain'
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))
    successor,next_root=operation_request(action,'Update',['C']);successor.predecessor_action_id=request.action_id
    successor.permissions=request.permissions
    new=cm.Policy.model_validate_json((Path(cm.__file__).parent/'policies/wang_claim_management_joint_update_v3.json').read_text())
    new.initial_candidate='proposal';new.budget.gpt_calls=3;new.budget.opus_calls=3;new.budget.max_revisions=2
    metadata={k:v for k,v in parent.items() if k not in {'run_id','status','error'}}
    extension={'predecessor_action_id':request.action_id,'prior_metadata_sha256':cm.sha256_json(metadata),
        'budget':new.budget.model_dump(mode='json'),'authorization_quote':'允许你调用多次'}
    path=next_root.with_suffix('.policy.json');authorize(successor,new,path,extension)
    return store,request,root,parent,successor,next_root,new,extension,path


def decision(fid):
    return {'finding_id':fid,'disposition':'resolved','reason':'complete source settles the original question',
        'citations':[{'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}],
        'affected_target_keys':['claims/C'],'affected_write_keys':[],'impact_reason':'original target inspected'}


@pytest.mark.parametrize("lost_update", ["gpt", "opus"], indirect=True)
def test_owner_extension_reuses_findings_with_compact_output_and_preserves_unknown_call(lost_update):
    store,request,root,old,successor,next_root,policy,extension,path=lost_update
    original=store.get_record_state('claims','C')
    def propose(packet):
        assert packet['previous_candidate']
        assert packet['continuation']['preserved_unknown_calls']
        return {'changes':[],'findings':[],'finding_resolutions':[decision(fid) for fid in packet['unresolved']]}
    def review(packet):return {'complete_review':True,'findings':[],'prior_findings':[decision(fid) for fid in packet['unresolved']]}
    ports=SyntheticPorts(store,next_root,proposal=propose,opus=review)
    result=execute_claim_management(store,successor,root=next_root,ports=ports)
    assert result['status']=='completed',result
    assert ports.calls==['gpt','opus']
    assert store.get_record_state('claims','C')==original and store.claim_management_qualification('C')['ready']
    current=store.get_cvp_group(old['run_id'])
    for key,slot in old['call_reservations'].items():
        if slot['state']!='planned':assert current['call_reservations'][key]==slot
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(successor.action_id))
    assert parent['continuation']['owner_call_extension']==extension
    assert execute_claim_management(store,successor,root=next_root,ports=ports)==result
    assert ports.calls==['gpt','opus']
    third,third_root=operation_request((store,request,None,None,next_root,None),'Update',['C'])
    third.predecessor_action_id=request.action_id;third.permissions=request.permissions
    authorize(third,policy,third_root.with_suffix('.policy.json'),extension)
    with pytest.raises(ValueError):execute_claim_management(store,third,root=third_root,ports=ports)
    assert ports.calls==['gpt','opus']


@pytest.mark.parametrize("lost_update", ["gpt", "opus"], indirect=True)
@pytest.mark.parametrize('change',['missing_permission','metadata','budget','quote','predecessor','widen_permissions',
    'missing_intent','changed_intent','returned_raw','returned_exit','wrong_initial'])
def test_extension_never_infers_owner_permission_or_discards_results(lost_update,change):
    store,request,root,old,successor,next_root,policy,extension,path=lost_update
    metadata={k:v for k,v in old.items() if k not in {'run_id','status','error'}}
    slot=next(v for v in old['call_reservations'].values() if v['state']=='started')
    role=slot['role']
    stage=root/slot['round_id']
    if change=='missing_permission':
        auth_path=Path(successor.authorization.path);auth=json.loads(auth_path.read_text());auth.pop('owner_call_extension')
        auth_path.write_text(json.dumps(auth));successor.authorization=cm.source.FileBinding.model_validate(binding(auth_path))
    else:
        if change=='metadata':extension['prior_metadata_sha256']='0'*64
        elif change=='budget':extension['budget']['gpt_calls']=1
        elif change=='quote':extension['authorization_quote']=''
        elif change=='predecessor':extension['predecessor_action_id']='different'
        elif change=='widen_permissions':successor.permissions[0].fields.append('claim_type')
        elif change=='missing_intent':(stage/(role+'.reservation.json')).unlink()
        elif change=='changed_intent':(stage/(role+'.reservation.json')).write_text('{}')
        elif change=='returned_raw':(stage/(role+'.raw.json')).write_text('{}')
        elif change=='returned_exit':(stage/(role+'.transport')).mkdir();(stage/(role+'.transport/exit.json')).write_text('{}')
        elif change=='wrong_initial':policy.initial_candidate='existing'
        authorize(successor,policy,path,extension)
    with pytest.raises(ValueError):cm.continuation_budget(metadata,successor,policy)
    assert store.get_cvp_group(old['run_id'])==old


@pytest.mark.parametrize('change',['source','target','new_scope','transaction_consumer'])
def test_owner_extension_cannot_silently_cross_changed_sources_or_scope(lost_update,change):
    store,request,root,old,successor,next_root,policy,extension,path=lost_update
    if change=='source':
        source=Path(request.source_bindings['S'].path);source.write_text(source.read_text()+' ')
    elif change=='target':
        payload=deepcopy(store.get_record_state('claims','C')['payload']);payload['statement']='changed while paused'
        store.apply_plan(store.plan_package({'package_id':'target-drift','claims':[payload]}))
    elif change=='new_scope':
        seed_claims(store,['D'])
        store.apply_plan(store.plan_package({'package_id':'scope-drift','claim_relations':[
            {'claim_relation_id':'NEW','from_id':'D','to_id':'C','relation_type':'supports'}]}))
    else:
        with store.connect() as conn:
            conn.execute("UPDATE wang_knowledge.objects SET revision=2 WHERE collection='canonical_viewpoints' AND object_id='TRANSACTION-ONLY'")
    from backend.api.canonical_repository.postgres_store import ChangeSetConflict
    with pytest.raises((ValueError,ChangeSetConflict)):
        execute_claim_management(store,successor,root=next_root,ports=SyntheticPorts(store,next_root))
    assert store.get_cvp_group(old['run_id'])==old
    assert store.get_cvp_group('CMA-'+cm.sha256_json(successor.action_id)) is None

from backend.tests.test_claim_joint_update import shared,bind,joint_pass,joint_reject,resolve,update


@pytest.fixture(params=['remaining_findings','qualification_refresh'])
def owner_review_follow_up(shared,request):
    store,old,root,tmp=shared
    bind(old,tmp,required=[],initial='proposal')
    basis=request.param
    def propose(packet):
        candidate=cm.Candidate.model_validate(update(packet))
        candidate.finding_resolutions=resolve(packet,disposition='persistent')
        return candidate.model_dump(mode='json')
    def review(packet):
        if not packet['round_number'] or basis=='remaining_findings':
            report=joint_reject(packet,'claims/C',affected=[])
            if packet['round_number']:
                report['findings'][0]['component']='new-component'
            return report
        report=joint_pass(packet)
        for decision in report['prior_findings']:
            decision.update(disposition='persistent',affected_target_keys=[],affected_write_keys=[])
        for verdict in report['verdicts']:
            if verdict['record_key']=='claims/C':verdict.update(approved=False,problems=['context issue retained'])
        return report
    ports=SyntheticPorts(store,tmp,proposal=propose,opus=review)
    result=execute_claim_management(store,old,root=root,ports=ports)
    assert result['status']==('completed' if basis=='qualification_refresh' else 'paused'),result
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(old.action_id))
    if basis=='qualification_refresh':
        # A legitimate later edit changes shared input, not the original targets.
        payload=deepcopy(store.get_record_state('evidence_steps','E')['payload']);payload['statement']+=' 后续补充'
        store.apply_plan(store.plan_package({'package_id':'later-evidence','evidence_steps':[payload]}))
    successor,next_root=operation_request((store,old,None,None,tmp,None),'Update',['A','B'])
    successor.predecessor_action_id=old.action_id;successor.permissions=old.permissions
    policy=cm.Policy.model_validate_json((Path(cm.__file__).parent/'policies/wang_claim_management_joint_update_v3.json').read_text())
    policy.initial_candidate='proposal'
    records=store.read_claim_context([cm.source.split_key(k) for k in successor.target_keys]+[(p.collection,p.object_id) for p in successor.permissions])
    before={cm.source.key(*k):cm.model_state(v) for k,v in records.items()}
    metadata={k:v for k,v in parent.items() if k not in {'run_id','status','error'}}
    extension={'predecessor_action_id':old.action_id,'prior_metadata_sha256':cm.sha256_json(metadata),
        'budget':policy.budget.model_dump(mode='json'),'authorization_quote':'现在解决那三个问题；允许你调用多次',
        'basis':basis,'current_objects_sha256':cm.sha256_json(before),'related_action_metadata_sha256':{}}
    path=next_root.with_suffix('.policy.json');authorize(successor,policy,path,extension)
    return store,old,parent,successor,next_root,policy,extension,path,basis


def test_owner_follow_up_carries_final_findings_and_never_replays_old_writes(owner_review_follow_up):
    store,old,parent,successor,root,policy,extension,path,basis=owner_review_follow_up
    before=store.get_record_state('evidence_steps','E')
    def propose(packet):
        assert packet['previous_candidate'] and packet['unresolved']
        if basis=='qualification_refresh':
            assert not packet['previous_candidate']['records']
        else:
            assert packet['previous_candidate']['records']
        changes=[]
        if basis=='remaining_findings':
            for row in packet['previous_candidate']['records']:
                key=row['collection']+'/'+row['object_id'];fields={k:v for k,v in row['payload'].items() if packet['before'][key]['payload'].get(k)!=v}
                if fields:changes.append({'record_key':key,'fields_json':json.dumps(fields),'reason':'preserve preceding correction','citations':decision('unused')['citations']})
        return {'changes':changes,'findings':[],'finding_resolutions':[decision(fid) for fid in packet['unresolved']]}
    def review(packet):return {'complete_review':True,'findings':[],'prior_findings':[decision(fid) for fid in packet['unresolved']]}
    # Use actual target identifiers for this fixture.
    def adapt(fn):
        def run(packet):
            value=fn(packet)
            for item in value.get('finding_resolutions',value.get('prior_findings',[])):
                item['affected_target_keys']=[]
            return value
        return run
    ports=SyntheticPorts(store,root,proposal=adapt(propose),opus=adapt(review))
    result=execute_claim_management(store,successor,root=root,ports=ports)
    assert result['status']=='completed',result
    assert ports.calls==['gpt','opus']
    # No resubmission of the prior candidate: E changes only if it was uncommitted.
    after=store.get_record_state('evidence_steps','E')
    if basis=='qualification_refresh':assert after==before
    else:assert after['revision']==before['revision']+1 and after['payload']['statement']=='甲是假设，不是断言。'
    assert all(store.claim_management_qualification(cid)['ready'] for cid in ['A','B'])
    assert store.get_cvp_group(parent['run_id'])['continued_by']=='CMA-'+cm.sha256_json(successor.action_id)


@pytest.mark.parametrize('change',['scope_binding','metadata','basis','peer'])
def test_explicit_follow_up_refuses_unbound_history_and_current_scope(owner_review_follow_up,change):
    store,old,parent,successor,root,policy,extension,path,basis=owner_review_follow_up
    if change=='scope_binding':extension['current_objects_sha256']='0'*64
    elif change=='metadata':extension['prior_metadata_sha256']='0'*64
    elif change=='basis':extension['basis']='ordinary_continue'
    else:extension['related_action_metadata_sha256']={'CMA-missing':'0'*64}
    authorize(successor,policy,path,extension)
    from backend.api.canonical_repository.postgres_store import ChangeSetConflict
    with pytest.raises((ValueError,ChangeSetConflict)):
        execute_claim_management(store,successor,root=root,ports=SyntheticPorts(store,root))
    assert store.get_cvp_group(parent['run_id'])==parent


@pytest.mark.parametrize('tamper',[False,True])
def test_owner_follow_up_revision_uses_immediate_candidate(owner_review_follow_up,monkeypatch,tamper):
    store,old,parent,successor,root,policy,extension,path,basis=owner_review_follow_up
    policy.budget.max_revisions=2;extension['budget']=policy.budget.model_dump(mode='json')
    authorize(successor,policy,path,extension)
    def decisions(packet):
        values=[decision(fid) for fid in packet['unresolved']]
        for item in values:item['affected_target_keys']=[]
        return values
    def propose(packet):return {'changes':[],'findings':[],'finding_resolutions':decisions(packet)}
    def review(packet):
        findings=[]
        if not packet['round_number']:
            finding=deepcopy(next(iter(packet['unresolved'].values())))
            finding.update(target_key='claims/C',component='new follow-up context',affected_target_keys=[],affected_write_keys=[],impact_reason='new unrelated context reviewed')
            findings=[finding]
        return {'complete_review':True,'findings':findings,'prior_findings':decisions(packet)}
    ports=SyntheticPorts(store,root,proposal=propose,opus=review)
    prepare=store.prepare_claim_management_round
    def guarded(*args,**kwargs):
        if kwargs['round_number'] and tamper:kwargs['materials']['previous_candidate']={}
        return prepare(*args,**kwargs)
    monkeypatch.setattr(store,'prepare_claim_management_round',guarded)
    result=execute_claim_management(store,successor,root=root,ports=ports)
    if tamper:
        assert result['reason']=='engineering_required' and ports.calls==['gpt','opus']
    else:
        assert result['status']=='completed',result
        assert ports.calls==['gpt','opus','gpt','opus']


@pytest.mark.parametrize('target_blocker',[False,True])
def test_readonly_refresh_abandons_only_uncommitted_optional_repairs(owner_review_follow_up,target_blocker):
    store,old,parent,successor,root,policy,extension,path,basis=owner_review_follow_up
    # A real two-round proposer/reviewer disagreement about an optional write.
    policy.budget.max_revisions=2;extension['budget']=policy.budget.model_dump(mode='json');authorize(successor,policy,path,extension)
    def decisions(packet,disposition='resolved',writes=()):
        return [dict(decision(fid),disposition=disposition,affected_target_keys=[],affected_write_keys=list(writes)) for fid in packet['unresolved']]
    def propose(packet):
        changes=[]
        if packet['round_number']:
            changes=[{'record_key':'evidence_steps/E','fields_json':json.dumps({'statement':'proposed optional repair'}),'reason':'synthetic proposal','citations':decision('unused')['citations']}]
        return {'changes':changes,'findings':[],'finding_resolutions':decisions(packet)}
    def review(packet):
        if not packet['round_number']:
            finding=deepcopy(next(iter(packet['unresolved'].values())));finding.update(target_key='evidence_steps/E',component='optional proposed repair',affected_target_keys=[],affected_write_keys=[],impact_reason='independent optional issue')
            other=deepcopy(finding);other['component']='second optional repair'
            return {'complete_review':True,'findings':[finding,other],'prior_findings':decisions(packet)}
        values=decisions(packet,'persistent',['evidence_steps/E']);values[0]['disposition']='resolved'
        return {'complete_review':True,'findings':[],'prior_findings':values}
    ports=SyntheticPorts(store,root,proposal=propose,opus=review);before=store.get_record_state('evidence_steps','E')
    assert execute_claim_management(store,successor,root=root,ports=ports)['reason']=='disagreement'
    assert store.get_record_state('evidence_steps','E')==before
    prior=store.get_cvp_group('CMA-'+cm.sha256_json(successor.action_id));metadata={k:v for k,v in prior.items() if k not in {'run_id','status','error'}}
    if target_blocker:
        for finding in metadata['findings'].values():finding['affected_target_keys']=['claims/A']
    readonly,next_root=operation_request((store,successor,None,None,root.parent,None),'Update',['A','B']);readonly.predecessor_action_id=successor.action_id;readonly.permissions=[]
    new=policy.model_copy(deep=True);new.budget.gpt_calls=1;new.budget.opus_calls=1;new.budget.max_revisions=1
    records=store.read_claim_context([cm.source.split_key(k) for k in readonly.target_keys]);actual={cm.source.key(*k):cm.model_state(v) for k,v in records.items()}
    ext={'predecessor_action_id':successor.action_id,'prior_metadata_sha256':cm.sha256_json(metadata),'budget':new.budget.model_dump(mode='json'),
        'authorization_quote':'finish the original Claims; leave optional repairs uncommitted','basis':'qualification_refresh','current_objects_sha256':cm.sha256_json(actual),'related_action_metadata_sha256':{}}
    authorize(readonly,new,next_root.with_suffix('.policy.json'),ext)
    if target_blocker:
        with pytest.raises(ValueError):cm.continuation_budget(metadata,readonly,new)
        return
    def no_write(packet):
        assert not packet['request']['permissions'] and not packet['previous_candidate']['records']
        assert len(packet['unresolved'])==2, 'also restore issues resolved only in the rejected uncommitted candidate'
        return {'changes':[],'findings':[],'finding_resolutions':decisions(packet,'persistent')}
    final=SyntheticPorts(store,next_root,proposal=no_write,opus=lambda p:{'complete_review':True,'findings':[],'prior_findings':decisions(p,'persistent')})
    result=execute_claim_management(store,readonly,root=next_root,ports=final)
    assert result['status']=='completed',result
    assert final.calls==['gpt','opus'] and store.get_record_state('evidence_steps','E')==before
    assert all(store.claim_management_qualification(cid)['ready'] for cid in ['A','B'])
    assert store.get_cvp_group(prior['run_id'])['call_reservations']==prior['call_reservations']


@pytest.mark.parametrize('change',['transaction_only','target','evidence'])
def test_current_bound_remaining_findings_refreshes_only_transaction_consumers(owner_review_follow_up,monkeypatch,change):
    store,old,parent,successor,root,policy,extension,path,basis=owner_review_follow_up
    if basis!='remaining_findings':
        pytest.skip('only uncommitted remaining-findings continuation')
    # A consumer appearing while paused changes transaction pins but must not
    # widen the Claim model's content or discard the uncommitted correction.
    from backend.api.canonical_repository.postgres_store import record_content_sha,ChangeSetConflict
    payload={'viewpoint_id':'LATER-CONSUMER','revision':1}
    with store.connect() as conn:
        conn.execute("INSERT INTO wang_knowledge.objects (collection,object_id,revision,content_sha256,payload) VALUES ('canonical_viewpoints','LATER-CONSUMER',1,%s,%s::jsonb)",
            (record_content_sha(payload),json.dumps(payload)))
    read_context=store.read_claim_context
    def complete_context(*args,**kwargs):
        records=read_context(*args,**kwargs)
        records['canonical_viewpoints','LATER-CONSUMER']=store.get_record_state('canonical_viewpoints','LATER-CONSUMER')
        return records
    monkeypatch.setattr(store,'read_claim_context',complete_context)
    if change!='transaction_only':
        col,oid=('claims','A') if change=='target' else ('evidence_steps','E')
        edited=deepcopy(store.get_record_state(col,oid)['payload']);edited['statement']='changed while paused'
        store.apply_plan(store.plan_package({'package_id':'semantic-drift','%s'%col:[edited]}))
    records=store.read_claim_context([cm.source.split_key(k) for k in successor.target_keys])
    before={cm.source.key(*k):cm.model_state(v) for k,v in records.items()}
    extension['current_objects_sha256']=cm.sha256_json(before)
    authorize(successor,policy,path,extension)
    def decisions(packet):return [dict(decision(fid),affected_target_keys=[]) for fid in packet['unresolved']]
    def propose(packet):
        assert packet['previous_candidate']['records']
        changes=[]
        for row in packet['previous_candidate']['records']:
            key=row['collection']+'/'+row['object_id']
            fields={k:v for k,v in row['payload'].items() if packet['before'][key]['payload'].get(k)!=v}
            if fields:changes.append({'record_key':key,'fields_json':json.dumps(fields),'reason':'retain accepted candidate','citations':decision('unused')['citations']})
        return {'changes':changes,'findings':[],'finding_resolutions':decisions(packet)}
    ports=SyntheticPorts(store,root,proposal=propose,opus=lambda p:{'complete_review':True,'findings':[],'prior_findings':decisions(p)})
    if change!='transaction_only':
        with pytest.raises((ValueError,ChangeSetConflict)):
            execute_claim_management(store,successor,root=root,ports=ports)
        assert not ports.calls
        assert store.get_cvp_group(parent['run_id'])==parent
        return
    consumer_before=store.get_record_state('canonical_viewpoints','LATER-CONSUMER')
    result=execute_claim_management(store,successor,root=root,ports=ports)
    assert result['status']=='completed',result
    assert ports.calls==['gpt','opus']
    assert store.get_record_state('canonical_viewpoints','LATER-CONSUMER')==consumer_before
    assert store.get_record_state('evidence_steps','E')['payload']['statement']=='甲是假设，不是断言。'
    assert all(store.claim_management_qualification(cid)['ready'] for cid in ['A','B'])
    assert store.get_cvp_group(parent['run_id'])['call_reservations']==parent['call_reservations']
