"""One read-only report for Unit selection, existing Slide Eval, playback and UI.

No generation, review retries, publication or rewriting of previous verdicts.
Browser evidence must bind the exact displayed input and UI implementation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from datetime import datetime, timezone

from backend.pipeline import chapter_listening_units as units
from backend.pipeline import chapter_slides, chapter_slide_eval
from backend.pipeline import chapter_exposition, chapter_argument_selection
from backend.pipeline.chapter_material import CHAPTERS

ROOT = Path(__file__).resolve().parents[2]
UI_FILES = (
    'web/src/app/resources/wang-repository/audio/ChapterListener.tsx',
    'web/src/app/resources/wang-repository/audio/ChapterSlide.tsx',
    'web/src/app/resources/wang-repository/audio/listeningProgress.ts',
    'web/src/app/resources/wang-repository/audio/listeningUnits.ts',
    'backend/api/original_audio.py',
    'backend/pipeline/chapter_listening_catalog.py',
    'web/src/app/resources/wang-repository/audio/OriginalAudio.tsx',
    'web/src/app/resources/wang-repository/books/[book]/[chapter]/listen/page.tsx',
    'web/src/app/globals.css',
    'web/src/app/layout.tsx',
)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def binding(index):
    files = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in UI_FILES}
    return {'input_sha256': digest({'index': index, 'ui_files': files}),
            'index_sha256': digest(index), 'ui_files': files}


def browser_checks(evidence, expected_binding):
    if not evidence:
        return {'status': 'pending', 'problems': ['No bound browser evidence supplied']}
    if evidence.get('input_sha256') != expected_binding['input_sha256']:
        return {'status': 'stale', 'problems': ['Displayed input or UI implementation changed']}
    problems = []
    viewports = evidence.get('viewports', [])
    required = {(390, 844), (375, 667), (320, 568)}
    if {(v.get('width'), v.get('height')) for v in viewports} != required or len(viewports) != 3:
        problems.append('Both mobile layouts and smallest viewport must be measured')
    for view in viewports:
        controls = view.get('controls', [])
        labels = [c.get('text', '') for c in controls]
        if (not any(label in ('播放', '暫停') for label in labels)
                or not all(any(symbol in label for label in labels) for symbol in ('‹', '›', '↺', '↻'))):
            problems.append('Required playback buttons are missing from viewport evidence')
        if (not controls or view.get('overflow') is not False or view.get('slideHeight', 0) <= 0
                or any(c['width'] < 44 or c['height'] < 44 or c['x'] < 0 or c['y'] < 0
                       or c['x'] + c['width'] > view['width'] or c['y'] + c['height'] > view['height']
                       for c in controls)
                or max((c['y'] + c['height'] / 2 for c in controls), default=0)
                   - min((c['y'] + c['height'] / 2 for c in controls), default=0) >= 1):
            problems.append(f"Mobile controls overflow, wrap or lack usable targets: {view.get('width')}")
        compact = view.get('width', 999) < 390 and view.get('height', 999) < 600
        if view.get('menuVisible') != (not compact) or view.get('speedVisible') != (not compact):
            problems.append('Wrong regular/compact mobile layout')
    resume = evidence.get('resume', {})
    before, after = resume.get('before_reload'), resume.get('after_reload')
    if (not isinstance(before, (int, float)) or not isinstance(after, (int, float))
            or not math.isfinite(before) or not math.isfinite(after)
            or abs(before - after) >= 1 or resume.get('paused_after_reload') is not True
            or not resume.get('section_before') or resume.get('section_before') != resume.get('section_after')):
        problems.append('Exact-position paused resume was not demonstrated')
    interactions = evidence.get('compact_interactions', {})
    if not all(interactions.get(key) is True for key in ('previous_next', 'slide_scroll', 'transcript_scroll')):
        problems.append('Compact navigation and independent scrolling were not demonstrated')
    return {'status': 'failed' if problems else 'passed', 'problems': problems,
            'evidence': evidence, 'physical_device': 'not_verified'}


def catalog_checks(catalog, selected, evidence):
    problems = []
    if not catalog.get('unit_navigation'):
        problems.append('Normal chapter entry does not use Unit navigation')
    selected_rows = {s['passage']: s for s in selected['sections']}
    actual = {s['passage']: s for s in catalog['sections']}
    for key, expected in selected_rows.items():
        if key not in actual or actual[key]['sermons'] != expected['sermons']:
            problems.append(f'Normal catalog omitted or changed an approved playback run: {key}')
    for section in catalog['sections']:
        if section['passage'] in selected_rows: continue
        for row in section['sermons']:
            for own in row['stretches']:
                if own.get('left_out'): continue
                for selected_section in selected_rows.values():
                    for other in selected_section['sermons']:
                        if row.get('transcript_id') != other.get('transcript_id'): continue
                        if any(not span.get('left_out') and own['start'] < span['end'] and span['start'] < own['end'] for span in other['stretches']):
                            problems.append('Normal catalog repeats audio already assigned to an approved Unit')
    navigation = (evidence or {}).get('normal_entry', {})
    for required in ('overview_subunits', 'start_link', 'unit_link', 'subunit_link', 'historical_link'):
        if navigation.get(required) is not True:
            problems.append(f'Normal entry browser check missing: {required}')
    return {'status': 'failed' if problems else 'passed', 'problems': problems}


def evaluate(data_root, passage, *, evidence=None, catalog=None):
    try:
        index = units.build(data_root, passage)
    except (ValueError, OSError) as error:
        return {'schema_version': 'chapter-listening-eval.v1', 'passage': passage,
                'status': 'needs_attention', 'checks': {
                    'selection': {'status': 'blocked'},
                    'slides': {'status': 'failed', 'problems': [str(error)]},
                    'playback': {'status': 'blocked'}, 'ui': {'status': 'blocked'}},
                'new_model_calls': 0, 'publication_decision': False}
    current = binding(catalog if catalog is not None else index)
    plan = json.loads((data_root / 'chapter_exposition' / passage / 'listening-units.json').read_text())
    golden = json.loads((ROOT / 'backend/tests/fixtures/chapter_selection/unit1_sub_units_golden.json').read_text())
    unit1 = plan['units'][golden['target_section']]
    selection_problems = []
    for entry in plan['inputs']:
        try:
            frozen = json.loads(Path(entry['saved_input']).read_text())
            answer_sha = hashlib.sha256(Path(entry['saved_answer']).read_bytes()).hexdigest()
            if (frozen['system'] != chapter_argument_selection.RULES
                    or frozen['request']['source_id'] != entry['source_id']
                    or answer_sha != entry['answer_sha256']):
                selection_problems.append('Selection prompt/input changed or saved answer differs; fresh golden Eval required')
        except (OSError, KeyError, ValueError):
            selection_problems.append('Bound selection input/answer is unavailable')
    if ([r['paragraph_key'] for r in unit1['playback_order']] != golden['expected_kept_paragraphs']
            or [s['sub_unit_id'] for s in index['sections'] if s['unit_id'] == '1']
            != [r['sub_unit_id'] for r in golden['expected_playback_runs']]
            or {s['id']: [g.rsplit(':', 1)[-1] for g in s['group_keys']] for s in unit1['sub_units']}
            != {s['id']: [s['group_id']] for s in golden['sub_units']}):
        selection_problems.append('Owner golden Unit/Sub-unit identity, coverage or playback order changed')
    normalize = lambda text: ''.join(text.split())
    for unit in plan['units'].values():
        for sub in unit['sub_units']:
            groups = [g for g in unit['groups'] if g['key'] in sub['group_keys']]
            expected = normalize(''.join(r['text'] for g in groups for r in g['source_ranges']))
            for exclusion in plan.get('non_argument_exclusions', []):
                if any(g['source_id'] == exclusion['source_id'] for g in groups):
                    prefix = normalize(''.join(exclusion['sentences']))
                    if expected.startswith(prefix): expected = expected[len(prefix):]
            actual = normalize(''.join(line['text'] for s in index['sections'] if s['sub_unit_id'] == sub['id']
                                       for row in s['sermons'] for line in row['transcript']))
            if actual != expected:
                selection_problems.append(f"Source content missing or added: {sub['id']}")
    slide_results = []
    for entry in plan['inputs']:
        title = entry['lecture']
        selection_path = chapter_exposition.record_path(data_root, passage, title)
        selection = json.loads(selection_path.read_text())
        saved = chapter_slides.load_record(data_root, passage, title)
        try:
            saved, owner_corrections = units.owner_timing(saved, selection)
            preparation_problems = []
        except ValueError as error:
            owner_corrections, preparation_problems = [], [str(error)]
        context = saved.get('claim_context')
        kept = chapter_slides.kept_pieces(selection)
        problems, _, normalized = chapter_slides.checked(saved['raw_slides'], kept, CHAPTERS[passage], context)
        problems += preparation_problems
        if normalized != saved['slides']:
            problems.append('Stored normalized slides differ from their saved model answer')
        # checked() already runs the existing sentence Eval when context is present.
        if not context:
            problems += chapter_slide_eval.evaluate(saved['slides'], kept,
                candidates=chapter_slide_eval.candidate_chapters(saved['slides'], None))['problems']
        slide_results.append({'lecture': title, 'status': 'failed' if problems else 'passed',
                              'scope': 'full_saved_slide_record', 'problems': problems,
                              'program_timing_corrections': owner_corrections,
                              'saved_publication': saved.get('publication')})
        folder = data_root / 'chapter_exposition' / passage / 'unit-supplements'
        extra_path = folder / f'{title}.selection.json'
        if extra_path.exists():
            extra = json.loads(extra_path.read_text())
            slides = json.loads((folder / f'{title}.slides.json').read_text())
            review = json.loads((folder / f'{title}.review.json').read_text())
            derived, corrections = units.reviewed_timing(slides, review, extra)
            result = chapter_slide_eval.evaluate(derived['slides'], chapter_slides.kept_pieces(extra),
                candidates=chapter_slide_eval.candidate_chapters(derived['slides'], None))
            slide_results.append({'lecture': title, 'scope': 'missing_slide_supplement',
                'status': 'failed' if result['problems'] else 'passed', **result,
                'original_review': review, 'program_timing_corrections': corrections})
    playback_problems = []
    timing_anchor_checks = []
    for correction in index.get('program_timing_corrections', []):
        if not correction.get('source_text'):
            continue
        rows = [row for section in index['sections'] for row in section['sermons']
                if row['source_id'] == correction['source_id']]
        spoken = {line['at'] for row in rows for line in row['transcript'] if line['text'] == correction['source_text']}
        shown = {point['at'] for row in rows for slide in row['slides'] for point in slide['points']
                 if point['text'] == correction['text']}
        if not spoken and not shown:
            continue  # This source's corrected topic is outside the selected Units.
        passed = len(spoken) == len(shown) == 1 and abs(next(iter(spoken)) - next(iter(shown))) < .03
        timing_anchor_checks.append({'text': correction['text'], 'source_at': sorted(spoken),
                                     'point_at': sorted(shown), 'passed': passed})
        if not passed:
            playback_problems.append('Rendered owner timing correction differs from its spoken source')
    for section in index['sections']:
        for row in section['sermons']:
            for line in row['transcript']:
                if not any(s['start'] <= line['at'] < s['end'] for s in row['stretches']):
                    playback_problems.append(f"Sentence outside playback range: {section['label']}")
            for stretch in row['stretches']:
                end = stretch['start']
                for start, finish in sorted((max(stretch['start'], s['at']), min(stretch['end'], s['at'] + s['seconds']))
                                            for s in row['slides'] if s['at'] < stretch['end'] and s['at'] + s['seconds'] > stretch['start']):
                    if start > end + 1: playback_problems.append(f"Slide coverage gap: {section['label']}")
                    end = max(end, finish)
                if end < stretch['end'] - 1: playback_problems.append(f"Slide coverage gap: {section['label']}")
    checks = {'selection': {'status': 'failed' if selection_problems else 'passed', 'problems': selection_problems},
              'slides': {'status': 'failed' if any(r['status'] != 'passed' for r in slide_results) else 'passed', 'records': slide_results},
              'playback': {'status': 'failed' if playback_problems else 'passed', 'problems': playback_problems,
                           'timing_anchor_checks': timing_anchor_checks},
              'ui': browser_checks(evidence, current)}
    if catalog is not None:
        checks['normal_entry'] = catalog_checks(catalog, index, evidence)
    return {'schema_version': 'chapter-listening-eval.v1', 'passage': passage,
            'evaluated_at': datetime.now(timezone.utc).isoformat(),
            'scope': 'browser_preview_using_saved_reviews_and_current_program_checks',
            'status': 'passed_for_browser_preview' if all(c['status'] == 'passed' for c in checks.values()) else 'needs_attention',
            'binding': current, 'checks': checks, 'deferred': ['Physical iPhone/Safari device acceptance'],
            'new_model_calls': 0, 'publication_decision': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('passage')
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--browser-evidence', type=Path)
    parser.add_argument('--catalog', type=Path, help='Exact saved normal-entry API response for end-to-end evaluation')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    from backend.api.config import DATA_BASE_PATH
    evidence = json.loads(args.browser_evidence.read_text()) if args.browser_evidence else None
    report = evaluate(args.data_dir or Path(DATA_BASE_PATH), args.passage, evidence=evidence,
                      catalog=json.loads(args.catalog.read_text()) if args.catalog else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'checks': {k: v['status'] for k, v in report['checks'].items()},
                      'report': str(args.output)}, ensure_ascii=False))
    return 0 if report['status'] == 'passed_for_browser_preview' else 1


if __name__ == '__main__':
    raise SystemExit(main())
