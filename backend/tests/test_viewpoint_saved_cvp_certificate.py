from copy import deepcopy
from pathlib import Path
import json
import pytest

from backend.pipeline import viewpoint_resolution_runtime as runtime
from backend.pipeline import viewpoint_saved_cvp_certificate as certificate
from backend.pipeline.source_reconciliation_runner import seal, binding
from backend.pipeline.viewpoint_validation_context import ValidationContext, ValidationLimits
from backend.api.canonical_repository.viewpoint_foundation import sha256_json


def historical():
    ctx=ValidationContext('test saved transcript', limits=ValidationLimits(8*1024**2,5))
    ctx.depth=1
    return ctx


def test_sink_is_explicit_and_requires_bounded_historical_owner():
    with pytest.raises(ValueError):
        with runtime.saved_artifact_validation(): pass
    with ValidationContext('not historical',limits=ValidationLimits(10000,5)):
        with pytest.raises(ValueError):
            with runtime.saved_artifact_validation(): pass
    with historical(), runtime.saved_artifact_validation():
        with pytest.raises(ValueError):
            with runtime.saved_artifact_validation(): pass
    assert runtime.validating_saved_artifacts() is False


@pytest.mark.parametrize('sealed',[False,True])
def test_only_legacy_configuration_may_lack_logical_seal(tmp_path,sealed):
    path=tmp_path/'campaign.json';value={'cvp_policy':{'path':'policy','file_sha256':'a'*64}}
    runtime.write_immutable(path,seal(value) if sealed else value)
    with historical() as ctx:
        assert certificate._configuration(ctx,binding(path))==value
        if not sealed:
            with pytest.raises(ValueError,match='seal'):certificate._artifact(ctx,binding(path))


def test_stated_legacy_configuration_seal_is_never_ignored(tmp_path):
    path=tmp_path/'campaign.json'
    runtime.write_immutable(path,{'cvp_policy':{},'artifact_sha256':'not valid'})
    with historical() as ctx:
        with pytest.raises(ValueError,match='seal'):certificate._configuration(ctx,binding(path))


def test_saved_sink_never_publishes_mutates_or_hides_immutable_drift(tmp_path):
    path=tmp_path/'saved.json';runtime.write_immutable(path,{'original':True})
    old=path.read_bytes()
    with historical(), runtime.saved_artifact_validation():
        runtime.write_immutable(path,{'original':True})
        runtime.write_derived(tmp_path/'missing-derived.json',{'ignored':'runtime only'})
        with pytest.raises(ValueError): runtime.write_immutable(path,{'original':False})
        with pytest.raises(ValueError): runtime.write_immutable(path,{'original':True},require_new=True)
        with pytest.raises(FileNotFoundError): runtime.write_immutable(tmp_path/'missing'/'new.json',{})
    assert path.read_bytes()==old
    assert sorted(p.name for p in tmp_path.iterdir())==['saved.json']


def test_missing_raw_stops_before_adapter_encoding_identity_generate_or_directory(tmp_path):
    class Forbidden:
        def __getattr__(self,name): raise AssertionError('no adapter side effect: '+name)
    with historical(), runtime.saved_artifact_validation():
        with pytest.raises(ValueError,match='no invocation/fallback'):
            runtime.call_model(Forbidden(),{},tmp_path/'missing'/'raw.json')
    assert list(tmp_path.iterdir())==[]


def test_actual_runner_saved_reconstruction_ignores_unbounded_execution_history(tmp_path):
    from backend.tests.test_viewpoint_batch_resolution import _claim,_proposal,_StubAdapter,ROCK_STATEMENT
    from backend.pipeline.viewpoint_batch_resolution_runner import run_batch
    proposal=_proposal().model_dump(mode='json')
    review={'schema_version':'wang_canonical_viewpoint_review_v1',
        'proposal_sha256':sha256_json(proposal),'change_reviews':[
            {'claim_id':'C1','component_index':i,'decision':'pass','finding_codes':[], 'reason':'成立'} for i in (0,1)],
        'novelty_review':{'status':'pass','missed_claim_ids':[],'reason':'无遗漏'}}
    p,r=_StubAdapter(proposal),_StubAdapter(review)
    kwargs=dict(batch_id='CVB-test-001',scope_label='matt16-13-20',claims=[_claim('C1',ROCK_STATEMENT)],
        registry_context=[{'viewpoint_revision_id':'CVR-1'}],pending_candidates=[],output_dir=tmp_path,
        proposer=p,reviewer=r)
    report=run_batch(**kwargs)
    # Invalid JSON and larger than the owning IO budget; never parsed/hashed.
    (tmp_path/'measurements.json').write_bytes(b'invalid historical measurements'*400000)
    old={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    with historical() as ctx, runtime.saved_artifact_validation():
        again=run_batch(**kwargs)
    assert again['artifact_sha256']==report['artifact_sha256']
    assert p.calls==r.calls==1
    assert all(p.read_bytes()==old[p.name] for p in tmp_path.iterdir())
    assert not any(p.name in {'measurements.json','batch-run.json','current-state.json'} for p in ctx.files)


@pytest.fixture
def small_certificate(tmp_path,monkeypatch):
    inputs={}
    for name in ('packet','configuration','package','decision_time','code'):
        path=tmp_path/(name+'.json');runtime.write_immutable(path,seal({'kind':name}))
        inputs[name]=binding(path)
    monkeypatch.setattr(certificate,'implementation_paths',lambda:[Path(inputs['code']['path'])])
    directory=tmp_path/'review';directory.mkdir()
    for name in ('batch-packet.json','raw-proposal.json','raw-review.json'):
        runtime.write_immutable(directory/name,{'saved':True})
    declaration={k:inputs[k] for k in ('packet','configuration','package','decision_time')}
    declaration['review_directory']=str(directory)
    value=dict(schema_version=certificate.SCHEMA,status='saved_review_verified_not_registry_admission',
        declaration=declaration,declaration_sha256=sha256_json(declaration),package={'approved_package':'exact'},
        package_sha256=sha256_json({'approved_package':'exact'}),runtime_identity=certificate.runtime_identity(),
        model_executions={'proposal':{'artifact_sha256':'a'*64},'review':{'artifact_sha256':'b'*64}},
        implementation_paths=[inputs['code']['path']],dependencies=list(inputs.values())+[
            binding(directory/n) for n in ('batch-packet.json','raw-proposal.json','raw-review.json')],
        absent_dependencies=[str(directory/'raw-reconsideration.json')],provider_calls=0,registry_writes=0,
        sql_admission_granted=False,independent_audit_complete=False)
    return value,declaration


def publish(tmp_path,value):
    path=tmp_path/'certificate.json';runtime.write_immutable(path,seal(value))
    return binding(path)


def test_cold_consumer_never_runs_historical_models_and_never_grants_sql(tmp_path,small_certificate,monkeypatch):
    from backend.pipeline import viewpoint_batch_resolution_runner as runner
    monkeypatch.setattr(runner,'run_batch',lambda **kw:pytest.fail('no reconstruction at consumption'))
    value,declaration=small_certificate
    result,metrics=certificate.consume_saved_review(publish(tmp_path,value),declaration=declaration)
    assert result['package']==value['package']
    assert result['sql_admission_granted'] is result['independent_audit_complete'] is False
    assert metrics['proof_runs']==metrics['proof_reuses']==0
    assert metrics['json_parses']==1


@pytest.mark.parametrize('drift',['declaration','package','source','code','raw','new_stage',
    'missing_dependency','duplicate_dependency','missing_model','runtime','sql_approval','audit_approval','seal'])
def test_certificate_rejects_dependency_and_authority_drift(tmp_path,small_certificate,drift):
    value,declaration=deepcopy(small_certificate)
    if drift=='declaration': declaration['review_directory']=str(tmp_path/'foreign')
    if drift=='package': value['package']['approved_package']='not reviewed'
    if drift in {'source','code','raw'}:
        item=value['dependencies'][{'source':0,'code':4,'raw':6}[drift]]
        Path(item['path']).write_text('changed')
    if drift=='new_stage': Path(value['absent_dependencies'][0]).write_text('{}')
    if drift=='missing_dependency':value['dependencies'].pop(6)
    if drift=='duplicate_dependency':value['dependencies'].append(value['dependencies'][0])
    if drift=='missing_model':value['model_executions'].pop('proposal')
    if drift=='runtime':value['runtime_identity']={'python':'old'}
    if drift=='sql_approval':value['sql_admission_granted']=True
    if drift=='audit_approval':value['independent_audit_complete']=True
    pin=publish(tmp_path,value)
    if drift=='seal':
        path=Path(pin['path']);saved=json.loads(path.read_text());saved['artifact_sha256']='0'*64
        path.write_text(json.dumps(saved));pin=binding(path)
    with pytest.raises(ValueError):certificate.consume_saved_review(pin,declaration=declaration)
