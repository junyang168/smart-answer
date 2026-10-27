"""Build a local review artifact; picture evidence never becomes speech text."""
from __future__ import annotations

import argparse
from collections import Counter
import html
from pathlib import Path
import shutil

from backend.pipeline.slide_change_pilot import clock, file_sha, object_sha, read_json, save_json
from backend.pipeline.transcript_text_layout import apply_layout, text_units, text_sha


def paragraph_times(paragraphs: list[dict], tokens: list[dict]) -> list[dict]:
    result, previous = [], -1
    for paragraph in paragraphs:
        rows = [t for t in tokens if t['start'] < paragraph['source_end'] and t['end'] > paragraph['source_start']]
        anchors = [t for t in rows if t['timing_precision'] == 'word']
        start = anchors[0]['audio_start_ms'] if anchors else None
        end = anchors[-1]['audio_end_ms'] if anchors else None
        precision = 'phrase'
        if start is None or end <= start or start < previous:
            start = end = None
            precision = 'unresolved'
        else:
            previous = start
        result.append({**paragraph, 'start_ms': start, 'end_ms': end, 'timing_precision': precision,
                       'acoustic_anchor_count': len(anchors), 'unresolved_or_foreign_units': len(rows) - len(anchors)})
    return result


def place_slides(paragraphs: list[dict], events: list[dict]) -> list[dict]:
    placements = []
    for event in events:
        # Attach to a whole paragraph. Preserve the true visual event timestamp
        # separately; do not split a sentence or claim semantic agreement.
        target = next((p for p in paragraphs if p['start_ms'] is not None and p['end_ms'] >= event['time_ms']), None)
        if target is None:
            target = paragraphs[-1]
        distance = None if target['start_ms'] is None else max(target['start_ms'] - event['time_ms'],
                                                               event['time_ms'] - target['end_ms'], 0)
        placements.append({**event, 'paragraph_id': target['id'], 'distance_to_paragraph_interval_ms': distance,
                           'method': 'visual time to containing or next whole paragraph; no semantic inference'})
    if len({p['id'] for p in placements}) != len(events):
        raise ValueError('Duplicate slide occurrence IDs')
    return placements


def assemble(out: Path, slide_out: Path) -> dict:
    manifest = read_json(out / 'audio_manifest.json')
    timed = read_json(out / 'timed_text.json')
    formatted = read_json(out / 'formatted_text.json')
    plan = read_json(out / 'format_plan.json')
    if (formatted['source_text_sha256'] != text_sha(timed['text'])
            or timed['asr_sha256'] != file_sha(out / 'asr.json')):
        raise ValueError('Text stage dependencies changed')
    reconstructed = apply_layout(timed['text'], text_units(timed['text']), plan)
    if reconstructed['paragraphs'] != formatted['paragraphs']:
        raise ValueError('Formatted text differs from validated operations')
    source_slides = read_json(slide_out / 'report.json')
    if source_slides['source_sha256'] != manifest['source_sha256']:
        raise ValueError('Audio and slides are from different video versions')
    paragraphs = paragraph_times(formatted['paragraphs'], timed['tokens'])
    events = []
    for original in source_slides['events']:
        if not 0 <= original['time_ms'] < manifest['duration_ms']:
            raise ValueError('Slide occurrence outside audio window')
        picture = slide_out / original['slide_image']
        if file_sha(picture) != original['slide_sha256']:
            raise ValueError('Original slide picture changed')
        target = out / f'slides/{original["id"]}.png'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(picture, target)
        events.append({'id': original['id'], 'time_ms': original['time_ms'], 'capture_ms': original['capture_ms'],
                       'kind': original['kind'], 'image': str(target.relative_to(out)),
                       'image_sha256': file_sha(target), 'source_frame_number': original['source_frame_number']})
    placements = place_slides(paragraphs, events)
    flags = []
    # Only nominate review candidates. Never replace what was said with OCR or
    # a familiar Bible passage, and never call these confirmed speech errors.
    for term in ('且慕', '哥羅前書', '哥倫多強制', '那何本', 'in how', '幹。'):
        start = 0
        while (at := timed['text'].find(term, start)) >= 0:
            p = next(p for p in paragraphs if p['source_start'] <= at < p['source_end'])
            flags.append({'text': term, 'start': at, 'end': at + len(term), 'paragraph_id': p['id'],
                          'time_ms': p['start_ms'], 'status': 'possible_asr_error_requires_listening'})
            start = at + len(term)
    counts = dict(Counter(t['timing_precision'] for t in timed['tokens']))
    record = {'schema_version': 'illustrated-transcript-pilot.v1', 'source_sha256': manifest['source_sha256'],
        'audio_manifest_sha256': file_sha(out / 'audio_manifest.json'), 'asr_sha256': file_sha(out / 'asr.json'),
        'timed_text_sha256': file_sha(out / 'timed_text.json'), 'format_plan_sha256': object_sha(plan),
        'formatted_text_sha256': file_sha(out / 'formatted_text.json'),
        'slide_report_sha256': file_sha(slide_out / 'report.json'), 'paragraphs': paragraphs, 'slides': placements,
        'token_timing_counts': counts, 'review_candidates': flags, 'seam_review': timed['issues'],
        'content_characters_preserved': True, 'human_confirmed': False,
        'asr_estimated_usd': read_json(out / 'asr.json')['estimated_usd'],
        'format_estimated_usd_upper': formatted['estimated_usd_upper'],
        'limitations': ['ASR spelling and verse references are uncorrected; draft only',
            'Chinese timing prediction validity is not measured timestamp accuracy',
            'Zero, non-monotonic and foreign token timings remain unresolved; no interpolation',
            'Four overlapping chunk seams require listening review; discarded guard text is retained in the ownership ledger',
            'A picture is attached by visual time to a whole paragraph, not by inferred theological relevance',
            'Visual event onset is inherited from the slide candidate detector and may include cursor-induced timing uncertainty']}
    save_json(out / 'illustrated_transcript.json', record)
    e = html.escape
    timeline = []
    markdown = ['# S 210307 前十五分鐘圖文逐字稿試稿', '',
        '機器轉錄試稿，尚未校對。字詞、經文章節及切段接縫均可能有誤；圖片是原影片畫面，不補入口述。', '']
    for paragraph in paragraphs:
        label = clock(paragraph['start_ms']) if paragraph['start_ms'] is not None else '時間待核對'
        images = [s for s in placements if s['paragraph_id'] == paragraph['id']]
        pictures = []
        for slide in images:
            figure = (f'<figure><img loading="lazy" src="{e(slide["image"])}">'
                      f'<figcaption>原畫面 {clock(slide["time_ms"])} · 截圖 {clock(slide["capture_ms"])}</figcaption></figure>')
            if slide['kind'] == 'build':
                figure = f'<details><summary>{clock(slide["time_ms"])} 畫面內容增減</summary>{figure}</details>'
            pictures.append(figure)
            markdown += [f'原畫面 {clock(slide["time_ms"])}', f'![原影片投影片]({slide["image"]})', '']
        needs_review = any(f['paragraph_id'] == paragraph['id'] for f in flags)
        tag = ' · 有待回聽字詞' if needs_review else ''
        timeline.append(f'<section id="{paragraph["id"]}"><h2>{label}{tag}</h2>{"".join(pictures)}'
                        f'<p>{e(paragraph["text"]).replace(chr(10), "<br>")}</p></section>')
        markdown += [f'## {label}{tag}', '', paragraph['text'], '']
    (out / 'illustrated_transcript.md').write_text('\n'.join(markdown), encoding='utf-8')
    page = f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>S 210307 圖文逐字稿試稿</title><style>body{{font:19px/1.9 system-ui;max-width:1000px;margin:auto;padding:24px;color:#222;background:#f6f6f6}}
section,aside,details{{background:white;padding:16px;margin:12px 0;border-radius:8px}}h1,h2{{line-height:1.4}}h2{{font-size:21px}}img{{max-width:100%;height:auto}}figure{{margin:12px 0}}figcaption{{color:#555;font-size:15px}}summary{{cursor:pointer}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}a{{color:#165cab}}</style>
<h1>S 210307 前十五分鐘圖文逐字稿試稿</h1><aside>機器轉錄試稿，尚未校對。疑似錯字、經文章節及四處切段接縫需要回聽。
原圖不等於口述，沒有用投影片替換逐字稿文字。內容逐步增加的圖片可展開查看。</aside>
<p>{len(paragraphs)} 段文字 · {len(placements)} 次原畫面 · 中文可用字時間 {counts.get('word',0)} 個（只是預測有效率，不是準確率）。
<a href="audio.wav">原音訊</a> · <a href="illustrated_transcript.json">來源與核對清單</a> · <a href="asr.json">原始 ASR</a></p>
{''.join(timeline)}<h2>未經校對的原始拼接文字</h2><details><summary>展開原文</summary><pre>{e(timed['text'])}</pre></details></html>'''
    (out / 'index.html').write_text(page, encoding='utf-8')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--slides', type=Path, required=True)
    args = parser.parse_args()
    result = assemble(args.out, args.slides)
    print({'paragraphs': len(result['paragraphs']), 'slides': len(result['slides']),
           'timing': result['token_timing_counts'], 'review_candidates': len(result['review_candidates'])})


if __name__ == '__main__':
    main()
