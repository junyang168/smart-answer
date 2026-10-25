import pytest
from backend.pipeline import viewpoint_authorized_source_repair_357 as runner


def test_readback_uses_normalized_postimage_not_omitted_defaults(monkeypatch):
    after={'fragment_id':'F1','visual_facts':[]}
    before={'claim_id':'C1','revision':3}
    artifacts={
        'dry-run-plan.json':{'change_set_id':'K1','operations':[{'collection':'source_fragments','object_id':'F1','operation':'create','payload':after}]},
        'before.json':{'collections':['claims','source_fragments'],'pre_fingerprint_sha256':runner.fingerprint({('claims','C1'):before},['claims','source_fragments']),'before_records':[]},
        'apply-receipt.json':{'status':'applied','change_set_id':'K1'},
        'packet.json':{'source_path':'/source'},
    }
    monkeypatch.setattr(runner,'read_artifact',lambda p:artifacts[p.name])
    monkeypatch.setattr(runner,'snapshot',lambda s:{('source_fragments','F1'):after,('claims','C1'):before})
    monkeypatch.setattr(runner,'file_sha256',lambda p:runner.FILE_SHA)
    monkeypatch.setattr(runner,'write_immutable',lambda *args:None)
    from pathlib import Path
    assert runner.verify_applied(None,Path('/output'))['status']=='applied_readback_verified'
    # A semantic quote/post-image difference is not silently normalized away.
    after['visual_facts']=['tampered']
    monkeypatch.setattr(runner,'snapshot',lambda s:{('source_fragments','F1'):{'fragment_id':'F1','visual_facts':[]},('claims','C1'):before})
    with pytest.raises(AssertionError,match='Readback differs'):
        runner.verify_applied(None,Path('/output'))
