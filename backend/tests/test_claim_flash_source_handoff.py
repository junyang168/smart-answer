from copy import deepcopy
import hashlib
import pytest

from backend.pipeline import claim_flash_source as source, evidence_refresh as e
from backend.tests.test_evidence_refresh import sample
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres


class Ports:
    identity={'gpt':{'backend':'test','model':'test','effort':'medium'},'jev':e.MODEL}
    def __init__(self,quote='你沒有聽從祂的話，你根本沒信。',action='bind'):
        self.calls=[];self.quote=quote;self.action=action
    def judge(self,request):
        self.calls.append('jev')
        return {'model':e.MODEL,'answers':{key:{'type':'choice','choice':'S0001',
            'probabilities':{c:float(c=='S0001') for c in spec['criteria']}}
            for key,spec in request['questions'].items()}}
    def repair(self,request):
        self.calls.append('gpt')
        assert request['input']['task']=='source_binding_completion'
        return {'items':[{'job_id':job['job_id'],'action':self.action,
            'quotes':[{'paragraph_key':'S0001','quote':self.quote}],
            'reason':'補足原文指代；不修改摘要。'} for job in request['input']['jobs']]}


@pytest.mark.parametrize('old_quote',['你不知道神的旨意，怎麼遵行？','你'])
def test_source_handoff_commits_only_complete_bindings_and_reuses_exact_answer(sample,old_quote):
    store,root,_=sample
    fragment=store.get_record_state('source_fragments','F')['payload']
    fragment['verbatim_excerpt']=old_quote
    fragment['verbatim_excerpt_sha256']=hashlib.sha256(fragment['verbatim_excerpt'].encode()).hexdigest()
    store.apply_plan(store.plan_package({'package_id':'short-fragment','source_fragments':[fragment]}))
    fragment=store.get_record_state('source_fragments','F')['payload']
    before=deepcopy(store.get_record_state('evidence_steps','E')['payload'])
    findings=[{'object_key':'evidence_steps/E','layer':'source','reason':'缺少後半句原文。'}]
    ports=Ports();result=source.repair_bindings(store,'S',findings,root/'handoff',ports,apply=True)
    assert result['status']=='applied' and ports.calls==['jev','gpt']
    current=store.get_record_state('evidence_steps','E')['payload']
    assert {k:v for k,v in current.items() if k not in {'revision','source_fragment_id','source_fragment_ids'}} == {
        k:v for k,v in before.items() if k not in {'revision','source_fragment_id','source_fragment_ids'}}
    assert 'F' in current['source_fragment_ids'] and len(current['source_fragment_ids'])==2
    assert store.get_record_state('source_fragments','F')['payload']==fragment
    replay=Ports();again=source.repair_bindings(store,'S',findings,root/'handoff',replay,apply=True)
    assert again['status']=='applied' and replay.calls==[]


@pytest.mark.parametrize('action,quote',[('needs_source','你沒有聽從祂的話，你根本沒信。'),('bind','原文沒有這句話。')])
def test_source_handoff_does_not_write_missing_or_invented_quote(sample,action,quote):
    store,root,_=sample;before=deepcopy(store.get_record_state('evidence_steps','E'))
    findings=[{'object_key':'evidence_steps/E','layer':'source','reason':'需要補足原文。'}]
    if action=='bind':
        with pytest.raises(ValueError,match='unique literal'):
            source.repair_bindings(store,'S',findings,root/'handoff',Ports(quote,action),apply=True)
    else:
        assert source.repair_bindings(store,'S',findings,root/'handoff',Ports(quote,action),apply=True)['status']=='needs_attention'
    assert store.get_record_state('evidence_steps','E')==before


def test_uncovered_saved_inventory_unit_is_compiled_without_model_or_upper_write(sample):
    from backend.pipeline import claim_flash_common as f, claim_flash_source_fill as fill
    from backend.tests.test_claim_flash_evidence import saved_inventory
    store,root,_=sample
    fragment=store.get_record_state('source_fragments','F')['payload']
    fragment['verbatim_excerpt']='你不知道神的旨意，怎麼遵行？'
    fragment['verbatim_excerpt_sha256']=hashlib.sha256(fragment['verbatim_excerpt'].encode()).hexdigest()
    store.apply_plan(store.plan_package({'package_id':'short-inventory-span','source_fragments':[fragment]}))
    records,sources=f.load_source(store,'S')
    unit={'paragraph_key':'S0001','quote':'你沒有聽從祂的話，你根本沒信。','statement':'沒有聽從顯明沒有真正相信。'}
    previous=saved_inventory(root/'prior',sources,units=[unit])
    handoff={'layer':'source','reason':'important_unit_not_covered_by_verified_fragments','unit':unit}
    result=fill.fill_inventory_handoff(store,'S',[handoff],previous,root/'fill',apply=True)
    assert result['status']=='applied' and result['model_calls']==0 and len(result['new_fragments'])==1
    assert result['updates']=={}
    for key,row in records.items():
        assert store.get_record_state(*key.split('/',1))==row
    with pytest.raises(ValueError,match='exact saved'):
        fill.fill_inventory_handoff(store,'S',[{**handoff,'unit':{**unit,'statement':'自行改過的意思。'}}],previous,root/'bad',apply=True)
