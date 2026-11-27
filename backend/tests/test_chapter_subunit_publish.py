import json

import pytest

from backend.pipeline import (
    chapter_exposition, chapter_listening_units, chapter_slide_eval, chapter_slides,
    chapter_subunit_publish, chapter_subunit_slides,
)


@pytest.fixture
def prepared_subunit(tmp_path, monkeypatch):
    passage = 'mat-5-1-48'
    sub_id = '3.2'
    lecture = 'Lecture'
    data = tmp_path / 'data'
    transcript = data / 'script_published' / f'{lecture}.json'
    transcript.parent.mkdir(parents=True)
    transcript.write_text('{"script": []}')
    source_sha = chapter_exposition.file_sha256(transcript)
    source_ranges = [
        {'paragraph_key': 'P1', 'start': 0, 'end': 10, 'text': '第一段。'},
        {'paragraph_key': 'P2', 'start': 10, 'end': 20, 'text': '第二段。'},
    ]
    groups = [{'key': f'g{i}', 'source_id': 'src', 'source_ranges': [r]}
              for i, r in enumerate(source_ranges, 1)]
    parent = 'mat-5-17-20'
    plan = {
        'inputs': [{'lecture': lecture, 'source_id': 'src', 'source_sha256': source_sha, 'changeset': 'reviewed'}],
        'units': {parent: {
            'unit_number': 3, 'passage': parent, 'title': '律法',
            'groups': groups,
            'sub_units': [{'id': sid, 'title': sid, 'group_keys': [g['key']]}
                          for sid, g in zip((sub_id, '3.3'), groups, strict=True)],
            'playback_order': [{**r, 'source_id': 'src', 'group_key': group['key']}
                               for r, group in zip(source_ranges, groups, strict=True)],
        }},
        'chapter_summary': {'title': '已驗收摘要'},
    }
    plan_path = chapter_subunit_slides.plan_path(data, passage)
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(json.dumps(plan))

    media = dict(source_id='src', transcript_id=lecture, title=lecture, media_kind='audio',
                 media_url='/web/video/lecture.mp3', media_duration=30)

    class Sermons:
        def __init__(self, data_root):
            pass

        def load(self, source, document):
            return media

    monkeypatch.setattr(chapter_listening_units.audio, 'Sermons', Sermons)
    monkeypatch.setattr(chapter_listening_units.audio, 'retimer', lambda *a: lambda t: t + 1)
    pieces = [{**r, 'id': r['paragraph_key']} for r in source_ranges]
    monkeypatch.setattr(chapter_exposition, 'load_record', lambda *a: {
        'lecture': lecture, 'pieces': pieces, 'decisions': [{'id': r['id'], 'keep': True} for r in pieces],
    })
    monkeypatch.setattr(chapter_listening_units, 'owner_timing', lambda record, selection: (record, []))
    monkeypatch.setattr(chapter_slides, 'load_record', lambda *a: {'kind': 'old', 'slides': []})
    old_slides = [{'at': r['start'], 'seconds': 10, 'title': r['text'], 'points': [], 'scriptures': [], 'verses': []}
                  for r in source_ranges]
    replacement = [{**old_slides[0], 'title': '已驗收的新投影片'}]
    monkeypatch.setattr(chapter_slides, 'placed',
                        lambda selection, record, **k: (None, old_slides if record.get('kind') == 'old' else replacement))

    _, selection = chapter_subunit_slides.subunit_selection(plan, passage, sub_id)
    files = {k: tmp_path / f'{k}.json' for k in ('slides', 'selection', 'receipt')}
    files['selection'].write_text(json.dumps(selection))
    record = {'lecture': lecture, 'model': 'writer', 'semantic_eval': chapter_slide_eval.SEMANTIC_SCHEMA_VERSION,
              'claim_context': {'instance': {'change_set_id': 'reviewed'}}, 'slides': [{'first': 'S0001'}],
              'decisions_sha256': chapter_exposition.file_sha256(files['selection'])}
    files['slides'].write_text(json.dumps(record))
    receipt = chapter_slide_eval.with_run({
        'schema_version': chapter_slide_eval.SEMANTIC_SCHEMA_VERSION,
        'slides_sha256': chapter_slide_eval.semantic_slide_sha256(files['slides']),
        'selection_sha256': record['decisions_sha256'],
        'checks': [{'slide': 'S0001', 'criterion': key, 'passed': True, 'evidence': '驗收'}
                   for key in chapter_slide_eval.SEMANTIC_CRITERIA],
    })
    files['receipt'].write_text(json.dumps(receipt))
    return data, passage, sub_id, plan_path, files


def test_publish_switches_only_one_subunit_ppt_and_keeps_lecture_playback(prepared_subunit):
    data, passage, sub_id, plan, files = prepared_subunit
    original_plan = plan.read_bytes()
    original = chapter_listening_units.build(data, passage)
    pointer = chapter_listening_units.published_subunit_path(plan.parent, sub_id)

    preview = chapter_subunit_publish.publish(data, passage, sub_id, **files)
    assert preview['status'] == 'ready' and not pointer.exists()
    assert not (plan.parent / 'unit-supplements').exists()
    result = chapter_subunit_publish.publish(data, passage, sub_id, **files, apply=True)
    assert result['status'] == 'published' and result['bundle'] == preview['bundle']
    assert plan.read_bytes() == original_plan
    assert json.loads(pointer.read_text())['bundle'] == result['bundle']
    published = chapter_listening_units.build(data, passage)
    assert published['sections'][1] == original['sections'][1]
    current = published['sections'][0]['sermons'][0]
    prior = original['sections'][0]['sermons'][0]
    assert current['slides'][0]['title'] == '已驗收的新投影片'
    assert {**current, 'slides': prior['slides']} == prior
    assert published['resume_version'] == original['resume_version']
    assert chapter_subunit_publish.publish(data, passage, sub_id, **files, apply=True) == result

    changed = json.loads(original_plan)
    changed['inputs'][0]['changeset'] = 'different-approved-argument-layer'
    plan.write_text(json.dumps(changed))
    assert chapter_listening_units.build(data, passage) == original


def test_unbound_receipt_cannot_replace_a_published_subunit(prepared_subunit):
    data, passage, sub_id, plan, files = prepared_subunit
    chapter_subunit_publish.publish(data, passage, sub_id, **files, apply=True)
    pointer = chapter_listening_units.published_subunit_path(plan.parent, sub_id)
    before = pointer.read_bytes()
    record = json.loads(files['slides'].read_text())
    files['slides'].write_text(json.dumps({**record, 'changed': True}))
    with pytest.raises(ValueError, match='different slide version'):
        chapter_subunit_publish.publish(data, passage, sub_id, **files, apply=True)
    assert pointer.read_bytes() == before


def test_production_apply_requires_running_release(prepared_subunit, monkeypatch, tmp_path):
    data, passage, sub_id, plan, files = prepared_subunit
    monkeypatch.setattr(chapter_subunit_publish, '_PRODUCTION_DATA', data)
    monkeypatch.setenv('SMART_ANSWER_DEPLOY_ROOT', str(tmp_path))
    (tmp_path / 'active-release').write_text(str(tmp_path / 'old-release'))
    assert chapter_subunit_publish.publish(data, passage, sub_id, **files)['status'] == 'ready'
    with pytest.raises(ValueError, match='active deployed release'):
        chapter_subunit_publish.publish(data, passage, sub_id, **files, apply=True)
    assert not chapter_listening_units.published_subunit_path(plan.parent, sub_id).exists()
