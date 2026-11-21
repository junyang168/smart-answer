import pytest

from backend.pipeline import argument_shape as sh


def candidate():
    return {'source_fragments': [{'fragment_id': 'F1', 'verbatim_excerpt': '牧師說：講清楚就能得釋放。'}],
            'claims': [{'claim_id': 'DK-x-CL0001', 'title': '講清楚能得釋放', 'evidence_step_ids': ['DK-x-P01-ST01'],
                        'occurrences': [{'anchors': [{'source_fragment_id': 'F1'}]}]}],
            'evidence_steps': [{'evidence_step_id': 'DK-x-P01-ST01', 'statement': '牧師這樣說', 'source_fragment_ids': ['F1']}],
            'observations': [], 'position_nodes': [], 'questions': []}


class FakeClaude:
    model = 'claude-opus-5-5'

    def __init__(self, source):
        self.source = source

    def generate_json(self, system, user, schema):
        import json
        return {'items': [{'job_id': j['job_id'], 'source': self.source, 'detail': 'd'} for j in json.loads(user)['jobs']]}


def test_a_claim_is_paired_with_its_evidence_and_his_words():
    [r] = sh.rows(candidate())
    assert r['statement'] == '講清楚能得釋放' and r['evidence'] == ['牧師這樣說'] and '牧師說' in r['his_words']


def test_only_claims_at_the_measured_risk_go_to_claude():
    items = [{'job_id': 'a', 'statement': 's'}, {'job_id': 'b', 'statement': 't'}]
    out = sh.prefilter_report(items, {'a': 0.3, 'b': 0.29})
    assert [x['id'] for x in out['needs_look']] == ['a'] and out['findings'] == []


def test_claude_clears_his_own_and_sends_a_story_claim_to_the_owner(tmp_path):
    items = sh.rows(candidate())
    pre = sh.prefilter_report(items, {'DK-x-CL0001': 0.8})
    mine = sh.judged_report(pre, items, sh.judge(items, tmp_path, FakeClaude('his_own')), day='2026-11-21')
    assert mine['findings'] == [] and mine['needs_look'][0]['ruling'].startswith('Claude 2026-11-21')
    story = sh.judged_report(pre, items, sh.judge(items, tmp_path, FakeClaude('others_or_story')), day='2026-11-21')
    assert [f['id'] for f in story['findings']] == ['DK-x-CL0001'] and 'ruling' not in story['findings'][0]


def test_judge_refuses_an_answer_that_skips_a_claim(tmp_path):
    class Silent:
        model = 'm'

        def generate_json(self, *a):
            return {'items': []}
    with pytest.raises(ValueError, match='unanswered'):
        sh.judge(sh.rows(candidate()), tmp_path, Silent())


def test_the_loader_refuses_while_the_story_check_has_an_unruled_claim(tmp_path):
    import json
    from backend.tests.test_argument_review_binding import write_run, load_run
    run = tmp_path / 'run'
    write_run(run)
    shape = run / 'shape/shape-final.json'
    report = json.loads(shape.read_text())
    report['findings'] = [{'id': 'DK-1-CL0001'}]
    shape.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='story check has 1 Claims without a ruling'):
        load_run(None, run)
