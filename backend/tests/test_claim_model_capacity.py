"""Exercise real OS locks across callers, not synthetic worker counts."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import time

import pytest
from backend.pipeline.claim_model_capacity import ClaimModelCapacity, CapacityUnavailable


def capacity(tmp_path, **changes):
    return ClaimModelCapacity(dict(root=str(tmp_path/'capacity'), action_window=8,
                                  spacing_seconds=0, cooldown_seconds=.2) | changes)


def returned(path, code=0, error=''):
    path.mkdir(parents=True, exist_ok=True)
    (path/'invocation.json').write_text('{}')
    (path/'exit.json').write_text(json.dumps({'returncode': code}))
    (path/'stderr.log').write_text(error)


def test_independent_two_plus_two_and_immediate_refill(tmp_path):
    gate=capacity(tmp_path)
    for role in ('gpt','opus'):
        path=tmp_path/('warm-'+role)
        with gate.call(role,path): returned(path)
    lock=threading.Lock(); active={'gpt':0,'opus':0}; peak={}; both=threading.Event()
    release=threading.Event(); entered=[]
    def call(role, index):
        path=tmp_path/(role+str(index))
        with gate.call(role,path):
            with lock:
                active[role]+=1; entered.append((role,index)); peak.update({r:max(peak.get(r,0),active[r]) for r in active})
                if active=={'gpt':2,'opus':2}: both.set()
            assert release.wait(5)
            returned(path)
            with lock: active[role]-=1
    with ThreadPoolExecutor(max_workers=6) as pool:
        tasks=[pool.submit(call,r,i) for i in range(3) for r in ('gpt','opus')]
        try:
            assert both.wait(5)
            assert len(entered)==4
        finally: release.set()
        for task in tasks: task.result(timeout=5)
    assert peak=={'gpt':2,'opus':2}
    assert len(entered)==6


def test_unknown_blocks_new_calls_but_other_provider_runs(tmp_path):
    gate=capacity(tmp_path)
    with gate.call('gpt',tmp_path/'lost'):
        (tmp_path/'lost').mkdir(); (tmp_path/'lost/invocation.json').write_text('{}')
    with pytest.raises(CapacityUnavailable,match='unsettled'):
        with gate.call('gpt',tmp_path/'must-not-dispatch'): pytest.fail('dispatched')
    # Independent Opus reviews may still pass without requiring GPT repair.
    with gate.call('opus',tmp_path/'answer'): returned(tmp_path/'answer')


def test_refusal_cools_down_without_replaying_and_then_recovers(tmp_path):
    gate=capacity(tmp_path)
    with gate.call('opus',tmp_path/'refusal'):
        returned(tmp_path/'refusal',1,'HTTP 429 Too many requests')
    state=json.loads((gate.root/'opus/state.json').read_text())
    assert state['allowed']==1 and state['refusals']==1
    started=time.time()
    with gate.call('opus',tmp_path/'next'):
        assert time.time()-started >= .15
        returned(tmp_path/'next')
    assert json.loads((gate.root/'opus/state.json').read_text())['allowed']==2


def test_quota_stops_provider_without_probing(tmp_path):
    gate=capacity(tmp_path)
    with gate.call('gpt',tmp_path/'quota'): returned(tmp_path/'quota',1,'insufficient_quota')
    with pytest.raises(CapacityUnavailable,match='insufficient_quota'):
        with gate.call('gpt',tmp_path/'next'): pytest.fail('quota probe')


def test_api_uses_shared_slots_without_clearing_subscription_usage_block(tmp_path):
    gate = capacity(tmp_path)
    with gate.call('opus', tmp_path/'subscription-limit'):
        returned(tmp_path/'subscription-limit', 1, 'usage_limit_reached')
    with pytest.raises(CapacityUnavailable, match='usage_limit_reached'):
        with gate.call('opus', tmp_path/'no-subscription-probe'):
            pytest.fail('subscription quota was bypassed')
    with gate.call('opus', tmp_path/'api-answer', opus_api=True):
        returned(tmp_path/'api-answer')
    with gate.call('gpt', tmp_path/'gpt-for-api', opus_api=True):
        returned(tmp_path/'gpt-for-api')
    saved = json.loads((gate.root/'opus'/'state.json').read_text())
    assert saved['blocked'] == 'usage_limit_reached'
    assert (gate.root/'opus'/'slot-0.lock').exists()


def test_api_failure_blocks_api_and_its_gpt_without_erasing_old_subscription_block(tmp_path):
    gate = capacity(tmp_path)
    with gate.call('opus', tmp_path/'subscription-limit'):
        returned(tmp_path/'subscription-limit', 1, 'usage_limit_reached')
    with gate.call('opus', tmp_path/'api-limit', opus_api=True):
        returned(tmp_path/'api-limit', 1, 'insufficient_quota')
    for role in ['opus', 'gpt']:
        with pytest.raises(CapacityUnavailable, match='insufficient_quota'):
            with gate.call(role, tmp_path/('blocked-'+role), opus_api=True):
                pytest.fail('API quota probe')
    saved = json.loads((gate.root/'opus'/'state.json').read_text())
    assert saved['blocked'] == 'usage_limit_reached'
    assert saved['backend_blocks']['anthropic_api'] == 'insufficient_quota'


def test_api_never_ignores_an_unsettled_shared_transport(tmp_path):
    gate = capacity(tmp_path)
    with gate.call('opus', tmp_path/'unknown'):
        (tmp_path/'unknown').mkdir()
        (tmp_path/'unknown'/'invocation.json').write_text('{}')
    with pytest.raises(CapacityUnavailable, match='unsettled'):
        with gate.call('opus', tmp_path/'must-not-call', opus_api=True):
            pytest.fail('unknown shared transport was bypassed')


def test_interrupted_journal_is_not_freed_by_old_pid(tmp_path):
    gate=capacity(tmp_path)
    root=gate.root/'opus';root.mkdir()
    (tmp_path/'lost').mkdir(); (tmp_path/'lost/invocation.json').write_text('{}')
    (root/'slot-0.json').write_text(json.dumps({'active':True,'artifacts':str(tmp_path/'lost'),'pid':99999999}))
    with pytest.raises(CapacityUnavailable,match='unsettled previous'):
        with gate.call('opus',tmp_path/'new'): pytest.fail('overlap')


def test_explicit_stopped_quarantine_preserves_unknown_and_never_retries(tmp_path):
    gate=capacity(tmp_path);lost=tmp_path/'lost'
    with gate.call('opus',lost):
        lost.mkdir();(lost/'invocation.json').write_text('{}')
        (lost/'stdout.log').write_text('partial')
    original={p.name:p.read_bytes() for p in lost.iterdir()}
    proof=gate.quarantine_stopped('opus',lost)
    assert proof['result_state']=='unknown' and not proof['retry_authorized']
    assert proof['model_calls']==0 and proof['all_slot_locks_acquired']
    assert {p.name:p.read_bytes() for p in lost.iterdir()}==original
    assert not gate._settled(lost) and not (lost/'exit.json').exists()
    with pytest.raises(CapacityUnavailable,match='same transport cannot be repeated'):
        with gate.call('opus',lost):pytest.fail('unknown call repeated')
    with gate.call('opus',tmp_path/'different'):returned(tmp_path/'different')


@pytest.mark.parametrize('defect',['live','quota','wrong_artifacts','returned'])
def test_stopped_quarantine_refuses_live_unrelated_and_settled_calls(tmp_path,defect):
    import fcntl
    gate=capacity(tmp_path);lost=tmp_path/'lost'
    with gate.call('opus',lost):
        lost.mkdir();(lost/'invocation.json').write_text('{}')
    state_path=gate.root/'opus/state.json';state=json.loads(state_path.read_text())
    if defect=='quota':state['blocked']='insufficient_quota';state_path.write_text(json.dumps(state))
    elif defect=='returned':(lost/'exit.json').write_text('{"returncode":0}')
    before=state_path.read_bytes()
    with (gate.root/'opus/slot-0.lock').open('a') as held:
        if defect=='live':fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises((ValueError,CapacityUnavailable)):
            gate.quarantine_stopped('opus',tmp_path/'other' if defect=='wrong_artifacts' else lost)
    assert state_path.read_bytes()==before
    assert json.loads((gate.root/'opus/slot-0.json').read_text())['active']
    assert not list((gate.root/'opus').glob('interrupted-*.json'))


def test_quarantine_of_one_stopped_call_does_not_hide_a_second(tmp_path):
    gate=capacity(tmp_path);role=gate.root/'opus';role.mkdir()
    paths=[tmp_path/'first',tmp_path/'second']
    for i,path in enumerate(paths):
        path.mkdir();(path/'invocation.json').write_text('{}')
        (role/('slot-%s.json'%i)).write_text(json.dumps({'active':True,'artifacts':str(path)}))
    (role/'state.json').write_text(json.dumps({'allowed':2,'next_start':0,'refusals':0,
        'blocked':'unsettled previous transport: '+str(paths[1])}))
    gate.quarantine_stopped('opus',paths[0])
    assert json.loads((role/'state.json').read_text())['blocked']
    gate.quarantine_stopped('opus',paths[1])
    assert 'blocked' not in json.loads((role/'state.json').read_text())
    with gate.call('opus',tmp_path/'new'):returned(tmp_path/'new')


def test_configuration_drift_refused(tmp_path):
    capacity(tmp_path)
    with pytest.raises(ValueError,match='configuration drift'): capacity(tmp_path,spacing_seconds=5)


def test_preflight_rejection_does_not_block_provider(tmp_path):
    gate=capacity(tmp_path)
    with pytest.raises(ValueError):
        with gate.call('gpt',tmp_path/'no-invocation'): raise ValueError('changed SQL pin')
    with gate.call('gpt',tmp_path/'valid'): returned(tmp_path/'valid')


def test_zero_exit_error_envelope_still_stops_quota_probes(tmp_path):
    gate=capacity(tmp_path)
    path=tmp_path/'quota'
    with gate.call('opus',path):
        returned(path)
        (path/'stdout.log').write_text(json.dumps({'is_error':True,'result':'usage limit reached'}))
    with pytest.raises(CapacityUnavailable,match='usage limit reached'):
        with gate.call('opus',tmp_path/'next'): pytest.fail('quota probe')


def test_capacity_fd_survives_caller_death_in_real_transport(tmp_path):
    import fcntl
    import os
    import signal
    import subprocess
    import sys
    script=tmp_path/'caller.py'
    config=dict(root=str(tmp_path/'capacity'), action_window=4, spacing_seconds=0, cooldown_seconds=1)
    script.write_text('''import json,os,sys
from backend.pipeline.claim_model_capacity import ClaimModelCapacity
from backend.pipeline.subscription_process import run
with ClaimModelCapacity(json.loads(sys.argv[1])).call('opus',sys.argv[2]):
 run([sys.executable,'-c','import time;print("alive",flush=True);time.sleep(1.5)'],input='',env=dict(os.environ),timeout=5,directory=sys.argv[2])
''')
    artifacts=tmp_path/'transport'
    parent=subprocess.Popen([sys.executable,str(script),json.dumps(config),str(artifacts)])
    try:
        for _ in range(200):
            if (artifacts/'stdout.log').exists() and (artifacts/'stdout.log').read_text(): break
            time.sleep(.01)
        else: pytest.fail('transport did not start')
        os.kill(parent.pid,signal.SIGKILL);parent.wait()
        with (tmp_path/'capacity/opus/slot-0.lock').open('a') as probe:
            with pytest.raises(BlockingIOError): fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
        time.sleep(1.6)
        with pytest.raises(CapacityUnavailable,match='unsettled previous'):
            with ClaimModelCapacity(config).call('opus',tmp_path/'another'): pytest.fail('duplicate')
    finally:
        if parent.poll() is None: parent.kill();parent.wait()


@pytest.mark.parametrize('code',[0,1])
def test_actual_claude_session_quota_stops_all_later_claim_calls(tmp_path,code):
    gate=capacity(tmp_path); path=tmp_path/'quota'
    with gate.call('opus',path):
        returned(path,code)
        (path/'stdout.log').write_text(json.dumps({'is_error':True,'api_error_status':429,
            'api_error':'usage_limit_reached','terminal_reason':'api_error',
            'result':"You've hit your session limit · resets 3:50pm (America/Chicago)",
            'usage':{'input_tokens':0,'output_tokens':0}}))
    with pytest.raises(CapacityUnavailable,match='session limit|usage_limit_reached'):
        with gate.call('opus',tmp_path/'no-repeat'): pytest.fail('quota probe')
    with pytest.raises(CapacityUnavailable,match='GPT waits for Opus recovery'):
        with gate.call('gpt',tmp_path/'no-new-proposal'): pytest.fail('unreviewable proposal')
    assert not (tmp_path/'no-new-proposal/invocation.json').exists()
    assert not list((gate.root/'gpt').glob('wait-*.json'))
