from copy import deepcopy

import pytest

from backend.pipeline.claim_anchor_roles import source_roles
from backend.pipeline import claim_refresh as c
from backend.tests.test_claim_flash_source import fixture


@pytest.mark.parametrize('label',['**聽眾：**','**聽眾**：','听众问:'])
def test_question_excerpt_uses_explicit_source_turn_not_summary_speaker(label):
    result=source_roles({'verbatim_excerpt':'文獻裡有這個字嗎？'},label+'文獻裡有這個字嗎？')
    assert result=={'speaker':'audience','stance':None,'assertive':False}


def test_one_paragraph_can_contain_both_question_and_professor_response():
    text='**教授：**對。\n\n**聽眾：**是普通希臘文嗎？'
    assert source_roles({'verbatim_excerpt':text},text)=={'speaker':'mixed','stance':None,'assertive':False}
    assert source_roles({'verbatim_excerpt':'對。'},text)=={'speaker':'professor','stance':None,'assertive':None}
    assert source_roles({'verbatim_excerpt':'是普通希臘文嗎？'},text)['speaker']=='audience'


def test_professor_question_or_unlabelled_text_is_not_automatically_asserted():
    assert source_roles({'verbatim_excerpt':'為什麼？'},'教授：為什麼？')=={
        'speaker':'professor','stance':None,'assertive':None}
    assert source_roles({'verbatim_excerpt':'原話。'},'原話。')['assertive'] is None
    text='聽眾：對。\n教授：對。'
    assert source_roles({'verbatim_excerpt':'對。'},text)['speaker'] is None


def test_existing_fragment_local_attribution_remains_but_conflicting_turn_does_not():
    fragment={'verbatim_excerpt':'原話。','speaker':'professor','stance':'asserted'}
    assert source_roles(fragment,'原話。')=={'speaker':'professor','stance':'asserted','assertive':True}
    assert source_roles(fragment,'聽眾：原話。')=={'speaker':'audience','stance':None,'assertive':False}


def test_creation_and_update_shared_anchor_builder_corrects_old_labels_only(tmp_path):
    text='**聽眾：**文獻裡有這個字嗎？'
    records,_=fixture(tmp_path,[text],[('S0001','文獻裡有這個字嗎？')])
    records['evidence_steps/E']['payload'].update(speaker='professor',stance='asserted')
    before=deepcopy(records)
    old={'occurrences':[{'source_id':'S','anchors':[{'evidence_id':'E',
        'source_fragment_id':'F0','speaker':'professor','stance':'asserted','assertive':True,
        'proposed_highlight':{'text':'文獻裡有這個字嗎？','status':'proposed'}}]}]}
    result=c.refresh_occurrences(old,['E'],records,'S')
    anchor=result[0]['anchors'][0]
    assert (anchor['speaker'],anchor['stance'],anchor['assertive'])==('audience',None,False)
    assert anchor['proposed_highlight']==old['occurrences'][0]['anchors'][0]['proposed_highlight']
    assert records==before


def test_unknown_new_role_does_not_overwrite_existing_source_annotation(tmp_path):
    records,_=fixture(tmp_path,['原話。'],[('S0001','原話。')])
    records['evidence_steps/E']['payload'].update(speaker='professor',stance='asserted')
    assert c.anchors(['E'],records,'S')[0]['anchors'][0]['assertive'] is None
    old={'occurrences':[{'source_id':'S','anchors':[{'evidence_id':'E',
        'source_fragment_id':'F0','speaker':'professor','stance':'asserted','assertive':True,
        'role_provenance':'existing-owner-annotation',
        'proposed_highlight':{'text':'原話。','status':'proposed'}}]}]}
    result=c.refresh_occurrences(old,['E'],records,'S')[0]['anchors'][0]
    assert {k:result[k] for k in ['speaker','stance','assertive','role_provenance']}=={
        'speaker':'professor','stance':'asserted','assertive':True,'role_provenance':'existing-owner-annotation'}
