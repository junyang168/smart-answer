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
            default='a_to_b_supports' if name=='relation' else 'no'
            chosen=self.choices.get(name,default)
            if callable(chosen):chosen=chosen(request)
            answers[name]={'type':'choice','choice':chosen,'probabilities':{k:float(k==chosen) for k in spec['criteria']}}
        return {'model':e.MODEL,'answers':answers}
    def repair(self,request):
        self.calls.append(('gpt',request))
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
    store,root,ports=complete(sample,Ports(choices={'record_fidelity':'yes'}))
    assert store.get_record_state('evidence_steps','E')['payload']['statement']=='沒有聽從祂的話，顯明沒有真正相信祂。'
    assert len([q for role,q in ports.calls if role=='jev' and 'record_fidelity' in q['questions']])==1
    assert len([q for role,q in ports.calls if role=='gpt' and q['input']['task']=='lower_repair'])==1


def test_pairs_use_revised_statement_and_not_old_statement(sample):
    store,root,_=complete(sample)
    def fix(req):
        if req['input']['task']=='claim_statement':
            return {'items':[{'claim_id':'C','action':'repair','statement':'沒有聽從祂的話，顯明沒有真正相信祂。',
                'evidence_step_ids':['E'],'scripture_refs':[],'reason':'與原文一致。'}]}
    ports=Ports(choices={'statement_fidelity':'yes'},reply=fix)
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
        flash.run_group(store,'S',['C'],root/'lower',root/'group',Ports(choices={'support':'yes'},reply=bad),apply=True)
    assert store.get_record_state('claims','C')==before


def test_payload_splits_on_job_count_and_bytes_before_any_call(tmp_path,monkeypatch):
    monkeypatch.setattr(f,'GPT_BYTES',1500);monkeypatch.setattr(f,'GPT_JOBS',2)
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
    ports=Ports(choices={'missing_lower':'yes','conclusion_candidate':'yes'},reply=coverage)
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


def test_bad_locator_stops_before_any_model_call(sample):
    store,root,_=sample
    p=deepcopy(store.get_record_state('source_fragments','F')['payload']);p['anchor_state']='unresolved'
    store.apply_plan(store.plan_package({'package_id':'bad-anchor','source_fragments':[p]}))
    ports=Ports();result=lower.run(store,'S',root/'lower',ports,apply=True)
    assert result['status']=='needs_attention' and not ports.calls
    assert result['pending'][0]['reason']=='source_calibration_required'


def test_protected_evidence_is_not_silently_rewritten(sample):
    store,root,_=sample
    p=deepcopy(store.get_record_state('evidence_steps','E')['payload']);p['review_status']='human_hold'
    store.apply_plan(store.plan_package({'package_id':'hold','evidence_steps':[p]}))
    result=lower.run(store,'S',root/'lower',Ports(choices={'record_fidelity':'yes'}),apply=True)
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
    ports=Ports(choices={'answer_links':'yes'},reply=answer)
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
        flash.run_group(store,'S',['C'],root/'lower',root/'group',Ports(choices={'answer_links':'yes'},reply=bad),apply=True)
    assert store.get_record_state('questions','Q')['payload']['answer_claim_ids']==[]


def test_new_claim_is_handed_up_checked_linked_and_committed_once(sample):
    store,root,_=sample
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
    ports=Ports(choices={'conclusion_candidate':'yes'},reply=reply)
    lower.run(store,'S',root/'lower',ports,apply=True)
    result=flash.run_group(store,'S',['C'],root/'lower',root/'group',ports,apply=True)
    assert result['applied'] and len(result['new_claim_ids'])==1
    cid=result['new_claim_ids'][0]
    assert set(store.get_record_state('evidence_steps','E')['payload']['produced_claim_ids'])=={'C',cid}
    assert store.get_record_state('claims',cid)['payload']['occurrences'][0]['anchors']
    assert len([q for role,q in ports.calls if role=='jev' and 'statement_fidelity' in q['questions']])==2
    count=len(ports.calls)
    assert flash.run_group(store,'S',['C'],root/'lower',root/'group',ports,apply=True)['applied']
    assert len(ports.calls)==count


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
    ports=Ports(choices={'missing_lower':'yes','conclusion_candidate':'yes'},reply=reply)
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
        if req['input']['task']=='missing_attachments':
            p=deepcopy(store.get_record_state('claims','C')['payload']);p['statement']='同時有人改寫了。'
            store.apply_plan(store.plan_package({'package_id':'concurrent-target','claims':[p]}))
    before=store.get_record_state('evidence_steps','E')
    with pytest.raises(Exception):
        flash.run_group(store,'S',['C'],root/'lower',root/'group',Ports(reply=concurrent),apply=True)
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
    store,root,_=sample;ports=Ports(choices={'record_fidelity':'yes'})
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
    def choose(req):
        if req['input']['task']=='orphan_candidates':
            jobs=req['input']['jobs']
            assert all('evidence' not in c for j in jobs for c in j['target_claims'].values())
            return {'items':[{'job_id':j['job_id'],'pairs':[{'a':'evidence_steps/FAR','b':'claims/C','reason':'同篇相關結論。'}],
                'unresolved_keys':[],'reason':'候選不是通過。'} for j in jobs]}
    # Provide the actual record material but remove its paragraph routing hint to force the fallback.
    from unittest.mock import patch
    original=flash.paragraph_keys
    with patch.object(flash,'paragraph_keys',side_effect=lambda key,rs:set() if key=='evidence_steps/FAR' else original(key,rs)):
        records['evidence_steps/FAR']['payload']['source_fragment_ids']=['F']
        plan=flash.orphan_plan(records,sources,root/'far-plan',Ports(reply=choose))
    assert plan['pairs'][0]['a']=='evidence_steps/FAR'


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
    result=lower.run(store,'S',root/'lower',Ports(choices={'missing_lower':'yes'},reply=reply),apply=True)
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
    assert len(lower_req['state']['original_text_excerpts'])==2
    assert len(claim_req['state']['evidence'][0]['original_text_excerpts'])==2


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
        lower.run(store,'S',root/'lower',Ports(choices={'record_fidelity':'yes'},reply=drift),apply=True)
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
    with pytest.raises(ValueError,match='bounded request size'):
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
