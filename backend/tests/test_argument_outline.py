import pytest

from backend.pipeline import argument_outline as o

OWNER = {'CL1': '甲', 'CL2': '乙'}


def entry(kind, statement, owner=None):
    return {'kind': kind, 'statement': statement, 'why': 'x', **({'owner': owner} if owner else {})}


def test_each_owner_claim_needs_exactly_one_match_and_wrong_claims_count():
    gen = {'A': '甲說', 'B': '甲又說', 'C': '有張力'}
    out = o.grade_group(OWNER, gen, {'A': entry('match', '甲說', 'CL1'), 'B': entry('match', '甲又說', 'CL1'),
                                     'C': entry('question', '有張力')})
    verdicts = {(r['claim'], r['verdict']) for r in out['rows']}
    assert verdicts == {('CL1', 'split'), ('CL2', 'missed'), ('C', 'question')}
    assert out['covered_once'] == 0 and out['wrong_claims'] == 1


def test_a_key_written_for_another_run_is_refused():
    with pytest.raises(ValueError, match='stale'):
        o.grade_group(OWNER, {'A': '甲改了'}, {'A': entry('match', '甲說', 'CL1')})
    with pytest.raises(ValueError, match='unjudged'):
        o.grade_group(OWNER, {'A': '甲說', 'B': '新'}, {'A': entry('match', '甲說', 'CL1')})
