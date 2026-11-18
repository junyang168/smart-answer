import json

import pytest

from backend.pipeline import lecture_reader as L


def test_scripture_refs_become_short_ranges_a_reader_can_search():
    assert L.verse('馬太福音5:3–7:29') == {'label': '太 5:3–7:29', 'book': '太', 'from': [5, 3], 'to': [7, 29]}
    assert L.verse('哥林多前書10:13')['label'] == '林前 10:13'
    assert L.verse('馬太福音7:7–12')['to'] == [7, 12]
    assert L.verse('第六章25-34節') is None


class FakeText:
    """Two paragraphs under one heading; one second per character."""
    headings = [(0, '一、開頭')]
    paragraphs = ['不要憂慮。不是不要籌慮。', '別的話。']
    base = [0, 12]

    def seconds(self, position):
        return float(position)

    def locate(self, fragment):
        p = int(fragment['paragraph_key'][1:]) - 1
        s = self.paragraphs[p].find(fragment['verbatim_excerpt'])
        return None if s < 0 else (p, s, s + len(fragment['verbatim_excerpt']))

    def section(self, paragraph):
        return 0


RECORDS = {
    'source_fragments': [{'fragment_id': 'F1', 'paragraph_key': 'S0001', 'verbatim_excerpt': '不是不要籌慮。'},
                         {'fragment_id': 'F2', 'paragraph_key': 'S0001', 'verbatim_excerpt': '不要憂慮。'}],
    'evidence_steps': [{'evidence_step_id': 'E1', 'statement': '籌慮是本分', 'source_fragment_ids': ['F1'],
                        'produced_claim_ids': ['C1']}],
    'claims': [{'claim_id': 'C1', 'statement': '不要憂慮不是不要籌慮', 'scripture_refs': ['馬太福音6:25–34'],
                'evidence_step_ids': ['E1'],
                'occurrences': [{'anchors': [{'source_fragment_id': 'F2'}]}]}],
}


def test_reasons_follow_his_spoken_order_and_mark_the_words_they_use():
    view = L.build(RECORDS, FakeText(), {}, [], 'cs')
    [point] = view['points']
    assert [r['kind'] for r in point['reasons']] == ['他直接說', '理由']
    assert point['start'] == 0.0 and point['verses'][0]['label'] == '太 6:25–34'
    pieces = view['transcript'][0]['pieces']
    assert [(p['text'], p['uses']) for p in pieces] == [('不要憂慮。', [[1, 1]]), ('不是不要籌慮。', [[1, 2]])]
    # Words no reason uses stay in the transcript, unmarked and playable.
    assert view['transcript'][1]['pieces'] == [{'text': '別的話。', 'start': 12.0, 'uses': []}]


def test_rulings_are_appended_and_only_on_candidates(tmp_path, monkeypatch):
    core = tmp_path / 'core.json'
    core.write_text(json.dumps({'cs': {'candidates': [{'claim_id': 'C1', 'covers': []}]}}))
    monkeypatch.setattr(L, 'CORE_POINTS', core)
    L.record_ruling(tmp_path, 'cs', 'right', 'C1', '對')
    L.record_ruling(tmp_path, 'cs', 'wrong', 'C1')
    rows = L._rulings(tmp_path)
    assert [r['verdict'] for r in rows] == ['right', 'wrong']
    view = L.build(RECORDS, FakeText(), json.loads(core.read_text())['cs'], rows, 'cs')
    assert view['core']['candidates'][0]['ruling']['verdict'] == 'wrong'
    with pytest.raises(ValueError, match='not a core-point candidate'):
        L.record_ruling(tmp_path, 'cs', 'right', 'C9')
    with pytest.raises(ValueError, match='unknown verdict'):
        L.record_ruling(tmp_path, 'cs', 'maybe', 'C1')
    L.record_ruling(tmp_path, 'cs', 'no_summary')
    assert L.build(RECORDS, FakeText(), {}, L._rulings(tmp_path), 'cs')['core']['no_summary']['verdict'] == 'no_summary'
