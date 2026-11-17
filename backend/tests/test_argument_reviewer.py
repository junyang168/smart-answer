import pytest

from backend.pipeline import argument_reviewer as r

VIEW = {'group_id': 'G001',
        'sentences': [{'sentence_id': 'S1#1', 'text': '記者聖經不懂，亂扯。'},
                      {'sentence_id': 'S1#2', 'text': '所以基督的律法跟摩西的律法有重疊。'}],
        'claims': [{'id': 'CL0001', 'statement': '兩種律法有重疊', 'his_words': [], 'support_lines': []},
                   {'id': 'CL0002', 'statement': '記者不懂聖經', 'his_words': [], 'support_lines': []}],
        'others': [{'id': 'D1', 'kind': 'digression', 'statement': '批評記者', 'text': '記者聖經不懂，亂扯。'}]}


def answer(**change):
    a = {'claims': [{'id': 'CL0001', 'verdict': 'his_claim', 'target': '', 'quote': '基督的律法跟摩西的律法有重疊', 'why': ''},
                    {'id': 'CL0002', 'verdict': 'digression', 'target': '', 'quote': '記者聖經不懂', 'why': '沒有理由'}],
         'others': [{'id': 'D1', 'verdict': 'unsupported', 'target': '', 'quote': '亂扯', 'why': ''}], 'missing': []}
    a.update(change)
    return a


def test_only_disagreements_with_gpt_are_kept():
    r.check(VIEW, answer())
    found = r.disagreements(VIEW, answer())
    assert [(d['item'], d['verdict']) for d in found] == [('CL0002', 'digression')]


@pytest.mark.parametrize('fault,match', [
    ('invented_quote', 'not in the transcript'), ('skipped_claim', 'each Claim once'),
    ('wrong_kind', 'does not fit'), ('bad_target', 'must name another Claim')])
def test_a_review_that_skips_an_item_or_quotes_unspoken_words_is_rejected(fault, match):
    a = answer()
    if fault == 'invented_quote': a['claims'][0]['quote'] = '律法已經全部廢掉'
    if fault == 'skipped_claim': a['claims'] = a['claims'][:1]
    if fault == 'wrong_kind': a['others'][0]['verdict'] = 'answered'
    if fault == 'bad_target': a['claims'][1].update(verdict='evidence_for', target='CL0002')
    with pytest.raises(ValueError, match=match):
        r.check(VIEW, a)


def test_saved_review_is_replayed_without_a_call(tmp_path, monkeypatch):
    monkeypatch.setattr(r, 'group_views', lambda creation: [VIEW])
    class Client:
        calls = 0
        def generate_json(self, system, user, schema):
            Client.calls += 1
            return answer()
    first = r.review('run', tmp_path, Client())
    again = r.review('run', tmp_path, None, call=False)
    assert Client.calls == 1 and first['calls'] == 1 and again['calls'] == 0
    assert again['disagreements'] == first['disagreements']
