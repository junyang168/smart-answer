"""Normal entry uses approved Units without duplicating older audio."""
from copy import deepcopy

import pytest

from backend.pipeline.chapter_listening_catalog import compose, subtract


def row(source, start=0, end=100, title='recording'):
    return dict(source_id=source, transcript_id=title, seconds=end-start,
                stretches=[dict(start=start, end=end)], transcript=[dict(at=start,text='原話')])


def example():
    legacy = dict(passage='mat-5-1-48', sections=[
        dict(passage='mat-5-1-12',label='八福',sermons=[row('old')]),
        dict(passage='mat-5-21-26',label='動怒',sermons=[row('old',20,30)]),
        dict(passage='mat-5-27-30',label='淫念',topics=[dict(text='舊題目')],sermons=[row('third',0,100,'third')]),
    ])
    updated = dict(resume_version='v1',sections=[
        dict(passage='mat-5-1-12-sub-1.1-run-1',unit_id='1',unit_label='Unit 1',sub_unit_id='1.1',sermons=[row('new',0,20)]),
        dict(passage='mat-5-17-20-sub-3.5-run-1',unit_id='3',unit_label='Unit 3',sub_unit_id='3.5',sermons=[row('new',20,30)]),
    ])
    return legacy, updated


def test_normal_catalog_replaces_approved_passages_and_absorbs_duplicate_old_clip():
    old,new=example()
    before=deepcopy((old,new))
    result=compose(old,new)
    assert result['unit_navigation'] and not result['selection_preview']
    assert [s['unit_id'] for s in result['sections']] == ['1','3','5']
    assert result['section_aliases']['mat-5-21-26'] == new['sections'][1]['passage']
    assert result['source_aliases']['old'] == 'new'
    assert result['sections'][-1]['topics'] == old['sections'][-1]['topics']
    assert result['sections'][-1]['sermons'] == old['sections'][-1]['sermons']
    assert (old,new)==before


@pytest.mark.parametrize('covered,expected', [([],[(0,100)]), ([(200,300)],[(0,100)]),
    ([(20,30)],[(0,20),(30,100)]), ([(0,20),(20,100)],[]), ([(10,30),(20,40)],[(0,10),(40,100)])])
def test_interval_subtraction_retains_only_unassigned_audio(covered,expected):
    assert [(s['start'],s['end']) for s in subtract(dict(start=0,end=100),covered)] == expected


def test_partial_overlap_preserves_uncovered_tail_and_trims_bound_metadata():
    old,new=example()
    old['sections'][1]['sermons']=[row('old',20,50)]
    result=compose(old,new)
    tail=next(s for s in result['sections'] if s['passage']=='mat-5-21-26')['sermons'][0]
    assert tail['stretches']==[dict(start=30,end=50)]
    assert tail['seconds']==20 and tail['transcript']==[]


def test_default_api_adopts_catalog_and_explicit_legacy_retains_old_reader(tmp_path, monkeypatch):
    from backend.api import original_audio as api
    from backend.pipeline import chapter_topics, chapter_listening_units
    old,new=example()
    plan=tmp_path/'chapter_exposition/mat-5-1-48/listening-units.json'
    plan.parent.mkdir(parents=True)
    plan.write_text('{}')
    monkeypatch.setattr(api,'DATA_BASE_PATH',str(tmp_path))
    monkeypatch.setattr(api,'PostgresKnowledgeStore',lambda: object())
    monkeypatch.setattr(api.original_audio_index,'passages',lambda: [('mat-5-1-48','title')])
    monkeypatch.setattr(api.original_audio_index,'build_index',lambda *args: old)
    monkeypatch.setattr(chapter_topics,'attach',lambda result,*args: result)
    monkeypatch.setattr(chapter_listening_units,'build',lambda *args: new)
    assert api.index('mat-5-1-48')['unit_navigation']
    assert api.index('mat-5-1-48',legacy=True)==old
    assert api.index('mat-5-1-48',source='other')==old
    assert api.index('mat-5-1-48',units=True)==new


def test_subsecond_boundary_remnant_has_no_separate_listening_entry():
    old, new = example()
    old['sections'][1]['sermons'] = [row('old', 20, 30.51)]
    result = compose(old, new)
    assert not any(s['passage'] == 'mat-5-21-26' for s in result['sections'])
    assert result['section_aliases']['mat-5-21-26'] == new['sections'][1]['passage']

    old['sections'][1]['sermons'][0]['transcript'].append({'at': 30.1, 'text': '還有一句'})
    assert any(s['passage'] == 'mat-5-21-26' for s in compose(old, new)['sections'])
