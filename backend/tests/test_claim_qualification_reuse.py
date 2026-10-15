"""Completed target review survives reviewed peer anchor pruning, not semantic drift."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from backend.pipeline import claim_management as cm
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.claim_qualification_reuse import anchor_pruning
from backend.pipeline.source_reconciliation_runner import binding
from backend.tests.test_claim_management_postgres import action,postgres_store,_temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts,operation_request,passing,rejected


def request_for(action,cid,fields):
    request,root=operation_request(action,'Update',[cid]);request.permissions[0].fields=fields
    request.policy=cm.source.FileBinding.model_validate(binding(cm.DEFAULT_POLICY))
    file=Path(request.authorization.path);auth=json.loads(file.read_text());auth['request']=request.model_dump(mode='json',exclude={'authorization','policy'});auth['policy']=request.policy.model_dump();file.write_text(json.dumps(auth));request.authorization=cm.source.FileBinding.model_validate(binding(file))
    return request,root


@pytest.fixture
def related(action):
    store,_,_,_,root,_=action
    anchors=[{'paragraph_key':'S0001','proposed_highlight':{'text':text}} for text in ['甲是假设，不是断言。','甲是假设']]
    store.apply_plan(store.plan_package({'package_id':'peer-locators','claims':[
        {'claim_id':'C','claim_type':'exegesis','statement':'甲是假设。','occurrences':[{'source_id':'S','anchors':[]}],'evidence_step_ids':['E']},
        {'claim_id':'D','claim_type':'exegesis','statement':'甲是假设。','occurrences':[{'source_id':'S','anchors':anchors}]}],
        'evidence_steps':[{'evidence_step_id':'E','statement':'甲是假设。','produced_claim_ids':['C']}],
        'claim_relations':[{'claim_relation_id':'R','from_id':'D','to_id':'C','relation_type':'supports','reason':'same source'}]}))
    return action


def qualify_original(related):
    action=related;store=action[0];request,root=request_for(action,'C',['statement'])
    ports=SyntheticPorts(store,root);assert execute_claim_management(store,request,root=root,ports=ports)['status']=='completed'
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id));rid=next(iter(parent['latest_rounds'].values()))
    return request,root,rid,deepcopy(store.get_cvp_group(rid))


def prune_peer(related,*,semantic=False,expected_status="completed"):
    action=related;store=action[0];request,root=request_for(action,'D',['occurrences','statement'])
    def opus(packet):return rejected(packet,key='claims/D') if not packet['round_number'] else passing(packet)
    def proposal(packet):
        result=cm.existing_candidate(packet['before']);post=deepcopy(packet['before']['claims/D']['payload']);post['occurrences'][0]['anchors'].pop()
        if semantic:post['statement']='另一个判断。'
        result.records=[cm.source.Row(collection='claims',object_id='D',payload=post)]
        for d in result.dispositions:
            if d.record_key=='claims/D':d.action='update';d.reason='remove peer-only locator';d.citations=[cm.Citation(source_id='S',paragraph_key='S0001',exact_quote='甲是假设，不是断言。')]
        result.finding_resolutions=[cm.FindingResolution(finding_id=fid,disposition='resolved',reason='pruned wrong peer locator',citations=[cm.Citation.model_validate(f['citations'][0])]) for fid,f in packet['unresolved'].items()]
        return result.model_dump(mode='json')
    ports=SyntheticPorts(store,root,proposal=proposal,opus=opus)
    result=execute_claim_management(store,request,root=root,ports=ports);assert result['status']==expected_status,result
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))
    return next(iter(parent['latest_rounds'].values())),ports


def test_peer_pruning_retains_original_qualification_and_action_completion_without_new_calls(related):
    store=related[0];request,root,rid,old=qualify_original(related);original=store.get_record_state('claims','C')
    peer,ports=prune_peer(related)
    assert store.claim_management_qualification('C')['ready'] and store.claim_management_qualification('D')['ready']
    current=store.get_cvp_group(rid);proof=current.pop('reviewed_context_reuse')
    # Dependency invalidation remains a historical fact; the additive proof
    # explains why the original audit still applies, without rewriting it.
    assert current.pop('checkpoint')=='applied'
    assert current.pop('pending_audit_from_change_set_ids')
    old.pop('checkpoint')
    assert current==old and store.get_record_state('claims','C')==original
    assert proof['changed_readonly_claims']==['claims/D']
    calls=list(ports.calls)
    assert store.revalidate_claim_management_context(rid,peer)==proof
    assert execute_claim_management(store,request,root=root,ports=ports)['status']=='completed'
    assert ports.calls==calls
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM wang_knowledge.review_events WHERE decision='review_context_revalidated'").fetchone()==(1,)


def test_readonly_review_never_creates_missing_original_target_qualification(related):
    store=related[0];prune_peer(related)
    assert store.claim_management_qualification('C') is None


@pytest.mark.parametrize('change',['peer_statement','target_statement','evidence','relation','source_document'])
def test_actual_review_basis_change_never_reuses_old_target_proof(related,change):
    store=related[0];_,_,rid,_=qualify_original(related)
    if change!='peer_statement':
        col,oid,field,text={
            'target_statement':('claims','C','statement','新判断'),
            'evidence':('evidence_steps','E','statement','新证据'),
            'relation':('claim_relations','R','reason','新关系含义'),
            'source_document':('source_documents','S','title','new registered source')}[change]
        p=deepcopy(store.get_record_state(col,oid)['payload']);p[field]=text
        store.apply_plan(store.plan_package({'package_id':'actual-change-'+change,col:[p]}))
    peer,_=prune_peer(related,semantic=change=='peer_statement')
    assert not store.claim_management_qualification('C')['ready']
    assert 'reviewed_context_reuse' not in store.get_cvp_group(rid)
    with pytest.raises(ValueError):store.revalidate_claim_management_context(rid,peer)


@pytest.mark.parametrize('change',['source_file','target_hold','new_relation','event_removed','proof_tamper','peer_review_invalidated','peer_raw_tamper','original_raw_tamper'])
def test_reused_qualification_still_fails_closed_after_drift(related,change):
    store=related[0];request,root,rid,_=qualify_original(related);peer,_=prune_peer(related)
    if change=='source_file':
        p=Path(request.source_bindings['S'].path);p.write_text(p.read_text()+' ')
    elif change=='new_relation':
        store.apply_plan(store.plan_package({'package_id':'new-edge','claim_relations':[{'claim_relation_id':'R2','from_id':'C','to_id':'D','relation_type':'supports'}]}))
    elif change=='target_hold':
        p=deepcopy(store.get_record_state('claims','C')['payload']);p['review_status']='human_hold'
        store.apply_plan(store.plan_package({'package_id':'hold','claims':[p]}))
    elif change in {'peer_raw_tamper','original_raw_tamper'}:
        p=Path(store.get_cvp_group(peer if change=='peer_raw_tamper' else rid)['reviewed']['evidence']['approval']['path']);p.write_text(p.read_text()+' ')
    else:
        with store.connect() as conn:
            if change=='event_removed':conn.execute("DELETE FROM wang_knowledge.review_events WHERE decision='review_context_revalidated'")
            elif change=='proof_tamper':
                p=store.get_cvp_group(rid);p.pop('run_id');p['reviewed_context_reuse']['current_pins'][0]['pin'][0]+=1
                conn.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb WHERE run_id=%s',(json.dumps(p),rid))
            else:conn.execute("UPDATE wang_knowledge.pipeline_runs SET metadata=jsonb_set(metadata,'{revisit_required}','true') WHERE run_id=%s",(peer,))
    assert not store.claim_management_qualification('C')['ready']


def test_late_reuse_event_failure_rolls_back_completion_proof(related,monkeypatch):
    # Admission is part of completion's existing transaction, not the preceding
    # content transaction. A failed admission cannot publish a partial reuse.
    store=related[0];_,_,rid,_=qualify_original(related)
    from backend.pipeline import claim_qualification_reuse as reuse
    original=reuse.admit
    def fail(*args,**kwargs):
        original(*args,**kwargs);raise RuntimeError('after reuse event')
    monkeypatch.setattr(reuse,'admit',fail)
    peer,ports=prune_peer(related,expected_status='paused')
    assert 'reviewed_context_reuse' not in store.get_cvp_group(rid)
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM wang_knowledge.review_events WHERE decision='review_context_revalidated'").fetchone()==(0,)

    # The separately committed peer content survives. Retrying completion uses
    # saved review/readback and atomically registers the proof, without calls.
    assert len(store.get_record_state('claims','D')['payload']['occurrences'][0]['anchors'])==1
    monkeypatch.setattr(reuse,'admit',original)
    value=store.get_cvp_group(peer)
    request=cm.Request.model_validate(value['materials']['request'])
    calls=list(ports.calls)
    assert execute_claim_management(store,request,root=ports.tmp_path,ports=ports)['status']=='completed'
    assert ports.calls==calls and store.claim_management_qualification('C')['ready']


@pytest.mark.parametrize('change',['replacement','reorder','drop_all','occurrence_metadata','only_revision'])
def test_anchor_pruning_rejects_other_edits(change):
    before={'revision':1,'statement':'same','occurrences':[{'source_id':'S','anchors':[{'text':'a'},{'text':'b'},{'text':'c'}]}]}
    after=deepcopy(before);after['revision']=2
    if change=='replacement':after['occurrences'][0]['anchors']=[{'text':'a'},{'text':'new'}]
    elif change=='reorder':after['occurrences'][0]['anchors']=[{'text':'b'},{'text':'a'}]
    elif change=='drop_all':after['occurrences'][0]['anchors']=[]
    elif change=='occurrence_metadata':
        after['occurrences'][0]['source_id']='other';after['occurrences'][0]['anchors'].pop()
    assert not anchor_pruning(before,after)


def test_newer_formal_objection_prevents_reuse(related):
    store=related[0];_,_,rid,_=qualify_original(related)
    with store.connect() as conn:
        conn.execute("INSERT INTO wang_knowledge.pipeline_runs (run_id,subject_id,stage,trigger,status,metadata) VALUES ('newer-objection','C','review','cli','succeeded',%s::jsonb)",
            (json.dumps({'blocked_current_claim_ids':['C']}),))
    peer,_=prune_peer(related)
    assert not store.claim_management_qualification('C')['ready']
    with pytest.raises(ValueError,match='later formal finding'):store.revalidate_claim_management_context(rid,peer)
