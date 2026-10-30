"""Source-bound editorial slide grouping/placement; never changes speech or evidence."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

from backend.pipeline.slide_change_pilot import file_sha, object_sha, read_json, save_json

PROMPT = '''你是講道閱讀版的投影片編輯。只提出圖文排版，不改寫、補寫或刪除口述。
輸入為同一篇完整講道：依序列出的真實截圖（順序與 slides 相同）、OCR、所有口述段落。
請將同一頁逐步顯示的連續狀態合成一組，選擇清晰完整的真實截圖為代表，避免展示半句或半張圖。
不要單靠 kind、字數或標題分組；必須看截圖、OCR及講述。不同經文頁不要合併。離開後返回同一頁仍另成一組。
若同一頁的變化本身承載問題→推論→結論或重要對比，可在同組選2至3個代表狀態（按時間順序），各自放在相關講解開始前。
每組所有歷史狀態都將保留為可展開的逐步講解；普通逐行顯示只選1張完整圖。不可生成或拼接圖片。
每張代表圖選 before_paragraph_id：實質開始解釋該圖內容的段落之前，優先語意，不要僅因時間重疊。
候選段落必須距該組第一個畫面在影片中的時間不超過120秒（按段落時間區間距離計算）；多代表圖可以使用各自出現時間前後120秒。
維持全篇閱讀順序，不能把後來回顧誤移到前次講解。若語意不確定，confidence=uncertain，使用原時間對應段落；沒有口述對應可選null。
用繁體中文簡短說明分組與定位理由；理由只是編輯註記，不是教授原話。
返回純JSON：{"groups":[{"member_ids":["c-..."],"reason":"...","representatives":[{"slide_id":"c-...","before_paragraph_id":"w...或null","confidence":"high或uncertain","reason":"..."}]}]}。
所有輸入 slide ID 必須恰好出現一次於 member_ids，依原順序連續分組，不能漏掉任何畫面；代表ID只能選本組成員。不要回傳段落文字。'''


def distance(p, ms):
    if p.get('start_ms') is None or p.get('end_ms') is None:
        return float('inf')
    return max(p['start_ms'] - ms, ms - p['end_ms'], 0)


def compile_layout(record: dict, proposal: dict) -> dict:
    """Reject unsupported grouping; conservatively fall back on doubtful placements."""
    slides = record['slides']; by_id = {s['id']: s for s in slides}
    paragraphs = {p['id']: p for p in record['paragraphs']}
    order = {p['id']: i for i, p in enumerate(record['paragraphs'])}
    groups = proposal.get('groups')
    if not isinstance(groups, list) or (slides and not groups) or any(not g.get('member_ids') for g in groups):
        raise ValueError('No slide groups')
    flattened = [sid for g in groups for sid in g['member_ids']]
    if flattened != [s['id'] for s in slides]:
        raise ValueError('Groups must cover every source occurrence exactly once in order')
    result = []; last_anchor = -1
    for i, g in enumerate(groups):
        members = g['member_ids']; reps = g['representatives']
        if not isinstance(g['reason'], str) or not g['reason'].strip():
            raise ValueError('Missing grouping reason')
        if not 1 <= len(reps) <= 3:
            raise ValueError('One to three real representatives required')
        indices = [members.index(r['slide_id']) if r['slide_id'] in members else -1 for r in reps]
        if -1 in indices or indices != sorted(set(indices)):
            raise ValueError('Representatives must be distinct ordered group members')
        first_ms = by_id[members[0]]['capture_ms']; last_ms = by_id[members[-1]]['capture_ms']
        if any(span['state'] != 'visible' and span['start_ms'] < last_ms and span['end_ms'] > first_ms
               for span in record.get('visibility', {}).get('spans', [])):
            raise ValueError('Cannot group across a no-slide or unknown interval')
        if any(by_id[sid].get('kind') == 'return' for sid in members[1:]):
            raise ValueError('A slide return must begin a separate group')
        placements = []
        for j, rep in enumerate(reps):
            if rep['confidence'] not in ('high', 'uncertain') or not isinstance(rep['reason'], str):
                raise ValueError('Invalid placement evidence')
            sid = rep['slide_id']; candidate = rep['before_paragraph_id']
            if candidate is not None and candidate not in paragraphs:
                raise ValueError('Unknown paragraph ID')
            event = by_id[sid]
            scope_ms = by_id[members[0]]['time_ms'] if len(reps) == 1 else event['time_ms']
            fallback = by_id[members[0] if j == 0 else sid].get('paragraph_id')
            invalid = (rep['confidence'] == 'uncertain' or candidate is None or
                       distance(paragraphs[candidate], scope_ms) > 120000 or
                       order[candidate] < last_anchor)
            anchor = fallback if invalid else candidate
            # Never reorder argument episodes merely to satisfy a model's placement.
            if anchor is not None and (anchor not in order or order[anchor] < last_anchor):
                anchor = None
            if anchor is not None:
                last_anchor = order[anchor]
            placements.append({**rep, 'before_paragraph_id': anchor,
                               'proposed_before_paragraph_id': candidate,
                               'placement_method': 'temporal_fallback' if invalid else 'semantic_candidate',
                               'needs_review': invalid,
                               'source_time_ms': event['time_ms'], 'capture_ms': event['capture_ms']})
        result.append({'id': f'g{i:03d}', 'member_ids': members, 'reason': g['reason'],
                       'source_occurrences': [by_id[s] for s in members], 'representatives': placements})
    return {'schema_version': 'slide-reading-layout.v1', 'attribution': 'editorial_layout_candidate',
            'source_record_sha256': object_sha(record),
            'spoken_paragraphs_sha256': object_sha(record['paragraphs']),
            'source_video_sha256': record['source_sha256'],
            'source_visibility': record.get('visibility'), 'groups': result}


def infer(record: dict, out: Path) -> dict:
    from backend.pipeline.slide_pilot_models import VisionReviewer
    from backend.pipeline.transcript_audio_pilot import saved_call
    if not record['slides']:
        layout = compile_layout(record, {'groups': []})
        save_json(out / 'layout.json', layout)
        return layout
    assets = {a['asset_id']: a for a in record['visual_assets']}
    images = [Path(assets['a' + s['slide_sha256']]['original_image']) for s in record['slides']]
    for s, image in zip(record['slides'], images):
        if file_sha(image) != s['slide_sha256']:
            raise ValueError('Source slide image changed')
    packet = {'slides': [{**{k: s.get(k) for k in ('id', 'time_ms', 'kind', 'label', 'paragraph_id')},
                          'ocr': assets['a' + s['slide_sha256']]['visible_text']} for s in record['slides']],
              'paragraphs': [{k: p[k] for k in ('id', 'start_ms', 'end_ms', 'text')} for p in record['paragraphs']]}
    prompt = PROMPT + '\nSOURCE:\n' + json.dumps(packet, ensure_ascii=False)
    reviewer = VisionReviewer('gemini', prompt, {})
    reviewer.config.pop('response_json_schema')
    reviewer.config.update(max_output_tokens=24576, thinking_config={'thinking_level': 'LOW'})
    parts = reviewer.contents([s['id'] for s in record['slides']], images)
    request = {'stage': 'slide-reading-layout-v1', 'source_record_sha256': object_sha(record),
               'prompt_sha256': object_sha(prompt), 'model': reviewer.model, 'config': reviewer.config,
               'images_sha256': [file_sha(p) for p in images]}
    raw = saved_call(out, request, 1.0, lambda: reviewer.generate(parts), max_calls=1, budget_usd=1)
    proposal = json.loads(reviewer.text(raw))
    layout = compile_layout(record, proposal)
    layout['model_call'] = {'request_sha256': object_sha(request), 'response_sha256': object_sha(raw),
                            'model': reviewer.model, 'usage': reviewer.usage(raw)}
    save_json(out / 'proposal.json', proposal)
    save_json(out / 'layout.json', layout)
    return layout


def render(record: dict, layout: dict, out: Path):
    """Write a child view under an existing full-video preview root."""
    if layout['source_record_sha256'] != object_sha(record):
        raise ValueError('Layout belongs to a different source record')
    assets = {a['asset_id']: a for a in record['visual_assets']}
    occurrences = {s['id']: s for s in record['slides']}
    esc = lambda x: html.escape(str(x), quote=True)
    clock = lambda ms: f'{int(ms)//60000:02d}:{int(ms)//1000%60:02d}'
    def image(sid):
        s = occurrences[sid]; a = assets['a'+s['slide_sha256']]
        svg_link = (f'<a href="../pictures/{esc(a["asset_id"])}.svg">SVG 轉繪稿</a>' if a.get('svg') else '')
        return (svg_link + f'<img loading="lazy" alt="原投影片 {esc(sid)}" src="../pictures/{esc(a["asset_id"])}.png">'
                f'<details><summary>投影片文字（非口述）</summary><pre>{esc(a["visible_text"])}</pre></details>')
    def card(g, rep, history):
        sid = rep['slide_id']; ms = rep['source_time_ms']
        status = '位置待確認，暫依影片時間' if rep['needs_review'] else '依內容放在本段之前（編輯排版）'
        text = (f'<figure id="slide-{esc(sid)}" data-slide="{esc(sid)}" data-group="{g["id"]}">{image(sid)}'
                f'<figcaption>原畫面 {clock(ms)} · {status} '
                f'<button class="seek" data-time="{ms/1000}">觀看原片</button></figcaption>')
        if history:
            text += f'<details class="history"><summary>查看逐步講解（{len(g["member_ids"])} 個畫面）</summary>'
            for member in g['member_ids']:
                sm = occurrences[member]['time_ms']
                text += f'<div data-occurrence="{esc(member)}"><h4>{clock(sm)}</h4>{image(member)}<button class="seek" data-time="{sm/1000}">播放此時</button></div>'
            text += '</details>'
        text += f'<details><summary>排版依據</summary><p>{esc(g["reason"])}</p><p>{esc(rep["reason"])}</p></details></figure>'
        if rep['needs_review']:
            text = f'<details class="uncertain"><summary>關聯待確認的畫面 · {clock(ms)}</summary>{text}</details>'
        return text
    placement = {}; orphans = []
    for g in layout['groups']:
        for j, rep in enumerate(g['representatives']):
            target = rep['before_paragraph_id']; item = card(g, rep, j == 0)
            if target is None: orphans.append(item)
            else: placement.setdefault(target, []).append(item)
    body = []
    for p in record['paragraphs']:
        body.extend(placement.get(p['id'], []))
        ms = p['start_ms']
        button = '' if ms is None else f'<button class="seek" data-time="{ms/1000}">播放本段</button>'
        body.append(f'<section id="{esc(p["id"])}"><small>{clock(ms) if ms is not None else "時間未定"}</small> {button}<p class="speech">{esc(p["text"])}</p></section>')
    if orphans:
        body.append('<aside><h2>未對應口述的畫面</h2>' + ''.join(orphans) + '</aside>')
    page = '''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>投影片閱讀版試驗</title><style>body{font:19px/1.85 system-ui;max-width:920px;margin:auto;padding:20px;background:#f5f6f8;color:#222}figure,section,aside{margin:20px 0;padding:22px;background:white;border-radius:12px}figure{border-left:4px solid #536d9a}img{display:block;max-width:100%;height:auto;margin:auto}video{width:100%;max-height:280px}button{cursor:pointer;padding:6px 12px;font:inherit}pre,p{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}details{background:#f1f4f8;padding:10px;margin-top:10px}summary{cursor:pointer}figcaption,small{font-size:14px;color:#555}</style><h1>投影片閱讀版試驗</h1>'''
    count = sum(len(g['representatives']) for g in layout['groups'])
    page += f'<aside>{len(record["slides"])} 個原畫面 → {len(layout["groups"])} 組、{count} 張閱讀用代表圖（關聯待確認者收起）。原口述 {len(record["paragraphs"])} 段完整保留。<p>代表圖可能取自稍後才完成的畫面；此處依講述內容安排閱讀位置，並非影片當時已顯示完整內容。逐步講解保留每個原畫面與時間。機器排版試稿。</p><a href="../">比較原時間版</a> · <a href="layout.json">排版紀錄</a> · <a href="../full_transcript.json">原始完整紀錄</a><video id="movie" controls preload="metadata" src="../source.mp4"></video></aside>'
    page += ''.join(body) + '''<script>document.querySelectorAll('.seek').forEach(b=>b.onclick=()=>{const v=document.getElementById('movie');v.currentTime=Number(b.dataset.time);v.play();v.scrollIntoView({behavior:'smooth',block:'center'})})</script></html>'''
    (out / 'index.html').write_text(page, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True, help='Child directory under the existing source preview root')
    args = parser.parse_args()
    if args.out.resolve().parent != args.source.resolve().parent:
        parser.error('--out must be a child of the source preview directory')
    args.out.mkdir(parents=True, exist_ok=True)
    record = read_json(args.source)
    layout = infer(record, args.out)
    render(record, layout, args.out)
    print(json.dumps({'groups': len(layout['groups']), 'representatives': sum(len(g['representatives']) for g in layout['groups']), 'usage': layout.get('model_call', {}).get('usage')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
