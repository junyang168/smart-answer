from pathlib import Path
import os
import pytest
from backend.pipeline import viewpoint_content_blocked_fallback as m
from backend.pipeline.viewpoint_resolution_runtime import write_immutable
from backend.api.canonical_repository.postgres_store import sha256_json


def put(root,audit):
    root.mkdir(parents=True,exist_ok=True)
    marker=root/'fallback-manifest.json';write_immutable(marker,{'original_report_binding':m.binding(audit)})
    write_immutable(root/'assessment.json',{'meta':{m.KEY:m.binding(marker)}})


@pytest.mark.parametrize('fault',[None,'same','corrupt','collision'])
def test_only_exact_audit_generation_can_reuse_composed_assessment(tmp_path,monkeypatch,fault):
    old=tmp_path/'old.json';new=tmp_path/'new.json';write_immutable(old,{'generation':1});write_immutable(new,{'generation':2})
    output=tmp_path/'fallback';put(output,old)
    before={p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    validations=[]
    def validate(a):
        validations.append(a)
        if fault=='corrupt':raise ValueError('original evidence corrupt')
        return []
    monkeypatch.setattr(m,'validate',validate)
    selected=output/('audit-'+sha256_json(m.binding(new)))
    if fault=='collision':put(selected,old)
    if fault in {'corrupt','collision'}:
        with pytest.raises(ValueError):m.assessment_output(new,output)
    elif fault=='same':assert m.assessment_output(old,output)==output
    else:
        assert m.assessment_output(new,output)==selected
        put(selected,new)
        assert m.assessment_output(new,output)==selected
    assert validations
    assert all(p.read_bytes()==b for p,b in before.items())


def test_empty_composition_keeps_legacy_output(tmp_path):
    assert m.assessment_output(tmp_path/'unused.json',tmp_path/'fallback')==tmp_path/'fallback'


@pytest.mark.skipif(not os.environ.get('WKP_CURRENT_FALLBACK_AUDIT'),reason='explicit actual saved audit readonly opt-in')
def test_actual_full_current_composition_reuses_saved_answers_only(tmp_path,monkeypatch):
    from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
    monkeypatch.setattr(CodexSubscriptionClient,'generate_json',lambda *a,**k:pytest.fail('no new model budget'))
    audit=Path(os.environ['WKP_CURRENT_FALLBACK_AUDIT'])
    original=audit.read_bytes()
    write=m.write_immutable
    def local_only(path,value):
        assert Path(path).is_relative_to(tmp_path),'diagnostic cannot write original proof/cache'
        return write(path,value)
    monkeypatch.setattr(m,'write_immutable',local_only)
    result=m.apply(audit,tmp_path/'fallback')
    value=m.read_artifact(result);proofs=m.verify_saved_full_audit(result)
    assert value['layers']['3']['blocked']==value['layers']['4']['blocked']==0
    assert len(value['layers']['3']['results'])==12
    assert len(value['layers']['4']['results'])==11
    assert value['meta'][m.KEY] and proofs
    assert audit.read_bytes()==original
