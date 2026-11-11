import copy
import json

import numpy as np
import pytest

from backend.pipeline import playback_range_recovery as recovery
from backend.pipeline import transcript_playback_readiness as playback
from backend.pipeline.slide_change_pilot import file_sha, object_sha, save_json
from backend.pipeline.transcript_audio_pilot import write_audio


@pytest.fixture
def bound_case(tmp_path):
    samples = np.zeros(160000, dtype=np.float32)
    write_audio(tmp_path/'master.wav', samples)
    evidence = tmp_path/'evidence'
    save_json(evidence/'plan.json', dict(records={'source': dict(source=str(tmp_path),
              audio_sha256=file_sha(tmp_path/'master.wav'), duration_ms=10000)}))
    prior = dict(id='target', lecture='source', paragraph_id='p1', reference='原話',
                 candidates=[dict(id='c0', start_ms=0, end_ms=1000)])
    save_json(evidence/'playback-readiness/plan.json', dict(cases=[prior]))
    base = dict(paragraphs={'source': [dict(id='p1', status='blocked')]}, blocked=1, ready=0)
    folder = evidence/'playback-range-recovery'
    clip = folder/'audio/new.wav'
    write_audio(clip, samples[16000:48000])
    case = dict(id='target', lecture='source', paragraph_id='p1', reference='原話',
                localization_basis='重新定位到原音 1–3 秒', candidates=[dict(id='c0', start_ms=1000,
                end_ms=3000, audio=str(clip.relative_to(evidence)), audio_sha256=file_sha(clip))])
    plan = dict(parent_plan_sha256=file_sha(evidence/'playback-readiness/plan.json'),
                parent_assessment_sha256=object_sha(base), max_calls=1, cases=[case])
    save_json(folder/'plan.json', plan)
    return evidence, base, case


def complete_reply(evidence, case, verdict='complete'):
    folder = evidence/'playback-range-recovery'
    answer = dict(selected_id='c0', reason='原音確認', candidates=[dict(id='c0', verdict=verdict,
                  clipped_word=False, leading_context='none', trailing_context='none',
                  heard_opening='原話', heard_ending='原話')])
    raw = dict(candidates=[dict(finish_reason='STOP', content={'parts': [{'text': json.dumps(answer)}]})])
    request = dict(stage='complete-paragraph-new-range.v1', plan_sha256=file_sha(folder/'plan.json'),
                   model='gemini-3.8-flash', prompt=playback.PROMPT, schema=playback.SCHEMA,
                   case=recovery.material(case))
    call = folder/'reviews/c0000/calls'/object_sha(request)
    save_json(call/'request.json', request)
    save_json(call/'response.json', raw)
    save_json(call/'state.json', dict(status='complete', response_sha256=file_sha(call/'response.json')))
    save_json(folder/'reviews/c0000/validated.json', dict(answer=answer,
              request_sha256=object_sha(request), raw_response_sha256=object_sha(raw)))
    return call


def test_new_audio_range_requires_its_own_complete_review(bound_case):
    evidence, base, case = bound_case
    assert recovery.apply(evidence, base)['blocked'] == 1
    complete_reply(evidence, case)
    result = recovery.apply(evidence, base)
    assert result['blocked'] == 0 and result['ready'] == 1
    assert result['paragraphs']['source'][0]['start_ms'] == 1000
    assert result['paragraphs']['source'][0]['prior_playback_verdict']['status'] == 'blocked'
    assert base['paragraphs']['source'][0]['status'] == 'blocked'


def test_rejected_new_range_remains_blocked(bound_case):
    evidence, base, case = bound_case
    complete_reply(evidence, case, verdict='text_audio_mismatch')
    assert recovery.apply(evidence, base)['blocked'] == 1


@pytest.mark.parametrize('change', ['source', 'text', 'old_range', 'parent', 'unknown', 'raw'])
def test_changed_proof_or_unknown_outcome_cannot_qualify(bound_case, change):
    evidence, base, case = bound_case
    call = complete_reply(evidence, case)
    folder = evidence/'playback-range-recovery'
    plan = recovery.read_json(folder/'plan.json')
    if change == 'source':
        (evidence.parent/'master.wav').write_bytes(b'changed')
    elif change == 'text':
        plan['cases'][0]['reference'] = '改過的話'
    elif change == 'old_range':
        plan['cases'][0]['candidates'][0].update(start_ms=0, end_ms=1000)
    elif change == 'parent':
        base = copy.deepcopy(base)
        base['blocked'] = 2
    elif change == 'unknown':
        save_json(call/'state.json', dict(status='unknown'))
    else:
        save_json(call/'response.json', dict(candidates=[]))
    if change in {'text', 'old_range'}:
        save_json(folder/'plan.json', plan)
    with pytest.raises((ValueError, KeyError)):
        recovery.apply(evidence, base)
