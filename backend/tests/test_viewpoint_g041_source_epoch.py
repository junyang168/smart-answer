from copy import deepcopy
from types import SimpleNamespace

import pytest
from pathlib import Path

from backend.pipeline import viewpoint_g041_source_epoch as m


@pytest.mark.parametrize('sequence',[1,39,42,156])
def test_recompletion_cannot_expand_affected_groups(sequence):
    with pytest.raises(ValueError,match='only original affected'):
        m.completion_from_saved_audit(sequence,Path('/foreign/audit.json'),
            m.repair.APPLICATION_ROOT/'continuation/foreign')


def test_recompletion_cannot_replace_another_seed():
    with pytest.raises(ValueError,match='outside exact independent root'):
        m.recompletion_seed(m.repair.APPLICATION_ROOT/'continuation/g041-verified/completion.json',[])


def test_audit_worker_never_restarts_existing_continuation(monkeypatch,tmp_path):
    from backend.pipeline import viewpoint_g041_source_epoch_continuation as worker
    monkeypatch.setattr(worker,'ROOT',tmp_path)
    with pytest.raises(ValueError,match='already attempted'):
        worker.run()


@pytest.mark.parametrize('failure',[{'error':'semantic refusal','registry_writes':0},
    {'error':"module 'backend.pipeline.viewpoint_g041_transcript_repair' has no attribute 'write_derived'",'registry_writes':1}])
def test_progress_resume_rejects_other_failures(monkeypatch,tmp_path,failure):
    from backend.pipeline import viewpoint_g041_source_epoch_continuation as worker
    monkeypatch.setattr(worker,'ROOT',tmp_path)
    monkeypatch.setattr(worker.repair.old,'read_artifact',lambda p:failure)
    with pytest.raises(ValueError,match='exact once-only zero-write'):
        worker.run(saved_g040_progress_repair=True)


def fixture(monkeypatch):
    source={'source_id':m.repair.old.SOURCE,'revision':2,'exact':'new-source'}
    fragment={'fragment_id':'F','revision':2,'source_id':m.repair.old.SOURCE}
    claim={'claim_id':m.repair.CLAIM,'revision':2,'review_status':'ai_consensus_reviewed','evidence_step_ids':['E']}
    step={'evidence_step_id':'E','revision':2,'source_fragment_ids':['F']}
    normalized={'source_documents':{source['source_id']:source},'source_fragments':{'F':fragment},
        'claims':{claim['claim_id']:claim},'evidence_steps':{'E':step}}
    before={(c,o):{**p,'revision':1} for c,rows in normalized.items() for o,p in rows.items()}
    live={(c,o):p for c,rows in normalized.items() for o,p in rows.items()}
    records={(c,o):{'payload':p,'revision':p['revision'],'content_sha256':m.repair.old.semantic_record_sha(p),'retired':False}
        for (c,o),p in live.items()}
    package={'source_repair_application':{'review_packet':{'changed_paragraph_claims':[claim]}}}
    monkeypatch.setattr(m,'postimages',lambda p:deepcopy(normalized))
    monkeypatch.setattr(m.repair,'validate_package',lambda p:None)
    monkeypatch.setattr(m.repair,'saved_before',lambda:(deepcopy(before),{}))
    return package,normalized,before,live,records


@pytest.mark.parametrize('mutation',[None,'missing','payload','revision','content_sha','retired'])
def test_exact_current_postimage(monkeypatch,mutation):
    package,n,b,l,records=fixture(monkeypatch);key=('source_fragments','F')
    if mutation=='missing':del records[key]
    elif mutation=='payload':records[key]['payload']={**records[key]['payload'],'quote':'drift'}
    elif mutation=='revision':records[key]['revision']=3
    elif mutation=='content_sha':records[key]['content_sha256']='drift'
    elif mutation=='retired':records[key]['retired']=True
    if mutation is None:assert m.validate_postimage(package,records)==n
    else:
        with pytest.raises(ValueError):m.validate_postimage(package,records)


@pytest.mark.parametrize('mutation',[None,'not_completed','selected_changed','pending_hold','pending_drift'])
def test_predecessor_restore_is_only_membership_not_new_claim_approval(monkeypatch,mutation):
    package,n,b,l,records=fixture(monkeypatch)
    pending='pending';old={'claim_id':pending,'revision':1,'review_status':'ai_consensus_reviewed','statement':'unchanged'}
    b['claims',pending]=old
    records['claims',pending]={'payload':deepcopy(old),'revision':1,'content_sha256':m.repair.old.semantic_record_sha(old),'retired':False}
    monkeypatch.setattr(m.repair,'saved_before',lambda:(deepcopy(b),{}))
    manifest={'claim_packets':[{'claim_id':m.repair.CLAIM,'source_id':m.repair.old.SOURCE},
        {'claim_id':pending,'source_id':m.repair.old.SOURCE}]}
    completed={m.repair.CLAIM};selected={pending}
    if mutation=='not_completed':completed=set()
    elif mutation=='selected_changed':selected.add(m.repair.CLAIM)
    elif mutation=='pending_hold':records['claims',pending]['payload']['review_status']='human_review_required'
    elif mutation=='pending_drift':records['claims',pending]['payload']['statement']='new'
    if mutation is None:
        original=deepcopy(records);checked,used=m.membership_snapshot(manifest,records,package,completed,selected)
        assert records==original and used==[m.repair.CLAIM]
        assert checked['source_documents',m.repair.old.SOURCE]['revision']==1
        assert checked['source_fragments','F']['revision']==1
        assert checked['claims',m.repair.CLAIM]['revision']==1
        assert checked['claims',pending]==records['claims',pending]
    else:
        with pytest.raises(ValueError):m.membership_snapshot(manifest,records,package,completed,selected)


@pytest.mark.parametrize('mutation',[None,'foreign_claim','claim_version','hold','evidence','fragment','missing_fragment','extra_evidence'])
def test_fresh_bridge_only_reviewed_seven_and_exact_dependencies(monkeypatch,mutation):
    from backend.api.canonical_repository import knowledge_models as km
    package,n,b,live,records=fixture(monkeypatch)
    cid=m.repair.CLAIM;claim=deepcopy(live['claims',cid]);evidence={'E':deepcopy(live['evidence_steps','E'])};fragments={'F':deepcopy(live['source_fragments','F'])}
    monkeypatch.setattr(m,'verified_applied',lambda binding,store:(package,b,live))
    monkeypatch.setattr(m,'selection_row',lambda cid,p:{'review_binding':{'exact':'review'},'review_input_binding':{'exact':'packet'}})
    monkeypatch.setattr(km.EvidenceStepRecord,'model_validate',lambda p:SimpleNamespace(**p))
    monkeypatch.setattr(km,'evidence_fragment_ids',lambda e:e.source_fragment_ids)
    if mutation=='foreign_claim':cid='foreign'
    elif mutation=='claim_version':claim['revision']=3
    elif mutation=='hold':claim['review_status']='human_review_required'
    elif mutation=='evidence':evidence['E']['statement']='different'
    elif mutation=='fragment':fragments['F']['source_id']='foreign'
    elif mutation=='missing_fragment':fragments={}
    elif mutation=='extra_evidence':evidence['extra']={'revision':1}
    if mutation is None:
        result=m.bridge(claim_id=cid,current_claim=claim,current_evidence=evidence,current_fragments=fragments,
            source_repair_binding={'exact':'package'},store=object())
        assert result['old_review_not_relabelled'] is True
        assert result['review_provider']=='fresh_dual_subscription_owner_source_repair'
    else:
        with pytest.raises(ValueError):m.bridge(claim_id=cid,current_claim=claim,current_evidence=evidence,current_fragments=fragments,
            source_repair_binding={'exact':'package'},store=object())


def test_postimages_keep_unstated_source_fields_absent(monkeypatch):
    before={('source_documents','source'):{'source_id':'source','revision':1,'body':'old','preserved':'yes'}}
    normalized={'source_documents':{'source':{'source_id':'source','revision':2,'body':'new','visual_source_assets':[]}}}
    monkeypatch.setattr(m,'_normalize_records',lambda p:(normalized,{('source_documents','source'):frozenset({'source_id','revision','body'})}))
    monkeypatch.setattr(m.repair,'saved_before',lambda:(before,{}))
    actual=m.postimages({})['source_documents']['source']
    assert actual=={'source_id':'source','revision':2,'body':'new','preserved':'yes'}
    assert 'visual_source_assets' not in actual


@pytest.mark.parametrize('mutation',[None,'manifest','path','old_sha','frozen_sha','revision','body','content','current_sha','missing_applied','physical_drift'])
def test_grouping_transition_requires_exact_applied_predecessor(monkeypatch,mutation):
    old={'revision':1,'source_id':m.repair.old.SOURCE}
    versions={'published_path':'/published.json','old_file_sha256':'old','new_file_sha256':'new','old_body_sha256':'old-body','new_body_sha256':'new-body'}
    source={'source_id':m.repair.old.SOURCE,'path':'/published.json','file_sha256':'old','frozen_file_sha256':'old',
        'source_revision':1,'source_body_sha256':'old-body','source_content_sha256':m.repair.old.semantic_record_sha(old)}
    manifest={'sources':[source]};original=deepcopy(manifest)
    current={'source_sha256':'new-body','source_body_sha256':'new-body'}
    report={'source_versions':versions,'frozen_scope':{'manifest_path':'/frozen.json'}}
    def applied(binding,store):
        if mutation=='missing_applied':raise ValueError('not applied')
        return {},{('source_documents',m.repair.old.SOURCE):old},{('source_documents',m.repair.old.SOURCE):current}
    monkeypatch.setattr(m,'verified_applied',applied)
    monkeypatch.setattr(m.repair.old,'frozen_inputs',lambda:(report,{}))
    def physical(report):
        if mutation=='physical_drift':raise ValueError('physical drift')
    monkeypatch.setattr(m.repair.old,'verify_source',physical)
    if mutation=='manifest':manifest['foreign']=True
    fields={'path':'path','old_sha':'file_sha256','frozen_sha':'frozen_file_sha256','revision':'source_revision',
        'body':'source_body_sha256','content':'source_content_sha256'}
    if mutation in fields:source[fields[mutation]]='drift';original=deepcopy(manifest)
    if mutation=='current_sha':current['source_sha256']='drift'
    monkeypatch.setattr(m.repair.old,'read_artifact',lambda p:original)
    if mutation is None:assert m.grouping_physical_transition(manifest,{})=={'/published.json':('old','new')}
    else:
        with pytest.raises(ValueError):m.grouping_physical_transition(manifest,{})
