"""Explicit, bounded recovery of one terminal HTTP 429 subtitle dispatch.

This helper makes no model calls. It retains the complete failed generation and
records one additional dispatch allowance in the same source action directory.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

from backend.pipeline.subtitle_generation import PROMPT_PATH

TERMINAL_429 = 'ERROR: exceeded retry limit, last status: 429 Too Many Requests'
ACTION = 'retry_same_subtitle_generation_after_terminal_http_429'


def _sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_once(path, raw):
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '.')
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != raw:
                raise ValueError('immutable rate-limit recovery artifact differs')
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def reconcile_rate_limited_generation(plan_path, *, minimum_age_seconds=120,
                                       maximum_retries=1, now=None):
    """Archive one proven 429 failure and permit the same frozen action once.

    Unknown dispatches, transport timeouts and other failures are never retried.
    A second 429 cannot obtain another allowance, even if its text is identical.
    """
    if minimum_age_seconds < 120:
        raise ValueError('rate-limit recovery requires at least 120 seconds')
    if type(maximum_retries) is not int or not 0 <= maximum_retries <= 1:
        raise ValueError('rate-limit recovery permits at most one additional dispatch')
    plan_path = Path(plan_path).resolve()
    if plan_path.name != 'generation.json':
        raise ValueError('recovery requires an explicit generation.json path')
    instant = time.time() if now is None else (now.timestamp() if hasattr(now, 'timestamp') else float(now))
    with plan_path.with_suffix('.lock').open('a+b') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        raw = plan_path.read_bytes()
        stat = plan_path.stat()
        digest = hashlib.sha256(raw).hexdigest()
        plan = json.loads(raw)
        if (plan.get('schema_version') != 'wang_subtitle_generation_v1'
                or plan.get('status') != 'failed'
                or not isinstance(plan.get('error'), str)
                or not plan['error'].startswith('CodexSubscriptionError:')
                or plan['error'].strip().splitlines()[-1] != TERMINAL_429):
            raise ValueError('only a terminal Codex HTTP 429 failure can be reconciled')
        binding = plan.get('binding')
        if not isinstance(binding, dict) or plan.get('binding_sha256') != _sha(binding):
            raise ValueError('rate-limit generation binding seal changed')
        required = {'source_id','source_path','source_sha256','actor_id','prompt_sha256',
                    'model','reasoning_effort','scope_end','paragraphs'}
        if set(binding) != required:
            raise ValueError('rate-limit generation binding is incomplete')
        source = Path(binding['source_path'])
        if (not source.is_absolute()
                or hashlib.sha256(source.read_bytes()).hexdigest() != binding['source_sha256']):
            raise ValueError('rate-limit source changed after frozen dispatch')
        if hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest() != binding['prompt_sha256']:
            raise ValueError('rate-limit prompt changed after frozen dispatch')
        age = instant - stat.st_mtime
        if not age >= minimum_age_seconds:
            raise ValueError('rate-limit cooldown has not elapsed')
        identity = {'device': stat.st_dev, 'inode': stat.st_ino,
                    'mtime_ns': stat.st_mtime_ns, 'ctime_ns': stat.st_ctime_ns, 'size': stat.st_size}
        directory = plan_path.parent / 'rate-limit-failures'
        prior = []
        if directory.exists():
            prior = [json.loads(p.read_bytes()) for p in sorted(directory.glob('reconciliation-*.json'))]
        # An interrupted reconciliation may have saved its immutable record but
        # not yet unlinked the old file. Complete that same operation only for
        # the original inode/mtime, never for a later identical 429 response.
        matching = [r for r in prior if r.get('original_sha256') == digest
                    and r.get('original_file_identity') == identity]
        if matching:
            receipt = matching[0]
            if (receipt.get('binding') != binding or receipt.get('action') != ACTION
                    or receipt.get('artifact_sha256') != _sha({k:v for k,v in receipt.items() if k!='artifact_sha256'})):
                raise ValueError('rate-limit reconciliation record changed')
            archive = directory / (digest + '.json')
            if archive.read_bytes() != raw:
                raise ValueError('rate-limit failure archive changed')
        else:
            if len(prior) >= maximum_retries:
                raise ValueError('rate-limit additional dispatch allowance exhausted')
            directory.mkdir(parents=True, exist_ok=True)
            archive = directory / (digest + '.json')
            _write_once(archive, raw)
            receipt = {'schema_version':'wang_subtitle_rate_limit_reconciliation_v1',
                       'action':ACTION, 'plan_path':str(plan_path), 'attempt':len(prior)+1,
                       'original_sha256':digest, 'original_file_identity':identity,
                       'archive_path':str(archive), 'binding':binding,
                       'binding_sha256':plan['binding_sha256'],
                       'failed_at_mtime':stat.st_mtime, 'reconciled_at':instant,
                       'minimum_age_seconds':minimum_age_seconds,
                       'maximum_additional_dispatches':maximum_retries}
            receipt['artifact_sha256'] = _sha(receipt)
            encoded = json.dumps(receipt, ensure_ascii=False, indent=2).encode()
            _write_once(directory / f"reconciliation-{receipt['attempt']:03d}.json", encoded)
        plan_path.unlink()
        _sync_directory(plan_path.parent)
        return receipt
