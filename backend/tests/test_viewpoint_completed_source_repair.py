from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import pytest
from backend.tests.test_authorized_source_repair_contract import fixture_package
from backend.api.canonical_repository.postgres_store import normalize_package
from backend.api.canonical_repository.viewpoint_foundation import sha256_json, semantic_record_sha
from backend.api.canonical_repository.viewpoint_production_safety import CvpProductionBlocked
from backend.pipeline import viewpoint_reviewed_cvp_prepare as runner


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
