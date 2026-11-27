"""One whole-lecture alpha cleanup, isolated from editor and production storage."""
import argparse,html,json,re
from pathlib import Path
from backend.pipeline import full_video_transcript as full


def prepare(source,out):
    data=full.read(source);rows=data['script'] if isinstance(data,dict) else data
    paragraphs=[{'id':'p'+r['index'],'text':r['text'].replace('~~','')} for r in rows if r.get('type')=='content']
    manifest=dict(source=str(source),source_sha256=full.sha(source),paragraph_count=len(paragraphs),
                  policy=full.CLEANUP_POLICY,audio_checked=False,production_writes=0)
    path=out/'input.json'
    if path.exists() and full.read(path)!=manifest:raise ValueError('Frozen input changed')
    full.save(path,manifest)
    target=out/'windows/w00/correction/correction_trial.json'
    payload={'corrected_paragraphs':paragraphs}
    if target.exists() and full.read(target)!=payload:raise ValueError('Frozen material changed')
    full.save(target,payload);full.save(out/'master.json',{'windows':[{'id':'w00'}]})


def run(out):
    manifest=full.read(out/'input.json')
    if full.sha(Path(manifest['source']))!=manifest['source_sha256']:raise ValueError('Source changed')
    result=full.clean_full_transcript(out)
    if full.sha(Path(manifest['source']))!=manifest['source_sha256']:raise ValueError('Source changed')
    return result


def render(out):
    manifest=full.read(out/'input.json');result=full.read(out/'editorial-full/reading_view.json')
    rows=full.read(out/'windows/w00/correction/correction_trial.json')['corrected_paragraphs']
    exclusions={}
    for e in result['exclusions']:exclusions.setdefault(e['paragraph_id'].removeprefix('w00-'),[]).append(e)
    names={'speech_restart':'口誤重起','unrelated_aside':'無關旁枝','technical_setup':'設備操作','logistics':'現場安排'}
    body=[f'<h1>完整 {manifest["paragraph_count"]} 段：保守軟刪除驗收</h1>',
      '<p>GPT 6.1 Sol 一次閱讀全篇，未收到你的手工刪除答案。僅文字試跑；稿件、網站、Claim和時間定位均未修改。這不是人工批准或正式發佈。</p>',
      f"<p>模型提出 {len(result['exclusions'])} 處有效刪除；{len(result['withheld_deletions'])} 處定位無效而保留。內部用量估算 US${result['estimated_usd_upper']:.5f}，非帳單。</p>"]
    body.append('<p>已知漏刪：本次未提出第252段「收這個園戶的，收這個什麼，」。先前小樣本曾提出此處，這次不重試模型追求命中。這是漏刪記錄，不是準確率測量。</p>')
    for i,p in enumerate(rows):
        if p['id'] not in exclusions:continue
        es=sorted(exclusions[p['id']],key=lambda e:e['start']);marked=[];cursor=0
        for e in es:marked.extend([html.escape(p['text'][cursor:e['start']]),'<del>'+html.escape(e['old'])+'</del>']);cursor=e['end']
        marked.append(html.escape(p['text'][cursor:]))
        body.append('<section><h2>第'+p['id'][1:]+'段</h2>'+''.join('<p>'+html.escape(names[e['kind']]+': '+e['reason'])+'</p>' for e in es)+'<details><summary>前段</summary><p>'+html.escape(rows[max(0,i-1)]['text'])+'</p></details><p>'+''.join(marked)+'</p><details><summary>後段</summary><p>'+html.escape(rows[min(len(rows)-1,i+1)]['text'])+'</p></details></section>')
    (out/'report.html').write_text('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>全講保守清理驗收</title><style>body{font:18px/1.8 system-ui;max-width:1050px;margin:auto;padding:24px;background:#f4f6fa;color:#1e293b}section{background:white;padding:24px;margin:24px 0;border-radius:12px}p{white-space:pre-wrap}del{color:#9a201a;background:#ffe0db}details{background:#eef2f7;padding:8px}</style>'+''.join(body))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['prepare','run','report']);p.add_argument('--out',type=Path,required=True);p.add_argument('--source',type=Path);a=p.parse_args()
    if a.stage=='prepare':prepare(a.source,a.out)
    elif a.stage=='run':run(a.out)
    else:render(a.out)
    print(json.dumps({'stage':a.stage,'production_writes':0}))
