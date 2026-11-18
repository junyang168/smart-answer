import copy
import json

import pytest

from backend.pipeline import chapter_title
from backend.pipeline import chapter_unit_publish as pub
from backend.pipeline.chapter_material import Chapter


def test_summary_and_plan_publish_together_only_after_bound_eval(tmp_path, monkeypatch):
    passage = 'mat-6-1-34'
    slug = 'mat-6-8-to-7-11'
    plan = {'units': {slug: {'title': '禱告與生活'}}}
    material = [{'id': slug, 'unit': True, 'heading': '禱告與生活', 'subunits': []}]
    monkeypatch.setattr(pub, '_project', lambda *args: material)
    monkeypatch.setattr(chapter_title, 'chapters',
                        lambda *args: {passage: Chapter(passage, 'NYSC', ())})
    monkeypatch.setattr(chapter_title, '_slides_shas', lambda *args: {'lecture': 'expected'})
    path = pub.path_for(tmp_path, passage)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'units': {}}))
    previous = path.read_bytes()

    class Client:
        def __init__(self, title, verdict):
            self.title = title
            self.verdict = verdict

        def generate_json(self, system, prompt, schema):
            return {'title': self.title} if system == pub.TITLE_PROMPT else self.verdict

    verdict = {'units': [{'id': slug, 'covered': True, 'reason': '保留跨章禱告主題'}], 'problems': []}
    title = '太6:8–7:11禱告與生活'
    for invalid in [Client('第六章的禱告', verdict),
                    Client(title, {'units': [], 'problems': []}),
                    Client(title, {**verdict, 'problems': ['摘要夾帶未上線結語']})]:
        rejected = pub.prepare(tmp_path, passage, plan, invalid)
        with pytest.raises(ValueError, match='Eval did not pass'):
            pub.publish(tmp_path, passage, plan, rejected)
        assert path.read_bytes() == previous

    approved = pub.prepare(tmp_path, passage, plan, Client(title, verdict))
    tampered = {**approved, 'title': title + '與結語'}
    with pytest.raises(ValueError, match='changed after summary Eval'):
        pub.publish(tmp_path, passage, plan, tampered)
    assert path.read_bytes() == previous

    different_plan = copy.deepcopy(plan)
    different_plan['units'][slug]['title'] += '新'
    with pytest.raises(ValueError, match='different listening plan'):
        pub.publish(tmp_path, passage, different_plan, approved)
    assert path.read_bytes() == previous

    pub.publish(tmp_path, passage, plan, approved)
    assert json.loads(path.read_text()) == {**plan, 'chapter_summary': approved}
    assert chapter_title.load(tmp_path, passage) == title
    monkeypatch.setattr(chapter_title, '_slides_shas', lambda *args: {'lecture': 'changed'})
    assert chapter_title.load(tmp_path, passage) is None


def test_summary_material_uses_visible_playable_slides_only():
    def section(passage, seconds, title):
        return {'passage': passage, 'label': title, 'sermons': [
            {'seconds': seconds, 'slides': [{'title': title, 'points': [{'text': title}]}]}]}

    index = {'sections': [
        section('mat-5-31-32-sub-6.1-run-1', 10, '離婚'),
        section('mat-5-31-32-sub-6.2-run-2', 20, '再婚'),
        section('mat-5-38-42', 0, '空入口'),
    ]}
    plan = {'units': {'mat-5-31-32': {'title': '離婚與再婚'}}}
    [row] = pub.summary_material(index, plan)
    assert row['unit'] and row['id'] == 'mat-5-31-32'
    assert [sub['slides'][0]['title'] for sub in row['subunits']] == ['離婚', '再婚']
