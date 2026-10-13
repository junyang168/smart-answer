from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import pytest
from backend.tests.test_claim_qualifier_repair_contract import fixture, reseal
from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha
from backend.api.canonical_repository.viewpoint_production_safety import CvpProductionBlocked
from backend.pipeline import viewpoint_reviewed_cvp_prepare as m


def setup(monkeypatch):
    p=fixture(); packet=p['source_repair_application']['review_packet']
    packet.update(source_path='/source',source_file_sha256='file',source_body_sha256='body',complete_original=['original quote'],
                  bound_evidence=[{'step':{'evidence_step_id':'E1'},'fragment':{'fragment_id':'F1','source_id':'S1'}}])
    reseal(p)
    normalized={'claims':{'C1':p['claims'][0]},'viewpoint_claim_links':{'L1':p['viewpoint_claim_links'][0]}}
    monkeypatch.setattr(m,'normalize_package',lambda *a:normalized)
    records={(c,o):{'revision':v['revision'],'content_sha256':semantic_record_sha(v),'payload':deepcopy(v),'retired':False}
             for c,rows in normalized.items() for o,v in rows.items()}
    records['source_documents','S1']={'payload':{}}
    records['evidence_steps','E1']={'payload':{'evidence_step_id':'E1'},'retired':False}
    records['source_fragments','F1']={'payload':{'fragment_id':'F1','source_id':'S1'},'retired':False}
    old=packet['before_claim']
    manifest={'claim_packets':[{'claim_id':'C1','claim_revision':1,'claim_content_sha256':semantic_record_sha(old),'evidence_steps':[]}],'sources':[]}
    monkeypatch.setattr(m,'load_context_source',lambda *a:(Path('/source'),'file',SimpleNamespace(body_sha256='body',spoken_rows=[{'text':'original quote'}])))
    return p,records,manifest


def test_completed_qualifier_is_historical_membership_not_new_scope_authority(monkeypatch):
    p,r,manifest=setup(monkeypatch); before=deepcopy(r)
    assert m.completed_repair_parent_snapshot(manifest,r,[p],{'C1'},{'C2'})==['C1']
    assert r==before


@pytest.mark.parametrize('case',['selected','incomplete','oldpin','newpin','evidence','source','duplicate'])
def test_qualifier_cannot_hide_pending_or_stale_records(monkeypatch,case):
    p,r,manifest=setup(monkeypatch); completed={'C1'}; selected={'C2'}; packages=[p]
    if case=='selected': selected={'C1'}
    elif case=='incomplete': completed=set()
    elif case=='oldpin': manifest['claim_packets'][0]['claim_content_sha256']='wrong'
    elif case=='newpin': r['claims','C1']['content_sha256']='wrong'
    elif case=='evidence': r['evidence_steps','E1']['payload']['statement']='changed'
    elif case=='duplicate': packages.append(p)
    else: monkeypatch.setattr(m,'load_context_source',lambda *a:(Path('/source'),'changed',None))
    with pytest.raises(CvpProductionBlocked): m.completed_repair_parent_snapshot(manifest,r,packages,completed,selected)
