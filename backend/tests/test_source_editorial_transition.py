import copy
import json
from pathlib import Path

import pytest

from backend.pipeline import source_editorial_transition as transition
from backend.pipeline.source_projection import project_script


def encode(value):
    return json.dumps(value, ensure_ascii=False).encode()


@pytest.fixture
def sample(tmp_path):
    original = {'metadata': {'title': '人子', 'status': 'reviewed', 'revision': 1},
                'script': [{'index': 1, 'start_time': 0.1, 'text': '教授的原話。', 'type': 'content'},
                           {'index': 2, 'end_time': 4.0, 'text': '所以，這是結論。', 'user_id': 'a'}]}
    after = copy.deepcopy(original)
    after['script'].insert(0, {'index':'subtitle-test', 'type':'subtitle', 'text':'## 人子的稱號', 'user_id':'worker'})
    path = tmp_path / 'source.json'
    path.write_bytes(encode(original))
    source = {'source_id': 'source-1', 'source_type': 'sermon_transcript',
              'source_path': str(path), 'source_file_sha256': transition.file_binding(path)['file_sha256'],
              'source_body_sha256': project_script(original['script']).body_sha256,
              'locator_space': 'spoken_body_v1'}
    return original, after, path, source, tmp_path/'proofs'


def publish(sample):
    original, after, path, source, root = sample
    proof = transition.publish_transition(source=source, before_raw=encode(original), after_raw=encode(after),
                                          authorization='User requested subtitles', root=root)
    return proof


def test_exact_proof_preserves_old_binding_and_allows_current_file(sample):
    original, after, path, source, root = sample
    old = transition.file_binding(path)
    proof = publish(sample)
    # Pre-CAS receipt grants nothing until exact after bytes appear.
    assert transition.verify_current_source_binding(old, root=root) == old
    path.write_bytes(encode(after))
    assert transition.verify_current_source_binding(old, root=root) == transition.file_binding(path)
    receipt = json.loads(Path(proof['path']).read_bytes())
    assert receipt['proof']['semantic_approval'] is False
    assert receipt['proof']['claim_qualification_created'] is False
    assert receipt['source'] == source
    assert json.loads(Path(receipt['proof']['before']['path']).read_bytes()) == original
    assert publish(sample) == proof


@pytest.mark.parametrize('mutation', ['text','index','time','metadata','bool_metadata','existing_type','heading_type','extra_field','non_heading','delete','reorder'])
def test_any_non_insertion_change_rejected(sample, mutation):
    original, after, path, source, root = sample
    if mutation == 'text': after['script'][1]['text'] += '新增'
    elif mutation == 'index': after['script'][1]['index'] = 9
    elif mutation == 'time': after['script'][1]['start_time'] = 0.2
    elif mutation == 'metadata': after['metadata']['title'] = '新'
    elif mutation == 'bool_metadata': after['metadata']['revision'] = True
    elif mutation == 'existing_type': after['script'][1]['type'] = 'subtitle'
    elif mutation == 'heading_type': after['script'][0]['type'] = 'comment'
    elif mutation == 'extra_field': after['script'][0]['start_time'] = 5
    elif mutation == 'non_heading': after['script'][0]['text'] = '不是標題'
    elif mutation == 'delete': after['script'].pop()
    else: after['script'][1:] = reversed(after['script'][1:])
    with pytest.raises((ValueError, RuntimeError)):
        publish(sample)
    assert path.read_bytes() == encode(original)


def test_tampered_archive_and_live_change_fail_closed(sample):
    original, after, path, source, root = sample
    old = transition.file_binding(path)
    proof = publish(sample)
    path.write_bytes(encode(after))
    receipt = json.loads(Path(proof['path']).read_bytes())
    archive = Path(receipt['proof']['before']['path'])
    archive.write_text('{}')
    with pytest.raises(ValueError, match='physical SHA'):
        transition.verify_current_source_binding(old, root=root)


def test_missing_or_unrelated_receipt_does_not_accept_live_mutation(sample):
    original, after, path, source, root = sample
    old = transition.file_binding(path)
    publish(sample)
    after['script'][1]['text'] = '被更改的原文'
    path.write_bytes(encode(after))
    with pytest.raises(ValueError, match="physical SHA drift"):
        transition.verify_current_source_binding(old, root=root)


def test_bounded_chain_replays_only_exact_subtitle_insertions(sample):
    original, after, path, source, root = sample
    old = transition.file_binding(path)
    publish(sample)
    path.write_bytes(encode(after))
    next_source = source | {'source_file_sha256': transition.file_binding(path)['file_sha256']}
    third = copy.deepcopy(after)
    third['script'].insert(2, {'index':'subtitle-next', 'type':'subtitle', 'text':'### 論證'})
    transition.publish_transition(source=next_source, before_raw=encode(after), after_raw=encode(third),
                                   authorization='More headings', root=root)
    path.write_bytes(encode(third))
    assert transition.verify_current_source_binding(old, root=root) == transition.file_binding(path)


def test_context_loader_consumes_proof_and_retains_body_coordinates(sample, monkeypatch):
    from backend.pipeline.viewpoint_evidence_source_context import load_context_source
    original, after, path, source, root = sample
    publish(sample)
    path.write_bytes(encode(after))
    monkeypatch.setattr(transition, 'transition_root', lambda root=None: sample[-1])
    loaded = load_context_source(source)
    assert loaded[1] == transition.file_binding(path)['file_sha256']
    assert [r['text'] for r in loaded[2].spoken_rows] == [r['text'] for r in original['script']]


def test_invalid_original_registered_body_cannot_make_proof(sample):
    sample[3]['source_body_sha256'] = '0'*64
    with pytest.raises(ValueError, match='registered source'):
        publish(sample)


@pytest.mark.parametrize('fault', [None, 'text', 'offset', 'paragraph', 'forged_sha', 'no_proof'])
def test_saved_context_survives_metadata_only_with_exact_proof(sample, monkeypatch, fault):
    from types import SimpleNamespace
    from backend.pipeline.viewpoint_evidence_source_context import compile_source_context, verify_source_context
    original, after, path, source, root = sample
    evidence = SimpleNamespace(source_id=source['source_id'], paragraph_key='S0001', source_modality=None,
                               verbatim_excerpt='教授的原話。', source_context=None)
    evidence.source_context = compile_source_context(evidence, source)
    saved = evidence.source_context.model_dump(mode='json')
    if fault != 'no_proof':
        publish(sample)
    path.write_bytes(encode(after))
    monkeypatch.setattr(transition, 'transition_root', lambda root=None: sample[-1])
    if fault == 'text':
        evidence.source_context = evidence.source_context.model_copy(update={'paragraph_text':'不同的話'})
    elif fault == 'offset':
        evidence.source_context = evidence.source_context.model_copy(update={'excerpt_start_char':1})
    elif fault == 'paragraph':
        evidence.source_context = evidence.source_context.model_copy(update={'paragraph_key':'S0002'})
    elif fault == 'forged_sha':
        evidence.source_context = evidence.source_context.model_copy(update={'source_file_sha256':'f'*64})
    if fault:
        with pytest.raises(ValueError):
            verify_source_context(evidence, source)
    else:
        verify_source_context(evidence, source)
        assert evidence.source_context.model_dump(mode='json') == saved


def test_completion_uses_metadata_proof_without_sql_epoch_transition(sample, monkeypatch):
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    _, after, path, _, root = sample
    old = transition.file_binding(path)
    publish(sample)
    path.write_bytes(encode(after))
    monkeypatch.setattr(transition, 'transition_root', lambda root=None: sample[-1])
    class NoSQL:
        def execute(self, *args, **kwargs):
            raise AssertionError('metadata headings must not require SQL source transition')
    PostgresKnowledgeStore._assert_completion_source_generations(NoSQL(), [old], {})


def test_saved_cvp_resume_accepts_only_source_metadata_without_model_or_packet_rewrite(sample, monkeypatch):
    from types import SimpleNamespace
    from backend.pipeline import cvp_sql_flow as flow
    _, after, path, _, root = sample
    old = transition.file_binding(path)
    proof = publish(sample)
    path.write_bytes(encode(after))
    monkeypatch.setattr(transition, 'transition_root', lambda root=None: sample[-1])
    document = {'checkpoint':'reviewed', 'reviewed_sha256':'a'*64, 'reviewed':{
        'plan': {'source_kind':'cvp_batch_resolution'},
        'evidence': {'sources':[old], 'backup':{'dump':proof}, 'approval':{},
                     'read_pins':[], 'execution': {'original':'reviewed'}}}}
    original = copy.deepcopy(document)
    plan = SimpleNamespace(source_kind='cvp_batch_resolution')
    monkeypatch.setattr(flow, 'plan_from_document', lambda value: plan)
    class Store:
        retained = 0
        applied = 0
        def get_cvp_group(self, run_id):
            return copy.deepcopy(document)
        def retain_reviewed_cvp_relations(self, run_id, **kwargs):
            self.retained += 1
        def apply_plan(self, value, **kwargs):
            assert value is plan
            self.applied += 1
            return 'applied existing reviewed plan'
    store = Store()
    assert flow.resume_reviewed_action(store, 'saved-cvp') == 'applied existing reviewed plan'
    assert store.retained == store.applied == 1
    assert document == original


@pytest.mark.parametrize('duplicate_kind', ['body_body', 'comment_body'])
@pytest.mark.parametrize('fault', [None, 'change_first', 'change_second', 'reorder', 'duplicate_heading', 'colliding_heading'])
def test_repeated_original_ids_preserved_exactly_without_renumbering(sample, duplicate_kind, fault):
    original, after, path, source, root = sample
    original['script'][1]['index'] = original['script'][0]['index']
    if duplicate_kind == 'comment_body':
        original['script'][0]['type'] = 'comment'
    after.clear()
    after.update(copy.deepcopy(original))
    after['script'].insert(0, {'index':'subtitle-new', 'type':'subtitle', 'text':'## 新標題'})
    source['source_body_sha256'] = project_script(original['script']).body_sha256
    path.write_bytes(encode(original))
    source['source_file_sha256'] = transition.file_binding(path)['file_sha256']
    if fault == 'change_first':
        after['script'][1]['text'] += '篡改'
    elif fault == 'change_second':
        after['script'][2]['text'] += '篡改'
    elif fault == 'reorder':
        after['script'][1:] = reversed(after['script'][1:])
    elif fault == 'duplicate_heading':
        after['script'].append(copy.deepcopy(after['script'][0]))
    elif fault == 'colliding_heading':
        after['script'][0]['index'] = original['script'][0]['index']
    if fault:
        with pytest.raises((ValueError, RuntimeError)):
            publish(sample)
    else:
        old_binding = transition.file_binding(path)
        publish(sample)
        path.write_bytes(encode(after))
        assert transition.verify_current_source_binding(old_binding, root=root) == transition.file_binding(path)
        assert [row for row in json.loads(path.read_bytes())['script']
                if row.get('type') != 'subtitle'] == original['script']
