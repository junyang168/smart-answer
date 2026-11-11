"""Semantic lookup retains all matches and uncertainty without a GPT candidate loop."""
from copy import deepcopy
import json
import pytest

from backend.pipeline import claim_flash_lookup as lookup, claim_flash_common as f


@pytest.fixture
def material(monkeypatch):
    def node(key, records, sources):
        return {'object_key': key, 'statement': records[key]['payload']['statement'],
                'original_text_excerpts': [records[key]['payload'].get('source', '原文。')]}
    monkeypatch.setattr(lookup.pairs, 'node', node)
    def make(claims=5, orphans=2):
        rs = {'claims/C'+str(i): {'payload': {'statement': '主張'+str(i), 'attribution': 'professor'}}
              for i in range(claims)}
        rs.update({'evidence_steps/E'+str(i): {'payload': {'statement': '證據'+str(i)}}
                   for i in range(orphans)})
        return rs, {'S': {'body_sha256': 'frozen-source'}}, sorted(k for k in rs if k.startswith('evidence_steps/')), sorted(k for k in rs if k.startswith('claims/'))
    return make


class Ports:
    def __init__(self, matches=None, uncertain=(), fail=False):
        self.matches = matches or {}; self.uncertain = uncertain; self.calls = []; self.fail = fail
    def judge(self, req):
        self.calls.append(req)
        if self.fail:
            raise RuntimeError('network lost')
        answers = {}
        for name, question in req['questions'].items():
            label = name.split('_')[0]
            remaining = [c for c in req['questions'][label+'_claim']['criteria'] if c != 'none']
            selected = next((c for c in remaining if c in self.matches.get(label, [])), 'none')
            if question['type'] == 'noul':
                answers[name] = {'type': 'noul', 'noul': .5 if label in self.uncertain else .95 if selected != 'none' else .01}
            else:
                answers[name] = {'type': 'choice', 'choice': selected,
                    'probabilities': {c: float(c == selected) for c in question['criteria']}}
        return {'model': lookup.e.MODEL, 'answers': answers}
    def repair(self, req):
        raise AssertionError('candidate lookup must not call GPT')


def test_more_than_three_matches_and_many_queries_share_one_state(material, tmp_path):
    args = material()
    ports = Ports({'O00': ['C00', 'C01', 'C02', 'C03', 'C04']})
    result = lookup.run(*args, tmp_path, ports)
    assert {p['b'] for p in result['pairs']} == set(args[3])
    assert len(ports.calls[0]['questions']) == 4
    assert len(ports.calls[0]['state']['claims']) == 5
    assert all('evidence' not in c for c in ports.calls[0]['state']['claims'].values())
    assert not result['pending']
    assert all('最直接' in q['instructions'] for k, q in ports.calls[0]['questions'].items() if k.endswith('_claim'))
    assert all('不要求單項證明整個主張' in q['instructions'] for q in ports.calls[0]['questions'].values())
    last = ports.calls[-1]['questions']['O00_claim']
    assert '已排除的 Claim：C00,C01,C02,C03。' in last['instructions']
    assert set(last['criteria']) == {'C04', 'none'}
    assert 'remaining_claims' not in ports.calls[-1]['state']


def test_uncertain_no_choice_keeps_the_unsearched_scope(material, tmp_path):
    args = material(claims=3, orphans=1)
    result = lookup.run(*args, tmp_path, Ports(uncertain={'O00'}))
    assert result['pairs'] == []
    assert result['pending'][0]['reason'] == 'uncertain_orphan_lookup'
    assert result['pending'][0]['remaining_claim_keys'] == args[3]


def test_uncertain_selected_candidate_is_not_discarded(material, tmp_path):
    args = material(claims=2, orphans=1)
    result = lookup.run(*args, tmp_path, Ports({'O00': ['C00']}, uncertain={'O00'}))
    assert result['pairs'][0]['b'] == 'claims/C0'
    assert result['pending'][0]['remaining_claim_keys'] == ['claims/C1']


def test_exact_saved_responses_resume_without_calls_or_new_authority(material, tmp_path):
    args = material()
    ports = Ports({'O00': ['C00', 'C01']})
    expected = lookup.run(*args, tmp_path, ports)
    replay = Ports(fail=True)
    assert lookup.run(*args, tmp_path, replay) == expected
    assert not replay.calls
    changed = deepcopy(args)
    changed[0]['claims/C0']['payload']['statement'] = '另一個意思。'
    with pytest.raises(ValueError, match='saved input differs'):
        lookup.run(*changed, tmp_path, replay)
    assert not replay.calls


def test_unknown_request_is_not_reissued(material, tmp_path):
    args = material(claims=1, orphans=1)
    ports = Ports(fail=True)
    with pytest.raises(RuntimeError, match='network lost'):
        lookup.run(*args, tmp_path, ports)
    with pytest.raises(ValueError, match='no saved reply'):
        lookup.run(*args, tmp_path, ports)
    assert len(ports.calls) == 1


@pytest.mark.parametrize('probability', [True, None, '0.8', -0.1, 1.1, float('nan'), float('inf')])
def test_noul_reply_requires_a_finite_probability(probability, tmp_path):
    class Bad:
        def judge(self, req):
            return {'model': lookup.e.MODEL, 'answers': {'q': {'type': 'noul', 'noul': probability}}}
    with pytest.raises(ValueError, match='invalid Jev Noul'):
        f.jev(tmp_path, Bad(), {}, {'q': {'type': 'noul', 'instructions': '有匹配嗎？'}})


def test_payload_splits_claim_universe_and_shares_complete_record(material, tmp_path, monkeypatch):
    args = material(claims=9, orphans=3)
    for k in args[3]:
        args[0][k]['payload']['statement'] += '不能截斷的主張。'*8
    monkeypatch.setattr(f, 'JEV_BYTES', 2400)
    ports = Ports()
    lookup.run(*args, tmp_path, ports)
    assert len(ports.calls) > 1
    assert {label for req in ports.calls for label in req['state']['claims']} == {f'C{i:02}' for i in range(9)}
    assert all(lookup.size(req) <= f.JEV_BYTES for req in ports.calls)
    for req in ports.calls:
        assert all(r['original_text_excerpts'] == ['原文。'] for r in req['state']['records'].values())


def test_late_oversized_record_fails_before_any_paid_call(material, tmp_path, monkeypatch):
    args = material(claims=2, orphans=2)
    args[0]['evidence_steps/E1']['payload']['source'] = '完整原文'*1000
    monkeypatch.setattr(f, 'JEV_BYTES', 2000)
    ports = Ports()
    with pytest.raises(ValueError, match='no call made'):
        lookup.run(*args, tmp_path, ports)
    assert not ports.calls


def test_option_limit_splits_without_losing_claims(material, tmp_path):
    args = material(claims=260, orphans=1)
    ports = Ports()
    lookup.run(*args, tmp_path, ports)
    assert len(ports.calls) >= 2
    assert len({c for req in ports.calls for c in req['state']['claims']}) == 260
    assert all(len(q['criteria']) <= 255 for req in ports.calls for q in req['questions'].values() if q['type'] == 'choice')


def test_no_claims_is_explicit_and_makes_no_call(material, tmp_path):
    args = material(claims=0, orphans=2)
    ports = Ports()
    result = lookup.run(*args, tmp_path, ports)
    assert len(result['pending']) == 2
    assert all(p['reason'] == 'no_existing_claim_for_orphan' for p in result['pending'])
    assert not ports.calls
