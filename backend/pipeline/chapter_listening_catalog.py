"""One chapter catalog for the overview and the continuous listening player."""
from copy import deepcopy
import hashlib
import json


def parent_passage(section):
    return section.get('unit_passage') or section['passage'].split('-sub-', 1)[0]


def recording_key(row):
    return row.get('transcript_id') or row.get('media_url') or row['source_id']


def subtract(stretch, covered):
    pieces = [(stretch['start'], stretch['end'])]
    for start, end in covered:
        pieces = [(a, b) for low, high in pieces
                  for a, b in [(low, min(high, start)), (max(low, end), high)] if b > a]
    return [{**stretch, 'start': a, 'end': b} for a, b in pieces]


def has_playback_content(row, span):
    start, end = span['start'], span['end']
    return (
        any(start <= line['at'] < end and line.get('text', '').strip()
            for line in row.get('transcript', ()))
        or any(slide['at'] < end and slide['at'] + slide['seconds'] > start
               for slide in row.get('slides', ()))
    )


def compose(legacy, updated):
    """Replace approved passages, retaining uncovered older passages without duplicate audio.

    Replaced source material stays available via the explicit legacy reader.
    No slide wording, source file or previous generation record is rewritten.
    """
    result = deepcopy(legacy)
    sections = deepcopy(updated['sections'])
    replaced = {parent_passage(section) for section in sections}
    covered, new_sources = {}, {}
    for section in sections:
        section['unit_passage'] = parent_passage(section)
        for row in section['sermons']:
            key = recording_key(row)
            new_sources[key] = row['source_id']
            covered.setdefault(key, []).extend((s['start'], s['end']) for s in row['stretches'] if not s.get('left_out'))
    source_aliases, section_aliases = {}, {}
    for section in legacy['sections']:
        remaining = []
        overlaps = []
        changed = False
        for original in section['sermons']:
            key = recording_key(original)
            if key in new_sources: source_aliases[original['source_id']] = new_sources[key]
            if section['passage'] in replaced: continue
            row = deepcopy(original)
            row['stretches'] = [
                part for stretch in original['stretches'] for part in subtract(stretch, covered.get(key, []))
                # Different sentence and clip boundaries can leave a fraction
                # of a second after moving an argument into its new Unit.
                if part.get('left_out') or (
                    (part == stretch or part['end'] - part['start'] >= 1
                     or any(part['start'] <= line['at'] < part['end']
                            for line in original.get('transcript', ())))
                    # A legacy recording can include text later struck out by
                    # editors. Once rebuilt, keep only supported legacy clips.
                    and (key not in covered or 'transcript' not in original or 'slides' not in original
                         or any(part['start'] <= line['at'] < part['end']
                            for line in original.get('transcript', ())))
                )
            ]
            kept = [s for s in row['stretches'] if not s.get('left_out')]
            if kept and row['stretches'] == original['stretches']:
                remaining.append(row)
                continue
            changed = True
            if not kept:
                overlaps += [new['passage'] for new in updated['sections']
                             if any(recording_key(r) == key and any(
                                 a['start'] < b['end'] and b['start'] < a['end']
                                 for a in original['stretches'] if not a.get('left_out')
                                 for b in r['stretches'] if not b.get('left_out')) for r in new['sermons'])]
                continue
            inside = lambda at: any(s['start'] <= at < s['end'] for s in row['stretches'])
            for field in ('transcript', 'judgements', 'spoken', 'readings', 'original_language_events'):
                if field in row: row[field] = [item for item in row[field] if inside(item['at'])]
            if 'slides' in row:
                slides = []
                for slide in row['slides']:
                    own = [s for s in kept if s['start'] < slide['at'] + slide['seconds'] and slide['at'] < s['end']]
                    if own:
                        start = max(slide['at'], min(s['start'] for s in own))
                        end = min(slide['at'] + slide['seconds'], max(s['end'] for s in own))
                        slides.append({**slide, 'at': start, 'seconds': end - start})
                row['slides'] = slides
            row['seconds'] = sum(s['end'] - s['start'] for s in kept)
            row['left_out_seconds'] = sum(s['end'] - s['start'] for s in row['stretches'] if s.get('left_out'))
            remaining.append(row)
        if section['passage'] in replaced: continue
        if not remaining:
            if overlaps: section_aliases[section['passage']] = overlaps[0]
            continue
        own = {**deepcopy(section), 'sermons': remaining, 'unit_passage': section['passage']}
        # Canonical verse order determines the Unit number, including omitted sections.
        from backend.pipeline.chapter_material import CHAPTERS
        order = CHAPTERS[legacy['passage']].sections
        number = order.index(section['passage']) + 1
        own.update(unit_id=str(number), unit_label=f"Unit {number} · {section['label']}")
        if changed: own.pop('topics', None)  # Old summaries were bound to the pre-trimmed material.
        sections.append(own)
    if updated.get('playback_sequence') == 'subunit':
        from backend.pipeline.chapter_unit_plan import passage_bounds
        sections.sort(key=lambda section: passage_bounds(parent_passage(section))[1])
    else:
        sections.sort(key=lambda section: int(section['unit_id']))

    result.update(sections=sections, unit_navigation=True, source_aliases=source_aliases,
                  section_aliases=section_aliases, selection_preview=False,
                  program_timing_corrections=updated.get('program_timing_corrections', []))
    result['resume_version'] = hashlib.sha256(json.dumps({
        'updated_version': updated['resume_version'],
        'playback': [(s['passage'], [(r['source_id'], r['stretches']) for r in s['sermons']]) for s in sections],
    }, sort_keys=True).encode()).hexdigest()
    return result
