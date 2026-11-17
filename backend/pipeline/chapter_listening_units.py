"""Project approved passage Units onto existing, source-bound slides and audio.

This reader projects approved whole-argument selections and never generates
slides. Old AI omissions may be superseded by the approved plan; source text
and saved slides remain unchanged. It is opt-in until owner acceptance.
"""
from __future__ import annotations

from itertools import groupby
from copy import deepcopy
import json
import hashlib
from pathlib import Path

from backend.pipeline import chapter_exposition as exposition, chapter_slides
from backend.pipeline import original_audio_index as audio
from backend.pipeline.source_projection import LOCATOR_SPACE, project_script


def reviewed_timing(slides: dict, review: dict, selection: dict) -> tuple[dict, list]:
    """Apply only the reviewer's unambiguous sentence-anchor prescriptions.

    Original model slides and verdict remain immutable. No reader-visible words
    change; checked() validates the full derived result against the same source.
    Content findings and ambiguous or out-of-slide anchors remain blocked.
    """
    derived = deepcopy(slides)
    corrections = []
    lines = chapter_slides.sentences(chapter_slides.kept_pieces(selection))
    positions = {line['id']: i for i, line in enumerate(lines)}
    for finding in review['findings']:
        if not chapter_slides.finding_blocks(finding):
            continue
        if finding['kind'] != '出現時間':
            raise ValueError('Supplement still has unresolved content findings')
        own = [s for s in derived['slides'] if s['first'] == finding['slide']]
        now, proposed = finding['current_sentence'], finding['proposed_sentence']
        if len(own) != 1 or proposed not in positions:
            raise ValueError('Reviewer timing target is not uniquely located')
        slide = own[0]
        points = [p for p in slide['points'] if p['sentence'] == now]
        if (len(points) != 1 or not positions[slide['first']] <= positions[proposed] <= positions[slide['last']]):
            raise ValueError('Reviewer timing target is ambiguous or outside this slide')
        points[0]['sentence'] = proposed
        raw_slide = next(s for s in derived['raw_slides'] if s['first'] == finding['slide'])
        raw_point = [p for p in raw_slide['points'] if p['sentence'] == now and p['text'] == points[0]['text']]
        if len(raw_point) != 1:
            raise ValueError('Raw and normalized timing anchors disagree')
        raw_point[0]['sentence'] = proposed
        corrections.append({'slide': finding['slide'], 'text': points[0]['text'],
                            'from_sentence': now, 'to_sentence': proposed})
    if corrections:
        from backend.pipeline.chapter_material import CHAPTERS
        problems, _, checked = chapter_slides.checked(
            derived['raw_slides'], chapter_slides.kept_pieces(selection), CHAPTERS[selection['passage']], None)
        if problems or checked != derived['slides']:
            raise ValueError(f'Reviewed timing correction failed source checks: {problems}')
    elif chapter_slides.review_blocks_publication(review):
        raise ValueError('Blocking review has no mechanically resolvable timing prescription')
    return derived, corrections


def project(plan: dict, recordings: dict) -> dict:
    """Keep argument identity distinct from its possibly interrupted playback runs."""
    sections = []
    assigned = set()
    excluded = {}
    for item in plan.get('non_argument_exclusions', []):
        lines = recordings[item['source_id']]['lines']
        texts = item['sentences']
        if not texts or item['reason'] != 'opening_announcement':
            raise ValueError('Invalid non-argument exclusion')
        matches = [i for i in range(len(lines) - len(texts) + 1)
                   if [line['text'] for line in lines[i:i + len(texts)]] == texts]
        if len(matches) != 1 or matches[0] != 0:
            raise ValueError('Opening announcement must uniquely match the source beginning')
        excluded.setdefault(item['source_id'], set()).update(line['start'] for line in lines[:len(texts)])
    for passage, unit in plan['units'].items():
        groups = {g['key']: g for g in unit['groups']}
        sub_by_group = {}
        for sub in unit['sub_units']:
            for key in sub['group_keys']:
                if key in assigned or key not in groups:
                    raise ValueError('Duplicate or unknown argument group assignment')
                assigned.add(key)
                sub_by_group[key] = sub
        if set(sub_by_group) != set(groups):
            raise ValueError('Sub-units must cover every selected complete argument')
        wanted = {(g['key'], r['paragraph_key']) for g in groups.values() for r in g['source_ranges']}
        actual = [(r['group_key'], r['paragraph_key']) for r in unit['playback_order']]
        if len(actual) != len(set(actual)) or set(actual) != wanted:
            raise ValueError('Playback has missing or duplicate source paragraphs')
        for source in {r['source_id'] for r in unit['playback_order']}:
            own = [r for r in unit['playback_order'] if r['source_id'] == source]
            if any(a['end'] > b['start'] for a, b in zip(own, own[1:])):
                raise ValueError('Playback must preserve source order')
        seen = set()
        for run_index, ((sid, source), rows) in enumerate(groupby(
                unit['playback_order'], lambda r: (sub_by_group[r['group_key']]['id'], r['source_id'])), 1):
            ranges = list(rows)
            sub = sub_by_group[ranges[0]['group_key']]
            recording = recordings[source]
            lines = [line for line in recording['lines'] if line['start'] not in excluded.get(source, set()) and any(
                r['start'] <= line['start'] < r['end'] for r in ranges)]
            if not lines:
                raise ValueError(f'No existing slide source for Sub-unit {sid}')
            stretches = []
            for line in lines:
                if stretches and abs(stretches[-1]['end'] - line['start']) < .01:
                    stretches[-1]['end'] = line['end']
                else:
                    stretches.append({'start': line['start'], 'end': line['end']})
            value = {'pieces': stretches}
            slides = audio._chapter_slides({'run': value}, recording['slides']).get('run', [])
            if not slides:
                raise ValueError(f'No existing slides for Sub-unit {sid}')
            row = {**recording['media'], 'stretches': stretches,
                   'slides': slides, 'judgements': [],
                   # Keep full precision until the shared audio conversion;
                   # rounding a sentence first can hide it just before clip start.
                   'transcript': [{'at': line['start'], 'end': line['end'], 'text': line['text']} for line in lines],
                   'spoken': [], 'readings': [], 'original_language_events': []}
            row = audio._retimed(recording['retime'], row)
            continued = sid in seen
            sections.append({'passage': f'{passage}-sub-{sid}-run-{run_index}',
                             'unit_id': str(unit['unit_number']),
                             'unit_label': f"Unit {unit['unit_number']} · {unit['passage']} · {unit['title']}",
                             'sub_unit_id': sid, 'continued': continued,
                             'label': f"{sid} {sub['title']}" + ('（續）' if continued else ''),
                             'sermons': [row]})
            seen.add(sid)
    return {'schema_version': 'wang_original_audio_index_v4', 'passage': 'mat-5-1-48',
            'label': '馬太福音 5', 'source': '紐約靈命進深會',
            'title': 'Unit／Sub-unit 聆聽預覽', 'sermons': [], 'sections': sections,
            'missing_lectures': [], 'selection_preview': True}


def build(data_root: Path, passage: str) -> dict:
    path = data_root / 'chapter_exposition' / passage / 'listening-units.json'
    plan = json.loads(path.read_text())
    recordings = {}
    timing_corrections = []
    sermons = audio.Sermons(data_root)
    for entry in plan['inputs']:
        title, source = entry['lecture'], entry['source_id']
        transcript = data_root / 'script_published' / f'{title}.json'
        if exposition.file_sha256(transcript) != entry['source_sha256']:
            raise ValueError('Unit source changed; selection must be checked again')
        selection = exposition.load_record(data_root, passage, title, entry['source_sha256'])
        slides = chapter_slides.load_record(data_root, passage, title)
        if selection is None or slides is None:
            raise ValueError('Unit requires matching existing selection and slides')
        document = {'transcript_id': title, 'title': title, 'source_path': str(transcript)}
        # This new projection consumes paragraph keys, never legacy fragment indices.
        projection = project_script(json.loads(transcript.read_text())['script'])
        document.update(locator_space=LOCATOR_SPACE, source_body_sha256=projection.body_sha256)
        sermon = sermons.load(source, document)
        if not sermon or not sermon.get('media_url'):
            raise ValueError('Unit recording is unavailable')
        _, timed = chapter_slides.placed(selection, slides)
        supplement_root = data_root / 'chapter_exposition' / passage / 'unit-supplements'
        extra_selection_path = supplement_root / f'{title}.selection.json'
        if extra_selection_path.exists():
            extra = json.loads(extra_selection_path.read_text())
            extra_slides_path = supplement_root / f'{title}.slides.json'
            extra_slides = json.loads(extra_slides_path.read_text())
            review = json.loads((supplement_root / f'{title}.review.json').read_text())
            if (extra['transcript_sha256'] != entry['source_sha256']
                    or extra_slides['decisions_sha256'] != exposition.file_sha256(extra_selection_path)
                    or review['slides_sha256'] != exposition.file_sha256(extra_slides_path)):
                raise ValueError('Supplement does not have matching source and review bindings')
            extra_slides, corrections = reviewed_timing(extra_slides, review, extra)
            timing_corrections.extend({'source_id': source, **c} for c in corrections)
            _, additional = chapter_slides.placed(extra, extra_slides)
            timed = sorted(timed + additional, key=lambda slide: slide['at'])
        recordings[source] = {
            'media': {k: sermon[k] for k in ['source_id', 'transcript_id', 'title', 'media_kind', 'media_url', 'media_duration']},
            # New whole-argument selections supersede old AI omission decisions,
            # while the old kept sentence IDs still position the reused slides.
            'lines': chapter_slides.sentences(selection['pieces']),
            'slides': timed, 'retime': audio.retimer(sermon, data_root)}
    result = project(plan, recordings)
    result['program_timing_corrections'] = timing_corrections
    result['resume_version'] = hashlib.sha256(json.dumps({
        'sources': [(entry['source_id'], entry['source_sha256']) for entry in plan['inputs']],
        'playback': [(s['passage'], [(r['source_id'], r['stretches']) for r in s['sermons']])
                     for s in result['sections']],
    }, sort_keys=True).encode()).hexdigest()
    return result
