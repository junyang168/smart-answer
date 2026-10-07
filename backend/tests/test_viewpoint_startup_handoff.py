"""Local-only startup handoff: no provider, SQL, screen, or corpus mutation."""
import json
import os

import pytest

from backend.pipeline import viewpoint_startup_handoff as m
from backend.pipeline import viewpoint_scalability_acceptance as performance
from backend.pipeline import codex_subscription_preflight as runtime
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    monkeypatch.setattr(CodexSubscriptionClient, 'generate_json',
        lambda *a, **k: pytest.fail('startup tests must never call a provider'))


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    acceptance = {'path':'/pytest-only-acceptance.json', 'status':'measured_pass'}
    monkeypatch.setattr(performance, 'require_scalability_acceptance', lambda _: acceptance)
    output = tmp_path/'campaign'
    argv = ['python','-m','backend.pipeline.viewpoint_cvp_campaign_runner',
            '--output-root',str(output),'--validation-runtime']
    request = m.create(tmp_path/'attempt',output,argv,acceptance)
    gate = m.ProducerGate(request,output,[*argv[3:],'--startup-handoff',str(request)])
    evidence = tmp_path/'input.json'; evidence.write_text('actual fixed input')
    dump = tmp_path/'backup.dump'; dump.write_bytes(b'offline test backup')
    gate.ready({'status':'preflight_passed_no_model_calls','sequence':58,'group_key':'test'},
               [m.binding(evidence)],m.binding(dump))
    directory = request.parent
    # Existing runtime and command receipts are unsealed JSON, not new proofs.
    (directory/'codex-runtime-preflight.json').write_text(json.dumps({'test_runtime':True}))
    (directory/'backup-restore-list.json').write_text(json.dumps({
        'exit_code':0,'argv':['pg_restore','--list',str(dump)]}))
    consumed=[]
    monkeypatch.setattr(runtime,'consume_saved_runtime',lambda client,value:consumed.append(value))
    return request,gate,evidence,consumed


def publish_release(request,gate):
    m.release(request,gate.pid,m.binding(request.parent/'codex-runtime-preflight.json'),
              m.binding(request.parent/'backup-restore-list.json'))


def test_one_pid_one_owned_runtime_released_without_rebuilding(prepared):
    request,gate,_,calls = prepared
    ready=m.wait_ready(request,os.getpid(),lambda pid:pid==os.getpid())
    assert ready['owned_runtime_startups']==1
    publish_release(request,gate)
    from backend.pipeline.viewpoint_validation_context import validation_runtime, ValidationContext
    with validation_runtime() as owned_runtime:
        with ValidationContext('offline preflight') as before:
            assert before.pool is owned_runtime
        gate.wait_release(lambda:False,'pytest-only-codex')
        with ValidationContext('offline next phase') as after:
            assert after.pool is before.pool is owned_runtime
    receipt=m.wait_consumed(request,gate.pid,lambda pid:pid==gate.pid)
    assert gate.released
    assert calls==[{'test_runtime':True}]
    assert receipt['new_model_calls']==0 and receipt['Registry_writes']==0
    assert receipt['historical_rebuild_on_release'] is False
    with pytest.raises(ValueError):
        publish_release(request,gate)


@pytest.mark.parametrize('mutation',['pid','input','runtime','restore','request','approval','implementation'])
def test_drift_stops_before_release_consumption(prepared,monkeypatch,mutation):
    request,gate,evidence,calls=prepared
    publish_release(request,gate)
    directory=request.parent
    if mutation=='input': evidence.write_text('changed')
    elif mutation in {'runtime','restore'}:
        name='codex-runtime-preflight.json' if mutation=='runtime' else 'backup-restore-list.json'
        (directory/name).write_text('{}')
    elif mutation=='request': request.write_text('{}')
    elif mutation=='implementation':
        monkeypatch.setattr(performance,'require_scalability_acceptance',lambda _: {'changed':True})
    else:
        path=directory/'startup-release.json';value=m.small(path)
        value['producer_pid']+=1 if mutation=='pid' else 0
        if mutation=='approval': value['model_approval_granted']=True
        path.write_text(json.dumps(m.seal(value)))
    with pytest.raises((ValueError,KeyError)):
        gate.wait_release(lambda:False,'unused')
    assert calls==[] and not gate.released
    assert not (directory/'startup-release-consumed.json').exists()


@pytest.mark.parametrize('stop',['owner','abort','timeout'])
def test_missing_release_cannot_fall_back_to_another_producer(prepared,monkeypatch,stop):
    request,gate,_,calls=prepared
    if stop=='abort':m.abort(request,'preserved failure')
    if stop=='timeout':
        ticks=iter([0,11])
        monkeypatch.setattr(m.time,'monotonic',lambda:next(ticks))
    with pytest.raises(ValueError):
        gate.wait_release(lambda:stop=='owner','unused')
    assert calls==[] and not gate.released


@pytest.mark.parametrize('counter',['producer_pid','model_calls','Registry_writes','owned_runtime_startups'])
def test_boolean_execution_counters_are_not_measurements(prepared,counter):
    request,gate,_,_=prepared
    ready=m.small(request.parent/'startup-ready.json')
    ready[counter]=bool(ready[counter])
    with pytest.raises(ValueError):m.check_ready(request,ready,gate.pid)


def test_existing_owner_prohibits_same_request_restart(prepared):
    request,gate,_,_=prepared
    with pytest.raises(ValueError):
        m.ProducerGate(request,gate.output,[*gate.request['argv'][3:],'--startup-handoff',str(request)])


@pytest.mark.parametrize('argv',[[],['python'],['python','--output-root','/test'],
    ['python','-m','backend.pipeline.viewpoint_cvp_campaign_runner','--validation-runtime','--output-root'],
    ['python','-m','backend.pipeline.viewpoint_cvp_campaign_runner','--output-root','/test','--dry-run']])
def test_invalid_command_is_rejected_without_runtime_diagnostic(argv):
    with pytest.raises(ValueError):m.validate_command('/test',argv)


def test_dead_producer_is_not_a_resumed_campaign(prepared):
    request,gate,_,_=prepared
    with pytest.raises(ValueError,match='exited'):
        m.wait_ready(request,gate.pid,lambda _:False)


def test_expired_or_oversized_artifacts_stop(prepared):
    request,gate,_,_=prepared
    with pytest.raises(ValueError):m.wait_ready(request,gate.pid,lambda _:True,seconds=11)
    path=request.parent/'large.json';path.write_bytes(b' '*(m.MAX_BYTES+1))
    with pytest.raises(ValueError,match='bounded size'):m.small(path)


def test_bad_restore_is_never_a_release(prepared):
    request,gate,_,calls=prepared
    path=request.parent/'backup-restore-list.json'
    path.write_text(json.dumps({'exit_code':1,'argv':['pg_restore','--list','foreign']}))
    with pytest.raises(ValueError,match='restore gate'):
        publish_release(request,gate)
    assert not (request.parent/'startup-release.json').exists() and calls==[]
