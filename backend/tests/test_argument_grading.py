from backend.pipeline import argument_grading as g


def s(i, text):
    return {'script_index': 1, 'text': text}


GROUP = {'group': 'G', 'items': [
    {'id': 'a', 'kind': 'step', 'status': 'owner', 'sentences': [s(1, '甲一句。'), s(1, '甲二句。')]},
    {'id': 'b', 'kind': 'step', 'status': 'owner', 'sentences': [s(1, '乙一句。'), s(1, '乙二句。')]},
    {'id': 'ct', 'kind': 'claim_text', 'status': 'owner', 'sentences': [s(1, '結論。')]},
    {'id': 'x', 'kind': 'aside', 'status': 'owner', 'sentences': [s(1, '題外話。')]},
    {'id': 'o', 'kind': 'other', 'status': 'draft', 'sentences': [s(1, '好。')]},
]}


def keys(*texts):
    return {('1', g.norm(t)) for t in texts}


def verdicts(records, own=None):
    out = g.grade_group(GROUP, {('evidence_steps', k): v for k, v in records.items()}, own or {'C': keys('結論。')})
    return {(r['item'], r['verdict']) for r in out if r['verdict'] != 'correct'}


def test_each_step_in_its_own_record_is_correct():
    assert verdicts({'E1': keys('甲一句。', '甲二句。'), 'E2': keys('乙一句。', '乙二句。')}) == set()


def test_split_merged_missed_and_partial():
    assert ('a', 'split') in verdicts({'E1': keys('甲一句。'), 'E2': keys('甲二句。'), 'E3': keys('乙一句。', '乙二句。')})
    assert ('a+b', 'merged') in verdicts({'E1': keys('甲一句。', '甲二句。', '乙一句。', '乙二句。')})
    assert ('b', 'missed') in verdicts({'E1': keys('甲一句。', '甲二句。')})


def test_claim_text_must_be_the_claims_own_words():
    assert ('ct', 'missed') in verdicts({'E1': keys('甲一句。', '甲二句。'), 'E2': keys('乙一句。', '乙二句。')}, own={'C': set()})


def test_an_aside_held_as_argument_is_reported_and_its_record_is_extra():
    found = verdicts({'E1': keys('甲一句。', '甲二句。'), 'E2': keys('乙一句。', '乙二句。'), 'E3': keys('題外話。')})
    assert ('x', 'aside_as_argument') in found and ('E3', 'extra') in found


def test_fragment_matching_ignores_punctuation_and_needs_the_same_paragraph():
    index = {'1': {('1', g.norm('甲一句。'))}}
    assert g.fragment_sentences({'verbatim_excerpt': '甲一句，'}, '1', index) == {('1', '甲一句')}
    assert g.fragment_sentences({'verbatim_excerpt': '甲一句'}, '2', index) == set()
