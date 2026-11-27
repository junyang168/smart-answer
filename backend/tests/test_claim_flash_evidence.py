"""Grouped material scope, exact reuse, isolated SQL and layer-boundary regressions."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from backend.pipeline import claim_flash_evidence as stage, claim_flash_common as f
from backend.pipeline import claim_flash_coverage as coverage, evidence_refresh as e
from backend.tests.test_claim_update_flash import Ports as OriginalPorts
from backend.tests.test_claim_flash_source import fixture
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres


class Ports(OriginalPorts):
    def judge(self,request):
        if 'current_paragraphs' in request['state']:return super().judge(request)
        if 'record' in request['state']:
            bad='拒絕明白' in request['state']['record']['statement']
            self.choices={'record_fidelity':'changed' if bad else 'faithful'}
            return super().judge(request)
        self.calls.append(('jev',request));saved=[p['statement'] for p in request['state']['records'].values()]
        return {'model':e.MODEL,'answers':{key:{'type':'choice','choice':('covered' if spec['instructions'].split('意思：')[-1] in saved else 'missing'),
            'probabilities':{name:float(name==('covered' if spec['instructions'].split('意思：')[-1] in saved else 'missing')) for name in spec['criteria']}}
            for key,spec in request['questions'].items()}}
    def repair(self,request):
        task=request['input']['task']
        if task=='lower_decomposition':return super().repair(request)
        self.calls.append(('gpt',request));items=[]
        for job in (request['input']['jobs'][0]['candidates'] if task=='evidence_coverage_repair' else request['input']['jobs']):
            if task=='evidence_fidelity_repair':
                items.append({'job_id':job['job_id'],'action':'repair','statement':'沒有聽從祂的話，顯明沒有真正相信祂。',
                    'speaker':None,'stance':None,'attribution':None,'scripture_refs':None,'reason':'保留原來行為判準。'})
            elif task=='evidence_coverage_repair':
                items.append({'job_id':job['job_id'],'action':'create','record_key':'','record_type':'observations',
                    'statement':job['unit']['statement'],'speaker':None,'stance':None,'attribution':None,'scripture_refs':[],
                    'fragment_ids':job['required_fragment_ids'],'reason':'保存已確認的重要意思。'})
            else:raise AssertionError(task)
        return {'items':[{'job_id':request['input']['jobs'][0]['job_id'],'items':items}]} if task=='evidence_coverage_repair' else {'items':items}


def setup(tmp_path):
    text='你沒有聽從祂的話，你根本沒信。'
    records,sources=fixture(tmp_path,[text,'鄰近段落的秘密，不能當成已掛片段。'],[('S0001',text)])
    records['evidence_steps/E']['payload']['statement']='拒絕明白祂的話，顯明沒有真正相信祂。'
    plan={'source_id':'S','source_body_sha256':sources['S']['body_sha256'],'groups':[
        {'group_id':'A','title':'本題','paragraph_keys':['S0001'],'lower_record_keys':['evidence_steps/E'],'reference_record_keys':[]},
        {'group_id':'B','title':'另一題','paragraph_keys':['S0002'],'lower_record_keys':[],'reference_record_keys':[]}]}
    return records,sources,plan


@pytest.mark.parametrize('fault',[None,'same_record','conflicting','missing','unknown'])
def test_one_unit_can_repair_two_distinct_records_without_duplicate_disposition(fault):
    def item(key):return stage.Missing(job_id='U8',action='enrich',record_key=key,
        record_type='evidence_steps',fragment_ids=['F'],reason='保留本項的不同實質部分。')
    edits=[item('evidence_steps/A'),item('evidence_steps/B')]
    if fault=='same_record':edits[1].record_key=edits[0].record_key
    if fault=='conflicting':edits[1].action='keep'
    if fault=='missing':edits=[]
    if fault=='unknown':edits[1].job_id='unknown'
    if fault:
        with pytest.raises(ValueError):stage.coverage_edits(edits,[{'job_id':'U8'}])
    else:
        assert stage.coverage_edits(edits,[{'job_id':'U8'}])==edits


def saved_inventory(tmp_path,sources,units=()):
    previous=tmp_path/'coverage';rows=[];jobs=[]
    for i,pk in enumerate(sources['S']['paragraphs']):
        gid='A' if i==0 else 'B';jobs.append({'job_id':gid,'title':gid,'paragraphs':{pk:sources['S']['paragraphs'][pk]['text']}})
        rows.append({'job_id':gid,'units':[u for u in units if u['paragraph_key']==pk],'citations':[]})
    e.save(previous/'intent.json',{'source_id':'S','source':sources['S']['file']})
    request={'system':coverage.INVENTORY_PROMPT,'input':{'task':'source_preservation_inventory','jobs':jobs}}
    folder=previous/'source-inventory'/'gpt'/'original'
    e.save(folder/'request.json',request);e.save(folder/'response.json',{'items':rows})
    return previous


def test_fidelity_uses_only_complete_actual_fragments_and_source_blind_split(tmp_path):
    records,sources,plan=setup(tmp_path);before=deepcopy(records);ports=Ports();root=tmp_path/'run'
    previous=saved_inventory(tmp_path,sources)
    result=stage.run_group(None,records,sources,plan['groups'][0],['evidence_steps/E'],[],root,ports,previous,apply=False)
    assert result['status']=='verified' and set(result['updates'])=={'evidence_steps/E'} and result['new_fragments']=={}
    assert records==before
    for role,req in ports.calls:
        assert '鄰近段落的秘密' not in json.dumps(req,ensure_ascii=False)
        if role=='jev':
            assert 'source_context' not in req['state']
            assert req['state']['original_text_excerpts']==['你沒有聽從祂的話，你根本沒信。']
        elif req['input']['task']=='lower_decomposition':assert 'sources' not in json.dumps(req)
    post=result['updates']['evidence_steps/E']
    assert {k:v for k,v in post.items() if k!='statement'}=={k:v for k,v in before['evidence_steps/E']['payload'].items() if k!='statement'}


@pytest.mark.parametrize('mode',['direct','omitted','misquoted','neighbor','not_found','out_of_scope','changes_statement'])
def test_quote_only_repair_uses_one_local_selection(tmp_path, monkeypatch, mode):
    quote='當小孩子被人家騙了，小孩子受苦的時候，不聽話受苦的時候是什麼？父母比孩子更痛心。你不相信？我們這些做父母的人。神也一樣。'
    texts=['遠段一。','前二段。','前一段。',quote,'後一段。','後二段。','遠段二。']
    quote_key='S0004'
    if mode=='neighbor':
        quote=quote.removesuffix('神也一樣。')
        texts[2:4]=[quote,'神也一樣。']
        quote_key='S0003'
    records,sources=fixture(tmp_path,texts,[('S0004','神也一樣。')])
    statement='神對受騙受苦之人的心，也像父母對孩子一樣痛心。'
    payload=records['evidence_steps/E']['payload']
    payload.update(statement=statement,speaker='professor',stance='asserted')
    group={'group_id':'A','paragraph_keys':['S0004'],'lower_record_keys':['evidence_steps/E'],'reference_record_keys':[]}
    monkeypatch.setattr(stage.components,'check',lambda *a,**kw:{
        'record_fidelity':'partial','speaker_consistency':'different','stance_consistency':'matched'})
    monkeypatch.setattr(stage.source,'binding_candidates',lambda *a,**kw:{'evidence_steps/E':{
        'fragments':[{'fragment_id':'F0','paragraph_key':'S0004','quote':'神也一樣。'}],
        'nearby_paragraphs':[{'source_id':'S','paragraph_key':'S0004',**sources['S']['paragraphs']['S0004']}],
        'source_lookup':{'candidate_paragraph_keys':['S0004']}}})

    class QuotePorts(Ports):
        def repair(self, request):
            task=request['input']['task']
            if task not in {'evidence_fidelity_repair','source_binding_completion'}:
                return super().repair(request)
            self.calls.append(('gpt',request))
            job,=request['input']['jobs']
            if task=='evidence_fidelity_repair':
                item={'job_id':job['job_id'],'action':'add_quotes','reason':'補上父母類比。'}
                if mode=='direct':item['quotes']=[{'paragraph_key':'S0004','quote':quote}]
                if mode=='misquoted':item['quotes']=[{'paragraph_key':'S0004','quote':'來源沒有的文字。'}]
                if mode=='changes_statement':item['statement']='另一主張。'
            else:
                assert set(request['input']['source_paragraphs'])=={'S0002','S0003','S0004','S0005','S0006'}
                assert job['statement']==statement and job['reason']=='補上父母類比。'
                assert set(job)=={'job_id','statement','reason','material'}
                item={'job_id':job['job_id'],'action':'bind','reason':'父母的類比與結語。',
                      'quotes':[{'paragraph_key':quote_key,'quote':quote}]}
                if mode=='not_found':item.update(action='needs_source',reason='附近原文不足。',quotes=[])
                if mode=='out_of_scope':item['quotes']=[{'paragraph_key':'S0007','quote':texts[-1]}]
            return {'items':[item]}

    ports=QuotePorts();root=tmp_path/'run';previous=saved_inventory(tmp_path/'previous',sources)
    def run(ports):
        return stage.run_group(None,records,sources,group,['evidence_steps/E'],[],root,ports,previous,
                               apply=False,complete_source=True,program_validated=True,quote_action=True)
    if mode=='changes_statement':
        with pytest.raises(ValueError,match='add_quotes cannot modify Evidence fields'):
            run(ports)
        return
    result=run(ports)
    tasks=[request['input']['task'] for role,request in ports.calls if role=='gpt']
    assert tasks.count('evidence_fidelity_repair')==1
    assert tasks.count('source_binding_completion')==(0 if mode=='direct' else 1)
    if mode in {'not_found','out_of_scope'}:
        finding,=result['pending']
        assert finding['layer']=='source' and finding['source_completion_attempted']
        assert result['updates']=={} and result['new_fragments']=={}
    else:
        assert result['status']=='verified'
        after=result['updates']['evidence_steps/E']
        assert after['statement']==statement and after['speaker']=='professor' and after['stance']=='asserted'
        assert after['source_fragment_ids'][0]=='F0'
        fragment,=result['new_fragments'].values()
        assert fragment['verbatim_excerpt']==quote and fragment['paragraph_key']==quote_key
    replay=QuotePorts()
    assert run(replay)==result and replay.calls==[]


def test_process_commentary_repair_stays_within_lower_statement_task(tmp_path):
    records,sources,plan=setup(tmp_path)
    records['evidence_steps/E']['payload']['statement']='沒有聽從祂的話，顯明沒有真正相信祂；本組尚未解釋。'
    before=deepcopy(records)
    class CommentaryPorts(Ports):
        def judge(self,request):
            if 'record' in request['state']:
                self.choices={'record_fidelity':'changed'}
                return OriginalPorts.judge(self,request)
            return super().judge(request)
    ports=CommentaryPorts();previous=saved_inventory(tmp_path/'prior',sources)
    result=stage.run_group(None,records,sources,plan['groups'][0],['evidence_steps/E'],[],tmp_path/'run',ports,previous,apply=False)
    assert result['status']=='verified' and set(result['updates'])=={'evidence_steps/E'}
    post=result['updates']['evidence_steps/E']
    assert post['statement']=='沒有聽從祂的話，顯明沒有真正相信祂。'
    assert {k:v for k,v in post.items() if k!='statement'}=={k:v for k,v in before['evidence_steps/E']['payload'].items() if k!='statement'}
    requests=[r for role,r in ports.calls if role=='gpt' and r['input']['task']=='evidence_fidelity_repair']
    assert len(requests)==1 and f.KNOWLEDGE_TEXT_RULES in requests[0]['system']
    screening=next(r for role,r in ports.calls if role=='jev' and 'record_fidelity' in r['questions'])
    assert '本組尚未解釋' in screening['questions']['record_fidelity']['instructions']
    assert 'unsupported' in screening['questions']['record_fidelity']['criteria']
    assert records==before and result['new_fragments']=={}


@pytest.mark.parametrize('changed',[True,False])
def test_action_subject_is_repaired_without_touching_other_layers(tmp_path,changed):
    text='他們從伯大尼出來，耶穌餓了。'
    records,sources=fixture(tmp_path,[text],[('S0001',text)])
    records['evidence_steps/E']['payload']['statement']='耶穌與門徒從伯大尼出來時餓了。' if changed else text
    before=deepcopy(records)
    def repair(request):
        assert request['input']['task']=='evidence_fidelity_repair'
        assert '不得擴大或轉移主詞' in request['system']
        return {'items':[{'job_id':j['job_id'],'action':'repair','statement':text,'reason':'只有耶穌是飢餓的主體。'}
                         for j in request['input']['jobs']]}
    ports=OriginalPorts(choices={'record_fidelity':'changed' if changed else 'faithful'},reply=repair)
    group={'group_id':'A','paragraph_keys':['S0001'],'lower_record_keys':['evidence_steps/E'],'reference_record_keys':[]}
    result=stage.run_group(None,records,sources,group,['evidence_steps/E'],[],tmp_path/'run',ports,saved_inventory(tmp_path/'prior',sources),apply=False)
    assert result['status']=='verified' and result['new_fragments']=={} and records==before
    calls=[r for role,r in ports.calls if role=='gpt' and r['input']['task']=='evidence_fidelity_repair']
    assert len(calls)==int(changed)
    if changed:
        post=result['updates']['evidence_steps/E'];assert post['statement']==text
        assert {k:v for k,v in post.items() if k!='statement'}=={k:v for k,v in before['evidence_steps/E']['payload'].items() if k!='statement'}
    else:assert result['updates']=={}


def test_complete_bound_cross_group_fragments_are_not_truncated(tmp_path):
    records,sources,plan=setup(tmp_path)
    p=sources['S']['paragraphs']['S0002'];fragment=deepcopy(records['source_fragments/F0']['payload'])
    import hashlib
    fragment.update(fragment_id='F1',paragraph_key='S0002',verbatim_excerpt=p['text'],source_segment_index=p['index'],
        media_time=p['start_time'],media_end_time=p['end_time'],paragraph_text_sha256=hashlib.sha256(p['text'].encode()).hexdigest(),
        verbatim_excerpt_sha256=hashlib.sha256(p['text'].encode()).hexdigest())
    records['source_fragments/F1']={'payload':fragment,'retired':False}
    records['evidence_steps/E']['payload']['source_fragment_ids'].append('F1')
    value=stage.material('evidence_steps/E',records,sources)
    assert len(value['fragments'])==2 and value['original_text_excerpts'][1]==p['text']
    assert 'nearby_paragraphs' not in value


def test_changed_one_statement_reuses_other_decompositions(tmp_path):
    ports=Ports();stage.decompose(tmp_path,ports,{'A':'第一個意思。','B':'第二個意思。'})
    count=len(ports.calls);stage.decompose(tmp_path,ports,{'A':'改過的意思。','B':'第二個意思。'})
    later=[req for role,req in ports.calls[count:] if role=='gpt']
    assert len(later)==1
    assert [r['statement'] for j in later[0]['input']['jobs'] for r in j['records']]==['改過的意思。']


def test_exact_previous_jev_input_is_reused_and_unknown_is_never_resent(tmp_path):
    ports=Ports();state={'records':{'R':{'statement':'已保存。'}}};questions={'U0':{'type':'choice','instructions':'意思：已保存。','criteria':{'covered':'有','missing':'無'}}}
    first=f.jev(tmp_path/'old',ports,state,questions);ports.calls=[]
    assert f.jev(tmp_path/'new',ports,state,questions,reuse_roots=[tmp_path/'old'])==first and not ports.calls
    reply=next((tmp_path/'old').glob('jev/*/response.json'));reply.unlink()
    with pytest.raises(ValueError,match='no saved reply'):f.jev(tmp_path/'third',ports,state,questions,reuse_roots=[tmp_path/'old'])
    assert not ports.calls


def test_coverage_uses_saved_units_and_can_create_only_material_records(tmp_path):
    records,sources,plan=setup(tmp_path);ports=Ports();unit={'paragraph_key':'S0001','quote':'你沒有聽從祂的話，你根本沒信。','statement':'聽從是相信的檢驗。'}
    previous=saved_inventory(tmp_path,sources,[unit]);units=stage.inventory(previous,sources,'S')
    result=stage.run_group(None,records,sources,plan['groups'][0],['evidence_steps/E'],units,tmp_path/'run',ports,previous,apply=False)
    assert any(k.startswith('observations/') for k in result['updates'])
    assert not result['new_fragments'] and all(k.split('/')[0] in stage.COLLECTIONS for k in result['updates'])
    tasks=[req['input']['task'] for role,req in ports.calls if role=='gpt']
    assert 'source_preservation_inventory' not in tasks
    assert set(tasks)<={'lower_decomposition','evidence_fidelity_repair','evidence_coverage_repair'}


def test_missing_fragment_returns_to_source_without_whole_sermon_lookup(tmp_path):
    records,sources,plan=setup(tmp_path);records['source_fragments/F0']['payload']['source_sha256']='old'
    ports=Ports();previous=saved_inventory(tmp_path,sources)
    result=stage.run_group(None,records,sources,plan['groups'][0],['evidence_steps/E'],[],tmp_path/'run',ports,previous,apply=False)
    assert result['status']=='needs_attention' and not result['updates'] and ports.calls==[]
    assert result['pending'][0]['layer']=='source'


@pytest.fixture
def seeded(postgres_store,tmp_path):
    records,sources,plan=setup(tmp_path);doc=deepcopy(records['source_documents/S']['payload'])
    doc.pop('source_body_sha256');doc.pop('source_sha256')
    ev=records['evidence_steps/E']['payload'];ev['produced_claim_ids']=['C']
    postgres_store.apply_plan(postgres_store.plan_package({'package_id':'group-evidence-seed','source_documents':[doc],
        'source_fragments':[records['source_fragments/F0']['payload']],'evidence_steps':[ev],
        'claims':[{'claim_id':'C','statement':'原主張。','claim_type':'exegesis','evidence_step_ids':['E'],
                   'occurrences':[{'source_id':'S','anchors':[]}]}]}))
    return postgres_store,sources,plan,saved_inventory(tmp_path,sources),tmp_path/'run'


def test_real_sql_apply_and_resume_preserve_claims_sources_and_make_no_new_model_calls(seeded):
    store,sources,plan,previous,root=seeded;ports=Ports()
    before={k:store.get_record_state(*k.split('/')) for k in ['claims/C','source_fragments/F0','source_documents/S']}
    result=stage.run(store,'S',plan,root,ports,previous,apply=True)
    assert result['status']=='completed'
    assert store.get_record_state('evidence_steps','E')['payload']['statement']=='沒有聽從祂的話，顯明沒有真正相信祂。'
    assert before=={k:store.get_record_state(*k.split('/')) for k in before}
    replay=Ports();again=stage.run(store,'S',plan,root,replay,previous,apply=True)
    assert again['status']=='completed' and replay.calls==[]


def test_gpt_can_keep_despite_jev_disagreement_without_changing_fields(tmp_path):
    records,sources,plan=setup(tmp_path)
    class Keep(Ports):
        def repair(self,req):
            raw=super().repair(req)
            if req['input']['task']=='evidence_fidelity_repair':
                for item in raw['items']:item['action']='keep'
            return raw
    result=stage.run_group(None,records,sources,plan['groups'][0],['evidence_steps/E'],[],tmp_path/'run',Keep(),saved_inventory(tmp_path,sources),apply=False)
    assert result['updates']=={} and result['outcomes'][0]['basis']=='gpt_keep'


@pytest.mark.parametrize('defect',['source','unprovided_fragment','claim_key'])
def test_bad_inventory_or_scope_expansion_cannot_write(tmp_path,defect):
    records,sources,plan=setup(tmp_path);previous=saved_inventory(tmp_path,sources)
    if defect=='source':
        path=previous/'intent.json';d=json.loads(path.read_text());d['source_id']='OTHER';path.write_text(json.dumps(d))
        with pytest.raises(ValueError):stage.inventory(previous,sources,'S')
        return
    unit={'paragraph_key':'S0001','quote':'你沒有聽從祂的話，你根本沒信。','statement':'需保存的意思。'}
    class Broken(Ports):
        def repair(self,req):
            raw=super().repair(req)
            if req['input']['task']=='evidence_coverage_repair':
                if defect=='unprovided_fragment':raw['items'][0]['items'][0]['fragment_ids']=['OTHER']
                else:raw['items'][0]['items'][0].update(action='enrich',record_key='claims/C')
            return raw
    with pytest.raises(ValueError):stage.run_group(None,records,sources,plan['groups'][0],['evidence_steps/E'],[unit],tmp_path/'run',Broken(),previous,apply=False)


def test_owner_bookkeeping_does_not_repeat_semantic_review(tmp_path):
    records,sources,plan=setup(tmp_path);root=tmp_path/'run';previous=saved_inventory(tmp_path,sources)
    first=stage.run_group(None,records,sources,plan['groups'][0],['evidence_steps/E'],[],root,Ports(),previous,apply=False)
    records['evidence_steps/E']['payload']=deepcopy(first['updates']['evidence_steps/E'])
    records['evidence_steps/E']['payload']['produced_claim_ids']=['another-owner']
    replay=Ports();second=stage.run_group(None,records,sources,plan['groups'][0],['evidence_steps/E'],[],root,replay,previous,apply=False)
    assert second['updates']=={} and replay.calls==[]
    assert records['evidence_steps/E']['payload']['produced_claim_ids']==['another-owner']


def test_exact_source_stage_coverage_verdict_is_consumed_without_rechecking(tmp_path):
    from backend.pipeline import claim_flash_questions as q
    records,sources,plan=setup(tmp_path);p=records['evidence_steps/E']['payload'];p['statement']='既有判斷。'
    unit={'paragraph_key':'S0001','quote':sources['S']['paragraphs']['S0001']['text'],'statement':'既有判斷。'}
    previous=saved_inventory(tmp_path,sources,[unit]);alias='R'+e.digest('evidence_steps/E')[:8]
    state=stage.coverage_state({'evidence_steps/E':p})
    f.jev(previous/'coverage'/'A',Ports(),state,{'U0':q.coverage_unit(unit['statement'])})
    ports=Ports();root=tmp_path/'run'
    result=stage.run_group(None,records,sources,plan['groups'][0],['evidence_steps/E'],[unit],root,ports,previous,apply=False)
    assert result['status']=='verified'
    assert len([r for role,r in ports.calls if role=='jev'])==1  # Fidelity only, not coverage.
    assert list((root/'coverage'/'A'/'reuse').glob('*.json'))


def test_protected_record_and_unbound_important_unit_are_explicit_handoffs(tmp_path):
    records,sources,plan=setup(tmp_path);records['evidence_steps/E']['payload']['review_status']='human_approved'
    before=deepcopy(records)
    result=stage.run_group(None,records,sources,plan['groups'][0],['evidence_steps/E'],[],tmp_path/'run',Ports(),saved_inventory(tmp_path,sources),apply=False)
    assert result['updates']=={} and result['status']=='needs_attention' and records==before
    assert result['pending'][0]['reason']=='protected_record'


def test_unknown_call_and_duplicate_group_selection_stop_before_calls(seeded):
    store,sources,plan,previous,root=seeded
    ports=Ports()
    with pytest.raises(ValueError,match='selection'):stage.run(store,'S',plan,root,ports,previous,group_ids=['A','A'])
    assert not ports.calls
    e.save(root/'fidelity'/'jev'/'unsettled'/'started.json',{'request_sha256':'unknown'})
    with pytest.raises(ValueError,match='no saved reply'):stage.run(store,'S',plan,root,ports,previous)
    assert not ports.calls


def test_fidelity_output_cannot_inject_a_source_binding_change(tmp_path):
    records,sources,plan=setup(tmp_path)
    class Inject(Ports):
        def repair(self,request):
            raw=super().repair(request)
            if request['input']['task']=='evidence_fidelity_repair':raw['items'][0]['source_fragment_ids']=['OTHER']
            return raw
    with pytest.raises(ValueError,match='Extra inputs'):stage.run_group(None,records,sources,plan['groups'][0],['evidence_steps/E'],[],tmp_path/'run',Inject(),saved_inventory(tmp_path,sources),apply=False)


def test_sql_committed_without_receipt_recovers_before_any_models(seeded,monkeypatch):
    store,sources,plan,previous,root=seeded;apply=store.apply_plan
    def crash(*args,**kw):
        apply(*args,**kw);raise RuntimeError('after SQL commit')
    monkeypatch.setattr(store,'apply_plan',crash)
    with pytest.raises(RuntimeError,match='after SQL'):stage.run(store,'S',plan,root,Ports(),previous,apply=True)
    monkeypatch.setattr(store,'apply_plan',lambda *a,**k:pytest.fail('second SQL transaction'))
    replay=Ports();result=stage.run(store,'S',plan,root,replay,previous,apply=True)
    assert result['status']=='completed' and replay.calls==[]
    assert len(list((root/'groups').glob('*/*/commit.json')))==1


def test_coverage_creation_is_not_reviewed_again_on_resume(seeded,tmp_path):
    store,sources,plan,_,root=seeded
    unit={'paragraph_key':'S0001','quote':sources['S']['paragraphs']['S0001']['text'],'statement':'聽從是相信的檢驗。'}
    previous=saved_inventory(tmp_path/'with-units',sources,[unit])
    first=stage.run(store,'S',plan,root,Ports(),previous,apply=True)
    assert first['status']=='completed' and any(k.startswith('observations/') for k in first['groups'][0]['updates'])
    replay=Ports();second=stage.run(store,'S',plan,root,replay,previous,apply=True)
    assert second['status']=='completed' and replay.calls==[]


def test_shared_record_cannot_be_overwritten_by_conflicting_coverage_edits(tmp_path):
    records,sources,plan=setup(tmp_path);units=[{'paragraph_key':'S0001','quote':sources['S']['paragraphs']['S0001']['text'],
        'statement':'需保存的意思'+str(i)} for i in range(2)]
    class Conflict(Ports):
        def repair(self,request):
            raw=super().repair(request)
            if request['input']['task']=='evidence_coverage_repair':
                for i,item in enumerate(raw['items'][0]['items']):item.update(action='enrich',record_key='evidence_steps/E',record_type='evidence_steps',statement='不同的最終表述'+str(i))
            return raw
    root=tmp_path/'run'
    with pytest.raises(ValueError,match='conflict'):stage.run_group(None,records,sources,plan['groups'][0],['evidence_steps/E'],units,root,Conflict(),saved_inventory(tmp_path,sources),apply=False)
    assert not list((root/'groups').glob('*/*/commit-intent.json'))
    # Only the independently valid fidelity decision may be accepted, not the conflicting coverage output.
    for p in (root/'accepted').glob('*.json'):
        assert 'coverage-decisions' not in json.loads(p.read_text())['decision_path']


def test_shared_actual_write_names_prior_group_and_allows_same_scope_subset_resume(seeded,tmp_path):
    store,sources,plan,_,root=seeded
    good='沒有聽從祂的話，顯明沒有真正相信祂。'
    payload=store.get_record_state('evidence_steps','E')['payload'];payload['statement']=good
    store.apply_plan(store.plan_package({'package_id':'shared-layer-fixture','evidence_steps':[payload],
        'observations':[{'observation_id':'O','statement':'拒絕明白祂的話，顯明沒有真正相信祂。','source_fragment_ids':['F0']}]}))
    plan['groups'][1]['lower_record_keys']=['observations/O']
    unit={'paragraph_key':'S0001','quote':sources['S']['paragraphs']['S0001']['text'],'statement':good}
    previous=saved_inventory(tmp_path/'shared',sources,[unit])
    first=stage.run(store,'S',plan,root,Ports(),previous,apply=True)
    assert first['status']=='needs_attention' and first['affected_prior_group_ids']==['A']
    ports=Ports();second=stage.run(store,'S',plan,root,ports,previous,group_ids=['A'],apply=True)
    assert second['status']=='completed' and second['affected_prior_group_ids']==[]
    assert second['authorized_group_ids']==['A','B'] and second['requested_group_ids']==['A']
    assert not any(role=='gpt' for role,req in ports.calls)


def test_subset_resume_cannot_expand_original_authorized_groups(seeded):
    store,sources,plan,previous,root=seeded
    stage.run(store,'S',plan,root,Ports(),previous,group_ids=['A'],apply=True)
    ports=Ports()
    with pytest.raises(ValueError,match='expands frozen scope'):stage.run(store,'S',plan,root,ports,previous,group_ids=['B'],apply=True)
    assert ports.calls==[]


@pytest.mark.parametrize('bound', [True, False])
def test_enrichment_uses_bound_support_without_requiring_oral_bridge(tmp_path,bound):
    text='園主差遣愛子，期待園戶尊敬他。結果呢？他們怎麼樣？園戶把兒子殺了。'
    quotes=[('S0001','園主差遣愛子，期待園戶尊敬他。'),('S0001','園戶把兒子殺了。'),('S0001',text)]
    records,sources=fixture(tmp_path,[text],quotes)
    records['evidence_steps/E']['payload'].update(statement='園主差遣愛子。',source_fragment_ids=['F0','F1'] if bound else ['F0'])
    group={'group_id':'A','paragraph_keys':['S0001'],'lower_record_keys':['evidence_steps/E'],'reference_record_keys':[]}
    unit={'paragraph_key':'S0001','quote':text,'statement':'園主期待園戶尊敬愛子，園戶卻殺了他。'}
    class Enrich(Ports):
        def repair(self,request):
            raw=super().repair(request)
            if request['input']['task']=='evidence_coverage_repair':
                raw['items'][0]['items'][0].update(action='enrich',record_key='evidence_steps/E',record_type='evidence_steps',
                    statement=unit['statement'],fragment_ids=['F0','F1'])
            return raw
    result=stage.run_group(None,records,sources,group,['evidence_steps/E'],[unit],tmp_path/'run',Enrich(),saved_inventory(tmp_path,sources,[unit]),apply=False)
    if bound:
        assert result['status']=='verified'
        assert result['updates']['evidence_steps/E']['statement']==unit['statement']
        assert result['updates']['evidence_steps/E']['source_fragment_ids']==['F0','F1']
    else:
        assert result['status']=='needs_attention'
        assert result['pending'][0]['reason']=='existing_record_needs_source_binding_change'
        assert not result['updates']


def test_coverage_gpt_receives_position_title_as_readonly_existing_content(tmp_path):
    records,sources,plan=setup(tmp_path)
    records['position_nodes/P']={'retired':False,'payload':{'position_id':'P','title':'路加使用馬可加L文件。',
        'attribution':'教授所反對的學說','source_fragment_ids':['F0']}}
    unit={'paragraph_key':'S0001','quote':sources['S']['paragraphs']['S0001']['text'],'statement':'需要判定已有承載的意思。'}
    class KeepPosition(Ports):
        def repair(self,request):
            if request['input']['task']=='evidence_coverage_repair':
                assert request['input']['records']['position_nodes/P']['title']=='路加使用馬可加L文件。'
                assert request['input']['records']['position_nodes/P']['source_fragment_ids']==['F0']
                assert request['input']['records']['evidence_steps/E']['source_fragment_ids']==['F0']
                assert 'position_nodes/P' not in request['input']['writable_keys']
                return {'items':[{'job_id':'A','items':[{'job_id':'U0','action':'keep','record_key':'position_nodes/P',
                    'record_type':'observations','fragment_ids':[],'reason':'已有反方Position保存，不重複建卡。'}]}]}
            return super().repair(request)
    result=stage.run_group(None,records,sources,plan['groups'][0],['evidence_steps/E'],[unit],tmp_path/'run',KeepPosition(),saved_inventory(tmp_path,sources,[unit]),apply=False)
    assert all(not k.startswith(('position_nodes/','observations/')) for k in result['updates'])


def test_corrected_reference_input_retains_prior_unapplied_disposition(tmp_path):
    records,sources,plan=setup(tmp_path)
    records['evidence_steps/E']['payload']['statement']='既有表述。'
    records['position_nodes/P']={'retired':False,'payload':{'position_id':'P','title':'原參考內容。','source_fragment_ids':['F0']}}
    units=[{'paragraph_key':'S0001','quote':sources['S']['paragraphs']['S0001']['text'],'statement':t} for t in ['候選一。','候選二。']]
    class Pending(Ports):
        def repair(self,request):
            if request['input']['task']!='evidence_coverage_repair':return super().repair(request)
            title=request['input']['records']['position_nodes/P']['title']
            return {'items':[{'job_id':'A','items':[
                {'job_id':'U0','action':'enrich','record_key':'evidence_steps/E','record_type':'evidence_steps',
                 'fragment_ids':['F0'],'statement':'增補'+title,'reason':'依完整參考判定'+title},
                {'job_id':'U1','action':'needs_source','record_key':'','record_type':'observations','fragment_ids':[],'reason':'待補來源。'}]}]}
    root=tmp_path/'run';previous=saved_inventory(tmp_path,sources,units)
    first=stage.run_group(None,records,sources,plan['groups'][0],['evidence_steps/E'],units,root,Pending(),previous,apply=False)
    originals={p:p.read_bytes() for p in (root/'coverage-decisions').glob('*/*.json')}
    assert first['status']=='needs_attention' and len(originals)==1
    records['position_nodes/P']['payload']['title']='補齊後參考內容。'
    second=stage.run_group(None,records,sources,plan['groups'][0],['evidence_steps/E'],units,root,Pending(),previous,apply=False)
    assert second['status']=='needs_attention'
    assert second['updates']['evidence_steps/E']['statement']!=first['updates']['evidence_steps/E']['statement']
    assert all(p.read_bytes()==raw for p,raw in originals.items())
    assert len(list((root/'coverage-decisions').glob('*/*.json')))==2
    assert len(list((root/'groups/A').glob('*/verification.json')))==2
    assert not list((root/'coverage-accepted').glob('*.json'))


def test_new_record_with_omitted_optional_fields_keeps_acceptance_after_sql(seeded,tmp_path):
    store,sources,plan,_,root=seeded
    unit={'paragraph_key':'S0001','quote':sources['S']['paragraphs']['S0001']['text'],'statement':'原文有支持的重要材料。'}
    previous=saved_inventory(tmp_path/'no-optionals',sources,[unit])
    class WithoutOptionals(Ports):
        def repair(self,request):
            raw=super().repair(request)
            if request['input']['task']=='evidence_coverage_repair':
                for item in raw['items'][0]['items']:
                    for field in ['scripture_refs','speaker','stance','attribution']:item.pop(field,None)
            return raw
    first=stage.run(store,'S',plan,root,WithoutOptionals(),previous,apply=True)
    assert first['status']=='completed'
    created=[p for k,p in first['groups'][0]['updates'].items() if k.startswith('observations/')]
    assert len(created)==1 and created[0]['scripture_refs']==[]
    replay=WithoutOptionals();second=stage.run(store,'S',plan,root,replay,previous,apply=True)
    assert second['status']=='completed' and replay.calls==[]


@pytest.mark.parametrize('key',['evidence_steps/E','', 'evidence_steps/OUTSIDE'])
def test_coverage_source_handoff_keeps_exact_writable_target(tmp_path,key):
    records,sources,plan=setup(tmp_path)
    unit={'paragraph_key':'S0001','quote':sources['S']['paragraphs']['S0001']['text'],'statement':'待核定的重要意思。'}
    class SourceHandoff(Ports):
        def repair(self,request):
            if request['input']['task']!='evidence_coverage_repair':return super().repair(request)
            return {'items':[{'job_id':'A','items':[{'job_id':'U0','action':'needs_source','record_key':key,
                'record_type':'evidence_steps','fragment_ids':['F0'],'reason':'須補充已知記錄的來源。'}]}]}
    def run():return stage.run_group(None,records,sources,plan['groups'][0],['evidence_steps/E'],[unit],
        tmp_path/'run',SourceHandoff(),saved_inventory(tmp_path/'prior',sources,[unit]),apply=False)
    if key.endswith('OUTSIDE'):
        with pytest.raises(ValueError,match='other-group target'):run()
    else:
        result=run();assert result['status']=='needs_attention'
        finding=next(p for p in result['pending'] if p.get('unit_id')=='U0')
        assert finding.get('object_key')==(key or None)


@pytest.mark.parametrize('count', [1, 2])
def test_coverage_group_id_is_corrected_only_for_one_candidate(tmp_path, count):
    records, sources, plan = setup(tmp_path)
    group = plan['groups'][0]
    units = [
        {'paragraph_key': 'S0001', 'quote': sources['S']['paragraphs']['S0001']['text'],
         'statement': f'重要觀察 {i}。'}
        for i in range(count)
    ]

    class GroupIdReply(Ports):
        def repair(self, request):
            reply = super().repair(request)
            if request['input']['task'] == 'evidence_coverage_repair':
                reply['items'][0]['items'][0]['job_id'] = group['group_id']
            return reply

    root = tmp_path / 'run'
    previous = saved_inventory(tmp_path, sources, units)

    def run():
        return stage.run_group(None, records, sources, group, ['evidence_steps/E'], units,
                               root, GroupIdReply(), previous, apply=False)

    if count == 1:
        assert run()['status'] == 'verified'
        assert len(list((root / 'coverage-id-corrections').glob('*.json'))) == 1
    else:
        with pytest.raises(ValueError, match='coverage disposition must address every unit'):
            run()
        assert not (root / 'coverage-id-corrections').exists()


def test_coverage_split_checks_only_authorized_saved_units_and_replays(tmp_path):
    jobs = [{'job_id': key, 'unit': {'statement': key}, 'required_fragment_ids': ['F']}
            for key in ('U15', 'U18', 'U20', 'U21')]
    raw = {'items': [{'job_id': 'FG007', 'items': [{'job_id': 'FG007'}]}]}
    ports = Ports()
    with pytest.raises(ValueError, match='no unknown unit'):
        stage.split_coverage_disposition(tmp_path, ports, 'FG007', jobs, raw, 'Verify coverage.', {})
    assert not ports.calls
    ports.coverage_split_authority = {
        'group_id': 'FG007', 'reply_sha256': e.digest(raw), 'jobs_sha256': e.digest(jobs),
        'owner_direction': 'Check the four units independently',
    }
    check = lambda: stage.split_coverage_disposition(tmp_path, ports, 'FG007', jobs, raw, 'Verify coverage.', {})
    edits = check()
    assert [edit.job_id for edit in edits] == [job['job_id'] for job in jobs]
    assert [req['input']['jobs'][0]['candidates'] for _, req in ports.calls] == [[job] for job in jobs]
    ports.calls.clear()
    assert check() == edits
    assert not ports.calls


@pytest.mark.parametrize('mode', ['literal', 'after_retry', 'stance', 'invalid_quote'])
def test_empty_repair_uses_only_verified_source_bindings(postgres_store, tmp_path, monkeypatch, mode):
    preceding = '那尼哥丁姆這個人，他來找耶穌的時候，他有很多很好的資格，很好的背景。'
    existing = '因為聖經說他是法利賽人。'
    records, sources = fixture(tmp_path, [preceding + existing], [('S0001', existing)])
    doc = deepcopy(records['source_documents/S']['payload'])
    doc.pop('source_body_sha256')
    doc.pop('source_sha256')
    payload = records['evidence_steps/E']['payload']
    payload.update(statement='尼哥丁姆有很好的資格和背景，因為他是法利賽人。', stance='asserted')
    package = {'package_id': 'nicodemus-empty-repair', 'source_documents': [doc],
               'source_fragments': [records['source_fragments/F0']['payload']], 'evidence_steps': [payload]}
    if mode == 'literal':
        package['observations'] = [{'observation_id': 'O', 'statement': '尼哥丁姆是法利賽人。',
                                    'source_fragment_id': 'F0'}]
    postgres_store.apply_plan(postgres_store.plan_package(package))
    before = postgres_store.get_record_state('evidence_steps', 'E')
    plan = {'source_id': 'S', 'source_body_sha256': sources['S']['body_sha256'], 'groups': [
        {'group_id': 'A', 'title': '尼哥丁姆', 'paragraph_keys': ['S0001'],
         'lower_record_keys': ['evidence_steps/E'], 'reference_record_keys': []}]}
    if mode == 'literal':
        plan['groups'][0]['lower_record_keys'].append('observations/O')
        observation_before = postgres_store.get_record_state('observations', 'O')
    monkeypatch.setattr(stage.components, 'check', lambda *a, **kw: {
        'record_fidelity': 'partial',
        'stance_consistency': 'different' if mode == 'stance' else 'matched',
        'speaker_consistency': 'matched',
    })

    class Repair(Ports):
        correct_empty_evidence_once = mode == 'after_retry'

        def repair(self, request):
            task = request['input']['task']
            if task not in {'source_binding_completion', 'evidence_fidelity_repair'}:
                return super().repair(request)
            self.calls.append(('gpt', request))
            jobs = request['input']['jobs']
            if task == 'source_binding_completion':
                job, = jobs
                assert job['job_id'] == 'evidence_steps/E'
                assert job['material']['nearby_paragraph_keys'] == ['S0001']
                quote = '不存在的引文。' if mode == 'invalid_quote' else preceding
                return {'items': [{'job_id': job['job_id'], 'action': 'bind', 'reason': '前句支持原摘要。',
                                   'quotes': [{'paragraph_key': 'S0001', 'quote': quote}]}]}
            if mode == 'stance':
                reason = '補上前句，並將立場改為轉述。'
            elif mode == 'after_retry' and 'previous_empty_repair' not in jobs[0]:
                reason = '前句交代尼哥丁姆的背景。'
            else:
                reason = '補上前句，與現有引文中的「因為」合讀，可確認教授以法利賽人身分說明尼哥丁姆的資格和背景。'
            return {'items': [
                {'job_id': job['job_id'], 'action': 'repair', 'reason': reason}
                if job['job_id'] == 'evidence_steps/E' else
                {'job_id': job['job_id'], 'action': 'keep', 'reason': '原引文已直接支持。'}
                for job in jobs]}

    ports = Repair()
    previous = saved_inventory(tmp_path/'inventory', sources)
    root = tmp_path/'run'
    if mode == 'stance':
        with pytest.raises(ValueError, match='Evidence repair has no effective changes'):
            stage.run(postgres_store, 'S', plan, root, ports, previous, apply=True)
        assert postgres_store.get_record_state('evidence_steps', 'E') == before
        assert not any(req['input']['task'] == 'source_binding_completion'
                       for role, req in ports.calls if role == 'gpt')
        return
    result = stage.run(postgres_store, 'S', plan, root, ports, previous, apply=True)
    post = postgres_store.get_record_state('evidence_steps', 'E')
    selection_calls = [req for role, req in ports.calls if role == 'gpt'
                       and req['input']['task'] == 'source_binding_completion']
    if mode in {'literal', 'after_retry'}:
        assert result['status'] == 'completed', result['groups']
        assert len(selection_calls) == 1
        a, b = before['payload'], post['payload']
        assert a['statement'] == b['statement'] and a['stance'] == b['stance'] and a['speaker'] == b['speaker']
        assert b['source_fragment_ids'][0] == 'F0'
        fragment = postgres_store.get_record_state('source_fragments', b['source_fragment_ids'][1])['payload']
        assert fragment['verbatim_excerpt'] == preceding
        if mode == 'literal':
            assert postgres_store.get_record_state('observations', 'O') == observation_before
            assert sum(req['input']['task'] == 'evidence_fidelity_repair' for role, req in ports.calls if role == 'gpt') == 1
    else:
        assert result['status'] == 'needs_attention'
        assert post == before
        assert bool(selection_calls) == (mode == 'invalid_quote')
    replay = Repair()
    assert stage.run(postgres_store, 'S', plan, root, replay, previous, apply=True)['status'] == result['status']
    assert not replay.calls
