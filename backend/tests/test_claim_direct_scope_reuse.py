"""An original full proof plus a scoped peer review; no model-scope union."""
from copy import deepcopy
from pathlib import Path
import json

import pytest

from backend.pipeline import claim_management as cm, claim_qualification_reuse as reuse
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_qualification_reuse import related, qualify_original, prune_peer
from backend.tests.test_claim_management_execution_postgres import passing


@pytest.fixture(autouse=True)
def direct_policy(monkeypatch):
    monkeypatch.setattr(cm,'DEFAULT_POLICY',cm.DEFAULT_POLICY.parent/'wang_claim_management_opus_only_v3.json')


def prepare_graph(action, *, upstream=False):
    store=action[0]
    d=deepcopy(store.get_record_state('claims','D')['payload']);d['evidence_step_ids']=['F']
    r=deepcopy(store.get_record_state('claim_relations','R')['payload'])
    r.update(from_id='D' if upstream else 'C',to_id='C' if upstream else 'D')
    store.apply_plan(store.plan_package({'package_id':'separate-own-evidence',
        'claims':[d],'claim_relations':[r],
        'evidence_steps':[{'evidence_step_id':'F','statement':'甲是假设。','source_id':'S','produced_claim_ids':['D']}]}))


@pytest.mark.parametrize('upstream,semantic',[(False,False),(True,False),(False,True)])
@pytest.mark.parametrize('old_direct',[False,True])
def test_reuse_keeps_each_model_scope_separate_and_original_completion_current(related,monkeypatch,upstream,semantic,old_direct):
    store=related[0];prepare_graph(related,upstream=upstream)
    def opus(packet):
        assert 'evidence_steps/E' in packet['before']
        assert ('evidence_steps/F' not in packet['before'])==old_direct
        return passing(packet)
    with monkeypatch.context() as context:
        if not old_direct:context.setattr(cm,'DEFAULT_POLICY',cm.DEFAULT_POLICY.parent/'wang_claim_management_opus_only_v2.json')
        request,root,rid,old=qualify_original(related,opus=opus)
    peer,ports=prune_peer(related,semantic=semantic)
    p=store.get_cvp_group(peer)
    assert 'evidence_steps/F' in p['materials']['before'] and 'evidence_steps/E' not in p['materials']['before']
    proof=store.get_cvp_group(rid)['reviewed_context_reuse']
    assert proof['schema_version']=='wang_claim_review_context_reuse_v4'
    assert proof['unchanged_original_only_keys']==['evidence_steps/E']
    assert proof['changed_context_keys']==['claims/D']
    assert store.get_cvp_group(rid)['audited_pins']==old['audited_pins']
    assert store.claim_management_qualification('C')['ready'] and store.claim_management_qualification('D')['ready']
    assert store.revalidate_claim_management_context(rid,peer)==proof
    calls=list(ports.calls)
    assert execute_claim_management(store,request,root=root,ports=ports)['status']=='completed'
    assert ports.calls==calls==['opus','gpt','opus']


def test_changed_upstream_claim_still_requires_target_review(related):
    store=related[0];prepare_graph(related,upstream=True)
    _,_,rid,_=qualify_original(related)
    prune_peer(related,semantic=True)
    assert not store.claim_management_qualification('C')['ready']
    assert store.claim_management_qualification('D')['ready']
    assert 'reviewed_context_reuse' not in store.get_cvp_group(rid)


@pytest.mark.parametrize('drift',['own_evidence','target','new_target_relation','new_owner_evidence','later_finding'])
def test_original_scope_drift_cannot_hide_outside_peer_packet(related,drift):
    store=related[0];prepare_graph(related)
    _,_,rid,_=qualify_original(related)
    if drift=='later_finding':
        with store.connect() as conn:
            conn.execute("INSERT INTO wang_knowledge.pipeline_runs (run_id,subject_id,stage,trigger,status,metadata) VALUES ('new-objection','C','review','cli','succeeded',%s::jsonb)",
                (json.dumps({'blocked_current_claim_ids':['C']}),))
    else:
        package={'package_id':'outside-peer-drift'}
        if drift in {'own_evidence','target'}:
            col,oid=('evidence_steps','E') if drift=='own_evidence' else ('claims','C')
            value=deepcopy(store.get_record_state(col,oid)['payload']);value['statement']='另一个判断。'
            package[col]=[value]
        elif drift=='new_target_relation':
            package['claim_relations']=[{'claim_relation_id':'RC','from_id':'D','to_id':'C','relation_type':'supports'}]
        else:
            package['evidence_steps']=[{'evidence_step_id':'NEW','statement':'new support','source_id':'S','produced_claim_ids':['C']}]
            claim=deepcopy(store.get_record_state('claims','C')['payload'])
            claim['evidence_step_ids'].append('NEW');package['claims']=[claim]
        store.apply_plan(store.plan_package(package))
    prune_peer(related,semantic=True)
    assert not store.claim_management_qualification('C')['ready']
    assert 'reviewed_context_reuse' not in store.get_cvp_group(rid)


@pytest.mark.parametrize('drift',['own_evidence','peer_evidence','source_file','target_hold','new_relation',
    'peer_new_relation','peer_review','peer_raw','original_raw','proof','coverage','event'])
def test_direct_reuse_proof_stays_bound_after_later_drift(related,drift):
    store=related[0];prepare_graph(related)
    request,_,rid,_=qualify_original(related)
    peer,_=prune_peer(related,semantic=True)
    assert store.claim_management_qualification('C')['ready']
    if drift in {'own_evidence','peer_evidence','target_hold'}:
        col,oid=('claims','C') if drift=='target_hold' else ('evidence_steps','E' if drift=='own_evidence' else 'F')
        value=deepcopy(store.get_record_state(col,oid)['payload'])
        value.update({'review_status':'human_hold'} if drift=='target_hold' else {'statement':'另一个判断。'})
        store.apply_plan(store.plan_package({'package_id':'post-proof-change',col:[value]}))
    elif drift=='source_file':
        file=Path(request.source_bindings['S'].path);file.write_text(file.read_text()+' ')
    elif drift in {'new_relation','peer_new_relation'}:
        package={'package_id':'new-edge','claim_relations':[
            {'claim_relation_id':'NEW','from_id':'D','to_id':'C' if drift=='new_relation' else 'P','relation_type':'supports'}]}
        if drift=='peer_new_relation':
            package['claims']=[{'claim_id':'P','statement':'甲是假设。','claim_type':'exegesis','occurrences':[{'source_id':'S','anchors':[]}]}]
        store.apply_plan(store.plan_package(package))
    elif drift in {'peer_raw','original_raw'}:
        value=store.get_cvp_group(peer if drift=='peer_raw' else rid)
        file=Path(value['reviewed']['evidence']['approval']['path']);file.write_text(file.read_text()+' ')
    else:
        with store.connect() as conn:
            if drift=='event':conn.execute("DELETE FROM wang_knowledge.review_events WHERE decision='review_context_revalidated'")
            elif drift=='peer_review':conn.execute("UPDATE wang_knowledge.pipeline_runs SET metadata=jsonb_set(metadata,'{revisit_required}','true') WHERE run_id=%s",(peer,))
            else:
                value=store.get_cvp_group(rid);value.pop('run_id')
                if drift=='coverage':value['reviewed_context_reuse']['unchanged_original_only_keys']=[]
                else:value['reviewed_context_reuse']['current_pins'][0]['pin'][0]+=1
                conn.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb WHERE run_id=%s',(json.dumps(value),rid))
    assert not store.claim_management_qualification('C')['ready']


def test_reuse_event_rollback_and_completion_recovery_do_not_repeat_models(related,monkeypatch):
    store=related[0];prepare_graph(related)
    _,_,rid,_=qualify_original(related)
    original=reuse.admit
    def fail(*args,**kwargs):
        original(*args,**kwargs);raise RuntimeError('late reuse event failure')
    monkeypatch.setattr(reuse,'admit',fail)
    peer,ports=prune_peer(related,semantic=True,expected_status='paused')
    assert 'reviewed_context_reuse' not in store.get_cvp_group(rid)
    monkeypatch.setattr(reuse,'admit',original)
    calls=list(ports.calls);request=cm.Request.model_validate(store.get_cvp_group(peer)['materials']['request'])
    assert execute_claim_management(store,request,root=ports.tmp_path,ports=ports)['status']=='completed'
    assert ports.calls==calls and store.claim_management_qualification('C')['ready']


def test_successive_peer_reviews_replace_one_proof_without_a_history_chain(related):
    store=related[0];prepare_graph(related)
    _,_,rid,_=qualify_original(related)
    first,_=prune_peer(related,semantic=True)
    second,ports=prune_peer(related,semantic=True)
    proof=store.get_cvp_group(rid)['reviewed_context_reuse']
    assert proof['peer_round_id']==second
    assert proof['unchanged_original_only_keys']==['evidence_steps/E']
    with store.connect() as conn:
        conn.execute("UPDATE wang_knowledge.pipeline_runs SET metadata=jsonb_set(metadata,'{revisit_required}','true') WHERE run_id=%s",(first,))
    assert store.claim_management_qualification('C')['ready'] and ports.calls==['opus','gpt','opus']


def test_peer_full_review_does_not_qualify_an_unreviewed_neighbour(related):
    store=related[0];prepare_graph(related)
    prune_peer(related,semantic=True)
    assert store.claim_management_qualification('C') is None


@pytest.mark.parametrize('change',['missing_verdict','rejected_verdict','missing_answer','history_missing','history_sha','scope_expanded'])
def test_reuse_requires_authentic_complete_peer_review_and_original_history(related,monkeypatch,change):
    from backend.tests.test_claim_qualification_reuse import test_context_reuse_requires_full_current_review_and_exact_history
    store=related[0]
    relation=deepcopy(store.get_record_state('claim_relations','R')['payload'])
    relation.update(from_id='C',to_id='D')
    store.apply_plan(store.plan_package({'package_id':'direction-only','claim_relations':[relation]}))
    test_context_reuse_requires_full_current_review_and_exact_history(related,monkeypatch,change)


@pytest.mark.parametrize('when',['before_peer','after_reuse'])
def test_new_human_fact_requires_review_of_that_fact(related,when):
    from backend.tests.test_claim_human_source_decisions import facts,save
    store=related[0];prepare_graph(related)
    request,_,rid,_=qualify_original(related)
    if when=='after_reuse':
        prune_peer(related,semantic=True)
        assert store.claim_management_qualification('C')['ready']
    _,entries=facts(request,related[4])
    save(store,entries)
    if when=='before_peer':prune_peer(related,semantic=True)
    assert not store.claim_management_qualification('C')['ready']
    if when=='before_peer':assert 'reviewed_context_reuse' not in store.get_cvp_group(rid)


def test_new_peer_objection_cannot_prove_safety_for_old_target(related):
    from backend.tests.test_claim_qualification_reuse import context_objection
    store=related[0];prepare_graph(related)
    store.apply_plan(store.plan_package({'package_id':'second-neighbour',
        'claims':[{'claim_id':'P','statement':'甲是假设。','claim_type':'exegesis','occurrences':[{'source_id':'S','anchors':[]}]}],
        'claim_relations':[{'claim_relation_id':'DP','from_id':'D','to_id':'P','relation_type':'supports'}]}))
    _,_,rid,old=qualify_original(related)
    assert 'claims/P' not in old['materials']['before']
    prune_peer(related,semantic=True,context=context_objection)
    assert not store.claim_management_qualification('C')['ready']
    assert 'reviewed_context_reuse' not in store.get_cvp_group(rid)


@pytest.mark.parametrize('peer_source_drift',[False,True])
def test_cross_sermon_reuse_uses_two_complete_own_sources_without_merging_model_packets(related,peer_source_drift):
    from backend.pipeline.source_reconciliation_runner import binding
    from backend.tests.test_claim_management_execution_postgres import operation_request,SyntheticPorts
    from backend.tests.test_claim_management_human_source_follow_up import authorize
    store,original,_,_,root,_=related
    prepare_graph(related)
    file=root/'foreign-source.json';file.write_text(json.dumps({'script':[{'text':'乙是另一篇的判断。'}]}))
    b=binding(file)
    d=deepcopy(store.get_record_state('claims','D')['payload']);d['occurrences']=[{'source_id':'T','anchors':[]}]
    evidence=deepcopy(store.get_record_state('evidence_steps','F')['payload']);evidence['source_id']='T'
    store.apply_plan(store.plan_package({'package_id':'cross-sermon-peer',
        'source_documents':[{'source_id':'T','title':'foreign','source_type':'sermon_transcript','source_path':str(file),'source_file_sha256':b['file_sha256']}],
        'claims':[d],'evidence_steps':[evidence]}))
    def old_review(packet):
        assert set(packet['sources'])=={'S'} and 'evidence_steps/F' not in packet['before']
        return passing(packet)
    _,_,rid,old=qualify_original(related,opus=old_review)
    assert set(old['materials']['request']['source_bindings'])=={'S'}
    original.source_bindings['T']=cm.source.FileBinding.model_validate(b)
    request,output=operation_request(related,'Update',['D'])
    policy=cm.Policy.model_validate_json(cm.DEFAULT_POLICY.read_text());policy.initial_candidate='proposal'
    policyfile=root/'foreign-peer-policy.json';policyfile.write_text(policy.model_dump_json())
    request.policy=cm.source.FileBinding.model_validate(binding(policyfile));authorize(request,{})
    def proposal(packet):
        assert set(packet['sources'])=={'T'} and 'evidence_steps/E' not in packet['before']
        candidate=cm.existing_candidate(packet['before'])
        post=deepcopy(packet['before']['claims/D']['payload']);post['statement']='乙是另一篇的判断。'
        candidate.records=[cm.source.Row(collection='claims',object_id='D',payload=post)]
        for disposition in candidate.dispositions:
            if disposition.record_key=='claims/D':
                disposition.action='update';disposition.reason='synthetic foreign source correction'
                disposition.citations=[cm.Citation(source_id='T',paragraph_key='S0001',exact_quote='乙是另一篇的判断。')]
        return candidate.model_dump(mode='json')
    def peer_review(packet):
        assert set(packet['sources'])=={'T'} and 'evidence_steps/E' not in packet['before']
        return passing(packet)
    ports=SyntheticPorts(store,root,proposal=proposal,opus=peer_review)
    result=execute_claim_management(store,request,root=output,ports=ports)
    assert result['status']=='completed',result
    assert store.claim_management_qualification('C')['ready'] and store.claim_management_qualification('D')['ready']
    assert store.get_cvp_group(rid)['reviewed_context_reuse']['schema_version']=='wang_claim_review_context_reuse_v4'
    assert ports.calls==['gpt','opus']
    if peer_source_drift:
        file.write_text(file.read_text()+' ')
        assert not store.claim_management_qualification('C')['ready']


def test_original_evidence_write_is_preserved_without_peer_reapproval(related):
    from backend.tests.test_claim_qualification_reuse import request_for
    from backend.tests.test_claim_management_execution_postgres import SyntheticPorts
    from backend.tests.test_claim_management_human_source_follow_up import authorize
    from backend.pipeline.source_reconciliation_runner import binding
    store=related[0];prepare_graph(related)
    request,root=request_for(related,'C',['statement'])
    request.permissions.append(cm.source.Permission(collection='evidence_steps',object_id='E',fields=['statement']))
    policy=cm.Policy.model_validate_json(cm.DEFAULT_POLICY.read_text());policy.initial_candidate='proposal'
    path=related[4]/'own-evidence-policy.json';path.write_text(policy.model_dump_json())
    request.policy=cm.source.FileBinding.model_validate(binding(path));authorize(request,{})
    def proposal(packet):
        result=cm.existing_candidate(packet['before'])
        post=deepcopy(packet['before']['evidence_steps/E']['payload']);post['statement']='甲是假设，不是断言。'
        result.records=[cm.source.Row(collection='evidence_steps',object_id='E',payload=post)]
        for d in result.dispositions:
            if d.record_key=='evidence_steps/E':
                d.action='update';d.reason='synthetic own evidence correction'
                d.citations=[cm.Citation(source_id='S',paragraph_key='S0001',exact_quote='甲是假设，不是断言。')]
        return result.model_dump(mode='json')
    ports=SyntheticPorts(store,related[4],proposal=proposal,opus=passing)
    result=execute_claim_management(store,request,root=root,ports=ports)
    assert result['status']=='completed',result
    rid=next(iter(store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))['latest_rounds'].values()))
    original=deepcopy(store.get_record_state('evidence_steps','E'))
    peer,_=prune_peer(related,semantic=True)
    assert 'evidence_steps/E' not in store.get_cvp_group(peer)['materials']['before']
    assert store.get_record_state('evidence_steps','E')==original
    assert store.claim_management_qualification('C')['ready']
    assert store.get_cvp_group(rid)['reviewed_context_reuse']['unchanged_original_only_keys']==['evidence_steps/E']


def test_joint_original_targets_keep_their_own_complete_proofs_when_only_one_is_peer_context(related):
    from backend.tests.test_claim_management_execution_postgres import operation_request,SyntheticPorts
    from backend.tests.test_claim_management_human_source_follow_up import authorize
    from backend.pipeline.source_reconciliation_runner import binding
    store=related[0];prepare_graph(related)
    store.apply_plan(store.plan_package({'package_id':'joint-independent-target',
        'claims':[{'claim_id':'W','statement':'甲是假设。','claim_type':'exegesis','occurrences':[{'source_id':'S','anchors':[]}],'evidence_step_ids':['EW']}],
        'evidence_steps':[{'evidence_step_id':'EW','statement':'甲是假设。','source_id':'S','produced_claim_ids':['W']}]}))
    request,root=operation_request(related,'Update',['C','W'])
    request.policy=cm.source.FileBinding.model_validate(binding(cm.DEFAULT_POLICY.parent/'wang_claim_management_joint_update_v4.json'))
    authorize(request,{'required_maintenance_keys':[]})
    result=execute_claim_management(store,request,root=root,ports=SyntheticPorts(store,related[4],
        opus=lambda packet:{'complete_review':True,'findings':[],'prior_findings':[]}))
    assert result['status']=='completed',result
    rid=next(iter(store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))['latest_rounds'].values()))
    peer,ports=prune_peer(related,semantic=True)
    packet=store.get_cvp_group(peer)['materials']['before']
    assert 'claims/C' in packet and not {'claims/W','evidence_steps/E','evidence_steps/EW'} & set(packet)
    proof=store.get_cvp_group(rid)['reviewed_context_reuse']
    assert set(proof['unchanged_original_only_keys'])=={'claims/W','evidence_steps/E','evidence_steps/EW'}
    assert store.claim_management_qualification('C')['ready'] and store.claim_management_qualification('W')['ready']
    assert ports.calls==['opus','gpt','opus']
