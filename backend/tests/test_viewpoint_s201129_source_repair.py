from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.api.canonical_repository.knowledge_models import ViewpointIdentityDecisionRecord
from backend.api.canonical_repository.reviewed_candidate_contract import validate_store_package_authorization, ConsensusApplicationError
from backend.pipeline import viewpoint_s201129_source_repair as repair
from backend.pipeline import viewpoint_s201129_source_epoch as epoch
from backend.pipeline import viewpoint_applied_source_epochs as composed


def decision():
    return dict(identity_decision_id='VID-test', identity_candidate_id='VIC-test',
        decision='match_existing', resolved_viewpoint_id='CV-test',
        reviewer_kind='system', reviewer_id='s201129_owner_scoped_opus_terminal_review',
        approval_basis='owner_scoped_independent_terminal_review', reason='exact review',
        input_sha256='a'*64, review_artifact_sha256='b'*64, policy_version=repair.VERSION,
        reviewer_model_ids=['claude-opus-5-5'], semantic_call_artifact_sha256s=['b'*64],
        created_at='2026-10-06T00:00:00Z', review_status='system_approved')


@pytest.mark.parametrize('mutation',[None,'wrong_policy','fake_human','fake_dual','wrong_model','missing_raw','missing_review','wrong_reviewer'])
def test_actual_single_opus_provenance_not_fake_dual_review(mutation):
    row=decision()
    changes={'wrong_policy':('policy_version','other'), 'fake_human':('reviewer_kind','human_editor'),
        'fake_dual':('approval_basis','dual_model_consensus'), 'wrong_model':('reviewer_model_ids',['gpt-6.1-sol']),
        'missing_raw':('semantic_call_artifact_sha256s',[]), 'missing_review':('review_artifact_sha256',None),
        'wrong_reviewer':('reviewer_id','arbitrary')}
    if mutation:
        k,v=changes[mutation];row[k]=v
        with pytest.raises(ValidationError):ViewpointIdentityDecisionRecord.model_validate(row)
    else:
        assert ViewpointIdentityDecisionRecord.model_validate(row).reviewer_model_ids==['claude-opus-5-5']


@pytest.mark.parametrize('package',[
    {'viewpoint_identity_decisions':[decision()]},
    {'claims':[{'review_status':'independent_terminal_reviewed'}]},
    {'source_repair_application':{'schema_version':repair.VERSION,'review_root':'/foreign'}}])
def test_scoped_review_cannot_bypass_exact_package_store_gate(package):
    with pytest.raises((ValueError,ConsensusApplicationError)):
        validate_store_package_authorization(package)


@pytest.mark.parametrize('mutation',[None,'missing','payload','revision','sha','retired'])
def test_all_postimages_not_only_changed_claims(monkeypatch,mutation):
    normal={'source_fragments':{'F':{'fragment_id':'F','revision':3,'quote':'exact'}}}
    monkeypatch.setattr(epoch,'postimages',lambda p:deepcopy(normal))
    p=normal['source_fragments']['F']
    records={('source_fragments','F'):{'payload':deepcopy(p),'revision':3,'content_sha256':repair.semantic_record_sha(p),'retired':False}}
    if mutation=='missing':records.clear()
    elif mutation=='payload':records['source_fragments','F']['payload']['quote']='drift'
    elif mutation=='revision':records['source_fragments','F']['revision']=4
    elif mutation=='sha':records['source_fragments','F']['content_sha256']='wrong'
    elif mutation=='retired':records['source_fragments','F']['retired']=True
    if mutation:
        with pytest.raises(ValueError):epoch.validate_postimage({},records)
    else:assert epoch.validate_postimage({},records)==normal


@pytest.mark.parametrize('mutation',[None,'duplicate','foreign','nested','unsorted','missing_hash'])
def test_multiple_epochs_keep_separate_exact_proofs(monkeypatch,mutation):
    old={'path':str(composed.historical.PACKAGE),'file_sha256':'a'*64}
    new={'path':str(repair.PACKAGE),'file_sha256':'b'*64}
    rows=sorted([old,new],key=lambda b:b['path'])
    proof={'schema_version':composed.VERSION,'bindings':rows}
    if mutation=='duplicate':proof['bindings']=[old,old]
    elif mutation=='foreign':proof['bindings']=[{'path':'/foreign','file_sha256':'c'*64}]
    elif mutation=='nested':proof['bindings']=[proof.copy()]
    elif mutation=='unsorted':proof['bindings']=list(reversed(rows))
    elif mutation=='missing_hash':proof['bindings']=[{'path':str(repair.PACKAGE)}]
    if mutation:
        with pytest.raises((ValueError,KeyError)):composed.members(proof)
    else:
        assert composed.members(proof)==rows
        assert composed.implementation(old) is composed.historical
        assert composed.implementation(new) is epoch


def test_pending_reviewed_claim_restores_only_membership_and_no_mutation(monkeypatch):
    claim={'claim_id':repair.PREFIX+'CL007','revision':3,'statement':'new','review_status':'independent_terminal_reviewed'}
    old={**claim,'revision':2,'statement':'old','review_status':'ai_consensus_reviewed'}
    records={('claims',claim['claim_id']):{'payload':deepcopy(claim),'revision':3,'content_sha256':repair.semantic_record_sha(claim),'retired':False}}
    package={'claims':[claim]};manifest={'claim_packets':[{'claim_id':claim['claim_id']}]}
    monkeypatch.setattr(repair,'validate_package',lambda p:None)
    monkeypatch.setattr(epoch,'postimages',lambda p:{'claims':{claim['claim_id']:claim}})
    monkeypatch.setattr(repair,'saved_before',lambda:({('claims',claim['claim_id']):old},{}))
    monkeypatch.setattr(repair,'reviewed_inputs',lambda:({'manifest_path':'/manifest'},None,None,None))
    monkeypatch.setattr(repair,'read_artifact',lambda p:manifest)
    original=deepcopy(records)
    checked,used=epoch.membership_snapshot(manifest,records,package,set(),{claim['claim_id']})
    assert checked['claims',claim['claim_id']]['payload']==old
    assert records==original and used==[claim['claim_id']]
    with pytest.raises(ValueError):epoch.selection_row('another-claim',package)


@pytest.mark.parametrize('wrong',[True,False])
def test_exact_legacy_flag_not_a_global_human_hold_waiver(monkeypatch,wrong):
    cid=repair.PREFIX+'CL004'
    claim={'claim_id':cid,'revision':3,'review_status':'human_review_required','review_note':'human_spot_check old'}
    before=deepcopy(claim)
    if wrong:claim['review_note']='real content hold'
    monkeypatch.setattr(repair,'stable_decided_at',lambda p:'2026-10-06T00:00:00Z')
    monkeypatch.setattr(repair,'binding',lambda p:{'path':str(p),'file_sha256':'a'*64})
    draft={'claims':[claim],'viewpoint_claim_links':[]}
    if wrong:
        with pytest.raises(ValueError):repair.package_for({('claims',cid):before},{},draft,{'owner_authorization':{}},{})
    else:
        result=repair.package_for({('claims',cid):before},{},draft,{'owner_authorization':{}},{})
        assert result['claims'][0]['review_status']=='independent_terminal_reviewed'
        assert 'Not fresh dual-review or human approval' in result['claims'][0]['review_note']
        assert draft['claims'][0]==claim


def test_epoch_cannot_double_transition_same_physical_source(monkeypatch):
    proof={'path':str(repair.PACKAGE),'file_sha256':'a'*64}
    monkeypatch.setattr(composed,'members',lambda p:[proof,proof])
    monkeypatch.setattr(epoch,'grouping_physical_transition',lambda m,p:{'/source':('old','new')})
    with pytest.raises(ValueError,match='conflicting'):
        composed.grouping_physical_transition({},proof)


@pytest.mark.parametrize('field',['claims','source_fragments','viewpoint_claim_links','viewpoint_identity_decisions','canonical_viewpoints','source_repair_application'])
def test_store_rebuilds_entire_reviewed_package_not_partial_pass(monkeypatch,field):
    expected={'claims':[{'exact':'claim'}],'source_fragments':[{'exact':'source'}],
        'viewpoint_claim_links':[{'exact':'link'}],'viewpoint_identity_decisions':[decision()],
        'source_repair_application':{'schema_version':repair.VERSION,'review_root':str(repair.REVIEW_ROOT)}}
    monkeypatch.setattr(repair,'reviewed_inputs',lambda:({},{},{},{}))
    monkeypatch.setattr(repair,'saved_before',lambda:({},{}))
    monkeypatch.setattr(repair,'package_for',lambda *args:deepcopy(expected))
    repair.validate_package(expected)
    altered=deepcopy(expected)
    if field=='source_repair_application':altered[field]['review_root']='/foreign'
    else:altered[field]=[{'not':'reviewed'}]
    with pytest.raises(ValueError):repair.validate_package(altered)


def test_no_application_or_audit_replay(monkeypatch,tmp_path):
    from backend.pipeline import viewpoint_s201129_source_continuation as worker
    monkeypatch.setattr(worker,'ROOT',tmp_path)
    with pytest.raises(ValueError,match='already attempted'):worker.run()
    out=tmp_path/'exact-audit';out.mkdir()
    with pytest.raises(ValueError,match='already attempted'):worker.verified_audit('exact-audit',{'C'},{'V'})
    with pytest.raises(ValueError,match='outside exact'):worker.recompletion_seed(Path('/foreign'),[])


@pytest.mark.parametrize('sequence',[1,39,42,43,156])
def test_source_audit_cannot_complete_pending_or_unaffected_group(sequence,tmp_path):
    from backend.pipeline import viewpoint_s201129_source_continuation as worker
    with pytest.raises(ValueError,match='two already-completed'):
        worker.completion_from_saved_audit(sequence,tmp_path/'audit.json',tmp_path/'output')
