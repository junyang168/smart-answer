"""Use ordinary Claim Update contracts on changed shared Question evidence."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from backend.pipeline import claim_management as cm, claim_management_batch as batch
from backend.pipeline import source_claim_updates as updates
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.cvp_sql_commit import binding
from backend.tests.test_claim_management_postgres import action,postgres_store,_temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts
from backend.tests.test_claim_management_intake import pass_review


def fixture(action):
    store,request,_,_,root,_=action
    path=Path(request.source_bindings['S'].path)
    rows=[{'index':1,'text':'耶稣是摩西的中保。'},{'index':2,'text':'后来会被证明。'},{'index':3,'text':'教授说中保。'}]
    path.write_text(json.dumps(rows,ensure_ascii=False))
    doc=deepcopy(store.get_record_state('source_documents','S')['payload']);doc['source_file_sha256']=binding(path)['file_sha256']
    store.apply_plan(store.plan_package({'package_id':'old-source','source_documents':[doc],
        'claims':[{'claim_id':c,'claim_type':'exegesis','statement':'教授说中保。','evidence_step_ids':['E'+c],
                   'occurrences':[{'source_id':'S','anchors':[]}]} for c in ['A','B','C','D','N']],
        'evidence_steps':[{'evidence_step_id':'E'+c,'statement':'教授说中保。','produced_claim_ids':[c],
                          'source_fragment_ids':['FD' if c=='D' else 'FS'],
                          'speaker':'professor','stance':'asserted','support_eligibility':'eligible_candidate'} for c in ['A','B','C','D','N']],
        'source_fragments':[{'fragment_id':f,'source_id':'S','paragraph_key':f'S{i:04d}','verbatim_excerpt':rows[i-1]['text']} for f,i in [('FQ',1),('FD',2),('FS',3)]],
        'questions':[{'question_id':'Q','text':'中保有矛盾吗？','answer_claim_ids':['A','B','C'],'source_fragment_ids':['FQ']}],
        'claim_relations':[{'claim_relation_id':'NA','from_id':'N','to_id':'A','relation_type':'supports','reason':'direct neighbour only'}]}))
    old=root/'before.json';old.write_bytes(path.read_bytes())
    rows[0]['text']='摩西是中保。';rows[1]['text']='教授否定这个译法。'
    path.write_text(json.dumps(rows,ensure_ascii=False));doc['source_file_sha256']=binding(path)['file_sha256']
    plan=store.plan_package({'package_id':'registered-source','source_documents':[doc]});store.apply_plan(plan)
    return store,old,plan.change_set_id,root/'source-updates'


def test_shared_question_owners_are_selected_but_claim_neighbour_is_not(action):
    store,old,cid,root=fixture(action)
    plan=updates.prepare(store,'S',old,cid,root,'source corrections')
    assert plan['changed_paragraphs']==['S0001','S0002']
    assert plan['paragraphs']==[{'paragraph_key':'S0001','claim_ids':['A','B','C']},{'paragraph_key':'S0002','claim_ids':['D']}]
    assert plan['unowned_paragraphs']==[]
    assert 'source_fragments/FQ' in plan['dependencies']['claims/B']['S0001']
    assert store.get_record_state('source_fragments','FQ')['payload']['verbatim_excerpt']=='耶稣是摩西的中保。'
    assert not list(root.glob('paragraphs/*/prepared.json'))  # planning never dispatches or grants identity
    assert updates.prepare(store,'S',old,cid,root,'source corrections')==plan


def test_complete_shared_object_refresh_then_fresh_next_paragraph_and_zero_call_resume(action,monkeypatch):
    store,old,cid,root=fixture(action);clients={};events=[]
    original={c:store.get_record_state('claims',c)['payload']['statement'] for c in ['A','B','C','D']}
    def execute(manifest,output):
        for key,(request,execution) in batch.load(manifest,output,store=store)[1].items():
            target=request.target_keys[0];events.append(target)
            fid='FD' if target=='claims/D' else 'FQ';paragraph='S0002' if fid=='FD' else 'S0001'
            if target=='claims/D':
                assert store.get_record_state('source_fragments','FQ')['payload']['verbatim_excerpt']=='摩西是中保。'
                assert all(store.claim_management_qualification(c)['ready'] for c in ['A','B','C'])
            def citation(packet):return {'source_id':'S','paragraph_key':paragraph,'exact_quote':packet['sources']['S']['paragraphs'][paragraph]['text']}
            def resolutions(packet):return [{'finding_id':f,'disposition':'resolved','reason':'refresh shared source quotation',
                'citations':[citation(packet)],'affected_target_keys':[target],
                'affected_write_keys':['source_fragments/'+fid],'impact_reason':'current shared source binding'} for f in packet['unresolved']]
            def review(packet):
                result=pass_review(packet);rows={k:v['payload'] for k,v in packet['before'].items()}
                rows.update(cm.source.index(cm.Candidate.model_validate(packet['candidate']).records))
                if rows['source_fragments/'+fid]['verbatim_excerpt']!=citation(packet)['exact_quote']:
                    if fid=='FD':
                        for assessment in result['binding_assessments']:
                            if assessment['claim_key']==target:assessment['sufficient']=False
                    result['findings']=[{'target_key':'source_fragments/'+fid,'component':'source_binding','category':'mapping',
                        'reason':'old shared quotation is no longer in the source','acceptance_condition':'use current exact source',
                        'citations':[citation(packet)],'affected_target_keys':[target],'affected_write_keys':[],
                        'impact_reason':'related Question or Evidence needs refresh even though statement is unchanged'}]
                result['prior_findings']=resolutions(packet)
                return result
            def proposal(packet):return {'changes':[{'record_key':'source_fragments/'+fid,
                'fields_json':json.dumps({'verbatim_excerpt':citation(packet)['exact_quote']}),
                'reason':'refresh shared evidence','citations':[citation(packet)]}],
                'creations':[],'retirements':[],'findings':[],'finding_resolutions':resolutions(packet)}
            client=clients.setdefault(key,SyntheticPorts(store,execution,opus=review,proposal=proposal))
            result=execute_claim_management(store,request,root=execution,ports=client)
            assert result['status']=='completed',result
    monkeypatch.setattr(batch,'run',execute)
    result=updates.run(store,'S',old,cid,root,'full refresh')
    assert result['status']=='claims_completed',result
    assert result['downstream_handoff']['status']=='not_executed'
    assert events==['claims/A','claims/B','claims/C','claims/D']
    assert {c:store.get_record_state('claims',c)['payload']['statement'] for c in original}==original
    assert [c.calls for c in clients.values()]==[['opus','gpt','opus'],['opus'],['opus'],['opus','gpt','opus']]
    assert updates.run(store,'S',old,cid,root,'full refresh')==result
    assert len(events)==4


def test_current_source_drift_blocks_without_preparing_claim_batches(action):
    store,old,cid,root=fixture(action);updates.prepare(store,'S',old,cid,root,'refresh')
    doc=store.get_record_state('source_documents','S')['payload'];Path(doc['source_path']).write_text('[]')
    with pytest.raises(ValueError):updates.run(store,'S',old,cid,root,'refresh')
    assert not (root/'paragraphs').exists()


def test_unaccepted_fragment_review_recovers_same_raw_and_then_normal_revision(action,monkeypatch):
    from backend.pipeline import claim_management_intake as intake,claim_evidence_review as evidence
    from backend.pipeline.claim_review_recovery import recover_returned_review,VERSION
    store,old,cid,root=fixture(action);root=root/'single-D'
    citation={'source_id':'S','paragraph_key':'S0002','exact_quote':'教授否定这个译法。'}
    def resolutions(packet):return [{'finding_id':f,'disposition':'resolved','reason':'bound excerpt repaired',
        'citations':[citation],'affected_target_keys':['claims/D'],'affected_write_keys':['source_fragments/FD'],
        'impact_reason':'own bound evidence refreshed'} for f in packet['unresolved']]
    def review(packet):
        value=pass_review(packet)
        if not packet['unresolved']:
            for a in value['binding_assessments']:
                if a['claim_key']=='claims/D':a['sufficient']=False
            value['findings']=[{'target_key':'source_fragments/FD','component':'binding','category':'evidence',
                'reason':'old bound excerpt is no longer current','acceptance_condition':'refresh own bound excerpt',
                'citations':[citation],'affected_target_keys':['claims/D'],'affected_write_keys':[],
                'impact_reason':'own source evidence is stale'}]
        value['prior_findings']=resolutions(packet);return value
    def proposal(packet):return {'changes':[{'record_key':'source_fragments/FD',
        'fields_json':json.dumps({'verbatim_excerpt':citation['exact_quote']}),'reason':'refresh quotation','citations':[citation]}],
        'creations':[],'retirements':[],'findings':[],'finding_resolutions':resolutions(packet)}
    clients={}
    def execute(manifest,output):
        for key,(request,execution) in batch.load(manifest,output,store=store)[1].items():
            client=clients.setdefault(key,SyntheticPorts(store,execution,opus=review,proposal=proposal))
            execute_claim_management(store,request,root=execution,ports=client)
    monkeypatch.setattr(batch,'run',execute)
    current=evidence.validate_assessments
    def legacy(review,materials,candidate):
        if any(not a.sufficient for a in review.binding_assessments):
            raise ValueError('insufficient own Evidence requires a concrete Claim finding')
        return current(review,materials,candidate)
    monkeypatch.setattr(evidence,'validate_assessments',legacy)
    first=intake.run(['D'],root,store=store,instruction='refresh source')
    assert first['status']=='paused'
    aid=first['claims']['D']['action_id'];parent=store.get_cvp_group('CMA-'+cm.sha256_json(aid))
    rid=next(iter(parent['latest_rounds'].values()));saved=store.get_cvp_group(rid)
    raw=Path(parent['execution_root'])/rid/'opus.raw.json';original_raw=raw.read_bytes()
    monkeypatch.setattr(evidence,'validate_assessments',current)
    auth=root/'engineering-direction.json';auth.write_text(json.dumps({'schema_version':VERSION,'round_id':rid,
        'authorized_by':'owner','authorization_quote':'fix the bound-excerpt validator and resume original result',
        'human_content_approval':False,'record_unaccepted_result':True,
        'prior_metadata_sha256':cm.sha256_json({k:v for k,v in saved.items() if k not in {'run_id','status','error'}})}))
    recovery=recover_returned_review(store,rid,authorization=binding(auth),unrecorded=True)
    record=json.loads(Path(recovery,'engineering-recovery.json').read_text())
    assert record['model_calls']==0 and record['returned_review_passed'] is False
    assert raw.read_bytes()==original_raw
    assert Path(recovery,'opus.raw.json').read_bytes()==original_raw
    final=intake.run(['D'],root,store=store,instruction='refresh source')
    assert final['all_actions_completed'],final
    assert next(iter(clients.values())).calls==['opus','gpt','opus']
    assert store.get_record_state('source_fragments','FD')['payload']['verbatim_excerpt']==citation['exact_quote']
