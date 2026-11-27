"""Prepare an editorial Unit decision, then compile it into the listening format."""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

from backend.pipeline import chapter_argument_selection, chapter_exposition
from backend.pipeline.chapter_material import load_chapters, chapters_path, verse_range
from backend.pipeline.chapter_slide_claims import digest, instance_records
from backend.pipeline.original_audio_index import verse_texts

RULES = """Assign each unplaced complete argument group once across the chapter.
Use the verses actually explained to set Unit boundaries. Distinguish scripture used
as evidence and passages merely named in an overview. Keep approved Units intact.
Keep arguments complete and put audience questions with the argument their answer
develops, even across lectures. Preserve the question and answer together.
Keep a cross-passage argument together if the professor makes that connection.
Use Subunits for coherent listening sections. Order Subunits as they should play.
Mark supplementary or insufficient material as deferred and explain each decision.
In boundary_notes, identify mixed-topic paragraphs needing exact listening checks.
Use mat-6-8-to-7-11 syntax for a cross-chapter Unit.
Return {input_sha256, units, deferred_groups, boundary_notes}. Each new Unit has passage (e.g.
mat-5-31-32), title, and sub_units. Each Subunit has title and arguments, an ordered
list of {key, placement, reason}; placement is direct or context. A deferred
group has {key, reason}. Do not select timestamps or rewrite the transcript.
Within each recording preserve the original order inside a Subunit."""


def passage_bounds(passage):
    cross = re.fullmatch(r'([a-z0-9]+)-(\d+)-(\d+)-to-(\d+)-(\d+)', passage)
    if cross:
        book, first_chapter, first, last_chapter, last = cross.groups()
        return book, (int(first_chapter), int(first)), (int(last_chapter), int(last))
    book, chapter, first, last = verse_range(passage)
    return book, (chapter, first), (chapter, last)


def prepare(passage, sources, *, data_root, baseline=None, additional_passages=()):
    """Load explicitly selected, version-bound argument layers for the chapter."""
    from backend.pipeline.argument_review import connect

    scope = (passage, *additional_passages)
    verses = {}
    for slug in scope:
        book, chapter, low, high = verse_range(slug)
        verses.update(verse_texts(data_root, book, chapter, low, high))
    configured = load_chapters(chapters_path(data_root)).get(passage)
    inputs, groups = [], {}
    with connect() as conn:
        for lecture_number, change_set in enumerate(sources, 1):
            records, meta = instance_records(conn, change_set)
            topics, paragraphs, owners, document = chapter_argument_selection.topic_material(records, meta)
            source_id, lecture = document['source_id'], document['title']
            transcript = data_root / 'script_published' / f'{lecture}.json'
            source_sha = chapter_exposition.file_sha256(transcript)
            if chapter_exposition.file_sha256(Path(document['source_path'])) != source_sha:
                raise ValueError(f'Published transcript differs from argument layer: {lecture}')
            if any(entry['source_id'] == source_id for entry in inputs):
                raise ValueError(f'Duplicate source: {source_id}')
            inputs.append({'source_id': source_id, 'lecture': lecture, 'changeset': change_set,
                           'source_path': document['source_path'], 'source_sha256': source_sha})
            for topic in topics:
                group_id = topic['group_id']
                key = f'{source_id}:{group_id}'
                paragraphs_in_group = [k for k, owner in owners.items() if owner == group_id]
                fragments = [f for f in records.get('source_fragments', [])
                             if f.get('paragraph_key') in paragraphs_in_group]
                fragment_ids = {f['fragment_id'] for f in fragments}
                groups[key] = {
                    **topic, 'source_id': source_id, 'lecture': lecture,
                    'lecture_number': lecture_number, 'paragraph_keys': paragraphs_in_group,
                    'observations': [o for o in records.get('observations', [])
                                     if fragment_ids.intersection(o.get('source_fragment_ids', []))],
                    'questions': [q for q in records.get('questions', [])
                                  if fragment_ids.intersection(q.get('source_fragment_ids', []))],
                    'source_ranges': [
                        {'paragraph_key': k, 'start': float(paragraphs[k]['start_time']),
                         'end': float(paragraphs[k]['end_time']), 'text': paragraphs[k]['text']}
                        for k in paragraphs_in_group if paragraphs[k]['text'].strip() not in ('', '.')
                    ],
                }
    return {'passage': passage, 'inputs': inputs, 'groups': groups,
            'scope': list(scope), 'chapter_verses': verses,
            'reserved_unit_numbers': len(configured.sections) if configured else 0,
            'baseline': baseline or {}, 'rules': RULES}


def compile_plan(packet, decision):
    """Check coverage and resolve the decisions to the existing playback contract."""
    if decision['input_sha256'] != digest(packet):
        raise ValueError('Unit decision does not match the prepared input')
    groups = packet['groups']
    baseline = packet['baseline']
    units = dict(baseline.get('units', {}))
    assigned = set()
    for unit in units.values():
        for group in unit['groups']:
            key = group['key']
            if key not in groups or key in assigned or group['source_ranges'] != groups[key]['source_ranges']:
                raise ValueError(f'Approved group is missing, duplicated or changed: {key}')
            assigned.add(key)
    inputs = {entry['source_id']: entry for entry in packet['inputs']}
    for entry in baseline.get('inputs', []):
        current = inputs.get(entry['source_id'])
        if current is None or any(entry[k] != current[k] for k in ('source_sha256', 'changeset')):
            raise ValueError(f"Approved source changed: {entry['lecture']}")
        inputs[entry['source_id']] = entry

    scope = [passage_bounds(p) for p in packet['scope']]
    allowed = {(chapter, verse)
               for _, (chapter, first), (_, last) in scope
               for verse in range(first, last + 1)}
    book = scope[0][0]
    if any(part[0] != book for part in scope):
        raise ValueError('Unit planning scope must use one book')
    occupied = set()

    def claim_range(passage):
        unit_book, start, end = passage_bounds(passage)
        verses = {v for v in allowed if start <= v <= end}
        if (unit_book != book or start not in allowed or end not in allowed or start > end
                or {v[0] for v in verses} != set(range(start[0], end[0] + 1))
                or occupied.intersection(verses)):
            raise ValueError(f'Overlapping or out-of-scope Unit: {passage}')
        occupied.update(verses)

    for passage in units:
        claim_range(passage)
    number = max(packet['reserved_unit_numbers'], max((u['unit_number'] for u in units.values()), default=0))
    for proposal in sorted(decision['units'], key=lambda u: passage_bounds(u['passage'])[1]):
        passage = proposal['passage']
        claim_range(passage)
        number += 1
        sub_units, selected, playback = [], [], []
        if not proposal['title'].strip() or not proposal['sub_units']:
            raise ValueError(f'Unit requires a title and Subunits: {passage}')
        for index, sub in enumerate(proposal['sub_units'], 1):
            keys, clips = [], []
            if not sub['title'].strip() or not sub['arguments']:
                raise ValueError(f'Subunit requires a title and arguments: {passage}')
            for argument in sub['arguments']:
                key = argument['key']
                if (key not in groups or key in assigned or argument['placement'] not in ('direct', 'context')
                        or not argument['reason'].strip()):
                    raise ValueError(f'Invalid or duplicate group assignment: {key}')
                assigned.add(key)
                keys.append(key)
                group = groups[key]
                selected.append({k: group[k] for k in ('source_id', 'lecture', 'group_id', 'title',
                                                       'paragraph_keys', 'source_ranges')} | {
                    'key': key, 'placement': argument['placement'], 'reason': argument['reason'],
                    'claim_ids': [claim['claim_id'] for claim in group['claims']],
                })
                for row in group['source_ranges']:
                    if not all(math.isfinite(row[k]) for k in ('start', 'end')) or not 0 <= row['start'] < row['end']:
                        raise ValueError(f'Invalid source timing: {key} {row["paragraph_key"]}')
                    clips.append({k: row[k] for k in ('paragraph_key', 'start', 'end')} | {
                        'group_key': key, 'source_id': group['source_id'], 'lecture_number': group['lecture_number'],
                    })
            if not clips:
                raise ValueError(f'Subunit has no playable source: {passage}')
            # Keep the requested recording order. Restore transcript order where
            # intact argument groups interleave within the same recording.
            for source in dict.fromkeys(clip['source_id'] for clip in clips):
                playback.extend(sorted((c for c in clips if c['source_id'] == source), key=lambda c: c['start']))
            sub_units.append({'id': f'{number}.{index}', 'title': sub['title'], 'group_keys': keys})
        units[passage] = {'unit_number': number, 'passage': passage, 'title': proposal['title'],
                          'sub_units': sub_units, 'groups': selected, 'playback_order': playback,
                          'paragraph_envelope_seconds': sum(c['end'] - c['start'] for c in playback)}

    deferred = set()
    for item in decision['deferred_groups']:
        key = item['key']
        if key not in groups or key in assigned or key in deferred or not item['reason'].strip():
            raise ValueError(f'Invalid or duplicate deferred group: {key}')
        deferred.add(key)
    missing = groups.keys() - assigned - deferred
    if missing:
        raise ValueError(f'Groups without a disposition: {", ".join(sorted(missing))}')
    ranges = {}
    for unit in units.values():
        for clip in unit['playback_order']:
            ranges.setdefault(clip['source_id'], []).append((clip['start'], clip['end']))
    for source_id, clips in ranges.items():
        ordered = sorted(clips)
        if any(left[1] > right[0] for left, right in zip(ordered, ordered[1:])):
            raise ValueError(f'Playback repeats source audio: {source_id}')
    # Previous sign-offs remain on the original plan and on the approved Units.
    metadata = {key: baseline[key] for key in ('ticket', 'non_argument_exclusions') if key in baseline}
    return {**metadata, 'schema_version': baseline.get('schema_version', 'chapter-listening-units.v1'),
            'status': 'candidate', 'scope': packet['scope'], 'inputs': list(inputs.values()), 'decision_input_sha256': digest(packet),
            'playback_sequence': 'subunit',
            'units': dict(sorted(units.items(), key=lambda pair: passage_bounds(pair[0])[1])),
            'deferred_groups': decision['deferred_groups'],
            'boundary_notes': decision.get('boundary_notes', [])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    stages = parser.add_subparsers(dest='stage', required=True)
    prep = stages.add_parser('prepare')
    prep.add_argument('passage')
    prep.add_argument('--data-root', type=Path, required=True)
    prep.add_argument('--changeset', action='append', required=True)
    prep.add_argument('--additional-passage', action='append', default=[])
    prep.add_argument('--baseline', type=Path)
    prep.add_argument('--output', type=Path, required=True)
    compile_stage = stages.add_parser('compile')
    compile_stage.add_argument('packet', type=Path)
    compile_stage.add_argument('decision', type=Path)
    compile_stage.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.stage == 'prepare':
        packet = prepare(args.passage, args.changeset, data_root=args.data_root,
                         baseline=json.loads(args.baseline.read_text()) if args.baseline else None,
                         additional_passages=args.additional_passage)
        result = {**packet, 'input_sha256': digest(packet)}
    else:
        packet = json.loads(args.packet.read_text())
        packet.pop('input_sha256')
        result = compile_plan(packet, json.loads(args.decision.read_text()))
    with args.output.open('x') as output:
        json.dump(result, output, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
