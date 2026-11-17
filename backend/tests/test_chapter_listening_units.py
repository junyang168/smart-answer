import copy
import json
from pathlib import Path

import pytest

from backend.pipeline.chapter_listening_units import project


def example():
    case = json.loads((Path(__file__).parent / 'fixtures/chapter_selection/unit1_sub_units_golden.json').read_text())
    source = case['source_id']
    groups = []
    order = []
    for sub in case['sub_units']:
        gid = sub['group_id']
        key = source + ':' + gid
        ranges = [{'paragraph_key': k, 'start': p['start_time'], 'end': p['end_time']}
                  for k, p in case['paragraphs'].items() if case['owners'][k] == gid]
        groups.append({'key': key, 'source_id': source, 'source_ranges': ranges})
        order.extend({'group_key': key, 'source_id': source, **r} for r in ranges)
    order.sort(key=lambda r: r['start'])
    unit = {'unit_number': 1, 'passage': '太5:1–12', 'title': '八福', 'groups': groups,
            'playback_order': order, 'sub_units': [{**s, 'group_keys': [source + ':' + s['group_id']]}
                                                for s in case['sub_units']]}
    lines = [{'start': r['start'], 'end': r['end'], 'text': case['paragraphs'][r['paragraph_key']]['text']}
             for r in order]
    slides = [{'at': r['start'], 'seconds': r['end'] - r['start'], 'title': r['paragraph_key'],
               'points': [], 'scriptures': [], 'verses': ['mat-5-1-12']} for r in order]
    recordings = {source: {'lines': lines, 'slides': slides, 'retime': lambda t: t + 1,
                           'media': {'source_id': source, 'media_url': '/audio.mp3'}}}
    return {'units': {'mat-5-1-12': unit}}, recordings, case


def test_real_golden_playback_resumes_same_sub_unit_without_repeating_audio():
    plan, recordings, case = example()
    before = copy.deepcopy(plan)
    sections = project(plan, recordings)['sections']
    assert [s['sub_unit_id'] for s in sections] == ['1.1', '1.2', '1.3', '1.2']
    assert [s['continued'] for s in sections] == [False, False, False, True]
    assert sections[-1]['label'].endswith('（續）')
    assert [slide['title'] for s in sections for row in s['sermons'] for slide in row['slides']] == case['expected_kept_paragraphs']
    assert sections[0]['sermons'][0]['stretches'][0]['start'] == 72
    assert sections[0]['sermons'][0]['transcript'][0]['at'] == 72
    assert sections[0]['sermons'][0]['slides'][0]['at'] == 72
    assert plan == before


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'reordered', 'double_assignment'])
def test_invalid_unit_plan_is_rejected(fault):
    plan, recordings, _ = example()
    unit = plan['units']['mat-5-1-12']
    if fault == 'missing': unit['playback_order'].pop()
    if fault == 'duplicate': unit['playback_order'].append(unit['playback_order'][-1])
    if fault == 'reordered': unit['playback_order'].reverse()
    if fault == 'double_assignment': unit['sub_units'].append(unit['sub_units'][0])
    with pytest.raises(ValueError): project(plan, recordings)


def test_missing_slides_cannot_silently_drop_a_sub_unit():
    plan, recordings, _ = example()
    for recording in recordings.values(): recording['slides'] = []
    with pytest.raises(ValueError, match='No existing slides'): project(plan, recordings)


def test_preview_api_reports_changed_material_instead_of_silently_falling_back(monkeypatch):
    from fastapi import HTTPException
    from backend.api.original_audio import index
    from backend.pipeline import chapter_listening_units
    def changed(*args):
        raise ValueError('source changed')
    monkeypatch.setattr(chapter_listening_units, 'build', changed)
    with pytest.raises(HTTPException) as error:
        index('mat-5-1-48', source='紐約靈命進深會', units=True)
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        index('mat-6-1-34', units=True)
    assert error.value.status_code == 404
