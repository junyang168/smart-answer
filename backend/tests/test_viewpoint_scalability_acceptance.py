from copy import deepcopy
import json

import pytest

from backend.pipeline import viewpoint_scalability_acceptance as m
from backend.api.canonical_repository.viewpoint_foundation import sha256_json


@pytest.fixture
def report(tmp_path, monkeypatch):
    # Synthetic measurements test ONLY the gate, not performance of production.
    monkeypatch.setattr(m,'PROJECT',tmp_path)
    for name in m.IMPLEMENTATION:
        p=tmp_path/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_text('test implementation')
    driver=tmp_path/'driver.py'; driver.write_text('# test driver, never production acceptance')
    measured=tmp_path/'synthetic-measurement.json'
    results=[]
    for ni,n in enumerate(m.SCALES):
        for hi,h in enumerate(m.SCALES):
            for phase in m.PHASES:
                for mode in ('cold','warm'):
                    results.append(dict(phase=phase,mode=mode,unrelated_records_scale=n,history_scale=h,
                        process_id=100+ni*3+hi,model_calls=0,registry_writes=0,
                        full_history_snapshot_parses=0,all_active_payload_reads=0,full_rebuild_fallbacks=0,
                        owned_runtime_startups=1 if mode=='cold' else 0,
                        file_io_bytes=1000,sql_payload_rows=10,proof_rebuilds=0,peak_rss_bytes=1024**2,
                        wall_seconds=0.1,affected_readset_sha256=sha256_json(phase),
                        reference_result_sha256=sha256_json({'complete_result':phase}),
                        result_sha256=sha256_json({'complete_result':phase})))
    bind=lambda p:dict(path=str(p),file_sha256=m.file_sha256(p))
    value=dict(schema_version=m.SCHEMA,status='measured_pass',production_execution_performed=False,
        implementation_bindings=[bind(tmp_path/name) for name in m.IMPLEMENTATION],
        benchmark_driver=bind(driver),results=results)
    path=tmp_path/'test-acceptance.json'
    def save(changed=None):
        body=deepcopy(value if changed is None else changed)
        measured.write_text(json.dumps({'results':body['results']}))
        body['measurement_report']=bind(measured)
        path.write_text(json.dumps({**body,'artifact_sha256':sha256_json(body)}))
        return path
    return value,save


def test_gate_accepts_complete_synthetic_shape_only(report):
    value,save=report
    receipt=m.require_scalability_acceptance(save())
    assert receipt['measurement_points']==4 and receipt['status']=='measured_pass'


@pytest.mark.parametrize('mutation', [
    'warm_only','missing_scale','duplicate','time','io','rss','rows_growth','bytes_growth',
    'history_rebuild','all_sql','fallback','changed_target','missing_metric','double_start',
    'split_runtime','missing_code','mutation','driver_drift','code_drift','measurement_drift','seal',
    'changed_result','missing_reference','cold_warm_result','extra_growth'])
def test_gate_rejects_missing_stale_or_non_scalable_measurements(report,mutation):
    value,save=report
    if mutation=='warm_only':value['results']=[r for r in value['results'] if r['mode']=='warm']
    elif mutation=='missing_scale':value['results'].pop()
    elif mutation=='duplicate':value['results'][-1]=deepcopy(value['results'][0])
    elif mutation=='missing_code':value['implementation_bindings'].pop()
    elif mutation=='extra_growth':
        value['results'].append({**value['results'][0],'history_scale':10})
    else:
        row=value['results'][-1]
        changes={'time':('wall_seconds',5.01),'io':('file_io_bytes',64*1024**2+1),
            'rss':('peak_rss_bytes',513*1024**2),'rows_growth':('sql_payload_rows',11),
            'bytes_growth':('file_io_bytes',1001),'history_rebuild':('full_history_snapshot_parses',1),
            'all_sql':('all_active_payload_reads',1),'fallback':('full_rebuild_fallbacks',1),
            'changed_target':('affected_readset_sha256','0'*64),
            'double_start':('owned_runtime_startups',2),'mutation':('registry_writes',1)}
        if mutation in changes:
            key,new=changes[mutation];row[key]=new
        elif mutation=='missing_metric':row.pop('sql_payload_rows')
        elif mutation=='split_runtime':row['process_id']=101
        elif mutation=='changed_result':row['result_sha256']='0'*64
        elif mutation=='missing_reference':row.pop('reference_result_sha256')
        elif mutation=='cold_warm_result':
            row['result_sha256']=row['reference_result_sha256']='0'*64
    path=save(value)
    if mutation=='driver_drift':
        from pathlib import Path
        Path(value['benchmark_driver']['path']).write_text('# changed')
    elif mutation=='code_drift':
        from pathlib import Path
        Path(value['implementation_bindings'][0]['path']).write_text('# changed')
    elif mutation=='measurement_drift':
        (path.parent/'synthetic-measurement.json').write_text('{"results":[]}')
    elif mutation=='seal':path.write_text('{}')
    with pytest.raises(ValueError):m.require_scalability_acceptance(path)


def test_absent_acceptance_stops_before_old_preflight_backup_or_process(tmp_path, monkeypatch):
    # The obsolete orchestration is no longer a supported solution.
    # Current content validators and SQL action tests carry its safety rules.
    from backend.pipeline import viewpoint_cvp_recovery_executor as worker
    with pytest.raises(RuntimeError,match='runtime is retired'):
        worker.start_prepared_campaign(None,None,None,None,None,None,None)


def test_relative_path_cannot_select_other_acceptance():
    with pytest.raises(ValueError,match='absolute'):
        m.require_scalability_acceptance('elsewhere.json')
