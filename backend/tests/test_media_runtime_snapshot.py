from pathlib import Path
import json
import pytest
from backend.pipeline.media_batch.runtime_snapshot import runtime_root
from backend.pipeline.media_batch.gemini_plan import implementation
from backend.pipeline.media_batch.store import Conflict


def test_committed_runtime_reuse_and_code_drift_rejection_without_models(tmp_path):
    root=Path(__file__).resolve().parents[2]
    expected=implementation()
    cfg={'pipeline':'gemini-transcript-v1','implementation':expected,'artifact_root':str(tmp_path)}
    snapshot=runtime_root(cfg,root)
    assert snapshot!=root
    assert json.loads((snapshot/'runtime-snapshot.json').read_text())['implementation']==expected
    assert (snapshot/'backend/.venv').resolve()==(root/'backend/.venv').resolve()
    assert runtime_root(cfg,root)==snapshot
    source=snapshot/'backend/pipeline/media_batch/gemini_plan.py'
    source.write_text(source.read_text()+'\n# code drift\n')
    with pytest.raises(Conflict,match='code drift'):runtime_root(cfg,root)
    assert implementation()==expected
