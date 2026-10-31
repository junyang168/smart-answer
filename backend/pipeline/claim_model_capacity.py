"""Shared admission for Claim subscription calls; never retries a call.

OS locks protect actual CLI lifetimes. Durable slot records retain uncertainty
when a controller dies before an exit receipt. Waiting consumes no call budget.
"""
from contextlib import contextmanager
from contextlib import ExitStack
from contextvars import ContextVar
import fcntl
import json
import os
import re
from pathlib import Path
import time
import uuid
import hashlib

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

    def quarantine_stopped(self, role, artifacts):
        """Release only a proved stopped local transport, preserving its unknown result.

        This is an explicit recovery operation, never an automatic retry. The
        real transport inherits the slot lock, so acquiring every slot proves
        no admitted child remains. Original files and call accounting stay intact.
        """
        if role not in ('gpt', 'opus'):
            raise ValueError('content role required')
        artifacts = str(Path(artifacts).resolve())
        with self._state(role) as (root, state), ExitStack() as held:
            journals = []
            for index in range(2):
                lock = held.enter_context((root / ('slot-%s.lock' % index)).open('a'))
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise CapacityUnavailable('cannot reconcile while a transport holds a slot') from None
                path = root / ('slot-%s.json' % index)
                journals.append((path, _read(path, {})))
            matches = [(path, value) for path, value in journals
                       if value.get('active') and value.get('artifacts') == artifacts]
            if len(matches) != 1:
                raise ValueError('quarantine requires its exact active slot journal')
            transport = Path(artifacts)
            if not (transport / 'invocation.json').is_file() or (transport / 'exit.json').exists():
                raise ValueError('quarantine requires an interrupted invocation without an exit receipt')
            reasons = ('unsettled transport: ', 'unsettled previous transport: ')
            blocked = state.get('blocked')
            if blocked and not any(blocked.startswith(prefix) and
                    blocked[len(prefix):] in {v.get('artifacts') for _, v in journals if v.get('active')}
                    for prefix in reasons):
                raise ValueError('quarantine cannot clear quota, authentication or unrelated blocks')
            files = {name: hashlib.sha256((transport / name).read_bytes()).hexdigest()
                     for name in ('invocation.json', 'stdout.log', 'stderr.log', 'response.json')
                     if (transport / name).is_file()}
            path, previous = matches[0]
            proof = root / ('interrupted-' + hashlib.sha256(artifacts.encode()).hexdigest() + '.json')
            if proof.exists():
                raise ValueError('interrupted transport already reconciled')
            record = {'artifacts': artifacts, 'previous_journal': previous, 'files': files,
                      'result_state': 'unknown', 'model_calls': 0, 'retry_authorized': False,
                      'all_slot_locks_acquired': True, 'reconciled_at': time.time()}
            _save(proof, record)
            _save(path, dict(previous, active=False, interruption_proof=str(proof)))
            if blocked in tuple(prefix + artifacts for prefix in reasons):
                state.pop('blocked', None)
            state['allowed'] = 1
            state['next_start'] = max(state['next_start'], time.time() + self.config['spacing_seconds'])
            return record

    @contextmanager
    def call(self, role, artifacts, *, opus_api=False):
        if role not in ('gpt', 'opus'):
            raise ValueError('Claim capacity accepts only GPT and Opus')
        root = self.root / role
        root.mkdir(exist_ok=True)
        quarantine = root / ('interrupted-' + hashlib.sha256(str(Path(artifacts).resolve()).encode()).hexdigest() + '.json')
        if quarantine.exists():
            raise CapacityUnavailable('interrupted call remains unknown; the same transport cannot be repeated')
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
                        reason = self.block_reason(state, opus_api=opus_api and role == 'opus')
                        if reason:
                            raise CapacityUnavailable(role + ': ' + reason)
                        if role == 'gpt':
                            downstream = _read(self.root / 'opus' / 'state.json', {})
                            reason = self.block_reason(downstream, opus_api=opus_api)
                            if reason:
                                raise CapacityUnavailable('GPT waits for Opus recovery: ' + reason)
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
                            if role == 'opus' and opus_api:
                                state.setdefault('backend_blocks', {})['anthropic_api'] = fatal
                            else:
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

    @staticmethod
    def block_reason(state, *, opus_api=False):
        """One shared slot pool; subscription quota is not an API quota.

        Unknown/interrupted transports remain global blockers. Old journals
        have subscription-only usage blocks; never clear or overwrite them.
        """
        reason = state.get('blocked')
        if opus_api:
            api = state.get('backend_blocks', {}).get('anthropic_api')
            if api:
                return api
            if reason in {'usage_limit_reached', 'usage limit reached', "you've hit your limit", "you've hit your session limit"}:
                return None
        return reason
