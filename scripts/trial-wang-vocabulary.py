#!/usr/bin/env python3
"""Paired vocabulary-only trial on fixed source clips; writes only --out.

First-pass ASR and correction are separate experiments. Both correction arms get
the SAME original draft, audio and slides, not the ASR experiment's outputs.
Never sends the published reference prose to either runtime model.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import html
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.pipeline.slide_change_pilot import file_sha, read_json, save_json
from backend.pipeline.transcription_vocabulary import load_vocabulary, vocabulary_binding
from backend.pipeline.transcript_audio_pilot import read_audio, write_audio, transcribe
from backend.pipeline.transcript_audio_correction import run as correct

ROOT = Path(__file__).resolve().parents[1]


def render(result: dict, out: Path) -> None:
    from opencc import OpenCC
    converter = OpenCC('s2t')
    escape = lambda text: html.escape(converter.convert(text))
    groups = {}
    for row in result['results']:
        groups.setdefault(row['case_id'], {})[row['arm']] = row
    sections = []
    labels = {'speaker_self': '教授自稱', 'seminary_history': '神學院就學與任教',
              'speaker_students': '學員稱呼教授', 'jesus_sons': '藉耶穌基督成為兒子',
              'jesus_exorcism': '耶穌趕鬼的例子', 'seminary_students': '神學院的學生',
              'speaker_quoted': '學生引述王教授', 'jesus_predestined': '經文中的耶穌基督',
              'seminary_career': '教授的任教經歷', 'control_near_death': '對照：補藥的例子',
              'control_dictionary': '對照：字典的例子', 'control_faith': '對照：信與背叛'}
    for case_id, arms in sorted(groups.items(), key=lambda pair: pair[1]['baseline']['start_ms']):
        before, after = arms['baseline'], arms['vocabulary']
        seconds = before['start_ms'] // 1000
        stamp = f'{seconds // 60:02}:{seconds % 60:02}'
        table = ''.join('<tr><th>' + label + '</th><td>' + escape(before[key])
                        + '</td><td>' + escape(after[key]) + '</td></tr>'
                        for key, label in [('asr_text', '首次轉錄'), ('corrected_text', '原聲與投影片校正')])
        sections.append(f'<section id="{html.escape(case_id)}"><h2>{stamp} · {html.escape(labels.get(case_id, case_id))}</h2>'
            f'<audio controls preload="none" src="{case_id}/source.wav"></audio>'
            '<table><thead><tr><th>階段</th><th>原設定</th><th>加入詞彙表</th></tr></thead><tbody>' + table
            + '</tbody></table><details><summary>校正階段兩組共用的原始草稿</summary><p>'
            + escape(before['original_draft']) + '</p></details></section>')
    cost = result['asr_estimated_usd'] + result['correction_estimated_usd_upper']
    page = '''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>教授詞彙表第一版：音訊對照測試</title><style>body{font:18px/1.7 system-ui;max-width:1300px;margin:auto;padding:24px;background:#f4f5f7;color:#222}section,aside{padding:20px;background:white;margin:20px 0;border-radius:8px}table{width:100%;border-collapse:collapse;table-layout:fixed}th,td{padding:14px;border:1px solid #ddd;vertical-align:top;white-space:pre-wrap}th:first-child{width:110px}audio{width:100%;margin:12px 0}p{white-space:pre-wrap}h2{font-size:21px}@media(max-width:700px){body{padding:10px}th,td{padding:7px;font-size:15px}th:first-child{width:55px}}</style>
<h1>教授詞彙表第一版：音訊對照測試</h1><aside>
<p>12 個片段，共約 4 分 48 秒。同一音訊分別使用原設定與詞彙表。首次轉錄與校正是兩個獨立實驗：校正兩組都讀完全相同的舊草稿、音訊和投影片，並非讀上方首次轉錄的結果。</p>
<p>這篇是已人工編輯並發布的講道，用來測試已知問題。不是未編輯樣本，也不是盲測。人工發布正文未交給轉錄或校正模型。畫面統一顯示繁體字；原始輸出完整保存在 JSON。</p>
<p>可見改善：48 分附近「我從王家說明學會／管教授」改為「我從王教授那裡學會」；Asbury 的辨識和拼寫更穩定。三個耶穌片段兩組原本都正確，不能宣稱有額外改善。</p>
<p>仍有問題：「差點把我害死」兩組仍誤作蔡姓人名；48 分附近引語中的「我／他」仍不穩定；年份也仍有差異。詞彙表只改善專名，沒有證明整段語意已完全正確。三個無目標詞對照未出現額外的王教授、Asbury 或耶穌，但不代表沒有其他錯誤。</p>
<p>第一版保持可選設定，未啟用到 production，沒有改寫原有整篇試稿。</p>'''
    page += f'<p>本輪模型費用估計約 US${cost:.3f}，依現有程式費率計算，非帳單。</p>'
    page += '<p><a href="results.json">完整前後結果</a> · <a href="protocol.json">測試設定與來源紀錄</a></p></aside>'
    (out / 'index.html').write_text(page + ''.join(sections) + '</html>', encoding='utf-8')


def trial(source: Path, out: Path, profile_path: Path, cases_path: Path) -> dict:
    profile, cases = load_vocabulary(profile_path), read_json(cases_path)
    full, master = read_json(source / 'full_transcript.json'), read_json(source / 'master.json')
    if full['source_sha256'] != cases['source_sha256'] or master['source_sha256'] != cases['source_sha256']:
        raise ValueError('Cases refer to another lecture')
    if file_sha(source / 'master.wav') != master['audio_sha256']:
        raise ValueError('Source audio changed')
    protocol = {'schema_version': 'paired-vocabulary-trial.v1', 'cases': cases,
        'vocabulary_hint': vocabulary_binding(profile), 'source_audio_sha256': master['audio_sha256'],
        'source_transcript_sha256': file_sha(source / 'full_transcript.json'),
        'originals_preserved': True, 'production_sources_written': 0,
        'asr_model': 'gpt-transcribe', 'correction_model': 'gemini-3.8-flash',
        'correction_input': 'identical original pre-correction paragraphs in both arms',
        'published_reference_sent_to_models': False,
        'rounds': 'one completed call per arm and clip, plus at most one schema repair by existing correction code',
        'audio_padding_ms': 1800}
    protocol_path = out / 'protocol.json'
    if protocol_path.exists() and read_json(protocol_path) != protocol:
        raise ValueError('Trial protocol changed; do not replace saved evidence')
    save_json(protocol_path, protocol)
    samples = read_audio(source / 'master.wav')
    originals = {p['id']: p for p in full['original_paragraphs']}
    jobs = []
    for case in cases['cases']:
        p = full['paragraphs'][case['paragraph_number'] - 1]
        start, end = max(0, p['start_ms'] - 1800), min(full['duration_ms'], p['end_ms'] + 1800)
        case_root = out / case['id']
        wav = case_root / 'source.wav'
        write_audio(wav, samples[start * 16:end * 16])
        draft = {**copy.deepcopy(originals[p['id']]), 'start_ms': p['start_ms'] - start,
                 'end_ms': p['end_ms'] - start}
        joined_dir = source / 'windows' / p['window_id'] / 'joined'
        joined = read_json(joined_dir / 'illustrated_with_visual_text.json')
        local_id = p['id'].split('-', 1)[1]
        # Same associated visual hints in both arms, preserving their source IDs.
        occurrences = [s for s in joined['occurrences'] if s['paragraph_id'] == local_id]
        assets = [copy.deepcopy(a) for a in joined['assets'] if a['asset_id'] in {s['asset_id'] for s in occurrences}]
        for a in assets:
            a['original_image'] = str((joined_dir / a['original_image']).resolve())
        occurrences = [{**s, 'paragraph_id': p['id'],
            'time_ms': s['time_ms'] + originals[p['id']]['source_window_origin_sample'] // 16 - start}
            for s in occurrences]
        for arm, vocabulary in [('baseline', None), ('vocabulary', profile)]:
            folder = case_root / arm
            audio = folder / 'audio'
            audio.mkdir(parents=True, exist_ok=True)
            write_audio(audio / 'audio.wav', samples[start * 16:end * 16])
            chunk = {'id': 'clip', 'audio': 'audio.wav', 'audio_sha256': file_sha(wav),
                'sample_start': 0, 'sample_end': (end-start)*16, 'start_ms': 0, 'end_ms': end-start}
            save_json(audio / 'audio_manifest.json', {'source_sha256': full['source_sha256'],
                'duration_ms': end-start, 'sample_rate': 16000, 'audio_sha256': file_sha(wav),
                'source_clip_start_ms': start, 'source_clip_end_ms': end, 'chunks': [chunk]})
            save_json(audio / 'illustrated_transcript.json', {'paragraphs': [draft]})
            save_json(folder / 'joined/illustrated_with_visual_text.json', {
                'source_sha256': full['source_sha256'], 'speech_artifact_sha256': file_sha(audio / 'illustrated_transcript.json'),
                'speaker_identity_hint': profile['speaker'], 'paragraphs': [draft], 'assets': assets, 'occurrences': occurrences})
            jobs.append((case, arm, vocabulary, folder, start, end))

    def perform(job):
        case, arm, vocabulary, folder, start, end = job
        asr = transcribe(folder / 'audio', vocabulary=vocabulary)
        correction = correct(folder / 'joined', folder / 'audio', folder / 'correction', vocabulary=vocabulary)
        row = {'case_id': case['id'], 'arm': arm, 'target_groups': case['target_groups'],
               'start_ms': start, 'end_ms': end, 'audio_sha256': file_sha(folder / 'audio/audio.wav'),
               'asr_text': asr['chunks'][0]['text'],
               'original_draft': correction['original_paragraphs'][0]['text'],
               'corrected_text': correction['corrected_paragraphs'][0]['text'],
               'decisions': correction['decisions'],
               'asr_estimated_usd': asr['estimated_usd'],
               'correction_estimated_usd_upper': correction['proposal_estimated_usd_upper'],
               'asr_sha256': file_sha(folder / 'audio/asr.json'),
               'correction_sha256': file_sha(folder / 'correction/correction_trial.json')}
        save_json(folder / 'result.json', row)
        print(case['id'], arm, 'complete', flush=True)
        return row

    # Independent clips/arms have separate durable call ledgers. No retry of an
    # unresolved call; saved_call keeps outcome-unknown states fail-closed.
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(perform, jobs))
    result = {'protocol_sha256': file_sha(protocol_path), 'results': results,
              'asr_estimated_usd': sum(r['asr_estimated_usd'] for r in results),
              'correction_estimated_usd_upper': sum(r['correction_estimated_usd_upper'] for r in results)}
    save_json(out / 'results.json', result)
    render(result, out)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--vocabulary', type=Path, default=ROOT / 'backend/pipeline/policies/wang_transcription_vocabulary_v1.json')
    parser.add_argument('--cases', type=Path, default=ROOT / 'backend/tests/fixtures/wang_vocab_regression_cases_v1.json')
    parser.add_argument('--render-only', action='store_true')
    args = parser.parse_args()
    if args.render_only:
        render(read_json(args.out / 'results.json'), args.out)
    else:
        trial(args.source, args.out, args.vocabulary, args.cases)
