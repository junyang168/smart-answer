"""Read-only, source-bound operator report for withheld transcript timing.

This is a diagnostic artifact, never an editor draft or publication certificate.
"""
from pathlib import Path
import html
import os
import shutil
from backend.pipeline.slide_change_pilot import read_json,save_json,file_sha


def write_report(out:Path,record:dict,evidence:Path):
    assessment=read_json(evidence/'assessment.json');master=read_json(out/'master.json')
    windows={w['id']:w for w in master['windows']};folder=out/'timing-review';folder.mkdir(exist_ok=True)
    by_id={p['id']:(i,p) for i,p in enumerate(record['paragraphs'],1)};notes={}
    for name,subdir in [('paragraph-coherence-plan.json','coherence-calls'),('coherence-observation-recovery-plan.json','coherence-recovery-calls')]:
        path=evidence/name
        if not path.exists():continue
        for i,case in enumerate(read_json(path)['cases']):
            receipt=evidence/subdir/f'c{i:04d}/validated.json'
            if receipt.exists():
                value=read_json(receipt)
                notes.setdefault(case['paragraph_id'],[]).append(dict(reason=value['answer']['reason'],
                    eligible=value['eligible'],receipt_sha256=file_sha(receipt),path=str(receipt.relative_to(out))))
    rows=[]
    for lecture in assessment['lectures'].values():
        for row in lecture['paragraphs']:
            if row['status']=='qualified':continue
            number,p=by_id[row['id']];w=windows.get(p.get('window_id',row['id'].split('-')[0]));context=None
            if w:
                # A physical source context is not a claimed paragraph boundary.
                context=dict(kind='whole_source_window',start_ms=w['start_ms'],end_ms=w['end_ms'])
                try:
                    from backend.pipeline.full_video_transcript import original_paragraph_clip
                    start,end,proof=original_paragraph_clip(out/'windows'/w['id'],{'id':row['id'].split('-',1)[1]},w,max_seconds=None)
                    context=dict(kind='original_asr_chunk_context',start_ms=start//16,end_ms=end//16,proof=proof)
                except (ValueError,FileNotFoundError,KeyError,StopIteration):pass
            rows.append(dict(number=number,id=row['id'],text=p['text'],status='withheld',
                issue=row.get('issue','unqualified_acoustic_boundary'),source_context=context,
                candidate_start_ms=row.get('start_ms',p.get('start_ms')),candidate_end_ms=row.get('end_ms',p.get('end_ms')),
                reviews=notes.get(row['id'],[])))
    report=dict(schema_version='transcript-timing-review-report.v1',source_sha256=record['source_sha256'],
        assessment_sha256=file_sha(evidence/'assessment.json'),total=len(record['paragraphs']),
        qualified=len(record['paragraphs'])-len(rows),withheld=len(rows),paragraphs=rows,
        editor_imported=False,human_approved=False,model_calls=0)
    save_json(folder/'report.json',report)
    receipt=out/'media-receipt.json';media=None;media_sha=None
    if receipt.exists():
        r=read_json(receipt);source=Path(r['path'])
        if r['source_sha256']!=record['source_sha256'] or file_sha(source)!=r['sha256']:raise ValueError('Diagnostic playback source changed')
        media='playback.mp3' if r['mime']=='audio/mpeg' else 'playback.mp4'
        media_sha=r['sha256']
        dest=folder/media
        if dest.exists():
            if file_sha(dest)!=r['sha256']:raise ValueError('Diagnostic playback changed')
        else:
            try:os.link(source,dest)
            except OSError:shutil.copyfile(source,dest)
    e=html.escape
    def clock(ms):
        s=ms//1000;return f'{s//3600:02}:{s//60%60:02}:{s%60:02}'
    cards=[]
    for r in rows:
        context=r['source_context'];button=''
        if media and context:
            button=f'<button data-start="{context["start_ms"]/1000}" data-end="{context["end_ms"]/1000}">播放來源範圍 {clock(context["start_ms"])}–{clock(context["end_ms"])}</button>'
        start,end=r['candidate_start_ms'],r['candidate_end_ms']
        if media and isinstance(start,int) and isinstance(end,int) and 0<=start<end:
            button=f'<button data-start="{start/1000}" data-end="{end/1000}">聽定位候選（尚未核准）</button> '+button
        reasons=''.join(f'<li>{e(v["reason"])}</li>' for v in r['reviews']) or '<li>相鄰段落的時間仍重疊，尚無可靠的切分點。</li>'
        cards.append(f'<article><h2>第 {r["number"]} 段 <small>{e(r["id"])}</small></h2><p>{e(r["text"])}</p><details open><summary>核驗記錄（仍待解決）</summary><ul>{reasons}</ul></details>{button}</article>')
    player=f'<audio id="player" controls preload="metadata" src="{media}?sha256={media_sha}"></audio>' if media else ''
    page=f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>羅馬書第一講・時間核驗結果</title>
<style>body{{font:17px/1.7 system-ui,sans-serif;max-width:1040px;margin:32px auto;padding:0 20px;color:#172033;background:#f5f7fb}}header,article{{padding:24px;background:white;border:1px solid #dde3ec;border-radius:14px;margin:18px 0}}h1{{margin:0}}h2{{font-size:21px}}small{{font-size:13px;color:#64748b}}button{{padding:10px 15px;border:1px solid #b9c9df;border-radius:8px;background:#eef4ff;color:#1648a5;cursor:pointer}}audio{{width:100%}}p{{white-space:pre-wrap}}li{{margin:10px 0}}.hold{{color:#995318;font-weight:600}}</style>
<header><h1>羅馬書第一講・時間核驗結果</h1><p>2014-04-18 · 羅馬書 1:1–4</p><p><strong>{report['qualified']}/{report['total']} 段通過</strong> · <span class="hold">{report['withheld']} 段尚未通過</span></p><p>這是問題檢視頁。尚未建立 Editor 草稿，未發佈。下方播放器播放的是原始錄音的來源範圍，並不表示段落起訖已正確。</p>{player}</header>{''.join(cards)}
<script>const p=document.getElementById('player');let stop=null;document.querySelectorAll('button[data-start]').forEach(b=>b.onclick=()=>{{p.currentTime=Number(b.dataset.start);stop=Number(b.dataset.end);p.play()}});if(p)p.addEventListener('timeupdate',()=>{{if(stop!==null&&p.currentTime>=stop){{p.pause();stop=null}}}});</script></html>'''
    (folder/'report.html').write_text(page)
    return folder/'report.json'
