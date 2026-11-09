import copy,json
from pathlib import Path
import pytest
from backend.tests.test_transcript_seam_repair import case
from backend.pipeline import transcript_audio_seams as seams
from backend.pipeline.slide_change_pilot import save_json,read_json
from backend.pipeline.transcript_text_layout import SeamCoverageError


def fixture(tmp_path,monkeypatch,verdict='pass'):
    case(tmp_path)
    save_json(tmp_path/'seam-recovery/original/proof.json',{'pairs':[],'status':'needs_audio_review','reason':'No exact edge'})
    called=[]
    class Adapter:
        def __init__(self,provider,prompt,schema):
            self.provider=provider;self.prompt=prompt;self.model='gemini-3.8-flash';self.config={'response_json_schema':schema}
        def generate(self,contents):
            called.append(self.prompt)
            answer={'pairs':[dict(id='s000',decision='remove_duplicate',right_prefix='乙丙丁戊',audio_quote='乙丙丁戊',reason='fixture')]} if self.prompt==seams.PROMPT else {'pairs':[dict(id='s000',verdict=verdict,audio_quote='乙丙丁戊',reason='fixture')]}
            return dict(candidates=[dict(finish_reason='STOP',content={'parts':[{'text':json.dumps(answer)}]})])
        def text(self,raw):return raw['candidates'][0]['content']['parts'][0]['text']
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
