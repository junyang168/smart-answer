import json
from copy import deepcopy
from pathlib import Path

import pytest

from backend.api.canonical_repository.cvp_claim_input import compile_input, compile_positions

POLICY=json.loads((Path(__file__).resolve().parents[1]/'pipeline/policies/wang_cvp_claim_input_v1.json').read_text())


def text_input():
    return compile_input([{'claim_id':'C1','statement':'人子👨‍👩‍👦，e\u0301未到时候。',
        'conditions':['除非父赐下'],'limitations':{'对象':'门徒'},'attribution':'professor',
        'review_note':'old review metadata must not become teaching'}],POLICY)


def components(doc):
    return [{'claim_ref':unit['claim_ref'],'spans':[{
        'text_ref':unit['text_ref'],'start_char':0,'end_char':len(unit['text'])}]}
        for unit in doc['text_units']]


def test_raw_unicode_offsets_include_qualifiers_without_normalization_or_copying():
    doc=text_input();proposal=components(doc)
    compiled=compile_positions(doc,proposal)
    assert len(doc['text_units'])==3
    assert doc['claims'][0]['content']['attribution']=='professor'
    assert 'review_note' not in doc['claims'][0]['content']
    assert compiled['components'][0]['spans'][0]['exact_text']=='人子👨‍👩‍👦，e\u0301未到时候。'
    assert compiled['semantic_coverage_approved'] is False
    assert 'exact_text' not in proposal[0]['spans'][0]


@pytest.mark.parametrize('kind',['gap','overlap','missing_qualifier','foreign','cross_claim','utf8','negative','bool','copy'])
def test_positional_errors_cannot_silently_lose_or_reassign_teaching(kind):
    doc=text_input();cs=components(doc)
    if kind=='gap':cs[0]['spans'][0]['end_char']-=1
    if kind=='overlap':cs.append(deepcopy(cs[0]))
    if kind=='missing_qualifier':cs.pop()
    if kind=='foreign':cs[0]['spans'][0]['text_ref']='t999'
    if kind=='cross_claim':cs[0]['claim_ref']='c999'
    if kind=='utf8':cs[0]['spans'][0]['end_char']=len(doc['text_units'][0]['text'].encode())
    if kind=='negative':cs[0]['spans'][0]['start_char']=-1
    if kind=='bool':cs[0]['spans'][0]['start_char']=False
    if kind=='copy':cs[0]['spans'][0]['exact_text']=doc['text_units'][0]['text']
    with pytest.raises(ValueError):compile_positions(doc,cs)


def test_unregistered_optional_fields_fail_before_a_new_qualifier_is_dropped():
    claim={'claim_id':'C1','statement':'判断','new_limitation':'只有父知道'}
    with pytest.raises(ValueError,match='unregistered'):compile_input([claim],POLICY)
    updated=deepcopy(POLICY);updated['policy_id']='bound-v2';updated['position_fields'].append('new_limitation')
    assert len(compile_input([claim],updated)['text_units'])==2


def test_multiple_components_may_split_raw_text_but_cannot_overlap():
    doc=compile_input([{'claim_id':'C1','statement':'甲乙丙丁'}],POLICY)
    cs=[{'claim_ref':'c1','spans':[{'text_ref':'t1','start_char':a,'end_char':b}]} for a,b in [(0,2),(2,4)]]
    assert [c['spans'][0]['exact_text'] for c in compile_positions(doc,cs)['components']]==['甲乙','丙丁']


def test_program_references_can_match_common_material_without_promoting_readonly_members():
    doc=compile_input([{'claim_id':'C-target','statement':'判断'}],POLICY,claim_refs={'C-target':'c27'})
    assert doc['text_units'][0]['claim_ref']=='c27'
    with pytest.raises(ValueError):compile_input([{'claim_id':'C1','statement':'判断'}],POLICY,claim_refs={})
