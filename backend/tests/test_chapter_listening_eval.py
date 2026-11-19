import json
import hashlib

import pytest

from backend.pipeline import chapter_listening_eval as ev
from backend.tests.test_chapter_listening_units import example


def browser_evidence(signature='bound'):
    viewports = []
    for width, height in [(390, 844), (375, 667), (320, 568)]:
        compact = width == 320
        labels = ['‹ 1.3', '↺ 15', '播放', '15 ↻'] + ([] if compact else ['速度']) + ['3.1 ›']
        viewports.append({'width': width, 'height': height, 'overflow': False, 'slideHeight': 202,
                          'menuVisible': not compact, 'speedVisible': not compact,
                          'controls': [{'text': label, 'x': i * 48, 'y': 400, 'width': 44, 'height': 44}
                                       for i, label in enumerate(labels)]})
    return {'input_sha256': signature, 'viewports': viewports,
            'resume': {'before_reload': 200.4, 'after_reload': 200.1, 'paused_after_reload': True,
                       'section_before': '1.2-continued', 'section_after': '1.2-continued'},
            'compact_interactions': {'previous_next': True, 'slide_scroll': True, 'transcript_scroll': True}}


def test_browser_evidence_must_bind_current_content_and_code():
    evidence = browser_evidence()
    assert ev.browser_checks(evidence, {'input_sha256': 'bound'})['status'] == 'passed'
    assert ev.browser_checks(None, {'input_sha256': 'bound'})['status'] == 'pending'
    assert ev.browser_checks(evidence, {'input_sha256': 'changed'})['status'] == 'stale'


@pytest.mark.parametrize('fault', ['wrap', 'small_target', 'missing_viewport', 'missing_buttons', 'bad_resume', 'autoplay', 'missing_scroll'])
def test_summary_pass_cannot_hide_failed_browser_checks(fault):
    evidence = browser_evidence()
    evidence['status'] = 'passed'  # The aggregator must derive its result from measurements.
    if fault == 'wrap': evidence['viewports'][0]['controls'][-1]['y'] += 50
    if fault == 'small_target': evidence['viewports'][0]['controls'][0]['width'] = 20
    if fault == 'missing_viewport': evidence['viewports'].pop()
    if fault == 'missing_buttons': evidence['viewports'][0]['controls'] = []
    if fault == 'bad_resume': evidence['resume']['after_reload'] = float('nan')
    if fault == 'autoplay': evidence['resume']['paused_after_reload'] = False
    if fault == 'missing_scroll': evidence['compact_interactions']['slide_scroll'] = False
    assert ev.browser_checks(evidence, {'input_sha256': 'bound'})['status'] == 'failed'


def setup_report(tmp_path, monkeypatch, problems):
    plan, recordings, golden = example()
    for unit in plan['units'].values():
        for group in unit['groups']:
            for fragment in group['source_ranges']:
                fragment['text'] = golden['paragraphs'][fragment['paragraph_key']]['text']
    plan['inputs'] = [{'lecture': 'test', 'source_id': golden['source_id']}]
    frozen = tmp_path / 'selection-input.json'
    answer = tmp_path / 'selection-answer.json'
    frozen.write_text(json.dumps({'system': ev.chapter_argument_selection.RULES,
                                 'request': {'source_id': golden['source_id']}}))
    answer.write_text('{}')
    plan['inputs'][0].update(saved_input=str(frozen), saved_answer=str(answer),
                             answer_sha256=hashlib.sha256(answer.read_bytes()).hexdigest())
    folder = tmp_path / 'chapter_exposition/mat-5-1-48'
    folder.mkdir(parents=True)
    (folder / 'listening-units.json').write_text(json.dumps(plan))
    (folder / 'test.json').write_text(json.dumps({'lecture': 'test', 'passage': 'mat-5-1-48', 'pieces': [], 'decisions': []}))
    index = ev.units.project(plan, recordings)
    monkeypatch.setattr(ev.units, 'build', lambda *args: index)
    monkeypatch.setattr(ev.chapter_slides, 'load_record', lambda *args: {'raw_slides': [], 'slides': []})
    monkeypatch.setattr(ev.chapter_slides, 'checked', lambda *args: (problems, [], []))
    monkeypatch.setattr(ev.chapter_slide_eval, 'evaluate', lambda *args, **kwargs: {'problems': []})
    evidence = browser_evidence(ev.binding(index)['input_sha256'])
    return index, evidence


def test_existing_slide_eval_failure_blocks_unified_pass(tmp_path, monkeypatch):
    _, evidence = setup_report(tmp_path, monkeypatch, ['Owner timing case failed'])
    report = ev.evaluate(tmp_path, 'mat-5-1-48', evidence=evidence)
    assert report['checks']['ui']['status'] == 'passed'
    assert report['checks']['slides']['status'] == 'failed'
    assert report['status'] == 'needs_attention'


def test_prompt_change_requires_fresh_golden_eval_even_if_ui_and_old_answers_pass(tmp_path, monkeypatch):
    _, evidence = setup_report(tmp_path, monkeypatch, [])
    monkeypatch.setattr(ev.chapter_argument_selection, 'RULES', 'changed prompt')
    report = ev.evaluate(tmp_path, 'mat-5-1-48', evidence=evidence)
    assert report['checks']['selection']['status'] == 'failed'
    assert report['status'] == 'needs_attention'
    assert report['new_model_calls'] == 0
    assert report['publication_decision'] is False


def test_golden_order_and_missing_evidence_cannot_be_reported_as_complete(tmp_path, monkeypatch):
    index, evidence = setup_report(tmp_path, monkeypatch, [])
    assert ev.evaluate(tmp_path, 'mat-5-1-48', evidence=evidence)['status'] == 'passed_for_browser_preview'
    assert ev.evaluate(tmp_path, 'mat-5-1-48')['status'] == 'needs_attention'
    index['sections'][1], index['sections'][2] = index['sections'][2], index['sections'][1]
    report = ev.evaluate(tmp_path, 'mat-5-1-48', evidence=evidence)
    assert report['checks']['selection']['status'] == 'failed'
    assert report['checks']['ui']['status'] == 'stale'
    assert report['status'] == 'needs_attention'


def test_rendered_point_time_must_match_owner_source_not_just_pass_saved_slide_checks(tmp_path, monkeypatch):
    index, evidence = setup_report(tmp_path, monkeypatch, [])
    row = index['sections'][0]['sermons'][0]
    quote = row['transcript'][0]
    row['slides'][0]['points'] = [{'at': quote['at'] + 10, 'text': 'Owner point'}]
    index['program_timing_corrections'] = [{'source_id': row['source_id'],
        'source_text': quote['text'], 'text': 'Owner point', 'authority': 'owner_case'}]
    evidence['input_sha256'] = ev.binding(index)['input_sha256']
    report = ev.evaluate(tmp_path, 'mat-5-1-48', evidence=evidence)
    assert report['checks']['ui']['status'] == 'passed'
    assert report['checks']['playback']['status'] == 'failed'
    assert report['status'] == 'needs_attention'


def test_isolated_preview_cannot_prove_normal_entry():
    from backend.tests.test_chapter_listening_catalog import example
    from backend.pipeline.chapter_listening_catalog import compose
    old,selected=example()
    catalog=compose(old,selected)
    assert ev.catalog_checks(catalog,selected,{})['status']=='failed'
    evidence={'normal_entry':{k:True for k in ('overview_subunits','start_link','unit_link','subunit_link','historical_link')}}
    assert ev.catalog_checks(catalog,selected,evidence)['status']=='passed'
    catalog['sections'].pop(0)
    assert ev.catalog_checks(catalog,selected,evidence)['status']=='failed'


def test_new_argument_version_and_diagrams_participate_in_unified_eval(tmp_path, monkeypatch):
    index, evidence = setup_report(tmp_path, monkeypatch, [])
    row = index['sections'][0]['sermons'][0]
    slide = row['slides'][0]
    sha = 'a' * 64
    slide['diagram'] = {'at': row['transcript'][0]['at'], 'sha256': sha}
    evidence['input_sha256'] = ev.binding(index)['input_sha256']
    expected_versions = {row['source_id']: 'new'}
    report = ev.evaluate(tmp_path, 'mat-5-1-48', evidence=evidence, expected_versions=expected_versions)
    assert report['checks']['selection']['problems'] == ['Stale argument version: test']
    assert report['checks']['ui']['status'] == 'failed'
    assert report['checks']['playback']['status'] == 'passed'
    for viewport in evidence['viewports']:
        viewport['diagrams'] = [{'sha256': sha, 'fits': True, 'dark': True, 'no_zoom': True,
                                 'hidden_before': True, 'min_font_px': 14}]
    assert ev.evaluate(tmp_path, 'mat-5-1-48', evidence=evidence)['checks']['ui']['status'] == 'passed'
    evidence['viewports'][-1]['diagrams'][0]['min_font_px'] = 11
    assert ev.evaluate(tmp_path, 'mat-5-1-48', evidence=evidence)['checks']['ui']['status'] == 'failed'
    slide['diagram']['at'] += 0.5
    assert ev.evaluate(tmp_path, 'mat-5-1-48')['checks']['playback']['status'] == 'failed'


def test_existing_audio_boundaries_with_new_slide_clock_use_player_coverage():
    from pathlib import Path
    from backend.pipeline.chapter_listening_units import align_playback_intervals

    fixture = Path(__file__).parent / 'fixtures/chapter_slides/mixed_playback_clocks.json'
    index = json.loads(fixture.read_text())
    original = json.loads(fixture.read_text())
    assert len(ev.playback_alignment_problems(index)) == 3
    for section in index['sections']:
        row = section['sermons'][0]
        aligned = align_playback_intervals(row)
        assert aligned['stretches'] == row['stretches']
        assert [s['at'] for s in aligned['slides']] == [s['at'] for s in row['slides']]
        assert aligned['transcript'][0]['text'] == row['transcript'][0]['text']
        section['sermons'][0] = aligned
    assert ev.playback_alignment_problems(index) == []
    assert index['sections'][0]['sermons'][0]['transcript'][0]['at'] == 37.25
    assert original['sections'][0]['sermons'][0]['transcript'][0]['at'] == 37.24
