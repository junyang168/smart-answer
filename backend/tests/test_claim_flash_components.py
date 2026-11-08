"""Source-blind decomposition and safe routing, using the real lower SQL flow."""
from copy import deepcopy
import pytest

from backend.pipeline import claim_flash_components as parts, claim_flash_lower as lower
from backend.pipeline import claim_flash_common as f
from backend.tests.test_claim_update_flash import Ports
from backend.tests.test_evidence_refresh import sample
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres


def test_decomposition_is_source_blind_bounded_cached_and_shared_only_by_text(tmp_path):
    texts={str(i):f'教授提出判斷{i}。' for i in range(17)}
    texts['duplicate']=texts['0']
    ports=Ports();result=parts.decompose(tmp_path,ports,texts)
    requests=[q for role,q in ports.calls if role=='gpt']
    assert len(requests)==2 and set(result)==set(texts)
    seen=[]
    for req in requests:
        assert f.SEMANTIC_REVIEW not in req['system']
        assert req['input']['task']=='lower_decomposition'
        assert len(req['input']['jobs'])<=4
        for bundle in req['input']['jobs']:
            assert len(bundle['records'])<=4
            for record in bundle['records']:
                assert set(record)=={'object_key','statement'}
                seen.append(record['statement'])
    assert len(seen)==len(set(seen))==17
    assert result['duplicate']==result['0']
    n=len(ports.calls)
    assert parts.decompose(tmp_path,ports,texts)==result and len(ports.calls)==n


@pytest.mark.parametrize('key,value',[('component_0','unsupported'),('component_0','uncertain'),
                                    ('decomposition_fidelity','yes'),('attribution_fidelity','yes')])
def test_component_or_role_problem_routes_to_gpt_despite_whole_record_no_issue(sample,key,value):
    store,root,_=sample;ports=Ports(choices={key:value})
    claim=store.get_record_state('claims','C')
    result=lower.run(store,'S',root/'lower',ports,apply=True)
    assert result['applied']
    req=next(q for role,q in ports.calls if role=='gpt' and q['input']['task']=='lower_repair')
    assert req['input']['jobs'][0]['verdict']['record_fidelity']=='no'
    assert req['input']['jobs'][0]['verdict'][key]==value
    assert store.get_record_state('evidence_steps','E')['payload']['statement']=='沒有聽從祂的話，顯明沒有真正相信祂。'
    assert store.get_record_state('claims','C')==claim


def test_conditional_component_role_and_whole_original_material_reach_jev(tmp_path):
    components=[{'role':'conditional_relation','text':'若不知道旨意，就不能遵行。'},
                {'role':'embedded_description','text':'不知道旨意'}]
    material={'original_text_excerpts':['你不知道神的旨意，怎麼遵行？','你沒有聽從祂的話，你根本沒信。']}
    ports=Ports()
    answers=parts.check(tmp_path,ports,'evidence_steps',{'statement':'若不知道旨意，就不能遵行。'},material,components)
    req=ports.calls[0][1]
    assert req['state']['components']['1']['role']=='embedded_description'
    assert req['state']['original_text_excerpts']==material['original_text_excerpts']
    assert '不要求證明行為已發生' in req['questions']['component_1']['instructions']
    assert not parts.needs_disposition(answers)


@pytest.mark.parametrize('defect',['missing','duplicate','scope'])
def test_missing_duplicate_or_injected_split_records_rejected_before_judging(tmp_path,defect):
    class Broken(Ports):
        def repair(self,req):
            reply=super().repair(req)
            items=reply['items'][0]['items']
            if defect=='missing':items.clear()
            elif defect=='duplicate':items.append(deepcopy(items[0]))
            else:items[0]['object_key']='unprovided'
            return reply
    ports=Broken()
    with pytest.raises(ValueError,match='exactly once|scope|at least 1'):
        parts.decompose(tmp_path,ports,{'E':'教授的判斷。'})
    assert not any(role=='jev' for role,q in ports.calls)


def test_oversized_decomposition_and_component_material_fail_before_calls(tmp_path,monkeypatch):
    ports=Ports();monkeypatch.setattr(f,'GPT_BYTES',500)
    with pytest.raises(ValueError,match='exceeds'):
        parts.decompose(tmp_path,ports,{'E':'字'*1000})
    assert not ports.calls
    monkeypatch.setattr(f,'JEV_BYTES',500)
    with pytest.raises(ValueError,match='exceeds'):
        parts.check(tmp_path,ports,'evidence_steps',{'statement':'一項判斷。'},
                    {'original_text_excerpts':['原文'*1000]},[{'role':'judgment','text':'一項判斷。'}])
    assert not ports.calls
