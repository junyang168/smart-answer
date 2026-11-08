"""Flash regression cases use actual SQL and frozen fake provider responses."""
from copy import deepcopy
import json
from pathlib import Path
import pytest

from backend.pipeline import claim_flash_common as f, claim_flash_lower as lower
from backend.pipeline import claim_flash_pairs as pairs, claim_update_flash as flash
from backend.pipeline import evidence_refresh as e
from backend.tests.test_evidence_refresh import sample
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres


class Ports:
    identity={'jev':e.MODEL,'gpt':{'backend':'test','model':'test','effort':'medium'}}
    def __init__(self,*,choices=None,reply=None):
        self.choices=choices or {};self.reply=reply;self.calls=[]
    def judge(self,request):
        self.calls.append(('jev',request))
        answers={}
        for name,spec in request['questions'].items():
            default=(0. if spec['type']=='noul' else 'supported' if name.startswith('component_') else 'none' if name.endswith('_claim')
                     else {'record_fidelity':'faithful','decomposition_fidelity':'faithful',
                           'speaker_classification':'professor','stance_classification':'asserted','questioner_classification':'professor',
                           'statement_fidelity':'faithful','support':'supported','scripture':'complete',
                           'missing_lower':'covered','conclusion_candidate':'background_or_example',
                           'answer_links':'complete','reason_valid':'grounded','relation':'a_to_b_supports'}.get(name,'no'))
            if 'current_paragraphs' in request['state']:default='none'
            chosen=self.choices.get(name,default)
            if callable(chosen):chosen=chosen(request)
            if spec['type']=='noul':
                answers[name]={'type':'noul','noul':chosen}
                continue
            answers[name]={'type':'choice','choice':chosen,'probabilities':{k:float(k==chosen) for k in spec['criteria']}}
        return {'model':e.MODEL,'answers':answers}
    def repair(self,request):
        self.calls.append(('gpt',request))
        if request['input']['task']=='lower_decomposition':
            return {'items':[{'job_id':j['job_id'],'items':[
                {'object_key':r['object_key'],'components':[{'role':'judgment','text':r['statement']}]} for r in j['records']
            ]} for j in request['input']['jobs']]}
        if self.reply:
            result=self.reply(request)
            if result is not None:return result
        task=request['input']['task'];jobs=request['input']['jobs']
        if task=='lower_repair':
            return {'items':[{'job_id':j['job_id'],'action':'repair',
                'statement':'沒有聽從祂的話，顯明沒有真正相信祂。','speaker':'professor','stance':'asserted','scripture_refs':[],
                'quotes':[{'paragraph_key':'S0001','quote':'你沒有聽從祂的話，你根本沒信。'}],'reason':'恢復行為判準。'} for j in jobs]}
        if task=='lower_coverage':
            return {'items':[{'job_id':j['job_id'],'action':'resolved','new_records':[], 'conclusions':[],
                            'reason':'既有資料已覆蓋。'} for j in jobs]}
        if task in {'missing_attachments','missing_claim_relations'}:
            return {'items':[{'job_id':j['job_id'],'pairs':[],'unresolved_keys':[], 'reason':'沒有額外直接連接。'} for j in jobs]}
        raise AssertionError('unexpected task: '+task)


def complete(sample,ports=None):
    store,root,_=sample
    ports=ports or Ports()
    result=lower.run(store,'S',root/'lower',ports,apply=True)
    assert result['status'] in {'applied','verified'},result
    return store,root,ports


def test_whole_source_census_includes_unowned_evidence_and_observation(sample):
    store,root,_=sample
    ev=deepcopy(store.get_record_state('evidence_steps','E')['payload'])
    ev.update(evidence_step_id='ORPHAN',produced_claim_ids=[])
    store.apply_plan(store.plan_package({'package_id':'orphans','evidence_steps':[ev],
        'observations':[{'observation_id':'O','statement':'教授把聽從作為相信的判準。','source_fragment_id':'F'}]}))
    ports=Ports();result=lower.run(store,'S',root/'lower',ports,apply=True)
    assert {'evidence_steps/E','evidence_steps/ORPHAN','observations/O'}<=set(result['fidelity'])
    assert set(result['fidelity'])=={j['object_key'] for j in result['outcomes']}
    assert 'evidence_steps/ORPHAN' in flash.orphans(f.load_source(store,'S')[0])


def test_claim_layer_consumes_lower_proof_without_rechecking_fidelity(sample):
    store,root,ports=complete(sample)
    count=len(ports.calls)
    result=flash.run_group(store,'S',['C'],root/'lower',root/'group',ports,apply=True)
    assert result['applied']
    checks=[q for role,q in ports.calls[count:] if role=='jev']
    assert not any('record_fidelity' in q['questions'] or 'missing_lower' in q['questions'] for q in checks)
    statement=next(q for q in checks if 'statement_fidelity' in q['questions'])
    assert set(statement['questions'])=={'statement_fidelity','support','scripture'}
    pair=next(q for q in checks if 'relation' in q['questions'])
    assert set(pair['questions'])=={'relation'}
    assert pair['state']['A']['record_type']=='evidence_steps'
    n=len(ports.calls)
    assert flash.run_group(store,'S',['C'],root/'lower',root/'group',ports,apply=True)['applied']
    assert len(ports.calls)==n


def test_pair_partial_support_does_not_require_whole_claim_entailment(sample):
    store,root,ports=complete(sample)
    flash.run_group(store,'S',['C'],root/'lower',root/'group',ports,apply=True)
    q=next(q for role,q in ports.calls if role=='jev' and 'relation' in q['questions'])
    assert '不要求單項證明整個 Claim' in q['questions']['relation']['instructions']
    assert not any(role=='gpt' and q['input']['task']=='pair_attachment' for role,q in ports.calls)


def test_lower_source_drift_and_content_drift_are_not_reused(sample):
    store,root,ports=complete(sample)
    p=deepcopy(store.get_record_state('evidence_steps','E')['payload']);p['statement']='改過意思。'
    store.apply_plan(store.plan_package({'package_id':'drift','evidence_steps':[p]}))
    count=len(ports.calls)
    with pytest.raises(ValueError,match='lower content changed'):
        flash.run_group(store,'S',['C'],root/'lower',root/'group',ports)
    assert len(ports.calls)==count


def test_new_owner_does_not_repeat_lower_checks(sample):
    store,root,ports=complete(sample)
    p=deepcopy(store.get_record_state('evidence_steps','E')['payload']);p['produced_claim_ids'].append('NEWOWNER')
    store.apply_plan(store.plan_package({'package_id':'owner','claims':[{'claim_id':'NEWOWNER','claim_type':'exegesis',
        'statement':'相信涉及聽從。','evidence_step_ids':['E'],'occurrences':[{'source_id':'S','anchors':[]}]}],'evidence_steps':[p]}))
    count=len(ports.calls)
    assert flash.run_group(store,'S',['C'],root/'lower',root/'group',ports,apply=True)['applied']
    assert not any(role=='jev' and 'record_fidelity' in q['questions'] for role,q in ports.calls[count:])


def test_gpt_overwrites_jev_without_veto_or_second_round(sample):
    store,root,ports=complete(sample,Ports(choices={'record_fidelity':'unsupported'}))
    assert store.get_record_state('evidence_steps','E')['payload']['statement']=='沒有聽從祂的話，顯明沒有真正相信祂。'
    assert len([q for role,q in ports.calls if role=='jev' and 'record_fidelity' in q['questions']])==1
    assert len([q for role,q in ports.calls if role=='gpt' and q['input']['task']=='lower_repair'])==1


def test_pairs_use_revised_statement_and_not_old_statement(sample):
    store,root,_=complete(sample)
    def fix(req):
        if req['input']['task']=='claim_statement':
            return {'items':[{'claim_id':'C','action':'repair','statement':'沒有聽從祂的話，顯明沒有真正相信祂。',
                'evidence_step_ids':['E'],'scripture_refs':[],'reason':'與原文一致。'}]}
    ports=Ports(choices={'statement_fidelity':'changed'},reply=fix)
    result=flash.run_group(store,'S',['C'],root/'lower',root/'group',ports,apply=True)
    assert result['applied']
    q=next(q for role,q in ports.calls if role=='jev' and 'relation' in q['questions'])
    assert q['state']['B']['statement']=='沒有聽從祂的話，顯明沒有真正相信祂。'


def test_invalid_gpt_claim_scope_cannot_write(sample):
    store,root,_=complete(sample)
    def bad(req):
        if req['input']['task']=='claim_statement':
            return {'items':[{'claim_id':'C','action':'repair','statement':'無據改動。',
                'evidence_step_ids':['FOREIGN'],'scripture_refs':[],'reason':'x'}]}
    before=store.get_record_state('claims','C')
    with pytest.raises(ValueError,match='unverified Evidence'):
        flash.run_group(store,'S',['C'],root/'lower',root/'group',Ports(choices={'support':'partial'},reply=bad),apply=True)
    assert store.get_record_state('claims','C')==before


def test_payload_splits_on_job_count_and_bytes_before_any_call(tmp_path,monkeypatch):
    monkeypatch.setattr(f,'GPT_BYTES',1500+len(f.SEMANTIC_REVIEW.encode()));monkeypatch.setattr(f,'GPT_JOBS',2)
    ports=Ports(reply=lambda req:{'items':[]})
    list(f.gpt(tmp_path,ports,'small',[{'job_id':str(i),'text':'x'*400} for i in range(5)],{},'small'))
    requests=[req for role,req in ports.calls if role=='gpt']
    assert len(requests)==3 and all(len(q['input']['jobs'])<=2 for q in requests)
    ports.calls=[]
    with pytest.raises(ValueError,match='one complete GPT job'):
        list(f.gpt(tmp_path/'large',ports,'small',[{'text':'ok'},{'text':'x'*3000}],{},'small'))
    assert not ports.calls


def test_unknown_gpt_call_is_not_automatically_repeated(tmp_path):
    ports=Ports(reply=lambda req: (_ for _ in ()).throw(RuntimeError('network lost')))
    with pytest.raises(RuntimeError):list(f.gpt(tmp_path,ports,'unknown',[{'job_id':'X'}],{},'small'))
    count=len(ports.calls)
    with pytest.raises(ValueError,match='no saved reply'):list(f.gpt(tmp_path,ports,'unknown',[{'job_id':'X'}],{},'small'))
    assert len(ports.calls)==count


@pytest.mark.parametrize('field,task',[('job_id','lower_repair'),('claim_id','claim_statement')])
def test_partial_gpt_batch_reuses_answer_and_requests_only_missing_once(tmp_path,field,task):
    from backend.pipeline.claim_flash_lower import Edits
    calls=[]
    def reply(req):
        calls.append(req)
        jobs=req['input']['jobs']
        return {'items':[{field:j['job_id'],'reason':'有據保留。'} for j in (jobs[:1] if len(calls)==1 else jobs)]}
    ports=Ports(reply=reply);jobs=[{'job_id':'A'},{'job_id':'B'},{'job_id':'C'}]
    result=list(f.gpt(tmp_path,ports,task,jobs,Edits.model_json_schema(),'test'))
    assert [i[field] for i in result[0][1]['items']]==['A','B','C']
    assert [j['job_id'] for j in calls[1]['input']['jobs']]==['B','C']
    assert calls[1]['schema']['properties']['items']['minItems']==2
    assert len(calls)==2
    assert list(f.gpt(tmp_path,ports,task,jobs,Edits.model_json_schema(),'test'))==result
    assert len(calls)==2


def test_partial_gpt_batch_has_no_unbounded_output_retry(tmp_path):
    from backend.pipeline.claim_flash_lower import Edits
    ports=Ports(reply=lambda req:{'items':[]})
    with pytest.raises(ValueError,match='exactly once'):
        list(f.gpt(tmp_path,ports,'lower_repair',[{'job_id':'A'}],Edits.model_json_schema(),'test'))
    assert len(ports.calls)==2
    with pytest.raises(ValueError,match='exactly once'):
        list(f.gpt(tmp_path,ports,'lower_repair',[{'job_id':'A'}],Edits.model_json_schema(),'test'))
    assert len(ports.calls)==2


@pytest.mark.parametrize('fixed',[False,True])
def test_lower_wrong_job_quote_gets_one_scoped_correction_not_expanded_authority(sample,fixed):
    store,root,_=sample;calls=[]
    def reply(req):
        if req['input']['task']!='lower_repair':return None
        job=req['input']['jobs'][0];calls.append(job)
        correcting='contract_error' in job
        return {'items':[{'job_id':job['job_id'],'action':'repair',
            'statement':'沒有聽從祂的話，顯明沒有真正相信祂。','speaker':'professor','stance':'asserted',
            'scripture_refs':[],'quotes':[{'paragraph_key':'S0001' if correcting and fixed else 'OTHER',
                'quote':'你沒有聽從祂的話，你根本沒信。'}],'reason':'只用本項原文。'}]}
    ports=Ports(choices={'record_fidelity':'unsupported'},reply=reply)
    if fixed:
        assert lower.run(store,'S',root/'lower',ports,apply=True)['applied']
    else:
        with pytest.raises(ValueError,match='unauthorized paragraph'):
            lower.run(store,'S',root/'lower',ports,apply=True)
    assert len(calls)==2
    count=len(ports.calls)
    if fixed:lower.run(store,'S',root/'lower',ports,apply=True)
    else:
        with pytest.raises(ValueError,match='unauthorized paragraph'):
            lower.run(store,'S',root/'lower',ports,apply=True)
    assert len(ports.calls)==count


@pytest.mark.parametrize('action',['repair','retire','needs_source'])
def test_deleted_question_uses_complete_source_then_repairs_or_retires_without_claim_edits(sample,action):
    store,root,_=sample
    records,sources=f.load_source(store,'S')
    stale=deepcopy(records['source_fragments/F']['payload'])
    stale.update(fragment_id='OLD-Q',anchor_state='source_deleted',verbatim_excerpt='以前的問題？',
        deleted_in_source_sha256=sources['S']['body_sha256'])
    store.apply_plan(store.plan_package({'package_id':'deleted-question','source_fragments':[stale],
        'questions':[{'question_id':'Q','text':'以前的問題？','questioner':'professor','source_fragment_id':'OLD-Q',
                      'answer_claim_ids':['C']}]}))
    claim=store.get_record_state('claims','C')
    def reply(req):
        if req['input']['task']=='lower_repair':
            return {'items':[{'job_id':j['job_id'],'action':'needs_source','statement':j['record']['text'],
                'speaker':'professor','stance':'asserted','scripture_refs':[],
                'quotes':[{'paragraph_key':'S0001','quote':'你不知道神的旨意，怎麼遵行？'}],
                'reason':'局部來源未見此提問。'} for j in req['input']['jobs']]}
        if req['input']['task']=='deleted_question_source':
            assert req['input']['current_source_paragraphs']=={'S0001':sources['S']['paragraphs']['S0001']['text']}
            return {'items':[{'job_id':j['job_id'],'action':action,'text':'不知道神的旨意，怎麼遵行？',
                'questioner':'professor','quotes':[{'paragraph_key':'S0001','quote':'你不知道神的旨意，怎麼遵行？'}],
                'reason':'完整現版原文已核對，舊記錄原引文已被來源遷移標為刪除。'} for j in req['input']['jobs']]}
    ports=Ports(reply=reply)
    result=lower.run(store,'S',root/'lower',ports,apply=True)
    assert store.get_record_state('claims','C')==claim
    if action=='needs_source':assert result['status']=='needs_attention'
    else:
        assert result['applied']
        saved=store.get_record_state('questions','Q')
        assert saved['retired']==(action=='retire')
        if action=='repair':
            assert saved['payload']['text']=='不知道神的旨意，怎麼遵行？'
            assert saved['payload']['answer_claim_ids']==['C']
        lower.verify(root/'lower',*f.load_source(store,'S'))
    count=len(ports.calls)
    lower.run(store,'S',root/'lower',ports,apply=True)
    assert len(ports.calls)==count


def test_question_link_context_can_keep_valid_empty_answers_without_creating_claim(sample):
    store,root,_=sample
    store.apply_plan(store.plan_package({'package_id':'context-question','questions':[{
        'question_id':'Q','text':'你不知道神的旨意，怎麼遵行？','questioner':'professor','source_fragment_id':'F'}]}))
    records,sources=f.load_source(store,'S')
    def reply(req):
        if req['input']['task']=='question_answers':
            assert req['input']['jobs'][0]['material']['source_context']['S0001']==sources['S']['paragraphs']['S0001']['text']
            return {'items':[{'job_id':'Q','action':'keep','answer_claim_ids':[],
                'reason':'所列 Claim 只談聽從與相信，未直接回答這個具體問題；保留空答案。'}]}
    ports=Ports(choices={'answer_links':'complete'},reply=reply)
    updates,pending=flash.question_links(records,sources,['claims/C'],{'S0001'},[],root,ports,required_gpt_ids=['Q'])
    assert not updates and not pending
    assert [q['input']['task'] for role,q in ports.calls if role=='gpt']==['question_answers']
    assert store.get_record_state('questions','Q')['payload']['answer_claim_ids']==[]


def test_old_pending_question_context_recovery_reuses_other_group_answers(sample,monkeypatch):
    store,root,_=sample
    store.apply_plan(store.plan_package({'package_id':'recover-q','questions':[{
        'question_id':'Q','text':'你不知道神的旨意，怎麼遵行？','source_fragment_id':'F'}]}))
    ports=Ports()
    lower.run(store,'S',root/'lower',ports,apply=True)
    original=flash.question_links
    monkeypatch.setattr(flash,'question_links',lambda *a,**kw:({},[{'object_key':'questions/Q','reason':'缺上下文。'}]))
    group=root/'group'
    assert flash.run_group(store,'S',['C'],root/'lower',group,ports,apply=True)['status']=='needs_attention'
    draft=json.loads((group/'draft.json').read_text());draft.pop('question_context_contract')
    (group/'draft.json').write_text(json.dumps(draft,ensure_ascii=False))
    monkeypatch.setattr(flash,'question_links',original)
    def reply(req):
        if req['input']['task']=='question_answers':
            assert req['input']['jobs'][0]['material']['source_context']
            return {'items':[{'job_id':'Q','action':'keep','answer_claim_ids':[],'reason':'沒有適合的 Claim 答案。'}]}
    ports.reply=reply;count=len(ports.calls)
    assert flash.run_group(store,'S',['C'],root/'lower',group,ports,apply=True)['applied']
    assert [q['input']['task'] for role,q in ports.calls[count:] if role=='gpt']==['question_answers']
    assert json.loads(next((group/'superseded').glob('draft-*.json')).read_text())==draft


@pytest.mark.parametrize('blocked_stage',['lower','claims'])
def test_campaign_reports_remaining_targets_and_actual_nested_obstacle(sample,monkeypatch,blocked_stage):
    store,root,_=sample
    pending=[{'object_key':'questions/Q','reason':'來源上下文未提供。'}]
    monkeypatch.setattr(lower,'run',lambda *a,**kw:{'status':'needs_attention' if blocked_stage=='lower' else 'verified',
        'pending':pending if blocked_stage=='lower' else [],'updates':{}})
    monkeypatch.setattr(flash,'run_group',lambda *a,**kw:{'status':'needs_attention','pending':pending})
    result=flash.run(store,'S',[['C']],root,Ports(),apply=True)
    assert result['status']=='needs_attention' and result['stage']==blocked_stage
    assert result['completed_claim_ids']==[] and result['remaining_claim_ids']==['C']
    assert result['pending']==pending


def test_indirect_observation_path_is_not_orphan_and_cycle_is_not_support():
    def row(**p):return {'payload':p,'retired':False}
    records={'claims/C':row(evidence_step_ids=['E']), 'evidence_steps/E':row(),
        'observations/O':row(),'observations/X':row(),'observations/Y':row(),
        'knowledge_relations/R':row(from_id='O',to_id='E',relation_type='supports'),
        'knowledge_relations/X':row(from_id='X',to_id='Y',relation_type='supports'),
        'knowledge_relations/Y':row(from_id='Y',to_id='X',relation_type='supports')}
    assert flash.orphans(records)=={'observations/X','observations/Y'}


def test_new_observation_to_claim_relation_uses_existing_polymorphic_model(sample):
    store,root,_=complete(sample)
    store.apply_plan(store.plan_package({'package_id':'o','observations':[{
        'observation_id':'O','statement':'行為的觀察。','source_fragment_id':'F'}]}))
    # Old extraction endpoint contracts remain unchanged; the additive Flash namespace is explicit.
    from backend.api.canonical_repository.knowledge_models import knowledge_relation_endpoints
    assert 'claims' not in knowledge_relation_endpoints('supports')[1]
    store.apply_plan(store.plan_package({'package_id':'flash-connection','knowledge_relations':[{
        'relation_id':'OCR','from_id':'O','to_id':'C','relation_type':'supports','reason':'觀察支持主張。',
        'record_namespace':f.VERSION}]}))
    assert 'observations/O' not in flash.orphans(f.load_source(store,'S')[0])


def test_coverage_creates_missing_observation_and_hands_up_an_owned_evidence_conclusion(sample):
    store,root,_=sample
    def coverage(req):
        if req['input']['task']=='lower_coverage':
            return {'items':[{'job_id':j['job_id'],'action':'resolved','reason':'保留論證。',
                'new_records':[{'record_type':'observations','statement':'教授以聽從檢驗信心。',
                    'speaker':'professor','stance':'asserted','scripture_refs':[],
                    'quotes':[{'paragraph_key':'S0001','quote':'你沒有聽從祂的話，你根本沒信。'}]}],
                'conclusions':[{'statement':'不知道神旨意就無法遵行。','support_keys':['evidence_steps/E'],
                    'quotes':[{'paragraph_key':'S0001','quote':'你不知道神的旨意，怎麼遵行？'}],
                    'reason':'教授以知道旨意為遵行的前提。'}]} for j in req['input']['jobs']]}
    ports=Ports(choices={'missing_lower':'missing_evidence','conclusion_candidate':'substantive_judgment'},reply=coverage)
    result=lower.run(store,'S',root/'lower',ports,apply=True)
    assert result['applied'] and len(result['conclusion_candidates'])==1
    oid=next(k for k in result['updates'] if k.startswith('observations/'))
    assert store.get_record_state(*oid.split('/'))
    records,sources=f.load_source(store,'S')
    lower.verify(root/'lower',records,sources)


def test_gpt_final_pair_decision_overwrites_uncertain_jev(sample):
    store,root,_=complete(sample)
    def answer(req):
        if req['input']['task']=='pair_attachment':
            return {'items':[{'job_id':j['job_id'],'action':'keep','relation_type':'supports',
                'direction':'a_to_b','reason':'完整原文表明是有效論據。'} for j in req['input']['jobs']]}
    ports=Ports(choices={'relation':'uncertain'},reply=answer)
    result=flash.run_group(store,'S',['C'],root/'lower',root/'group',ports,apply=True)
    assert result['applied']
    assert len([q for role,q in ports.calls if role=='jev' and 'relation' in q['questions']])==1
    assert len([q for role,q in ports.calls if role=='gpt' and q['input']['task']=='pair_attachment'])==1


def test_unique_current_quote_calibrates_without_a_semantic_repair_call(sample):
    store,root,_=sample
    p=deepcopy(store.get_record_state('source_fragments','F')['payload']);p['anchor_state']='unresolved'
    store.apply_plan(store.plan_package({'package_id':'bad-anchor','source_fragments':[p]}))
    ports=Ports();result=lower.run(store,'S',root/'lower',ports,apply=True)
    assert result['applied'] and not result['pending']
    assert store.get_record_state('source_fragments','F')['payload']['anchor_state']=='unresolved'
    assert store.get_record_state('evidence_steps','E')['payload']['source_fragment_ids']!=['F']
    assert not any(role=='gpt' and req['input']['task']=='lower_repair' for role,req in ports.calls)
    assert not any(role=='jev' and 'current_paragraphs' in req['state'] for role,req in ports.calls)


def test_protected_evidence_is_not_silently_rewritten(sample):
    store,root,_=sample
    p=deepcopy(store.get_record_state('evidence_steps','E')['payload']);p['review_status']='human_hold'
    store.apply_plan(store.plan_package({'package_id':'hold','evidence_steps':[p]}))
    result=lower.run(store,'S',root/'lower',Ports(choices={'record_fidelity':'unsupported'}),apply=True)
    assert result['status']=='needs_attention' and not result['applied']
    assert store.get_record_state('evidence_steps','E')['payload']['statement']==p['statement']


def test_source_change_after_lower_stops_without_new_calls(sample):
    store,root,ports=complete(sample)
    path=sample[2];path.write_text(path.read_text()+' ')
    count=len(ports.calls)
    with pytest.raises(ValueError):flash.run_group(store,'S',['C'],root/'lower',root/'group',ports)
    assert len(ports.calls)==count


def test_unrelated_new_lower_record_cannot_hide_behind_old_proof(sample):
    store,root,ports=complete(sample)
    store.apply_plan(store.plan_package({'package_id':'new-o','observations':[{'observation_id':'LATE','statement':'待核對。','source_fragment_id':'F'}]}))
    count=len(ports.calls)
    with pytest.raises(ValueError,match='census changed'):
        flash.run_group(store,'S',['C'],root/'lower',root/'group',ports)
    assert len(ports.calls)==count


def test_gpt_needs_source_blocks_atomic_group_commit(sample):
    store,root,_=complete(sample)
    def cannot(req):
        if req['input']['task']=='claim_statement':
            return {'items':[{'claim_id':'C','action':'needs_source','statement':'聽從與相信有關。',
                'evidence_step_ids':['E'],'scripture_refs':[],'reason':'需要額外來源。'}]}
    before=store.get_record_state('claims','C')
    result=flash.run_group(store,'S',['C'],root/'lower',root/'group',Ports(choices={'support':'uncertain'},reply=cannot),apply=True)
    assert result['status']=='needs_attention' and not result['applied']
    assert store.get_record_state('claims','C')==before


def test_question_answer_link_is_upper_layer_not_lower_fidelity(sample):
    store,root,_=sample
    store.apply_plan(store.plan_package({'package_id':'q','questions':[{'question_id':'Q',
        'text':'沒有聽從還是真正相信嗎？','source_fragment_id':'F','answer_claim_ids':[]}]}))
    lp=Ports();lower.run(store,'S',root/'lower',lp,apply=True)
    assert not any('answer_links' in q['questions'] for role,q in lp.calls if role=='jev')
    def answer(req):
        if req['input']['task']=='question_answers':
            return {'items':[{'job_id':'Q','action':'set','answer_claim_ids':['C'],'reason':'教授的回答由 C 承載。'}]}
    ports=Ports(choices={'answer_links':'missing'},reply=answer)
    result=flash.run_group(store,'S',['C'],root/'lower',root/'group',ports,apply=True)
    assert result['applied']
    assert store.get_record_state('questions','Q')['payload']['answer_claim_ids']==['C']
    records,sources=f.load_source(store,'S')
    lower.verify(root/'lower',records,sources)  # Only upper answer metadata changed.


def test_invalid_question_answer_cannot_write(sample):
    store,root,_=sample
    store.apply_plan(store.plan_package({'package_id':'q','questions':[{'question_id':'Q',
        'text':'沒有聽從還是真正相信嗎？','source_fragment_id':'F','answer_claim_ids':[]}]}))
    complete(sample)
    def bad(req):
        if req['input']['task']=='question_answers':
            return {'items':[{'job_id':'Q','action':'set','answer_claim_ids':['FOREIGN'],'reason':'x'}]}
    with pytest.raises(ValueError,match='exceeded provided Claims'):
        flash.run_group(store,'S',['C'],root/'lower',root/'group',Ports(choices={'answer_links':'missing'},reply=bad),apply=True)
    assert store.get_record_state('questions','Q')['payload']['answer_claim_ids']==[]


@pytest.mark.parametrize('peer_without_occurrence',[False,True])
@pytest.mark.parametrize('attachment_survives',[False,True])
def test_new_claim_is_handed_up_checked_linked_and_committed_once(sample,peer_without_occurrence,attachment_survives):
    store,root,_=sample
    if peer_without_occurrence:
        evidence=deepcopy(store.get_record_state('evidence_steps','E')['payload'])
        evidence['produced_claim_ids'].append('PEER')
        store.apply_plan(store.plan_package({'package_id':'no-occurrence-peer','evidence_steps':[evidence],
            'claims':[{'claim_id':'PEER','claim_type':'exegesis','statement':'另一項既有判斷。',
                       'evidence_step_ids':['E'],'occurrences':[]}]}))
        from backend.pipeline import claim_refresh as legacy
        _,_,_,peers=legacy.context(store,legacy.fetch_claims(store,['C']),['E'])
        assert 'PEER' in peers
    def reply(req):
        if req['input']['task']=='lower_coverage':
            return {'items':[{'job_id':j['job_id'],'action':'resolved','new_records':[],
                'conclusions':[{'statement':'不知道旨意就無法遵行。','support_keys':['evidence_steps/E'],
                    'quotes':[{'paragraph_key':'S0001','quote':'你不知道神的旨意，怎麼遵行？'}],
                    'reason':'前提與遵行之間有論證。'}],'reason':'交上層核對。'} for j in req['input']['jobs']]}
        if req['input']['task']=='conclusion_disposition':
            return {'items':[{'job_id':j['job_id'],'action':'new_claim','claim_id':'',
                'statement':'不知道神的旨意，就無法遵行祂的旨意。','evidence_step_ids':['E'],
                'scripture_refs':[],'answer_question_ids':[],'reason':'需要獨立保存前提。'} for j in req['input']['jobs']]}
    ports=Ports(choices={'conclusion_candidate':'substantive_judgment'},reply=reply)
    lower.run(store,'S',root/'lower',ports,apply=True)
    if not attachment_survives:ports.choices['relation']='none'
    result=flash.run_group(store,'S',['C'],root/'lower',root/'group',ports,apply=True)
    if not attachment_survives:
        assert result['status']=='needs_attention' and not result['applied']
        assert len(flash.active_claims(f.load_source(store,'S')[0]))==1+int(peer_without_occurrence)
        return
    assert result['applied'] and len(result['new_claim_ids'])==1
    cid=result['new_claim_ids'][0]
    assert set(store.get_record_state('evidence_steps','E')['payload']['produced_claim_ids'])=={'C',cid}|({'PEER'} if peer_without_occurrence else set())
    assert store.get_record_state('claims',cid)['payload']['occurrences'][0]['anchors']
    assert len([q for role,q in ports.calls if role=='jev' and 'statement_fidelity' in q['questions']])==2
    count=len(ports.calls)
    assert flash.run_group(store,'S',['C'],root/'lower',root/'group',ports,apply=True)['applied']
    assert len(ports.calls)==count


def test_source_identity_guard_detects_new_complete_chain_claim_without_occurrences(sample):
    from backend.pipeline import claim_refresh as legacy
    store,_,_=sample
    records,_=f.load_source(store,'S')
    index={k.split('/',1)[1]:{'statement':p['statement'],'attribution':p.get('attribution')}
        for k,p in flash.active_claims(records).items()}
    changed={**records['claims/C']['payload'],'statement':'更新過的主張。'}
    plan=store.plan_package({'package_id':'guarded-change','claims':[changed]},source_kind=legacy.VERSION)
    evidence=deepcopy(store.get_record_state('evidence_steps','E')['payload'])
    evidence['produced_claim_ids'].append('NEW-PEER')
    store.apply_plan(store.plan_package({'package_id':'concurrent-no-occurrence-peer','evidence_steps':[evidence],
        'claims':[{'claim_id':'NEW-PEER','claim_type':'exegesis','statement':'並行新增的既有判斷。',
                   'evidence_step_ids':['E'],'occurrences':[]}]}))
    state=records['claims/C']
    with pytest.raises(Exception,match='source Claim identities or statements changed'):
        store.apply_plan(plan,expected_current_source_claims={'S':index},
            expected_current_record_pins={('claims','C'):(state['revision'],state['content_sha256'],False)})
    assert store.get_record_state('claims','C')['payload']['statement']==records['claims/C']['payload']['statement']


def test_missing_non_orphan_evidence_connection_is_added_reciprocally(sample):
    store,root,_=complete(sample)
    ev=deepcopy(store.get_record_state('evidence_steps','E')['payload']);ev.update(evidence_step_id='E2',produced_claim_ids=['D'])
    store.apply_plan(store.plan_package({'package_id':'e2','evidence_steps':[ev],'claims':[{
        'claim_id':'D','statement':'另一個已保存的判斷。','claim_type':'exegesis','evidence_step_ids':['E2'],
        'occurrences':[{'source_id':'S','anchors':[]}]}]}))
    # New input needs its own honest lower-stage record, not reuse of the old census.
    lower.run(store,'S',root/'lower2',Ports(),apply=True)
    def add(req):
        task=req['input']['task']
        if task=='missing_attachments':
            return {'items':[{'job_id':j['job_id'],'pairs':[{'a':'evidence_steps/E2','b':'claims/C','reason':'同樣支持本主張。'}],
                'unresolved_keys':[],'reason':'補漏接。'} for j in req['input']['jobs']]}
        if task=='pair_attachment':
            return {'items':[{'job_id':j['job_id'],'action':'set','relation_type':'supports','direction':'a_to_b',
                             'reason':'行為觀察也支持本主張。'} for j in req['input']['jobs']]}
    result=flash.run_group(store,'S',['C'],root/'lower2',root/'group',Ports(reply=add),apply=True)
    assert result['applied']
    assert set(store.get_record_state('claims','C')['payload']['evidence_step_ids'])=={'E','E2'}
    assert set(store.get_record_state('evidence_steps','E2')['payload']['produced_claim_ids'])=={'C','D'}


def test_rejected_pair_removes_only_owner_link_and_preserves_shared_evidence(sample):
    store,root,_=complete(sample)
    result=flash.run_group(store,'S',['C'],root/'lower',root/'group',Ports(choices={'relation':'none'}),apply=True)
    assert result['applied']
    assert store.get_record_state('claims','C')['payload']['evidence_step_ids']==[]
    assert store.get_record_state('evidence_steps','E')['payload']['produced_claim_ids']==[]
    assert not store.get_record_state('evidence_steps','E')['retired']


def test_new_claim_from_observation_has_source_anchors_and_typed_relation(sample):
    store,root,_=sample
    def reply(req):
        if req['input']['task']=='lower_coverage':
            return {'items':[{'job_id':j['job_id'],'action':'resolved','new_records':[{
                'record_type':'observations','statement':'教授以知道旨意為遵行前提。','speaker':'professor','stance':'asserted',
                'scripture_refs':[],'quotes':[{'paragraph_key':'S0001','quote':'你不知道神的旨意，怎麼遵行？'}]}],
                'conclusions':[{'statement':'不知道神旨意就無法遵行。','support_keys':['new:0'],
                    'quotes':[{'paragraph_key':'S0001','quote':'你不知道神的旨意，怎麼遵行？'}],
                    'reason':'問句表達必要前提。'}],'reason':'保留觀察與結論。'} for j in req['input']['jobs']]}
        if req['input']['task']=='conclusion_disposition':
            return {'items':[{'job_id':j['job_id'],'action':'new_claim','claim_id':'',
                'statement':'不知道神的旨意，就無法遵行祂的旨意。','evidence_step_ids':[],
                'scripture_refs':[],'answer_question_ids':[],'reason':'獨立保存結論。'} for j in req['input']['jobs']]}
        if req['input']['task']=='pair_attachment':
            return {'items':[{'job_id':j['job_id'],'action':'set','relation_type':'supports','direction':'a_to_b',
                'reason':'教授的觀察支持該結論。'} for j in req['input']['jobs']]}
    ports=Ports(choices={'missing_lower':'missing_evidence','conclusion_candidate':'substantive_judgment'},reply=reply)
    lower.run(store,'S',root/'lower',ports,apply=True)
    result=flash.run_group(store,'S',['C'],root/'lower',root/'group',ports,apply=True)
    assert result['applied'] and result['new_claim_ids']
    cid=result['new_claim_ids'][0];p=store.get_record_state('claims',cid)['payload']
    assert p['evidence_step_ids']==[] and p['occurrences'][0]['anchors'][0]['observation_id']
    assert any(k.startswith('knowledge_relations/') and p['to_id']==cid for k,p in result['updates'].items())


def test_shared_implicit_and_explicit_support_pair_is_checked_only_once(sample):
    store,root,_=complete(sample)
    store.apply_plan(store.plan_package({'package_id':'same-edge','knowledge_relations':[{
        'relation_id':'SAME','from_id':'E','to_id':'C','relation_type':'supports',
        'reason':'行為支持判斷。','record_namespace':f.VERSION}]}))
    ports=Ports();result=flash.run_group(store,'S',['C'],root/'lower',root/'group',ports,apply=True)
    assert result['applied']
    assert len([q for role,q in ports.calls if role=='jev' and 'relation' in q['questions']])==1


def test_source_and_statement_drift_before_commit_block_all_group_writes(sample):
    store,root,_=complete(sample)
    def concurrent(req):
        p=deepcopy(store.get_record_state('claims','C')['payload']);p['statement']='同時有人改寫了。'
        store.apply_plan(store.plan_package({'package_id':'concurrent-target','claims':[p]}))
        return 'a_to_b_supports'
    before=store.get_record_state('evidence_steps','E')
    with pytest.raises(Exception):
        flash.run_group(store,'S',['C'],root/'lower',root/'group',Ports(choices={'relation':concurrent}),apply=True)
    assert store.get_record_state('evidence_steps','E')==before
    assert store.get_record_state('claims','C')['payload']['statement']=='同時有人改寫了。'


def test_sql_commit_receipt_loss_recovers_without_models_or_second_write(sample,monkeypatch):
    store,root,ports=complete(sample)
    original=e.save;failed=False
    def lost(path,value):
        nonlocal failed
        if Path(path)==root/'group/commit.json' and not failed:
            failed=True;raise OSError('receipt lost after SQL')
        return original(path,value)
    monkeypatch.setattr(e,'save',lost)
    with pytest.raises(OSError,match='receipt lost'):
        flash.run_group(store,'S',['C'],root/'lower',root/'group',ports,apply=True)
    saved=store.get_record_state('claims','C');count=len(ports.calls)
    assert flash.run_group(store,'S',['C'],root/'lower',root/'group',ports,apply=True)['applied']
    assert store.get_record_state('claims','C')==saved and len(ports.calls)==count


def test_group_pins_exclude_unrelated_source_objects(sample):
    store,root,_=sample
    # The unrelated object has no paragraph anchor in this topic; it is not a model input.
    store.apply_plan(store.plan_package({'package_id':'unrelated','claims':[{'claim_id':'Z','claim_type':'exegesis',
        'statement':'另一主題。','evidence_step_ids':[],'occurrences':[{'source_id':'S','anchors':[]}]}]}))
    complete(sample)
    def concurrent(req):
        if req['input']['task']=='missing_attachments':
            p=deepcopy(store.get_record_state('claims','Z')['payload']);p['statement']='另一主題的獨立變更。'
            store.apply_plan(store.plan_package({'package_id':'unrelated-concurrent','claims':[p]}))
    result=flash.run_group(store,'S',['C'],root/'lower',root/'group',Ports(reply=concurrent),apply=True)
    assert result['applied']
    frozen=json.loads((root/'group/before.json').read_text())['records']
    assert 'claims/Z' not in frozen


def test_explicit_pair_constraint_is_checked_after_gpt_override(sample):
    store,root,_=complete(sample)
    records,sources=f.load_source(store,'S')
    p=deepcopy(records['claims/C']);p['payload'].update(claim_id='D',statement='另一主張。')
    records['claims/D']=p
    records['claim_relation_constraints/X']={'payload':{'source_id':'C','target_id':'D','forbidden_relation_types':['supports']}}
    def setit(req):
        return {'items':[{'job_id':j['job_id'],'action':'set','relation_type':'supports','direction':'a_to_b','reason':'模型想新增。'} for j in req['input']['jobs']]}
    result=pairs.review(records,sources,[],[{'a':'claims/C','b':'claims/D'}],root/'pairs',Ports(reply=setit),kind='claim')
    assert not result['new_relations'] and result['pending'][0]['reason']=='explicit_relation_constraint'


def test_source_campaign_runs_two_groups_with_one_lower_pass_and_reuses_completion(sample):
    store,root,_=sample
    store.apply_plan(store.plan_package({'package_id':'second-claim','claims':[{'claim_id':'D','claim_type':'exegesis',
        'statement':'相信與聽從不能分開。','evidence_step_ids':['E'],'occurrences':[{'source_id':'S','anchors':[]}]}]}))
    ports=Ports();result=flash.run(store,'S',[['C'],['D']],root/'campaign',ports,apply=True)
    assert result['status']=='completed' and result['completed_claim_ids']==['C','D']
    assert len([q for role,q in ports.calls if role=='jev' and 'record_fidelity' in q['questions']])==1
    count=len(ports.calls)
    assert flash.run(store,'S',[['C'],['D']],root/'campaign',ports,apply=True)['status']=='completed'
    assert len(ports.calls)==count


def test_duplicate_campaign_targets_are_rejected_before_calls(sample):
    store,root,_=sample;ports=Ports()
    with pytest.raises(ValueError,match='multiple groups'):
        flash.run(store,'S',[['C'],['C']],root/'campaign',ports,apply=True)
    assert not ports.calls


def test_preview_does_not_write_and_does_not_claim_full_campaign_completion(sample):
    store,root,_=sample;ports=Ports(choices={'record_fidelity':'unsupported'})
    old=store.get_record_state('evidence_steps','E')
    result=flash.run(store,'S',[['C']],root/'campaign',ports)
    assert result['preview_only'] and result['stage']=='lower' and result['completed_claim_ids']==[]
    assert store.get_record_state('evidence_steps','E')==old


def test_far_orphan_candidates_are_routed_without_dumping_private_claim_graphs(sample):
    store,root,_=complete(sample)
    records,sources=f.load_source(store,'S')
    p=deepcopy(records['evidence_steps/E']);p['payload'].update(evidence_step_id='FAR',source_fragment_ids=[])
    # Candidate planning needs only paragraph hints; source text is still in this same frozen source.
    records['evidence_steps/FAR']=p
    # Provide the actual record material but remove its paragraph routing hint to force the fallback.
    from unittest.mock import patch
    original=flash.paragraph_keys
    with patch.object(flash,'paragraph_keys',side_effect=lambda key,rs:set() if key=='evidence_steps/FAR' else original(key,rs)):
        records['evidence_steps/FAR']['payload']['source_fragment_ids']=['F']
        ports=Ports(choices={'O00_match':.9,'O00_claim':'C00'})
        plan=flash.orphan_plan(records,sources,root/'far-plan',ports)
    assert plan['pairs'][0]['a']=='evidence_steps/FAR'
    assert not any(role=='gpt' for role,req in ports.calls)
    lookup=ports.calls[0][1]
    assert all('evidence' not in c for c in lookup['state']['claims'].values())


def test_declared_missing_lower_connection_is_verified_then_saved(sample):
    store,root,_=sample
    def reply(req):
        if req['input']['task']=='lower_coverage':
            return {'items':[{'job_id':j['job_id'],'action':'resolved','new_records':[{
                'record_type':'observations','statement':'教授的行為觀察。','speaker':'professor','stance':'asserted',
                'scripture_refs':[],'quotes':[{'paragraph_key':'S0001','quote':'你沒有聽從祂的話，你根本沒信。'}]}],
                'conclusions':[],'connections':[{'a':'new:0','b':'evidence_steps/E','reason':'觀察支持論據。'}],
                'reason':'補下層連接。'} for j in req['input']['jobs']]}
        if req['input']['task']=='pair_lower':
            return {'items':[{'job_id':j['job_id'],'action':'set','relation_type':'supports','direction':'a_to_b',
                'reason':'原文觀察支持這項論據。'} for j in req['input']['jobs']]}
    result=lower.run(store,'S',root/'lower',Ports(choices={'missing_lower':'missing_evidence'},reply=reply),apply=True)
    assert result['applied']
    edge=next(p for k,p in result['updates'].items() if k.startswith('knowledge_relations/'))
    assert edge['to_id']=='E' and edge['relation_type']=='supports'


def test_noop_preview_checks_versions_before_marking_complete(sample):
    store,root,_=complete(sample)
    flash.run_group(store,'S',['C'],root/'lower',root/'first',Ports(),apply=True)
    preview=flash.run_group(store,'S',['C'],root/'lower',root/'noop',Ports())
    assert preview['updates']=={} and not preview['new_claim_candidates']
    p=deepcopy(store.get_record_state('claims','C')['payload']);p['statement']='預覽後改動。'
    store.apply_plan(store.plan_package({'package_id':'after-noop','claims':[p]}))
    with pytest.raises(ValueError,match='no-op inputs changed'):
        flash.run_group(store,'S',['C'],root/'lower',root/'noop',Ports(),apply=True)


def test_every_support_fragment_reaches_fidelity_and_claim_checks(sample):
    store,root,_=sample
    fragment=deepcopy(store.get_record_state('source_fragments','F')['payload'])
    quote='你沒有聽從祂的話，你根本沒信。'
    import hashlib
    fragment.update(fragment_id='F2',verbatim_excerpt=quote,
        verbatim_excerpt_sha256=hashlib.sha256(quote.encode()).hexdigest())
    ev=deepcopy(store.get_record_state('evidence_steps','E')['payload']);ev['source_fragment_ids']=['F','F2']
    store.apply_plan(store.plan_package({'package_id':'all-fragments','evidence_steps':[ev],'source_fragments':[fragment]}))
    ports=Ports();lower.run(store,'S',root/'lower',ports,apply=True)
    flash.run_group(store,'S',['C'],root/'lower',root/'group',ports,apply=True)
    lower_req=next(q for role,q in ports.calls if role=='jev' and 'record_fidelity' in q['questions'])
    claim_req=next(q for role,q in ports.calls if role=='jev' and 'statement_fidelity' in q['questions'])
    assert len(lower_req['state']['original_text_excerpts'])==1
    assert quote in lower_req['state']['original_text_excerpts'][0]
    assert len(claim_req['state']['evidence'][0]['original_text_excerpts'])==1
    assert quote in claim_req['state']['evidence'][0]['original_text_excerpts'][0]


def test_jev_malformed_multiquestion_reply_is_not_silently_accepted(sample):
    store,root,_=complete(sample)
    class Malformed(Ports):
        def judge(self,req):
            result=super().judge(req)
            if 'statement_fidelity' in req['questions']:
                result['answers'].pop('support')
            return result
    before=store.get_record_state('claims','C')
    with pytest.raises(ValueError,match='question set differs'):
        flash.run_group(store,'S',['C'],root/'lower',root/'group',Malformed(),apply=True)
    assert store.get_record_state('claims','C')==before


def test_post_gpt_source_change_rolls_back_the_entire_lower_write(sample):
    store,root,path=sample
    def drift(req):
        if req['input']['task']=='lower_repair':path.write_text(path.read_text()+' ')
    before=store.get_record_state('evidence_steps','E')
    with pytest.raises(ValueError,match='source changed'):
        lower.run(store,'S',root/'lower',Ports(choices={'record_fidelity':'unsupported'},reply=drift),apply=True)
    assert store.get_record_state('evidence_steps','E')==before


def test_gpt_batches_observe_previous_dispositions_without_repeating_history(tmp_path,monkeypatch):
    monkeypatch.setattr(f,'GPT_JOBS',1)
    state=[]
    ports=Ports(reply=lambda req:{'items':[]})
    def prepare(batch):
        return [{**job,'current_claim_summaries':list(state)} for job in batch]
    for batch,reply in f.gpt(tmp_path,ports,'test',[{'job_id':'a'},{'job_id':'b'}],{},'small',prepare=prepare):
        state.append(batch[0]['job_id'])
    requests=[q for role,q in ports.calls if role=='gpt']
    assert requests[0]['input']['jobs'][0]['current_claim_summaries']==[]
    assert requests[1]['input']['jobs'][0]['current_claim_summaries']==['a']


def test_overlarge_lower_statement_does_not_truncate_raw_support(sample):
    store,root,_=sample
    p=deepcopy(store.get_record_state('evidence_steps','E')['payload']);p['statement']='這是教授的主張。'*10000
    store.apply_plan(store.plan_package({'package_id':'oversized-e','evidence_steps':[p]}))
    ports=Ports()
    with pytest.raises(ValueError,match='bounded request size|complete GPT job exceeds'):
        lower.run(store,'S',root/'lower',ports,apply=True)
    assert not ports.calls


def test_relation_type_correction_reuses_existing_exact_edge_signature(sample):
    store,root,_=complete(sample)
    store.apply_plan(store.plan_package({'package_id':'two-roles','knowledge_relations':[
        {'relation_id':rid,'from_id':'E','to_id':'C','relation_type':typ,'reason':'既有理由。','record_namespace':f.VERSION}
        for rid,typ in [('SUP','supports'),('QUAL','qualifies')]]}))
    records,sources=f.load_source(store,'S')
    def correct(req):
        return {'items':[{'job_id':j['job_id'],'action':'set','relation_type':'supports','direction':'a_to_b','reason':'實際是支持。'} for j in req['input']['jobs']]}
    result=pairs.review(records,sources,['knowledge_relations/SUP','knowledge_relations/QUAL'],[],root/'pairs',Ports(reply=correct),kind='attachment')
    assert result['updates']=={} and result['retiring_keys']==['knowledge_relations/QUAL']


def test_completed_campaign_cannot_be_reused_for_changed_source_bytes(sample):
    store,root,path=sample;ports=Ports()
    assert flash.run(store,'S',[['C']],root/'campaign',ports,apply=True)['status']=='completed'
    count=len(ports.calls);path.write_text(path.read_text()+' ')
    with pytest.raises(ValueError,match='source file changed'):
        flash.run(store,'S',[['C']],root/'campaign',ports,apply=True)
    assert len(ports.calls)==count


def test_foreign_campaign_target_does_not_start_lower_work(sample):
    store,root,_=sample
    p=deepcopy(store.get_record_state('claims','C')['payload']);p['occurrences'][0]['source_id']='OTHER'
    store.apply_plan(store.plan_package({'package_id':'foreign-source','claims':[p]}))
    ports=Ports()
    with pytest.raises(ValueError,match='source differs'):
        flash.run(store,'S',[['C']],root/'campaign',ports,apply=True)
    assert not ports.calls


def test_candidate_answer_addition_preserves_protected_question(sample):
    store,root,_=complete(sample)
    store.apply_plan(store.plan_package({'package_id':'held-q','questions':[{'question_id':'Q','text':'相信是什麼？',
        'source_fragment_id':'F','answer_claim_ids':[],'review_status':'human_hold'}]}))
    records,sources=f.load_source(store,'S')
    updates,pending=flash.question_links(records,sources,['claims/C'],{'S0001'},[{'claim':'C','question_ids':['Q']}],root,Ports())
    assert updates=={} and pending[0]['reason']=='protected_question_answers'


def test_existing_direct_observation_support_is_in_claim_level_material(sample):
    store,root,_=sample
    ev=deepcopy(store.get_record_state('evidence_steps','E')['payload']);ev['produced_claim_ids']=[]
    claim=deepcopy(store.get_record_state('claims','C')['payload']);claim['evidence_step_ids']=[]
    claim['occurrences'][0]['anchors']=[{'observation_id':'O','source_fragment_id':'F','paragraph_key':'S0001'}]
    store.apply_plan(store.plan_package({'package_id':'direct-o','claims':[claim],'evidence_steps':[ev],
        'observations':[{'observation_id':'O','statement':'教授把聽從連到相信。','source_fragment_id':'F'}],
        'knowledge_relations':[{'relation_id':'OC','from_id':'O','to_id':'C','relation_type':'supports',
                               'reason':'觀察提供支持。','record_namespace':f.VERSION}]}))
    ports=Ports(choices={'relation':lambda req:'none' if req['state']['A']['record_type']=='evidence_steps' else 'a_to_b_supports'})
    lower.run(store,'S',root/'lower',ports,apply=True)
    flash.run_group(store,'S',['C'],root/'lower',root/'group',ports,apply=True)
    req=next(q for role,q in ports.calls if role=='jev' and 'statement_fidelity' in q['questions'])
    assert req['state']['evidence'][0]['record_type']=='observations'
    assert req['state']['evidence'][0]['relation_type']=='supports'


def test_jev_candidate_filter_is_broad_and_gpt_can_retain_leaf_evidence(sample):
    store,root,_=sample
    def reply(req):
        if req['input']['task']=='lower_coverage':
            return {'items':[{'job_id':j['job_id'],'action':'resolved','new_records':[],
                'conclusions':[{'statement':'這是教授的一項實質判斷。','support_keys':['evidence_steps/E'],
                    'quotes':[{'paragraph_key':'S0001','quote':'你沒有聽從祂的話，你根本沒信。'}],
                    'reason':'交上層判斷粒度，未判定獨立支持。'}],'reason':'保留候選。'} for j in req['input']['jobs']]}
        if req['input']['task']=='conclusion_disposition':
            return {'items':[{'job_id':j['job_id'],'action':'retain_evidence','claim_id':'',
                'statement':'','evidence_step_ids':[],'scripture_refs':[],'answer_question_ids':[],
                'reason':'只作上層論據，沒有另外支持，保留 Evidence。'} for j in req['input']['jobs']]}
    ports=Ports(choices={'conclusion_candidate':'substantive_judgment'},reply=reply)
    result=lower.run(store,'S',root/'lower',ports,apply=True)
    assert len(result['conclusion_candidates'])==1
    q=next(req for role,req in ports.calls if role=='jev' and 'conclusion_candidate' in req['questions'])
    assert '不判斷有無獨立支持' in q['questions']['conclusion_candidate']['instructions']
    result=flash.run_group(store,'S',['C'],root/'lower',root/'group',ports,apply=True)
    assert result['applied'] and not result['new_claim_ids']
    assert result['conclusion_dispositions'][0]['action']=='retain_evidence'


def test_gpt_can_select_other_verified_evidence_for_candidate_support(sample):
    store,root,_=sample
    ev=deepcopy(store.get_record_state('evidence_steps','E')['payload'])
    ev.update(evidence_step_id='E2',statement='教授以知道旨意作為遵行的前提。',produced_claim_ids=[])
    store.apply_plan(store.plan_package({'package_id':'other-support','evidence_steps':[ev]}))
    def reply(req):
        if req['input']['task']=='lower_coverage':
            return {'items':[{'job_id':j['job_id'],'action':'resolved','new_records':[],
                'conclusions':[{'statement':'不知道神旨意就無法遵行。','support_keys':['evidence_steps/E'],
                    'quotes':[{'paragraph_key':'S0001','quote':'你不知道神的旨意，怎麼遵行？'}],
                    'reason':'只提候選，交 GPT 判斷。'}],'reason':'交接。'} for j in req['input']['jobs']]}
        if req['input']['task']=='conclusion_disposition':
            assert 'evidence_steps/E2' in req['input']['jobs'][0]['related_evidence']
            return {'items':[{'job_id':j['job_id'],'action':'new_claim','claim_id':'',
                'statement':'知道神的旨意是遵行祂旨意的前提。','evidence_step_ids':['E2'],
                'scripture_refs':[],'answer_question_ids':[],'reason':'另一條已驗證論據支持這項判斷。'} for j in req['input']['jobs']]}
        if req['input']['task']=='pair_attachment':
            return {'items':[{'job_id':j['job_id'],'action':'set','relation_type':'supports','direction':'a_to_b',
                'reason':'教授的前提支持結論。'} for j in req['input']['jobs']]}
    ports=Ports(choices={'conclusion_candidate':'substantive_judgment'},reply=reply)
    lower.run(store,'S',root/'lower',ports,apply=True)
    result=flash.run_group(store,'S',['C'],root/'lower',root/'group',ports,apply=True)
    assert result['applied'] and len(result['new_claim_ids'])==1
    assert store.get_record_state('claims',result['new_claim_ids'][0])['payload']['evidence_step_ids']==['E2']


def test_keep_justification_can_quote_a_subset_without_replacing_any_binding(sample):
    store,root,_=sample
    fragment=deepcopy(store.get_record_state('source_fragments','F')['payload'])
    quote='你沒有聽從祂的話，你根本沒信。'
    import hashlib
    fragment.update(fragment_id='F2',verbatim_excerpt=quote,verbatim_excerpt_sha256=hashlib.sha256(quote.encode()).hexdigest())
    ev=deepcopy(store.get_record_state('evidence_steps','E')['payload']);ev['source_fragment_ids']=['F','F2']
    store.apply_plan(store.plan_package({'package_id':'keep-full-sources','source_fragments':[fragment],'evidence_steps':[ev]}))
    before=store.get_record_state('evidence_steps','E')
    def keep(req):
        if req['input']['task']=='lower_repair':
            return {'items':[{'job_id':j['job_id'],'action':'keep',
                'statement':j['record']['statement'],'speaker':j['record']['speaker'],'stance':j['record']['stance'],
                'scripture_refs':j['record'].get('scripture_refs',[]),
                'quotes':[{'paragraph_key':'S0001','quote':quote}], 'reason':'完整來源已支持，摘錄一處說明即可。'} for j in req['input']['jobs']]}
    result=lower.run(store,'S',root/'lower',Ports(choices={'record_fidelity':'unsupported'},reply=keep),apply=True)
    assert result['status']=='verified' and result['updates']=={} and result['new_fragments']=={}
    assert store.get_record_state('evidence_steps','E')==before


def test_keep_never_applies_rephrased_echo_fields(sample):
    store,root,_=sample
    before=store.get_record_state('evidence_steps','E')
    def keep(req):
        if req['input']['task']=='lower_repair':
            return {'items':[{'job_id':j['job_id'],'action':'keep',
                'statement':j['record']['statement']+'成立於教授的解讀。','speaker':'audience','stance':'quoted',
                'scripture_refs':['回顯不是修改'], 'quotes':[{'paragraph_key':'S0001','quote':'附帶回顯不作新原文。'}],
                'reason':'原記錄成立，保留。'} for j in req['input']['jobs']]}
    result=lower.run(store,'S',root/'lower',Ports(choices={'record_fidelity':'unsupported'},reply=keep),apply=True)
    assert result['updates']=={} and result['new_fragments']=={}
    assert store.get_record_state('evidence_steps','E')==before
    assert result['outcomes'][0]['quote_projection'][0]['ignored_echo_fields']


def test_claim_keep_never_applies_echo_rewording_or_ids(sample):
    store,root,_=complete(sample)
    before=store.get_record_state('claims','C')['payload']
    def keep(req):
        if req['input']['task']=='claim_statement':
            return {'items':[{'claim_id':'C','action':'keep','statement':'回顯中的附加說明。',
                'evidence_step_ids':['UNUSED_ECHO'],'scripture_refs':['UNUSED_ECHO'],'reason':'原主張成立，保留。'}]}
    result=flash.run_group(store,'S',['C'],root/'lower',root/'group',Ports(choices={'support':'partial'},reply=keep),apply=True)
    actual=store.get_record_state('claims','C')['payload']
    assert result['applied'] and actual['statement']==before['statement']
    assert actual['evidence_step_ids']==before['evidence_step_ids']


def test_unresolved_coverage_proposals_are_preserved_without_partial_write(sample):
    store,root,_=sample
    before=store.get_record_state('evidence_steps','E')
    def unresolved(req):
        if req['input']['task']=='lower_coverage':
            return {'items':[{'job_id':j['job_id'],'action':'needs_source','new_records':[],
                'conclusions':[{'statement':'尚缺材料的候選。','support_keys':['evidence_steps/E'],
                    'quotes':[{'paragraph_key':'S0001','quote':'你沒有聽從祂的話，你根本沒信。'}],'reason':'待解。'}],
                'connections':[],'reason':'提供的材料仍不足。'} for j in req['input']['jobs']]}
    ports=Ports(choices={'missing_lower':'missing_evidence'},reply=unresolved)
    result=lower.run(store,'S',root/'lower',ports,apply=True)
    assert result['status']=='needs_attention' and not result['applied']
    assert store.get_record_state('evidence_steps','E')==before
    assert [q['input']['task'] for role,q in ports.calls if role=='gpt']==['lower_decomposition','lower_coverage']


@pytest.mark.parametrize('final_action',['resolved','needs_source'])
def test_coverage_expands_bound_support_once_without_sending_old_draft(sample,final_action):
    store,root,path=sample
    import hashlib
    raw=json.loads(path.read_text());raw['script'] += [
        {'index':2,'start_time':2.0,'end_time':3.0,'text':'這是附近的過渡。'},
        {'index':3,'start_time':3.0,'end_time':4.0,'text':'這是同項已掛載的後續解說。'}]
    path.write_text(json.dumps(raw,ensure_ascii=False))
    doc=deepcopy(store.get_record_state('source_documents','S')['payload']);doc['source_file_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    snap=e.source_snapshot({'path':str(path),'file_sha256':doc['source_file_sha256']},source=doc)
    f1=deepcopy(store.get_record_state('source_fragments','F')['payload']);f1['source_sha256']=snap['body_sha256']
    p3=snap['paragraphs']['S0003'];f3={**f1,'fragment_id':'F3','paragraph_key':'S0003','source_segment_index':p3['index'],
        'verbatim_excerpt':p3['text'],'media_time':p3['start_time'],'media_end_time':p3['end_time'],
        'paragraph_text_sha256':hashlib.sha256(p3['text'].encode()).hexdigest(),
        'verbatim_excerpt_sha256':hashlib.sha256(p3['text'].encode()).hexdigest()}
    ev=deepcopy(store.get_record_state('evidence_steps','E')['payload']);ev['source_fragment_ids']=['F','F3']
    store.apply_plan(store.plan_package({'package_id':'expanded-support','source_documents':[doc],
        'source_fragments':[f1,f3],'evidence_steps':[ev]}))
    def reply(req):
        task=req['input']['task']
        if task in {'lower_coverage','lower_coverage_context'}:
            if task=='lower_coverage_context':
                job=req['input']['jobs'][0]
                assert 'S0003' in job['source_paragraphs']
                assert 'original_unresolved_reply' not in job
                assert job['coverage_target_paragraph_key']=='S0001'
                assert job['context_recovery']['max_additional_calls']==1
            return {'items':[{'job_id':j['job_id'],'action':'needs_source' if task=='lower_coverage' else final_action,
                'new_records':[],'conclusions':[],'connections':[],
                'reason':'檢查提供的完整後文。'} for j in req['input']['jobs']]}
    ports=Ports(choices={'missing_lower':lambda req:'missing_evidence' if req['state']['paragraph_key']=='S0001' else 'covered'},reply=reply)
    result=lower.run(store,'S',root/'lower',ports,apply=True)
    assert result['status']==('verified' if final_action=='resolved' else 'needs_attention')
    tasks=[req['input']['task'] for role,req in ports.calls if role=='gpt']
    assert tasks==['lower_decomposition','lower_coverage','lower_coverage_context']


def test_candidate_quote_uses_existing_mechanical_newline_projection(sample):
    store,root,path=sample
    import hashlib
    raw=json.loads(path.read_text());raw['script'][0]['text']=raw['script'][0]['text'].replace('沒有聽從','沒有\n聽從')
    path.write_text(json.dumps(raw,ensure_ascii=False))
    doc=deepcopy(store.get_record_state('source_documents','S')['payload']);doc['source_file_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    snap=e.source_snapshot({'path':str(path),'file_sha256':doc['source_file_sha256']},source=doc);paragraph=snap['paragraphs']['S0001']
    frag=deepcopy(store.get_record_state('source_fragments','F')['payload']);frag.update(source_sha256=snap['body_sha256'],
        verbatim_excerpt=paragraph['text'],paragraph_text_sha256=hashlib.sha256(paragraph['text'].encode()).hexdigest(),
        verbatim_excerpt_sha256=hashlib.sha256(paragraph['text'].encode()).hexdigest())
    store.apply_plan(store.plan_package({'package_id':'newline-source','source_documents':[doc],'source_fragments':[frag]}))
    def reply(req):
        if req['input']['task']=='lower_coverage':
            return {'items':[{'job_id':j['job_id'],'action':'resolved','new_records':[],
                'conclusions':[{'statement':'候選判斷。','support_keys':['evidence_steps/E'],
                    'quotes':[{'paragraph_key':'S0001','quote':'你沒有聽從祂的話，你根本沒信。'}],
                    'reason':'交上層判斷。'}],'reason':'候選引文僅有換行差異。'} for j in req['input']['jobs']]}
    result=lower.run(store,'S',root/'lower',Ports(choices={'conclusion_candidate':'substantive_judgment'},reply=reply),apply=True)
    assert result['status']=='verified'
    assert '\n' in result['conclusion_candidates'][0]['quotes'][0]['quote']
    assert list((root/'lower/candidate-quote-projection/S0001').glob('*.json'))


def test_orphan_routing_shares_source_summaries_for_all_records(sample):
    store,root,_=complete(sample);records,sources=f.load_source(store,'S')
    for i in range(5):
        key='evidence_steps/FAR'+str(i);records[key]=deepcopy(records['evidence_steps/E'])
        records[key]['payload']['evidence_step_id']='FAR'+str(i)
    ports=Ports()
    from unittest.mock import patch
    fn=flash.paragraph_keys
    with patch.object(flash,'paragraph_keys',side_effect=lambda k,rs:set() if 'FAR' in k else fn(k,rs)):
        flash.orphan_plan(records,sources,root/'orphans',ports)
    assert not any(role=='gpt' for role,q in ports.calls)
    calls=[q for role,q in ports.calls if role=='jev']
    assert len(calls)==1 and len(calls[0]['questions'])==10
    assert list(calls[0]['state']['claims'])==['C00']


def test_orphan_routing_preserves_completed_legacy_plan(sample):
    store,root,_=complete(sample);records,sources=f.load_source(store,'S')
    records['evidence_steps/FAR']=deepcopy(records['evidence_steps/E']);records['evidence_steps/FAR']['payload']['evidence_step_id']='FAR'
    prior={'source_id':'S','source_sha256':sources['S']['body_sha256'],
           'pairs':[{'a':'evidence_steps/FAR','b':'claims/C','reason':'已找到候選。'}],
           'reused_request_sha256':['original-paid-answer']}
    e.save(root/'orphans/plan.json',prior)
    ports=Ports()
    result=flash.orphan_plan(records,sources,root/'orphans',ports)
    assert result==prior and not ports.calls


def test_orphan_lookup_does_not_expand_to_imported_foreign_or_retired_reference_claims(sample):
    store,root,_=complete(sample);records,sources=f.load_source(store,'S')
    records['evidence_steps/FAR']=deepcopy(records['evidence_steps/E'])
    records['evidence_steps/FAR']['payload'].update(evidence_step_id='FAR',produced_claim_ids=[])
    foreign=deepcopy(records['claims/C']);foreign['payload'].update(claim_id='FOREIGN',occurrences=[{'source_id':'OTHER','anchors':[]}])
    records['claims/FOREIGN']=foreign
    retired=deepcopy(records['claims/C']);retired['retired']=True;retired['payload']['claim_id']='RETIRED'
    records['claims/RETIRED']=retired
    ports=Ports()
    flash.orphan_plan(records,sources,root/'source-local-plan',ports)
    assert len(ports.calls)==1
    assert ports.calls[0][1]['state']['claims']=={'C00':flash.peer_summary(['claims/C'],records)['claims/C']}


def test_unresolved_lookup_scope_reaches_existing_orphan_disposition(sample):
    store,root,_=sample
    record=deepcopy(store.get_record_state('evidence_steps','E')['payload'])
    record.update(evidence_step_id='ORPHAN',produced_claim_ids=[])
    store.apply_plan(store.plan_package({'package_id':'isolated','evidence_steps':[record]}))
    complete(sample)
    uncertainty={'object_key':'evidence_steps/ORPHAN','reason':'uncertain_orphan_lookup','remaining_claim_keys':['claims/C'],'match_probability':.5}
    e.save(root/'orphan-plan/plan.json',{'pairs':[],'pending':[uncertainty]})
    def reply(req):
        assert req['input']['task']=='remaining_orphans'
        job=req['input']['jobs'][0]
        assert job['lookup_uncertainty']==[uncertainty]
        assert list(job['unresolved_claim_candidates'])==['claims/C']
        return {'items':[{'job_id':job['job_id'],'action':'retain_evidence','reason':'本項只保留為背景，不強迫連接。'}]}
    dispositions,pending=flash.finish_orphans(store,'S',root,Ports(reply=reply))
    assert dispositions[0]['action']=='retain_evidence' and not pending


@pytest.mark.parametrize('task,prompt,job',[
    ('lower_repair',lower.PROMPT,{'job_id':'evidence_steps/E','statement':'待審證據。'}),
    ('claim_statement',flash.PROMPT,{'job_id':'C','claim':{'statement':'待審主張。'},
                                    'material':{'evidence':[{'statement':'已審證據。'}]}}),
])
def test_semantic_review_contract_reaches_runtime_and_keeps_layer_scope(tmp_path,task,prompt,job):
    ports=Ports(reply=lambda req:{'items':[]})
    list(f.gpt(tmp_path,ports,task,[job],{},prompt))
    request=ports.calls[0][1]
    assert request['system'].count(f.SEMANTIC_REVIEW)==1
    assert '不審閱下一層內容' in request['system']
    assert 'keep 也須解釋' in request['system']
    assert '不因局部片段未重述就刪除' in request['system']
    assert request['input']['jobs']==[job]
    if task=='claim_statement':
        assert '不審 CVP' in request['system']
        assert '不重審或改寫 Evidence／Observation' in request['system']
    count=len(ports.calls)
    list(f.gpt(tmp_path,ports,task,[job],{},prompt))
    assert len(ports.calls)==count


def test_legacy_prompt_intent_is_not_silently_resumed(sample):
    store,root,ports=complete(sample)
    intent=root/'lower/intent.json';old=json.loads(intent.read_text());old.pop('gpt_review_contract')
    intent.write_text(json.dumps(old))
    before=store.get_record_state('evidence_steps','E');count=len(ports.calls)
    with pytest.raises(ValueError,match='saved input differs'):
        lower.run(store,'S',root/'lower',ports,apply=True)
    assert len(ports.calls)==count
    assert store.get_record_state('evidence_steps','E')==before


@pytest.mark.parametrize('name,value',[
    ('statement_fidelity',v) for v in ['unsupported','changed','contradicted','uncertain']]+[
    ('support',v) for v in ['partial','unsupported','contradicted','uncertain']]+[
    ('scripture',v) for v in ['missing','mismatched','missing_and_mismatched','uncertain']])
def test_every_nonpassing_claim_classification_reaches_gpt_without_automatic_rewrite(sample,name,value):
    store,root,_=complete(sample);before=store.get_record_state('claims','C')
    def keep(req):
        if req['input']['task']=='claim_statement':
            p=before['payload']
            return {'items':[{'claim_id':'C','action':'keep','statement':p['statement'],
                              'evidence_step_ids':p['evidence_step_ids'],'scripture_refs':p.get('scripture_refs',[]),
                              'reason':'本例原文支持現有表述，保留。'}]}
    ports=Ports(choices={name:value},reply=keep)
    result=flash.run_group(store,'S',['C'],root/'lower',root/'classified',ports,apply=True)
    requests=[q for role,q in ports.calls if role=='gpt' and q['input']['task']=='claim_statement']
    assert result['applied'] and len(requests)==1
    assert requests[0]['input']['jobs'][0]['findings'][name]==value
    actual=store.get_record_state('claims','C')['payload']
    # Normal group completion projects the previously absent physical anchors.
    # Classification and a GPT keep must not change any other Claim field.
    assert {k:v for k,v in actual.items() if k not in {'revision','occurrences'}}=={
        k:v for k,v in before['payload'].items() if k not in {'revision','occurrences'}}
    assert [o['source_id'] for o in actual['occurrences']]==['S']
    assert {a['evidence_id'] for o in actual['occurrences'] for a in o['anchors']}=={'E'}


@pytest.mark.parametrize('classification',['background_or_example','oral_or_repetition','question_only'])
def test_nonjudgment_classes_do_not_trigger_lower_extraction(sample,classification):
    store,root,_=sample;ports=Ports(choices={'conclusion_candidate':classification})
    result=lower.run(store,'S',root/'lower',ports)
    assert result['status']=='verified'
    assert not any(role=='gpt' and q['input']['task']=='lower_coverage' for role,q in ports.calls)


@pytest.mark.parametrize('contract',['jev_contract','material_contract'])
def test_runtime_flash_questions_use_named_classes_and_legacy_contract_cannot_resume(sample,contract):
    store,root,ports=complete(sample)
    flash.run_group(store,'S',['C'],root/'lower',root/'group',ports,apply=True)
    for role,request in ports.calls:
        if role=='jev':
            for question in request['questions'].values():
                if question['type']=='choice':assert not {'yes','no'}&set(question['criteria'])
    path=root/'lower/intent.json';old=json.loads(path.read_text());old.pop(contract);path.write_text(json.dumps(old))
    count=len(ports.calls)
    with pytest.raises(ValueError,match='saved input differs'):
        lower.run(store,'S',root/'lower',ports)
    assert len(ports.calls)==count


@pytest.mark.parametrize('choice',['unsupported','mismatched','uncertain'])
def test_relationship_reason_classes_route_without_rechecking_node_contents(sample,choice):
    store,root,_=complete(sample)
    records,sources=f.load_source(store,'S')
    records['knowledge_relations/K']={'payload':{'relation_id':'K','from_id':'E','to_id':'C',
        'relation_type':'supports','reason':'既有理由。'},'retired':False}
    def keep(req):
        if req['input']['task']=='pair_attachment':
            return {'items':[{'job_id':'knowledge_relations/K','action':'keep','relation_type':'supports',
                              'direction':'a_to_b','reason':'保留現有教授論證。'}]}
    ports=Ports(choices={'reason_valid':choice},reply=keep)
    result=pairs.review(records,sources,['knowledge_relations/K'],[],root/'reason',ports,kind='attachment')
    assert not result['updates'] and not result['retiring_keys']
    jobs=[q for role,q in ports.calls if role=='gpt']
    assert len(jobs)==1 and jobs[0]['input']['jobs'][0]['precheck']['reason_valid']==choice
    req=next(q for role,q in ports.calls if role=='jev')
    assert 'existing_relation.reason' in req['questions']['reason_valid']['instructions']
    assert '選定' not in req['questions']['reason_valid']['instructions']


def test_missing_attachment_discovery_skips_known_implicit_pairs(sample):
    store,root,_=sample;records,sources=f.load_source(store,'S');ports=Ports()
    result=flash.discover(records,sources,['claims/C'],{'evidence_steps/E'},root/'known-pairs',ports,task='missing_attachments')
    assert result==([],[],[]) and not ports.calls


def test_shared_evidence_missing_for_second_target_is_kept_with_existing_link_disclosed(sample):
    store,root,_=sample;records,sources=f.load_source(store,'S')
    records['claims/D']={'payload':{'claim_id':'D','statement':'另一主張。','evidence_step_ids':[]},'retired':False}
    def inspect(req):
        if req['input']['task']=='missing_attachments':
            job=req['input']['jobs'][0]
            assert job['candidate_records'].keys()=={'evidence_steps/E'}
            assert {'from_id':'E','to_id':'C','relation_type':'supports'} in job['existing_connections']
            return {'items':[{'job_id':j['job_id'],'pairs':[],'unresolved_keys':[],
                             'reason':'已披露 C 連接，另查 D。'} for j in req['input']['jobs']]}
    ports=Ports(reply=inspect)
    flash.discover(records,sources,['claims/C','claims/D'],{'evidence_steps/E'},root/'shared-pairs',ports,task='missing_attachments')
    assert len(ports.calls)==1


def test_missing_attachment_input_includes_existing_lower_route_without_inventing_direct_support(sample):
    store,root,_=sample;records,sources=f.load_source(store,'S')
    row=deepcopy(records['evidence_steps/E']);row['payload'].update(evidence_step_id='E2',produced_claim_ids=[])
    records['evidence_steps/E2']=row
    records['knowledge_relations/R']={'payload':{'from_id':'E2','to_id':'E','relation_type':'qualifies'}}
    def inspect(req):
        if req['input']['task']=='missing_attachments':
            job=req['input']['jobs'][0];links=job['existing_connections']
            assert {'from_id':'E2','to_id':'E','relation_type':'qualifies'} in links
            assert {'from_id':'E','to_id':'C','relation_type':'supports'} in links
            assert {'from_id':'E2','to_id':'C','relation_type':'supports'} not in links
            return {'items':[{'job_id':j['job_id'],'pairs':[],'unresolved_keys':[],
                             'reason':'保留既有路線，不把限定當成直接支持。'} for j in req['input']['jobs']]}
    flash.discover(records,sources,['claims/C'],{'evidence_steps/E','evidence_steps/E2'},root/'route',Ports(reply=inspect),task='missing_attachments')


def test_saved_relation_reason_resolves_packet_labels_using_existing_renderer(sample):
    store,root,_=sample;records,sources=f.load_source(store,'S')
    def write(req):
        if req['input']['task']=='pair_attachment':
            return {'items':[{'job_id':j['job_id'],'action':'set','relation_type':'explains',
                             'direction':'a_to_b','reason':'A解釋B。'} for j in req['input']['jobs']]}
    result=pairs.review(records,sources,[],[{'a':'evidence_steps/E','b':'claims/C'}],root/'labels',
                        Ports(choices={'relation':'a_to_b_explains'},reply=write),kind='attachment')
    reason=result['new_relations'][0]['reason']
    assert 'A' not in reason and 'B' not in reason
    assert records['evidence_steps/E']['payload']['statement'] in reason
    assert records['claims/C']['payload']['statement'] in reason


def test_discovery_batches_keep_the_whole_local_route_without_expanding_node_text(sample):
    store,root,_=sample;records,sources=f.load_source(store,'S')
    for eid in ['E2','E3','E4','E5','Z']:
        row=deepcopy(records['evidence_steps/E']);row['payload'].update(evidence_step_id=eid,produced_claim_ids=[])
        records['evidence_steps/'+eid]=row
    for name,a,b in [('R1','E2','Z'),('R2','Z','E')]:
        records['knowledge_relations/'+name]={'payload':{'from_id':a,'to_id':b,'relation_type':'supports'}}
    def inspect(req):
        if req['input']['task']=='missing_attachments':
            for job in req['input']['jobs']:
                assert len(job['candidate_records'])<=4
                assert {'from_id':'E2','to_id':'Z','relation_type':'supports'} in job['existing_connections']
                assert {'from_id':'Z','to_id':'E','relation_type':'supports'} in job['existing_connections']
                assert {'from_id':'E','to_id':'C','relation_type':'supports'} in job['existing_connections']
            return {'items':[{'job_id':j['job_id'],'pairs':[],'unresolved_keys':[],
                             'reason':'保留跨批次的既有路線。'} for j in req['input']['jobs']]}
    flash.discover(records,sources,['claims/C'],{'evidence_steps/'+eid for eid in ['E','E2','E3','E4','E5','Z']},
                   root/'batched-routes',Ports(reply=inspect),task='missing_attachments')


def test_old_discovery_intent_cannot_silently_acquire_the_new_scope(sample):
    store,root,_=complete(sample);ports=Ports()
    flash.run_group(store,'S',['C'],root/'lower',root/'group',ports)
    path=root/'group/intent.json';old=json.loads(path.read_text());old.pop('discovery_contract');path.write_text(json.dumps(old))
    count=len(ports.calls)
    with pytest.raises(ValueError,match='saved input differs'):
        flash.run_group(store,'S',['C'],root/'lower',root/'group',ports)
    assert len(ports.calls)==count


@pytest.mark.parametrize('status',['ai_consensus_reviewed','independent_terminal_reviewed'])
def test_changed_legacy_ai_claim_is_saved_as_a_candidate_not_a_borrowed_approval(sample,status):
    from backend.api.canonical_repository.postgres_store import record_content_sha
    store,root,_=sample
    p=deepcopy(store.get_record_state('claims','C')['payload'])
    p.update(review_status=status,reviewed_by='historical-reviewer',reviewed_at='2026-09-01',
             ai_review_provenance={'historical':'not a review of the new content'},source_reconciliation_review_sha256='historical')
    with store.connect() as conn:
        conn.execute("UPDATE wang_knowledge.objects SET payload=%s::jsonb,review_status=%s,content_sha256=%s WHERE collection='claims' AND object_id='C'",
                     (json.dumps(p),p['review_status'],record_content_sha(p)))
    lower.run(store,'S',root/'lower',Ports(),apply=True)
    def repair(req):
        if req['input']['task']=='claim_statement':
            return {'items':[{'claim_id':'C','action':'repair','statement':'沒有聽從，顯明沒有真正相信。',
                             'evidence_step_ids':['E'],'scripture_refs':[],'reason':'依教授原意澄清。'}]}
    result=flash.run_group(store,'S',['C'],root/'lower',root/'group',Ports(choices={'support':'partial'},reply=repair),apply=True)
    assert result['applied']
    p=store.get_record_state('claims','C')['payload']
    assert p['review_status']=='candidate' and p.get('reviewed_by') is None
    assert p.get('reviewed_at') is None and p.get('ai_review_provenance') is None
    assert p.get('source_reconciliation_review_sha256') is None
    assert result['legacy_qualification']=='not_granted'


def test_flash_candidate_projection_does_not_clear_human_decisions():
    for status in ['approved','human_approved','human_hold','human_review_required','rejected']:
        p={'review_status':status,'reviewed_by':'human'}
        assert flash.current_claim_payload(p)==p


def test_unbound_unowned_dialogue_is_excluded_without_retirement_or_source_approval(sample):
    store,root,_=sample
    frag=deepcopy(store.get_record_state('source_fragments','F')['payload']);frag.update(fragment_id='OLD',anchor_state='source_deleted')
    context=deepcopy(store.get_record_state('evidence_steps','E')['payload'])
    context.update(evidence_step_id='CONTEXT',statement='請聽眾重述。',step_type='dialogue_context',
                   support_eligibility='context_only',produced_claim_ids=[],source_fragment_ids=['OLD'])
    store.apply_plan(store.plan_package({'package_id':'context','source_fragments':[frag],'evidence_steps':[context]}))
    before=store.get_record_state('evidence_steps','CONTEXT');ports=Ports()
    result=flash.run(store,'S',[['C']],root,ports,apply=True)
    assert result['status']=='completed'
    proof=json.loads((root/'lower/verification.json').read_text())
    assert proof['excluded_context_keys']==['evidence_steps/CONTEXT']
    outcome=next(x for x in proof['outcomes'] if x['object_key']=='evidence_steps/CONTEXT')
    assert outcome['source_verified'] is False and outcome['argument_use_permitted'] is False
    assert store.get_record_state('evidence_steps','CONTEXT')==before
    records,sources=f.load_source(store,'S');lower.verify(root/'lower',records,sources)
    claim=deepcopy(store.get_record_state('claims','C')['payload']);claim['evidence_step_ids'].append('CONTEXT')
    context['produced_claim_ids']=['C']
    store.apply_plan(store.plan_package({'package_id':'new-use','claims':[claim],'evidence_steps':[context]}))
    with pytest.raises(ValueError,match='acquired an argument use'):
        lower.verify(root/'lower',*f.load_source(store,'S'))


@pytest.mark.parametrize('collection,payload',[('questions',{'question_id':'Q','text':'這話是什麼意思？'}),
                                              ('position_nodes',{'position_id':'P','title':'外部立場。'})])
def test_question_and_position_locators_share_mechanical_recovery(sample,collection,payload):
    store,root,_=sample
    frag=deepcopy(store.get_record_state('source_fragments','F')['payload']);frag.update(fragment_id='STALE',anchor_state='unresolved')
    store.apply_plan(store.plan_package({'package_id':'stale-other','source_fragments':[frag],
        collection:[{**payload,'source_fragment_ids':['STALE']}]}))
    result=lower.run(store,'S',root/'lower',Ports(),apply=True)
    assert result['applied'] and not result['pending']
    oid=payload.get('question_id',payload.get('position_id'))
    saved=store.get_record_state(collection,oid)['payload']
    assert saved['source_fragment_ids']!=['STALE']
    assert all(saved[k]==v for k,v in payload.items())
    assert store.get_record_state('source_fragments','STALE')['payload']['anchor_state']=='unresolved'


@pytest.mark.parametrize('repair_lower',[False,True])
@pytest.mark.parametrize('second_group',[False,True])
def test_full_campaign_creates_claim_updates_question_and_resumes_without_models(sample,repair_lower,second_group):
    store,root,_=sample
    if second_group:
        evidence=deepcopy(store.get_record_state('evidence_steps','E')['payload'])
        evidence['produced_claim_ids'].append('C2')
        store.apply_plan(store.plan_package({'package_id':'second-group','claims':[{
            'claim_id':'C2','claim_type':'exegesis','statement':'相信涉及聽從。','evidence_step_ids':['E'],
            'occurrences':[{'source_id':'S','anchors':[]}]}],'evidence_steps':[evidence]}))
    store.apply_plan(store.plan_package({'package_id':'q-full','questions':[{
        'question_id':'Q','text':'為什麼需要知道神的旨意？','questioner':'professor',
        'source_fragment_id':'F','answer_claim_ids':['C']}]}))
    def reply(req):
        task=req['input']['task']
        if task=='lower_coverage':
            return {'items':[{'job_id':j['job_id'],'action':'resolved','new_records':[],
                'conclusions':[{'statement':'不知道神旨意就無法遵行。','support_keys':['evidence_steps/E'],
                    'quotes':[{'paragraph_key':'S0001','quote':'你不知道神的旨意，怎麼遵行？'}],
                    'reason':'將前提關係交給 Claim 層核對。'}],'reason':'不重複抽取 Evidence。'} for j in req['input']['jobs']]}
        if task=='conclusion_disposition':
            def candidate(j):
                existing=next((k.split('/',1)[1] for k,p in j['existing_claims'].items()
                    if p['statement']=='不知道神的旨意，就無法遵行祂的旨意。'),'')
                return {'job_id':j['job_id'],'action':'represented' if existing else 'new_claim','claim_id':existing,
                'statement':'不知道神的旨意，就無法遵行祂的旨意。','evidence_step_ids':['E'],
                'scripture_refs':[],'answer_question_ids':['Q'],'reason':'單獨保存前提關係並補直接答案。'}
            return {'items':[candidate(j) for j in req['input']['jobs']]}
    ports=Ports(choices={'conclusion_candidate':'substantive_judgment',
        'record_fidelity':lambda req:'unsupported' if repair_lower and req['state']['record_type']=='evidence_steps' else 'faithful'},reply=reply)
    groups=[['C'],['C2']] if second_group else [['C']]
    result=flash.run(store,'S',groups,root,ports,apply=True)
    assert result['status']=='completed'
    new_ids=result['groups'][0]['new_claim_ids'];assert len(new_ids)==1
    saved=store.get_record_state('questions','Q')['payload']
    assert set(saved['answer_claim_ids'])=={'C',new_ids[0]} and saved['answer_state']=='answered'
    assert set(store.get_record_state('evidence_steps','E')['payload']['produced_claim_ids'])=={'C',new_ids[0]}|({'C2'} if second_group else set())
    lower.verify(root/'lower',*f.load_source(store,'S'))
    count=len(ports.calls)
    resumed=flash.run(store,'S',groups,root,ports,apply=True)
    assert resumed['status']=='completed' and resumed['groups'][0]['new_claim_ids']==new_ids
    assert len(ports.calls)==count
    evidence=deepcopy(store.get_record_state('evidence_steps','E')['payload'])
    evidence['produced_claim_ids'].append('EXTERNAL')
    store.apply_plan(store.plan_package({'package_id':'external-after-group','evidence_steps':[evidence],
        'claims':[{'claim_id':'EXTERNAL','claim_type':'exegesis','statement':'外部判斷。','evidence_step_ids':['E'],
                   'occurrences':[{'source_id':'S','anchors':[]}]}]}))
    with pytest.raises(ValueError,match='bookkeeping changed outside later campaign group'):
        flash.run_group(store,'S',['C'],root/'lower',root/'group-1',ports,apply=True)
    assert len(ports.calls)==count


def test_flash_lower_resume_does_not_hide_unrelated_owner_writes(sample):
    store,root,_=sample
    ports=Ports(choices={'record_fidelity':'unsupported'})
    assert flash.run(store,'S',[['C']],root,ports,apply=True)['status']=='completed'
    evidence=deepcopy(store.get_record_state('evidence_steps','E')['payload'])
    evidence['produced_claim_ids'].append('OTHER')
    store.apply_plan(store.plan_package({'package_id':'unrelated-owner','evidence_steps':[evidence],
        'claims':[{'claim_id':'OTHER','claim_type':'exegesis','statement':'另一項判斷。','evidence_step_ids':['E'],
                   'occurrences':[{'source_id':'S','anchors':[]}]}]}))
    count=len(ports.calls)
    with pytest.raises(ValueError,match='bookkeeping changed outside later campaign group'):
        flash.run(store,'S',[['C']],root,ports,apply=True)
    assert len(ports.calls)==count


def test_campaign_recovers_missing_occurrence_only_from_complete_owned_source_chain(sample):
    store,root,_=sample
    claim=deepcopy(store.get_record_state('claims','C')['payload']);claim['occurrences']=[]
    store.apply_plan(store.plan_package({'package_id':'no-occurrence','claims':[claim]}))
    records,sources=f.load_source(store,'S');assert 'claims/C' in records
    assert f.belongs_to_source(claim,records,'S')
    result=flash.run(store,'S',[['C']],root,Ports(),apply=True)
    assert result['status']=='completed'
    saved=store.get_record_state('claims','C')['payload']
    assert saved['statement']==claim['statement']
    assert saved['occurrences'][0]['source_id']=='S'
    assert saved['occurrences'][0]['anchors']
    missing=deepcopy(records);missing.pop('evidence_steps/E')
    assert not f.belongs_to_source(claim,missing,'S')
    foreign=deepcopy(records);foreign['source_fragments/F']['payload']['source_id']='OTHER'
    assert not f.belongs_to_source(claim,foreign,'S')


@pytest.mark.parametrize('human_race',[False,True])
def test_verified_legacy_ai_sampling_refreshes_but_new_human_event_blocks_commit(sample,monkeypatch,human_race):
    from backend.api.canonical_repository.postgres_store import record_content_sha
    store,root,_=sample
    p=deepcopy(store.get_record_state('claims','C')['payload'])
    p.update(review_status='human_review_required',reviewed_by='old-model')
    resolution={'schema_version':'wang_legacy_ai_review_reconciliation_v1','claim_id':'C',
        'approval_status':'not_human_approved','independent_review_decision':'pass',
        'adjudication_status':'human_spot_check','target_review_status':'human_review_required'}
    with store.connect() as conn:
        conn.execute("UPDATE wang_knowledge.objects SET payload=%s::jsonb,review_status=%s,content_sha256=%s WHERE collection='claims' AND object_id='C'",(json.dumps(p),p['review_status'],record_content_sha(p)))
        conn.execute("INSERT INTO wang_knowledge.review_events(review_event_id,collection,object_id,object_revision,reviewer_kind,reviewer_id,decision,artifact) VALUES ('OLD-AI','claims','C',1,'ai','old-model','human_review_required',%s::jsonb)",(json.dumps({'resolution':resolution}),))
    complete(sample)
    before=store.get_record_state('claims','C');apply=store.apply_plan
    def race(plan,**kwargs):
        if kwargs.get('expected_current_claim_sampling_markers'):
            with store.connect() as conn:
                conn.execute("INSERT INTO wang_knowledge.review_events(review_event_id,collection,object_id,object_revision,reviewer_kind,reviewer_id,decision) VALUES ('NEW-HUMAN','claims','C',1,'human','owner','human_review_required')")
        return apply(plan,**kwargs)
    if human_race:monkeypatch.setattr(store,'apply_plan',race)
    if human_race:
        with pytest.raises(Exception,match='sampling marker or review history'):
            flash.run_group(store,'S',['C'],root/'lower',root/'group',Ports(),apply=True)
        assert store.get_record_state('claims','C')==before
    else:
        result=flash.run_group(store,'S',['C'],root/'lower',root/'group',Ports(),apply=True)
        assert result['applied'] and result['review_sampling_markers']
        assert store.get_record_state('claims','C')['payload']['review_status']=='candidate'
        with store.connect() as conn:
            assert conn.execute("SELECT count(*) FROM wang_knowledge.review_events WHERE collection='claims' AND object_id='C'").fetchone()==(1,)
