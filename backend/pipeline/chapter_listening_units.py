"""Project approved passage Units onto existing, source-bound slides and audio.

This reader projects approved whole-argument selections and never generates
slides. Old AI omissions may be superseded by the approved plan; source text
and saved slides remain unchanged. It is opt-in until owner acceptance.
"""
from __future__ import annotations

from itertools import groupby
import json
from pathlib import Path

from backend.pipeline import chapter_exposition as exposition, chapter_slides
from backend.pipeline import original_audio_index as audio
from backend.pipeline.source_projection import LOCATOR_SPACE, project_script


def project(plan: dict, recordings: dict) -> dict:
    """Keep argument identity distinct from its possibly interrupted playback runs."""
    sections = []
    assigned = set()
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
            lines = [line for line in recording['lines'] if any(
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
                   'transcript': audio._transcript(lines, stretches),
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
                    or review['slides_sha256'] != exposition.file_sha256(extra_slides_path)
                    or chapter_slides.review_blocks_publication(review)):
                raise ValueError('Supplement does not have a matching source and passing review')
            _, additional = chapter_slides.placed(extra, extra_slides)
            timed = sorted(timed + additional, key=lambda slide: slide['at'])
        recordings[source] = {
            'media': {k: sermon[k] for k in ['source_id', 'transcript_id', 'title', 'media_kind', 'media_url', 'media_duration']},
            # New whole-argument selections supersede old AI omission decisions,
            # while the old kept sentence IDs still position the reused slides.
            'lines': chapter_slides.sentences(selection['pieces']),
            'slides': timed, 'retime': audio.retimer(sermon, data_root)}
    return project(plan, recordings)
