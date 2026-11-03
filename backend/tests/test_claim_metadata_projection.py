"""Real SQL sampling boundaries and lossless support identity projection."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.pipeline import claim_management as cm,claim_management_intake as intake,claim_management_batch as batch
from backend.pipeline.claim_metadata_projection import FIELD,CONTRACT,project_support_aliases
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.tests.test_claim_management_intake import claims,pass_review
from backend.tests.test_claim_management_postgres import action,postgres_store,_temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts


def seed_marker(store,fault):
    store.record_review('claims','C',decision='human_review_required',reviewer_kind='ai',reviewer_id='legacy-model')
    state=store.get_record_state('claims','C')
    resolution={'schema_version':'wang_legacy_ai_review_reconciliation_v1','claim_id':'C',
        'approval_status':'not_human_approved','independent_review_decision':'pass',
        'adjudication_status':'human_spot_check','target_review_status':'human_review_required'}
    if fault=='source_uncertain':resolution['adjudication_status']='human_confirmation'
    if fault=='failed_review':resolution['independent_review_decision']='changes_suggested'
    if fault=='wrong_claim':resolution['claim_id']='OTHER'
    if fault=='ordinary_hold':resolution={}
    with store.connect() as conn:
        conn.execute("UPDATE wang_knowledge.review_events SET artifact=%s::jsonb,reviewer_kind=%s,object_revision=%s WHERE collection='claims' AND object_id='C'",
            (json.dumps({'resolution':resolution}),'human' if fault=='human' else 'ai',state['revision']-1 if fault=='wrong_revision' else state['revision']))
    return store.get_record_state('claims','C')


@pytest.mark.parametrize('fault',[None,'human','source_uncertain','failed_review','wrong_revision','wrong_claim','ordinary_hold','human_after_proposal','human_before_commit'])
def test_only_verified_ai_sampling_can_enter_content_update(claims,monkeypatch,fault):
    store,root=claims;race=fault in {'human_after_proposal','human_before_commit'};old=seed_marker(store,None if race else fault)
    prepared=intake.prepare(['C'],root,instruction='Full professor-source Update',store=store)
    if fault and not race:
        assert prepared['excluded']['C']['reason']=='human_hold'
        assert not prepared['manifest']
        assert store.get_record_state('claims','C')==old
        return
    _,jobs=batch.load(prepared['manifest']['path'],root/'batch',store=store)
    request,execution=next(iter(jobs.values()));citation={'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}
    with store.connect() as conn:events=conn.execute("SELECT object_revision,reviewer_kind,decision,artifact FROM wang_knowledge.review_events WHERE collection='claims' AND object_id='C' ORDER BY review_event_id").fetchall()
    def decisions(packet):return [{'finding_id':fid,'disposition':'resolved','reason':'restore the complete professor distinction','citations':[citation],
        'affected_target_keys':['claims/C'],'affected_write_keys':['claims/C'],'impact_reason':'the actual statement is corrected'} for fid in packet['unresolved']]
    def review(packet):
        result=pass_review(packet);result['prior_findings']=decisions(packet)
        if not packet['unresolved']:
            result['findings']=[{'target_key':'claims/C','category':'content','component':'statement','reason':'the explicit negative qualification is missing',
                'acceptance_condition':'restore the complete statement','citations':[citation],'affected_target_keys':['claims/C'],'affected_write_keys':[],
                'impact_reason':'original target teaching incomplete'}]
        else:
            row=next(r for r in packet['candidate']['records'] if r['object_id']=='C')
            assert row['payload']['review_status']=='candidate'
            assert row['payload']['reviewed_by']==old['payload']['reviewed_by']
        return result
    def add_human_event():
        # Simulate an imported human decision without an object revision bump:
        # pins alone cannot detect this history change.
        with store.connect() as conn:
            conn.execute("INSERT INTO wang_knowledge.review_events (review_event_id,collection,object_id,object_revision,reviewer_kind,reviewer_id,decision,reason,artifact) VALUES (%s,'claims','C',%s,'human','owner','human_review_required','external human decision','{}'::jsonb)",('HUMAN-'+fault,old['revision']))
    apply=store.apply_plan
    if fault=='human_before_commit':
        def raced_apply(plan,**kwargs):
            if plan.source_kind=='wang_claim_management_v2':add_human_event()
            return apply(plan,**kwargs)
        monkeypatch.setattr(store,'apply_plan',raced_apply)
    def proposal(packet):
        if fault=='human_after_proposal':add_human_event()
        assert packet['before']['claims/C']['payload']['review_status']=='human_review_required'
        return {'changes':[{'record_key':'claims/C','fields_json':json.dumps({'statement':citation['exact_quote']}),'reason':'restore explicit qualification','citations':[citation]}],
            'creations':[],'retirements':[],'findings':[],'finding_resolutions':decisions(packet)}
    ports=SyntheticPorts(store,execution,opus=review,proposal=proposal)
    result=execute_claim_management(store,request,root=execution,ports=ports)
    if race:
        assert result['status']=='paused',result
        assert 'metadata projection or review history drift' in result.get('detail',''),result
        assert ports.calls==(['opus','gpt'] if fault=='human_after_proposal' else ['opus','gpt','opus'])
        assert store.get_record_state('claims','C')==old
        return
    assert result['status']=='completed',result
    assert ports.calls==['opus','gpt','opus']
    after=store.get_record_state('claims','C')
    assert after['payload']['review_status']=='candidate' and after['payload']['statement']==citation['exact_quote']
    assert store.claim_management_qualification('C')['ready']
    with store.connect() as conn:assert conn.execute("SELECT object_revision,reviewer_kind,decision,artifact FROM wang_knowledge.review_events WHERE collection='claims' AND object_id='C' ORDER BY review_event_id").fetchall()==events


def alias_materials():
    return {FIELD:{'contract':CONTRACT,'sampling_markers':{},'support_identities':True},'before':{},
        'policy':{'model_output_contract':'claim_changes_v4'},'unit_request':{'action_id':'A','creation_permissions':[{'collection':c} for c in ['source_fragments','evidence_steps']]}}


def test_support_aliases_change_only_identities_and_typed_references():
    raw={'changes':[{'record_key':'claims/C','fields_json':json.dumps({'evidence_step_ids':['E'],'statement':'E and F are words',
         'occurrences':[{'canonical_evidence_step_ids':['E'],'local_source_evidence_ids':['legacy-E'],
                         'anchors':[{'source_fragment_id':'F','evidence_id':'E','proposed_highlight':{'text':'E and F are words'}}]}]})}],
         'creations':[{'collection':'source_fragments','object_id':'F','fields_json':json.dumps({'source_id':'S','verbatim_excerpt':'E and F are words'})},
                      {'collection':'evidence_steps','object_id':'E','fields_json':json.dumps({'source_fragment_ids':['F'],'statement':'E and F are words'})}]}
    original=deepcopy(raw);result,mappings=project_support_aliases(raw,alias_materials());ids={m['alias']:m['object_id'] for m in mappings}
    assert raw==original and ids['F']!=ids['E']
    c=json.loads(result['changes'][0]['fields_json']);assert c['evidence_step_ids']==[ids['E']] and c['statement']=='E and F are words'
    assert c['occurrences'][0]['anchors'][0]['source_fragment_id']==ids['F']
    assert c['occurrences'][0]['anchors'][0]['evidence_id']==ids['E']
    assert c['occurrences'][0]['canonical_evidence_step_ids']==[ids['E']]
    assert c['occurrences'][0]['local_source_evidence_ids']==['legacy-E']
    assert c['occurrences'][0]['anchors'][0]['proposed_highlight']['text']=='E and F are words'
    assert json.loads(result['creations'][0]['fields_json'])=={'source_id':'S','verbatim_excerpt':'E and F are words'}
    assert json.loads(result['creations'][1]['fields_json'])=={'source_fragment_ids':[ids['F']],'statement':'E and F are words'}
    assert project_support_aliases(raw,alias_materials())== (result,mappings)
    renamed=deepcopy(raw)
    renamed['creations'][0]['object_id']='U';renamed['creations'][1]['object_id']='V'
    e=json.loads(renamed['creations'][1]['fields_json']);e['source_fragment_ids']=['U'];renamed['creations'][1]['fields_json']=json.dumps(e)
    c=json.loads(renamed['changes'][0]['fields_json']);c['evidence_step_ids']=['V'];c['occurrences'][0]['anchors'][0]['source_fragment_id']='U'
    c['occurrences'][0]['anchors'][0]['evidence_id']='V';c['occurrences'][0]['canonical_evidence_step_ids']=['V'];renamed['changes'][0]['fields_json']=json.dumps(c)
    renamed['creations'].reverse()
    same,renamed_maps=project_support_aliases(renamed,alias_materials())
    assert {m['object_id'] for m in renamed_maps}==set(ids.values())
    assert same['changes']==result['changes']
    different=alias_materials();different['unit_request']['action_id']='B'
    assert project_support_aliases(raw,different)[1]!=mappings


def test_new_claim_anchor_references_use_only_the_evidence_identity_mapping():
    raw={'creations':[
        {'collection':'source_fragments','object_id':'F','fields_json':json.dumps({'source_id':'S','verbatim_excerpt':'E'})},
        {'collection':'evidence_steps','object_id':'E','fields_json':json.dumps({'statement':'E','source_fragment_ids':['F']})},
        {'collection':'claims','object_id':'C','fields_json':json.dumps({'statement':'E','evidence_step_ids':['E'],
            'occurrences':[{'anchors':[{'evidence_id':'E','evidence_step_id':'E','source_fragment_id':'F'},
                                      {'evidence_id':'F','source_fragment_id':'E'}]}]})}]}
    original=deepcopy(raw);projected,mappings=project_support_aliases(raw,alias_materials())
    ids={m['alias']:m['object_id'] for m in mappings}
    claim=json.loads(projected['creations'][2]['fields_json'])
    assert claim['evidence_step_ids']==[ids['E']]
    assert claim['occurrences'][0]['anchors'][0]=={
        'evidence_id':ids['E'],'evidence_step_id':ids['E'],'source_fragment_id':ids['F']}
    # Projection must not reinterpret an ID of the wrong collection or prose.
    assert claim['occurrences'][0]['anchors'][1]=={'evidence_id':'F','source_fragment_id':'E'}
    assert claim['statement']=='E' and raw==original


@pytest.mark.parametrize('fault', [None, 'teaching', 'disposition', 'identity', 'mapping', 'normalization'])
def test_accepted_support_reference_refresh_cannot_reinterpret_a_proposal(fault):
    from backend.pipeline.claim_metadata_projection import accepted_support_reference_projection
    old = cm.Candidate(records=[cm.source.Row(collection='claims', object_id='C', payload={
        'claim_id': 'C', 'statement': 'E remains literal teaching',
        'occurrences': [{'anchors': [{'evidence_id': 'E'}]}]})], dispositions=[])
    new = old.model_copy(deep=True)
    new.records[0].payload['occurrences'][0]['anchors'][0]['evidence_id'] = 'CME-final'
    mappings = [{'collection': 'evidence_steps', 'alias': 'E', 'object_id': 'CME-final'}]
    a = {'candidate_sha256': old.digest, 'raw_candidate_sha256': 'old',
         'model_response_sha256': 'same raw', 'support_creation_identities': mappings}
    b = {**a, 'candidate_sha256': new.digest, 'raw_candidate_sha256': 'new'}
    if fault == 'teaching': new.records[0].payload['statement'] += ' changed'
    if fault == 'disposition':
        new.dispositions.append(cm.ObjectDisposition(record_key='claims/C', action='update', reason='new decision'))
    if fault == 'identity': new.records[0].object_id = 'D'
    if fault == 'mapping': b['support_creation_identities'] = []
    if fault == 'normalization': b['model_response_sha256'] = 'different raw'
    b['candidate_sha256'] = new.digest
    if fault:
        with pytest.raises(ValueError): accepted_support_reference_projection(old, new, a, b)
    else:
        proof = accepted_support_reference_projection(old, new, a, b)
        assert proof['paths'] == [['records', 0, 'payload', 'occurrences', 0, 'anchors', 0, 'evidence_id']]
        assert old.records[0].payload['occurrences'][0]['anchors'][0]['evidence_id'] == 'E'


@pytest.mark.parametrize('fault',['known_id','duplicate_alias','duplicate_json','no_grant','empty_alias','padded_alias'])
def test_support_projection_refuses_ambiguous_or_invalid_input(fault):
    m=alias_materials();raw={'creations':[{'collection':'source_fragments','object_id':'F','fields_json':'{"source_id":"S"}'}]}
    if fault=='known_id':m['before']['claims/F']={}
    if fault=='duplicate_alias':raw['creations'].append({'collection':'claims','object_id':'F','fields_json':'{"statement":"S"}'})
    if fault=='duplicate_json':raw['creations'][0]['fields_json']='{"source_id":"S","source_id":"OTHER"}'
    if fault=='no_grant':m['unit_request']['creation_permissions']=[]
    if fault=='empty_alias':raw['creations'][0]['object_id']=''
    if fault=='padded_alias':raw['creations'][0]['object_id']=' F '
    with pytest.raises(ValueError):project_support_aliases(raw,m)


class DurableSyntheticPorts(SyntheticPorts):
    """Fixture-only successful CLI evidence; never invokes a provider."""
    def claim_management_adapter(self,role,policy):
        adapter=super().claim_management_adapter(role,policy)
        if role!='gpt':return adapter
        import sys
        from backend.pipeline.subscription_process import digest_file
        adapter._client.executable=sys.executable
        generate=adapter.generate
        def fake(packet):
            response=generate(packet)
            transport=self.tmp_path/packet['round_id']/'gpt.transport';transport.mkdir(parents=True,exist_ok=True)
            provider={'stdout':'synthetic successful GPT transport','stderr':'','last_message':json.dumps(response)}
            adapter._client.last_raw_response=provider
            (transport/'stdout.log').write_text(provider['stdout']);(transport/'stderr.log').write_text('')
            (transport/'response.json').write_text(json.dumps(response))
            (transport/'invocation.json').write_text(json.dumps({'command_sha256':'b'*64,'input_sha256':'c'*64,
                'executable_sha256':digest_file(Path(sys.executable).resolve()),'cwd':str(transport)}))
            (transport/'exit.json').write_text(json.dumps({'returncode':0,'files':{n:digest_file(transport/n) for n in ['stdout.log','stderr.log','response.json']}}))
            return response
        adapter.generate=fake
        return adapter


@pytest.mark.parametrize('case',['sampling','supports'])
def test_saved_failed_proposal_finishes_with_only_its_original_unused_opus(claims,monkeypatch,case):
    from backend.pipeline import claim_metadata_projection as projection
    from backend.pipeline.claim_review_recovery import recover_returned_candidate,CANDIDATE_IDENTITY_VERSION
    from backend.pipeline.cvp_sql_commit import binding
    store,root=claims
    if case=='sampling':seed_marker(store,None)
    else:
        doc=deepcopy(store.get_record_state('source_documents','S')['payload']);source=Path(doc['source_path'])
        source.write_text(json.dumps({'script':[{'text':'甲是假设，不是断言。'},{'text':'乙是结论。'}]},ensure_ascii=False));doc['source_file_sha256']=binding(source)['file_sha256']
        store.apply_plan(store.plan_package({'package_id':'occupied', 'source_documents':[doc],
            'source_fragments':[{'fragment_id':'NEW-F','source_id':'S','paragraph_key':'S0001','verbatim_excerpt':'甲是假设，不是断言。'}],
            'evidence_steps':[{'evidence_step_id':'NEW-E','statement':'unrelated old teaching','source_fragment_ids':['NEW-F'],'produced_claim_ids':[]}]}))
    prepared=intake.prepare(['C'],root,instruction='Full Update',store=store)
    request,execution=next(iter(batch.load(prepared['manifest']['path'],root/'batch',store=store)[1].values()))
    policy=cm.Policy.model_validate_json(Path(request.policy.path).read_text());policy.initial_candidate='proposal';policy.budget.gpt_calls=1;policy.budget.opus_calls=1;policy.budget.max_revisions=1
    policy_path=root/'one-pair-policy.json';policy_path.write_text(policy.model_dump_json());request.policy=cm.source.FileBinding.model_validate(binding(policy_path))
    authority=json.loads(Path(request.authorization.path).read_text());authority.update(request=request.model_dump(mode='json',exclude={'authorization','policy'}),policy=request.policy.model_dump(mode='json'))
    auth_path=root/'one-pair-authority.json';auth_path.write_text(json.dumps(authority));request.authorization=cm.source.FileBinding.model_validate(binding(auth_path))
    def proposal(packet):
        cit={'source_id':'S','paragraph_key':'S0001' if case=='sampling' else 'S0002','exact_quote':'甲是假设，不是断言。' if case=='sampling' else '乙是结论。'}
        value={'changes':[],'creations':[],'retirements':[],'findings':[],'finding_resolutions':[]}
        if case=='sampling':value['changes']=[{'record_key':'claims/C','fields_json':json.dumps({'statement':cit['exact_quote']}),'citations':[cit],'reason':'restore the qualification'}]
        else:
            from backend.api.canonical_repository.claim_identity_allocation import allocated_ids
            cid=allocated_ids(packet['original_goal']['claim_creation_allocations'])[0]
            for col,oid,payload in [('source_fragments','NEW-F',{'source_id':'S','paragraph_key':'S0002','verbatim_excerpt':cit['exact_quote']}),
                ('evidence_steps','NEW-E',{'statement':cit['exact_quote'],'source_fragment_ids':['NEW-F'],'produced_claim_ids':[cid],'speaker':'professor','stance':'asserted','support_eligibility':'eligible_candidate'}),
                ('claims',cid,{'claim_type':'exegesis','attribution':'professor','statement':cit['exact_quote'],'evidence_step_ids':['NEW-E'],
                    'occurrences':[{'source_id':'S','anchors':[{'paragraph_key':'S0002','evidence_id':'NEW-E',
                        'proposed_highlight':{'text':cit['exact_quote']}}]}]})]:
                value['creations'].append({'collection':col,'object_id':oid,'fields_json':json.dumps(payload),'citations':[cit],'reason':'record an independent professor judgment'})
        return value
    real_compile=projection.compile_projection;calls=[]
    def old_rounds(*args):
        calls.append(1)
        return real_compile(*args) if len(calls)==1 else None
    monkeypatch.setattr(projection,'compile_projection',old_rounds)
    ports=DurableSyntheticPorts(store,execution,proposal=proposal,opus=pass_review)
    failed=execute_claim_management(store,request,root=execution,ports=ports)
    assert failed['status']=='paused' and failed['reason']=='engineering_required',failed
    assert ports.calls==['gpt']
    assert ('protected review hold' if case=='sampling' else 'create identity already exists') in failed['detail']
    monkeypatch.setattr(projection,'compile_projection',real_compile)
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id));rid=next(iter(parent['latest_rounds'].values()));child=store.get_cvp_group(rid)
    raw_path=Path(parent['execution_root'])/rid/'gpt.raw.json';raw_before=raw_path.read_bytes()
    clean=lambda v:{k:x for k,x in v.items() if k not in {'run_id','status','error'}}
    direction={'schema_version':CANDIDATE_IDENTITY_VERSION,'round_id':rid,'authorized_by':'owner','authorization_quote':'repair both engineering boundaries and finish the same two targets',
        'human_content_approval':False,'program_metadata_projection':True,'prior_metadata_sha256':cm.sha256_json(clean(child)),
        'prior_parent_sha256':cm.sha256_json(clean(parent)),'original_raw':binding(raw_path)}
    path=root/'repair-direction.json';path.write_text(json.dumps(direction));bound=binding(path)
    untouched=store.get_cvp_group(parent['run_id'])
    for bad in ('metadata','raw','approval','missing_projection'):
        wrong=deepcopy(direction)
        if bad=='metadata':wrong['prior_metadata_sha256']='0'*64
        elif bad=='raw':wrong['original_raw']['file_sha256']='0'*64
        elif bad=='approval':wrong['human_content_approval']=True
        else:wrong.pop('program_metadata_projection')
        bad_path=root/(bad+'-direction.json');bad_path.write_text(json.dumps(wrong))
        with pytest.raises(ValueError):recover_returned_candidate(store,rid,authorization=binding(bad_path))
        assert store.get_cvp_group(parent['run_id'])==untouched
    recovered=recover_returned_candidate(store,rid,authorization=bound)
    assert recover_returned_candidate(store,rid,authorization=bound)==recovered
    resumed=SyntheticPorts(store,execution,proposal=lambda p:pytest.fail('saved GPT must not be repeated'),opus=pass_review)
    result=execute_claim_management(store,request,root=execution,ports=resumed)
    assert result['status']=='completed',result
    assert resumed.calls==['opus'] and raw_path.read_bytes()==raw_before
    after=store.get_cvp_group(parent['run_id']);assert len(after['call_reservations'])==2
    assert all(s['state']=='completed' for s in after['call_reservations'].values())
    assert after['policy']['budget']['gpt_calls']==after['policy']['budget']['opus_calls']==1
    assert store.claim_management_qualification('C')['ready']
    if case=='supports':
        assert store.get_record_state('evidence_steps','NEW-E')['payload']['statement']=='unrelated old teaching'
        for key in after['created_keys']:
            if key.startswith('claims/'):
                cid=key.split('/',1)[1]
                assert store.claim_management_qualification(cid)['ready']
                claim=store.get_record_state('claims',cid)['payload']
                assert claim['occurrences'][0]['anchors'][0]['evidence_id']==claim['evidence_step_ids'][0]


@pytest.mark.parametrize('human',[False,True])
def test_sampling_retirement_keeps_history_and_never_retires_a_human_hold(claims,human):
    from backend.pipeline.claim_metadata_projection import compile_projection
    store,root=claims;seed_marker(store,'human' if human else None)
    prepared=intake.prepare(['C'],root,instruction='Review every authorized disposition',store=store)
    if human:
        assert prepared['excluded']['C']['reason']=='human_hold'
        return
    request,_=next(iter(batch.load(prepared['manifest']['path'],root/'batch',store=store)[1].values()))
    records=store.read_claim_context([('claims','C'),('source_documents','S')]);raw={cm.source.key(*k):cm.model_state(v) for k,v in records.items()}
    before=cm.original_goal(request,raw)['original_objects'];policy=cm.Policy.model_validate_json(Path(request.policy.path).read_text())
    with store.connect() as conn:projection=compile_projection(conn.cursor(),request,before,policy)
    candidate=cm.existing_candidate(before);citation=cm.Citation(source_id='S',paragraph_key='S0001',exact_quote='甲是假设，不是断言。')
    for d in candidate.dispositions:
        if d.record_key=='claims/C':d.action='retire';d.citations=[citation]
        elif d.record_key=='evidence_steps/EC':
            d.action='update';d.citations=[citation]
            candidate.records.append(cm.source.Row(collection='evidence_steps',object_id='EC',payload={**before[d.record_key]['payload'],'produced_claim_ids':[]}))
    _,sources=cm.load_authority(request)
    _,writes=cm.validate_candidate(request,before,candidate,sources,metadata_projection=projection)
    assert writes['claims/C']['action']=='retire' and writes['claims/C']['payload']==before['claims/C']['payload']


@pytest.mark.parametrize('later_human',[False,True])
def test_full_review_reuse_recognizes_same_old_ai_spot_check_and_rechecks_human_history(claims,later_human):
    from backend.pipeline import claim_review_reuse
    store,root=claims
    before=seed_marker(store,None)
    prepared=intake.prepare(['C'],root,instruction='Full unchanged-source Claim review',store=store)
    request,execution=next(iter(batch.load(prepared['manifest']['path'],root/'batch',store=store)[1].values()))
    ports=SyntheticPorts(store,execution,opus=pass_review)
    result=execute_claim_management(store,request,root=execution,ports=ports)
    assert result['status']=='completed',result
    assert store.get_record_state('claims','C')==before
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))
    rid=next(iter(parent['latest_rounds'].values()))
    proof=claim_review_reuse.admit(store,'C',rid)
    assert store.claim_management_qualification('C')['ready']
    assert ports.calls==['opus']
    if later_human:
        with store.connect() as conn:
            conn.execute("INSERT INTO wang_knowledge.review_events (review_event_id,collection,object_id,object_revision,reviewer_kind,reviewer_id,decision,reason,artifact) VALUES ('HUMAN-LATER-REUSE','claims','C',%s,'human','owner','human_review_required','human source hold','{}'::jsonb)",(before['revision'],))
        with pytest.raises(ValueError,match='held content'):
            with store.connect() as conn,conn.cursor() as cursor:
                claim_review_reuse.verify(store,cursor,'C',proof)
        with pytest.raises(ValueError,match='held content'):
            claim_review_reuse.build(store,'C',rid)
    assert store.get_record_state('claims','C')==before


@pytest.mark.parametrize('event_kind',['context','human','ai_rejection','other_system','wrong_pin','wrong_contract'])
def test_only_mechanical_context_events_leave_ai_sampling_decision_in_effect(claims,event_kind):
    from backend.pipeline.claim_metadata_projection import sampling_marker
    store,_=claims;state=seed_marker(store,None)
    with store.connect() as conn,conn.cursor() as cursor:
        original=sampling_marker(cursor,'C',state)
    artifact={'schema_version':'wang_claim_review_context_reuse_v4','original_round_id':'old','peer_round_id':'peer',
        'original_approval':{'path':'old'},'peer_approval':{'path':'peer'},
        'current_pins':[{'collection':'claims','object_id':'C','pin':[state['revision'],state['content_sha256'],False]}]}
    if event_kind=='wrong_pin':artifact['current_pins'][0]['pin'][1]='0'*64
    if event_kind=='wrong_contract':artifact['schema_version']='unrecognized'
    kind='human' if event_kind=='human' else 'ai' if event_kind=='ai_rejection' else 'system'
    decision='rejected' if event_kind=='ai_rejection' else 'human_review_required' if event_kind in {'human','other_system'} else 'review_context_revalidated'
    with store.connect() as conn:
        conn.execute("INSERT INTO wang_knowledge.review_events (review_event_id,collection,object_id,object_revision,reviewer_kind,reviewer_id,decision,reason,artifact) VALUES ('LATER-MARKER-EVENT','claims','C',%s,%s,'fixture',%s,'new history',%s::jsonb)",(state['revision'],kind,decision,json.dumps(artifact)))
    with store.connect() as conn,conn.cursor() as cursor:
        current=sampling_marker(cursor,'C',state)
    assert bool(current)==(event_kind=='context')
    if current:assert current['events_sha256']!=original['events_sha256']
    assert store.get_record_state('claims','C')==state
