"""Program-first citation attachment; Jev only for semantic location, no GPT."""
from copy import deepcopy
import hashlib
import pytest
from backend.pipeline import claim_flash_scripture as stage, claim_flash_common as f
from backend.tests.test_evidence_refresh import sample
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres

class Ports:
    identity={'jev':'test'}
    def __init__(self,selection=()):self.calls=[];self.selection=iter(selection)
    def judge(self,req):
        self.calls.append(req);choice=next(self.selection,'none')
        from backend.pipeline import evidence_refresh as e
        return {'model':e.MODEL,'answers':{'match':{'type':'choice','choice':choice,
            'probabilities':{k:float(k==choice) for k in req['questions']['match']['criteria']}}}}
    def repair(self,req):pytest.fail('citation binding must not call GPT')


def job(quote='你沒有聽從祂的話，你根本沒信。',reference='羅馬書13:14'):
    # Synthetic fixture tests location, not whether this sample quote is Romans.
    return {'job_id':'citation','reference':reference,'quote':quote,'paragraph_key':'S0001','target_keys':['evidence_steps/E']}

@pytest.mark.parametrize('text,osis',[
    ('太5–7',['Matt.5-Matt.7']),
    ('罗马书十三章十四节',['Rom.13.14']),
    ('彼后第一章第十六至十八节',['2Pet.1.16-2Pet.1.18']),
    ('馬太福音五至七章',['Matt.5-Matt.7']),
    ('太5:17、21、23-24',['Matt.5.17','Matt.5.21','Matt.5.23-Matt.5.24']),
    ('羅１３：１４/約１：１４',['Rom.13.14','John.1.14']),
    ('太5:17 加上一個未知出處',[]),
    ('馬太福音五章十七至四十八節',['Matt.5.17-Matt.5.48']),
    ('太五章十七節至六章十一節',['Matt.5.17-Matt.6.11']),
    ('太五章十七節；第十八節',['Matt.5.17','Matt.5.18']),
    ('太五章十七節；六章',['Matt.5.17','Matt.6']),
    ('罗13:14',['Rom.13.14']),('羅馬書十三章十四節',['Rom.13.14']),
    ('Rom.13.14',['Rom.13.14']),('太5:17–48；6:1–7:11',['Matt.5.17-Matt.5.48','Matt.6.1-Matt.7.11']),
    ('太5:17；18',['Matt.5.17','Matt.5.18']),('林前15:22-24',['1Cor.15.22-1Cor.15.24']),
    ('羅馬書13:14；約翰福音1:14',['Rom.13.14','John.1.14']),
    ('關於不為肉體預備的經文（未明說書卷章節）',[])])
def test_existing_reference_parsers_cover_forms(text,osis):
    assert [r.osis for r in stage.references(text)]==osis

@pytest.mark.parametrize('quote',['你沒有聽從祂的話，你根本沒信。','你没有听从祂的话，你根本没信。','你沒有聽從祂的話，\n你根本沒信。','你沒有聽從祂的話'])
def test_program_match_only_appends_ref_and_replays_without_calls(sample,quote):
    store,root,_=sample;before=store.get_record_state('evidence_steps','E')['payload'];ports=Ports()
    result=stage.run(store,'S',[job(quote)],root/'fix',ports,apply=True)
    assert result['status']=='applied' and not ports.calls and not result['new_fragments']
    after=store.get_record_state('evidence_steps','E')['payload']
    assert {k:v for k,v in after.items() if k not in {'scripture_refs','revision'}}=={k:v for k,v in before.items() if k not in {'scripture_refs','revision'}}
    assert after['scripture_refs']==['羅馬書 13:14']
    assert stage.run(store,'S',[job(quote)],root/'fix',Ports(),apply=True)['status']=='applied'
    noop=stage.run(store,'S',[job(quote)],root/'again',Ports(),apply=True)
    assert not noop['updates'] and not noop['new_fragments']


def test_jev_fallback_locates_paraphrase_and_never_rewrites_source(sample):
    store,root,_=sample;before=store.get_record_state('source_fragments','F');ports=Ports(['F0','none'])
    result=stage.run(store,'S',[job('不遵行祂的話，顯出尚未真正信祂。')],root/'semantic',ports,apply=True)
    assert result['status']=='applied' and len(ports.calls)==1
    assert result['outcomes'][0]['basis']=='jev_fragment'
    assert store.get_record_state('source_fragments','F')==before


def test_missing_fragment_compiles_literal_source_without_GPT(sample):
    store,root,_=sample;p=store.get_record_state('source_fragments','F')['payload']
    p['verbatim_excerpt']='你不知道神的旨意，怎麼遵行？';p['verbatim_excerpt_sha256']=hashlib.sha256(p['verbatim_excerpt'].encode()).hexdigest()
    store.apply_plan(store.plan_package({'package_id':'short','source_fragments':[p]}))
    ports=Ports(['none']);result=stage.run(store,'S',[job()],root/'source',ports,apply=True)
    assert result['status']=='applied' and result['outcomes'][0]['basis']=='program_source'
    assert result['new_fragments'] and 'F' in store.get_record_state('evidence_steps','E')['payload']['source_fragment_ids']

@pytest.mark.parametrize('reference,quote',[
    ('未明說章節','你沒有聽從祂的話，你根本沒信。'),('羅馬書13:14','完全不存在的引文。')])
def test_unknown_reference_or_absent_source_never_fills(sample,reference,quote):
    store,root,_=sample;before=store.get_record_state('evidence_steps','E');ports=Ports()
    result=stage.run(store,'S',[job(quote,reference)],root/'missing',ports,apply=True)
    assert result['status']=='needs_attention' and not result['updates'] and not result['new_fragments']
    assert store.get_record_state('evidence_steps','E')==before
    if reference=='未明說章節':assert not ports.calls

@pytest.mark.parametrize('status',['human_hold','human_approved'])
def test_actual_holds_remain_protected(sample,status):
    store,root,_=sample;store.record_review('evidence_steps','E',decision=status,reviewer_kind='human');ports=Ports()
    with pytest.raises(ValueError,match='protected'):stage.run(store,'S',[job()],root/'hold',ports,apply=True)
    assert not ports.calls


def test_only_confirmed_existing_owner_findings_become_write_jobs():
    base={'verdict':'missing_scripture_reference','reference':'羅馬書13:14','quote':'引用原話。',
          'paragraph_key':'S0001','existing_record_keys':['claims/C','evidence_steps/E']}
    assert stage.coverage_jobs([base])[0]['target_keys']==['evidence_steps/E']
    assert not stage.coverage_jobs([{**base,'verdict':'needs_context'}])
    assert not stage.coverage_jobs([{**base,'reference':'未明說章節'}])
    assert not stage.coverage_jobs([{**base,'existing_record_keys':['claims/C']}])


def test_existing_alias_and_other_references_are_preserved(sample):
    store,root,_=sample;p=store.get_record_state('evidence_steps','E')['payload'];p['scripture_refs']=['罗13:14','路4:4']
    store.apply_plan(store.plan_package({'package_id':'refs','evidence_steps':[p]}))
    result=stage.run(store,'S',[job()],root/'same',Ports(),apply=True)
    assert not result['updates']
    assert store.get_record_state('evidence_steps','E')['payload']['scripture_refs']==['罗13:14','路4:4']


def test_existing_unowned_fragment_is_attached_without_duplicate_creation(sample):
    store,root,_=sample;p=store.get_record_state('source_fragments','F')['payload'];fresh=deepcopy(p)
    p['verbatim_excerpt']='你不知道神的旨意，怎麼遵行？';p['verbatim_excerpt_sha256']=hashlib.sha256(p['verbatim_excerpt'].encode()).hexdigest()
    fresh['fragment_id']='F2'
    store.apply_plan(store.plan_package({'package_id':'reuse','source_fragments':[p,fresh]}))
    ports=Ports();result=stage.run(store,'S',[job()],root/'attach',ports,apply=True)
    assert result['status']=='applied' and not ports.calls and not result['new_fragments']
    assert store.get_record_state('evidence_steps','E')['payload']['source_fragment_ids']==['F','F2']


def test_deleted_fragment_is_not_resurrected_from_a_literal_match(sample):
    store,root,_=sample;p=store.get_record_state('source_fragments','F')['payload'];p['anchor_state']='source_deleted'
    store.apply_plan(store.plan_package({'package_id':'deleted','source_fragments':[p]}))
    before=store.get_record_state('evidence_steps','E');ports=Ports()
    with pytest.raises(ValueError):stage.run(store,'S',[job()],root/'refuse',ports,apply=True)
    assert not ports.calls and store.get_record_state('evidence_steps','E')==before


def test_uncertain_jev_does_not_become_a_match(sample):
    store,root,_=sample;before=store.get_record_state('evidence_steps','E');ports=Ports(['uncertain','uncertain'])
    result=stage.run(store,'S',[job('這段引句在此處無法確認。')],root/'uncertain',ports,apply=True)
    assert result['status']=='needs_attention' and not result['updates'] and not result['new_fragments']
    assert store.get_record_state('evidence_steps','E')==before
