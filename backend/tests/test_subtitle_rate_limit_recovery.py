import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from backend.pipeline import subtitle_rate_limit_recovery as recovery


@pytest.fixture
def failed(tmp_path, monkeypatch):
    source = tmp_path / 'source.json'
    source.write_text('[{"index":1,"text":"原文。"}]')
    prompt = tmp_path / 'prompt.md'
    prompt.write_text('subtitle prompt')
    monkeypatch.setattr(recovery, 'PROMPT_PATH', prompt)
    binding = {'source_id':'S', 'source_path':str(source),
               'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
               'actor_id':'editor', 'prompt_sha256':hashlib.sha256(prompt.read_bytes()).hexdigest(),
               'model':'gpt-test', 'reasoning_effort':'high', 'scope_end':1,
               'paragraphs':[{'index':1,'text':'原文。'}]}
    plan = {'schema_version':'wang_subtitle_generation_v1', 'binding':binding,
            'binding_sha256':recovery._sha(binding), 'status':'failed',
            'error':'CodexSubscriptionError: Codex subscription generation failed (exit 1): diagnostic\n' + recovery.TERMINAL_429 + '\n' + recovery.TERMINAL_429}
    path = tmp_path/'generation.json'
    save(path, plan)
    return path, plan, source, prompt


def save(path, plan):
    path.write_text(json.dumps(plan, ensure_ascii=False))
    os.utime(path, (800, 800))


def test_one_explicit_reconciliation_preserves_failed_bytes_and_same_action(failed):
    path, plan, source, _ = failed
    original = path.read_bytes()
    before_source = source.read_bytes()
    record = recovery.reconcile_rate_limited_generation(path, now=1000)
    assert not path.exists()
    archive = Path(record['archive_path'])
    assert archive.read_bytes() == original
    assert json.loads(archive.read_bytes())['status'] == 'failed'
    assert record['binding'] == plan['binding']
    assert record['binding_sha256'] == plan['binding_sha256']
    assert record['plan_path'] == str(path)
    assert record['attempt'] == 1
    assert record['action'] == recovery.ACTION
    assert source.read_bytes() == before_source
    assert record['artifact_sha256'] == recovery._sha({k:v for k,v in record.items() if k!='artifact_sha256'})
    assert len(list(path.parent.rglob('reconciliation-*.json'))) == 1
    # The second provider failure may have identical error/input bytes. It is
    # still a distinct dispatch and gets no second allowance.
    save(path, plan)
    with pytest.raises(ValueError, match='allowance exhausted'):
        recovery.reconcile_rate_limited_generation(path, now=1000)
    assert path.read_bytes() == original
    assert archive.read_bytes() == original


@pytest.mark.parametrize('status,error', [
    ('dispatch_unknown', 'CodexSubscriptionError: '+recovery.TERMINAL_429),
    ('validated', 'CodexSubscriptionError: '+recovery.TERMINAL_429),
    ('failed', 'TimeoutError: '+recovery.TERMINAL_429),
    ('failed', 'CodexSubscriptionError: transport timeout'),
    ('failed', 'CodexSubscriptionError: '+recovery.TERMINAL_429+'\nERROR: timeout'),
    ('failed', 'CodexSubscriptionError: ERROR: exceeded retry limit, last status: 401 Unauthorized'),
])
def test_unknown_timeout_success_and_non429_never_receive_retry(failed, status, error):
    path, plan, _, _ = failed
    plan.update(status=status, error=error)
    save(path, plan)
    original = path.read_bytes()
    with pytest.raises(ValueError, match='terminal Codex HTTP 429'):
        recovery.reconcile_rate_limited_generation(path, now=1000)
    assert path.read_bytes() == original
    assert not (path.parent/'rate-limit-failures').exists()


@pytest.mark.parametrize('change', ['seal', 'source', 'prompt', 'model'])
def test_changed_frozen_input_refuses_recovery_without_artifacts(failed, change):
    path, plan, source, prompt = failed
    if change == 'source':
        source.write_text('changed source')
    elif change == 'prompt':
        prompt.write_text('changed prompt')
    elif change == 'model':
        plan['binding']['model'] = 'other-model'
        save(path, plan)
    else:
        plan['binding_sha256'] = '0'*64
        save(path, plan)
    original = path.read_bytes()
    with pytest.raises(ValueError, match='changed'):
        recovery.reconcile_rate_limited_generation(path, now=1000)
    assert path.read_bytes() == original
    assert not (path.parent/'rate-limit-failures').exists()


def test_age_and_retry_cap_checked_before_archiving(failed):
    path, _, _, _ = failed
    for kwargs in [dict(now=919), dict(now=1000, minimum_age_seconds=119),
                   dict(now=1000, maximum_retries=0), dict(now=1000, maximum_retries=2)]:
        with pytest.raises(ValueError):
            recovery.reconcile_rate_limited_generation(path, **kwargs)
        assert path.exists()
        assert not list(path.parent.rglob('reconciliation-*.json'))


def test_interrupted_reconciliation_completes_only_same_original_file(failed, monkeypatch):
    path, _, _, _ = failed
    original = path.read_bytes()
    unlink = Path.unlink
    def interrupted(self, *args, **kwargs):
        if self == path:
            raise OSError('interrupted before removing old dispatch file')
        return unlink(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', interrupted)
    with pytest.raises(OSError, match='interrupted'):
        recovery.reconcile_rate_limited_generation(path, now=1000)
    assert path.read_bytes() == original
    monkeypatch.setattr(Path, 'unlink', unlink)
    receipt = recovery.reconcile_rate_limited_generation(path, now=1001)
    assert receipt['attempt'] == 1
    assert not path.exists()
    assert len(list(path.parent.rglob('reconciliation-*.json'))) == 1
