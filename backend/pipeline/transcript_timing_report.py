"""Read-only, source-bound operator report for withheld transcript timing.

This is a diagnostic artifact, never an editor draft or publication certificate.
"""
from pathlib import Path
import html
import os
import shutil
from backend.pipeline.slide_change_pilot import read_json,save_json,file_sha


def report_binding(out:Path,batch:str,recording:str):
    """Bind remote viewing to these exact saved diagnostics and playback bytes."""
    from urllib.parse import quote
    folder=out/'timing-review';report=read_json(folder/'report.json')
    receipt=read_json(out/'media-receipt.json')
    if report['source_sha256']!=receipt['source_sha256']:raise ValueError('Report source differs from playback')
    media='playback.mp3' if receipt['mime']=='audio/mpeg' else 'playback.mp4'
    if file_sha(folder/media)!=receipt['sha256']:raise ValueError('Report playback changed')
    return dict(timing_report_href=f'/api/sc_api/media_batches/{quote(batch,safe="")}/timing/{quote(recording,safe="")}',
        withheld=report['withheld'],qualified=report['qualified'],
        timing_report=dict(json_sha256=file_sha(folder/'report.json'),html_sha256=file_sha(folder/'report.html'),
            media_file=media,media_sha256=receipt['sha256'],mime=receipt['mime'],source_sha256=report['source_sha256']))


def write_report(out:Path,record:dict,evidence:Path,*,playback=None):
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
    rows=[];readiness={};playback_cases={}
    if playback is not None:
        if playback['source_plan_sha256']!=file_sha(evidence/'plan.json'):raise ValueError('Playback report source changed')
        readiness={r['id']:r for rs in playback['paragraphs'].values() for r in rs}
        plan=read_json(evidence/'playback-readiness/plan.json')
        for i,case in enumerate(plan['cases']):
            if case['id']=='control':continue
            value=read_json(evidence/f'playback-readiness/reviews/c{i:04d}/validated.json')
            judgments={j['id']:j for j in value['answer']['candidates']}
            playback_cases[case['paragraph_id']]=[{**c,'judgment':judgments.get(c['id'])} for c in case['candidates']]
    for lecture in assessment['lectures'].values():
        for row in lecture['paragraphs']:
            if row['status']=='qualified' and readiness.get(row['id'],{}).get('status')!='blocked':continue
            number,p=by_id[row['id']];w=windows.get(p.get('window_id',row['id'].split('-')[0]));context=None
            if w:
                # A physical source context is not a claimed paragraph boundary.
                context=dict(kind='whole_source_window',start_ms=w['start_ms'],end_ms=w['end_ms'])
                try:
                    from backend.pipeline.full_video_transcript import original_paragraph_clip
                    start,end,proof=original_paragraph_clip(out/'windows'/w['id'],{'id':row['id'].split('-',1)[1]},w,max_seconds=None)
                    context=dict(kind='original_asr_chunk_context',start_ms=start//16,end_ms=end//16,proof=proof)
                except (ValueError,FileNotFoundError,KeyError,StopIteration):pass
            active=readiness.get(row['id'])
            rows.append(dict(number=number,id=row['id'],text=p['text'],status=active['status'] if active else 'withheld',strict_status=row['status'],
                issue=row.get('issue','unqualified_acoustic_boundary'),source_context=context,
                candidate_start_ms=active.get('start_ms') if active else row.get('start_ms',p.get('start_ms')),candidate_end_ms=active.get('end_ms') if active else row.get('end_ms',p.get('end_ms')),
                playback_readiness=active,playback_candidates=playback_cases.get(row['id'],[]),
                reviews=notes.get(row['id'],[])))
    report=dict(schema_version='transcript-timing-review-report.v1',source_sha256=record['source_sha256'],
        assessment_sha256=file_sha(evidence/'assessment.json'),total=len(record['paragraphs']),
        qualified=playback['ready'] if playback else len(record['paragraphs'])-len(rows),withheld=playback['blocked'] if playback else len(rows),paragraphs=rows,
        editor_imported=False,human_approved=False,model_calls=0)
    if playback is not None:
        report.update(playback_policy=playback['policy'],context_warnings=playback['warnings'],
            playback_assessment_sha256=file_sha(evidence/'playback-readiness/assessment.json'),strict_qualified=assessment['lectures']['source']['qualified'])
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
    comparison=[]
    for r in rows:
        context=r['source_context'];button=''
        if media and context:
            button=f'<button data-start="{context["start_ms"]/1000}" data-end="{context["end_ms"]/1000}">播放來源範圍 {clock(context["start_ms"])}–{clock(context["end_ms"])}</button>'
        start,end=r['candidate_start_ms'],r['candidate_end_ms']
        ready=r['status'] in ('ready','ready_with_context')
        label='可用・含少量上下文提醒' if r['status']=='ready_with_context' else '可用' if ready else '仍阻塞・實質問題待處理'
        if media and isinstance(start,int) and isinstance(end,int) and 0<=start<end:
            play_label='播放本段' if ready else '聽定位候選（尚未核准）'
            button=f'<button data-start="{start/1000}" data-end="{end/1000}">{play_label} {clock(start)}–{clock(end)}</button> '+button
        reasons=''.join(f'<li>{e(v["reason"])}</li>' for v in r['reviews']) or '<li>相鄰段落的時間仍重疊，尚無可靠的切分點。</li>'
        summary='';options='';active=r['playback_readiness']
        if active:
            reason=active.get('program_selection_note') or active['reason']
            # Display actual ranges instead of internal candidate identifiers.
            import re
            mapping={c['id']:f'範圍 {clock(c["start_ms"])}–{clock(c["end_ms"])}' for c in r['playback_candidates']}
            reason=re.sub(r'(?<![A-Za-z0-9_])c\d+(?![A-Za-z0-9_])',lambda m:mapping.get(m.group(),m.group()),reason)
            summary=f'<p class="{"ok" if ready else "hold"}">{label}</p><p>{e(reason)}</p>'
            comparison.append(f'<tr><td><a href="#p-{r["number"]}">第 {r["number"]} 段</a></td><td>嚴格邊界未通過</td><td>{label}</td></tr>')
            parts=[]
            names={'complete':'內容完整','missing_content':'有漏句或截詞','wrong_occurrence':'位置不對','text_audio_mismatch':'文字與錄音不符','unclear':'尚不確定'}
            for c in r['playback_candidates']:
                j=c.get('judgment');verdict=names.get(j['verdict'],'未回覆') if j else '未回覆'
                parts.append(f'<p><button data-start="{c["start_ms"]/1000}" data-end="{c["end_ms"]/1000}">播放 {clock(c["start_ms"])}–{clock(c["end_ms"])}</button> {verdict}</p>')
            if parts:options='<details><summary>比較其他錄音範圍</summary>'+''.join(parts)+'</details>'
        history_title='原嚴格標準的核驗記錄（保留追溯）' if playback else '核驗記錄（仍待解決）'
        cards.append(f'<article id="p-{r["number"]}"><h2>第 {r["number"]} 段 <small>{e(r["id"])}</small></h2>{summary}<p>{e(r["text"])}</p>{button}{options}<details {"" if playback else "open"}><summary>{history_title}</summary><ul>{reasons}</ul></details></article>')
    player=f'<audio id="player" controls preload="metadata" src="{media}?sha256={media_sha}"></audio>' if media else ''
    policy_note=f'<p>新標準：整段內容完整、位置正確即可校對；少量前後文只作提醒。原嚴格邊界結果保留，沒有改成通過。上下文提醒共 {report["context_warnings"]} 段。</p><table><thead><tr><th>段落</th><th>原結果</th><th>新標準結果</th></tr></thead><tbody>{"".join(comparison)}</tbody></table>' if playback else ''
    page=f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>羅馬書第一講・時間核驗結果</title>
<style>body{{font:17px/1.7 system-ui,sans-serif;max-width:1040px;margin:32px auto;padding:0 20px;color:#172033;background:#f5f7fb}}header,article{{padding:24px;background:white;border:1px solid #dde3ec;border-radius:14px;margin:18px 0}}h1{{margin:0}}h2{{font-size:21px}}small{{font-size:13px;color:#64748b}}button{{padding:10px 15px;border:1px solid #b9c9df;border-radius:8px;background:#eef4ff;color:#1648a5;cursor:pointer}}audio{{width:100%}}p{{white-space:pre-wrap}}li{{margin:10px 0}}.hold{{color:#995318;font-weight:600}}.ok{{color:#17663a;font-weight:600}}table{{width:100%;border-collapse:collapse;margin:18px 0;font-size:15px}}td,th{{text-align:left;border-bottom:1px solid #dde3ec;padding:9px 6px}}article{{scroll-margin-top:12px}}</style>
<header><h1>羅馬書第一講・時間核驗結果</h1><p>2014-04-18 · 羅馬書 1:1–4</p><p><strong>{report['qualified']}/{report['total']} 段{'可用於校對' if playback else '通過'}</strong> · <span class="hold">{report['withheld']} 段{'仍阻塞' if playback else '尚未通過'}</span></p><p>這是問題檢視頁。尚未建立 Editor 草稿，未發佈。下方播放器播放的是原始錄音的來源範圍，並不表示段落起訖已正確。</p>{policy_note}{player}</header>{''.join(cards)}
<script>const p=document.getElementById('player');let stop=null;document.querySelectorAll('button[data-start]').forEach(b=>b.onclick=()=>{{p.currentTime=Number(b.dataset.start);stop=Number(b.dataset.end);p.play()}});if(p)p.addEventListener('timeupdate',()=>{{if(stop!==null&&p.currentTime>=stop){{p.pause();stop=null}}}});</script></html>'''
    (folder/'report.html').write_text(page)
    return folder/'report.json'
