from backend.pipeline import argument_fidelity as af


def candidate():
    return {'source_fragments': [{'fragment_id': 'F1', 'verbatim_excerpt': '心裡恨人就等於殺人。'}],
            'claims': [{'claim_id': 'DK-x-CL0001', 'title': '恨人等於殺人', 'occurrences': [{'anchors': [{'source_fragment_id': 'F1'}]}]}],
            'evidence_steps': [{'evidence_step_id': 'DK-x-P01-ST01', 'statement': '恨人就是殺人', 'source_fragment_ids': ['F1']}],
            'observations': [], 'position_nodes': [], 'questions': []}


def test_each_statement_is_paired_with_his_own_words():
    rows = af.records(candidate())
    assert [(r['kind'], r['his_words']) for r in rows] == [('Claim', '心裡恨人就等於殺人。'), ('Evidence', '心裡恨人就等於殺人。')]


def test_gpt_decides_and_jev_only_raises_a_look():
    rows = af.records(candidate())
    decided = {'DK-x-CL0001': {'verdict': 'faithful', 'detail': ''}, 'DK-x-P01-ST01': {'verdict': 'added', 'detail': 'x'}}
    out = af.report(rows, decided, {'DK-x-CL0001': 0.8, 'DK-x-P01-ST01': 0.1})
    assert [f['id'] for f in out['findings']] == ['DK-x-P01-ST01']   # GPT flagged it; Jev's low score does not clear it
    assert [f['id'] for f in out['needs_look']] == ['DK-x-CL0001']   # Jev doubts what GPT passed: a person looks


def test_prefilter_sends_records_at_the_measured_risk_to_claude_and_decides_nothing():
    from backend.pipeline import argument_fidelity as F
    rows = [{'job_id': 'a', 'kind': 'Claim', 'statement': 's'}, {'job_id': 'b', 'kind': 'Position', 'statement': 't'}]
    out = F.prefilter_report(rows, {'a': 0.31, 'b': 0.29})
    assert [x['id'] for x in out['needs_look']] == ['a'] and out['findings'] == []
    assert F.prefilter_question('Position')['Q0']['criteria'] == F.SUPPORTED['others']['criteria']
    assert F.prefilter_question('Question')['Q0']['instructions'] == F.SUPPORTED['question']['instructions']
