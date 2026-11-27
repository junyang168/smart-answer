"""Real OS processes prove cross-source concurrency and legacy exclusion."""
from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from backend.pipeline import source_execution_locks as locks


def request(sources=('S1',), writes=('evidence_steps/E1',)):
    return SimpleNamespace(source_bindings=dict.fromkeys(sources), target_keys=[],
                           permissions=[SimpleNamespace(key=k) for k in writes])


@pytest.fixture
def lock_root(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_BASE_DIR', str(tmp_path))
    monkeypatch.delenv('WANG_CVP_RECOVERY_ROOT', raising=False)
    return tmp_path


PROBE = '''
import json,sys,fcntl
from pathlib import Path
from types import SimpleNamespace
from contextlib import ExitStack
from backend.pipeline import source_execution_locks as l
v=json.loads(sys.argv[1])
try:
 with ExitStack() as stack:
  if v['mode']=='legacy':
   for path in l.gate_paths():
    path.parent.mkdir(parents=True,exist_ok=True)
    f=stack.enter_context(path.open('a+'));fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
  else:
   stack.enter_context(l.shared_maintenance_gates())
   if v['mode']=='claim':
    r=SimpleNamespace(source_bindings=dict.fromkeys(v['sources']),target_keys=[],
                      permissions=[SimpleNamespace(key=k) for k in v['writes']])
    stack.enter_context(l.claim_execution_locks(r))
   else:stack.enter_context(l.source_write_locks(v['sources'],v['writes']))
  print('acquired',flush=True)
  if v.get('hold'):sys.stdin.readline()
except BlockingIOError:print('blocked',flush=True)
'''


def probe(mode, sources=('S2',), writes=('evidence_steps/E2',)):
    value=dict(mode=mode,sources=sources,writes=writes)
    result=subprocess.run([sys.executable,'-c',PROBE,json.dumps(value)],capture_output=True,
                          text=True,timeout=10,check=True)
    return result.stdout.strip()


def test_cross_sermon_batch_reads_both_sources_and_only_blocks_overlapping_writes(lock_root):
    with locks.shared_maintenance_gates(), locks.claim_execution_locks(request(('S1','S2'))):
        assert probe('claim',('S3',))=='acquired'
        assert probe('claim',('S1','S2'))=='blocked'  # Existing same-own-source Update order.
        assert probe('claim',('S3',),('evidence_steps/E1',))=='blocked'
        assert probe('migration',('S2',))=='blocked'  # Replaces a source being read.
        assert probe('migration',('S3',))=='acquired'
    assert probe('migration',('S2',))=='acquired'


def test_source_replacement_blocks_its_readers_and_shared_evidence_writer(lock_root):
    with locks.shared_maintenance_gates(), locks.source_write_locks(['S2'],['evidence_steps/E2']):
        assert probe('claim',('S1',),('evidence_steps/E1',))=='acquired'
        assert probe('claim',('S2',),('evidence_steps/E3',))=='blocked'
        assert probe('claim',('S1',),('evidence_steps/E2',))=='blocked'
        assert probe('migration',('S3',),('evidence_steps/E3',))=='acquired'


def test_legacy_exclusive_workers_remain_mutually_exclusive_with_new_gates(lock_root):
    with locks.shared_maintenance_gates():
        assert probe('legacy')=='blocked'
    process=subprocess.Popen([sys.executable,'-c',PROBE,json.dumps(dict(mode='legacy',hold=True))],
                             stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    try:
        assert process.stdout.readline().strip()=='acquired'
        with pytest.raises(BlockingIOError):
            with locks.shared_maintenance_gates():pass
    finally:
        process.communicate('',timeout=10)
    assert probe('claim')=='acquired'


def test_partial_multi_source_acquisition_releases_its_prior_handles(lock_root):
    with locks.source_write_locks(['S2']):
        with pytest.raises(BlockingIOError):
            with locks.source_write_locks(['S1','S2']):pass
        assert probe('migration',('S1',))=='acquired'


def test_claim_worker_requires_all_bound_source_and_write_descriptors(lock_root):
    r=request(('S1','S2'))
    with locks.claim_execution_locks(r) as handles:
        fds=[h.fileno() for h in handles]
        locks.verify_claim_locks(fds,r)
        with pytest.raises(ValueError,match='incomplete'):
            locks.verify_claim_locks(fds[:-1],r)
        with pytest.raises(ValueError,match='identity drift'):
            locks.verify_claim_locks(list(reversed(fds)),r)
        with pytest.raises(ValueError):
            locks.verify_claim_locks(fds,request(('S1','S3')))


def test_inherited_scope_outlives_controller_and_excludes_new_writers(lock_root):
    r=request(('S1','S2'))
    with ExitStack() as controller:
        gates=controller.enter_context(locks.shared_maintenance_gates())
        handles=controller.enter_context(locks.claim_execution_locks(r))
        fds=[h.fileno() for h in (*gates,*handles)]
        code='''import sys,json
from types import SimpleNamespace
from backend.pipeline import source_execution_locks as l
fds=json.loads(sys.argv[1]);r=SimpleNamespace(source_bindings={'S1':None,'S2':None},target_keys=[],permissions=[SimpleNamespace(key='evidence_steps/E1')])
l.verify_inherited(fds[:2],l.gate_paths(),exclusive=False)
l.verify_claim_locks(fds[2:],r)
print('holding',flush=True);sys.stdin.readline()
'''
        child=subprocess.Popen([sys.executable,'-c',code,json.dumps(fds)],pass_fds=tuple(fds),
                               stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        assert child.stdout.readline().strip()=='holding'
    try:
        assert probe('migration',('S2',))=='blocked'
        assert probe('claim',('S3',),('evidence_steps/E1',))=='blocked'
        assert probe('legacy')=='blocked'
        assert probe('claim',('S3',))=='acquired'
    finally:
        child.communicate('',timeout=10)
    assert probe('migration',('S2',))=='acquired'


def test_global_paths_do_not_depend_on_campaign_or_model_capacity_roots(lock_root,monkeypatch):
    a=locks.resource_paths(['S1'],['evidence_steps/E1'])
    monkeypatch.setenv('WKP_CLAIM_CAPACITY_CONFIG','/different/campaign/capacity.json')
    assert locks.resource_paths(['S1'],['evidence_steps/E1'])==a
    with pytest.raises(ValueError):locks.resource_paths([])
    with pytest.raises(ValueError):locks.resource_paths(['S1'],['bad-record-key'])


def test_recovery_combines_overlapping_original_requests_without_self_deadlock(lock_root):
    with locks.claim_scopes_locks(r for r in [request(),request(('S1','S2'),('evidence_steps/E2',))]):
        assert probe('claim',('S1',),('evidence_steps/E3',))=='blocked'
        assert probe('claim',('S3',),('evidence_steps/E1',))=='blocked'
        assert probe('claim',('S3',),('evidence_steps/E3',))=='acquired'


def test_readonly_claims_share_sources_without_acquiring_update_order_lanes(lock_root):
    reader=request(('S1','S2'),());reader.operation='Review'
    with locks.claim_execution_locks(reader):
        assert probe('claim',('S1',),('evidence_steps/E2',))=='acquired'
        assert probe('migration',('S2',))=='blocked'
