"""Rejected, uncommitted proposals can be withdrawn through normal review."""
from backend.pipeline import cvp_management_runner as runner
from test_cvp_management import passing
from test_cvp_management_models import envelope
from test_cvp_management_runner import setup, postgres_store, _temporary_postgres, parent


def test_rejected_new_identity_can_be_withdrawn_without_writing_it(setup):
    store, request, root, clients, _ = setup
    reviewed = []

    def propose(packet):
        if not packet.get('schema_version', '').endswith('_proposal_packet') or not packet['previous']:
            return None
        return envelope('candidate', candidate={
            'discard_refs': ['nv1', 'nr1', 'nl1'],
            'component_dispositions': [{'component_ref': 'k1', 'disposition': 'background',
                                       'reason': '此处是语境说明，不成立独立观点。'}],
        })

    def review(packet):
        if not packet.get('schema_version', '').endswith('_review_packet'):
            return None
        candidate = packet['candidate']
        reviewed.append(candidate)
        result = passing(candidate)
        if len(reviewed) == 1:
            result['object_reviews'][0].update(decision='revise', finding_refs=['f1'])
            result['findings'] = [{'slot': 'f1', 'target_refs': [result['object_reviews'][0]['ref']],
                                  'category': 'identity', 'reason': '材料仅作背景，不支持拟建独立观点。'}]
        else:
            result['prior_findings'] = [{'ref': ref, 'disposition': 'resolved',
                                        'reason': '候选已撤回，原主张成分仍完整记录为背景。'}
                                       for ref in candidate['review_sets']['prior_findings']]
        return envelope('review', review=result)

    clients['gpt'].behavior = propose
    clients['opus'].behavior = review
    outcome = runner.run(store, request, root=root, execute=True, clients=clients)
    assert outcome['status'] == 'completed', parent(store)['units']['g1'].get('detail')
    assert len(reviewed) == 2 and not reviewed[-1]['changes']
    assert reviewed[-1]['prior_findings']
    assert reviewed[-1]['component_plan'] == reviewed[0]['component_plan']
    assert store.read_cvp_heads() == []
    assert store.read_cvp_links(claim_ids=['C1']) == {}
    assert len(parent(store)['call_reservations']) == 4
    assert runner.run(store, request, root=root, execute=True, clients=clients) == outcome
    assert len(clients['gpt'].requests) == len(clients['opus'].requests) == 2
