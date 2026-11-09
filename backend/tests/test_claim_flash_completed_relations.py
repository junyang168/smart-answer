"""Completed GPT edges are compiled once; mechanical failures never become approvals."""
from copy import deepcopy
import pytest
from backend.pipeline import claim_flash_pairs as pairs
from backend.tests.test_claim_flash_source import fixture
from backend.tests.test_claim_flash_arguments import Ports


class NoModels:
    def judge(self,*a):raise AssertionError('completed edge was sent to Jev')
    def repair(self,*a):raise AssertionError('completed edge was sent to GPT again')


def scene(tmp_path):
    records,sources=fixture(tmp_path)
    records['observations/O']={'payload':{'observation_id':'O','statement':'教授原話。','source_fragment_ids':['F0']},'retired':False}
    for cid in ['C','D']:
        records['claims/'+cid]={'payload':{'claim_id':cid,'statement':'教授的判斷。','evidence_step_ids':['E']},'retired':False}
    return records,sources


def edge(**changes):
    return {'a':'observations/O','b':'evidence_steps/E','relation_type':'supports',
            'direction':'a_to_b','reason':'教授用這項觀察支持此判斷。',**changes}


def test_complete_edge_has_no_second_model_call_and_preserves_reason(tmp_path):
    records,sources=scene(tmp_path);before=deepcopy(records);reason='教授用這項觀察支持此判斷。'
    result=pairs.review(records,sources,[],[],tmp_path,NoModels(),kind='lower',completed_proposals=[edge(reason=reason)])
    assert result['pending']==[] and result['updates']=={} and result['retiring_keys']==[]
    assert result['new_relations']==[{'from_id':'O','to_id':'E','relation_type':'supports','reason':reason,'record_namespace':pairs.f.VERSION}]
    assert result['outcomes'][0]['basis']=='gpt_completed_proposal' and records==before
    assert not list(tmp_path.glob('gpt/*')) and not list(tmp_path.glob('jev/*'))


def test_reverse_direction_is_compiled_without_another_semantic_decision(tmp_path):
    records,sources=scene(tmp_path)
    result=pairs.review(records,sources,[],[],tmp_path,NoModels(),kind='claim',
        completed_proposals=[edge(a='claims/C',b='claims/D',direction='b_to_a')])
    assert [(x['from_id'],x['to_id']) for x in result['new_relations']]==[('D','C')]


@pytest.mark.parametrize('change',[
    {'a':'observations/X'},{'b':'observations/O'},{'relation_type':'invented'},
    {'direction':'invented'},{'reason':''},{'a':'claims/C','b':'claims/D','relation_type':'applies'},
])
def test_invalid_complete_edge_is_rejected_before_models(tmp_path,change):
    records,sources=scene(tmp_path);kind='claim' if change.get('a')=='claims/C' else 'lower'
    with pytest.raises(ValueError):
        pairs.review(records,sources,[],[],tmp_path,NoModels(),kind=kind,completed_proposals=[edge(**change)])


def test_incomplete_saved_proposal_cannot_be_relabelled_completed(tmp_path):
    records,sources=scene(tmp_path);partial=edge();partial.pop('direction')
    with pytest.raises(ValueError):
        pairs.review(records,sources,[],[],tmp_path,NoModels(),kind='lower',completed_proposals=[partial])


def test_actual_source_drift_is_still_rejected_without_models(tmp_path):
    records,sources=scene(tmp_path);records['source_fragments/F0']['payload']['source_sha256']='old'
    with pytest.raises(ValueError,match='source binding'):
        pairs.review(records,sources,[],[],tmp_path,NoModels(),kind='lower',completed_proposals=[edge()])


def test_explicit_constraint_still_prevents_complete_edge_write(tmp_path):
    records,sources=scene(tmp_path)
    records['claim_relation_constraints/N']={'payload':{'source_id':'O','target_id':'E',
        'forbidden_relation_types':['supports'],'bidirectional':False}}
    result=pairs.review(records,sources,[],[],tmp_path,NoModels(),kind='lower',completed_proposals=[edge()])
    assert result['new_relations']==[] and result['pending'][0]['reason']=='explicit_relation_constraint'


def test_duplicate_canonical_edge_is_saved_once_and_conflict_is_not_silently_chosen(tmp_path):
    records,sources=scene(tmp_path)
    same=edge(a='evidence_steps/E',b='observations/O',direction='b_to_a')
    result=pairs.review(records,sources,[],[],tmp_path/'same',NoModels(),kind='lower',completed_proposals=[edge(),same])
    assert len(result['new_relations'])==1 and not result['pending']
    conflict=pairs.review(records,sources,[],[],tmp_path/'conflict',NoModels(),kind='lower',
        completed_proposals=[edge(),edge(direction='b_to_a')])
    assert conflict['pending'][0]['reason']=='conflicting_completed_relation_proposals'


def test_legacy_unfinished_pair_keeps_reason_for_its_missing_decision_not_jev(tmp_path):
    records,sources=scene(tmp_path);ports=Ports();partial={'a':'observations/O','b':'evidence_steps/E',
        'reason':'原來的候選理由。','candidate_reason':'原來的候選理由。'}
    result=pairs.review(records,sources,[],[partial],tmp_path,ports,kind='lower')
    req=next(req for role,req in ports.calls if role=='gpt')
    assert req['input']['jobs'][0]['candidate_reason']==partial['reason']
    assert all('candidate_reason' not in req['state'] for role,req in ports.calls if role=='jev')
    assert not result['pending']
