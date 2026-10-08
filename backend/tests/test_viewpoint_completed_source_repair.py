from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import pytest
from backend.tests.test_authorized_source_repair_contract import fixture_package
from backend.api.canonical_repository.postgres_store import normalize_package
from backend.api.canonical_repository.viewpoint_foundation import sha256_json, semantic_record_sha
from backend.api.canonical_repository.viewpoint_production_safety import CvpProductionBlocked
from backend.pipeline import viewpoint_reviewed_cvp_prepare as runner


@pytest.mark.parametrize('schema,module_name',[
    ('wang_owner_claim_statement_revision_v1','claim_statement_revision'),
    ('wang_source_reconciliation_v1','source_reconciliation_epoch'),
    ('wang_s201129_owner_opus_source_reconciliation_v1','viewpoint_s201129_source_epoch'),
    ('wang_g041_owner_qualified_transcript_reconciliation_v2','viewpoint_g041_source_epoch')])
@pytest.mark.parametrize('drift',[None,'reader_failure','duplicate','accepted_pin'])
def test_shared_membership_consumes_complete_epoch_reader_without_new_write_reconstruction(monkeypatch,schema,module_name,drift):
    from importlib import import_module
    module=import_module('backend.pipeline.'+module_name)
    old={'claim_id':'C','revision':1,'statement':'original accepted'}
    current={**old,'revision':2,'statement':'actual independently reviewed change'}
    row=lambda p:{'payload':p,'revision':p['revision'],'content_sha256':semantic_record_sha(p),'retired':False}
    records={('claims','C'):row(current),('other','unchanged'):{'all':'original non-target metadata'}}
    manifest={'claim_packets':[{'claim_id':'C','claim_revision':1,'claim_content_sha256':semantic_record_sha(old),'evidence_steps':[]}],'sources':[]}
    package={'source_repair_application':{'schema_version':schema}};packages=[package];calls=[]
    before=deepcopy((manifest,records,package))
    def membership(m,r,p,done,selected):
        calls.append((done,selected))
        assert m==manifest and p==package and r['other','unchanged']==records['other','unchanged']
        if drift=='reader_failure':raise ValueError('original applied/review/source/current proof invalid')
        checked=dict(r);checked['claims','C']=row(old)
        return checked,['C']
    monkeypatch.setattr(module,'membership_snapshot',membership)
    monkeypatch.setattr(runner,'validate_store_package_authorization',lambda *a:pytest.fail('new-write reconstruction'))
    if drift=='duplicate':packages*=2
    elif drift=='accepted_pin':manifest['claim_packets'][0]['claim_content_sha256']='different'
    if drift:
        with pytest.raises(ValueError):runner.completed_repair_parent_snapshot(manifest,records,packages,{'C'},{'next'})
    else:
        assert runner.completed_repair_parent_snapshot(manifest,records,packages,{'C'},{'next'})==['C']
        assert (manifest,records,package)==before
    assert len(calls)==(2 if drift=='duplicate' else 1)


def inputs(monkeypatch):
    package=fixture_package(); app=package['source_repair_application']; packet=app['review_packet']
    package['source_fragments'][0].update(paragraph_key='S0001')
    packet.update(source_path='/source',source_file_sha256='file')
    body={k:v for k,v in package.items() if k!='source_repair_application'}
    packet['package_sha256']=sha256_json(body)
    for raw in app['review_artifacts']:
        raw['request_payload_sha256']=sha256_json(packet)
        raw['artifact_sha256']=sha256_json({k:v for k,v in raw.items() if k!='artifact_sha256'})
    old=packet['items'][0]['before']
    manifest={'claim_packets':[{'claim_id':'C1','claim_revision':3,'claim_content_sha256':semantic_record_sha(old),'evidence_steps':[]}],'sources':[]}
    records={(c,oid):{'revision':p['revision'],'content_sha256':semantic_record_sha(p),'payload':p,'retired':False}
        for c,rows in normalize_package(package).items() for oid,p in rows.items()}
    records[('source_documents','S1')]={'payload':{'source_id':'S1'}}
    monkeypatch.setattr(runner,'load_context_source',lambda s:(Path('/source'),'file',SimpleNamespace(spoken_rows=[{'text':'new context'}])))
    return manifest,records,package


def test_only_completed_repair_may_preserve_historical_membership(monkeypatch):
    manifest,records,package=inputs(monkeypatch)
    before=deepcopy(records)
    assert runner.completed_repair_parent_snapshot(manifest,records,[package],{'C1'},{'C2'})==['C1']
    assert records==before  # Never repins or mutates real records/manifest.


@pytest.mark.parametrize('case',['selected','not_complete','predecessor','current_drift','duplicate'])
def test_source_repair_cannot_admit_stale_or_selected_claims(monkeypatch,case):
    manifest,records,package=inputs(monkeypatch)
    completed={'C1'}; selected={'C2'}; packages=[package]
    if case=='selected': selected={'C1'}
    elif case=='not_complete': completed=set()
    elif case=='predecessor': manifest['claim_packets'][0]['claim_content_sha256']='bad'
    elif case=='current_drift': records[('claims','C1')]['content_sha256']='bad'
    else: packages.append(package)
    with pytest.raises(CvpProductionBlocked):
        runner.completed_repair_parent_snapshot(manifest,records,packages,completed,selected)


def test_without_repair_normal_parent_drift_still_blocks(monkeypatch):
    manifest,records,_=inputs(monkeypatch)
    with pytest.raises(CvpProductionBlocked,match='current pin drift'):
        runner.completed_repair_parent_snapshot(manifest,records,[],{'C1'},{'C2'})

def annotation_inputs(monkeypatch):
    from backend.tests.test_viewpoint_owner_evidence_annotation import package as annotation_package
    from backend.pipeline import viewpoint_owner_evidence_annotation as annotation
    pkg=annotation_package();app=pkg['source_repair_application'];p=app['review_packet']
    p['claim']['revision']=3;p['physical_bindings']=[]
    for raw in app['review_artifacts']:
        raw['request_payload_sha256']=sha256_json(p)
        raw['artifact_sha256']=sha256_json({k:v for k,v in raw.items() if k!='artifact_sha256'})
    old,new=p['before_evidence'],p['after_evidence'];claim=p['claim']
    manifest={'claim_packets':[{'claim_id':'C1','claim_revision':3,'claim_content_sha256':semantic_record_sha(claim),
        'evidence_steps':[{'evidence_step_id':'E1','revision':old['revision'],'content_sha256':semantic_record_sha(old),'fragments':[]}]}],'sources':[]}
    records={('claims','C1'):{'revision':3,'content_sha256':semantic_record_sha(claim),'payload':claim,'retired':False},
        ('evidence_steps','E1'):{'revision':new['revision'],'content_sha256':semantic_record_sha(new),'payload':new,'retired':False}}
    return manifest,records,pkg

def test_completed_labeled_annotation_preserves_only_historical_evidence_pin(monkeypatch):
    manifest,records,pkg=annotation_inputs(monkeypatch);before=deepcopy(records)
    assert runner.completed_repair_parent_snapshot(manifest,records,[pkg],{'C1'},{'C2'})==['C1']
    assert records==before

@pytest.mark.parametrize('case',['selected','not_complete','old_pin','current','claim_pin','duplicate'])
def test_annotation_parent_cannot_relax_current_or_selected_admission(monkeypatch,case):
    m,r,p=annotation_inputs(monkeypatch);done={'C1'};selected={'C2'};packages=[p]
    if case=='selected':selected={'C1'}
    elif case=='not_complete':done=set()
    elif case=='old_pin':m['claim_packets'][0]['evidence_steps'][0]['content_sha256']='bad'
    elif case=='current':r['evidence_steps','E1']['payload']['statement']='changed'
    elif case=='claim_pin':m['claim_packets'][0]['claim_content_sha256']='bad'
    else:packages.append(p)
    with pytest.raises(ValueError):runner.completed_repair_parent_snapshot(m,r,packages,done,selected)
