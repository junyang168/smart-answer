"""Source changes against disposable PostgreSQL, never live content/providers."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from backend.pipeline import source_change_transition as transition
from backend.pipeline import claim_management as cm
from backend.pipeline.source_projection import project_script
from backend.pipeline.cvp_sql_commit import binding
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import seed_claims, SyntheticPorts, passing


def edited(action):
    store, request, _, _, root, _ = action
    seed_claims(store, ['C', 'D'])
    from backend.pipeline.source_projection import project_script
    path = Path(request.source_bindings['S'].path)
    body = project_script(json.loads(path.read_text())['script']).body_sha256
    store.apply_plan(store.plan_package({'package_id':'evidence-fixture',
        'source_fragments':[{'fragment_id':'F','source_id':'S','paragraph_key':'S0001',
            'verbatim_excerpt':'甲是假设，不是断言。','source_sha256':body,'anchor_state':'source_version_bound'}],
        'evidence_steps':[{'evidence_step_id':'E','source_fragment_id':'F','statement':'甲是假设，不是断言。',
            'produced_claim_ids':['C'],'speaker':'professor','stance':'asserted','support_eligibility':'eligible_candidate'}],
        'claims':[{**store.get_record_state('claims','C')['payload'],'evidence_step_ids':['E']}]}))
    old = root / 'old-source.json'; old.write_bytes(path.read_bytes())
    path.write_text(json.dumps({'script': [{'text': '甲是假设，不是断言。现在说明限定。'}]}, ensure_ascii=False))
    return store, old, root


def test_multi_change_version_handoff_is_atomic_and_replayable(action):
    store, old, root = edited(action)
    prior = store.get_record_state('claims', 'C')
    result = transition.register(store, 'S', binding(old), root/'handoff', instruction='synthetic change',
                                 source_confirmation='synthetic user corrected transcript and added professor slide')
    assert result['changed_paragraph_keys'] == ['S0001']
    assert not result['claim_qualification_granted']
    assert store.get_record_state('source_fragments', 'F')['payload']['source_sha256'] == project_script(
        json.loads(Path(store.get_record_state('source_documents', 'S')['payload']['source_path']).read_text())['script']).body_sha256
    assert store.get_record_state('claims', 'C') == prior
    assert len(store.read_human_source_decisions(['S'])) == 1
    assert transition.register(store, 'S', binding(old), root/'handoff', instruction='synthetic change',
        source_confirmation='synthetic user corrected transcript and added professor slide') == result


def test_recovered_baseline_requires_all_complete_projection_hashes(action):
    store, old, root = edited(action)
    with pytest.raises(ValueError, match='every registered projection'):
        transition.register(store, 'S', binding(old), root/'recover', instruction='synthetic', recovered=True)
    doc = store.get_record_state('source_documents', 'S')['payload']
    view = transition.projection(binding(old))
    doc.update({f:getattr(view,a) for f,a in transition.PROJECTION_FIELDS.items()})
    doc['locator_space'] = 'spoken_body_v1'
    store.apply_plan(store.plan_package({'package_id':'projection-fixture','source_documents':[doc]}))
    recovered = root/'recovered.json'
    recovered.write_text(json.dumps(json.loads(old.read_text()), indent=4, ensure_ascii=False))
    result = transition.register(store, 'S', binding(recovered), root/'recover2', instruction='synthetic', recovered=True)
    assert result['registered_before_file_sha256'] != result['observed_before_file_sha256']
    model = cm.SourceTransition.model_validate_json(Path(result['source_transition']['path']).read_text())
    assert model.recovered_baselines
    from backend.pipeline.cvp_sql_actions import source_before_matches
    history = store.get_change_history(result['change_set_id'])
    olddoc = next(o['before'] for o in history['operations'] if o['collection']=='source_documents')
    assert source_before_matches(model, 'S', olddoc, history)


def test_late_failure_rolls_back_source_and_human_facts(action, monkeypatch):
    store, old, root = edited(action)
    prior = store.get_record_state('source_documents', 'S')
    from backend.pipeline import claim_human_source as human
    original = human.register
    def fail(cursor, entries, *, origin):
        original(cursor, entries, origin=origin)
        raise RuntimeError('synthetic late failure')
    monkeypatch.setattr(human, 'register', fail)
    with pytest.raises(RuntimeError, match='late failure'):
        transition.register(store, 'S', binding(old), root/'handoff', instruction='synthetic', source_confirmation='human fact')
    assert store.get_record_state('source_documents', 'S') == prior
    assert store.read_human_source_decisions(['S']) == []


def two_paragraphs(action):
    store, request, _, _, root, _ = action
    path = Path(request.source_bindings['S'].path)
    rows = [{'index':1,'text':'甲是假设，不是断言。'}, {'index':2,'text':'乙也是假设。'}]
    path.write_text(json.dumps(rows, ensure_ascii=False))
    doc = store.get_record_state('source_documents','S')['payload']
    view = project_script(rows)
    doc.update(source_file_sha256=binding(path)['file_sha256'], locator_space='spoken_body_v1', source_sha256=view.body_sha256,
               **{f:getattr(view,a) for f,a in transition.PROJECTION_FIELDS.items()})
    store.apply_plan(store.plan_package({'package_id':'two-paragraphs', 'source_documents':[doc],
        'claims':[{'claim_id':c,'statement':rows[i]['text'],'claim_type':'exegesis',
            'occurrences':[{'source_id':'S','anchors':[]}],'evidence_step_ids':['E'+c]} for i,c in enumerate(['C','D'])],
        'evidence_steps':[{'evidence_step_id':'E'+c,'statement':rows[i]['text'],'source_fragment_ids':['F'+c],
            'speaker':'professor','stance':'asserted','support_eligibility':'eligible_candidate','produced_claim_ids':[c]}
            for i,c in enumerate(['C','D'])],
        'source_fragments':[{'fragment_id':'F'+c,'source_id':'S','paragraph_key':f'S{i+1:04d}',
            'source_segment_index':i+1,'verbatim_excerpt':rows[i]['text'],'source_sha256':view.body_sha256,
            'anchor_state':'source_version_bound'} for i,c in enumerate(['C','D'])]}))
    old = root/'two-before.json'; old.write_bytes(path.read_bytes())
    rows[0]['text'] += '但有新的限定。'
    rows[1]['text'] += '教授投影片补充理由。'
    path.write_text(json.dumps(rows, ensure_ascii=False))
    return store, old, root/'automated'


def automated_impact(packet):
    result = {'complete_review': True, 'findings': []}
    result['source_impacts'] = []
    result['source_coverage'] = []
    for i,c in enumerate(['C','D'],1):
        cite = {'source_id':'S','paragraph_key':f'S{i:04d}','exact_quote':packet['sources']['S']['paragraphs'][f'S{i:04d}']['text']}
        result['source_impacts'].append({'record_key':'claims/'+c,'conclusion':'maintenance',
            'operation':'Update','reason':'synthetic changed teaching','citations':[cite]})
        result['findings'].append({'target_key':'claims/'+c,'component':'limitation','category':'mapping',
            'reason':'synthetic changed context','acceptance_condition':'review current own source','citations':[cite]})
        result['source_coverage'].append({'source_id':'S','paragraph_key':f'S{i:04d}','claim_keys':['claims/'+c],
            'conclusion':'needs_update','reason':'synthetic coverage check','citations':[cite]})
    return result


def test_two_paragraphs_call_normal_updates_in_order_and_resume(action, monkeypatch):
    from contextlib import nullcontext
    from backend.pipeline import source_change_automation as automation
    from backend.pipeline import claim_management_batch as batch
    from backend.pipeline.cvp_sql_actions import execute_claim_management
    from backend.tests.test_claim_management_intake import pass_review
    store, old, root = two_paragraphs(action)
    events=[]; clients={}
    class Ports(automation.Production):
        lock = staticmethod(nullcontext)
        def guard(self): pass
        def impact(self, request, output):
            events.append('impact')
            client=clients.setdefault('impact',SyntheticPorts(store,output,opus=automated_impact))
            return execute_claim_management(store, request, root=output, ports=client)
        def update(self, ids, output, **kwargs):
            events.append(tuple(ids))
            if ids == ['D']:
                assert store.claim_management_qualification('C')['ready']
            return super().update(ids, output, **kwargs)
        def downstream(self, ids, retired, output, instruction):
            events.append(('downstream',tuple(ids)))
            assert all(store.claim_management_qualification(c)['ready'] for c in ids)
            return {'status':'completed','fixture':'downstream boundary only'}
    def execute(manifest, output):
        _, jobs=batch.load(manifest, output, store=store)
        for key,(request,execution) in jobs.items():
            def resolutions(packet):
                target=packet['action_obligations']['target_keys'][0]
                paragraph='S0001' if target=='claims/C' else 'S0002'
                cite={'source_id':'S','paragraph_key':paragraph,'exact_quote':packet['sources']['S']['paragraphs'][paragraph]['text']}
                return [{'finding_id':fid,'disposition':'resolved','reason':'synthetic source finding adjudicated with full current source',
                    'citations':[cite],'affected_target_keys':[target],'affected_write_keys':[],
                    'impact_reason':'synthetic reviewed target'} for fid in packet['unresolved']]
            def propose(packet):
                return {'changes':[],'creations':[],'retirements':[],'findings':[], 'finding_resolutions':resolutions(packet)}
            def review(packet):
                value=pass_review(packet);value['prior_findings']=resolutions(packet);return value
            client=clients.setdefault(key,SyntheticPorts(store,execution,proposal=propose,opus=review))
            actual=execute_claim_management(store,request,root=execution,ports=client)
            assert actual['status']=='completed',actual
    monkeypatch.setattr(batch,'run',execute)
    ports=Ports(store)
    kwargs={'instruction':'synthetic full source update','before':old,'ports':ports}
    result=automation.run(store,'S',root,**kwargs)
    assert result['status']=='completed',result
    assert events == ['impact',('C',),('D',),('downstream',('C','D'))]
    assert [c.calls for c in clients.values()] == [['opus'],['gpt','opus'],['gpt','opus']]
    # Lost outer receipt after a committed source handoff must consume the
    # archived internal intent, not look for the now-current source as 'before'.
    (root/'handoff.json').unlink()
    assert automation.run(store,'S',root,**kwargs)['status']=='completed'
    assert [c.calls for c in clients.values()] == [['opus'],['gpt','opus'],['gpt','opus']]


def test_editor_archives_exact_bytes_only_after_cas_success(tmp_path):
    from backend.api.sc_api.script_delta import ScriptDelta, ScriptConflictError
    path = tmp_path/'script_review/sermon.json'; path.parent.mkdir()
    raw=b'[ {"index":1,"text":"original"} ]\n';path.write_bytes(raw)
    import hashlib
    oldsha=hashlib.sha256(raw).hexdigest()
    with pytest.raises(ScriptConflictError):
        ScriptDelta.save_json_payload(str(tmp_path),'sermon','script_review',[],expected_current_sha256='f'*64)
    archive=tmp_path/'wang-knowledge-platform/source-versions'/(oldsha+'.json')
    assert not archive.exists()
    ScriptDelta.save_json_payload(str(tmp_path),'sermon','script_review',[{'index':1,'text':'changed'}],expected_current_sha256=oldsha)
    assert archive.read_bytes()==raw


@pytest.mark.parametrize('reviewed', [['C','D'], ['C']])
@pytest.mark.parametrize('moved', [False, True])
def test_unaffected_source_impact_reuses_actual_opus_program_readback(action, monkeypatch, reviewed, moved):
    from backend.pipeline import source_change_automation as automation, claim_management_intake as intake
    from backend.pipeline import claim_management_batch as batch
    from backend.pipeline.cvp_sql_actions import execute_claim_management
    from backend.tests.test_claim_management_intake import pass_review
    store, old, root = two_paragraphs(action)
    path = Path(store.get_record_state('source_documents','S')['payload']['source_path'])
    edited_raw=path.read_bytes(); path.write_bytes(old.read_bytes())
    store.apply_plan(store.plan_package({'package_id':'readonly-neighbour', 'claim_relations':[{
        'claim_relation_id':'R','from_id':'C','to_id':'D','relation_type':'supports','reason':'synthetic direct neighbour'}]}))
    def execute(manifest, output):
        _,jobs=batch.load(manifest,output,store=store)
        for _,(request,execution) in jobs.items():
            assert execute_claim_management(store,request,root=execution,
                ports=SyntheticPorts(store,execution,opus=pass_review))['status']=='completed'
    monkeypatch.setattr(batch,'run',execute)
    assert intake.run(reviewed,root/'previous-content',instruction='synthetic old content review',store=store)['status']=='completed'
    if moved:
        original=root/'previous-content';archived=root/'moved-content'
        original.rename(archived);original.symlink_to(archived,target_is_directory=True)
    path.write_bytes(edited_raw)
    handoff=transition.register(store,'S',binding(old),root/'source',instruction='synthetic source')
    request=automation.prepare_impact(store,handoff,root/'impact','synthetic source')
    def unaffected(packet):
        value=automated_impact(packet)
        value['findings']=[]
        for item in value['source_impacts']:
            item['conclusion']='unaffected';item['operation']=None
        for item in value['source_coverage']:item['conclusion']='represented'
        return value
    result=execute_claim_management(store,request,root=root/'impact/execution',
        ports=SyntheticPorts(store,root,opus=unaffected))
    assert result['status']=='completed',result
    assert result['source_revalidated_keys']==['claims/'+c for c in reviewed], result
    assert store.claim_management_qualification('C')['ready']
    assert store.claim_management_qualification('D')['ready'] == ('D' in reviewed)


def test_first_paragraph_pause_never_prepares_later_batch(action):
    from contextlib import nullcontext
    from backend.pipeline import source_change_automation as automation
    from backend.pipeline.cvp_sql_actions import execute_claim_management
    store,old,root=two_paragraphs(action)
    starts=[]
    class Ports(automation.Production):
        lock=staticmethod(nullcontext)
        def guard(self):pass
        def impact(self,request,output):
            return execute_claim_management(store,request,root=output,ports=SyntheticPorts(store,output,opus=automated_impact))
        def update(self,ids,output,**kwargs):
            starts.append(ids);return {'status':'paused','all_actions_completed':False,'reason':'call_uncertain'}
        def downstream(self,*args):raise AssertionError('unfinished Claims cannot reach CVP')
    result=automation.run(store,'S',root,instruction='synthetic',before=old,ports=Ports(store))
    assert result['status']=='paused' and result['stage']=='claim_update'
    assert starts==[['C']]
    assert not (root/'paragraphs/S0002').exists()


def test_changed_source_between_batches_blocks_next_dispatch(action):
    from contextlib import nullcontext
    from backend.pipeline import source_change_automation as automation
    from backend.pipeline.cvp_sql_actions import execute_claim_management
    store,old,root=two_paragraphs(action)
    starts=[]
    class Ports(automation.Production):
        lock=staticmethod(nullcontext)
        def guard(self):pass
        def impact(self,request,output):
            return execute_claim_management(store,request,root=output,ports=SyntheticPorts(store,output,opus=automated_impact))
        def update(self,ids,output,**kwargs):
            starts.append(ids)
            path=Path(store.get_record_state('source_documents','S')['payload']['source_path'])
            path.write_text(path.read_text()+'\n')
            return {'status':'completed','all_actions_completed':True,'claims':{c:{} for c in ids}}
    with pytest.raises(ValueError,match='binding drift'):
        automation.run(store,'S',root,instruction='synthetic',before=old,ports=Ports(store))
    assert starts==[['C']]
