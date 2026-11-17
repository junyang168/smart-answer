import pytest

from backend.pipeline import argument_eval as ev

REQ = {'input': {'claims': {'CL1': '覺得漂亮不等於心裡犯姦淫', 'CL2': '以弗所書四13同歸於一'},
                 'sentences': [{'sentence_id': 'S1', 'text': '記者聖經不懂，亂扯。'}, {'sentence_id': 'S2', 'text': '因為神造男造女。'}]}}
STEPS = {'steps': [{'step_id': 'ST1', 'line': '理由', 'supports': ['CL1'], 'sentence_ids': ['S2']}],
         'digressions': [{'sentence_ids': ['S1']}], 'not_claims': [{'claim_id': 'CL2', 'kind': 'evidence'}], 'claim_text': [], 'other': []}
CLAIMS = {'candidates': [{'statement': '教授認為那位弟兄求神使他看女孩子如石頭', 'existing_claim_id': None},
                         {'statement': '覺得異性漂亮是神造男造女的本性，不等於犯姦淫', 'existing_claim_id': None},
                         {'statement': '記者不懂', 'existing_claim_id': 'CL9'}]}


@pytest.mark.parametrize('c,ok', [
    ({'check': 'no_claim', 'all': ['石頭']}, False), ({'check': 'claim', 'all': ['漂亮', '姦淫']}, True),
    ({'check': 'no_claim', 'all': ['記者']}, True),  # a reused Claim is not this call's new one
    ({'check': 'no_word', 'pattern': '教授'}, False), ({'check': 'at_most', 'all': ['漂亮|石頭'], 'max': 1}, False),
    ({'check': 'apart', 'a': ['石頭'], 'b': ['本性']}, True), ({'check': 'every_with', 'if': ['漂亮'], 'then': ['姦淫']}, True)])
def test_claim_checks_find_claims_by_his_words(c, ok):
    assert ev.check(c, REQ, CLAIMS) is ok


@pytest.mark.parametrize('c,ok', [
    ({'check': 'in_digression', 'sentence': '亂扯'}, True), ({'check': 'not_digression', 'sentence': '神造男造女'}, True),
    ({'check': 'evidence_for', 'sentence': '神造男造女', 'claim': ['漂亮']}, True),
    ({'check': 'returned', 'all': ['同歸於一'], 'kind': 'evidence'}, True), ({'check': 'kept', 'all': ['漂亮']}, True)])
def test_step_checks_find_sentences_by_his_words(c, ok):
    assert ev.check(c, REQ, STEPS) is ok


def test_a_check_naming_words_he_never_said_is_an_error_not_a_pass():
    with pytest.raises(ValueError, match='no sentence'):
        ev.check({'check': 'in_digression', 'sentence': '律法全部廢掉'}, REQ, STEPS)


def test_cases_sharing_a_call_share_it():
    cases = [{'run': 'r', 'group': 'G1', 'stage': 'claims'}, {'run': 'r', 'group': 'G1', 'stage': 'claims'},
             {'run': 'r', 'group': 'G1', 'stage': 'steps'}]
    assert len(ev.plan(cases)) == 2 and len(ev.plan(cases, only=['steps'])) == 1


def test_every_case_points_at_one_saved_call_and_its_words_exist():
    import json
    cases = json.loads(ev.CASES.read_text())['cases']
    assert len({c['id'] for c in cases}) == len(cases)
    if not ev.RUNS.exists():
        pytest.skip('saved runs are not on this machine')
    for case in cases:
        request = ev.saved_request(case)
        for c in case['checks']:
            if 'sentence' in c and c['check'] != 'record_kind':
                ev._sentences(request, c['sentence'])
