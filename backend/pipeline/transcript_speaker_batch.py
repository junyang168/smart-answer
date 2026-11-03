"""Full-audio speaker candidates; insertion-only labels for untouched pending text.

The caller supplies an immutable review-document snapshot. This module has no
production write path. Speaker labels are candidates, never review decisions.
"""
from __future__ import annotations
import argparse
import concurrent.futures
import json
import re
from pathlib import Path

from backend.pipeline.slide_change_pilot import file_sha, read_json, save_json
from backend.pipeline.slide_pilot_models import VisionReviewer
from backend.pipeline.transcript_audio_pilot import read_audio, write_audio, saved_call
from backend.pipeline.transcript_speaker_pilot import PROMPT, SCHEMA, validate

BATCH_PROMPT = PROMPT.replace('TARGET\ntext', 'each TARGET paragraph').replace(
    'Return ordered turns that partition TARGET EXACTLY',
    'For each supplied paragraph ID, return ordered turns that partition its TARGET text EXACTLY') + '''
This is a complete consecutive window of a longer lecture. Listen to the ENTIRE
supplied audio, not just sentences that look like questions. Include every supplied
paragraph exactly once in input order. A paragraph may contain multiple speakers,
including short professor acknowledgments inside audience speech. Lecturer quotations
and self-questions are NOT speaker changes. Output results: [{paragraph_id, turns,
limitations}], each using the turn schema above. If audible participant speech is
absent from the transcript, record it separately in missing_speech with approximate
clip times and an audio quote; never insert those words into the supplied text.
Do not label missing speech unless actually audible. The core interval identifies
window ownership; surrounding overlapping audio is context. Do not infer exact
speaker identity across windows or identify an audience member by name.'''

BATCH_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['results', 'missing_speech'],
    'properties': {
        'results': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['paragraph_id', 'turns', 'limitations'],
            'properties': {'paragraph_id': {'type': 'string'}, **SCHEMA['properties']}}},
        'missing_speech': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['audio_quote', 'clip_start_s', 'clip_end_s', 'reason'],
            'properties': {'audio_quote': {'type': 'string'},
                           'clip_start_s': {'type': 'number'}, 'clip_end_s': {'type': 'number'},
                           'reason': {'type': 'string'}}}},
    },
}


def protection_reasons(row: dict, context: dict, original_text: str) -> list[str]:
    reasons = []
    if 'p:' + str(row['index']) in context.get('reviews', {}):
        reasons.append('existing_review_record')
    if row['text'] != original_text:
        reasons.append('existing_text_edit')
    if re.search(r'聽眾|听众|說話者未明|教授\s*[:：]', row['text']):
        reasons.append('existing_speaker_label')
    return reasons


def insert_labels(text: str, turns: list[dict]) -> str:
    if ''.join(t['text'] for t in turns) != text:
        raise ValueError('Cannot label changed source text')
    # Collapse adjacent same-role partitions; they do not prove a new speaker.
    merged = []
    for turn in turns:
        if merged and merged[-1]['role'] == turn['role']:
            merged[-1]['text'] += turn['text']
        else:
            merged.append({'role': turn['role'], 'text': turn['text']})
    if len(merged) == 1 and merged[0]['role'] == 'professor':
        return text
    names = {'professor': '教授', 'audience': '聽眾', 'unknown': '說話者未明'}
    return '\n\n'.join('**' + names[t['role']] + '：**' + t['text'] for t in merged)


def plan_windows(paragraphs: list[dict], duration_ms: int, core_seconds=300) -> list[dict]:
    windows = []
    for start in range(0, duration_ms, core_seconds * 1000):
        end = min(duration_ms, start + core_seconds * 1000)
        owned = [p for p in paragraphs if start <= p['start_ms'] < end]
        clip_start = max(0, start - 15000)
        clip_end = min(duration_ms, max(end + 15000, max((p['end_ms'] + 5000 for p in owned), default=end)))
        windows.append({'id': f'w{len(windows):03d}', 'core_start_ms': start,
                        'core_end_ms': end, 'clip_start_ms': clip_start,
                        'clip_end_ms': clip_end, 'paragraphs': owned})
    if [p['id'] for w in windows for p in w['paragraphs']] != [p['id'] for p in paragraphs]:
        raise ValueError('Source paragraph order/timing prevents complete window ownership')
    return windows


def run(source: Path, snapshot: Path, out: Path, workers=2, *,
        core_seconds=300, recovery_parent: Path | None = None,
        thinking_level: str | None = None, only_window_ids: list[str] | None = None) -> dict:
    if workers not in (1, 2):
        raise ValueError('Bounded pilot supports one or two workers')
    if (out.resolve().is_relative_to(source.resolve())
            or source.resolve().is_relative_to(out.resolve())
            or snapshot.resolve().is_relative_to(out.resolve())):
        raise ValueError('Output must be separate from source and snapshot')
    transcript = read_json(source/'full_transcript.json')
    document = read_json(snapshot)
    master = read_json(source/'master.json')
    hashes = {name: file_sha(source/name) for name in ['master.wav', 'full_transcript.json']}
    snapshot_sha = file_sha(snapshot)
    source_by_id = {p['id']:p for p in transcript['paragraphs']}
    protections = {}
    for row in document['script']:
        binding = document['context']['paragraph_bindings'][row['index']]
        source_row = source_by_id[binding['source_paragraph_id']]
        protections[row['index']] = protection_reasons(row, document['context'], source_row['text'])
    windows = plan_windows(transcript['paragraphs'], master['duration_ms'], core_seconds)
    recovery_binding = None
    if recovery_parent is not None:
        states = list((recovery_parent/'calls').glob('*/state.json'))
        if len(states) != 1:
            raise ValueError('Recovery must bind exactly one original call')
        state = read_json(states[0])
        if state.get('http_status') != 504:
            raise ValueError('Only a recorded explicit HTTP 504 permits bounded split recovery')
        parent_plan = read_json(recovery_parent.parent/'plan.json')
        if parent_plan['source_hashes'] != hashes or parent_plan['snapshot_sha256'] != snapshot_sha:
            raise ValueError('Recovery source/snapshot differs from parent')
        parent = next(w for w in parent_plan['windows'] if w['id']==recovery_parent.name)
        windows = [w for w in windows if parent['core_start_ms'] <= w['core_start_ms'] < parent['core_end_ms']]
        if (len(windows) != 2 or windows[0]['core_start_ms'] != parent['core_start_ms']
                or windows[-1]['core_end_ms'] != parent['core_end_ms']):
            raise ValueError('Recovery must split the original core into exactly two complete windows')
        recovery_binding = {'parent_state_sha256':file_sha(states[0]),
                            'parent_request_sha256':file_sha(states[0].with_name('request.json')),
                            'parent_plan_sha256':file_sha(recovery_parent.parent/'plan.json'),
                            'parent_window':parent['id'], 'reason':'explicit_HTTP_504_split_no_original_replay',
                            'max_additional_calls':2, 'maximum_additional_reserved_usd':2}

    if only_window_ids:
        selected = [w for w in windows if w['id'] in only_window_ids]
        if len(selected) != len(only_window_ids) or len(set(only_window_ids)) != len(only_window_ids):
            raise ValueError('Unknown/duplicate selected window')
        windows = selected
    plan = {'schema_version': 'speaker-full-audio-plan.v1', 'source_hashes': hashes,
            'snapshot_sha256': snapshot_sha, 'snapshot_revision': document['context']['revision'],
            'window_count': len(windows), 'max_calls': len(windows),
            'reserved_usd_per_call': 1, 'maximum_reserved_usd': len(windows),
            'workers': workers, 'windows': windows, 'protections': protections}
    if recovery_binding:
        plan['bounded_recovery'] = recovery_binding
    if thinking_level is not None:
        if thinking_level != 'LOW':
            raise ValueError('Only existing audio correction LOW thinking policy is supported')
        plan['thinking_level'] = thinking_level
    if only_window_ids:
        plan['selected_window_ids'] = only_window_ids
    out.mkdir(parents=True, exist_ok=True)
    if (out/'plan.json').exists() and read_json(out/'plan.json') != plan:
        raise ValueError('Saved plan differs; do not replace')
    save_json(out/'plan.json', plan)
    samples = read_audio(source/'master.wav')

    def one(window):
        folder = out/window['id'];folder.mkdir(exist_ok=True)
        clip = folder/'context.wav'
        lo, hi = window['clip_start_ms'], window['clip_end_ms']
        audio = samples[lo * 16:hi * 16]
        if clip.exists():
            import numpy as np
            if not np.array_equal(read_audio(clip), audio):
                raise ValueError('Saved clip changed')
        else:
            write_audio(clip, audio)
        reviewer = VisionReviewer('gemini', BATCH_PROMPT, BATCH_SCHEMA)
        reviewer.config['max_output_tokens'] = 16384
        if thinking_level is not None:
            reviewer.config['thinking_config'] = {'thinking_level':thinking_level}
        material = {'core_clip_range_s': [(window['core_start_ms']-lo)/1000,
                                          (window['core_end_ms']-lo)/1000],
                    'paragraphs': [{'paragraph_id':p['id'], 'text':p['text'],
                                    'approximate_clip_range_s':[(p['start_ms']-lo)/1000,
                                                                (p['end_ms']-lo)/1000]}
                                   for p in window['paragraphs']]}
        request = {'stage':'full-audio-speakers.v1', 'model':reviewer.model,
                   'prompt':BATCH_PROMPT, 'schema':BATCH_SCHEMA, 'config':reviewer.config,
                   'source_hashes':hashes, 'clip_sha256':file_sha(clip),
                   'window':window['id'], 'material':material}
        from google.genai import types
        contents=[types.Part.from_text(text=BATCH_PROMPT+'\n'+json.dumps(material,ensure_ascii=False)),
                  types.Part.from_bytes(data=clip.read_bytes(),mime_type='audio/wav')]
        raw=saved_call(folder,request,1,lambda:reviewer.generate(contents),max_calls=1,budget_usd=1)
        answer=json.loads(reviewer.text(raw))
        from backend.pipeline.slide_visual_evidence import check_schema
        check_schema(answer,BATCH_SCHEMA)
        if [p['paragraph_id'] for p in answer['results']] != [p['id'] for p in window['paragraphs']]:
            raise ValueError('Missing, reordered or extra target paragraph')
        accepted, rejected = [], []
        for row, original in zip(answer['results'],window['paragraphs']):
            try:
                validate({k:v for k,v in row.items() if k!='paragraph_id'},original['text'],(hi-lo)/1000)
                accepted.append(row)
            except ValueError as error:
                rejected.append({'paragraph_id':row['paragraph_id'],'reason':str(error)})
        result={'window_id':window['id'],'source_clip_start_ms':lo,
                'accepted':accepted,'rejected':rejected,'missing_speech':answer['missing_speech'],
                'usage':reviewer.usage(raw),'review_status':'unreviewed_model_candidate'}
        save_json(folder/'candidate.json',result)
        return {'window':window['id'],'status':'complete','accepted':len(accepted),
                'rejected':len(rejected),'audience_paragraphs':sum(any(t['role']=='audience' for t in p['turns']) for p in accepted)}

    def guarded_one(window):
        try:
            return one(window)
        except Exception as error:
            return {'window':window['id'],'status':'stopped','error_type':type(error).__name__,'reason':str(error)}

    progress=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(guarded_one,windows):
            progress.append(result);save_json(out/'progress.json',progress)
            print(json.dumps(result,ensure_ascii=False),flush=True)
    if hashes != {name:file_sha(source/name) for name in hashes} or file_sha(snapshot)!=snapshot_sha:
        raise ValueError('Source/snapshot changed during model work')
    result={'windows':progress,'source_hashes':hashes,'snapshot_sha256':snapshot_sha,
            'production_writes':0,'complete':all(p['status']=='complete' and p['rejected']==0 for p in progress)}
    save_json(out/'batch-result.json',result)
    return result





def prepare_operations(initial: dict, current: dict, source: dict, candidates: dict) -> dict:
    """Only label paragraphs still identical, unedited, unreviewed and source-bound.

    Caller must submit returned operations using the current document CAS version
    and original assignment epoch. This function never persists review decisions.
    """
    original = {p['id']:p for p in source['paragraphs']}
    latest = {str(p['index']):p for p in current['script']}
    operations, skipped = [], []
    for row in initial['script']:
        key = str(row['index'])
        binding = initial['context']['paragraph_bindings'].get(key)
        now = latest.get(key)
        if not binding or binding.get('source_paragraph_id') not in original or now is None:
            skipped.append({'id':key,'reason':'source_or_paragraph_missing'})
            continue
        baseline = original[binding['source_paragraph_id']]['text']
        reasons = protection_reasons(row,initial['context'],baseline)
        reasons += protection_reasons(now,current['context'],baseline)
        if current['context']['paragraph_bindings'].get(key) != binding:
            reasons.append('binding_changed')
        if now['text'] != row['text']:
            reasons.append('concurrent_text_change')
        candidate = candidates.get(binding['source_paragraph_id'])
        if reasons:
            skipped.append({'id':key,'reason':sorted(set(reasons))})
        elif not candidate:
            skipped.append({'id':key,'reason':'no_valid_candidate'})
        else:
            # Reject source drift even if the candidate came from a saved call.
            replacement = insert_labels(now['text'],candidate['turns'])
            if replacement != now['text']:
                operations.append({'kind':'text','id':key,'text':replacement})
            else:
                skipped.append({'id':key,'reason':'professor_only_unchanged'})
    return {'operations':operations,'skipped':skipped}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--snapshot',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--core-seconds',type=int,choices=[75,150,300],default=300)
    parser.add_argument('--recovery-parent',type=Path)
    parser.add_argument('--thinking-level',choices=['LOW'])
    parser.add_argument('--only-window',action='append')
    args=parser.parse_args()
    run(args.source,args.snapshot,args.out,core_seconds=args.core_seconds,recovery_parent=args.recovery_parent,
        thinking_level=args.thinking_level,only_window_ids=args.only_window)
