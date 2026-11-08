"""Source search shares inputs and repairs only the owner's binding, never history."""
from copy import deepcopy
import hashlib
import json
import pytest

from backend.pipeline import claim_flash_source_lookup as lookup, claim_flash_common as f
from backend.pipeline import claim_flash_lower as lower, evidence_refresh as e
from backend.tests.test_claim_flash_source_assembly import scene
from backend.tests.test_claim_update_flash import Ports, sample, postgres_store, _temporary_postgres


class LookupPorts:
    def __init__(self, matches=(), choice=None, fail=False):
        self.matches=matches;self.choice=choice;self.fail=fail;self.calls=[]
    def judge(self, req):
        self.calls.append(req)
        if self.fail:raise TimeoutError('unknown response')
        answers={}
        for label,q in req['questions'].items():
            selected=self.choice or next((pk for pk in q['criteria'] if pk in self.matches),'none')
            answers[label]={'type':'choice','choice':selected,
                            'probabilities':{k:float(k==selected) for k in q['criteria']}}
        return {'model':e.MODEL,'answers':answers}


def jobs(count=2):
    return [{'job_id':f'evidence_steps/E{i}','record':{'statement':f'待查判斷{i}。'},
             'material':{'fragments':[{'quote':'歷史引文。'}], 'nearby_paragraphs':[]},
             'verdict':{'record_fidelity':'unsupported'},'components':None} for i in range(count)]


def test_many_questions_share_source_and_retrieve_more_than_one_passage(tmp_path):
    records,sources=scene(tmp_path,['條件。','例子。','回答。','限定。'],[('S0001','條件。')])
    ports=LookupPorts(matches=['S0001','S0003','S0004'])
    result=lookup.lookup(jobs(),sources,tmp_path/'lookup',ports)
    assert len(ports.calls[0]['questions'])==2
    assert len(ports.calls[0]['state']['current_paragraphs'])==4
    assert all(x['source_lookup']['candidate_paragraph_keys']==['S0001','S0003','S0004'] for x in result.values())
    assert len(ports.calls)==4  # Three distinct matches, then an explicit no-match.
    assert 'S0001' not in ports.calls[1]['questions']['E000']['criteria']
    assert all(lookup.size(req)<=f.JEV_BYTES for req in ports.calls)
    replay=LookupPorts(fail=True)
    assert lookup.lookup(jobs(),sources,tmp_path/'lookup',replay)==result
    assert not replay.calls


def test_all_source_blocks_are_searched_and_no_candidate_is_not_a_verdict(tmp_path,monkeypatch):
    _,sources=scene(tmp_path,['甲'*140,'乙'*140,'丙'*140,'丁'*140],[('S0001','甲'*140)])
    monkeypatch.setattr(f,'JEV_BYTES',1900)
    ports=LookupPorts(matches=['S0004'])
    result=lookup.lookup(jobs(1),sources,tmp_path/'lookup',ports)
    assert result['evidence_steps/E0']['source_lookup']['candidate_paragraph_keys']==['S0004']
    seen=set().union(*(set(req['state']['current_paragraphs']) for req in ports.calls))
    assert seen==set(sources['S']['paragraphs'])
    assert all(lookup.size(req)<=1900 for req in ports.calls)
    unknown=lookup.lookup(jobs(1),sources,tmp_path/'uncertain',LookupPorts(choice='uncertain'))
    assert unknown['evidence_steps/E0']['source_lookup']['uncertain_paragraph_blocks']
    assert unknown['evidence_steps/E0']['source_lookup']['absence_is_not_proof']


def test_late_oversize_paragraph_fails_before_any_call(tmp_path,monkeypatch):
    _,sources=scene(tmp_path,['甲。','乙'*1000],[('S0001','甲。')])
    monkeypatch.setattr(f,'JEV_BYTES',1300)
    ports=LookupPorts()
    with pytest.raises(ValueError,match='no call made'):
        lookup.lookup(jobs(1),sources,tmp_path/'lookup',ports)
    assert not ports.calls


def test_unknown_response_is_not_reissued_and_input_drift_is_rejected(tmp_path):
    _,sources=scene(tmp_path,['原文。'],[('S0001','原文。')])
    ports=LookupPorts(fail=True)
    with pytest.raises(TimeoutError):lookup.lookup(jobs(),sources,tmp_path/'lookup',ports)
    with pytest.raises(ValueError,match='no saved reply'):
        lookup.lookup(jobs(),sources,tmp_path/'lookup',ports)
    assert len(ports.calls)==1
    changed=deepcopy(sources);changed['S']['body_sha256']='changed'
    with pytest.raises(ValueError,match='saved input differs'):
        lookup.lookup(jobs(),changed,tmp_path/'lookup',ports)
    assert len(ports.calls)==1


def test_normalization_is_unique_and_retains_numbers_negation_and_original_offsets(tmp_path):
    records,sources=scene(tmp_path,['前文。聽從祂的話，顯明信心。','不是七十人，是七十二人。'],[('S0001','聽從祂的話，顯明信心。')])
    locator=lookup.Locator(sources)
    assert locator.unique('听从祂的话 显明信心')['quote']=='聽從祂的話，顯明信心'
    assert locator.unique('是七十一人') is None
    assert locator.unique('不是七十二人') is None
    assert locator.unique('沒有聽從') is None
    duplicate=deepcopy(sources);duplicate['S']['paragraphs']['S0003']=deepcopy(sources['S']['paragraphs']['S0001'])
    assert lookup.Locator(duplicate).unique('聽從祂的話') is None


def test_deleted_fragment_requires_semantic_disposition_even_when_words_exist(tmp_path):
    records,sources=scene(tmp_path,['原話仍出現。'],[('S0001','原話仍出現。')])
    records['source_fragments/F0']['payload']['anchor_state']='source_deleted'
    before=deepcopy(records)
    material,exact=lookup.Locator(sources).prepare('evidence_steps/E',records,sources)
    assert exact is None and material['original_text_excerpts']==[]
    assert material['historical_quotes']==['原話仍出現。'] and records==before
    records['source_fragments/F0']['payload']['source_id']='OTHER'
    with pytest.raises(ValueError,match='foreign'):
        lookup.Locator(sources).prepare('evidence_steps/E',records,sources)


def test_missing_answer_is_found_and_bound_without_editing_claim_or_shared_fragment(sample):
    store,root,source=sample
    data=json.loads(source.read_text())
    data['script']=[{'index':1,'text':'君王出征是騎馬還是騎驢？','start_time':1.,'end_time':2.}]+[
        {'index':i,'text':'其他話題。','start_time':float(i),'end_time':float(i+1)} for i in range(2,7)]+[
        {'index':7,'text':'猶太君王出征都是騎馬。','start_time':7.,'end_time':8.}]
    source.write_text(json.dumps(data,ensure_ascii=False))
    doc=deepcopy(store.get_record_state('source_documents','S')['payload'])
    doc['source_file_sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
    snap=e.source_snapshot({'path':str(source),'file_sha256':doc['source_file_sha256']},source=doc)
    _,frags,_=e.repair_postimage({'evidence_step_id':'E','statement':'出征騎馬。'},{'evidence_id':'E','action':'repair','reason':'測試來源。',
        'statement':'出征騎馬。','speaker':'professor','stance':'asserted',
        'quotes':[{'paragraph_key':'S0001','quote':'君王出征是騎馬還是騎驢？'}]},
        {'nearby_paragraphs':[{'source_id':'S','paragraph_key':'S0001',**snap['paragraphs']['S0001']}]},{},{'S':snap})
    ev=deepcopy(store.get_record_state('evidence_steps','E')['payload'])
    ev.update(statement='猶太君王出征騎馬。',source_fragment_ids=[next(iter(frags)).split('/',1)[1]])
    store.apply_plan(store.plan_package({'package_id':'question-only','source_documents':[doc],
        'source_fragments':list(frags.values()),'evidence_steps':[ev]}))
    claim=store.get_record_state('claims','C');old=store.get_record_state('source_fragments',ev['source_fragment_ids'][0])
    def repair(req):
        if req['input']['task']=='lower_repair':
            assert 'S0007' in {p['paragraph_key'] for p in req['input']['jobs'][0]['material']['nearby_paragraphs']}
            return {'items':[{'job_id':j['job_id'],'action':'repair','statement':ev['statement'],
                'speaker':'professor','stance':'asserted','scripture_refs':[],
                'quotes':[{'paragraph_key':'S0007','quote':'猶太君王出征都是騎馬。'}],'reason':'補入教授回答。'} for j in req['input']['jobs']]}
    ports=Ports(choices={'record_fidelity':'unsupported','E000':lambda req:'S0007' if 'S0007' in req['questions']['E000']['criteria'] else 'none'},reply=repair)
    result=lower.run(store,'S',root/'lower',ports,apply=True)
    assert result['applied'] and not result['pending']
    assert store.get_record_state('claims','C')==claim
    assert store.get_record_state('source_fragments',ev['source_fragment_ids'][0])==old
    repaired=store.get_record_state('evidence_steps','E')['payload']
    assert store.get_record_state('source_fragments',repaired['source_fragment_ids'][0])['payload']['paragraph_key']=='S0007'
    assert repaired['support_eligibility']==ev['support_eligibility']
    calls=len(ports.calls)
    assert lower.run(store,'S',root/'lower',ports,apply=True)['applied']
    assert len(ports.calls)==calls


@pytest.mark.parametrize('action',['repair','keep','needs_source'])
def test_deleted_binding_cannot_be_kept_and_shared_history_is_unchanged(sample,action):
    store,root,_=sample
    frag=deepcopy(store.get_record_state('source_fragments','F')['payload']);frag['anchor_state']='source_deleted'
    ev=deepcopy(store.get_record_state('evidence_steps','E')['payload']);ev['source_binding_status']='source_deleted'
    store.apply_plan(store.plan_package({'package_id':'delete','source_fragments':[frag],'evidence_steps':[ev]}))
    before=store.get_record_state('evidence_steps','E');historical=store.get_record_state('source_fragments','F')
    def repair(req):
        if req['input']['task']=='lower_repair':
            return {'items':[{'job_id':j['job_id'],'action':action,'statement':'沒有聽從，顯明沒有相信。',
                'speaker':'professor','stance':'asserted','scripture_refs':[],
                'quotes':[{'paragraph_key':'S0001','quote':'你沒有聽從祂的話，你根本沒信。'}],
                'reason':'以目前原文判定。'} for j in req['input']['jobs']]}
    ports=Ports(reply=repair)
    result=lower.run(store,'S',root/'lower',ports,apply=True)
    assert store.get_record_state('source_fragments','F')==historical
    if action=='repair':
        assert result['applied'] and not result['pending']
        p=store.get_record_state('evidence_steps','E')['payload']
        assert p['source_binding_status']=='needs_claim_refresh' and p['source_fragment_ids']!=['F']
        records,sources=f.load_source(store,'S');f.value('evidence_steps/E',records,sources)
    else:
        assert not result['applied'] and result['pending']
        assert store.get_record_state('evidence_steps','E')==before


def test_invalid_deterministic_fragment_identity_gets_a_new_id_without_overwriting(tmp_path):
    records,sources=scene(tmp_path,['原話。'],[('S0001','原話。')])
    fid='FR-BU-'+e.digest(['S',sources['S']['body_sha256'],'S0001','原話。'])[:32]
    historical=deepcopy(records['source_fragments/F0'])
    historical['payload'].update(fragment_id=fid,anchor_state='source_deleted')
    records['source_fragments/'+fid]=historical
    before=deepcopy(records)
    p=records['evidence_steps/E']['payload']
    edit=lower.Edit(job_id='evidence_steps/E',action='repair',statement=p['statement'],speaker='professor',
        stance='asserted',scripture_refs=[],quotes=[{'paragraph_key':'S0001','quote':'原話。'}],reason='只綁現版原話。')
    # Remove the valid alternate identity so that only the invalid deterministic ID collides.
    del records['source_fragments/F0']
    material={'nearby_paragraphs':[{'source_id':'S','paragraph_key':'S0001',**sources['S']['paragraphs']['S0001']}]}
    post,new,_=lower.record_postimage('evidence_steps/E',p,edit,material,records,sources)
    assert post['source_fragment_ids']!=[fid] and 'source_fragments/'+fid not in new
    assert records['source_fragments/'+fid]==before['source_fragments/'+fid]


def test_source_pending_defers_its_relations_and_keeps_other_results(sample):
    store,root,_=sample
    good=deepcopy(store.get_record_state('source_fragments','F')['payload']);good['fragment_id']='GOOD'
    dead=deepcopy(good);dead.update(fragment_id='F',anchor_state='source_deleted')
    store.apply_plan(store.plan_package({'package_id':'partial-source','source_fragments':[good,dead],
        'observations':[{'observation_id':'O','statement':'沒有聽從顯明沒有相信。','source_fragment_ids':['GOOD']}],
        'knowledge_relations':[{'relation_id':'R','from_id':'O','to_id':'E','relation_type':'supports','reason':'行為支持判斷。'}]}))
    def repair(req):
        if req['input']['task']=='lower_repair':
            return {'items':[{'job_id':j['job_id'],'action':'needs_source','statement':j['record']['statement'],
                'speaker':'professor','stance':'asserted','scripture_refs':[],
                'quotes':[{'paragraph_key':'S0001','quote':'你沒有聽從祂的話，你根本沒信。'}],
                'reason':'舊綁定尚無可靠處置。'} for j in req['input']['jobs']]}
    result=lower.run(store,'S',root/'lower',Ports(reply=repair))
    assert not result['applied'] and result['pending']
    assert any(x['object_key']=='observations/O' for x in result['outcomes'])


def test_missing_api_configuration_is_not_recorded_as_an_unknown_call(tmp_path,monkeypatch):
    from types import SimpleNamespace
    monkeypatch.delenv('TYPESAFE_API_KEY',raising=False)
    ports=e.Models(SimpleNamespace(backend='codex_subscription',model='test',reasoning_effort='medium'),tmp_path)
    with pytest.raises(ValueError,match='no request sent'):
        f.jev(tmp_path,ports,{'text':'原文。'},{'q':{'type':'choice','instructions':'分類。','criteria':{'a':'甲','b':'乙'}}})
    assert not list(tmp_path.rglob('started.json'))
    assert list(tmp_path.rglob('request.json'))


def test_question_lookup_uses_the_question_text_instead_of_an_empty_statement(tmp_path):
    _,sources=scene(tmp_path,['這詞是什麼意思？'],[('S0001','這詞是什麼意思？')])
    job=jobs(1)[0];job['record']={'text':'這詞是什麼意思？'}
    ports=LookupPorts(matches=['S0001'])
    lookup.lookup([job],sources,tmp_path/'question',ports)
    assert ports.calls[0]['state']['records']['E000']['statement']=='這詞是什麼意思？'
