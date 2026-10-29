"""Shared admission for Claim subscription calls; never retries a call.

OS locks protect actual CLI lifetimes. Durable slot records retain uncertainty
when a controller dies before an exit receipt. Waiting consumes no call budget.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import fcntl
import json
import os
import re
from pathlib import Path
import time
import uuid

_ACTIVE_FDS = ContextVar('claim_model_capacity_fds', default=())


def inherited_fds():
    return _ACTIVE_FDS.get()


def _save(path, value):
    temp = path.with_name(path.name + '.partial')
    with temp.open('w') as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def _read(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def validate(value):
    if (set(value) != {'root', 'action_window', 'spacing_seconds', 'cooldown_seconds'}
            or not Path(value['root']).is_absolute()
            or type(value['action_window']) is not int or not 4 <= value['action_window'] <= 8
            or any(type(value[k]) not in (float, int) or not 0 <= value[k] <= 3600
                   for k in ('spacing_seconds', 'cooldown_seconds'))):
        raise ValueError('capacity requires an absolute shared root, action window 4..8 and bounded timing')
    return value


class CapacityUnavailable(RuntimeError):
    pass


class ClaimModelCapacity:
    def __init__(self, configuration):
        self.config = validate(dict(configuration))
        self.root = Path(self.config['root'])
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / 'configuration.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            saved = self.root / 'configuration.json'
            if saved.exists() and _read(saved, {}) != self.config:
                raise ValueError('shared model capacity configuration drift')
            if not saved.exists():
                _save(saved, self.config)

    @contextmanager
    def _state(self, role):
        root = self.root / role
        root.mkdir(exist_ok=True)
        with (root / 'state.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = _read(root / 'state.json', {'allowed': 1, 'next_start': 0, 'refusals': 0})
            yield root, state
            _save(root / 'state.json', state)

    @staticmethod
    def _waiters(root):
        live = []
        for path in sorted(root.glob('wait-*.json')):
            with path.open('a') as probe:
                try:
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    live.append(path.name)
                else:
                    path.unlink(missing_ok=True)
        return live

    @staticmethod
    def _settled(artifacts):
        receipt = Path(artifacts) / 'exit.json'
        return (not (Path(artifacts) / 'invocation.json').exists()
                or receipt.is_file() and _read(receipt, {}).get('returncode') is not None)

    @contextmanager
    def call(self, role, artifacts):
        if role not in ('gpt', 'opus'):
            raise ValueError('Claim capacity accepts only GPT and Opus')
        root = self.root / role
        root.mkdir(exist_ok=True)
        ticket = root / ('wait-%020d-%s.json' % (time.time_ns(), uuid.uuid4().hex))
        slot = None
        with self._state(role):
            waiting = ticket.open('x+')
            fcntl.flock(waiting, fcntl.LOCK_EX)
            waiting.write(json.dumps({'artifacts': str(artifacts)})); waiting.flush()
        with waiting:
            try:
                while slot is None:
                    with self._state(role) as (root, state):
                        if state.get('blocked'):
                            raise CapacityUnavailable(role + ': ' + state['blocked'])
                        if role == 'gpt':
                            downstream = _read(self.root / 'opus' / 'state.json', {})
                            if downstream.get('blocked'):
                                raise CapacityUnavailable('GPT waits for Opus recovery: ' + downstream['blocked'])
                        if (self._waiters(root)[:1] == [ticket.name]
                                and time.time() >= state['next_start']):
                            for index in range(state['allowed']):
                                candidate = (root / ('slot-%s.lock' % index)).open('a')
                                try:
                                    fcntl.flock(candidate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                                except BlockingIOError:
                                    candidate.close(); continue
                                journal = root / ('slot-%s.json' % index)
                                previous = _read(journal, {})
                                if previous.get('active') and not self._settled(previous['artifacts']):
                                    candidate.close()
                                    state['blocked'] = 'unsettled previous transport: ' + previous['artifacts']
                                    break
                                if previous.get('active') and (Path(previous['artifacts']) / 'invocation.json').exists():
                                    candidate.close()
                                    state['blocked'] = 'interrupted capacity feedback requires reconciliation: ' + previous['artifacts']
                                    break
                                _save(journal, {'active': True, 'artifacts': str(Path(artifacts).resolve())})
                                slot = candidate
                                state['next_start'] = time.time() + self.config['spacing_seconds']
                                break
                    if slot is None:
                        time.sleep(.1)
                ticket.unlink(missing_ok=True)
                token = _ACTIVE_FDS.set((slot.fileno(),))
                try:
                    yield
                finally:
                    _ACTIVE_FDS.reset(token)
                    with self._state(role) as (root, state):
                        # A saved transport receipt proves termination even if
                        # semantic decoding failed. No receipt remains unknown.
                        settled = self._settled(artifacts)
                        receipt = _read(Path(artifacts) / 'exit.json', {})
                        error = ''
                        envelope = {}
                        stdout = Path(artifacts) / 'stdout.log'
                        if stdout.exists():
                            try:
                                envelope = json.loads(stdout.read_text())
                                if isinstance(envelope, dict) and envelope.get('is_error'):
                                    error = ' '.join(str(envelope.get(k, '')) for k in
                                        ('result', 'api_error', 'api_error_status')).lower()
                            except (ValueError, OSError):
                                pass
                        if receipt.get('returncode') not in (None, 0):
                            stderr = Path(artifacts) / 'stderr.log'
                            error += stderr.read_text()[-16000:].lower() if stderr.exists() else ''
                        fatal = next((s for s in ('insufficient_quota', 'usage_limit_reached', 'usage limit reached',
                            "you've hit your limit", "you've hit your session limit", 'not logged in', 'authentication_error', 'invalid_api_key') if s in error), None)
                        limited = (isinstance(envelope, dict)
                            and envelope.get('is_error') and envelope.get('api_error_status') in (429, 503)) or any(s in error for s in ('rate_limit_error', 'rate limit exceeded',
                                                          'too many requests', 'http 429', 'overloaded_error', 'temporarily unavailable'))
                        if not settled:
                            state['blocked'] = 'unsettled transport: ' + str(artifacts)
                        elif fatal:
                            state['blocked'] = fatal
                        elif limited:
                            state['refusals'] += 1
                            state['allowed'] = 1
                            retry_after = re.search(r'retry-after[:= ]+([0-9]+)', error)
                            delay = max(int(retry_after.group(1)) if retry_after else 0,
                                min(3600, self.config['cooldown_seconds'] * 2 ** min(state['refusals'] - 1, 6)))
                            state['next_start'] = max(state['next_start'], time.time() + delay)
                        elif receipt.get('returncode') == 0 and time.time() >= state['next_start']:
                            state['allowed'] = 2
                            state['refusals'] = 0
                        _save(journal, {'active': not settled, 'artifacts': str(Path(artifacts).resolve())})
            finally:
                ticket.unlink(missing_ok=True)
                if slot is not None:
                    slot.close()
