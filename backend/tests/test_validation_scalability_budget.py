from pathlib import Path

import pytest

from backend.pipeline import viewpoint_validation_context as v


@pytest.mark.parametrize('io, seconds', [(0, 1), (True, 1), (1, 0), (1, float('inf')),
                                        (1, float('nan')), (1, True), (1.5, 1)])
def test_invalid_limits_cannot_disable_guard(io, seconds):
    with pytest.raises(ValueError, match='positive finite'):
        v.ValidationLimits(io, seconds)


def test_oversized_history_refused_before_any_open_parse_or_validator(tmp_path, monkeypatch):
    path=tmp_path/'before.json'; path.write_bytes(b'"'+b'x'*1000+b'"')
    calls=[]
    @v.validated_history(bound_files_only=True)
    def load(path):
        calls.append(1)
        return v.active_context().read(path)
    # The decorator also pins its small code dependency; forbid only the large
    # snapshot, not the source file. Source/schema pins must not be skipped.
    original=Path.open
    def open_checked(self, *args, **kwargs):
        if self==path:pytest.fail('oversized snapshot opened before budget check')
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Path,'open',open_checked)
    limit=Path(__file__).stat().st_size+200
    with pytest.raises(v.ValidationBudgetExceeded) as failed:
        with v.ValidationContext('cold admission', limits=v.ValidationLimits(limit, 5)) as ctx:
            load(path)
    assert failed.value.detail['automatic_fallback'] is False
    assert failed.value.detail['operation']=='authenticate '+str(path)
    assert calls==[1]
    assert ctx.metrics['json_parses']==ctx.metrics['proof_runs']==0
    assert not ctx.results and not ctx.documents and ctx.closed


def test_authentication_parse_and_close_all_count_toward_same_budget(tmp_path):
    path=tmp_path/'proof.json'; path.write_bytes(b'{}')
    with v.ValidationContext('one exact proof', limits=v.ValidationLimits(6, 5)) as ctx:
        assert ctx.read(path)=={}
        assert ctx.read(path)=={}  # no second parse, but closing SHA mandatory
    assert ctx.metrics['file_io_bytes']==6 and ctx.metrics['hashed_bytes']==4
    assert ctx.metrics['parsed_bytes']==2 and ctx.metrics['json_parses']==1
    with pytest.raises(v.ValidationBudgetExceeded) as failed:
        with v.ValidationContext('closing boundary', limits=v.ValidationLimits(5, 5)) as ctx:
            ctx.read(path)
    assert failed.value.detail['operation'].startswith('authenticate ')
    assert ctx.closed and not ctx.results and not ctx.documents


def test_deadline_stops_without_new_reads_or_fallback(tmp_path, monkeypatch):
    now=[0.0]; monkeypatch.setattr(v.time,'perf_counter',lambda:now[0])
    path=tmp_path/'proof.json'; path.write_text('{}')
    with pytest.raises(v.ValidationBudgetExceeded) as failed:
        with v.ValidationContext('deadline', limits=v.ValidationLimits(100, 5)) as ctx:
            now[0]=5.001
            ctx.read(path)
    assert failed.value.detail['metric']=='wall_seconds'
    assert ctx.metrics['file_io_bytes']==ctx.metrics['json_parses']==0


def test_slow_validator_cannot_store_a_successful_receipt(tmp_path, monkeypatch):
    now=[0.0]; monkeypatch.setattr(v.time,'perf_counter',lambda:now[0])
    @v.validated_history(bound_files_only=True)
    def slow():
        now[0]=6.0
        return {'status':'complete'}
    with pytest.raises(v.ValidationBudgetExceeded):
        with v.ValidationContext('slow local build', limits=v.ValidationLimits(1024**2, 5)) as ctx:
            slow()
    assert not ctx.results and ctx.metrics['proof_runs']==0


def test_runtime_budget_cannot_be_disabled_by_nested_consumer():
    strict=v.ValidationLimits(100, 5)
    with v.validation_runtime(limits=strict):
        with v.ValidationContext('inherited') as ctx:
            assert ctx.limits==strict
        with pytest.raises(ValueError, match='cannot override'):
            v.ValidationContext('unbounded fallback', limits=v.ValidationLimits(100000, 100))


def test_each_phase_has_its_own_budget_not_history_length(tmp_path):
    path=tmp_path/'proof.json'; path.write_text('{}')
    with v.validation_runtime(limits=v.ValidationLimits(6, 5)) as pool:
        for i in range(100):
            with v.ValidationContext(f'group-{i}') as ctx:
                ctx.read(path)
        assert len(pool.phase_metrics)==100
        assert pool.phase_metrics[0]['file_io_bytes']==6
        assert all(m['file_io_bytes']==4 and m['json_parses']==0 for m in pool.phase_metrics[1:])


def test_failure_preserves_actual_file_and_reports_mechanical_not_semantic(tmp_path):
    path=tmp_path/'old-raw.json'; path.write_bytes(b'"'+b'x'*100+b'"'); original=path.read_bytes()
    with pytest.raises(v.ValidationBudgetExceeded) as failed:
        with v.ValidationContext('stored provider raw', limits=v.ValidationLimits(10, 5)):
            v.active_context().read(path)
    assert path.read_bytes()==original
    assert failed.value.detail['status']=='scalability_budget_exceeded'
    assert failed.value.detail['automatic_fallback'] is False
    assert failed.value.detail['model_calls']==failed.value.detail['registry_writes']==0


def test_guard_still_reads_and_verifies_real_sha_not_file_timestamp(tmp_path):
    path=tmp_path/'proof.json'; path.write_text('{"value":1}')
    with pytest.raises(ValueError, match='input drift'):
        with v.ValidationContext('same-size drift', limits=v.ValidationLimits(1000, 5)):
            v.active_context().read(path)
            path.write_text('{"value":2}')
