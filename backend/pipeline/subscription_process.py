"""File-backed CLI execution. An interrupted intent is never an automatic retry."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from datetime import datetime, timezone


def save(path, value):
    path = Path(path)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def digest_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(command, *, input, env, timeout, directory, cwd=None, replay_only=False):
    """Replay only a successful, byte-bound exit receipt; keep all other calls spent.

    CLI stdout/stderr go straight to permanent files, including on timeout or
    parent death. No shell, daemon, provider retry or changed request is accepted.
    """
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    binary = Path(shutil.which(command[0], path=env.get('PATH')) or command[0]).resolve(strict=True)
    intent = {'command_sha256': hashlib.sha256(json.dumps(command).encode()).hexdigest(),
              'input_sha256': hashlib.sha256(input.encode()).hexdigest(),
              'executable_sha256': digest_file(binary), 'cwd': str(cwd) if cwd else None}
    request = root / 'invocation.json'
    receipt = root / 'exit.json'
    out, err = root / 'stdout.log', root / 'stderr.log'
    if request.exists():
        if json.loads(request.read_text()) != intent:
            raise ValueError('subscription invocation drift; no replay')
        if not receipt.exists():
            raise ValueError('subscription invocation has no exit receipt; no retry')
        saved = json.loads(receipt.read_text())
        files = [out, err, *([root / 'response.json'] if (root / 'response.json').exists() else [])]
        if (saved.get('returncode') != 0 or saved.get('files') != {p.name: digest_file(p) for p in files}):
            raise ValueError('subscription exit failed or output drift; no retry')
        return subprocess.CompletedProcess(command, 0, out.read_text(), err.read_text())
    if replay_only:
        raise ValueError('subscription reservation lacks durable invocation; no retry')
    save(request, intent)
    with out.open('x') as stdout, err.open('x') as stderr:
        try:
            completed = subprocess.run(command, input=input, text=True, env=env, cwd=cwd,
                                       stdout=stdout, stderr=stderr, timeout=timeout, check=False)
            exit_state = {'returncode': completed.returncode}
        except BaseException as exc:
            exit_state = {'error_type': type(exc).__name__, 'returncode': None}
            raise
        finally:
            for stream in (stdout, stderr):
                stream.flush()
                os.fsync(stream.fileno())
            files = [out, err, *([root / 'response.json'] if (root / 'response.json').exists() else [])]
            save(receipt, {**exit_state, 'files': {p.name: digest_file(p) for p in files},
                           'finished_at': datetime.now(timezone.utc).isoformat()})
    return subprocess.CompletedProcess(command, completed.returncode, out.read_text(), err.read_text())
