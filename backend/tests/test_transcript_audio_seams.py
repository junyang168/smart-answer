import copy,json
from pathlib import Path
import pytest
from backend.tests.test_transcript_seam_repair import case
from backend.pipeline import transcript_audio_seams as seams
from backend.pipeline.slide_change_pilot import save_json,read_json
from backend.pipeline.transcript_text_layout import SeamCoverageError


def fixture(tmp_path,monkeypatch,verdict='pass',blocked_prompt=None):
    case(tmp_path)
    save_json(tmp_path/'seam-recovery/original/proof.json',{'pairs':[],'status':'needs_audio_review','reason':'No exact edge'})
    called=[]
    class Adapter:
        def __init__(self,provider,prompt,schema):
            self.provider=provider;self.prompt=prompt;self.model='gemini-3.8-flash';self.config={'response_json_schema':schema}
        def generate(self,contents):
            called.append(self.prompt)
            if self.prompt == blocked_prompt:
                return {'prompt_feedback': {'block_reason': 'PROHIBITED_CONTENT'}}
            answer={'pairs':[dict(id='s000',decision='remove_duplicate',right_prefix='乙丙丁戊',audio_quote='乙丙丁戊',reason='fixture')]} if self.prompt==seams.PROMPT else {'pairs':[dict(id='s000',verdict=verdict,audio_quote='乙丙丁戊',reason='fixture')]}
            return dict(candidates=[dict(finish_reason='STOP',content={'parts':[{'text':json.dumps(answer)}]})])
        def text(self,raw):
            if not raw.get('candidates'):
                raise ValueError('Incomplete Gemini response saved; no automatic retry')
            return raw['candidates'][0]['content']['parts'][0]['text']
    monkeypatch.setattr(seams,'VisionReviewer',Adapter)
    return called


def test_audio_ownership_replays_without_new_calls_and_binds_actual_answers(tmp_path,monkeypatch):
    calls=fixture(tmp_path,monkeypatch)
    result=seams.recover(tmp_path,tmp_path/'capacity')
    assert result['text']=='甲乙丙丁戊\n己庚' and len(calls)==2
    assert seams.recover(tmp_path,tmp_path/'capacity')['text']==result['text'] and len(calls)==2
    p=tmp_path/'seam-context-recovery/proof.json';proof=read_json(p);proof['answers'][1]['pairs'][0]['reason']='changed';save_json(p,proof)
    with pytest.raises(ValueError,match='Incomplete'):seams.load(tmp_path,p)


def test_rejected_audio_stays_rejected_without_repeat_judgment(tmp_path,monkeypatch):
    calls=fixture(tmp_path,monkeypatch,'reject')
    for _ in range(2):
        with pytest.raises(SeamCoverageError,match='unresolved'):seams.recover(tmp_path,tmp_path/'capacity')
    assert len(calls)==2 and not (tmp_path/'timed_text.json').exists()


def test_prior_audio_adjudication_cannot_gain_fresh_budget(tmp_path,monkeypatch):
    calls=fixture(tmp_path,monkeypatch)
    save_json(tmp_path/'seam-recovery/prior/audio-calls/calls/id/state.json',{'status':'rejected'})
    with pytest.raises(SeamCoverageError,match='budget'):seams.recover(tmp_path,tmp_path/'capacity')
    assert not calls


def test_cut_requires_exact_prefix_complete_token_and_positive_audio_verdict(tmp_path,monkeypatch):
    fixture(tmp_path,monkeypatch)
    asr,_,pairs=seams.materialize(tmp_path)
    pairs[0].update(alignment_path=str(tmp_path/'alignment/b.json'),left_alignment_path=str(tmp_path/'alignment/a.json'))
    for prefix in ['不存在','乙丙丁戊己庚','，']:
        with pytest.raises(ValueError):seams.decisions(asr,pairs,{'pairs':[dict(id='s000',decision='remove_duplicate',right_prefix=prefix,audio_quote='fixture',reason='fixture')]},{'pairs':[dict(id='s000',verdict='pass',audio_quote='fixture',reason='fixture')]})


def test_saved_unmatched_edge_failure_routes_to_audio_review_not_an_unhandled_valueerror(tmp_path,monkeypatch):
    from backend.pipeline import transcript_seam_repair as legacy
    from backend.pipeline.slide_change_pilot import object_sha
    chunks=case(tmp_path);binding=legacy.source_binding(tmp_path,read_json(tmp_path/'asr.json'))
    identity=object_sha({'policy':legacy.POLICY,'binding':binding})
    save_json(tmp_path/f'seam-recovery/{identity[:20]}/proof.json',dict(policy=legacy.POLICY,binding=binding,status='needs_audio_review',reason='No exact shared edge phrase; no fuzzy deletion permitted',pairs=[]))
    monkeypatch.setattr(seams,'recover',lambda *a,**kw:{'recovered':'audio branch'})
    assert legacy.stitch_with_recovery(tmp_path,Path('/unused'))=={'recovered':'audio branch'}


def test_uncertain_audio_keeps_all_words_and_a_notice_without_new_calls(tmp_path,monkeypatch):
    from backend.pipeline.transcript_seam_retention import recover,load
    from backend.pipeline.transcript_text_layout import stitch
    calls=fixture(tmp_path,monkeypatch,'reject')
    with pytest.raises(SeamCoverageError):seams.recover(tmp_path,tmp_path/'capacity')
    result=recover(tmp_path)
    assert result['text']=='甲乙丙丁戊\n乙丙丁戊己庚'
    proof=read_json(tmp_path/'seam-retention-proof.json')
    assert not proof['approved'] and proof['source_notices'][0]['status']=='retained_pending_human_review'
    assert proof['human_confirmed'] is False and len(calls)==2
    assert stitch(tmp_path)['text']==result['text']
    proof['source_notices']=[];save_json(tmp_path/'seam-retention-proof.json',proof)
    with pytest.raises(ValueError,match='proof changed'):load(tmp_path,tmp_path/'seam-retention-proof.json')


@pytest.mark.parametrize('blocked_prompt', [seams.PROMPT, seams.REVIEW])
def test_blocked_seam_keeps_both_edges_without_another_call(tmp_path,monkeypatch,blocked_prompt):
    from backend.pipeline.transcript_seam_retention import recover
    calls = fixture(tmp_path, monkeypatch, blocked_prompt=blocked_prompt)
    with pytest.raises(SeamCoverageError, match='Incomplete Gemini response'):
        seams.recover(tmp_path, tmp_path/'capacity')
    before = list(calls)
    result = recover(tmp_path)
    proof = read_json(tmp_path/'seam-retention-proof.json')
    assert result['text'] == '甲乙丙丁戊\n乙丙丁戊己庚'
    assert calls == before and not proof['approved'] and not proof['human_confirmed']
    assert 'Provider returned no seam decision' in proof['source_notices'][0]['reason']
