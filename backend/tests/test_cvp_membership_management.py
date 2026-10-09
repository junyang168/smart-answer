"""Retirement transfers to routine management, never a fake pass/retry."""
from copy import deepcopy
import pytest
from backend.pipeline import cvp_membership_management as m
from backend.pipeline import source_reconciliation as c
from backend.tests.test_source_unresolved_retirement_review import pending
from backend.tests.test_source_reconciliation import scenario
from backend.tests.test_claim_issue_management import issue_setup
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres, postgres_store


@pytest.fixture
def retired(pending):
    frozen, proposal = deepcopy(pending)
    states = c.index(frozen['before_records'])
    states['canonical_viewpoints/CV-test'].update(
        identity_status='active',review_status='system_approved',created_from_candidate_id='VIC-old')
    states['viewpoint_revisions/CVR-test']['review_status']='system_approved'
    frozen['before_records']=c.rows(states)
    frozen['sql_scope']['read_pins'].append(
        {'collection':'viewpoint_identity_candidates','object_id':'VIC-old','pin':[2,'retired-proof',True]})
    raw={'artifact_sha256':'saved-correction','response':proposal.model_dump()}
    return frozen,proposal,raw


def test_routine_intake_preserves_unresolved_and_budget(retired):
    frozen,proposal,raw=retired
    before=deepcopy((frozen,proposal,raw))
    handoff=m.retirement_handoff(frozen,proposal,raw)
    assert handoff['waiting_for']=='system' and not handoff['write_authority']
    assert handoff['original_correction_budget_exhausted']
    assert handoff['targets'][0]['retired_creation_candidate_id']=='VIC-old'
    assert (frozen,proposal,raw)==before
    with pytest.raises(ValueError,match='unresolved'):c.validate_proposal(frozen,proposal)


@pytest.mark.parametrize('change',['no_retirement','foreign_retirement','head_hold','revision_hold','wrong_revision','claim_unresolved','unknown','write_unresolved'])
def test_not_a_missing_proof_or_content_escape(retired,change):
    frozen,proposal,raw=retired
    states=c.index(frozen['before_records'])
    if change=='no_retirement':frozen['sql_scope']['read_pins']=[]
    elif change=='foreign_retirement':frozen['sql_scope']['read_pins'][-1]['object_id']='another'
    elif change=='head_hold':states['canonical_viewpoints/CV-test']['review_status']='withheld'
    elif change=='revision_hold':states['viewpoint_revisions/CVR-test']['review_status']='rejected'
    elif change=='wrong_revision':states['viewpoint_revisions/CVR-test']['viewpoint_id']='other'
    elif change=='claim_unresolved':next(d for d in proposal.dispositions if d.record_key.startswith('claims/')).action='unresolved'
    elif change=='unknown':frozen['inventory']['unknown_keys']=['foreign/X']
    else:proposal.records.append(c.Row(collection='canonical_viewpoints',object_id='CV-test',payload=states['canonical_viewpoints/CV-test']))
    frozen['before_records']=c.rows(states)
    try: result=m.retirement_handoff(frozen,proposal,raw)
    except ValueError:return
    assert result is None


def test_no_second_management_identity_for_reason_or_raw_wording(retired):
    frozen,proposal,raw=retired
    first=m.retirement_handoff(frozen,proposal,raw)
    raw['artifact_sha256']='different-report'
    for d in proposal.dispositions:d.reason+=' more words'
    assert m.retirement_handoff(frozen,proposal,raw)['handoff_id']==first['handoff_id']


@pytest.fixture
def member_material(scenario):
    from backend.tests.test_source_reconciliation import add_viewpoint
    add_viewpoint(scenario)
    _,states,_=scenario
    # New member is a different current Claim; the old link is immutable context.
    states['claims/CL-new']={**states['claims/CL-test'],'claim_id':'CL-new'}
    return {'handoff':{'targets':[{'viewpoint_id':'CV-test','viewpoint_revision_id':'CVR-old'}]},
            'before_records':c.rows(states),
            'candidates':[{'claim':states['claims/CL-new'],'projection':{'evidence':[
                {'evidence_step_id':'E-test','source_fragment_id':'FR-test',
                 'valid_for_identity_review':True,'support_eligibility':'eligible'}]}}]}


def member_answer():
    return {'decision':'propose_members','members':[{'viewpoint_id':'CV-test','claim_id':'CL-new',
        'statement_component':'甲是假设','evidence_bindings':[{'evidence_step_id':'E-test','source_fragment_id':'FR-test'}],
        'reason':'SYNTHETIC ONLY: independently verify same core'}],'reason':'SYNTHETIC ONLY'}


def test_exact_new_component_does_not_mutate_existing_support_or_core(member_material):
    before=deepcopy(member_material)
    links=m.new_links(member_material,member_answer())
    assert links[0]['link_type']=='equivalent_component'
    assert links[0]['component_locator']['canonical_spans']==[
        {'start_char':0,'end_char':4,'exact_text':'甲是假设'}]
    assert links[0]['claim_id']=='CL-new' and member_material==before


@pytest.mark.parametrize('change',['foreign_target','foreign_claim','duplicate_member','missing_target',
    'existing_support','ambiguous_span','foreign_evidence','foreign_fragment','extra_binding_field','unresolved'])
def test_membership_is_not_automatic_equivalence_or_scope_escape(member_material,change):
    answer=member_answer();member=answer['members'][0]
    if change=='foreign_target':member['viewpoint_id']='foreign'
    elif change=='foreign_claim':member['claim_id']='retired'
    elif change=='duplicate_member':answer['members'].append(deepcopy(member))
    elif change=='missing_target':answer['members']=[]
    elif change=='existing_support':member_material['before_records'].append(
        {'collection':'viewpoint_claim_links','object_id':'already','payload':{
            'claim_id':'CL-new','viewpoint_id':'CV-test','link_type':'supports','effective_state':'active'}})
    elif change=='ambiguous_span':member_material['candidates'][0]['claim']['statement']='甲是假设。甲是假设。'
    elif change=='foreign_evidence':member['evidence_bindings'][0]['evidence_step_id']='other'
    elif change=='foreign_fragment':member['evidence_bindings'][0]['source_fragment_id']='other'
    elif change=='extra_binding_field':member['evidence_bindings'][0]['upgrade']='eligible'
    else:answer.update(decision='unresolved',members=[])
    from backend.pipeline.source_reconciliation_runner import Stop
    with pytest.raises((ValueError,Stop)):m.new_links(member_material,answer)


def test_independent_complete_package_compiler_is_exact_three_creates(member_material,tmp_path):
    proposal={'response':member_answer(),'artifact_sha256':'a'*64}
    raw={'response':{'verdicts':[{'claim_id':'CL-new','approved':True,'problems':[],
        'reason':'SYNTHETIC ONLY'}]},'artifact_sha256':'b'*64,'stage':'initial_review','completed_at':'2026-10-09T00:00:00Z'}
    before=deepcopy(member_material)
    package=m.compile_package(member_material,proposal,raw,tmp_path)
    assert [len(package[k]) for k in ['viewpoint_claim_links','viewpoint_identity_candidates','viewpoint_identity_decisions']]==[1,1,1]
    assert member_material==before
    assert package['viewpoint_identity_decisions'][0]['review_artifact_sha256']=='b'*64
    assert package['viewpoint_claim_links'][0]['decision_id']==package['viewpoint_identity_decisions'][0]['identity_decision_id']
    raw['response']['verdicts'][0]['approved']=False
    with pytest.raises(ValueError,match='rejection'):m.compile_package(member_material,proposal,raw,tmp_path)


@pytest.mark.parametrize('change',['missing','duplicate','approved_with_problems','rejected'])
def test_complete_review_denominator_and_real_rejection(member_material,change):
    packet=m.review_packet(member_material,member_answer())
    row={'claim_id':'CL-new','approved':True,'problems':[],'reason':'SYNTHETIC ONLY'}
    verdicts=[row]
    if change=='missing':verdicts=[]
    elif change=='duplicate':verdicts.append(deepcopy(row))
    elif change=='approved_with_problems':row['problems']=['real ambiguity']
    else:row['approved']=False
    try: result=m.approved(packet,{'response':{'verdicts':verdicts}})
    except ValueError:return
    assert not result


@pytest.mark.parametrize('stage',['proposal','correction','initial_review','final_review'])
def test_membership_roles_and_codec_exact_saved_identity(tmp_path,stage):
    from backend.pipeline.source_reconciliation_runner import Runner
    class Ports:
        def adapter(self,role):
            adapter=m.model_adapter(role);adapter._client.executable=None
            adapter.generate=lambda _:({'verdicts':[]} if 'review' in role else
                {'decision':'unresolved','members':[],'reason':'SYNTHETIC ONLY'})
            return adapter
    runner=Runner(tmp_path,Ports());runner.root=tmp_path
    raw=runner.call(stage,{'synthetic':'full own body'})
    assert m.saved_raw(tmp_path,stage,{'synthetic':'full own body'})==raw
    with pytest.raises(ValueError):m.saved_raw(tmp_path,stage,{'synthetic':'changed'})


def test_same_cvp_version_reservation_survives_other_group_root(postgres_store,tmp_path):
    from backend.api.canonical_repository.postgres_store import ChangeSetConflict
    kwargs={'identity':'synthetic-version-identity','parent_run_id':'g1','root':tmp_path,'handoff_id':'finding'}
    postgres_store.reserve_cvp_membership_identity(**kwargs)
    postgres_store.reserve_cvp_membership_identity(**kwargs)
    for field,value in [('parent_run_id','g2'),('root',tmp_path/'other'),('handoff_id','new-finder')]:
        with pytest.raises(ChangeSetConflict):postgres_store.reserve_cvp_membership_identity(**{**kwargs,field:value})


def resolution_fixture(retired,tmp_path,monkeypatch):
    from backend.pipeline.source_reconciliation_runner import write,binding
    f,p,raw=retired;h=m.retirement_handoff(f,p,raw)
    # Pure routing fixture: no model approval or production write is claimed.
    package={'package_id':'synthetic','viewpoint_claim_links':[{'claim_id':'synthetic-new-member'}],
             'viewpoint_identity_candidates':[],'viewpoint_identity_decisions':[],
             'source_repair_application':{'run_root':str(tmp_path/'management')}}
    write(tmp_path/'management'/'materials.json',{'handoff':h,'source_materials_sha256':m.sha256_json(f),
        'before_records':f['before_records'],'read_pins':f['sql_scope']['read_pins']})
    write(tmp_path/'management'/'approved-package.json',package)
    receipt={'schema_version':m.VERSION,'handoff_id':h['handoff_id'],
        'package':binding(tmp_path/'management'/'approved-package.json'),
        'status':'independently_reviewed_pending_joint_transaction',
        'old_unresolved_is_not_pass':True,'original_correction_budget_exhausted':True}
    write(tmp_path/'membership-resolution.json',receipt)
    monkeypatch.setattr(m,'validate_package',lambda *_:None)
    return f,p,raw,receipt


def test_domain_resolution_is_explicit_and_preserves_raw_and_six_updates(retired,tmp_path,monkeypatch):
    f,p,raw,receipt=resolution_fixture(retired,tmp_path,monkeypatch)
    original=deepcopy((f,p,raw))
    resolved,package,actual=m.resolved_proposal(tmp_path,f,p,raw)
    assert (f,p,raw)==original and resolved.records==p.records
    for old,new in zip(p.dispositions,resolved.dispositions,strict=True):
        if old.action=='unresolved':
            assert new.action=='retain' and 'pending' in new.reason and receipt['package']['file_sha256'] in new.reason
        else:assert new==old
    assert all(i.action=='retain' for i in resolved.cvp_impacts)
    assert actual==receipt
    c.validate_proposal(f,resolved)


@pytest.mark.parametrize('field,value',[('handoff_id','foreign'),('old_unresolved_is_not_pass',False),
    ('original_correction_budget_exhausted',False),('status','already_passed'),('schema_version','foreign')])
def test_receipt_cannot_fake_pass_or_reset_original_budget(retired,tmp_path,monkeypatch,field,value):
    f,p,raw,receipt=resolution_fixture(retired,tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='receipt drift'):
        m.resolved_proposal(tmp_path,f,p,raw,receipt={**receipt,field:value})


def test_missing_resolution_or_independent_rejection_cannot_clear_unresolved(retired,tmp_path,monkeypatch):
    f,p,raw,receipt=resolution_fixture(retired,tmp_path,monkeypatch)
    def rejection(_):raise ValueError('real independent refusal')
    monkeypatch.setattr(m,'validate_package',rejection)
    with pytest.raises(ValueError,match='refusal'):m.resolved_proposal(tmp_path,f,p,raw)
    with pytest.raises(ValueError,match='unresolved'):c.validate_proposal(f,p)
