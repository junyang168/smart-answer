"""Frozen heading batches exercise real source bytes and insertion proof callbacks."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.api.canonical_repository.postgres_store import record_content_sha, sha256_json
from backend.pipeline import detailed_knowledge_extraction_runner as extraction
from backend.pipeline import source_editorial_transition as transition
from backend.pipeline import source_heading_batch_runner as batch
from backend.pipeline.sermon_subtitle_persistence import write_back_generated_subtitles
from backend.pipeline.source_heading_inventory import build_source_heading_inventory
from backend.pipeline.source_projection import project_script


def seal(value):
    value = copy.deepcopy(value)
    value.pop('manifest_sha256', None)
    return value | {'manifest_sha256': sha256_json(value)}


class Store:
    def __init__(self, records):
        self.records = copy.deepcopy(records)

    def list_records(self, collection):
        return [s['payload'] for (c, _), s in self.records.items() if c == collection and not s['retired']]

    def get_record_state(self, collection, ident):
        return copy.deepcopy(self.records.get((collection, ident)))


def state(payload):
    return {'payload': payload, 'revision': 1, 'retired': False,
            'content_sha256': record_content_sha(payload)}


@pytest.fixture
def sample(tmp_path, monkeypatch):
    proof_root = tmp_path / 'proofs'
    monkeypatch.setattr(transition, 'transition_root', lambda root=None: proof_root)
    records = {}
    paths = {}
    for sid, statement in [('source-A', '其他主題'), ('source-B', '人子的稱號')]:
        path = tmp_path / 'script_published' / (sid + '.json')
        path.parent.mkdir(exist_ok=True)
        payload = {'metadata': {'title': sid, 'status': 'published'}, 'script': [
            {'index': 1, 'text': '教授的第一段。', 'start_time': 0.2},
            {'index': 2, 'text': '教授的第二段。', 'end_time': 2.0},
            {'index': 3, 'text': '教授的第三段。'},
        ]}
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        source = {'source_id': sid, 'transcript_id': sid, 'source_type': 'sermon_transcript',
                  'title': sid, 'source_path': str(path), 'locator_space': 'spoken_body_v1',
                  'source_file_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                  'source_body_sha256': project_script(payload['script']).body_sha256}
        records['source_documents', sid] = state(source)
        records['claims', 'claim-' + sid] = state({'claim_id': 'claim-' + sid,
            'statement': statement, 'occurrences': [{'source_id': sid}]})
        paths[sid] = path
    records['canonical_viewpoints', 'cvp-1'] = state({'viewpoint_id': 'cvp-1', 'statement': '人子'})
    store = Store(records)
    manager = Manager(paths, proof_root)
    inventory = build_source_heading_inventory(store)
    manifest = batch.batch_manifest(inventory, actor_id='editor', model='fake-model', effort='high')
    return SimpleNamespace(store=store, manager=manager, inventory=inventory, manifest=manifest,
                           out=tmp_path / 'out', client=SimpleNamespace(model='fake-model', reasoning_effort='high'),
                           paths=paths, proof_root=proof_root)


class Manager:
    def __init__(self, paths, proof_root):
        self.paths, self.proof_root = paths, proof_root
        self.calls = 0
        self.lose_reply = False

    def can_persist_generated_subtitles(self, actor):
        return actor == 'editor'

    def authoritative_transcript_path(self, item):
        return self.paths[item]

    def persist_generated_subtitles(self, actor, item, *, expected_source_sha256, insertions):
        self.calls += 1
        path = self.paths[item]
        # A durable, verifiable proof must precede even the actual CAS write.
        receipt_path = transition.receipt_path({'path': str(path), 'file_sha256': expected_source_sha256})
        assert receipt_path.is_file()
        receipt = json.loads(receipt_path.read_bytes())
        assert receipt['proof']['semantic_approval'] is False
        assert path.read_bytes() == Path(receipt['proof']['before']['path']).read_bytes()
        report = write_back_generated_subtitles(path, expected_source_sha256=expected_source_sha256,
                                                insertions=insertions, actor_id=actor)
        if self.lose_reply:
            self.lose_reply = False
            raise RuntimeError('lost writer reply after committed CAS')
        return report


def executor(path, **kwargs):
    payload = json.loads(path.read_bytes())
    if project_script(payload['script']).headings:
        return 'skipped', path
    kwargs['subtitle_writer'](kwargs['subtitle_actor_id'], path.stem,
        expected_source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        insertions=[{'after_index': 'START', 'text': '## 人子的稱號', 'level': 1}])
    return 'created', path


def apply(sample, **kwargs):
    return batch.apply_batch(sample.store, sample.manifest, output_dir=sample.out,
                             client=sample.client, manager=sample.manager, **kwargs)


def test_batch_manifest_freezes_priority_limit_and_scope(sample):
    assert [r['source_id'] for r in sample.manifest['sources']] == ['source-B', 'source-A']
    limited = batch.batch_manifest(sample.inventory, actor_id='editor', model='fake-model',
                                  effort='high', priority_only=True, limit=1)
    assert limited['priority_only'] is True and limited['limit'] == 1
    assert [r['source_id'] for r in limited['sources']] == ['source-B']
    assert limited['manifest_sha256'] != sample.manifest['manifest_sha256']
    batch.validate_manifest(limited)
    with pytest.raises(ValueError, match='positive'):
        batch.batch_manifest(sample.inventory, actor_id='editor', model='fake-model', effort='high', limit=0)


@pytest.mark.parametrize('field,value', [('limit', 1), ('priority_only', True), ('model', 'changed'), ('effort', 'low')])
def test_batch_manifest_rejects_unsealed_option_drift(sample, field, value):
    sample.manifest[field] = value
    with pytest.raises(ValueError, match='manifest changed'):
        apply(sample, execute=executor)
    assert sample.manager.calls == 0


def test_batch_rejects_changed_prompt_and_client_before_source_save(sample, tmp_path, monkeypatch):
    prompt = tmp_path / 'prompt.md'
    prompt.write_text('changed prompt')
    monkeypatch.setattr(batch, 'PROMPT_PATH', prompt)
    with pytest.raises(ValueError, match='prompt changed'):
        apply(sample, execute=executor)
    assert sample.manager.calls == 0


@pytest.mark.parametrize('field,value', [('model', 'changed'), ('reasoning_effort', 'low')])
def test_batch_rejects_runtime_model_settings_drift(sample, field, value):
    setattr(sample.client, field, value)
    with pytest.raises(ValueError, match='client differs'):
        apply(sample, execute=executor)
    assert sample.manager.calls == 0


def test_batch_adds_only_headings_proof_precedes_cas_sql_unchanged_and_resume_skips(sample):
    before_sql = copy.deepcopy(sample.store.records)
    before = {sid: json.loads(path.read_bytes()) for sid, path in sample.paths.items()}
    result = apply(sample, execute=executor)
    assert result['completed'] == result['planned'] == 2
    assert sample.manager.calls == 2
    assert sample.store.records == before_sql
    for sid, path in sample.paths.items():
        after = json.loads(path.read_bytes())
        transition.insertion_facts(before[sid], after)
        assert after['metadata'] == before[sid]['metadata']
        assert result['results'][0]['current_source']['file_sha256'] != sample.manifest['sources'][0]['current_file_sha256']
    replay = apply(sample, execute=executor)
    assert replay['completed'] == 2
    assert all(r['application'] == 'skipped' for r in replay['results'])
    assert sample.manager.calls == 2
    assert sample.store.records == before_sql


@pytest.mark.parametrize('bad_status', ['unknown', 'failed', None])
def test_unknown_executor_result_never_reports_completion(sample, bad_status):
    with pytest.raises(ValueError, match='unknown subtitle application result'):
        apply(sample, execute=lambda path, **kwargs: (bad_status, path))
    result = json.loads((sample.out / 'results.json').read_bytes())
    assert result['completed'] == 0
    assert result['results'][0]['status'] == 'incomplete'


@pytest.mark.parametrize('false_status', ['created', 'skipped'])
def test_headingless_executor_noop_never_reports_completion(sample, false_status):
    with pytest.raises(ValueError, match='heading|untitled|application'):
        apply(sample, execute=lambda path, **kwargs: (false_status, path))
    result = json.loads((sample.out / 'results.json').read_bytes())
    assert result['completed'] == 0


def test_lost_writer_reply_reconciles_exact_committed_plan_without_generating_twice(sample, monkeypatch):
    calls = []
    def generate(*args, **kwargs):
        calls.append(args)
        return [{'after_index': 'START', 'text': '## 人子的稱號', 'level': 1}]
    monkeypatch.setattr(extraction, 'generate_subtitles', generate)
    sample.manager.lose_reply = True
    with pytest.raises(RuntimeError, match='lost writer reply'):
        apply(sample)
    first = json.loads((sample.out / 'results.json').read_bytes())
    assert first['completed'] == 0
    result = apply(sample)
    assert result['completed'] == 2
    assert len(calls) == 2  # one dispatch per source, including the already committed source
    assert sample.manager.calls == 2
    audits = list(sample.out.rglob('application.json'))
    assert len(audits) == 2
    assert all(json.loads(p.read_bytes())['status'] == 'persisted' for p in audits)


def test_batch_unknown_dispatch_stays_incomplete_without_second_model_call(sample, monkeypatch):
    calls = []
    def interrupt(*args, **kwargs):
        calls.append(args)
        raise KeyboardInterrupt('dispatched but no recorded response')
    monkeypatch.setattr(extraction, 'generate_subtitles', interrupt)
    with pytest.raises(KeyboardInterrupt):
        apply(sample)
    with pytest.raises(RuntimeError, match='dispatch_unknown.*redispatch refused'):
        apply(sample)
    assert len(calls) == 1
    assert sample.manager.calls == 0
    result = json.loads((sample.out / 'results.json').read_bytes())
    assert result['completed'] == 0
    assert result['results'][0]['status'] == 'incomplete'


@pytest.mark.parametrize('mutation', ['retired', 'revision', 'payload'])
def test_batch_refuses_changed_sql_source_pin_without_any_source_write(sample, mutation):
    item = sample.store.records['source_documents', 'source-B']
    if mutation == 'retired':
        item['retired'] = True
    elif mutation == 'revision':
        item['revision'] = 2
    else:
        item['payload']['title'] = 'Changed registered source'
    with pytest.raises(ValueError, match='registered source changed'):
        apply(sample, execute=executor)
    assert sample.manager.calls == 0


def test_batch_refuses_unproven_source_mutation(sample):
    path = sample.paths['source-B']
    before = json.loads(path.read_bytes())
    before['script'][0]['text'] += '不應接受的修改。'
    path.write_text(json.dumps(before, ensure_ascii=False), encoding='utf-8')
    with pytest.raises((FileNotFoundError, ValueError)):
        apply(sample, execute=executor)
    assert sample.manager.calls == 0


def test_batch_refuses_empty_executor_source_even_with_valid_heading_receipt(sample):
    # An executor's reported success is not authority for deleting source body.
    def empty(path, **kwargs):
        kwargs['subtitle_writer'](kwargs['subtitle_actor_id'], path.stem,
            expected_source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            insertions=[{'after_index': 'START', 'text': '## 標題', 'level': 1}])
        payload = json.loads(path.read_bytes())
        payload['script'] = [payload['script'][0]]
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        return 'created', path
    with pytest.raises((FileNotFoundError, ValueError)):
        apply(sample, execute=empty)
    result = json.loads((sample.out / 'results.json').read_bytes())
    assert result['completed'] == 0


def test_batch_manifest_refuses_inventory_with_blocking_current_input_issues(sample):
    inventory = copy.deepcopy(sample.inventory)
    inventory['blocking_issues'] = [{'key': 'claims/current-claim', 'reason': 'stored_content_sha_mismatch'}]
    with pytest.raises(ValueError, match='unresolved current input integrity errors'):
        batch.batch_manifest(inventory, actor_id='editor', model='fake-model', effort='high',
                             priority_only=True, limit=1)
    assert sample.manager.calls == 0


def unregistered_manifest(sample):
    """Freeze a filesystem transcript that has never entered knowledge SQL."""
    manifest = copy.deepcopy(sample.manifest)
    manifest['sources'] = [r for r in manifest['sources'] if r['source_id'] == 'source-B']
    manifest['sources'][0]['source_pin'] = None
    manifest['sources'][0]['source_id'] = 'transcript-file:source-B'
    manifest['sources'][0]['source']['source_id'] = 'transcript-file:source-B'
    manifest['sources'][0]['metadata_origin'] = 'authoritative_transcript_file'
    manifest['sources'][0]['source']['metadata_origin'] = 'authoritative_transcript_file'
    sample.store.records.pop(('source_documents', 'source-B'))
    return seal(manifest)


def test_unregistered_transcript_gets_only_headings_and_no_sql_records(sample):
    sample.manifest = unregistered_manifest(sample)
    before = copy.deepcopy(sample.store.records)
    result = apply(sample, execute=executor)
    assert result['completed'] == 1
    assert sample.store.records == before
    assert ('source_documents', 'source-B') not in sample.store.records
    after = json.loads(sample.paths['source-B'].read_bytes())
    assert after['script'][0]['type'] == 'subtitle'
    assert sample.manager.calls == 1
    replay = apply(sample, execute=executor)
    assert replay['completed'] == 1
    assert replay['results'][0]['application'] == 'skipped'
    assert sample.manager.calls == 1
    assert sample.store.records == before


@pytest.mark.parametrize('registration_id', ['transcript-file:source-B', 'new-source-id'])
def test_unregistered_transcript_refuses_registration_appearing_after_freeze(sample, registration_id):
    sample.manifest = unregistered_manifest(sample)
    source = copy.deepcopy(sample.manifest['sources'][0]['source'])
    source['source_id'] = registration_id
    sample.store.records['source_documents', registration_id] = state(source)
    before = sample.paths['source-B'].read_bytes()
    with pytest.raises(ValueError, match='registration|source.*changed'):
        apply(sample, execute=executor)
    assert sample.manager.calls == 0
    assert sample.paths['source-B'].read_bytes() == before


def test_three_worker_assignments_are_disjoint_exact_frozen_manifest_cover(sample):
    inventory = copy.deepcopy(sample.inventory)
    template = inventory['generation_plan'][0]
    inventory['generation_plan'] = [dict(template, source_id=f'S{i}', path=f'/synthetic/script_published/S{i}.json') for i in range(11)]
    manifest = batch.batch_manifest(inventory, actor_id='editor', model='fake-model', effort='high', worker_count=3)
    assert manifest['worker_count'] == 3
    shards = [batch.assigned_sources(manifest, worker_count=3, worker_index=i) for i in range(3)]
    ids = [{row['source_id'] for row in shard} for shard in shards]
    assert sorted(map(len, shards)) == [3, 4, 4]
    assert set.union(*ids) == {row['source_id'] for row in manifest['sources']}
    assert not (ids[0] & ids[1] or ids[1] & ids[2] or ids[0] & ids[2])
    assert [r['source_id'] for r in shards[0]] == ['S0', 'S3', 'S6', 'S9']
    with pytest.raises(ValueError, match='worker'):
        batch.assigned_sources(manifest, worker_count=2, worker_index=0)
    with pytest.raises(ValueError, match='worker'):
        batch.assigned_sources(manifest, worker_count=3, worker_index=3)
    changed = copy.deepcopy(manifest)
    changed['worker_count'] = 2
    with pytest.raises(ValueError, match='manifest changed'):
        batch.validate_manifest(changed)


def test_three_worker_apply_keeps_progress_separate_and_never_rewrites_completed_sources(sample):
    sample.manifest = batch.batch_manifest(sample.inventory, actor_id='editor', model='fake-model',
                                           effort='high', worker_count=3)
    before_sql = copy.deepcopy(sample.store.records)
    summaries = [apply(sample, execute=executor, worker_count=3, worker_index=i) for i in range(3)]
    assert sum(s['planned'] for s in summaries) == 2
    assert sum(s['completed'] for s in summaries) == 2
    assert sample.manager.calls == 2
    paths = [sample.out / 'workers' / f'{i:03d}-of-003' / 'results.json' for i in range(3)]
    assert all(p.is_file() for p in paths)
    assert [json.loads(p.read_bytes())['worker_index'] for p in paths] == [0, 1, 2]
    all_ids = [r['source_id'] for s in summaries for r in s['results']]
    assert len(all_ids) == len(set(all_ids)) == 2
    for i in range(3):
        replay = apply(sample, execute=executor, worker_count=3, worker_index=i)
        assert all(r['application'] == 'skipped' for r in replay['results'])
    assert sample.manager.calls == 2
    assert sample.store.records == before_sql
