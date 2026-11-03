"""Actual coordinator admission: only different own sources may overlap."""
from contextlib import contextmanager
from pathlib import Path
import json
from types import SimpleNamespace

import pytest
from backend.pipeline import claim_management as cm, claim_management_batch as batch


@pytest.mark.parametrize('same_source', [True, False])
def test_coordinator_serializes_same_source_and_overlaps_different_sources(tmp_path, monkeypatch, same_source):
    from backend.api.canonical_repository import postgres_store as pg, viewpoint_production_safety as safety
    from backend.pipeline import source_reconciliation_runtime as runtime
    @contextmanager
    def locked(*args, **kwargs):
        with (tmp_path / ('lock-' + str(len(list(tmp_path.glob('lock-*')))))).open('a') as fd:
            yield fd
    monkeypatch.setattr(runtime, 'maintenance_lock', locked)
    monkeypatch.setattr(safety, 'exclusive_cvp_run_lock', locked)
    monkeypatch.setattr(batch.time, 'sleep', lambda _: None)
    monkeypatch.setenv('DATA_BASE_DIR', str(tmp_path))
    jobs = {key: (SimpleNamespace(target_keys=['claims/' + key], source_bindings={
        'S' if same_source else 'S-' + key: {}}, permissions=[
        cm.source.Permission(collection='claims', object_id=key, fields=['statement'])]), tmp_path / key)
        for key in ['a', 'b']}
    monkeypatch.setattr(batch, 'load', lambda *args: ({'max_workers': 2}, jobs))
    monkeypatch.setattr(batch, '_paragraph_results', lambda *args: {'all_actions_completed': True, 'created_keys': []})
    class Store:
        def read_claim_context(self, keys): return {keys[0]: {}}
        def claim_management_qualification(self, cid): return {'ready': True}
    monkeypatch.setattr(pg, 'PostgresKnowledgeStore', Store)
    processes, overlaps = [], []
    class Process:
        def __init__(self, command, **kwargs):
            self.key, self.pid, self.polls, self.live = command[-1], 1, 0, True
            processes.append(self)
            overlaps.append(sum(p.live for p in processes))
            (tmp_path / (cm.sha256_json(self.key) + '.result.json')).write_text(json.dumps({
                'result': {'status': 'completed'}}))
        def poll(self):
            self.polls += 1
            if self.polls <= 3: return None
            self.live = False
            return 0
    monkeypatch.setattr(batch.subprocess, 'Popen', Process)
    batch.run(tmp_path / 'manifest.json', tmp_path)
    assert len(processes) == 2
    assert max(overlaps) == (1 if same_source else 2)


@pytest.mark.parametrize('same_source',[True,False])
@pytest.mark.parametrize('as_string',[True,False])
def test_capacity_mode_uses_source_only_and_more_than_two_actions(tmp_path,monkeypatch,same_source,as_string):
    from backend.api.canonical_repository import postgres_store as pg,viewpoint_production_safety as safety
    from backend.pipeline import source_reconciliation_runtime as runtime
    @contextmanager
    def locked(*args,**kwargs):
        with (tmp_path / ('lock-'+str(len(list(tmp_path.glob('lock-*')))))).open('a') as fd: yield fd
    monkeypatch.setattr(runtime,'maintenance_lock',locked)
    monkeypatch.setattr(safety,'exclusive_cvp_run_lock',locked)
    monkeypatch.setattr(batch.time,'sleep',lambda _:None)
    monkeypatch.setenv('DATA_BASE_DIR',str(tmp_path))
    class Request(SimpleNamespace):
        def model_dump(self,**kwargs): return {'target_keys':self.target_keys}
    jobs={key:(Request(target_keys=['claims/'+key],source_bindings={'S' if same_source else 'S-'+key:{}},
        permissions=[SimpleNamespace(key='shared-cross-source-object')]),tmp_path/key) for key in 'abcd'}
    manifest=tmp_path/'manifest.json';manifest.write_text('{}')
    monkeypatch.setattr(batch,'load',lambda *a:({'max_workers':2},jobs))
    monkeypatch.setattr(batch,'_paragraph_results',lambda *a:{'all_actions_completed':True,'created_keys':[]})
    class Store:
        def get_cvp_group(self,key): return None
        def read_claim_context(self,keys): pytest.fail('cross-source W/R scope used for scheduling')
        def claim_management_qualification(self,cid): return {'ready':True}
    monkeypatch.setattr(pg,'PostgresKnowledgeStore',Store)
    processes=[];overlaps=[]
    class Process:
        def __init__(self,command,**kwargs):
            self.key=command[-1];self.pid=1;self.polls=0;self.live=True
            assert kwargs['env']['WKP_CLAIM_CAPACITY_CONFIG']
            assert kwargs['env']['WKP_CLAIM_SOURCE_LOCK_FDS']
            processes.append(self);overlaps.append(sum(p.live for p in processes))
            (tmp_path/(cm.sha256_json(self.key)+'.result.json')).write_text(json.dumps({'result':{'status':'completed'}}))
        def poll(self):
            self.polls+=1
            if self.polls<3:return None
            self.live=False;return 0
    monkeypatch.setattr(batch.subprocess,'Popen',Process)
    batch.run(str(manifest) if as_string else manifest,tmp_path,model_capacity=dict(root=str(tmp_path/'capacity'),action_window=4,spacing_seconds=0,cooldown_seconds=1))
    assert max(overlaps)==(1 if same_source else 4)


def test_refill_starts_next_source_without_waiting_for_slow_action(tmp_path,monkeypatch):
    from backend.api.canonical_repository import postgres_store as pg,viewpoint_production_safety as safety
    from backend.pipeline import source_reconciliation_runtime as runtime
    @contextmanager
    def locked(*args,**kwargs):
        with (tmp_path / ('lock-'+str(len(list(tmp_path.glob('lock-*')))))).open('a') as fd: yield fd
    monkeypatch.setattr(runtime,'maintenance_lock',locked)
    monkeypatch.setattr(safety,'exclusive_cvp_run_lock',locked)
    monkeypatch.setattr(batch.time,'sleep',lambda _:None)
    monkeypatch.setenv('DATA_BASE_DIR',str(tmp_path))
    class Request(SimpleNamespace):
        def model_dump(self,**kwargs):return {'target_keys':self.target_keys}
    def job(key):return (Request(target_keys=['claims/'+key],source_bindings={'S-'+key:{}},permissions=[]),tmp_path/key)
    original=tmp_path/'manifest.json';original.write_text('{}')
    extra=tmp_path/'extra/manifest.json';extra.parent.mkdir();extra.write_text('{}')
    monkeypatch.setattr(batch,'load',lambda path,*a,**k:({'max_workers':2},{'a':job('a')} if Path(path)==original else {'b':job('b')}))
    monkeypatch.setattr(batch,'_paragraph_results',lambda *a:{'all_actions_completed':True,'created_keys':[]})
    class Store:
        def get_cvp_group(self,key):return None
        def claim_management_qualification(self,cid):return {'ready':True}
    monkeypatch.setattr(pg,'PostgresKnowledgeStore',Store)
    processes=[]; overlapping=[]
    class Process:
        def __init__(self,command,**kwargs):
            self.key=command[-1];self.pid=1;self.polls=0;self.live=True
            overlapping.append(any(p.live for p in processes));processes.append(self)
            (tmp_path/(cm.sha256_json(self.key)+'.result.json')).write_text(json.dumps({'result':{'status':'completed'}}))
        def poll(self):
            self.polls+=1
            if self.polls<4:return None
            self.live=False;return 0
    monkeypatch.setattr(batch.subprocess,'Popen',Process)
    delivered=[]
    def refill(occupied,room,jobs):
        if not delivered:
            delivered.append(True)
            return [batch.binding(extra)]
        return []
    batch.run(original,tmp_path,model_capacity=dict(root=str(tmp_path/'capacity'),action_window=4,spacing_seconds=0,cooldown_seconds=1),refill=refill)
    assert len(processes)==2 and overlapping==[False,True]
    assert len(list((tmp_path/'dispatch-inputs').glob('*.json')))==1


def test_refill_and_restart_pass_the_bound_manifest_to_real_children(tmp_path,monkeypatch):
    import subprocess,sys
    from backend.api.canonical_repository import postgres_store as pg,viewpoint_production_safety as safety
    from backend.pipeline import source_reconciliation_runtime as runtime
    @contextmanager
    def locked(*args,**kwargs):
        with (tmp_path/('lock-'+str(len(list(tmp_path.glob('lock-*')))))).open('a') as fd:yield fd
    monkeypatch.setattr(runtime,'maintenance_lock',locked)
    monkeypatch.setattr(safety,'exclusive_cvp_run_lock',locked)
    monkeypatch.setenv('DATA_BASE_DIR',str(tmp_path))
    original=tmp_path/'manifest.json';extra=tmp_path/'extra.json'
    original.write_text(json.dumps({'jobs':['a'],'max_workers':2}))
    extra.write_text(json.dumps({'jobs':['b'],'max_workers':2}))
    class Request(SimpleNamespace):
        def model_dump(self,**kwargs):return {'target_keys':self.target_keys}
    def load(path,*args,**kwargs):
        plan=json.loads(Path(path).read_text())
        return plan,{key:(Request(target_keys=['claims/'+key],source_bindings={'S-'+key:{}},permissions=[]),tmp_path/key) for key in plan['jobs']}
    monkeypatch.setattr(batch,'load',load)
    monkeypatch.setattr(batch,'_paragraph_results',lambda *a:{'all_actions_completed':True,'created_keys':[]})
    class Store:
        def get_cvp_group(self,key):return None
        def claim_management_qualification(self,cid):return {'ready':True}
    monkeypatch.setattr(pg,'PostgresKnowledgeStore',Store)
    # Execute a real child at the CLI boundary. It must find its job in the
    # actual supplied manifest; the old fake Process never checked this.
    probe=tmp_path/'child.py'
    probe.write_text('''import sys,json,hashlib
from pathlib import Path
args=sys.argv
manifest=Path(args[args.index('--manifest')+1]);key=args[args.index('--job')+1]
root=Path(args[args.index('--root')+1])
assert key in json.loads(manifest.read_text())['jobs'], 'worker cannot find refilled job'
token=hashlib.sha256(json.dumps(key,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
(root/(token+'.result.json')).write_text(json.dumps({'result':{'status':'completed'},'manifest':str(manifest)}))
''')
    popen=subprocess.Popen;commands=[]
    def launch(command,**kwargs):
        commands.append(command)
        return popen([sys.executable,str(probe),*command[3:]],**kwargs)
    monkeypatch.setattr(batch.subprocess,'Popen',launch)
    delivered=[]
    def refill(*args):
        if delivered:return []
        delivered.append(True);return [batch.binding(extra)]
    config=dict(root=str(tmp_path/'capacity'),action_window=4,spacing_seconds=0,cooldown_seconds=1)
    for callback in (refill,None):
        batch.run(original,tmp_path,model_capacity=config,refill=callback)
        results=json.loads((tmp_path/'result.json').read_text())['results']
        assert set(results)=={'a','b'}
        assert all(v['result']['status']=='completed' for v in results.values())
        assert results['a']['manifest']==str(original)
        assert results['b']['manifest']==str(extra)
    assert len(commands)==4
