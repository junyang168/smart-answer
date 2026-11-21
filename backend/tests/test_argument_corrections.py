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


def _with_text():
    c = candidate()
    c['claims'][0].update({'title': '不能把放棄計劃當作信心', 'occurrences': [{'anchors': []}]})
    c['source_fragments'] = [{'fragment_id': 'F1', 'paragraph_key': 'S0009', 'media_time': 1268, 'media_end_time': 1619,
                              'verbatim_excerpt': '你好好的倚靠神……神一定會供給你。'}]
    return c


def test_restate_claim_keeps_the_old_sentence_and_adds_where_he_says_it():
    c = _with_text()
    r = ac.restate_claim(c, 'CL0001', '就要倚靠神，神一定會供給', 'F1')
    assert c['claims'][0]['title'] == '就要倚靠神，神一定會供給' and r['before'] == '不能把放棄計劃當作信心'
    assert [a['source_fragment_id'] for a in c['claims'][0]['occurrences'][0]['anchors']] == ['F1']
    ac.restate_claim(c, 'CL0001', '再改一次', 'F1')
    assert len(c['claims'][0]['occurrences'][0]['anchors']) == 1


def test_restate_claim_needs_his_words_and_the_owner_ruling(tmp_path):
    import json, pytest
    with pytest.raises(ValueError, match='not a fragment'):
        ac.restate_claim(_with_text(), 'CL0001', 'x', 'F404')
    cand = tmp_path / 'c.json'; cand.write_text(json.dumps(_with_text(), ensure_ascii=False))
    spec = tmp_path / 's.json'
    spec.write_text(json.dumps({'corrections': [{'op': 'restate_claim', 'short': 'CL0001', 'statement': 'x', 'basis_fragment_id': 'F1'}]}))
    with pytest.raises(ValueError, match='owner ruling'):
        ac.apply(cand, spec, tmp_path / 'o.json')
