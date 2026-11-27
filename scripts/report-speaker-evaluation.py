#!/usr/bin/env python3
"""Render a read-only speaker experiment from saved candidates and held-out labels.

No model calls; no production configuration, manuscripts or review state writes.
"""
import argparse
import html
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.pipeline.transcript_speaker_evaluation import compare
from backend.pipeline.slide_change_pilot import save_json


def render(root: Path) -> dict:
    plan = json.loads((root / 'plan.json').read_text())
    reference = json.loads((root / 'review-snapshot.json').read_text())
    by_index = {str(row['index']): row['text'] for row in reference}
    rows, sections = [], []
    names = {'professor': '教授', 'audience': '聽眾', 'unknown': '說話者未明'}
    for case in plan['cases']:
        number = case['paragraph']
        folder = f'p{number:04d}'
        path = root / folder / 'candidate.json'
        if not path.exists():
            rows.append({'paragraph': number, 'status': 'stopped_or_incomplete'})
            continue
        candidate = json.loads(path.read_text())
        evaluation = compare(by_index[str(number)], candidate['turns'])
        row = {'paragraph': number, 'status': 'candidate', **evaluation,
               'roles': [t['role'] for t in candidate['turns']]}
        rows.append(row)
        if not evaluation['explicit_reference_characters']:
            label = '無人工說話者標記：僅供檢查自問自答／引述，不計入準確率'
        elif evaluation['mismatches']:
            label = f"與人工標記有分歧：{len(evaluation['mismatches'])} 個對齊字元，待核實"
        elif evaluation['reference_alignment_coverage'] == 1:
            label = '人工明確標記的文字全部對齊，說話者一致'
        else:
            label = '已對齊的人工標記文字說話者一致；有未對齊文字，不能宣稱整段通過'
        turns = ''.join(
            f'<p><b>{names[t["role"]]}：</b>{html.escape(t["text"])} '
            f'<small>（原片約 {t["source_start_s"]:.1f}–{t["source_end_s"]:.1f} 秒）</small></p>'
            for t in candidate['turns'])
        sections.append(f'<section><h2>第 {number} 段</h2><p>{label}</p>'
                        f'<audio controls preload="none" src="{folder}/context.wav"></audio>'
                        f'<small>播放器 00:00 對應原片 {case["start"]} 秒。換人時間是模型估計。</small>'
                        f'{turns}<details><summary>核對用人工稿（沒有提供給模型）</summary>'
                        f'<pre>{html.escape(by_index[str(number)])}</pre></details></section>')
    scored = [r for r in rows if r.get('matched_reference_characters', 0)]
    total = sum(r['matched_reference_characters'] for r in scored)
    summary = {'planned_cases': len(rows), 'completed_cases': sum(r['status']=='candidate' for r in rows),
               'explicitly_labelled_cases': len(scored),
               'cases_with_role_disagreement': [r['paragraph'] for r in scored if r['mismatches']],
               'matched_explicit_reference_characters': total,
               'correctly_attributed_matched_characters': sum(r['correctly_attributed_matched_characters'] for r in scored),
               'scope': 'sampled_paragraphs_explicit_labels_only_not_whole_lecture_accuracy',
               'unlabelled_controls_not_human_verified': True,
               'production_writes': 0, 'cases': rows}
    save_json(root/'evaluation.json', summary)
    page = '''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>第1講說話者辨識測試</title>
<style>body{max-width:900px;margin:30px auto;padding:20px;font:19px/1.8 system-ui;color:#172337;background:#f6f8fb}section{padding:24px;background:white;border:1px solid #dbe2ed;border-radius:12px;margin:24px 0}audio{width:100%}small{color:#637088}pre{white-space:pre-wrap;font:inherit}</style>
<h1>2018 NYSC 第1講：說話者辨識測試</h1>
<p>以原聲和未標記的原機器稿測試；人工標記僅用於事後核對。模型候選尚未人工核實。所有原文字句保留，網站校對稿未改動。</p>
<p>此為抽樣測試，已標記案例集中在前21分鐘。未標記不代表只有教授說話，不可據此計算整篇準確率、漏檢率或宣稱後半篇正確。聲音重疊及短促接話仍需核實。換人時間不是精確音訊對齊。</p>'''
    page += f'<p>完成 {summary["completed_cases"]}/{summary["planned_cases"]} 段；有明確人工說話者標記 {len(scored)} 段；與標記分歧的段落：{summary["cases_with_role_disagreement"]}。</p>'
    (root/'index.html').write_text(page + ''.join(sections) + '</html>')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    result = render(args.root)
    print(json.dumps({k:v for k,v in result.items() if k != 'cases'}, ensure_ascii=False, indent=2))
