from backend.pipeline.argument_instance_correct import dangling


def test_a_retired_record_still_pointed_at_is_a_broken_reference():
    records = {'observations': [{'observation_id': 'O5', 'source_fragment_ids': ['F7', 'F5']}],
               'source_fragments': [{'fragment_id': 'F5'}, {'fragment_id': 'F7'}],
               'claims': [{'claim_id': 'C1', 'evidence_step_ids': [],
                           'occurrences': [{'anchors': [{'observation_id': 'O7', 'source_fragment_id': 'F7'}]}]}],
               'knowledge_relations': [{'relation_id': 'R1', 'from_id': 'O5', 'to_id': 'C1'}]}
    assert dangling(records) == ['C1 -> O7']
    records['claims'][0]['occurrences'][0]['anchors'][0]['observation_id'] = 'O5'
    assert dangling(records) == []
