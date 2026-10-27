"""Waiting for shared resource contention never holds the other lock or calls models."""
import fcntl
import threading
from pathlib import Path

import pytest
from backend.pipeline import source_reconciliation_runtime as runtime
from backend.api.canonical_repository.viewpoint_production_safety import CvpProductionBlocked


def test_wait_releases_first_lock_before_waiting_for_second(tmp_path,monkeypatch):
    monkeypatch.setenv('DATA_BASE_DIR',str(tmp_path));monkeypatch.delenv('WANG_CVP_RECOVERY_ROOT',raising=False)
    parent=tmp_path/'wang-knowledge-platform/staging/cvp-production-357'
    maintenance=parent/'immediate-recovery-20261002-v1/recovery.lock';maintenance.parent.mkdir(parents=True);maintenance.touch()
    registry=parent/'registry-cvp.lock'
    waiting=threading.Event();release=threading.Event();entered=[];errors=[]
    def pause(_):
        waiting.set();assert release.wait(5)
    import time
    monkeypatch.setattr(time,'sleep',pause)
    def work():
        try:
            with runtime.wait_for_claim_operation_locks():entered.append(True)
        except BaseException as exc:errors.append(exc)
    with registry.open('a+') as occupied:
        fcntl.flock(occupied,fcntl.LOCK_EX|fcntl.LOCK_NB)
        thread=threading.Thread(target=work);thread.start()
        assert waiting.wait(5)
        with maintenance.open('r+') as available:
            fcntl.flock(available,fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert entered==[]
    release.set();thread.join(5)
    assert not thread.is_alive() and not errors and entered==[True]


def test_non_contention_rejection_is_not_retried(monkeypatch):
    from contextlib import contextmanager
    @contextmanager
    def rejected():
        raise CvpProductionBlocked(['different actual authority problem'])
        yield
    monkeypatch.setattr(runtime,'maintenance_lock',rejected)
    with pytest.raises(CvpProductionBlocked,match='different actual authority problem'):
        with runtime.wait_for_claim_operation_locks():pytest.fail('must not enter')
