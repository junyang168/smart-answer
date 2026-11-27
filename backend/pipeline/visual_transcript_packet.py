"""Join visual candidates beside speech, preserving separate evidence channels."""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
from pathlib import Path
import shutil

from backend.pipeline.slide_change_pilot import clock, file_sha, object_sha, read_json, save_json
from backend.pipeline.slide_visual_evidence import render_svg, validate_content


def build(transcript: Path, evidence: Path, out: Path) -> dict:
    source = read_json(transcript / 'illustrated_transcript.json')
    visuals = read_json(evidence / 'slide_evidence.json')
    if source['source_sha256'] != visuals['source_video_sha256']:
        raise ValueError('Speech and visual evidence belong to different video versions')
    source_sha = source['source_sha256']
    speech_sha = file_sha(transcript / 'illustrated_transcript.json')
    comparison_path = evidence / 'independent_comparison.json'
    comparison = read_json(comparison_path) if comparison_path.exists() else None
    if comparison is not None and comparison['primary_collection_sha256'] != file_sha(evidence / 'slide_evidence.json'):
        raise ValueError('Independent comparison belongs to another extraction revision')
    findings = {row['asset_id']: row for row in comparison['results']} if comparison else {}
    assets, region_sources, shape_sources, edge_sources = {}, [], [], []
    for asset in visuals['assets']:
        content = asset['content']
        validate_content(content)
        if (asset['source_video_sha256'] != source_sha or asset['content_sha256'] != object_sha(content)
                or asset['visible_text'] != '\n'.join(r['text'] for r in content['text_regions'])
                or asset['text_sha256'] != hashlib.sha256(asset['visible_text'].encode()).hexdigest()
                or asset['asset_id'] != 'a' + asset['image_sha256'] or content['asset_id'] != asset['asset_id']
                or asset['review_status'] != 'unreviewed_model_candidate'
                or asset['quotation_status'] != 'not_spoken_verbatim'):
            raise ValueError('Visual candidate payload changed')
        original = evidence / asset['original_image']
        if file_sha(original) != asset['image_sha256']:
            raise ValueError('Original visual source changed')
        if asset['asset_id'] in assets:
            raise ValueError('Duplicate visual asset ID')
        target = out / 'visuals' / asset['asset_id']
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target / 'original.png')
        current = copy.deepcopy(asset)
        current['independent_sample_result'] = findings.get(asset['asset_id'])
        current['original_image'] = str((target / 'original.png').relative_to(out))
        prefix = f'visual:{source_sha}:{asset["image_sha256"]}:'
        if asset['svg'] is not None:
            svg_path = evidence / asset['svg']['path']
            if (file_sha(svg_path) != asset['svg']['sha256'] or svg_path.read_text() !=
                    render_svg(content, asset['image_width'], asset['image_height'], asset['image_sha256'])):
                raise ValueError('SVG differs from its source-bound reconstruction')
            shutil.copyfile(svg_path, target / 'reconstruction.svg')
            current['svg']['path'] = str((target / 'reconstruction.svg').relative_to(out))
        assets[asset['asset_id']] = current
        binding = {'channel': 'slide_visual', 'asset_id': asset['asset_id'], 'image_sha256': asset['image_sha256'],
                   'content_sha256': asset['content_sha256'], 'review_status': asset['review_status'],
                   'quotation_status': 'not_spoken_verbatim', 'admission_status': 'candidate_not_ingested'}
        region_sources += [{**binding, **region, 'evidence_id': prefix + region['id']} for region in content['text_regions']]
        shape_sources += [{**binding, **shape, 'evidence_id': prefix + shape['id']} for shape in content['shapes']]
        edge_sources += [{**binding, **edge, 'evidence_id': prefix + edge['id'],
                          'interpretation': 'visible_connector_only_not_an_argument_claim'} for edge in content['edges']]
    events = {event['id']: event for event in source['slides']}
    seen, occurrences = set(), []
    for occurrence in visuals['occurrences']:
        event = events.get(occurrence['event_id'])
        asset = assets.get(occurrence['asset_id'])
        if event is None or asset is None or occurrence['event_id'] in seen:
            raise ValueError('Visual occurrence has a missing or duplicate source event')
        if (event['image_sha256'] != asset['image_sha256'] or event['time_ms'] != occurrence['time_ms']
                or event['capture_ms'] != occurrence['capture_ms']):
            raise ValueError('Visual occurrence does not match the original timeline')
        seen.add(occurrence['event_id'])
        occurrences.append({**occurrence, 'paragraph_id': event['paragraph_id'],
                            'image': asset['original_image'], 'svg': asset['svg']})
    if seen != set(events):
        raise ValueError('Some confirmed slide occurrences have no extraction result')
    paragraphs = copy.deepcopy(source['paragraphs'])
    record = {'schema_version': 'illustrated-transcript-with-visual-evidence.v1',
        'source_sha256': source_sha, 'speech_artifact_sha256': speech_sha,
        'visual_evidence_sha256': file_sha(evidence / 'slide_evidence.json'),
        'paragraphs': paragraphs, 'assets': list(assets.values()), 'occurrences': occurrences,
        'speech_text_unchanged': paragraphs == source['paragraphs'],
        'visibility': source.get('visibility'), 'visibility_available': source.get('visibility_available', False),
        'review_status': 'unreviewed_model_candidate', 'store_writes': 0}
    record['independent_comparison'] = comparison
    save_json(out / 'illustrated_with_visual_text.json', record)
    packet = {'schema_version': 'visual-argument-source-packet.v1', 'source_video_sha256': source_sha,
        'speech_artifact_sha256': speech_sha, 'visual_evidence_sha256': record['visual_evidence_sha256'],
        'speech_segments': [{'evidence_id': f'speech:{speech_sha}:{p["id"]}', 'channel': 'speech', **p} for p in paragraphs],
        'slide_text_regions': region_sources, 'diagram_shapes': shape_sources, 'diagram_edges': edge_sources,
        'visual_occurrences': occurrences, 'admission_status': 'candidate_not_ingested',
        'independent_comparison': comparison,
        'visual_source_attestations': [],
        'downstream_rules': [
            'Speech and displayed text are separate source channels; never label slide-only text as spoken.',
            'OCR and SVG are model-derived candidates; original images remain the source evidence.',
            'Visible connectors describe geometry, not automatically causality, support, or theological conclusions.',
            'Use source-bound region/shape/edge IDs for later evidence coverage; do not silently ignore visual inventory.',
            'No production source registration, attestation, Claim or Canonical Viewpoint approval is supplied.']}
    save_json(out / 'argument_source_packet.json', packet)
    e = html.escape
    sections, markdown = [], ['# 圖文逐字稿與投影片文字試稿', '', '口述與投影片文字分開標記；機器辨識尚待核對。', '']
    for paragraph in paragraphs:
        time = clock(paragraph['start_ms']) if paragraph['start_ms'] is not None else '時間待核對'
        parts = [f'<section><h2>{time}</h2>']
        markdown += [f'## {time}', '']
        for occurrence in occurrences:
            if occurrence['paragraph_id'] != paragraph['id']:
                continue
            asset = assets[occurrence['asset_id']]
            label = f'投影片原圖 {clock(occurrence["time_ms"])}'
            parts += [f'<figure><img loading="lazy" src="{e(asset["original_image"])}"><figcaption>{label}</figcaption></figure>',
                      '<aside><h3>投影片文字（機器辨識，非口述）</h3>', f'<pre>{e(asset["visible_text"])}</pre>']
            markdown += [label, f'![原投影片]({asset["original_image"]})', '',
                         '### 投影片文字（機器辨識，非口述）', '', asset['visible_text'], '']
            if asset['content']['issues'] or any(r['uncertain'] for field in ('text_regions', 'shapes', 'edges') for r in asset['content'][field]):
                parts.append('<p>部分字詞或圖片區域待核對。</p>')
            if asset['independent_sample_result'] and not asset['independent_sample_result']['exact_text_match']:
                parts.append('<p>兩家模型的文字辨識有差異，請對照原圖核對。</p>')
                markdown += ['兩家模型的文字辨識有差異，請對照原圖核對。', '']
            if asset['svg']:
                path = asset['svg']['path']
                parts += [f'<p><a href="{e(path)}">下載 SVG 轉繪稿</a>（保留原圖核對）</p>',
                          f'<img loading="lazy" src="{e(path)}">']
                markdown += [f'[SVG 轉繪稿，尚未核對]({path})', '']
            parts.append('</aside>')
        parts += ['<h3>口述</h3>', f'<p>{e(paragraph["text"]).replace(chr(10), "<br>")}</p></section>']
        sections.append(''.join(parts))
        markdown += ['### 口述', '', paragraph['text'], '']
    (out / 'illustrated_with_visual_text.md').write_text('\n'.join(markdown), encoding='utf-8')
    (out / 'index.html').write_text('''<!doctype html><html lang="zh-Hant"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>圖文逐字稿與投影片文字試稿</title>
<style>body{font:19px/1.8 system-ui;max-width:1000px;margin:auto;padding:24px;background:#f6f6f6;color:#222}
section{background:white;padding:18px;margin:18px 0;border-radius:8px}img{max-width:100%;height:auto}figure{margin:12px 0}
aside{padding:16px;background:#eef3f8;border-left:4px solid #627f9d}pre{white-space:pre-wrap;font:inherit;overflow-wrap:anywhere}h3{font-size:18px}</style>
<h1>圖文逐字稿與投影片文字試稿</h1><p>口述與投影片文字分開標記；機器辨識尚待核對。原圖保留，圖表轉繪不取代來源。</p>
''' + ''.join(sections) + '</html>', encoding='utf-8')
    return {'paragraphs': len(paragraphs), 'slides': len(occurrences), 'text_regions': len(region_sources),
            'diagrams': sum(a['svg'] is not None for a in assets.values()), 'speech_text_unchanged': record['speech_text_unchanged']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--transcript', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(build(args.transcript, args.evidence, args.out))


if __name__ == '__main__':
    main()
