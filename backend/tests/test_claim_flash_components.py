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


@pytest.mark.parametrize('mistyped',[False,True])
def test_decomposition_recovers_only_missing_record_and_keeps_original_answer(tmp_path,mistyped):
    import json
    class Once(Ports):
        def repair(self,req):
            value=super().repair(req)
            if len([q for role,q in self.calls if role=='gpt'])==1:
                if mistyped:value['items'][0]['items'][0]['object_key']='truncated-id'
                else:value['items'][0]['items'].pop(0)
            return value
    ports=Once();texts={'A':'第一個判斷。','B':'第二個判斷。','C':'第三個判斷。'}
    result=parts.decompose(tmp_path,ports,texts)
    requests=[q for role,q in ports.calls if role=='gpt']
    assert len(requests)==2 and set(result)==set(texts)
    assert requests[1]['input']['jobs'][0]['records']==[{'object_key':'A','statement':texts['A']}]
    assert requests[1]['schema']['$defs']['Parts']['properties']['object_key']['enum']==['A']
    original=next((tmp_path/'components/gpt').glob('*/response.json'))
    raw=json.loads(original.read_text())
    assert ('truncated-id' in str(raw))==mistyped
    assert parts.decompose(tmp_path,ports,texts)==result
    assert len([q for role,q in ports.calls if role=='gpt'])==2


@pytest.mark.parametrize('key,value',[('component_0','unsupported'),('component_0','uncertain'),
                                    ('decomposition_fidelity','changed'),('speaker_classification','audience')])
def test_component_or_role_problem_routes_to_gpt_despite_whole_record_no_issue(sample,key,value):
    store,root,_=sample;ports=Ports(choices={key:value})
    claim=store.get_record_state('claims','C')
    result=lower.run(store,'S',root/'lower',ports,apply=True)
    assert result['applied']
    req=next(q for role,q in ports.calls if role=='gpt' and q['input']['task']=='lower_repair')
    assert req['input']['jobs'][0]['verdict']['record_fidelity']=='faithful'
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
    assert '不聲稱特定行為已發生' in req['questions']['component_1']['instructions']
    assert components[1]['text'] in req['questions']['component_1']['instructions']
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


@pytest.mark.parametrize('old,chosen,expected',[
    ('opposed','asserted','different'),('asserted','asserted','matched'),
    ('opposed','opposed','matched'),('asserted','uncertain','uncertain'),
    ('quoted','quoted','matched'),('unknown_old_value','asserted','different')])
def test_stance_is_independently_classified_then_compared_by_program(tmp_path,old,chosen,expected):
    record={'statement':'不能由有人未得救推論耶穌白死。','stance':old,'speaker':'professor',
            'discourse_role':'舊有角色分類','attribution':'舊有歸屬分類'}
    original=deepcopy(record)
    ports=Ports(choices={'stance_classification':chosen})
    answers=parts.check(tmp_path,ports,'evidence_steps',record,{'original_text_excerpts':['我說：沒有。']},
                        [{'role':'judgment','text':record['statement']}])
    request=ports.calls[0][1]
    assert not {'stance','speaker','attribution','discourse_role'}&set(request['state']['record'])
    assert set(request['questions']['stance_classification']['criteria'])=={'asserted','opposed','questioned','quoted','uncertain'}
    assert 'speaker_classification' in request['questions']
    assert record==original and answers['stance_consistency']==expected
    assert parts.needs_disposition(answers)==(expected!='matched')


def test_mixed_speaker_and_unknown_questioner_cannot_pass_as_matching(tmp_path):
    ports=Ports(choices={'speaker_classification':'mixed','questioner_classification':'uncertain'})
    answers=parts.check(tmp_path,ports,'questions',{'text':'誰？','questioner':'professor','speaker':'professor'},
                        {'original_text_excerpts':['誰？']},None)
    assert answers['speaker_consistency']==answers['questioner_consistency']=='uncertain'
    assert parts.needs_disposition(answers)
    assert not {'questioner','speaker'}&set(ports.calls[0][1]['state']['record'])


def test_legacy_yes_no_stance_reply_is_rejected_not_mapped_to_approval(tmp_path):
    ports=Ports(choices={'stance_classification':'no'})
    with pytest.raises(ValueError):
        parts.check(tmp_path,ports,'evidence_steps',{'statement':'教授的判斷。','stance':'asserted'},
                    {'original_text_excerpts':['教授的判斷。']},None)


def test_listener_stance_uses_listener_as_subject_without_leaking_speaker_answer(tmp_path):
    record={'statement':'聽眾表示自己先前沒有想到。','speaker':'audience','stance':'asserted'}
    ports=Ports(choices={'speaker_classification':'audience'})
    result=parts.check(tmp_path,ports,'evidence_steps',record,{'original_text_excerpts':['聽眾：我沒有想到。']},None)
    request=ports.calls[0][1]
    assert '聽眾對這個完整判斷' in request['questions']['stance_classification']['instructions']
    speaker_question=request['questions']['speaker_classification']
    assert speaker_question['instructions'].startswith('這條 Evidence 摘要主要記錄哪一方的發言？')
    assert '教授的比較或轉述仍選此項' in speaker_question['criteria']['professor']
    assert '直接引語為主要內容' in speaker_question['criteria']['quoted_source']
    assert 'speaker' not in request['state']['record']
    assert not parts.needs_disposition(result)


def test_wrong_speaker_routes_even_when_stance_for_the_recorded_actor_matches(tmp_path):
    ports=Ports(choices={'speaker_classification':'professor','stance_classification':'asserted'})
    result=parts.check(tmp_path,ports,'evidence_steps',{'statement':'不能這樣推論。','speaker':'audience','stance':'asserted'},
                        {'original_text_excerpts':['教授：不能這樣推論。']},None)
    assert result['speaker_consistency']=='different' and result['stance_consistency']=='matched'
    assert parts.needs_disposition(result)
