import json
import shutil

import pytest

from backend.pipeline import chapter_exposition as ce, chapter_slides as cs, chapter_material
from backend.pipeline.chapter_slides_publish import publish
from backend.tests.test_chapter_slides import CHAPTER, FakeClient, _decided, slide


def setup_trial(tmp_path, monkeypatch):
    live, trial = tmp_path/'live', tmp_path/'trial'
    _decided(live)
    transcript = tmp_path/'source.json'; transcript.write_text('original transcript')
    selection = ce.record_path(live, CHAPTER.passage, 'L')
    decisions = json.loads(selection.read_text())
    decisions.update(transcript_file=str(transcript), transcript_sha256=ce.file_sha256(transcript))
    selection.write_text(json.dumps(decisions))
    cs.decide(CHAPTER, 'L', data_base_dir=live, client=FakeClient([{'slides':[slide('S0001', title='old')]}]), force=True)
    saved = ce.record_path(trial/'data', CHAPTER.passage, 'L')
    saved.parent.mkdir(parents=True); shutil.copy2(selection,saved)
    baseline=trial/'baseline';baseline.mkdir();shutil.copy2(cs.record_path(live,CHAPTER.passage,'L'),baseline)
    (trial/'claims-context.json').write_text(json.dumps({'passage':CHAPTER.passage,'lecture':'L'}))
    cs.decide(CHAPTER,'L',data_base_dir=trial/'data',client=FakeClient([{'slides':[slide('S0001',title='model')]}]))
    cs.review(CHAPTER,'L',data_base_dir=trial/'data',client=FakeClient([{'grade':'B','blocking':False,'findings':[]}]))
    monkeypatch.setitem(chapter_material.CHAPTERS,CHAPTER.passage,CHAPTER)
    return live,trial


def test_publish_preserves_model_answers_and_binds_exact_owner_title(tmp_path,monkeypatch):
    live,trial=setup_trial(tmp_path,monkeypatch)
    source=cs.record_path(trial/'data',CHAPTER.passage,'L')
    original_source=source.read_bytes()
    correction=trial/'owner.json'
    correction.write_text(json.dumps({'base_slides_sha256':ce.file_sha256(source),'corrections':[
        {'authority':'owner_exact_wording','first':'S0001','before':'model','after':'owner'}]}))
    receipt=publish(trial,live,owner_corrections=correction)
    record=json.loads(cs.record_path(live,CHAPTER.passage,'L').read_text())
    assert record['slides'][0]['title']=='owner'
    assert record['raw_slides'][0]['title']=='model'
    assert record['publication']['model_slides_sha256']==ce.file_sha256(source)
    assert record['publication']['owner_title_edit_after_model_review'] is True
    assert source.read_bytes()==original_source
    assert (trial/'publication/previous-live/L.json').read_bytes()==(trial/'baseline/L.json').read_bytes()
    assert publish(trial,live,owner_corrections=correction)==receipt


@pytest.mark.parametrize('drift',['selection','live','source','review'])
def test_publish_refuses_drift_without_overwriting_live(tmp_path,monkeypatch,drift):
    live,trial=setup_trial(tmp_path,monkeypatch)
    target=cs.record_path(live,CHAPTER.passage,'L')
    if drift=='selection':
        ce.record_path(live,CHAPTER.passage,'L').write_text('{}')
    elif drift=='live':
        target.write_text('changed')
    elif drift=='source':
        (tmp_path/'source.json').write_text('changed')
    else:
        path=cs.review_path(trial/'data',CHAPTER.passage,'L',0)
        review=json.loads(path.read_text()); review['blocking']=True;path.write_text(json.dumps(review))
    before=target.read_bytes()
    with pytest.raises(ValueError): publish(trial,live)
    assert target.read_bytes()==before


def test_publish_blocks_confirmed_timing_findings_even_if_reviewer_flag_is_false(tmp_path, monkeypatch):
    live, trial = setup_trial(tmp_path, monkeypatch)
    target = cs.record_path(live, CHAPTER.passage, "L")
    before = target.read_bytes()
    path = cs.review_path(trial / "data", CHAPTER.passage, "L", 0)
    review = json.loads(path.read_text())
    review["findings"] = [{
        "slide": "S0001", "kind": "出現時間", "problem": "要點標在 S0002",
        "fix": "移到 S0001，原文「耶穌上了山」",
    }]
    path.write_text(json.dumps(review))
    with pytest.raises(ValueError, match="no matching nonblocking review"):
        publish(trial, live)
    assert target.read_bytes() == before
