import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from backend.pipeline.subscription_process import run
from backend.pipeline.claim_management_batch import selection, status


def command(code):
    return [sys.executable, '-c', code]


def invoke(tmp_path, code, **kwargs):
    return run(command(code), input='input', env=dict(os.environ), timeout=5,
               directory=tmp_path, **kwargs)


def test_completed_response_replays_without_spawn(tmp_path, monkeypatch):
    code = 'import sys; print("answer"); print("diagnostic", file=sys.stderr)'
    first = invoke(tmp_path, code)
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('replay spawned a call'))
    saved = invoke(tmp_path, code, replay_only=True)
    assert saved.stdout == first.stdout == 'answer\n'
    assert saved.stderr == 'diagnostic\n'


@pytest.mark.parametrize('drift', ['command', 'input', 'stdout', 'receipt', 'response'])
def test_replay_refuses_drift(tmp_path, drift, monkeypatch):
    code = 'print("answer")'
    invoke(tmp_path, code)
    kwargs = dict(input='input', env=dict(os.environ), timeout=5, directory=tmp_path, replay_only=True)
    cmd = command(code)
    if drift == 'command': cmd = command('print("other")')
    if drift == 'input': kwargs['input'] = 'changed'
    if drift == 'stdout': (tmp_path/'stdout.log').write_text('changed')
    if drift == 'receipt': (tmp_path/'exit.json').unlink()
    if drift == 'response': (tmp_path/'response.json').write_text('{}')
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('invalid replay spawned'))
    with pytest.raises(ValueError): run(cmd, **kwargs)


def test_timeout_retains_partial_output_and_cannot_retry(tmp_path):
    cmd = command('import time; print("partial", flush=True); time.sleep(5)')
    with pytest.raises(subprocess.TimeoutExpired):
        run(cmd, input='', env=dict(os.environ), timeout=.2, directory=tmp_path)
    assert (tmp_path/'stdout.log').read_text() == 'partial\n'
    assert json.loads((tmp_path/'exit.json').read_text())['error_type'] == 'TimeoutExpired'
    with pytest.raises(ValueError, match='no retry'):
        run(cmd, input='', env=dict(os.environ), timeout=5, directory=tmp_path)


def test_failed_exit_retained_never_retried(tmp_path):
    code = 'import sys; print("quota",file=sys.stderr); sys.exit(3)'
    result = invoke(tmp_path, code)
    assert result.returncode == 3 and result.stderr == 'quota\n'
    with pytest.raises(ValueError): invoke(tmp_path, code)


def test_legacy_unknown_cannot_become_new_invocation(tmp_path):
    with pytest.raises(ValueError, match='no retry'):
        invoke(tmp_path, 'print("must not run")', replay_only=True)
    assert not (tmp_path/'invocation.json').exists()


def test_scheduler_continues_independent_work_and_serializes_conflicts():
    scopes = {'a': ({'1','2'},{'1'}), 'b': ({'1','3'},{'3'}), 'c': ({'4'},{'4'})}
    assert selection(['b','c'], ['a'], scopes, 2) == ['c']
    assert selection(['b'], [], scopes, 2) == ['b']
    assert selection(['c'], ['a','b'], scopes, 2) == []


def test_status_does_not_treat_saved_running_as_liveness(tmp_path):
    (tmp_path/'status.json').write_text(json.dumps({'status':'running', 'controller_pid':os.getpid()}))
    assert status(tmp_path)['live_lock_holder'] is False


def test_inherited_lock_survives_coordinator_descriptor_close(tmp_path):
    import fcntl
    path = tmp_path/'lock'
    first = path.open('a'); fcntl.flock(first, fcntl.LOCK_EX | fcntl.LOCK_NB)
    child = subprocess.Popen(command('import time; time.sleep(2)'), pass_fds=(first.fileno(),))
    first.close()
    try:
        with path.open('a') as probe:
            with pytest.raises(BlockingIOError): fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        child.terminate(); child.wait()
    with path.open('a') as probe: fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize('provider', ['codex', 'claude'])
def test_real_client_files_survive_runner_gap_and_replay(tmp_path, monkeypatch, provider):
    from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
    from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
    binary = tmp_path/'fake-cli'
    binary.write_text('#!' + sys.executable + '\n' + '''import sys,json,pathlib
sys.stdin.read()
if '--output-last-message' in sys.argv:
 pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text('{"ok": true}')
 print('provider returned')
else:
 print(json.dumps({'structured_output':{'ok':True}}))
''')
    binary.chmod(0o755)
    cls = CodexSubscriptionClient if provider == 'codex' else ClaudeSubscriptionClient
    client = cls(model='fake', executable=str(binary))
    client._authenticated = True
    client.call_artifacts = tmp_path/'transport'
    assert client.generate_json('sys', 'user', {'type':'object'}) == {'ok': True}
    client.replay_only = True
    monkeypatch.setattr(subprocess,'run',lambda *a,**k: pytest.fail('replay invoked provider'))
    assert client.generate_json('sys', 'user', {'type':'object'}) == {'ok': True}


def test_partial_stream_survives_abrupt_parent_exit(tmp_path):
    # Kill the caller after its child has written, as in the actual incident.
    import signal
    import time
    caller = tmp_path/'caller.py'
    caller.write_text('''from backend.pipeline.subscription_process import run
import os,sys
run([sys.executable,'-c','import time; print("already on disk",flush=True); time.sleep(2)'],
 input='',env=dict(os.environ),timeout=5,directory=sys.argv[1])
''')
    root = tmp_path/'transport'
    parent = subprocess.Popen([sys.executable,str(caller),str(root)])
    try:
        for _ in range(100):
            if (root/'stdout.log').exists() and (root/'stdout.log').read_text(): break
            time.sleep(.02)
        else: pytest.fail('child output never reached disk')
        os.kill(parent.pid, signal.SIGKILL); parent.wait()
        assert (root/'stdout.log').read_text() == 'already on disk\n'
        assert not (root/'exit.json').exists()
        with pytest.raises(ValueError,match='no exit receipt'):
            run([sys.executable,'-c','import time; print("already on disk",flush=True); time.sleep(2)'],
                input='',env=dict(os.environ),timeout=5,directory=root)
    finally:
        if parent.poll() is None: parent.kill(); parent.wait()


def test_common_bound_call_recovers_returned_output_without_reservation_or_generation(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from pydantic import BaseModel
    from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
    from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
    from backend.pipeline.source_reconciliation_runner import Runner, read
    class Answer(BaseModel):
        ok: bool
    binary = tmp_path/'fake-cli'
    binary.write_text('#!' + sys.executable + '\nimport sys,pathlib\nsys.stdin.read()\npathlib.Path(sys.argv[sys.argv.index("--output-last-message")+1]).write_text(\'{"ok":true}\')\n')
    binary.chmod(0o755)
    client = CodexSubscriptionClient(model='fake',executable=str(binary));client._authenticated=True
    adapter = StructuredJsonReviewerAdapter(client=client,prompt='sys',response_model=Answer,schema_name='answer',compact_payload=True,shared_text_payload=True,shared_record_payload=True)
    adapter.max_request_bytes = 500000
    reserved=[]
    runner=Runner(tmp_path/'round',SimpleNamespace(before_model_call=lambda s:None,
                  model_call_reserved=lambda s,r: reserved.append(s)))
    runner.root=tmp_path/'round'
    raw=runner.call_bound('gpt',{'input':'complete'},adapter,expected=('fake','codex_subscription'))
    (runner.root/'gpt.raw.json').unlink()  # returned CLI, parent not yet saved common raw
    monkeypatch.setattr(subprocess,'run',lambda *a,**k: pytest.fail('recovery sent another call'))
    recovered=runner.call_bound('gpt',{'input':'complete'},adapter,expected=('fake','codex_subscription'))
    assert recovered['response']==raw['response']=={'ok':True}
    assert reserved==['gpt']
    assert read(runner.root/'gpt.reservation.json')['automatic_retry'] is False


@pytest.mark.parametrize('failed_first', [True, False])
def test_batch_records_each_exit_and_rechecks_current_qualification(tmp_path,monkeypatch,failed_first):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from backend.pipeline import claim_management_batch as batch
    from backend.pipeline import source_reconciliation_runtime as runtime
    from backend.api.canonical_repository import postgres_store as pg, viewpoint_production_safety as safety
    from backend.pipeline.claim_management import sha256_json
    @contextmanager
    def locked(*args,**kwargs):
        with (tmp_path/('lock'+str(len(list(tmp_path.glob('lock*')))))).open('a') as fd: yield fd
    monkeypatch.setattr(runtime,'maintenance_lock',locked)
    monkeypatch.setattr(safety,'exclusive_cvp_run_lock',locked)
    monkeypatch.setenv('DATA_BASE_DIR',str(tmp_path))
    requests={k:(SimpleNamespace(target_keys=['claims/'+k],permissions=[]),tmp_path/k) for k in ['a','b']}
    monkeypatch.setattr(batch,'load',lambda *a: ({'max_workers':2},requests))
    class Store:
        def read_claim_context(self,keys): return {tuple(keys[0]): {}}
        def claim_management_qualification(self,key): return {'ready':key=='b'}
    monkeypatch.setattr(pg,'PostgresKnowledgeStore',Store)
    started=[]
    class Process:
        def __init__(self,cmd,**kwargs):
            key=cmd[-1];self.key=key;self.pid=123;started.append(key)
            assert len(kwargs['pass_fds'])==3
            if not (key=='a' and failed_first):
                (tmp_path/(sha256_json(key)+'.result.json')).write_text(json.dumps({'result':{'status':'completed'}}))
        def poll(self): return 3 if self.key=='a' and failed_first else 0
    monkeypatch.setattr(batch.subprocess,'Popen',Process)
    batch.run(tmp_path/'manifest',tmp_path)
    report=json.loads((tmp_path/'result.json').read_text())
    assert started==['a','b']
    assert report['results']['b']['result']['status']=='completed'
    assert report['all_actions_completed'] is False  # current qualification, never old success alone
    assert report['current_qualifications']['a']['claims/a']['ready'] is False
