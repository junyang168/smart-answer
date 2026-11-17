from backend.pipeline import argument_corrections as ac

P = 'DK-x-'


def candidate():
    return {'claims': [{'claim_id': P + 'CL0001', 'evidence_step_ids': [P + 'ST1'], 'opposed_position_ids': [P + 'ST9']},
                       {'claim_id': P + 'CL0003', 'evidence_step_ids': [P + 'ST1'], 'opposed_position_ids': []}],
            'evidence_steps': [{'evidence_step_id': P + 'ST1', 'produced_claim_ids': [P + 'CL0003', P + 'CL0001']}],
            'observations': [], 'questions': [],
            'position_nodes': [{'position_id': P + 'ST9', 'title': '拿不出證據怎麼辦？', 'attribution': '會眾', 'source_fragment_ids': ['F9']}],
            'knowledge_relations': [{'relation_id': 'XR1', 'from_id': P + 'ST0', 'to_id': P + 'CL0003'},
                                    {'relation_id': 'XR2', 'from_id': P + 'ST0', 'to_id': P + 'CL0001'}],
            'claim_relations': []}


def test_drop_claim_keeps_the_step_for_the_other_claim():
    c = candidate()
    r = ac.drop_claim(c, 'CL0003')
    assert [x['claim_id'] for x in c['claims']] == [P + 'CL0001']
    assert c['evidence_steps'][0]['produced_claim_ids'] == [P + 'CL0001']
    assert r['removed_relations'] == ['XR1'] and [x['relation_id'] for x in c['knowledge_relations']] == ['XR2']


def test_position_becomes_an_answered_question_with_its_own_text():
    c = candidate()
    ac.position_to_question(c, 'ST9', 'CL0001')
    assert c['position_nodes'] == [] and c['claims'][0]['opposed_position_ids'] == []
    [q] = c['questions']
    assert q['text'] == '拿不出證據怎麼辦？' and q['questioner'] == 'audience' and q['answer_claim_ids'] == [P + 'CL0001']
