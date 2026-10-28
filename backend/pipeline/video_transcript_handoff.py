"""Export a staged sermon source in the existing multimodal extraction format.

This is a format bridge, never source approval. In particular, machine-traced
SVGs do not acquire professor-original attestations by passing an XML parser.
"""
from __future__ import annotations

from pathlib import Path
import shutil

from backend.pipeline.slide_change_pilot import file_sha, read_json, save_json, object_sha
from backend.pipeline.slide_visual_evidence import render_svg
from backend.pipeline.source_projection import project_script


def export_handoff(out: Path) -> dict:
    record = read_json(out / 'full_transcript.json')
    assets = {a['asset_id']: a for a in record['visual_assets']}
    folder = out / 'handoff'; folder.mkdir(exist_ok=True)
    rows, bindings = [], []
    paragraphs = record['paragraphs']
    known = {p['id'] for p in paragraphs}
    if any(s['paragraph_id'] is not None and s['paragraph_id'] not in known for s in record['slides']):
        raise ValueError('Slide has an unknown paragraph binding')
    # Visual-only events are retained as visual-only body rows, never as speech.
    ordered = [(p, [s for s in record['slides'] if s['paragraph_id'] == p['id']]) for p in paragraphs]
    ordered += [(None, [s]) for s in record['slides'] if s['paragraph_id'] is None]
    for p, occurrences in ordered:
        row = {'index': p['id'] if p else 'visual-' + occurrences[0]['id'],
               'text': p['text'] if p else '',
               'start_time': p['start_ms'] / 1000 if p and p['start_ms'] is not None else None,
               'end_time': p['end_ms'] / 1000 if p and p['end_ms'] is not None else None}
        for ordinal, slide in enumerate(occurrences, start=1):
            asset = assets['a' + slide['slide_sha256']]
            if asset['source_video_sha256'] != record['source_sha256']:
                raise ValueError('Visual handoff belongs to another video')
            image = Path(asset['original_image'])
            if file_sha(image) != asset['image_sha256'] or object_sha(asset['content']) != asset['content_sha256']:
                raise ValueError('Visual source changed before handoff')
            dest = folder / 'images' / (asset['asset_id'] + '.png'); dest.parent.mkdir(exist_ok=True)
            shutil.copyfile(image, dest)
            # Text-only slides use text elements, without manufacturing diagrams.
            svg = render_svg(asset['content'], asset['image_width'], asset['image_height'],
                             asset['image_sha256'], allow_text_only=True)
            row['text'] += '\n' + svg
            bindings.append({'locator': f'S{len(rows)+1:04d}/V{ordinal:02d}',
                'event_id': slide['id'], 'time_ms': slide['time_ms'], 'capture_ms': slide['capture_ms'],
                'asset_id': asset['asset_id'], 'image_path': str(dest.resolve()),
                'image_sha256': asset['image_sha256'], 'content_sha256': asset['content_sha256'],
                'review_status': asset['review_status'], 'quotation_status': 'not_spoken_verbatim',
                'derivation': 'model_reconstruction_unreviewed', 'issues': asset['content']['issues'],
                'uncertain_region_ids': [r['id'] for r in asset['content']['text_regions'] if r['uncertain']]})
            if slide.get('original_image'):
                full = out / 'slides' / slide['original_image']
                if file_sha(full) != slide['original_sha256']:
                    raise ValueError('Full source frame changed before handoff')
                dest_full = folder / 'images' / (slide['id'] + '-full.png')
                shutil.copyfile(full, dest_full)
                bindings[-1].update(source_frame_path=str(dest_full.resolve()),
                    source_frame_sha256=slide['original_sha256'],
                    screen_quad_original_xy=slide.get('screen_quad_original_xy'),
                    image_derivation=slide.get('image_derivation','source_pixels_axis_aligned_crop'))
        rows.append(row)
    payload = {'metadata': {'title': record['lecture_id'], 'status': 'machine_candidate',
        'source_type': 'sermon_transcript', 'source_video_sha256': record['source_sha256'],
        'full_transcript_sha256': file_sha(out / 'full_transcript.json')}, 'script': rows}
    projection = project_script(rows)
    expected = [p['text'] for p in paragraphs] + [''] * sum(s['paragraph_id'] is None for s in record['slides'])
    if [r['text'].strip() for r in projection.spoken_rows] != [t.strip() for t in expected]:
        raise ValueError('Visual handoff changed spoken text')
    by_locator = {b.locator: b for b in projection.visual_blocks}
    for binding in bindings:
        binding['raw_svg_sha256'] = by_locator[binding['locator']].raw_sha256
    path = folder / (record['lecture_id'] + '.json'); save_json(path, payload)
    result = {'schema_version': 'video-transcript-extraction-handoff.v1',
        'source_path': str(path.resolve()), 'source_file_sha256': file_sha(path),
        'source_body_sha256': projection.body_sha256, 'source_visual_sha256': projection.visual_content_sha256,
        'visual_bindings': bindings, 'visibility': record['visibility'],
        'speech_text_unchanged': True, 'visual_source_attestations': [],
        'admission_status': 'candidate_requires_source_fidelity_review', 'store_writes': 0,
        'consumer': 'backend.pipeline.detailed_knowledge_extraction_runner',
        'limitations': ['Format compatibility is not source approval. Existing extraction rejects unattested reconstructions.',
                       'Original images and uncertainties must accompany source-fidelity review; XML validity is insufficient.']}
    save_json(folder / 'extraction_handoff.json', result)
    return result
