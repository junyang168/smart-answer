"""Completed target review survives reviewed peer anchor pruning, not semantic drift."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from backend.pipeline import claim_management as cm
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.claim_qualification_reuse import anchor_pruning,argument_basis
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


def qualify_original(related,*,opus=None):
    action=related;store=action[0];request,root=request_for(action,'C',['statement'])
    def proposal(packet):
        result=cm.existing_candidate(packet['before'])
        result.finding_resolutions=[context_resolution(fid,f) for fid,f in packet['unresolved'].items()]
        return result.model_dump(mode='json')
    ports=SyntheticPorts(store,root,opus=opus,proposal=proposal if opus else None)
    result=execute_claim_management(store,request,root=root,ports=ports)
    assert result['status']=='completed',result
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id));rid=next(iter(parent['latest_rounds'].values()))
    return request,root,rid,deepcopy(store.get_cvp_group(rid))


def prune_peer(related,*,semantic=False,expected_status="completed",context=None):
    action=related;store=action[0];request,root=request_for(action,'D',['occurrences','statement'])
    def opus(packet):
        if packet['round_number']:return context(packet) if context else passing(packet)
        result=rejected(packet,key='claims/D')
        if context:
            extra=context(packet);result['findings']+=extra['findings']
            for verdict in result['verdicts']:
                if verdict['record_key']=='claims/P':verdict.update(approved=False,problems=['occurrence mismatch'])
        return result
    def proposal(packet):
        result=cm.existing_candidate(packet['before']);post=deepcopy(packet['before']['claims/D']['payload']);post['occurrences'][0]['anchors'].pop()
        if semantic:post['statement']='另一个判断。'
        result.records=[cm.source.Row(collection='claims',object_id='D',payload=post)]
        for d in result.dispositions:
            if d.record_key=='claims/D':d.action='update';d.reason='remove peer-only locator';d.citations=[cm.Citation(source_id='S',paragraph_key='S0001',exact_quote='甲是假设，不是断言。')]
        result.finding_resolutions=[cm.FindingResolution(finding_id=fid,disposition='resolved',reason='pruned wrong peer locator',citations=[cm.Citation.model_validate(f['citations'][0])]) for fid,f in packet['unresolved'].items()]
        if context:
            result.finding_resolutions=[context_resolution(r.finding_id,packet['unresolved'][r.finding_id])
                if packet['unresolved'][r.finding_id]['target_key']=='claims/P' else r
                for r in result.finding_resolutions]
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


@pytest.fixture
def downstream(related):
    store=related[0]
    relation=deepcopy(store.get_record_state('claim_relations','R')['payload'])
    relation.update(from_id='C',to_id='D')
    store.apply_plan(store.plan_package({'package_id':'downstream-direction','claim_relations':[relation]}))
    return related


def test_reviewed_downstream_semantic_change_preserves_real_target_without_calls(downstream):
    store=downstream[0];request,root,rid,old=qualify_original(downstream)
    target=store.get_record_state('claims','C')
    peer,ports=prune_peer(downstream,semantic=True)
    proof=store.get_cvp_group(rid)['reviewed_context_reuse']
    assert proof['schema_version']=='wang_claim_review_context_reuse_v2'
    assert proof['changed_context_keys']==['claims/D']
    assert {'claims/C','evidence_steps/E','claim_relations/R','source_documents/S'}<=set(proof['unchanged_argument_basis'])
    assert 'claims/D' not in proof['unchanged_argument_basis']
    assert store.get_cvp_group(rid)['audited_pins']==old['audited_pins']
    assert store.get_cvp_group(rid)['stage_results']==old['stage_results']
    assert store.get_record_state('claims','C')==target
    assert store.claim_management_qualification('C')['ready']
    assert store.claim_management_qualification('D')['ready']
    calls=list(ports.calls)
    assert store.revalidate_claim_management_context(rid,peer)==proof
    assert execute_claim_management(store,request,root=root,ports=ports)['status']=='completed'
    assert ports.calls==calls
    with store.connect() as c:
        assert c.execute("SELECT count(*) FROM wang_knowledge.review_events WHERE decision='review_context_revalidated'").fetchone()==(1,)


def test_reviewed_context_does_not_qualify_an_unreviewed_bystander(downstream):
    store=downstream[0];prune_peer(downstream,semantic=True)
    assert store.claim_management_qualification('C') is None


@pytest.mark.parametrize('change',['missing_verdict','rejected_verdict','missing_answer','history_missing','history_sha','scope_expanded'])
def test_context_reuse_requires_full_current_review_and_exact_history(downstream,monkeypatch,change):
    from backend.pipeline import claim_qualification_reuse as reuse
    store=downstream[0];_,_,rid,_=qualify_original(downstream)
    complete=reuse.after_completion
    monkeypatch.setattr(reuse,'after_completion',lambda *a:[])
    peer,_=prune_peer(downstream,semantic=True)
    monkeypatch.setattr(reuse,'after_completion',complete)
    evidence=reuse.evidence
    def changed_evidence(value):
        bound,raw=evidence(value);raw=deepcopy(raw)
        if value['materials']['round_id']==peer:
            if change=='missing_verdict':raw['verdicts']=[v for v in raw['verdicts'] if v['record_key']!='claims/C']
            elif change=='rejected_verdict':next(v for v in raw['verdicts'] if v['record_key']=='claims/C')['approved']=False
            elif change=='missing_answer':raw['answered_questions']=[]
        return bound,raw
    monkeypatch.setattr(reuse,'evidence',changed_evidence)
    with store.connect() as c:
        if change=='history_missing':c.execute("DELETE FROM wang_knowledge.object_versions WHERE collection='claims' AND object_id='D' AND revision=1")
        elif change=='history_sha':c.execute("UPDATE wang_knowledge.object_versions SET content_sha256=%s WHERE collection='claims' AND object_id='D' AND revision=1",('0'*64,))
        elif change=='scope_expanded':
            value=store.get_cvp_group(peer);value.pop('run_id');value['audited_pins'].append({'collection':'claims','object_id':'not-read','pin':[1,'0'*64,False]})
            c.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb WHERE run_id=%s',(json.dumps(value),peer))
    with pytest.raises((ValueError,RuntimeError)):
        store.revalidate_claim_management_context(rid,peer)
    assert not store.claim_management_qualification('C')['ready']
    assert 'reviewed_context_reuse' not in store.get_cvp_group(rid)


@pytest.mark.parametrize('change',['source_file','target_hold','new_relation','peer_raw_tamper','peer_review_invalidated'])
def test_context_proof_still_checks_current_sources_holds_scope_and_review(downstream,change):
    store=downstream[0];request,_,rid,_=qualify_original(downstream);peer,_=prune_peer(downstream,semantic=True)
    assert store.claim_management_qualification('C')['ready']
    if change=='source_file':
        p=Path(request.source_bindings['S'].path);p.write_text(p.read_text()+' ')
    elif change=='target_hold':
        p=deepcopy(store.get_record_state('claims','C')['payload']);p['review_status']='human_hold'
        store.apply_plan(store.plan_package({'package_id':'context-target-hold','claims':[p]}))
    elif change=='new_relation':
        store.apply_plan(store.plan_package({'package_id':'new-basis-edge','claim_relations':[{'claim_relation_id':'new','from_id':'D','to_id':'C','relation_type':'supports'}]}))
    elif change=='peer_raw_tamper':
        p=Path(store.get_cvp_group(peer)['reviewed']['evidence']['approval']['path']);p.write_text(p.read_text()+' ')
    else:
        with store.connect() as c:c.execute("UPDATE wang_knowledge.pipeline_runs SET metadata=jsonb_set(metadata,'{revisit_required}','true') WHERE run_id=%s",(peer,))
    assert not store.claim_management_qualification('C')['ready']


def test_argument_basis_follows_upstream_and_protects_outgoing_edges():
    records={'claims/C':{'claim_id':'C','evidence_step_ids':['E']},'claims/D':{'claim_id':'D'},
        'claims/P':{'claim_id':'P'},'evidence_steps/E':{'evidence_step_id':'E','produced_claim_ids':['C']},
        'claim_relations/out':{'from_id':'C','to_id':'D','relation_type':'supports'},
        'claim_relations/in':{'from_id':'P','to_id':'C','relation_type':'supports'}}
    basis=argument_basis({'claims/C'},records)
    assert basis==set(records)-{'claims/D'}
    records['claim_relations/out']['relation_type']='unknown-direction'
    assert argument_basis({'claims/C'},records)==set(records)


def change_downstream_graph(action,*,new_owner=False,expected_status='completed'):
    store=action[0];request,root=request_for(action,'D',['statement','occurrences'])
    for col,oid,fields in [('evidence_steps','F',['statement','produced_claim_ids']),
                           ('knowledge_relations','RF',['reason']),('questions','Q',['answer_state'])]:
        request.permissions.append(cm.source.Permission(collection=col,object_id=oid,fields=fields))
    file=Path(request.authorization.path);auth=json.loads(file.read_text())
    auth['request']=request.model_dump(mode='json',exclude={'authorization','policy'})
    file.write_text(json.dumps(auth));request.authorization=cm.source.FileBinding.model_validate(binding(file))
    def proposal(packet):
        result=cm.existing_candidate(packet['before'])
        updates={'claims/D':{'statement':'另一个判断。'},'evidence_steps/F':{'statement':'修正后的下游证据。'},
                 'knowledge_relations/RF':{'reason':'修正后的下游支持理由。'},'questions/Q':{'answer_state':'partially_answered'}}
        if new_owner:updates['evidence_steps/F']['produced_claim_ids']=['D','C']
        for key,fields in updates.items():
            payload={**packet['before'][key]['payload'],**fields};col,oid=key.split('/',1)
            result.records.append(cm.source.Row(collection=col,object_id=oid,payload=payload))
            d=next(d for d in result.dispositions if d.record_key==key)
            d.action='update';d.reason='synthetic full-context correction'
            d.citations=[cm.Citation(source_id='S',paragraph_key='S0001',exact_quote='甲是假设，不是断言。')]
        result.finding_resolutions=[cm.FindingResolution(finding_id=fid,disposition='resolved',reason='synthetic correction',
            citations=[cm.Citation.model_validate(f['citations'][0])]) for fid,f in packet['unresolved'].items()]
        return result.model_dump(mode='json')
    ports=SyntheticPorts(store,root,proposal=proposal,opus=lambda p:rejected(p,key='claims/D') if not p['round_number'] else passing(p))
    result=execute_claim_management(store,request,root=root,ports=ports)
    assert result['status']==expected_status,result
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))
    return next(iter(parent['latest_rounds'].values())),ports


@pytest.fixture
def downstream_graph(downstream):
    store=downstream[0];d=deepcopy(store.get_record_state('claims','D')['payload']);d['evidence_step_ids']=['F']
    store.apply_plan(store.plan_package({'package_id':'downstream-content-graph','claims':[d],
        'evidence_steps':[{'evidence_step_id':'F','statement':'下游证据。','source_id':'S','produced_claim_ids':['D']}],
        'observations':[{'observation_id':'O','statement':'下游观察。','source_id':'S'}],
        'knowledge_relations':[{'relation_id':'RF','from_id':'O','to_id':'F','relation_type':'supports','reason':'下游支持'}],
        'questions':[{'question_id':'Q','question':'下游问题？','source_id':'S','answer_claim_ids':['D'],'answer_state':'answered'}]}))
    return downstream


def test_full_context_evidence_relation_question_updates_do_not_restart_old_target(downstream_graph):
    store=downstream_graph[0];_,_,rid,old=qualify_original(downstream_graph)
    peer,ports=change_downstream_graph(downstream_graph)
    proof=store.get_cvp_group(rid)['reviewed_context_reuse']
    assert set(proof['changed_context_keys'])=={'claims/D','evidence_steps/F','knowledge_relations/RF','questions/Q'}
    assert proof['schema_version']=='wang_claim_review_context_reuse_v2'
    assert store.claim_management_qualification('C')['ready'] and store.claim_management_qualification('D')['ready']
    assert ports.calls==['opus','gpt','opus']
    assert store.get_cvp_group(rid)['audited_pins']==old['audited_pins']
    assert store.revalidate_claim_management_context(rid,peer)==proof
    assert ports.calls==['opus','gpt','opus']


def test_new_implicit_owner_cannot_be_hidden_as_context_change(downstream_graph):
    store=downstream_graph[0];_,_,rid,_=qualify_original(downstream_graph)
    change_downstream_graph(downstream_graph,new_owner=True,expected_status='paused')
    assert store.claim_management_qualification('C')['ready']
    assert store.get_record_state('evidence_steps','F')['payload']['produced_claim_ids']==['D']
    assert 'reviewed_context_reuse' not in store.get_cvp_group(rid)


def test_argument_basis_includes_implicit_evidence_owners():
    records={'claims/C':{'claim_id':'C','evidence_step_ids':[]},
             'evidence_steps/F':{'evidence_step_id':'F','produced_claim_ids':['D']}}
    assert argument_basis({'claims/C'},records)=={'claims/C'}
    records['evidence_steps/F']['produced_claim_ids'].append('C')
    assert argument_basis({'claims/C'},records)==set(records)


def test_context_proof_failure_rolls_back_and_saved_completion_recovers_without_calls(downstream,monkeypatch):
    from backend.pipeline import claim_qualification_reuse as reuse
    store=downstream[0];_,_,rid,_=qualify_original(downstream);admit=reuse.admit
    def fail(*args,**kw):
        admit(*args,**kw);raise RuntimeError('after context reuse event')
    monkeypatch.setattr(reuse,'admit',fail)
    peer,ports=prune_peer(downstream,semantic=True,expected_status='paused')
    assert 'reviewed_context_reuse' not in store.get_cvp_group(rid)
    with store.connect() as c:assert c.execute("SELECT count(*) FROM wang_knowledge.review_events WHERE decision='review_context_revalidated'").fetchone()==(0,)
    monkeypatch.setattr(reuse,'admit',admit);calls=list(ports.calls)
    request=cm.Request.model_validate(store.get_cvp_group(peer)['materials']['request'])
    assert execute_claim_management(store,request,root=ports.tmp_path,ports=ports)['status']=='completed'
    assert store.claim_management_qualification('C')['ready'] and ports.calls==calls


def test_successive_context_updates_keep_one_direct_proof_not_a_history_chain(downstream_graph):
    store=downstream_graph[0];_,_,rid,old=qualify_original(downstream_graph)
    first,_=change_downstream_graph(downstream_graph)
    second,ports=prune_peer(downstream_graph,semantic=True)
    proof=store.get_cvp_group(rid)['reviewed_context_reuse']
    assert proof['schema_version']=='wang_claim_review_context_reuse_v2'
    assert proof['peer_round_id']==second and proof['original_round_id']==rid
    assert store.get_cvp_group(rid)['audited_pins']==old['audited_pins']
    with store.connect() as c:
        c.execute("UPDATE wang_knowledge.pipeline_runs SET metadata=jsonb_set(metadata,'{revisit_required}','true') WHERE run_id=%s",(first,))
    calls=list(ports.calls)
    assert store.claim_management_qualification('C')['ready']
    assert ports.calls==calls


def context_resolution(fid,finding):
    return cm.FindingResolution(finding_id=fid,disposition='persistent',reason='synthetic unchanged context judgment',
        citations=finding['citations'],affected_target_keys=[],affected_write_keys=[],
        impact_reason='synthetic explicit judgment for this operation')


def context_objection(packet):
    result=passing(packet)
    result['findings']=[{'target_key':'claims/P','component':'occurrences','category':'mapping',
        'reason':'synthetic unrelated occurrence mismatch','acceptance_condition':'correct the unrelated occurrence',
        'citations':[{'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}],
        'affected_target_keys':[],'affected_write_keys':[],'impact_reason':'synthetic explicit judgment for this operation'}]
    prior={fid:f for fid,f in packet['unresolved'].items() if f['target_key']=='claims/P'}
    if prior:
        result['findings']=[]
        result['prior_findings']=[context_resolution(r['finding_id'],prior[r['finding_id']]).model_dump(mode='json')
            if r['finding_id'] in prior else r for r in result['prior_findings']]
    next(v for v in result['verdicts'] if v['record_key']=='claims/P').update(approved=False,problems=['occurrence mismatch'])
    return result


@pytest.mark.parametrize('drift',[None,'event_removed','peer_review','proof','source'])
def test_exact_prior_context_objection_preserves_qualification_without_erasing_rejection(downstream,drift):
    store=downstream[0]
    store.apply_plan(store.plan_package({'package_id':'readonly-context','claims':[
        {'claim_id':'P','claim_type':'exegesis','statement':'甲是假设。','occurrences':[{'source_id':'S','anchors':[]}]}],
        'claim_relations':[{'claim_relation_id':'DP','from_id':'D','to_id':'P','relation_type':'supports'}]}))
    request,root,rid,old=qualify_original(downstream,opus=context_objection)
    peer,ports=prune_peer(downstream,semantic=True,context=context_objection)
    current=store.get_cvp_group(rid);proof=current['reviewed_context_reuse']
    assert proof['schema_version']=='wang_claim_review_context_reuse_v3'
    assert proof['retained_context_objections'][0]['record_key']=='claims/P'
    assert proof['retained_context_objections'][0]['basis']=='unchanged_original_context'
    assert current['context_findings']==old['context_findings']
    assert current['audited_pins']==old['audited_pins']
    assert store.claim_management_qualification('C')['ready']
    assert not store.claim_management_qualification('P')['ready']
    calls=list(ports.calls)
    assert store.revalidate_claim_management_context(rid,peer)==proof
    assert execute_claim_management(store,request,root=root,ports=ports)['status']=='completed'
    assert ports.calls==calls
    if drift=='source':
        p=Path(request.source_bindings['S'].path);p.write_text(p.read_text()+' ')
    elif drift:
        with store.connect() as conn:
            if drift=='event_removed':conn.execute("DELETE FROM wang_knowledge.review_events WHERE decision='review_context_revalidated'")
            elif drift=='peer_review':conn.execute("UPDATE wang_knowledge.pipeline_runs SET metadata=jsonb_set(metadata,'{revisit_required}','true') WHERE run_id=%s",(peer,))
            else:
                value=store.get_cvp_group(rid);value.pop('run_id')
                value['reviewed_context_reuse']['retained_context_objections'][0]['signature']='0'*64
                conn.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb WHERE run_id=%s',(json.dumps(value),rid))
    if drift:assert not store.claim_management_qualification('C')['ready']


def objection_fixture():
    finding=context_objection({'candidate_sha256':'0'*64,'denominator':['claims/P'],'unresolved':{},
        'original_goal':{'required_questions':[]}})['findings'][0]
    value={'materials':{'original_goal':{'target_keys':['claims/C']},'unresolved':{}}}
    review={'findings':[finding],'prior_findings':[],
        'verdicts':[{'record_key':'claims/P','approved':False,'problems':['mismatch']}]}
    return value,review


@pytest.mark.parametrize('change',['new','reason','component','citation','acceptance','target_impact',
    'write_impact','missing_impact','missing_write_impact','missing_explanation','changed_object',
    'protected_object','unexplained_rejection','peer_scope_only','reopened'])
def test_scoped_reuse_rejects_new_expanded_or_unproven_objections(change):
    from backend.pipeline.claim_qualification_reuse import scoped_objections
    old,previous=objection_fixture();peer=deepcopy(old);review=deepcopy(previous)
    finding=review['findings'][0];protected={'claims/C'};changed=set();basis={'claims/C','claims/P'}
    if change=='new':previous['findings']=[]
    elif change=='reason':finding['reason']='a broader problem'
    elif change=='component':finding['component']='statement'
    elif change=='citation':finding['citations'][0]['exact_quote']='甲是假设'
    elif change=='acceptance':finding['acceptance_condition']='replace the whole argument'
    elif change=='target_impact':finding['affected_target_keys']=['claims/C']
    elif change=='write_impact':finding['affected_write_keys']=['evidence_steps/E']
    elif change=='missing_impact':finding.pop('affected_target_keys')
    elif change=='missing_write_impact':finding.pop('affected_write_keys')
    elif change=='missing_explanation':finding['impact_reason']=' '
    elif change=='changed_object':changed.add('claims/P')
    elif change=='protected_object':protected.add('claims/P')
    elif change=='unexplained_rejection':review['verdicts'].append({'record_key':'claims/X','approved':False,'problems':['unknown']})
    elif change=='peer_scope_only':
        previous['findings']=[];basis={'claims/C'};peer['materials']['original_goal']['target_keys']=['claims/D']
    elif change=='reopened':
        peer['materials']['unresolved']={'F':finding};review['findings']=[]
        review['prior_findings']=[{'finding_id':'F','disposition':'reopened','reason':'new concern',
            'citations':finding['citations'],'affected_target_keys':[],'affected_write_keys':[],
            'impact_reason':'does not affect peer'}]
    with pytest.raises(ValueError):scoped_objections(old,peer,previous,review,basis,protected,changed)


def test_new_context_objection_requires_explicit_review_of_original_targets_and_is_outside_basis():
    from backend.pipeline.claim_qualification_reuse import scoped_objections
    old,review=objection_fixture();peer=deepcopy(old);previous=deepcopy(review);previous['findings']=[]
    peer['materials']['original_goal']['target_keys']=['claims/C','claims/D']
    proof=scoped_objections(old,peer,previous,review,{'claims/C'},{'claims/C'},set())
    assert proof[0]['basis']=='new_context_outside_original_basis'
