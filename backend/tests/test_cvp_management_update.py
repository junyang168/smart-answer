"""Update input uses the normal maintain proposal/review/SQL lifecycle."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from backend.api.canonical_repository import cvp_management as domain
from backend.api.canonical_repository.cvp_recall import seal, work_groups
from backend.api.canonical_repository.postgres_store import canonical_json, record_content_sha
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline import cvp_management_runner as runner
from backend.pipeline.cvp_sql_commit import binding
from backend.pipeline.cvp_update_preparation import prepare_update_input, update_permissions
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable
from test_cvp_management_runner import setup, postgres_store, _temporary_postgres
from test_cvp_management_maintenance import _initial, _change
from test_cvp_management_models import envelope


def _request(store, initial, root, views, *, replacement_ids=(), name='update'):
    ids=[v['viewpoint_id'] for v in views]
    inp=prepare_update_input(store,initial['input'],viewpoint_ids=ids,replacement_claim_ids=replacement_ids)
    request={**deepcopy(initial),'action_id':name,'mode':'maintain','input':inp,
        'maintenance_viewpoint_ids':ids,'permissions':update_permissions(store,ids),
        'creation_slots':{'viewpoint_revisions':2,'viewpoint_claim_links':4}}
    auth=root.parent/(name+'-authorization.json')
    write_immutable(auth,{'schema_version':'wang_cvp_management_authorization_v1','authorized_by':'owner',
        **{k:request[k] for k in ('action_id','mode','maintenance_viewpoint_ids','permissions','creation_slots','budget')},
        'input_sha256':inp['artifact_sha256']})
    request['authorization']=binding(auth)
    return request


def _member_candidate(packet, *, summary=None):
    view=packet['material']['viewpoints'][0]['cvp_ref']
    links={r['claim_ref']:r['link_ref'] for r in packet['material']['links']}
    value={'changes':[], 'component_dispositions':[{'component_ref':f'k{i}',
        'disposition':'existing_coverage','target_ref':view,'link_ref':links[row['claim_ref']],
        'reason':'按当前完整成员与来源复核。'} for i,row in enumerate(packet['input']['claims'],1)],
        'member_dispositions':[{'member_ref':ref,'disposition':'retained','reason':'保留原作用，重核当前依据。'}
                               for ref in links.values()]}
    if summary:value['summaries']=[{'ref':view,'summary':summary}]
    return envelope('candidate',candidate=value)


def _proposal_behavior(callback):
    def behavior(packet):
        if packet.get('schema_version','').endswith('_proposal_packet'):
            return callback(packet)
    return behavior


def _update_claim(store, cid, **changes):
    row=store.get_record_state('claims',cid)
    payload={**row['payload'],**changes,'revision':row['revision']+1}
    with store.connect() as conn:
        conn.execute('UPDATE wang_knowledge.objects SET payload=%s::jsonb,revision=%s,content_sha256=%s,review_status=%s,visibility=%s WHERE collection=%s AND object_id=%s',
            (canonical_json(payload),payload['revision'],record_content_sha(payload),payload['review_status'],payload['visibility'],'claims',cid))
        q=conn.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s',('claim-round-'+cid,)).fetchone()[0]
        q['audited_pins']=[{'collection':'claims','object_id':cid,'pin':[payload['revision'],record_content_sha(payload),False]}]
        conn.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb WHERE run_id=%s',
            (canonical_json(q),'claim-round-'+cid))


def _retire_claim(store,cid):
    with store.connect() as conn:
        conn.execute("UPDATE wang_knowledge.objects SET retired_at=now(),revision=revision+1 WHERE collection='claims' AND object_id=%s",(cid,))


def _run(store, request, root, clients):
    result=runner.run(store,request,root=root,execute=True,clients=clients)
    parent=store.get_cvp_group('CVPM-'+sha256_json(request['action_id']))
    if result['cvp_status']!='completed':pytest.fail(canonical_json(parent['units']))
    step=store.get_cvp_group(parent['units']['cvp-update']['completed_step'])
    return result, step


def test_update_rebinds_changed_claim_and_summary_without_new_head_and_hands_off_routes(setup):
    store,initial,root,clients,_,view,link,_=_initial(setup)
    _update_claim(store,'C1',statement='此处人子称号表明耶稣的神性。')
    request=_request(store,initial,root,[view])
    clients['gpt'].requests.clear();clients['opus'].requests.clear()
    clients['gpt'].behavior=_proposal_behavior(lambda p:_member_candidate(p,summary='此处人子称号表明耶稣的神性。'))
    result,step=_run(store,request,root.parent/'update',clients)
    assert result['update']['status']=='arguments_pending'
    assert len(result['update']['argument_jobs'])==1
    assert len(clients['gpt'].requests)==len(clients['opus'].requests)==1
    assert step['materials']['bundle']['maintenance_viewpoint_ids']==[view['viewpoint_id']]
    candidate=read_artifact(Path(step['materials']['step_root'])/'candidate.json')
    assert not any(w['collection']=='viewpoint_revisions' for w in candidate['writes'])
    saved=store.get_record_state('viewpoint_claim_links',link['viewpoint_claim_link_id'])
    assert saved['payload']['pinned_claim_revision']==2
    v=store.get_record_state('canonical_viewpoints',view['viewpoint_id'])['payload']
    assert v['current_revision_id']==view['viewpoint_revision_id'] and v['summary_basis']
    assert '不要求Claim diff' in clients['gpt'].requests[0]['system_prompt']


@pytest.mark.parametrize('role', ['supports', 'extends', 'qualifies'])
def test_update_retains_whole_claim_nonidentity_role_without_inventing_component(setup, role):
    store,initial,root,clients,_,view,link,_=_initial(setup)
    lid=link['viewpoint_claim_link_id']
    row=store.get_record_state('viewpoint_claim_links',lid)
    payload=deepcopy(row['payload']);payload['link_type']=role
    assert not payload.get('component_locator') and not payload.get('component_position_locator')
    with store.connect() as conn:
        conn.execute("UPDATE wang_knowledge.objects SET payload=%s::jsonb,content_sha256=%s WHERE collection='viewpoint_claim_links' AND object_id=%s",
            (canonical_json(payload),record_content_sha(payload),lid))
    _update_claim(store,'C1',statement='此处人子称号表明耶稣的神性。')
    request=_request(store,initial,root,[view],name='whole-'+role)
    clients['gpt'].behavior=_proposal_behavior(lambda p:_member_candidate(p,summary='此处人子称号表明耶稣的神性。'))
    result,_=_run(store,request,root.parent/('whole-'+role),clients)
    assert result['cvp_status']=='completed'
    after=store.get_record_state('viewpoint_claim_links',lid)['payload']
    assert after['link_type']==role and after['pinned_claim_revision']==2
    assert not after.get('component_locator') and not after.get('component_position_locator')


def test_zero_business_change_is_reviewed_and_still_queued_once(setup):
    store,initial,root,clients,_,view,_,_=_initial(setup)
    first=_request(store,initial,root,[view],name='summary-bind')
    clients['gpt'].behavior=_proposal_behavior(lambda p:_member_candidate(p,summary='此处人子称号表明神性。'))
    _run(store,first,root.parent/'bind',clients)
    clients['gpt'].behavior=_proposal_behavior(lambda p:_member_candidate(p))
    request=_request(store,initial,root,[view],name='no-change')
    clients['gpt'].requests.clear();clients['opus'].requests.clear()
    result,step=_run(store,request,root.parent/'noop',clients)
    candidate=read_artifact(Path(step['materials']['step_root'])/'candidate.json')
    assert candidate['writes']==[]
    assert candidate['review_sets']['objects'] # current CVP and members are reviewed even without writes
    assert result['update']['status']=='arguments_pending' and result['update']['argument_jobs']
    assert len(clients['gpt'].requests)==len(clients['opus'].requests)==1
    again=runner.run(store,request,root=root.parent/'noop',execute=True,clients=clients)
    assert again==result
    assert len(clients['gpt'].requests)==len(clients['opus'].requests)==1


def test_retired_last_claim_is_history_not_support_and_inactive_is_not_queued(setup):
    store,initial,root,clients,_,view,_,_=_initial(setup)
    _retire_claim(store,'C1')
    request=_request(store,initial,root,[view])
    assert request['input']['claim_ids']==[]
    def retire(packet):
        mat=packet['material'];v=mat['viewpoints'][0]['cvp_ref'];l=mat['links'][0]['link_ref']
        assert mat['claims'][0]['support_status']=='unavailable_history_only'
        assert not mat['claims'][0]['support_loaded'] and not mat['evidence']
        return envelope('candidate',candidate={'changes':[
            _change(v,{'identity_status':'retired'}),_change(l,{'effective_state':'retired'})],
            'member_dispositions':[{'member_ref':l,'disposition':'background','reason':'唯一当前依据已正式退役。'}]})
    clients['gpt'].behavior=_proposal_behavior(retire)
    result,step=_run(store,request,root.parent/'retire',clients)
    assert result['update']['status']=='completed'
    assert result['update']['argument_jobs']=={}
    assert result['update']['inactive_viewpoints'][view['viewpoint_id']]['current_use']=='closed_by_inactive_conclusion'
    bundle=step['materials']['bundle']
    assert any(r['collection']=='claims' and r['retired'] for r in bundle['records'])
    assert store.get_record_state('canonical_viewpoints',view['viewpoint_id'])['payload']['identity_status']=='retired'
    assert store.get_record_state('claims','C1')['retired']


def test_retired_claim_cannot_remain_active_support(setup):
    store,initial,root,clients,_,view,_,_=_initial(setup)
    _retire_claim(store,'C1')
    request=_request(store,initial,root,[view])
    clients['opus'].requests.clear()
    clients['gpt'].behavior=_proposal_behavior(lambda p:envelope('candidate',candidate={
        'member_dispositions':[{'member_ref':p['material']['links'][0]['link_ref'],
          'disposition':'retained','reason':'错误地沿用旧 summary。'}]}))
    result=runner.run(store,request,root=root.parent/'bad-retain',execute=True,clients=clients)
    assert result['status']!='completed'
    assert not any(r.get('schema_version','').endswith('_review_packet') for r in [json.loads(v['user_prompt']) for v in clients['opus'].requests[-1:]])
    assert store.get_record_state('canonical_viewpoints',view['viewpoint_id'])['payload']['identity_status']=='active'


def test_unqualified_member_is_not_silently_excluded(setup,monkeypatch):
    store,initial,root,clients,_,view,_,_=_initial(setup)
    monkeypatch.setattr(store,'claim_management_qualification',lambda cid:None)
    request=_request(store,initial,root,[view])
    assert request['input']['claim_ids']==['C1']
    clients['gpt'].requests.clear();clients['opus'].requests.clear()
    result=runner.run(store,request,root=root.parent/'blocked',execute=True,clients=clients)
    assert result['status']!='completed'
    assert not clients['gpt'].requests and not clients['opus'].requests


def test_update_cannot_omit_current_member_or_run_in_create_mode(setup):
    store,initial,root,_,_,view,_,_=_initial(setup)
    request=_request(store,initial,root,[view])
    with pytest.raises(ValueError,match='maintain viewpoint scope'):
        domain.Request.model_validate({**request,'mode':'create','maintenance_viewpoint_ids':[],'permissions':[]})
    request['input']=seal({k:v for k,v in request['input'].items() if k!='artifact_sha256'}|{
        'claim_ids':[],'grouping':{'scope_label':'update','groups':[{'group_key':'cvp-update','claim_ids':[],'rationale':'omit'}]}})
    from backend.pipeline.cvp_recall_preparation import prepare_recall
    with pytest.raises(ValueError,match='every current member'):
        prepare_recall(store,request['input'])


def test_update_whole_member_unit_does_not_inherit_generation_twenty_claim_limit():
    ids=[f'C{i}' for i in range(25)]
    grouping={'scope_label':'update','groups':[{'group_key':'one-cvp','claim_ids':ids,'rationale':'complete'}]}
    assert work_groups(grouping,ids,update=True)==[{'group_key':'one-cvp','claim_ids':ids}]
    with pytest.raises(ValueError):work_groups(grouping,ids)
    with pytest.raises(ValueError):work_groups({'groups':grouping['groups']*2},ids,update=True)


def _new_claim(store,cid,*,new_source=False):
    """Simulate a completed upstream split, including its qualified SQL pins."""
    from backend.api.canonical_repository.knowledge_models import KNOWLEDGE_COLLECTIONS
    claim=deepcopy(store.get_record_state('claims','C1')['payload'])
    claim.update(claim_id=cid,revision=1,evidence_step_ids=['E-'+cid])
    evidence=deepcopy(store.get_record_state('evidence_steps','E1')['payload'])
    evidence.update(evidence_step_id='E-'+cid,produced_claim_ids=[cid])
    source_rows=[]
    if new_source:
        doc=deepcopy(store.get_record_state('source_documents','S1')['payload'])
        path=Path(doc['source_path']).with_name('replacement-source.json')
        path.write_bytes(Path(doc['source_path']).read_bytes())
        doc.update(source_id='S2',transcript_id='replacement-source',source_path=str(path))
        fragment=deepcopy(store.get_record_state('source_fragments','F1')['payload'])
        fragment.update(fragment_id='F-'+cid,source_id='S2')
        evidence.update(source_fragment_id='F-'+cid,source_fragment_ids=[])
        source_rows=[('source_documents',doc),('source_fragments',fragment)]
    with store.connect() as conn:
        for col,payload in [*source_rows,('claims',claim),('evidence_steps',evidence)]:
            ident=KNOWLEDGE_COLLECTIONS[col][1]
            conn.execute('INSERT INTO wang_knowledge.objects(collection,object_id,revision,content_sha256,payload,review_status,visibility) VALUES(%s,%s,1,%s,%s::jsonb,%s,%s)',
                (col,payload[ident],record_content_sha(payload),canonical_json(payload),payload['review_status'],payload['visibility']))
        q=deepcopy(conn.execute("SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id='claim-round-C1'").fetchone()[0])
        q['audited_pins']=[{'collection':'claims','object_id':cid,'pin':[1,record_content_sha(claim),False]}]
        conn.execute("INSERT INTO wang_knowledge.pipeline_runs(run_id,subject_kind,subject_id,stage,trigger,status,metadata) VALUES(%s,'batch',%s,'cvp_group','cli','succeeded',%s::jsonb)",
            ('claim-round-'+cid,cid,canonical_json(q)))


@pytest.mark.parametrize('old_state,with_old_observation,new_source',[
    ('retired',False,False),('retired',True,False),('superseded',False,False),
    ('superseded',True,False),('retired',True,True)])
def test_unlinked_replacement_is_reviewed_and_added_while_old_member_is_withdrawn(setup,with_old_observation,old_state,new_source):
    store,initial,root,clients,_,view,old_link,_=_initial(setup)
    old_observation=None
    if with_old_observation:
        from backend.pipeline import viewpoint_route_cvp_worker as worker
        from test_viewpoint_observation_support import Answer,observation_proposal,approved
        worker.process_cvp_route(store=store,viewpoint_id=view['viewpoint_id'],output_dir=root.parent/'old-observation',
            proposer=Answer(observation_proposal,model_id='gpt'),reviewer=Answer(approved,model_id='opus'),
            reconsiderer=Answer({}),max_correction_rounds=4,apply=True)
        old_observation=next(iter(store.read_cvp_route_records(view['viewpoint_id']).values()))['payload']
    _new_claim(store,'C2',new_source=new_source)
    if old_state=='retired':_retire_claim(store,'C1')
    else:_update_claim(store,'C1',review_status='superseded')
    assert not store.read_cvp_links(claim_ids=['C2'])
    request=_request(store,initial,root,[view],replacement_ids=['C2'])
    def replace(packet):
        material=packet['material'];v=material['viewpoints'][0]['cvp_ref'];l=material['links'][0]['link_ref']
        c=packet['input']['claims'][0]['claim_ref']
        assert material['update']['replacement_claim_refs']==[c]
        assert len(material['claims'])==2 and material['evidence']
        return envelope('candidate',candidate={'changes':[
            _change(l,{'effective_state':'retired'}),
            _change('nl1',{'viewpoint_id':v,'claim_id':c,'link_type':'equivalent_full'})],
            'component_dispositions':[{'component_ref':'k1','disposition':'connected','target_ref':v,
                'link_ref':'nl1','reason':'当前替代 Claim 的原文表达同一判断。'}],
            'member_dispositions':[{'member_ref':l,'disposition':'background','reason':'旧 Claim 已正式退役，当前替代另建连接。'}],
            'summaries':[{'ref':v,'summary':'人子称号表明耶稣的神性。'}]})
    clients['gpt'].behavior=_proposal_behavior(replace)
    result,_=_run(store,request,root.parent/'replacement',clients)
    assert result['update']['status']=='arguments_pending'
    old=store.get_record_state('viewpoint_claim_links',old_link['viewpoint_claim_link_id'])['payload']
    assert old['claim_id']=='C1' and old['effective_state']=='retired'
    new=[r['payload'] for r in store.read_cvp_links(claim_ids=['C2']).values()]
    assert len(new)==1 and new[0]['viewpoint_id']==view['viewpoint_id']
    if with_old_observation:
        from backend.api.canonical_repository.viewpoint_route_cvp_queue import CvpRouteQueue
        from backend.pipeline.viewpoint_route_materials import prepare_cvp_route_materials,verify_cvp_route_materials
        material=prepare_cvp_route_materials(store,view['viewpoint_id'],include_context=True)
        assert {c['claim_id'] for c in material['claims']}=={'C2'}
        assert material['unusable_context_claims'][0]['claim_id']=='C1'
        verify_cvp_route_materials(store,material)
        def replacement_observation(packet):
            raw=observation_proposal(packet)
            raw['retired_observation_support_ids']=[old_observation['observation_support_id']]
            if new_source:
                raw['sources_without_observation']=[{'viewpoint_revision_id':packet['approved_viewpoint_revision_ids'][0],
                    'source_id':'S1','reason':'旧观察的 Claim 已退役，当前没有该来源的合格支持。'}]
            return raw
        job_id=next(iter(result['update']['argument_jobs']))
        queue=CvpRouteQueue(root.parent/'replacement'/'route-queue-v2')
        job=read_artifact(queue.jobs/(job_id+'.json'))
        _,epoch=queue.claim(worker_id='test',policy_sha256=job['policy_sha256'],lease_seconds=60)
        updated=worker.process_cvp_route(store=store,viewpoint_id=view['viewpoint_id'],output_dir=root.parent/'new-observation',
            proposer=Answer(replacement_observation,model_id='gpt'),reviewer=Answer(approved,model_id='opus'),
            reconsiderer=Answer({}),max_correction_rounds=4,apply=True)
        assert updated['status']=='applied_readback_verified'
        queue.finish(job_id,worker_id='test',epoch=epoch,status='complete',detail=updated['status'])
        completed=runner.run(store,request,root=root.parent/'replacement',execute=True,clients=clients)
        assert completed['update']['status']=='completed'
        assert set(completed['update']['argument_jobs'])=={job_id}
        current=store.read_cvp_route_records(view['viewpoint_id'])
        assert all(r['payload']['evidence_bindings'][0]['claim_id']=='C2' for r in current.values())


def test_complete_update_overflow_holds_without_screening_or_model_calls(setup,monkeypatch):
    from backend.pipeline import cvp_candidate_screening as screening
    store,initial,root,clients,_,view,_,_=_initial(setup)
    request=_request(store,initial,root,[view])
    request['budget']['max_request_bytes']=100
    auth=Path(request['authorization']['path'])
    direction=read_artifact(auth);direction['budget']=request['budget']
    auth.write_text(canonical_json(direction));request['authorization']=binding(auth)
    clients['gpt'].requests.clear();clients['opus'].requests.clear()
    monkeypatch.setattr(screening,'screen_to_fit',lambda *a,**k:pytest.fail('required update members were screened'))
    result=runner.run(store,request,root=root.parent/'overflow',execute=True,clients=clients)
    assert result['status']=='cvp_pending' and result['cvp_status']=='partial'
    assert not clients['gpt'].requests and not clients['opus'].requests
    assert result['units']['cvp-update']=='resource_hold'


def test_twenty_five_current_candidates_are_in_material_with_one_shared_source_section(setup):
    from backend.pipeline.cvp_recall_preparation import prepare_recall
    from backend.api.canonical_repository.cvp_recall import combine_recall_candidates
    from backend.pipeline.cvp_materials import prepare_materials
    store,initial,root,_,_,view,_,_=_initial(setup)
    replacements=[f'C{i}' for i in range(2,26)]
    for cid in replacements:_new_claim(store,cid)
    inp=prepare_update_input(store,initial['input'],viewpoint_ids=[view['viewpoint_id']],replacement_claim_ids=replacements)
    prepared=prepare_recall(store,inp)
    assert len(prepared['groups'])==1 and len(prepared['groups'][0]['claim_ids'])==25
    plan=prepared['recall_plans'][0]['plan'];assert plan['shards']==[]
    recall=combine_recall_candidates(plan,{cid:{view['viewpoint_id']} for cid in inp['claim_ids']})
    material=prepare_materials(store,prepared,'cvp-update',recall,maintenance_viewpoint_ids=[view['viewpoint_id']])
    assert len(material['claim_input']['claims'])==25
    assert all(c['support_loaded'] for c in material['material']['claims'])
    assert len(material['material']['sources'])==1
    assert len(material['material']['sources'][0]['sections'])==1


def test_update_uses_existing_multiround_review_and_preserves_old_findings(setup):
    from test_cvp_management import passing
    store,initial,root,clients,_,view,_,_=_initial(setup)
    request=_request(store,initial,root,[view])
    proposals=[];reviews=[]
    def propose(packet):
        proposals.append(packet)
        return _member_candidate(packet,summary='此处人子称号表明神性。' if len(proposals)==1 else '此处人子称号表明耶稣的神性，而非一般人的称呼。')
    def review(packet):
        if not packet.get('schema_version','').endswith('_review_packet'):return
        reviews.append(packet);value=passing(packet['candidate'])
        value['prior_findings']=[{'ref':ref,'disposition':'resolved','reason':'当前完整材料和修改已解决。'}
            for ref in packet['candidate']['review_sets']['prior_findings']]
        if len(reviews)==1:
            ref=packet['material']['viewpoints'][0]['cvp_ref']
            next(r for r in value['object_reviews'] if r['ref']==ref).update(decision='revise',finding_refs=['f1'])
            value['findings']=[{'slot':'f1','target_refs':[ref],'category':'wording','reason':'需要保留原文对照的限定。'}]
        return envelope('review',review=value)
    clients['gpt'].behavior=_proposal_behavior(propose);clients['opus'].behavior=review
    result,step=_run(store,request,root.parent/'multi',clients)
    assert len(proposals)==len(reviews)==2
    assert reviews[1]['candidate']['prior_findings']
    approval=read_artifact(Path(step['materials']['step_root'])/'approval.json')
    assert approval['approved'] and approval['review']['prior_findings'][0]['disposition']=='resolved'
    assert result['update']['status']=='arguments_pending'


def test_completed_update_does_not_reuse_old_pass_after_a_new_claim_change(setup):
    store,initial,root,clients,_,view,_,_=_initial(setup)
    request=_request(store,initial,root,[view])
    clients['gpt'].behavior=_proposal_behavior(lambda p:_member_candidate(p,summary='人子称号表明神性。'))
    _run(store,request,root.parent/'updated',clients)
    before=(len(clients['gpt'].requests),len(clients['opus'].requests))
    _update_claim(store,'C1',statement='人子称号表明神性。')
    with pytest.raises((ValueError,RuntimeError),match='changed|drift|pin|revision'):
        runner.run(store,request,root=root.parent/'updated',execute=True,clients=clients)
    assert before==(len(clients['gpt'].requests),len(clients['opus'].requests))


def test_identity_candidate_v1_still_rejects_empty_claims():
    from backend.api.canonical_repository.knowledge_models import ViewpointIdentityCandidateRecord
    value={'identity_candidate_id':'VIC-test','candidate_claim_ids':[],'candidate_viewpoint_ids':['V1'],
           'proposed_action':'match_existing','scope_manifest_sha256':'material','generation_fingerprint':'generation'}
    with pytest.raises(ValueError,match='empty Claim cohort'):ViewpointIdentityCandidateRecord.model_validate(value)
    assert ViewpointIdentityCandidateRecord.model_validate({**value,'schema_version':'wang_viewpoint_identity_candidate_v2'})
    with pytest.raises(ValueError,match='empty Claim cohort'):
        ViewpointIdentityCandidateRecord.model_validate({**value,'schema_version':'wang_viewpoint_identity_candidate_v2','proposed_action':'create_new'})


def test_existing_destination_and_source_are_reviewed_and_committed_together(setup):
    store,initial,root,clients,_,view_a,link_a,index_root=_initial(setup)
    _new_claim(store,'C2')
    second=deepcopy(initial);second['action_id']='second-view'
    second['input']=seal({k:v for k,v in initial['input'].items() if k!='artifact_sha256'}|{
        'claim_ids':['C2'],'grouping':{'scope_label':'second','groups':[{'group_key':'g1','claim_ids':['C2'],'rationale':'test'}]},
        'index_root':str(index_root)})
    auth=root.parent/'second-auth.json'
    write_immutable(auth,{'schema_version':'wang_cvp_management_authorization_v1','authorized_by':'owner',
        **{k:second[k] for k in ('action_id','mode','maintenance_viewpoint_ids','permissions','creation_slots','budget')},
        'input_sha256':second['input']['artifact_sha256']})
    second['authorization']=binding(auth)
    assert runner.run(store,second,root=root.parent/'second',execute=True,clients=clients)['status']=='completed'
    view_b=next(v for v in store.read_cvp_heads() if v['viewpoint_id']!=view_a['viewpoint_id'])
    request=_request(store,initial,root,[view_a,view_b])
    def move(packet):
        links={row['claim_ref']:row for row in packet['material']['links']}
        own_a=links[packet['input']['claims'][0]['claim_ref']]
        own_b=links[packet['input']['claims'][1]['claim_ref']]
        a,b=own_a['cvp_ref'],own_b['cvp_ref']
        return envelope('candidate',candidate={'changes':[
            _change(a,{'identity_status':'merged','redirect_to_viewpoint_id':b}),
            _change(own_a['link_ref'],{'viewpoint_id':b})],
            'component_dispositions':[{'component_ref':f'k{i}','disposition':'existing_coverage',
                'target_ref':b,'link_ref':own['link_ref'],'reason':'完整比较两边当前材料后保留为同一观点。'}
                for i,own in enumerate((own_a,own_b),1)],
            'member_dispositions':[{'member_ref':own_a['link_ref'],'disposition':'changed','reason':'改挂到保留身份。'},
                                   {'member_ref':own_b['link_ref'],'disposition':'retained','reason':'当前成员完整保留。'}],
            'summaries':[{'ref':b,'summary':'人子称号表达耶稣的神性。'}]})
    clients['gpt'].behavior=_proposal_behavior(move)
    result,step=_run(store,request,root.parent/'merge',clients)
    assert not result['update']['issues']
    assert result['update']['inactive_viewpoints'][view_a['viewpoint_id']]['identity_status']=='merged'
    after=store.get_record_state('viewpoint_claim_links',link_a['viewpoint_claim_link_id'])['payload']
    assert after['viewpoint_id']==view_b['viewpoint_id']
    assert store.get_record_state('canonical_viewpoints',view_b['viewpoint_id'])['payload']['summary_basis']['member_link_ids']
    records=step['materials']['bundle']['material']
    assert len([v for v in records['viewpoints'] if not v['context_only']])==2
    assert {r['content']['claim_id'] for r in records['links']}=={'C1','C2'}


@pytest.mark.parametrize('fault',['active','new_link','changed_assertion','other_workflow'])
def test_history_edge_exception_does_not_authorize_active_or_changed_edges(setup,fault):
    from types import SimpleNamespace
    from backend.api.canonical_repository.postgres_store import ChangeOperation,PostgresKnowledgeStore,ChangeSetConflict
    store,_,_,_,_,_,link,_=_initial(setup)
    _retire_claim(store,'C1')
    payload={**link,'schema_version':'wang_viewpoint_claim_link_v2','effective_state':'retired',
             'revision':2,'decision_id':'new-reviewed-decision'}
    source_kind=domain.VERSION
    if fault=='active':payload['effective_state']='active'
    if fault=='new_link':payload['viewpoint_claim_link_id']='different-history'
    if fault=='changed_assertion':payload['link_type']='supports'
    if fault=='other_workflow':source_kind='unrelated'
    op=ChangeOperation(operation='update',collection='viewpoint_claim_links',object_id=payload['viewpoint_claim_link_id'],
        before_revision=1,before_sha256=record_content_sha(link),after_revision=2,
        after_sha256=record_content_sha(payload),payload=payload)
    with store.connect() as conn,conn.cursor() as cursor,pytest.raises(ChangeSetConflict,match='non-current endpoints'):
        PostgresKnowledgeStore._assert_edge_integrity(cursor,SimpleNamespace(operations=[op],source_kind=source_kind))


def test_inactive_conclusion_does_not_expose_its_historical_route():
    from backend.tests.test_viewpoint_admin_projection import _fixture
    from backend.api.canonical_repository.viewpoint_runtime_projection import ViewpointRuntimeCompiler
    fixture=_fixture()
    fixture.records['canonical_viewpoints']=[v.model_copy(update={'identity_status':'retired'})
                                             for v in fixture.records['canonical_viewpoints']]
    compiler=ViewpointRuntimeCompiler(fixture.records,fixture.citations)
    assert compiler.compile_route_snapshots('CVS-16')==[]
    assert compiler.compile_registry_snapshots('CVS-16')==[]


def test_discovery_includes_semantic_dependents_but_not_old_production_candidates():
    from types import SimpleNamespace
    from backend.pipeline.cvp_update_preparation import affected_update_viewpoints
    def row(payload):return {'payload':payload,'retired':False}
    context={
        ('viewpoint_claim_links','L'):row({'effective_state':'invalidated','claim_id':'C','viewpoint_id':'V-link'}),
        ('viewpoint_relations','R'):row({'effective_state':'active','supporting_claim_ids':['C'],
                                       'source_viewpoint_id':'V-source','target_viewpoint_id':'V-target'}),
        ('viewpoint_observation_supports','O'):row({'effective_state':'active','viewpoint_id':'V-observation',
                                                  'evidence_bindings':[{'claim_id':'C'}]}),
        ('argument_route_attestations','A'):row({'effective_state':'active','claim_ids':['C'],'argument_route_id':'AR'}),
        ('argument_routes','AR'):row({'route_status':'active','conclusion_viewpoint_id':'V-route'}),
        ('viewpoint_identity_candidates','history'):row({'candidate_claim_ids':['C'],'candidate_viewpoint_ids':['V-unrelated']}),
    }
    reads=[]
    def affected(keys):reads.append(keys);return context
    store=SimpleNamespace(read_affected_context=affected,
        read_current_records=lambda keys:{key:row({'identity_status':'active'}) for key in keys})
    assert affected_update_viewpoints(store,['C'])==['V-link','V-observation','V-route','V-source','V-target']
    assert reads==[[('claims','C')]]
    assert affected_update_viewpoints(store,[])==[]
    assert len(reads)==1


def test_invalidated_member_remains_in_update_scope_and_can_be_revalidated(setup):
    from backend.pipeline.cvp_update_preparation import affected_update_viewpoints
    store,initial,root,clients,_,view,link,_=_initial(setup)
    payload={**link,'effective_state':'invalidated'}
    with store.connect() as conn:
        conn.execute("UPDATE wang_knowledge.objects SET payload=%s::jsonb,content_sha256=%s WHERE collection='viewpoint_claim_links' AND object_id=%s",
                     (canonical_json(payload),record_content_sha(payload),link['viewpoint_claim_link_id']))
    assert affected_update_viewpoints(store,['C1'])==[view['viewpoint_id']]
    request=_request(store,initial,root,[view])
    assert request['input']['claim_ids']==['C1']
    def revalidate(packet):
        v=packet['material']['viewpoints'][0]['cvp_ref'];l=packet['material']['links'][0]['link_ref']
        return envelope('candidate',candidate={'changes':[_change(l,{'effective_state':'active'})],
            'component_dispositions':[{'component_ref':'k1','disposition':'connected','target_ref':v,'link_ref':l,
                                       'reason':'当前 Claim 与完整原文重核后恢复同一连接。'}],
            'summaries':[{'ref':v,'summary':'人子称号表明神性。'}]})
    clients['gpt'].behavior=_proposal_behavior(revalidate)
    result,_=_run(store,request,root.parent/'revalidate',clients)
    assert result['update']['status']=='arguments_pending'
    assert store.get_record_state('viewpoint_claim_links',link['viewpoint_claim_link_id'])['payload']['effective_state']=='active'


def test_update_never_reads_the_unrelated_library_index(setup,monkeypatch):
    store,initial,root,clients,_,view,_,_=_initial(setup)
    request=_request(store,initial,root,[view])
    original=store.read_cvp_heads;scopes=[]
    def scoped_only(*,viewpoint_ids=None):
        assert viewpoint_ids is not None,'update read the complete Registry index'
        scopes.append(list(viewpoint_ids))
        return original(viewpoint_ids=viewpoint_ids)
    monkeypatch.setattr(store,'read_cvp_heads',scoped_only)
    clients['gpt'].behavior=_proposal_behavior(lambda p:_member_candidate(p,summary='人子称号表明神性。'))
    _run(store,request,root.parent/'scoped-index',clients)
    assert scopes and all(scope==[view['viewpoint_id']] for scope in scopes)
