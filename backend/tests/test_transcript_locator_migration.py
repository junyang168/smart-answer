import json
from unittest.mock import patch

import pytest

from backend.pipeline import transcript_locator_migration as flow
from backend.pipeline import transcript_evidence_apply as ea
from backend.pipeline import transcript_claim_migration as m
from backend.tests.test_transcript_evidence_apply import relocation
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres
from backend.tests.test_transcript_claim_anchor_sync import anchor


def setup(case):
    store, root, old, published = case
    c = store.get_record_state('claims', 'C')['payload']
    c['occurrences'][0]['anchors'] = [anchor('E', '教授原話'),
                                    anchor('E', '第二處是明確刪除的內容。', 'S0002', 5)]
    store.apply_plan(store.plan_package({'claims': [c]}))
    direction = root.parent / 'direction.txt'
    direction.write_text('只搬遷 Evidence 與 Claim 定位，排除軟刪除和口頭語；不呼叫模型。')
    args = dict(source_id='S', old_paths=[old], published=published, instruction_file=direction)
    return store, root, args


def test_sql_end_to_end_resume_and_readonly_prepare(relocation):
    store, root, args = setup(relocation)
    before = store.get_record_state('claims', 'C')
    preview = flow.prepare(store, root, **args)
    assert preview['exceptions'] == []
    assert store.get_record_state('claims', 'C') == before
    result = flow.run(store, root)
    assert result['updated_claims'] == 1
    assert result['model_calls'] == 0
    after = store.get_record_state('claims', 'C')
    assert after['payload']['statement'] == before['payload']['statement']
    assert len(after['payload']['occurrences'][0]['anchors']) == 1
    # No re-alignment or replay of the obsolete Evidence verification baseline.
    with patch.object(ea, 'apply', side_effect=AssertionError('Evidence replay')), \
         patch.object(ea, 'prepare', side_effect=AssertionError('Evidence replan')):
        assert flow.prepare(store, root, **args) == preview
        assert flow.run(store, root) == result
        assert flow.verify(store, root) == result
    assert store.get_record_state('claims', 'C') == after


def test_resume_after_evidence_commit_and_claim_reply_loss(relocation):
    store, root, args = setup(relocation)
    flow.prepare(store, root, **args)
    ea.apply(store, root / 'evidence')
    original = flow.anchors.apply
    def lose_reply(*a, **kw):
        original(*a, **kw)
        raise RuntimeError('lost Claim reply')
    with patch.object(flow.anchors, 'apply', side_effect=lose_reply):
        with pytest.raises(RuntimeError, match='lost Claim reply'):
            flow.run(store, root)
    before = store.get_record_state('claims', 'C')
    assert flow.run(store, root)['status'] == 'locator_migration_verified'
    assert store.get_record_state('claims', 'C') == before


def test_handoff_catches_edit_between_stages(relocation):
    store, root, args = setup(relocation)
    flow.prepare(store, root, **args)
    ea.apply(store, root / 'evidence')
    claim = store.get_record_state('claims', 'C')['payload']
    claim['statement'] = '保存另一位同工的修改'
    store.apply_plan(store.plan_package({'claims': [claim]}))
    with pytest.raises(ValueError, match='post-commit verification differs'):
        flow.run(store, root)
    assert not (root / 'anchors/plan.json').exists()


def test_changed_inputs_and_unknown_epoch_stop_before_write(relocation):
    store, root, args = setup(relocation)
    fragment = store.get_record_state('source_fragments', 'F1')['payload']
    fragment['source_sha256'] = '0' * 64
    store.apply_plan(store.plan_package({'source_fragments': [fragment]}))
    assert flow.prepare(store, root, **args)['exceptions']
    before = store.get_record_state('source_fragments', 'F2')
    with pytest.raises(ValueError, match='unresolved locators'):
        flow.run(store, root)
    assert store.get_record_state('source_fragments', 'F2') == before
    args['instruction_file'].write_text('changed')
    with pytest.raises(ValueError):
        flow.prepare(store, root, **args)


def test_same_run_worker_lock(tmp_path):
    with flow.run_lock(tmp_path):
        with pytest.raises(ValueError, match='already has a worker'):
            with flow.run_lock(tmp_path):
                pass


def test_scoped_migration_reserves_claim_stage_before_evidence_commit(relocation,monkeypatch):
    from types import SimpleNamespace
    from backend.pipeline import source_execution_locks as locks
    store,root,args=setup(relocation)
    monkeypatch.setenv('DATA_BASE_DIR',str(root.parent/'data'))
    flow.prepare(store,root,**args)
    before=store.get_record_state('source_fragments','F1')
    # Another cross-sermon task would write the same Claim in stage two.
    request=SimpleNamespace(source_bindings={'OTHER':None},target_keys=['claims/C'],permissions=[])
    with locks.shared_maintenance_gates(),locks.claim_execution_locks(request):
        with pytest.raises(BlockingIOError):
            with flow.execution_locks(root):flow.run(store,root)
    assert store.get_record_state('source_fragments','F1')==before
    independent=SimpleNamespace(source_bindings={'OTHER':None},target_keys=['claims/OTHER'],permissions=[])
    with locks.shared_maintenance_gates(),locks.claim_execution_locks(independent):
        with flow.execution_locks(root):
            assert flow.run(store,root)['updated_claims']==1


def test_post_evidence_edit_is_rejected_at_stage_handoff(relocation):
    store, root, args = setup(relocation)
    flow.prepare(store, root, **args)
    original = ea.apply
    def concurrent_change(*a, **kw):
        result = original(*a, **kw)
        claim = store.get_record_state('claims', 'C')['payload']
        claim['statement'] = '保留並行修改'
        store.apply_plan(store.plan_package({'claims': [claim]}))
        return result
    with patch.object(ea, 'apply', side_effect=concurrent_change):
        with pytest.raises(ValueError, match='handoff preimage differs'):
            flow.run(store, root)
    assert store.get_record_state('claims', 'C')['payload']['statement'] == '保留並行修改'
    assert not (root / 'result.json').exists()


def test_tampered_exception_preview_cannot_enable_apply(relocation):
    store, root, args = setup(relocation)
    flow.prepare(store, root, **args)
    preview = m.read(root / 'preview.json');preview['exceptions'] = [{'fake': True}]
    (root / 'preview.json').write_text(json.dumps(preview))
    with pytest.raises(ValueError):
        flow.run(store, root)


def test_fresh_run_on_already_synchronized_data(relocation):
    store, root, args = setup(relocation)
    flow.prepare(store, root, **args);flow.run(store, root)
    after = store.get_record_state('claims', 'C')
    root = root.parent / 'fresh-run'
    flow.prepare(store, root, **args)
    with patch.object(store, 'apply_plan', side_effect=AssertionError('no-op SQL write')):
        result = flow.run(store, root)
        assert flow.verify(store, root) == result
    assert result['evidence_change_set'] is None
    assert result['claim_change_set'] is None
    assert result['updated_claims'] == 0
    assert result['updated_evidence_records'] == 0
    assert store.get_record_state('claims', 'C') == after


def test_completed_run_detects_later_sql_drift(relocation):
    store, root, args = setup(relocation)
    flow.prepare(store, root, **args);flow.run(store, root)
    receipt = (root / 'result.json').read_bytes()
    claim = store.get_record_state('claims', 'C')['payload']
    claim['statement'] = '之後的合法新版本'
    store.apply_plan(store.plan_package({'claims': [claim]}))
    with pytest.raises(ValueError, match='postimage readback differs'):
        flow.verify(store, root)
    assert (root / 'result.json').read_bytes() == receipt


def test_pending_split_is_committed_in_source_order_and_used_by_update(relocation):
    from backend.pipeline.claim_flash_source_lookup import Locator

    store, root, args = setup(relocation)
    # Two live paragraphs in a distinct source version, both within the saved
    # playback window for F1. Keep the old quotation in the original fragment.
    published = args['published']
    doc = m.read(published)
    doc['script'].insert(1, {'index': 11, 'text': '教授緊接著回答。', 'start_time': 4, 'end_time': 5})
    published.write_text(json.dumps(doc))
    snapshot = ea.snap(published)
    source = store.get_record_state('source_documents', 'S')['payload']
    source_path = published.with_name('new.json')
    source_path.write_bytes(published.read_bytes())
    source.update(source_file_sha256=m.sha(source_path.read_bytes()),
                  source_sha256=snapshot['body_sha256'], source_body_sha256=snapshot['body_sha256'])
    store.apply_plan(store.plan_package({'source_documents': [source]}))
    # Include the previous registered source for the already-current F3.
    f3 = store.get_record_state('source_fragments', 'F3')['payload']
    f3.update(source_sha256=ea.snap(args['old_paths'][0])['body_sha256'])
    store.apply_plan(store.plan_package({'source_fragments': [f3]}))
    before = store.get_record_state('source_fragments', 'F1')
    calibration = root.parent / 'calibration.json'
    calibration.write_text(json.dumps({'fragments': {'source_fragments/F1': {
        'mode': 'paragraph_location', 'paragraph_keys': ['S0001', 'S0002'],
        'before_pin': [before['revision'], before['content_sha256']],
        'target_source_sha256': snapshot['body_sha256'], 'reason': '同一次發言跨兩段',
    }}}))
    assert not flow.prepare(store, root, **args, calibration_path=calibration)['exceptions']
    result = flow.run(store, root)
    assert flow.verify(store, root) == result
    parent = store.get_record_state('source_fragments', 'F1')['payload']
    children = parent['replacement_fragment_ids']
    assert parent['verbatim_excerpt'] == before['payload']['verbatim_excerpt']
    assert store.get_record_state('evidence_steps', 'E')['payload']['source_fragment_ids'] == [*children, 'F2']
    records = {'source_fragments/' + fid: store.get_record_state('source_fragments', fid) for fid in children}
    assert [r['payload']['paragraph_key'] for r in records.values()] == ['S0001', 'S0002']
    assert all(not r['payload']['verbatim_excerpt'] for r in records.values())
    key = 'questions/Q'
    records[key] = {'payload': {'text': '問題', 'source_fragment_ids': children}}
    sources = {'S': snapshot}
    material, quotes = Locator(sources).prepare(key, records, sources)
    assert quotes is None and material['original_text_excerpts'] == []
    assert '教授緊接著回答。' in [p['text'] for p in material['nearby_paragraphs']]
    fresh = flow.prepare(store, root.with_name('fresh'), **args)
    assert fresh['exceptions'] == []
    assert fresh['evidence_updates'] == 0
    stale = {'S': {**snapshot, 'body_sha256': 'newer'}}
    with pytest.raises(ValueError, match='pending quotation location is stale'):
        Locator(stale).prepare(key, records, stale)


def test_flash_preflight_reuses_existing_locator_transaction(relocation):
    store, root, args = setup(relocation)
    assert flow.preflight(store, 'S', published=args['published'])['status'] == 'locator_required'
    flow.prepare(store, root, **args)
    assert flow.preflight(store, 'S', published=args['published'], locator_root=root)['status'] == 'migrated'
    assert flow.preflight(store, 'S', published=args['published'])['status'] == 'not_needed'
    args['published'].write_text('{"script":[{"index":1,"text":"different publication"}]}')
    assert flow.preflight(store, 'S', published=args['published'])['status'] == 'registration_required'
