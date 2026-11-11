"""One source-bound review of newly located ranges for blocked paragraphs."""
import copy
import json
from pathlib import Path

import numpy as np

from backend.pipeline.slide_change_pilot import read_json, save_json, file_sha, object_sha


def validate(evidence, assessment):
    from backend.pipeline.transcript_audio_pilot import read_audio

    folder = evidence / 'playback-range-recovery'
    plan = read_json(folder / 'plan.json')
    parent = evidence / 'playback-readiness/plan.json'
    if (plan['parent_plan_sha256'] != file_sha(parent)
            or plan['parent_assessment_sha256'] != object_sha(assessment)
            or plan['max_calls'] != len(plan['cases'])):
        raise ValueError('Playback range recovery parent changed')
    originals = read_json(evidence / 'plan.json')['records']
    old = {(c['lecture'], c['paragraph_id']): c for c in read_json(parent)['cases'] if c['id'] != 'control'}
    rows = {(l, r['id']): r for l, rs in assessment['paragraphs'].items() for r in rs}
    seen = set()
    audio = {}
    for case in plan['cases']:
        key = case['lecture'], case['paragraph_id']
        if key in seen or key not in old or rows[key]['status'] != 'blocked':
            raise ValueError('Range recovery requires a distinct blocked paragraph')
        seen.add(key)
        prior = old[key]
        if case['reference'] != prior['reference'] or not case.get('localization_basis'):
            raise ValueError('Range recovery reference or localization basis changed')
        if not 1 <= len(case['candidates']) <= 6:
            raise ValueError('Range recovery candidate bound exceeded')
        source = originals[case['lecture']]
        master = Path(source['source']) / 'master.wav'
        if file_sha(master) != source['audio_sha256']:
            raise ValueError('Range recovery source audio changed')
        if case['lecture'] not in audio:
            audio[case['lecture']] = read_audio(master)
        ranges = set()
        for i, c in enumerate(case['candidates']):
            a, b = c['start_ms'], c['end_ms']
            if (c['id'] != f'c{i}' or not 0 <= a < b <= source['duration_ms']
                    or b - a > 180000 or (a, b) in ranges
                    or any((a, b) == (p['start_ms'], p['end_ms']) for p in prior['candidates'])):
                raise ValueError('Range recovery must use bounded new coordinates')
            ranges.add((a, b))
            clip = (evidence / c['audio']).resolve()
            if (not clip.is_relative_to(folder.resolve()) or file_sha(clip) != c['audio_sha256']
                    or not np.array_equal(read_audio(clip), audio[case['lecture']][a*16:b*16])):
                raise ValueError('Range recovery clip differs from original samples')
    return plan


def material(case):
    return {**case, 'candidates': [{k: v for k, v in c.items() if k != 'audio'} for c in case['candidates']]}


def run(evidence):
    from backend.pipeline import transcript_playback_readiness as playback
    from backend.pipeline.slide_pilot_models import VisionReviewer
    from backend.pipeline.transcript_audio_pilot import saved_call
    from google.genai import types

    folder = evidence / 'playback-range-recovery'
    if not (folder / 'plan.json').exists():
        return
    base = playback.assess(evidence, write=False, include_recovery=False)
    plan = validate(evidence, base)
    for i, case in enumerate(plan['cases']):
        reviewer = VisionReviewer('gemini', playback.PROMPT, playback.SCHEMA)
        reviewer.config['thinking_config'] = {'thinking_level': 'LOW'}
        parts = [types.Part.from_text(text=playback.PROMPT + '\n' + json.dumps(material(case), ensure_ascii=False))]
        for c in case['candidates']:
            parts += [types.Part.from_text(text='COMPLETE CANDIDATE ' + c['id']),
                      types.Part.from_bytes(data=(evidence/c['audio']).read_bytes(), mime_type='audio/wav')]
        request = dict(stage='complete-paragraph-new-range.v1', plan_sha256=file_sha(folder/'plan.json'),
                       model=reviewer.model, prompt=playback.PROMPT, schema=playback.SCHEMA,
                       config=reviewer.config, case=material(case))
        raw = saved_call(folder/f'reviews/c{i:04d}', request, 1, lambda: reviewer.generate(parts), max_calls=1, budget_usd=1)
        answer = json.loads(reviewer.text(raw))
        playback.usable(answer, case)
        save_json(folder/f'reviews/c{i:04d}/validated.json', dict(answer=answer,
                  request_sha256=object_sha(request), raw_response_sha256=object_sha(raw)))


def apply(evidence, base):
    from backend.pipeline import transcript_playback_readiness as playback
    from backend.pipeline.slide_pilot_models import VisionReviewer

    folder = evidence / 'playback-range-recovery'
    if not (folder/'plan.json').exists():
        return base
    plan = validate(evidence, base)
    result = copy.deepcopy(base)
    rows = {(l, r['id']): r for l, rs in result['paragraphs'].items() for r in rs}
    for row in rows.values():
        if row.get('prior_range_status'):
            row['status'] = row['prior_range_status']
    parser = VisionReviewer.__new__(VisionReviewer)
    parser.provider = 'gemini'
    for i, case in enumerate(plan['cases']):
        saved = folder/f'reviews/c{i:04d}/validated.json'
        if not saved.exists():
            continue
        value = read_json(saved)
        call = saved.parent/'calls'/value['request_sha256']
        request, state, raw = [read_json(call/name) for name in ('request.json', 'state.json', 'response.json')]
        if (object_sha(request) != value['request_sha256'] or state['status'] != 'complete'
                or state['response_sha256'] != file_sha(call/'response.json')
                or object_sha(raw) != value['raw_response_sha256']
                or request['plan_sha256'] != file_sha(folder/'plan.json')
                or request['stage'] != 'complete-paragraph-new-range.v1'
                or request['model'] != 'gemini-3.8-flash'
                or request['prompt'] != playback.PROMPT or request['schema'] != playback.SCHEMA
                or request['case'] != material(case)):
            raise ValueError('Range recovery saved request or response changed')
        answer = json.loads(parser.text(raw))
        if answer != value['answer']:
            raise ValueError('Range recovery interpretation changed')
        selected = playback.usable(answer, case)
        row = rows[case['lecture'], case['paragraph_id']]
        row['range_recovery_review'] = str(saved.relative_to(evidence))
        if selected:
            c = next(c for c in case['candidates'] if c['id'] == selected['id'])
            original = next(r for r in base['paragraphs'][case['lecture']] if r['id'] == case['paragraph_id'])
            row['prior_playback_verdict'] = copy.deepcopy(original)
            options = []
            for candidate in case['candidates']:
                judgment = playback.usable({**answer, 'selected_id': candidate['id']}, case)
                if judgment:
                    options.append(dict(id=candidate['id'], start_ms=candidate['start_ms'],
                                        end_ms=candidate['end_ms'], judgment=judgment))
            row.update(status='ready_with_context' if any(selected[k] == 'brief' for k in ('leading_context', 'trailing_context')) else 'ready',
                       start_ms=c['start_ms'], end_ms=c['end_ms'], reason=answer['reason'],
                       selected_id=c['id'], model_selected_id=c['id'], context=selected,
                       complete_options=options, basis='new_source_range_audio_review')
    for rs in result['paragraphs'].values():
        playback.repair_range_conflicts(rs)
        for left, right in zip(rs, rs[1:]):
            if left['status'] != 'blocked' and right['status'] != 'blocked' and (
                    right['start_ms'] < left['start_ms'] or left['end_ms'] - right['start_ms'] > playback.MAX_CONTEXT_MS):
                for row in (left, right):
                    row.update(status='blocked', reason='新範圍與鄰段倒退或重疊過多')
    result.update(recovery_plan_sha256=file_sha(folder/'plan.json'),
                  ready=sum(r['status'] != 'blocked' for rs in result['paragraphs'].values() for r in rs),
                  blocked=sum(r['status'] == 'blocked' for rs in result['paragraphs'].values() for r in rs),
                  warnings=sum(r['status'] == 'ready_with_context' for rs in result['paragraphs'].values() for r in rs))
    return result
