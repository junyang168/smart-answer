import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from backend.pipeline import transcript_three_way_merge as merge
from backend.tests.test_transcript_review import setup, adopted
from backend.api.sc_api.transcript_review import atomic, ReviewError


def doc(texts):
    return [{'index': str(i), 'text': text, 'start_time': i * 10, 'end_time': (i + 1) * 10}
            for i, text in enumerate(texts, 1)]


BASE = '這一段前面先說明上帝的國度和永生的關係，我們看見始於來世的生命，現在再往下看重要的經文內容。'


def test_correction_and_resegmentation_preserve_new_clocks_and_headings():
    base = doc([BASE]); old = doc([BASE.replace('始於', '屬於')])
    new = doc([BASE[:27], BASE[27:]])
    new.insert(0, {'index': 'heading', 'type': 'subtitle', 'text': '## 新稿標題'})
    result = merge.merge(base, old, new)
    assert result['merge_complete'] and result['summary'] == {'transfer': 1}
    assert ''.join(p['text'] for p in result['script'][1:]) == old[0]['text']
    assert result['script'][0] == new[0]
    for a, b in zip(result['script'], new):
        assert {k: v for k, v in a.items() if k != 'text'} == {k: v for k, v in b.items() if k != 'text'}


def test_same_correction_already_in_new_and_no_edit_case():
    base = doc([BASE]); old = doc([BASE.replace('始於', '屬於')])
    assert merge.merge(base, old, old)['summary'] == {'already_present': 1}
    assert merge.merge(base, base, old)['script'] == old


def test_soft_deletion_crosses_paragraphs_and_is_not_duplicated():
    first = '這一段前面先說明上帝的國度和永生的關係，'
    middle = '這些現場打斷的話先不要放進正式閱讀。'
    last = '現在回到我們剛才講過的經文來看。'
    base = doc([first + middle + last]); old = doc([first + '~~' + middle + '~~' + last])
    new = doc([first + middle[:8], middle[8:] + last])
    result = merge.merge(base, old, new)
    assert result['merge_complete']
    assert result['script'][0]['text'] == first + '~~' + middle[:8] + '~~'
    assert result['script'][1]['text'] == '~~' + middle[8:] + '~~' + last
    again = merge.merge(base, old, result['script'])
    assert again['script'] == result['script']


def test_hard_editor_removal_becomes_soft_deletion_and_overlapping_rewrite_conflicts():
    base = doc([BASE]); old = doc([BASE.replace('始於來世的生命', '')])
    result = merge.merge(base, old, base)
    assert '~~始於來世的生命~~' in result['script'][0]['text']
    current = doc([BASE.replace('始於', '屬於')]); new = doc([BASE.replace('始於', '將在')])
    result = merge.merge(base, current, new)
    assert not result['merge_complete'] and result['script'] == new
    hunk = result['changes'][0]
    span = [new[0]['text'].index('將在'), new[0]['text'].index('將在') + 2]
    resolved = merge.merge(base, current, new, resolutions={'changes': {hunk['id']: {
        'decision': 'transfer', 'target_span': span, 'expected_incoming': '將在',
        'existing_span': [current[0]['text'].index('屬於'), current[0]['text'].index('屬於') + 2],
        'basis': 'saved_source_context', 'reason': '精確定位舊版人工校正的同一用詞'}}})
    assert resolved['merge_complete'] and '屬於' in resolved['script'][0]['text']


def test_repeated_short_context_is_not_guessed_and_malformed_markers_rejected():
    base = doc(['好。' * 30]); old = doc(['好。' * 10 + '~~好。~~' + '好。' * 19])
    assert not merge.merge(base, old, doc(['好。' * 31]))['merge_complete']
    with pytest.raises(ValueError, match='unbalanced'):
        merge.merge(doc([BASE]), doc(['~~不完整']), doc([BASE]))


def test_frozen_prepare_rejects_source_and_report_drift(tmp_path):
    args = {}
    for name, value in [('baseline', doc([BASE])), ('existing', doc([BASE.replace('始於', '屬於')])), ('incoming', doc([BASE]))]:
        args[name] = tmp_path / (name + '.json');args[name].write_text(json.dumps(value))
    args['instruction_file'] = tmp_path / 'direction.txt';args['instruction_file'].write_text('合併既有修改，保留歷史。')
    root = tmp_path / 'run'
    assert merge.prepare(root, item='lecture', **args)['merge_complete']
    assert merge.prepare(root, item='lecture', **args)['merge_complete']
    args['existing'].write_text('[]')
    with pytest.raises(ValueError, match='inputs changed'):
        merge.prepare(root, item='lecture', **args)


def test_editor_merge_uses_history_owner_CAS_and_response_recovery(setup, tmp_path):
    store, root, _ = setup
    adopted(setup)
    incoming = store.document('a', '講道 test')['script']
    d = store.document('a', '講道 test')
    store.mutate('a', '講道 test', {
        'version': d['version'], 'epoch': d['epoch'], 'operation_id': 'fixture-long-text',
        'operations': [{'kind': 'text', 'id': incoming[0]['index'], 'text': BASE}]})
    incoming = store.document('a', '講道 test')['script']
    base = deepcopy(incoming); old = deepcopy(incoming)
    # Real fixture source has short paragraphs: wrap a uniquely contextual whole row.
    old[0]['text'] = '~~' + old[0]['text'] + '~~'
    paths = {}
    for name, value in [('baseline', base), ('existing', old), ('incoming', incoming)]:
        paths[name] = tmp_path / (name + '.json');atomic(paths[name], value)
    direction = tmp_path / 'direction.txt';direction.write_text('將既有軟刪除帶入新稿。')
    run = tmp_path / 'merge'
    result = merge.prepare(run, item='講道 test', instruction_file=direction, **paths)
    assert result['merge_complete']
    before = store.document('a', '講道 test')
    applied = merge.apply(store, run, actor='a')
    assert applied['revision'] == before['context']['revision'] + 1
    assert merge.apply(store, run, actor='a') == applied
    assert store.document('a', '講道 test')['script'][0]['text'].startswith('~~')
    assert not (root / 'script_published/講道 test.json').exists()


def replacement_case(setup, tmp_path):
    import shutil
    from backend.api.sc_api.transcript_review import ReviewStore
    from backend.pipeline import transcript_review_promotion as promotion
    target, root, _ = setup
    adopted(setup)
    d = target.document('a', '講道 test')
    pid = d['script'][0]['index']
    def set_text(store, text, opid):
        d = store.document('a', '講道 test')
        store.mutate('a', '講道 test', {'version': d['version'], 'epoch': d['epoch'],
            'operation_id': opid, 'operations': [{'kind': 'text', 'id': pid, 'text': text}]})
    set_text(target, BASE.replace('始於', '屬於'), 'old-human-correction')
    beta = tmp_path / 'incoming-review'
    shutil.copytree(root, beta)
    source = ReviewStore(beta)
    set_text(source, BASE + '這裡是新稿新增的後文。', 'new-transcript-candidate')
    existing = target.document('a', '講道 test')['script']
    baseline = deepcopy(existing);baseline[0]['text'] = BASE
    incoming = source.document('a', '講道 test')['script']
    paths = {}
    for name, value in [('baseline', baseline), ('existing', existing), ('incoming', incoming)]:
        paths[name] = tmp_path / ('replace-' + name + '.json');atomic(paths[name], value)
    direction = tmp_path / 'replacement-direction.txt';direction.write_text('保留舊校正，採用新稿，保留歷史和認領。')
    run = tmp_path / 'replacement-merge'
    assert merge.prepare(run, item='講道 test', instruction_file=direction, **paths)['merge_complete']
    merge.apply(source, run, actor='a')
    backup = tmp_path / 'replacement-backup'
    promotion.backup(beta, root, '講道 test', backup)
    return target, source, root, beta, run, backup


def test_modern_editor_replacement_keeps_history_owner_and_publication(setup, tmp_path):
    from backend.pipeline import transcript_review_promotion as promotion
    target, source, root, beta, run, backup = replacement_case(setup, tmp_path)
    before = target.document('a', '講道 test')
    history = {p.name: p.read_bytes() for p in (target.folder('講道 test') / 'revisions').glob('*.json')}
    publication = root / 'script_published/講道 test.json'
    publication.write_text('{"script": [], "metadata": {}}')
    # Publication is immutable during replacement; recapture exact backup once.
    import shutil
    shutil.rmtree(backup)
    promotion.backup(beta, root, '講道 test', backup)
    raw = publication.read_bytes()
    result = promotion.replace_review(beta, root, '講道 test', backup, run, actor='a')
    after = target.document('a', '講道 test')
    visible = target.history('a', '講道 test', grouped=True)
    assert {r["revision"] for period in visible["periods"] for r in period["revisions"]} == {
        *map(lambda name: int(name.removesuffix(".json")), history), result["revision"],
    }
    assert target.revision('a', '講道 test', before['context']['revision'])['script'] == before['script']
    assert after['context']['revision'] == before['context']['revision'] + 1
    assert after['script'] == source.document('a', '講道 test')['script']
    assert after['header'] == before['header'] and after['epoch'] == before['epoch']
    assert '屬於' in after['script'][0]['text']
    assert publication.read_bytes() == raw
    for name, value in history.items():
        assert (target.folder('講道 test') / 'revisions' / name).read_bytes() == value
    assert promotion.replace_review(beta, root, '講道 test', backup, run, actor='a') == result


def test_replacement_rejects_new_production_edits(setup, tmp_path):
    from backend.pipeline import transcript_review_promotion as promotion
    target, source, root, beta, run, backup = replacement_case(setup, tmp_path)
    d = target.document('a', '講道 test')
    target.mutate('a', '講道 test', {'version': d['version'], 'epoch': d['epoch'],
        'operation_id': 'later-edit', 'operations': [{'kind': 'text', 'id': d['script'][0]['index'], 'text': '其他人更新'}]})
    with pytest.raises(ValueError, match='current production draft'):
        promotion.replace_review(beta, root, '講道 test', backup, run, actor='a')
    assert target.document('a', '講道 test')['script'][0]['text'] == '其他人更新'


def test_no_change_merge_does_not_create_editor_revision(setup, tmp_path):
    store, _, _ = setup
    adopted(setup)
    incoming = store.document('a', '講道 test')['script']
    paths = {}
    for name in ('baseline', 'existing', 'incoming'):
        paths[name] = tmp_path / ('noop-' + name + '.json');atomic(paths[name], incoming)
    direction = tmp_path / 'noop-direction.txt';direction.write_text('合併新稿。')
    run = tmp_path / 'noop-merge'
    merge.prepare(run, item='講道 test', instruction_file=direction, **paths)
    before = store.document('a', '講道 test')
    with patch.object(store, 'mutate', side_effect=AssertionError('no-op editor write')):
        assert merge.apply(store, run, actor='a')['operation_id'] is None
    assert store.document('a', '講道 test') == before


def test_replacement_recovers_a_committed_reply_loss(setup, tmp_path):
    from backend.pipeline import transcript_review_promotion as promotion
    target, _, root, beta, run, backup = replacement_case(setup, tmp_path)
    original = target.__class__.commit
    def lose_reply(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if self.root == target.root:
            raise RuntimeError('lost response after commit')
        return result
    with patch.object(target.__class__, 'commit', lose_reply):
        with pytest.raises(RuntimeError, match='lost response'):
            promotion.replace_review(beta, root, '講道 test', backup, run, actor='a')
    revision = target.document('a', '講道 test')['context']['revision']
    assert promotion.replace_review(beta, root, '講道 test', backup, run, actor='a')['revision'] == revision
    assert target.document('a', '講道 test')['context']['revision'] == revision


def test_replacement_rejects_media_mismatch_before_revision(setup, tmp_path):
    import shutil
    from backend.pipeline import transcript_review_promotion as promotion
    from backend.api.sc_api.transcript_review import read
    target, source, root, beta, run, backup = replacement_case(setup, tmp_path)
    path = source.folder('講道 test') / 'context.json'
    context = read(path);context['source_video_sha256'] = '0' * 64;atomic(path, context)
    shutil.rmtree(backup)
    promotion.backup(beta, root, '講道 test', backup)
    before = target.document('a', '講道 test')
    with pytest.raises(ValueError, match='media identity differs'):
        promotion.replace_review(beta, root, '講道 test', backup, run, actor='a')
    assert target.document('a', '講道 test') == before


def test_input_snapshots_allow_replay_after_the_incoming_editor_file_changes(setup, tmp_path):
    store, root, _ = setup
    adopted(setup)
    d = store.document('a', '講道 test')
    store.mutate('a', '講道 test', {'version': d['version'], 'epoch': d['epoch'],
        'operation_id': 'snapshot-fixture', 'operations': [{'kind': 'text', 'id': d['script'][0]['index'], 'text': BASE}]})
    incoming = store.document('a', '講道 test')['script']
    old = deepcopy(incoming);old[0]['text'] = '~~' + old[0]['text'] + '~~'
    baseline = tmp_path / 'snapshot-base.json';existing = tmp_path / 'snapshot-existing.json'
    atomic(baseline, incoming);atomic(existing, old)
    direction = tmp_path / 'snapshot-direction.txt';direction.write_text('保留校對。')
    run = tmp_path / 'snapshot-merge'
    merge.prepare(run, item='講道 test', baseline=baseline, existing=existing,
                  incoming=root / 'script_review/講道 test.json', instruction_file=direction)
    result = merge.apply(store, run, actor='a')
    assert merge.apply(store, run, actor='a') == result


def test_ordered_anchors_allow_local_context_rewrite_but_never_drop_negation():
    left = '這裡先完整說明獨特的前提和經文脈絡作為開始，'
    right = '，然後還有完整且獨特的後續論證和結論需要保留。'
    text = left + '舊上下文甲始於舊上下文乙' + right
    existing = text.replace('甲始', '甲屬')
    incoming = text.replace('舊上下文', '新的敘述')
    result = merge.merge(doc([text]), doc([existing]), doc([incoming]))
    assert result['merge_complete']
    assert result['changes'][0]['reason'] == 'ordered_unique_anchors_and_exact_edit_boundaries'
    assert '甲屬於新的敘述乙' in result['script'][0]['text']
    negated = incoming.replace('甲始', '甲不屬')
    result = merge.merge(doc([text]), doc([existing]), doc([negated]))
    assert not result['merge_complete'] and result['script'] == doc([negated])


# The database is disposable; publication and review decisions below are fixture
# operations, never a call against real production or a live content model.
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres


def test_upgrade_editor_publication_source_and_claim_locator_handoff(setup, tmp_path, postgres_store):
    from backend.pipeline import transcript_review_promotion as promotion
    from backend.pipeline import transcript_claim_migration as source_migration
    from backend.pipeline import transcript_locator_migration as locators
    from backend.pipeline import transcript_evidence_apply as evidence
    from backend.tests.test_transcript_review import confirmed, op
    target, _, root, beta, merge_run, backup = replacement_case(setup, tmp_path)
    rows, context = target.load('講道 test')
    oldfile = tmp_path / 'registered-old.json'
    atomic(oldfile, source_migration.beta_source(rows, context, item='講道 test'))
    snapshot = evidence.snap(oldfile);body = snapshot['body_sha256']
    quote = '屬於來世的生命'
    first_clock = context['paragraph_bindings'][rows[0]['index']]
    package = {'source_documents': [{'source_id': 'S', 'source_type': 'sermon_transcript',
        'transcript_id': '講道 test', 'source_path': str(oldfile),
        'source_file_sha256': source_migration.sha(oldfile.read_bytes()),
        'source_sha256': body, 'source_body_sha256': body, 'locator_space': 'spoken_body_v1'}],
        'source_fragments': [{'fragment_id': 'F', 'source_id': 'S', 'source_sha256': body,
            'paragraph_key': 'S0001', 'verbatim_excerpt': quote,
            'media_time': first_clock['start_ms'] / 1000,
            'media_end_time': first_clock['end_ms'] / 1000}],
        'evidence_steps': [{'evidence_step_id': 'E', 'statement': '既有論據',
            'source_fragment_ids': ['F'], 'produced_claim_ids': ['C']}],
        'claims': [{'claim_id': 'C', 'claim_type': 'exegesis', 'statement': '既有主張不由定位搬遷重寫',
            'evidence_step_ids': ['E'], 'occurrences': [{'source_id': 'S', 'anchors': [
                {'evidence_id': 'E', 'paragraph_key': 'S0001', 'media_time': first_clock['start_ms'] / 1000,
                 'proposed_highlight': {'text': quote, 'status': 'proposed'}}]}]}]}
    postgres_store.apply_plan(postgres_store.plan_package(package))
    promotion.replace_review(beta, root, '講道 test', backup, merge_run, actor='a')
    d = confirmed(target)
    target.publish('a', '講道 test', op(d))
    source_run = tmp_path / 'source-migration'
    source_migration.prepare(postgres_store, beta_root=root, item='講道 test', source_id='S',
                             root=source_run, instruction='升級來源，只移植定位。')
    source_migration.register(postgres_store, source_run)
    published = root / 'script_published/講道 test.json'
    direction = tmp_path / 'locator-direction.txt';direction.write_text('移植引文及定位，不改論點，不呼叫模型。')
    locator_run = tmp_path / 'locators'
    locators.prepare(postgres_store, locator_run, source_id='S', old_paths=[oldfile],
                     published=published, instruction_file=direction)
    receipt = locators.run(postgres_store, locator_run)
    assert receipt['status'] == 'locator_migration_verified'
    assert locators.run(postgres_store, locator_run) == receipt
    after = postgres_store.get_record_state('claims', 'C')['payload']
    assert after['statement'] == package['claims'][0]['statement']
    registered = postgres_store.get_record_state('source_documents', 'S')['payload']
    assert after['occurrences'][0]['anchors'][0]['source_sha256'] == registered['source_body_sha256']


def test_backup_deduplicates_identical_media_without_linking_live_files(setup, tmp_path):
    target, source, root, beta, _, backup = replacement_case(setup, tmp_path)
    relative = 'transcript_review/講道 test/source.mp4'
    a, b = backup / 'beta' / relative, backup / 'production' / relative
    assert a.stat().st_ino == b.stat().st_ino
    assert a.stat().st_ino != (root / relative).stat().st_ino
    assert a.stat().st_ino != (beta / relative).stat().st_ino
