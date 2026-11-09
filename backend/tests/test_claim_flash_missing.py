"""Missing Claims: new-only review, identity safety, SQL and saved-answer recovery."""
from copy import deepcopy
import hashlib
import json
import pytest
from backend.pipeline import claim_flash_missing as stage, claim_flash_relations as relations
from backend.pipeline import claim_flash_common as f
from backend.pipeline import claim_flash_evidence as eo, claim_flash_statements as statements, claim_flash_arguments as arguments
from backend.tests.test_claim_flash_evidence import Ports as EvidencePorts, saved_inventory
from backend.tests.test_claim_flash_relations import ready as relation_ready, Ports as RelationPorts
from backend.tests.test_claim_flash_arguments import Ports as ArgumentPorts
from backend.tests.test_claim_update_flash import Ports as BasePorts
from backend.tests.test_evidence_refresh import sample
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres


def candidate(**changes):
    return {'action':'new_claim','claim_id':'','statement':'真正相信必然表現在聽從祂的話。',
        'support_keys':['evidence_steps/E'],'fragment_ids':['F'],'scripture_refs':[],
        'answer_question_ids':[],'reason':'教授以沒有聽從的觀察推到沒有真正相信。',**changes}


class Ports(ArgumentPorts):
    def __init__(self,candidates=(),**kwargs):super().__init__(**kwargs);self.candidates=list(candidates)
    def repair(self,request):
        task=request['input']['task']
        if task=='missing_group_claims':
            self.calls.append(('gpt',request));return {'candidates':self.candidates,'reason':'已對照教授原文、獨立論據及既有承載。'}
        if task in {'claim_fidelity','claim_support','claim_scripture'}:
            self.calls.append(('gpt',request))
            return {'items':[{'job_id':j['job_id'],'action':'keep','reason':'完整材料保留此判斷及限定。'} for j in request['input']['jobs']]}
        return super().repair(request)


@pytest.fixture
def ready(relation_ready):
    store,root,aroot=relation_ready;rroot=root/'relations'
    assert relations.run(store,'S',aroot,rroot,RelationPorts(),apply=True)['status']=='verified'
    return store,root,rroot


def test_no_missing_claims_needs_one_task_and_no_other_review(ready):
    store,root,rroot=ready;ports=Ports()
    result=stage.run(store,'S',rroot,root/'missing',ports,apply=True)
    assert result['status']=='verified' and not result['new_claim_candidates']
    assert len(ports.calls)==1 and ports.calls[0][1]['input']['task']=='missing_group_claims'
    assert f.CLAIM_DISCOVERY_RULES in ports.calls[0][1]['system']
    assert f.KNOWLEDGE_TEXT_RULES in ports.calls[0][1]['system']
    replay=Ports();assert stage.run(store,'S',rroot,root/'missing',replay,apply=True)['status']=='verified'
    assert replay.calls==[]


def test_new_claim_actual_identity_owners_anchors_and_replay(ready):
    store,root,rroot=ready;old={x:store.get_record_state('claims',x) for x in ['C','D']};old_e=store.get_record_state('evidence_steps','E')['payload']
    ports=Ports([candidate()]);result=stage.run(store,'S',rroot,root/'missing',ports,apply=True)
    assert result['status']=='applied' and len(result['new_claim_ids'])==1
    cid=result['new_claim_ids'][0];p=store.get_record_state('claims',cid)['payload']
    assert cid not in ['C','D'] and p['statement']==candidate()['statement'] and p['evidence_step_ids']==['E']
    assert p['occurrences'][0]['anchors'][0]['source_fragment_id']=='F'
    assert store.get_record_state('evidence_steps','E')['payload']['produced_claim_ids']==['C','D',cid]
    assert {k:v for k,v in store.get_record_state('evidence_steps','E')['payload'].items() if k not in {'produced_claim_ids','revision'}}=={k:v for k,v in old_e.items() if k not in {'produced_claim_ids','revision'}}
    assert old=={x:store.get_record_state('claims',x) for x in ['C','D']}
    checks=[r for role,r in ports.calls if role=='jev' and 'statement_fidelity' in r['questions']]
    assert len(checks)==1 and checks[0]['state']['claim']['statement']==candidate()['statement']
    assert not any('record_fidelity' in r.get('questions',{}) for role,r in ports.calls if role=='jev')
    replay=Ports();assert stage.run(store,'S',rroot,root/'missing',replay,apply=True)['status']=='applied';assert replay.calls==[]


def test_same_and_repeated_candidates_do_not_create_duplicates(ready):
    store,root,rroot=ready;text=store.get_record_state('claims','C')['payload']['statement']
    result=stage.run(store,'S',rroot,root/'missing',Ports([candidate(statement=text),candidate(),candidate()]),apply=True)
    assert result['status']=='applied' and len(result['new_claim_ids'])==1


def test_new_claim_link_discovery_is_complete_and_never_rejudged(ready):
    store,root,rroot=ready
    class CompleteLink(Ports):
        def repair(self,req):
            if req['input']['task']=='pair_claim':raise AssertionError('new Claim link rejudged by GPT')
            if req['input']['task']=='missing_claim_relations':
                self.calls.append(('gpt',req))
                return {'items':[{'job_id':j['job_id'],'pairs':[{
                    'a':'claims/C','b':next(iter(j['target_claims'])),'relation_type':'supports',
                    'direction':'a_to_b','reason':'既有判斷支持這個結論。'}],
                    'unresolved_keys':[],'reason':'完整給出直接連接。'} for j in req['input']['jobs']]}
            return super().repair(req)
        def judge(self,req):
            if all(req['state'].get(k,{}).get('record_type')=='claims' for k in ['A','B']):
                raise AssertionError('new Claim link rejudged by Jev')
            return super().judge(req)
    result=stage.run(store,'S',rroot,root/'complete-new-link',CompleteLink([candidate()]),apply=True)
    assert result['status']=='applied'
    assert any(p['from_id']=='C' and p['to_id']==result['new_claim_ids'][0]
               for k,p in result['updates'].items() if k.startswith('claim_relations/'))


@pytest.mark.parametrize('action',['retain_evidence','needs_material','needs_source','needs_update','represented'])
def test_candidate_dispositions_do_not_write_old_claims(ready,action):
    store,root,rroot=ready;old=store.get_record_state('claims','C')
    item=candidate(action=action,claim_id='C' if action in {'needs_update','represented'} else '')
    result=stage.run(store,'S',rroot,root/'missing',Ports([item]),apply=True)
    assert result['status']==('needs_attention' if action.startswith('needs_') else 'verified')
    assert not result['new_claim_candidates'] and store.get_record_state('claims','C')==old


@pytest.mark.parametrize('identity',['C','claims/C'])
def test_represented_claim_accepts_only_exact_provided_identity_forms(ready,identity):
    store,root,rroot=ready;old=store.get_record_state('claims','C');ports=Ports([candidate(action='represented',claim_id=identity)])
    result=stage.run(store,'S',rroot,root/'missing',ports,apply=True)
    assert result['status']=='verified' and store.get_record_state('claims','C')==old
    assert len(ports.calls)==1
    assert stage.run(store,'S',rroot,root/'missing',Ports(),apply=True)['status']=='verified'


@pytest.mark.parametrize('identity',['claims/UNKNOWN','observations/C','claims/claims/C'])
def test_represented_claim_key_does_not_bypass_identity_guard(ready,identity):
    store,root,rroot=ready
    with pytest.raises(ValueError,match='unprovided existing Claim'):
        stage.run(store,'S',rroot,root/'missing',Ports([candidate(action='represented',claim_id=identity)]),apply=True)
    assert not (root/'missing/commit-intent.json').exists()


@pytest.mark.parametrize('bad',[{'support_keys':['evidence_steps/X']},{'fragment_ids':['X']},{'answer_question_ids':['X']},{'claim_id':'MODEL-ALLOCATED'}])
def test_unprovided_material_and_model_allocated_identity_rejected(ready,bad):
    store,root,rroot=ready
    with pytest.raises(ValueError):stage.run(store,'S',rroot,root/'missing',Ports([candidate(**bad)]),apply=True)
    assert not (root/'missing'/'commit-intent.json').exists()


def test_creation_space_exhaustion_keeps_candidate_pending(ready):
    store,root,rroot=ready
    result=stage.run(store,'S',rroot,root/'missing',Ports([candidate()]),apply=True,max_new_claims=0)
    assert result['status']=='needs_attention' and result['pending'][0]['reason']=='creation_space_exceeded'


def test_inactive_identity_is_not_recreated(ready):
    store,root,rroot=ready
    store.apply_plan(store.plan_package({'package_id':'inactive','claims':[{'claim_id':'OLD','claim_type':'interpretive_judgment','statement':candidate()['statement'],
        'evidence_step_ids':['E'],'occurrences':[{'source_id':'S','anchors':[]}]}]}))
    store.apply_plan(store.plan_package({'package_id':'retire-old'},retiring_keys=[('claims','OLD')]))
    result=stage.run(store,'S',rroot,root/'missing',Ports([candidate()]),apply=True)
    assert result['status']=='needs_attention' and result['pending'][0]['reason']=='inactive_identity_requires_handoff'
    assert not result['new_claim_candidates']


def test_input_drift_rejects_before_models(ready):
    store,root,rroot=ready;p=store.get_record_state('evidence_steps','E')['payload'];p['statement']='另有意思。'
    store.apply_plan(store.plan_package({'package_id':'changed-material','evidence_steps':[p]}));ports=Ports()
    with pytest.raises(ValueError,match='input changed'):stage.run(store,'S',rroot,root/'missing',ports,apply=True)
    assert ports.calls==[]


def test_new_statement_doubts_only_review_new_claim(ready):
    store,root,rroot=ready;ports=Ports([candidate()],choices={'statement_fidelity':'changed'})
    result=stage.run(store,'S',rroot,root/'missing',ports,apply=True)
    assert result['status']=='applied'
    fidelity=[r for role,r in ports.calls if role=='gpt' and r['input']['task']=='claim_fidelity']
    assert len(fidelity)==1 and all(j['job_id'].startswith('claims/new:') for j in fidelity[0]['input']['jobs'])


def test_rejected_last_support_blocks_creation(ready):
    store,root,rroot=ready;result=stage.run(store,'S',rroot,root/'missing',Ports([candidate()],choices={'relation':'none'}),apply=True)
    assert result['status']=='needs_attention' and any(x['reason']=='new_claim_has_no_final_support' for x in result['pending'])
    assert not (root/'missing'/'commit-intent.json').exists()


def test_actual_commit_crash_reuses_identity_and_answers(ready,monkeypatch):
    store,root,rroot=ready;apply=store.apply_plan
    def crash(*a,**kw):apply(*a,**kw);raise RuntimeError('after SQL')
    monkeypatch.setattr(store,'apply_plan',crash)
    with pytest.raises(RuntimeError,match='after SQL'):stage.run(store,'S',rroot,root/'missing',Ports([candidate()]),apply=True)
    monkeypatch.setattr(store,'apply_plan',lambda *a,**kw:pytest.fail('duplicate SQL'))
    ports=Ports();result=stage.run(store,'S',rroot,root/'missing',ports,apply=True)
    assert result['status']=='applied' and len(result['new_claim_ids'])==1 and ports.calls==[]


def test_question_answer_link_preserves_existing_answers(ready):
    store,root,rroot=ready
    store.apply_plan(store.plan_package({'package_id':'question','questions':[{'question_id':'Q','text':'真正相信會有甚麼表現？',
        'source_fragment_id':'F','answer_claim_ids':['C'],'answer_state':'answered'}]}))
    ports=Ports([candidate(answer_question_ids=['Q'])]);result=stage.run(store,'S',rroot,root/'missing',ports,apply=True)
    assert result['status']=='applied'
    assert store.get_record_state('questions','Q')['payload']['answer_claim_ids']==['C',result['new_claim_ids'][0]]


@pytest.mark.parametrize('valid',[True,False])
def test_claim_own_quote_can_differ_from_support_but_must_be_source_bound(ready,valid):
    store,root,rroot=ready;p=deepcopy(store.get_record_state('source_fragments','F')['payload'])
    p.update(fragment_id='OWN',verbatim_excerpt='你不知道神的旨意，怎麼遵行？' if valid else '原文沒有說這一句。')
    p['verbatim_excerpt_sha256']=hashlib.sha256(p['verbatim_excerpt'].encode()).hexdigest()
    store.apply_plan(store.plan_package({'package_id':'own-statement-quote','source_fragments':[p]}))
    ports=Ports([candidate(fragment_ids=['OWN'])])
    if not valid:
        with pytest.raises(ValueError,match='source binding'):stage.run(store,'S',rroot,root/'missing',ports,apply=True)
        assert not (root/'missing'/'commit-intent.json').exists()
        return
    result=stage.run(store,'S',rroot,root/'missing',ports,apply=True)
    assert result['status']=='applied'
    claim=store.get_record_state('claims',result['new_claim_ids'][0])['payload']
    assert {a['source_fragment_id'] for a in claim['occurrences'][0]['anchors']}=={'F','OWN'}
    assert store.get_record_state('evidence_steps','E')['payload']['source_fragment_ids']==['F']


def test_final_scripture_repair_does_not_demand_statement_pair_handoff(sample,tmp_path):
    store,root,_=sample;ev=store.get_record_state('evidence_steps','E')['payload'];ev['statement']='沒有聽從祂的話，顯明沒有真正相信祂。'
    store.apply_plan(store.plan_package({'package_id':'material','evidence_steps':[ev],
        'observations':[{'observation_id':'O','statement':'沒有聽從祂的話。','source_fragment_id':'F'}]}))
    records,sources=f.load_source(store,'S');plan={'source_id':'S','source_body_sha256':sources['S']['body_sha256'],'groups':[{
        'group_id':'G','title':'one','claim_ids':['C'],'paragraph_keys':['S0001'],
        'lower_record_keys':['evidence_steps/E','observations/O'],'reference_record_keys':[]}]}
    eroot=root/'evidence';sroot=root/'statements';aroot=root/'arguments';rroot=root/'relations'
    assert eo.run(store,'S',plan,eroot,EvidencePorts(),saved_inventory(tmp_path/'prior',sources),apply=True)['status']=='completed'
    assert statements.run(store,'S',['C'],eroot,sroot,BasePorts(),apply=True)['status']=='verified'
    assert arguments.run(store,'S',plan,'G',eroot,sroot,aroot,ArgumentPorts(),apply=True)['status']=='verified'
    assert relations.run(store,'S',aroot,rroot,RelationPorts(),apply=True)['status']=='verified'
    class FinalScripture(Ports):
        def repair(self,request):
            raw=super().repair(request)
            if request['input']['task']=='pair_attachment':
                for item in raw['items']:item.update(action='remove',reason='同一觀察已由Evidence承載，不重複連接。')
            if request['input']['task']=='claim_scripture':
                for item in raw['items']:item.update(action='repair',scripture_refs=[],reason='移除未由本主張材料支持的經文標籤。')
            return raw
    ports=FinalScripture([candidate(support_keys=['evidence_steps/E','observations/O'],scripture_refs=['Matt 1:1'])],
        choices={'scripture':lambda req:'missing' if req['state'].get('claim',{}).get('statement')==candidate()['statement'] and len(req['state'].get('evidence',[]))==1 else 'complete'})
    result=stage.run(store,'S',rroot,root/'missing',ports,apply=True)
    assert result['status']=='applied' and not result['pending']
    post=store.get_record_state('claims',result['new_claim_ids'][0])['payload']
    assert post['statement']==candidate()['statement'] and post['scripture_refs']==[]
    assert len([r for role,r in ports.calls if role=='gpt' and r['input']['task']=='pair_attachment'])==1
