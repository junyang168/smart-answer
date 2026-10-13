"""One producer process: authenticate, zero-call dry-run, wait, then continue.

This is a mechanical rendezvous, NOT a semantic approval or performance pass.
The launcher must have measured scalability acceptance before creating it.
Missing/drifted/expired handoffs stop; never cold-start a fallback producer.
"""
from pathlib import Path
import os
import json
import time
import uuid

from backend.pipeline import source_reconciliation as c
from backend.pipeline.source_reconciliation_runner import body, seal, binding
from backend.pipeline.viewpoint_resolution_runtime import write_immutable

VERSION = 'wang_single_runtime_startup_handoff_v1'
MAX_BYTES = 1024**2
WAIT_SECONDS = 10


def bounded_json(path):
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('startup handshake artifact exceeds bounded size')
    return json.loads(path.read_text(encoding='utf-8'))


def small(path):
    value = bounded_json(path)
    if value.get('artifact_sha256') != c.sha256_json(body(value)):
        raise ValueError('startup artifact seal drift')
    return body(value)


def save(path, value):
    write_immutable(Path(path), seal(value), require_new=True)


def validate_command(output, argv):
    output = Path(output)
    tokens = [str(a) for a in argv]
    if (len(tokens) < 5 or not output.is_absolute() or '--dry-run' in tokens
            or '--startup-handoff' in tokens or '--validation-runtime' not in tokens
            or tokens[:3] != [tokens[0], '-m', 'backend.pipeline.viewpoint_cvp_campaign_runner']
            or tokens.count('--output-root') != 1
            or tokens[-1] == '--output-root'
            or tokens[tokens.index('--output-root')+1] != str(output)):
        raise ValueError('single-runtime handoff requires exact owned production command')
    return tokens


def create(attempt, output, argv, acceptance):
    attempt, output = Path(attempt), Path(output)
    tokens = validate_command(output, argv)
    if not attempt.is_absolute():
        raise ValueError('absolute startup receipt directory required')
    from backend.pipeline.viewpoint_scalability_acceptance import require_scalability_acceptance
    measured = require_scalability_acceptance(acceptance['path'])
    if measured != acceptance:
        raise ValueError('startup acceptance binding drift')
    request = attempt/'startup-request.json'
    save(request, {'schema_version':VERSION,'launch_id':uuid.uuid4().hex,
        'output_root':str(output),'argv':tokens,'acceptance':acceptance,
        'wait_seconds':WAIT_SECONDS,'mechanical_release_only':True,
        'model_approval_granted':False})
    return request


def check_ready(request, ready, pid):
    value = small(request)
    if (ready.get('schema_version') != VERSION or ready.get('request') != binding(request)
            or ready.get('producer_pid') != pid or ready.get('launch_id') != value['launch_id']
            or ready.get('output_root') != value['output_root']
            or ready.get('status') not in {'preflight_passed_no_model_calls','complete'}
            or ready.get('model_calls') != 0 or ready.get('Registry_writes') != 0
            or ready.get('owned_runtime_startups') != 1
            or any(type(ready.get(k)) is not int for k in
                   ('producer_pid','model_calls','Registry_writes','owned_runtime_startups'))):
        raise ValueError('startup ready identity/status/zero-call boundary drift')
    for pin in ready['bindings']:
        c.verify_file(pin)
    return ready


def wait_ready(request, pid, alive, *, seconds=WAIT_SECONDS):
    if not 0 < seconds <= WAIT_SECONDS:
        raise ValueError('bounded startup rendezvous timeout required')
    ready = Path(request).parent/'startup-ready.json'
    started = time.monotonic()
    while True:
        if ready.exists():
            value = check_ready(request, small(ready), pid)
            if value['status'] != 'complete' and not alive(pid):
                raise ValueError('authenticated producer exited before release; no restart')
            return value
        if not alive(pid):
            raise ValueError('producer exited before zero-call startup receipt; no restart')
        if time.monotonic()-started > seconds:
            raise ValueError('startup rendezvous timeout; no second cold start')
        time.sleep(.1)


def release(request, pid, runtime_binding, restore_binding):
    request = Path(request)
    ready = check_ready(request, small(request.parent/'startup-ready.json'), pid)
    if ready['status'] != 'preflight_passed_no_model_calls':
        raise ValueError('complete campaign has no execution release')
    if (runtime_binding['path'] != str(request.parent/'codex-runtime-preflight.json')
            or restore_binding['path'] != str(request.parent/'backup-restore-list.json')):
        raise ValueError('startup release detached from its runtime/backup gates')
    c.verify_file(runtime_binding); c.verify_file(restore_binding)
    restore = bounded_json(restore_binding['path'])
    if (type(restore.get('exit_code')) is not int or restore['exit_code'] != 0 or restore.get('argv') != [
            'pg_restore','--list',ready['backup']['path']]):
        raise ValueError('startup backup restore gate not actually passed')
    save(request.parent/'startup-release.json', {'schema_version':VERSION,
        'request':binding(request),'ready':binding(request.parent/'startup-ready.json'),
        'producer_pid':pid,'launch_id':ready['launch_id'],
        'runtime':runtime_binding,'restore':restore_binding,
        'mechanical_release_only':True,'model_approval_granted':False})


def abort(request, reason):
    request = Path(request)
    path = request.parent/'startup-abort.json'
    if not path.exists():
        save(path, {'request':binding(request),'status':'aborted',
                   'reason':reason,'automatic_restart':False})


def wait_consumed(request, pid, alive, *, seconds=WAIT_SECONDS):
    if not 0 < seconds <= WAIT_SECONDS:
        raise ValueError('bounded release consumption timeout required')
    request = Path(request)
    path = request.parent/'startup-release-consumed.json'
    started = time.monotonic()
    while True:
        if path.exists():
            value = small(path)
            if (value.get('request') != binding(request) or value.get('producer_pid') != pid
                    or value.get('launch_id') != small(request)['launch_id']
                    or value.get('release') != binding(request.parent/'startup-release.json')
                    or value.get('owned_runtime_startups') != 1
                    or value.get('new_model_calls') != 0 or value.get('Registry_writes') != 0
                    or any(type(value.get(k)) is not int for k in
                           ('producer_pid','owned_runtime_startups','new_model_calls','Registry_writes'))
                    or value.get('historical_rebuild_on_release') is not False):
                raise ValueError('same-process startup consumption receipt drift')
            if not alive(pid):
                raise ValueError('producer exited after startup consumption; not production running')
            return value
        if not alive(pid) or time.monotonic()-started > seconds:
            raise ValueError('startup release not consumed by live owned process; no restart')
        time.sleep(.1)


class ProducerGate:
    def __init__(self, path, output, cli):
        self.path, self.output = Path(path), Path(output)
        self.request = small(self.path)
        tokens = [str(x) for x in cli]
        if tokens.count('--startup-handoff') != 1:
            raise ValueError('one exact producer startup handoff required')
        offset = tokens.index('--startup-handoff')
        if tokens[offset+1] != str(self.path):
            raise ValueError('producer handoff argv identity drift')
        del tokens[offset:offset+2]
        req = self.request
        if (req.get('schema_version') != VERSION or req.get('argv',[])[3:] != tokens
                or req.get('output_root') != str(self.output) or '--dry-run' in tokens
                or '--validation-runtime' not in tokens or req.get('wait_seconds') != WAIT_SECONDS
                or req.get('mechanical_release_only') is not True
                or req.get('model_approval_granted') is not False):
            raise ValueError('producer cannot reuse a foreign command or bypass startup gates')
        from backend.pipeline.viewpoint_scalability_acceptance import require_scalability_acceptance
        if require_scalability_acceptance(req['acceptance']['path']) != req['acceptance']:
            raise ValueError('producer measured acceptance drift')
        self.original = binding(self.path)
        self.pid = os.getpid()
        self.released = False
        save(self.path.parent/'startup-owner.json',{'request':self.original,
            'producer_pid':self.pid,'launch_id':req['launch_id']})

    def ready(self, state, pins, backup=None):
        if binding(self.path) != self.original:
            raise ValueError('startup request changed after process ownership')
        status = state['status']
        if status not in {'preflight_passed_no_model_calls','complete'}:
            raise ValueError('only actual zero-call preflight/completion may publish ready')
        if status != 'complete' and (not backup or not pins):
            raise ValueError('startup preflight input/backup population missing')
        for pin in pins:c.verify_file(pin)
        if backup:c.verify_file(backup)
        save(self.path.parent/'startup-ready.json', {'schema_version':VERSION,
            'request':self.original,'launch_id':self.request['launch_id'],
            'producer_pid':self.pid,'output_root':str(self.output),'status':status,
            'progress':state,'bindings':pins,'backup':backup,
            'model_calls':0,'Registry_writes':0,'owned_runtime_startups':1})

    def wait_release(self, stop_requested, executable):
        directory = self.path.parent
        started = time.monotonic()
        while True:
            if stop_requested():
                raise ValueError('owner stop before startup release; no model or write')
            abort_path = directory/'startup-abort.json'
            if abort_path.exists():
                if small(abort_path).get('request') != self.original:
                    raise ValueError('foreign startup abort')
                raise ValueError('launcher aborted startup; no automatic restart')
            path = directory/'startup-release.json'
            if path.exists():
                value = small(path)
                from backend.pipeline.viewpoint_scalability_acceptance import require_scalability_acceptance
                if require_scalability_acceptance(self.request['acceptance']['path']) != self.request['acceptance']:
                    raise ValueError('implementation/scale acceptance changed during startup wait')
                if (value.get('schema_version') != VERSION or value.get('request') != self.original
                        or binding(self.path) != self.original or value.get('producer_pid') != self.pid
                        or value.get('launch_id') != self.request['launch_id']
                        or value.get('ready') != binding(directory/'startup-ready.json')
                        or value.get('mechanical_release_only') is not True
                        or value.get('model_approval_granted') is not False
                        or value.get('runtime',{}).get('path') != str(directory/'codex-runtime-preflight.json')
                        or value.get('restore',{}).get('path') != str(directory/'backup-restore-list.json')):
                    raise ValueError('startup release provenance drift')
                ready = check_ready(self.path,small(directory/'startup-ready.json'),self.pid)
                c.verify_file(value['runtime']); c.verify_file(value['restore'])
                restore = bounded_json(value['restore']['path'])
                if (type(restore.get('exit_code')) is not int or restore['exit_code'] != 0 or restore.get('argv') != [
                        'pg_restore','--list',ready['backup']['path']]):
                    raise ValueError('producer backup restore gate drift')
                from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
                from backend.pipeline.codex_subscription_preflight import consume_saved_runtime
                client = CodexSubscriptionClient(model='gpt-6.1-sol',reasoning_effort='high',
                                                 executable=executable)
                consume_saved_runtime(client,bounded_json(value['runtime']['path']))
                save(directory/'startup-release-consumed.json',{'request':self.original,
                    'release':binding(path),'producer_pid':self.pid,
                    'launch_id':self.request['launch_id'],'owned_runtime_startups':1,
                    'new_model_calls':0,'Registry_writes':0,'historical_rebuild_on_release':False})
                self.released = True
                return
            if time.monotonic()-started > self.request['wait_seconds']:
                raise ValueError('producer startup release timeout; no model or write')
            time.sleep(.1)
