"""One flagged-record disposition: keep, statement repair, source supplement, or both."""
from copy import deepcopy
import hashlib
import json

import pytest
from backend.pipeline import claim_flash_evidence as eo, claim_flash_common as f, evidence_refresh as e
from backend.tests.test_evidence_refresh import sample
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres
from backend.tests.test_claim_flash_evidence import Ports as BasePorts, saved_inventory
from backend.tests.test_claim_update_flash import Ports as OriginalPorts

QUOTE='你沒有聽從祂的話，你根本沒信。'
STATEMENT='沒有聽從顯明沒有真正相信。'


@pytest.fixture
def campaign(sample):
    store,root,path=sample
    fragment=store.get_record_state('source_fragments','F')['payload']
    fragment['verbatim_excerpt']='你不知道神的旨意，怎麼遵行？'
    fragment['verbatim_excerpt_sha256']=hashlib.sha256(fragment['verbatim_excerpt'].encode()).hexdigest()
    original=store.get_record_state('evidence_steps','E')['payload']
    targets=[{**deepcopy(original),'evidence_step_id':oid,'statement':text} for oid,text in [
        ('E','拒絕明白神的旨意。'),('KEEP','不知道神的旨意，怎麼遵行？'),
        ('BIND',STATEMENT),('BOTH','拒絕明白顯明沒有真正相信。')]]
    store.apply_plan(store.plan_package({'package_id':'completion-fixture',
        'source_fragments':[fragment],'evidence_steps':targets}))
    records,sources=f.load_source(store,'S')
    plan={'source_id':'S','source_body_sha256':sources['S']['body_sha256'],'groups':[{
        'group_id':'G','paragraph_keys':['S0001'],
        'lower_record_keys':['evidence_steps/'+p['evidence_step_id'] for p in targets],
        'reference_record_keys':[]}]}
    return store,root,path,plan,saved_inventory(root/'previous',sources)


class Ports(BasePorts):
    def __init__(self):super().__init__();self.mutate=None;self.locate=True
    def judge(self,request):
        if 'current_paragraphs' in request['state']:
            self.calls.append(('jev',request))
            answers={}
            for key,spec in request['questions'].items():
                selected=next((pk for pk in request['state']['current_paragraphs'] if pk in spec['criteria']), 'none') if self.locate else 'none'
                answers[key]={'type':'choice','choice':selected,'probabilities':{c:float(c==selected) for c in spec['criteria']}}
            return {'model':e.MODEL,'answers':answers}
        if 'record' in request['state']:
            self.choices={'record_fidelity':'changed'}
            return OriginalPorts.judge(self,request)
        return super().judge(request)
    def repair(self,request):
        if request['input']['task']!='evidence_fidelity_repair':return super().repair(request)
        self.calls.append(('gpt',request));items=[]
        assert request['input']['source_completion_contract']==eo.COMPLETION_CONTRACT
        assert set(request['input']['source_paragraphs'])=={'S0001'}
        for job in request['input']['jobs']:
            oid=job['job_id'].split('/')[1]
            action='keep' if oid=='KEEP' or oid.startswith('EXTRA') else 'repair'
            item={'job_id':job['job_id'],'action':action,'reason':'逐項處置本項疑點。'}
            if oid=='E':item['statement']='不知道神的旨意就無法遵行。'
            if oid in {'BIND','BOTH','O'}:item['quotes']=[{'paragraph_key':'S0001','quote':QUOTE}]
            if oid in {'BOTH','O'}:item['statement']=STATEMENT
            items.append(item)
        reply={'items':items}
        if self.mutate:self.mutate(request,reply)
        return reply


def fidelity_calls(ports):return [req for role,req in ports.calls if role=='gpt' and req['input']['task']=='evidence_fidelity_repair']


def test_four_outcomes_share_one_call_and_are_not_reviewed_again(campaign):
    store,root,_,plan,previous=campaign;ports=Ports();before=store.compile_package()
    result=eo.run(store,'S',plan,root/'run',ports,previous,apply=True)
    assert result['status']=='completed' and len(fidelity_calls(ports))==1
    first=fidelity_calls(ports)[0]
    assert len(first['input']['jobs'])==4
    assert first['schema'] == eo.program_validated_schema(eo.QuoteCompletionEdits.model_json_schema())
    assert 'minLength' not in json.dumps(first['schema'])
    assert 'minItems' not in first['schema']['properties']['items']
    assert 'enum' not in first['schema']['$defs']['QuoteCompletionEdit']['properties']['job_id']
    assert all(req['input']['task']!='source_binding_completion' for role,req in ports.calls if role=='gpt')
    assert store.get_record_state('evidence_steps','E')['payload']['statement']=='不知道神的旨意就無法遵行。'
    assert store.get_record_state('evidence_steps','KEEP')['payload']==next(p for p in before['evidence_steps'] if p['evidence_step_id']=='KEEP')
    for oid in ('BIND','BOTH'):
        p=store.get_record_state('evidence_steps',oid)['payload']
        assert p['statement']==STATEMENT and 'F' in p['source_fragment_ids'] and len(p['source_fragment_ids'])==2
        for fid in p['source_fragment_ids']:
            e.material({'evidence_step_id':oid,'statement':'','source_fragment_ids':[fid]},*f.load_source(store,'S'))
    assert store.compile_package()['claims']==before['claims']
    assert store.get_record_state('source_fragments','F')['payload']==before['source_fragments'][0]
    replay=Ports();again=eo.run(store,'S',plan,root/'run',replay,previous,apply=True)
    assert again['status']=='completed' and replay.calls==[]


@pytest.mark.parametrize('fault',['invented_quote','foreign_paragraph','foreign_record','duplicate_id','keep_with_quotes','needs_source_with_patch','binding_field','empty_reason','empty_quote','protected'])
def test_invalid_or_protected_combined_disposition_never_partially_commits(campaign,fault):
    store,root,_,plan,previous=campaign
    if fault=='protected':
        p=store.get_record_state('evidence_steps','BOTH')['payload'];p['review_status']='human_approved'
        store.apply_plan(store.plan_package({'package_id':'protect','evidence_steps':[p]}))
    before=store.compile_package();ports=Ports()
    def mutate(req,reply):
        item=next(i for i in reply['items'] if i['job_id'].endswith('/BOTH'))
        if fault=='invented_quote':item['quotes'][0]['quote']='原文不存在這句話。'
        if fault=='foreign_paragraph':item['quotes'][0]['paragraph_key']='S9999'
        if fault=='foreign_record':item['job_id']='claims/C'
        if fault=='duplicate_id':reply['items'][0]['job_id']=reply['items'][1]['job_id']
        if fault=='keep_with_quotes':item['action']='keep'
        if fault=='needs_source_with_patch':item['action']='needs_source'
        if fault=='binding_field':item['source_fragment_ids']=['unprovided']
        if fault=='empty_reason':item['reason']=''
        if fault=='empty_quote':item['quotes'][0]['quote']=''
    ports.mutate=mutate
    if fault=='protected':
        assert eo.run(store,'S',plan,root/'run',ports,previous,apply=True)['status']=='needs_attention'
    else:
        with pytest.raises(ValueError):eo.run(store,'S',plan,root/'run',ports,previous,apply=True)
    after=store.compile_package();before.pop('compiled_at');after.pop('compiled_at');before.pop('package_id');after.pop('package_id')
    assert after==before


@pytest.mark.parametrize('same_value',[False,True])
def test_empty_evidence_repairs_are_rejected_before_acceptance(campaign,same_value):
    store,root,_,plan,previous=campaign;ports=Ports()
    def empty_repairs(request,reply):
        reply['items']=[{'job_id':job['job_id'],'action':'repair','reason':'已補掛原文。',
                         'statement':job['record']['statement'] if same_value else None}
                        for job in request['input']['jobs']]
    ports.mutate=empty_repairs
    before=store.get_record_state('evidence_steps','E')
    with pytest.raises(ValueError,match='Evidence repair has no effective changes'):
        eo.run(store,'S',plan,root/'run',ports,previous,apply=True)
    assert store.get_record_state('evidence_steps','E')==before
    assert not list((root/'run'/'accepted').glob('*.json'))
    assert not list((root/'run').rglob('commit-intent.json'))


@pytest.mark.parametrize('where',['compile','sql','receipt'])
def test_saved_combined_answer_and_commit_resume_without_repeat_calls(campaign,monkeypatch,where):
    store,root,_,plan,previous=campaign;ports=Ports()
    if where=='compile':
        old=eo.source.supplement_binding
        monkeypatch.setattr(eo.source,'supplement_binding',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('program interruption')))
    elif where=='sql':
        old=store.apply_plan
        def crash(*a,**k):old(*a,**k);raise RuntimeError('program interruption')
        monkeypatch.setattr(store,'apply_plan',crash)
    else:
        old=e.publish
        def crash(path,result):
            if result.get('source_completion_contract') and result.get('applied'):raise RuntimeError('program interruption')
            return old(path,result)
        monkeypatch.setattr(e,'publish',crash)
    with pytest.raises(RuntimeError,match='program interruption'):eo.run(store,'S',plan,root/'run',ports,previous,apply=True)
    if where=='compile':monkeypatch.setattr(eo.source,'supplement_binding',old)
    elif where=='sql':monkeypatch.setattr(store,'apply_plan',old)
    else:monkeypatch.setattr(e,'publish',old)
    resumed=Ports();result=eo.run(store,'S',plan,root/'run',resumed,previous,apply=True)
    assert result['status']=='completed' and not resumed.calls
    assert len(fidelity_calls(ports))==1
    assert len(list((root/'run/groups').glob('*/*/commit.json')))==1


def test_focused_disposition_can_fix_binding_without_rewriting_other_fields(campaign):
    store,root,_,plan,previous=campaign;ports=Ports()
    ports.focus_evidence_findings=True;ports.focused_review_instruction='只處置Jev疑點，同時補齊原文。'
    result=eo.run(store,'S',plan,root/'run',ports,previous,apply=True)
    assert result['status']=='completed'
    assert fidelity_calls(ports)[0]['focused_contract']==eo.FOCUSED_CONTRACT
    assert 'source_paragraphs' in fidelity_calls(ports)[0]['system']


def test_unresolved_source_is_a_handoff_without_an_automatic_second_gpt(campaign):
    store,root,_,plan,previous=campaign;ports=Ports()
    def mutate(req,reply):
        reply['items']=[{'job_id':i['job_id'],'action':'needs_source','reason':'所給候選仍不足。'} for i in reply['items']]
    ports.mutate=mutate
    result=eo.run(store,'S',plan,root/'run',ports,previous,apply=True)
    assert result['status']=='needs_attention' and len(fidelity_calls(ports))==1
    assert all(p['layer']=='source' for p in result['groups'][0]['pending'])


def test_observation_can_repair_and_supplement_without_changing_claim_graph(campaign):
    store,root,_,plan,previous=campaign
    store.apply_plan(store.plan_package({'package_id':'observation-target','observations':[{
        'observation_id':'O','statement':'拒絕明白顯明沒有真正相信。','source_fragment_ids':['F']}]}))
    plan['groups'][0]['lower_record_keys']=['observations/O'];ports=Ports()
    before=store.compile_package()
    result=eo.run(store,'S',plan,root/'observation',ports,previous,apply=True)
    assert result['status']=='completed'
    p=store.get_record_state('observations','O')['payload']
    assert p['statement']==STATEMENT and len(p['source_fragment_ids'])==2
    assert store.compile_package()['claims']==before['claims']
    assert store.compile_package()['knowledge_relations']==before['knowledge_relations']


def test_omitted_job_only_is_requested_again_and_replay_reuses_both_answers(campaign):
    store,root,_,plan,previous=campaign;ports=Ports();base=ports.repair
    def omit(request):
        reply=base(request)
        if request['input']['task']=='evidence_fidelity_repair' and len(request['input']['jobs'])==4:
            reply['items']=reply['items'][:-1]
        return reply
    ports.repair=omit
    assert eo.run(store,'S',plan,root/'missing',ports,previous,apply=True)['status']=='completed'
    requests=fidelity_calls(ports)
    assert [len(req['input']['jobs']) for req in requests]==[4,1]
    assert requests[1]['input']['jobs'][0]['job_id']==requests[0]['input']['jobs'][-1]['job_id']
    assert requests[1]['schema'] == requests[0]['schema']
    replay=Ports();assert eo.run(store,'S',plan,root/'missing',replay,previous,apply=True)['status']=='completed'
    assert not replay.calls


def test_unknown_combined_call_cannot_be_reissued_or_downgraded(campaign):
    store,root,_,plan,previous=campaign;ports=Ports();base=ports.repair
    def timeout(req):
        if req['input']['task']=='evidence_fidelity_repair':
            ports.calls.append(('gpt',req));raise TimeoutError('no reply')
        return base(req)
    ports.repair=timeout
    with pytest.raises(TimeoutError):eo.run(store,'S',plan,root/'unknown',ports,previous,apply=True)
    replay=Ports()
    with pytest.raises(ValueError,match='no saved reply'):eo.run(store,'S',plan,root/'unknown',replay,previous,apply=True)
    assert not replay.calls


def test_legacy_intent_keeps_legacy_single_disposition_and_saved_answer(campaign):
    store,root,_,plan,previous=campaign;legacy=root/'legacy';legacy.mkdir(parents=True)
    pin=f.bind_source(store,'S',legacy)
    e.save(legacy/'intent.json',{'version':eo.VERSION,'source':pin,'groups':plan,'selected':['G'],
        'models':BasePorts.identity,'gpt_output_limit':None,
        'policy':e.digest([eo.PROMPT,eo.COVERAGE_PROMPT,eo.components.PROMPT,eo.Edits.model_json_schema(),eo.MissingEdits.model_json_schema()]),
        'previous':str(previous.resolve())})
    class LegacyPorts(BasePorts):
        def repair(self,req):
            if req['input']['task']=='evidence_fidelity_repair':
                assert 'source_completion_contract' not in req['input']
                assert all('source_candidates' not in j for j in req['input']['jobs'])
            return super().repair(req)
    ports=LegacyPorts()
    assert eo.run(store,'S',plan,legacy,ports,previous,apply=True)['status']=='completed'
    assert not any(role=='jev' and 'current_paragraphs' in req['state'] for role,req in ports.calls)
    replay=LegacyPorts();eo.run(store,'S',plan,legacy,replay,previous,apply=True)
    assert not replay.calls


def test_source_changes_during_combined_answer_block_all_writes(campaign):
    store,root,path,plan,previous=campaign;ports=Ports();before=store.compile_package()
    def drift(req,reply):
        data=json.loads(path.read_text());data['script'][0]['text']+='新增文字。';path.write_text(json.dumps(data))
    ports.mutate=drift
    with pytest.raises(ValueError,match='source changed'):eo.run(store,'S',plan,root/'drift',ports,previous,apply=True)
    after=store.compile_package();before.pop('compiled_at');after.pop('compiled_at');before.pop('package_id');after.pop('package_id')
    assert before==after


@pytest.mark.parametrize('group_hit',[True,False])
def test_candidate_search_stays_in_group_then_falls_back_only_if_no_candidate(tmp_path,group_hit):
    from backend.tests.test_claim_flash_evidence import setup
    records,sources,plan=setup(tmp_path);ports=Ports();lookup=[]
    original=ports.judge
    def locate(req):
        if 'current_paragraphs' not in req['state']:return original(req)
        lookup.append(req)
        wanted='S0001' if group_hit else 'S0002'
        return {'model':e.MODEL,'answers':{key:{'type':'choice',
            'choice':wanted if wanted in spec['criteria'] else 'none',
            'probabilities':{c:float(c==(wanted if wanted in spec['criteria'] else 'none')) for c in spec['criteria']}}
            for key,spec in req['questions'].items()}}
    ports.judge=locate
    jobs=[{'job_id':'evidence_steps/E','record':eo.visible(records['evidence_steps/E']['payload']),
           'components':[],'jev':{'record_fidelity':'changed'}}]
    candidates=eo.source.binding_candidates(jobs,records,sources,['S0001'],tmp_path/'lookup',ports)
    assert set(lookup[0]['state']['current_paragraphs'])=={'S0001'}
    value=candidates['evidence_steps/E']
    if group_hit:
        assert len(lookup)==1 and {p['paragraph_key'] for p in value['nearby_paragraphs']}=={'S0001'}
    else:
        assert any('S0002' in req['state']['current_paragraphs'] for req in lookup[1:])
        assert value['source_lookup']['candidate_paragraph_keys']==['S0002']
    assert not any(role=='gpt' for role,req in ports.calls)


@pytest.mark.parametrize('stale',[False,True])
def test_candidate_human_clarification_is_in_gpt_input_and_transaction_guard(tmp_path,stale):
    from backend.tests.test_claim_flash_evidence import setup
    from backend.tests.test_claim_flash_human_source import fact
    records,sources,plan=setup(tmp_path)
    # Source helper may select another paragraph from this sermon; its durable
    # human interpretation must be seen by GPT and guarded by the transaction.
    clarification=fact(sources,'S0002')
    if stale:sources['S']['paragraphs']['S0002']['text']+='更改原文。'
    from types import SimpleNamespace
    store=SimpleNamespace(read_human_source_decisions=lambda ids:[clarification])
    ports=Ports();original=ports.judge
    def locate(req):
        if 'current_paragraphs' not in req['state']:return original(req)
        wanted='S0002'
        return {'model':e.MODEL,'answers':{key:{'type':'choice',
            'choice':wanted if wanted in spec['criteria'] else 'none',
            'probabilities':{c:float(c==(wanted if wanted in spec['criteria'] else 'none')) for c in spec['criteria']}}
            for key,spec in req['questions'].items()}}
    ports.judge=locate
    original_repair=ports.repair
    def repair(req):
        if req['input']['task']=='evidence_fidelity_repair':
            job=req['input']['jobs'][0]
            assert job['human_source_decisions'][0]['decision_id']==clarification['decision_id']
            ports.calls.append(('gpt',req))
            return {'items':[{'job_id':job['job_id'],'action':'keep','reason':'依人工澄清保留。'}]}
        return original_repair(req)
    ports.repair=repair
    def run():return eo.run_group(store,records,sources,plan['groups'][0],['evidence_steps/E'],[],tmp_path/'run',ports,
        saved_inventory(tmp_path/'previous',sources),apply=False,complete_source=True)
    if stale:
        with pytest.raises(ValueError,match='reconfirmation'):run()
        assert not fidelity_calls(ports)
    else:
        result=run()
        assert result['status']=='verified' and len(fidelity_calls(ports))==1
        assert result['human_source_guard']['paragraphs']['S']==['S0001','S0002']
        assert result['human_source_guard']['entries']==[clarification]
        # Even an unchanged statement can have depended on a candidate-only
        # interpretation. Do not silently reuse that decision after it changes.
        updated_record=deepcopy(clarification['record'])
        updated_record['answers'][0]['answer']='另一項人工來源澄清。'
        updated=eo.human.entry(clarification['artifact'],updated_record,'S',sources['S'],['S0002'],'confirmation')
        store.read_human_source_decisions=lambda ids:[updated]
        ports=Ports()
        with pytest.raises(ValueError,match='candidate source interpretation changed'):run()
        assert not ports.calls


def test_keep_with_identical_echoed_fields_is_not_an_extra_correction_round(campaign):
    store,root,_,plan,previous=campaign;ports=Ports()
    def echo(req,reply):
        originals={j['job_id']:j['record'] for j in req['input']['jobs']}
        for item in reply['items']:
            if item['action']=='keep':item.update(originals[item['job_id']])
    ports.mutate=echo
    assert eo.run(store,'S',plan,root/'echo',ports,previous,apply=True)['status']=='completed'
    assert len(fidelity_calls(ports))==1


def test_seven_small_records_split_into_four_and_three_despite_fitting_bytes(campaign):
    store,root,_,plan,previous=campaign
    original=store.get_record_state('evidence_steps','KEEP')['payload']
    extra=[{**deepcopy(original),'evidence_step_id':'EXTRA'+str(i)} for i in range(3)]
    store.apply_plan(store.plan_package({'package_id':'seven-records','evidence_steps':extra}))
    plan['groups'][0]['lower_record_keys'].extend('evidence_steps/'+p['evidence_step_id'] for p in extra)
    ports=Ports();result=eo.run(store,'S',plan,root/'seven',ports,previous,apply=True)
    assert result['status']=='completed'
    requests=fidelity_calls(ports)
    assert [len(req['input']['jobs']) for req in requests]==[4,3]
    assert requests[0]['schema'] == requests[1]['schema']
    assert all(len(req['input']['source_paragraphs'])==1 for req in requests)


def test_empty_repair_correction_only_resends_affected_item(campaign):
    store, root, _, plan, previous = campaign
    ports = Ports()
    ports.correct_empty_evidence_once = True
    ports.new_gpt_identity = {**ports.identity['gpt'], 'model': 'gpt-6-sol'}

    def empty_first_reply(request, reply):
        for job, item in zip(request['input']['jobs'], reply['items'], strict=True):
            if job['job_id'] == 'evidence_steps/BIND' and 'previous_empty_repair' not in job:
                item.pop('quotes')

    ports.mutate = empty_first_reply
    result = eo.run(store, 'S', plan, root / 'corrected', ports, previous, apply=True)
    assert result['status'] == 'completed'
    initial, correction = fidelity_calls(ports)
    assert len(initial['input']['jobs']) == 4
    assert [job['job_id'] for job in correction['input']['jobs']] == ['evidence_steps/BIND']
    assert correction['identity']['model'] == 'gpt-6-sol'
    assert len(store.get_record_state('evidence_steps', 'BIND')['payload']['source_fragment_ids']) == 2
    replay = Ports()
    replay.correct_empty_evidence_once = True
    replay.new_gpt_identity = ports.new_gpt_identity
    assert eo.run(store, 'S', plan, root / 'corrected', replay, previous, apply=True)['status'] == 'completed'
    assert replay.calls == []
