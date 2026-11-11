"""Mechanical source spans stay within verified paragraph/version boundaries."""
from copy import deepcopy
import hashlib
import json
import pytest

from backend.pipeline import claim_flash_common as f,evidence_refresh as e


def scene(tmp_path,texts,quotes):
    path=tmp_path/'source.json'
    path.write_text(json.dumps({'script':[{'index':i,'text':text,'start_time':float(i),'end_time':float(i+1)}
                                         for i,text in enumerate(texts,1)]},ensure_ascii=False))
    doc={'source_id':'S','source_type':'sermon_transcript'}
    source=e.source_snapshot({'path':str(path),'file_sha256':hashlib.sha256(path.read_bytes()).hexdigest()},source=doc)
    records={};ids=[]
    for i,(pk,quote) in enumerate(quotes):
        fid='F'+str(i);ids.append(fid);p=source['paragraphs'][pk]
        records['source_fragments/'+fid]={'retired':False,'payload':{
            'fragment_id':fid,'source_id':'S','paragraph_key':pk,'verbatim_excerpt':quote,
            'source_sha256':source['body_sha256'],'anchor_state':'source_version_bound','source_segment_index':p['index'],
            'media_time':p['start_time'],'media_end_time':p['end_time'],
            'paragraph_text_sha256':hashlib.sha256(p['text'].encode()).hexdigest(),
            'verbatim_excerpt_sha256':hashlib.sha256(quote.encode()).hexdigest()}}
    records['evidence_steps/E']={'retired':False,'payload':{'evidence_step_id':'E','statement':'原摘要不變。',
        'speaker':'professor','stance':'asserted','source_fragment_ids':ids,'support_eligibility':'withheld_unreviewed'}}
    return records,{'S':source}


def test_same_paragraph_gap_is_filled_before_review_without_changing_records(tmp_path):
    text='前文。這字很關鍵。我們中文把它翻成立刻。一個月也叫立刻。後文。'
    records,sources=scene(tmp_path,[text],[('S0001','這字很關鍵。'),('S0001','一個月也叫立刻。')])
    before=deepcopy(records)
    value=f.value('evidence_steps/E',records,sources)
    assert value['original_text_excerpts']==['這字很關鍵。我們中文把它翻成立刻。一個月也叫立刻。']
    assert [x['quote'] for x in value['fragments']]==['這字很關鍵。','一個月也叫立刻。']
    assert value['source_assembly'][0]['fragment_ids']==['F0','F1']
    assert records==before
    assert e.material(records['evidence_steps/E']['payload'],records,sources)['original_text_excerpts']==['這字很關鍵。','一個月也叫立刻。']


@pytest.mark.parametrize('quotes',[['開頭。','結尾。'],['結尾。','開頭。']])
def test_between_fragments_negation_is_retained_in_source_order(tmp_path,quotes):
    records,sources=scene(tmp_path,['開頭。他不是這個意思。結尾。'],[('S0001',x) for x in quotes])
    assert f.value('evidence_steps/E',records,sources)['original_text_excerpts']==['開頭。他不是這個意思。結尾。']


def test_cross_paragraph_gap_is_not_filled(tmp_path):
    records,sources=scene(tmp_path,['開頭。','別的段落，不可順手補入。','結尾。'],[('S0001','開頭。'),('S0003','結尾。')])
    value=f.value('evidence_steps/E',records,sources)
    assert value['original_text_excerpts']==['開頭。','結尾。'] and not value['source_assembly']


@pytest.mark.parametrize('text,quotes',[
    ('開頭。中間。結尾。',['開頭。']),
    ('開頭。中間。開頭。結尾。',['開頭。','結尾。'])])
def test_single_or_ambiguous_fragment_does_not_expand(tmp_path,text,quotes):
    records,sources=scene(tmp_path,[text],[('S0001',x) for x in quotes])
    value=f.value('evidence_steps/E',records,sources)
    assert value['original_text_excerpts']==quotes and not value['source_assembly']


def test_overlapping_fragments_are_not_duplicated(tmp_path):
    records,sources=scene(tmp_path,['ABCDEF'],[('S0001','ABCD'),('S0001','CDEF')])
    assert f.value('evidence_steps/E',records,sources)['original_text_excerpts']==['ABCDEF']


def test_deleted_or_cross_source_bindings_are_not_rehabilitated_by_assembly(tmp_path):
    records,sources=scene(tmp_path,['開頭。中間。結尾。'],[('S0001','開頭。'),('S0001','結尾。')])
    records['source_fragments/F0']['payload']['anchor_state']='source_deleted'
    with pytest.raises(ValueError,match='mechanical calibration'):
        f.value('evidence_steps/E',records,sources)
    records['source_fragments/F0']['payload']['anchor_state']='source_version_bound'
    sources['OTHER']=deepcopy(sources['S']);records['source_fragments/F1']['payload']['source_id']='OTHER'
    with pytest.raises(ValueError,match='one source'):
        f.value('evidence_steps/E',records,sources)


def test_migration_repairs_question_references_with_the_flash_binding_validator(tmp_path):
    from backend.pipeline import transcript_consumer_binding as binding

    text = "寫離婚書可以防止輕易離婚。還有什麼？也保護被離婚的女子。"
    records, sources = scene(tmp_path, [text], [("S0001", "還有什麼？"), ("S0001", text)])
    records.pop("evidence_steps/E")
    records["source_fragments/F0"]["payload"]["anchor_state"] = "context_only"
    for key, ids in (("questions/Q1", ["F0"]), ("questions/Q2", ["F0", "F1"])):
        records[key] = {"retired": False, "revision": 1, "content_sha256": key,
                        "payload": {"text": "休書有什麼作用？", "source_fragment_ids": ids}}
    updates, created, missing = binding.project(records, {}, sources["S"], "S", {})
    assert set(updates) == {"questions/Q2"} and not created
    assert [row["record_key"] for row in missing] == ["questions/Q1"]
    rule = {"before_pin": [1, "questions/Q1"], "target_source_sha256": sources["S"]["body_sha256"],
            "segments": [{"paragraph_key": "S0001", "verbatim_excerpt": text}]}
    updates, created, missing = binding.project(records, {}, sources["S"], "S", {"questions/Q1": rule})
    assert not missing and len(created) == 1
    for key, value in {**updates, **created}.items():
        records[key] = {"retired": False, "payload": value}
    for key in updates:
        assert f.value(key, records, sources)["original_text_excerpts"] == [text]
