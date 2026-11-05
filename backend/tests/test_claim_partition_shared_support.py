"""Writable shared support coordinates targets without following Claim neighbours."""
from copy import deepcopy

import pytest

from backend.pipeline import claim_management as cm


def sample(*, collection=None, reverse_only=False, operation='Retire'):
    before = {}
    def row(col, oid, **payload):
        before[f'{col}/{oid}'] = {'payload': payload, 'retired': False}
    row('source_documents', 'S', source_id='S')
    for cid in 'ABCD':
        row('claims', cid, claim_id=cid, evidence_step_ids=[] if cid == 'A' and reverse_only else ['E'+cid],
            opposed_position_ids=['P'] if cid in 'ABD' else [], occurrences=[{'source_id': 'S'}])
        row('evidence_steps', 'E'+cid, evidence_step_id='E'+cid, produced_claim_ids=[cid],
            source_fragment_ids=['F'] if cid in 'ABD' else ['OTHER'])
    row('source_fragments', 'F', fragment_id='F', source_id='S')
    row('source_fragments', 'OTHER', fragment_id='OTHER', source_id='S')
    row('position_nodes', 'P', position_id='P', source_id='S')
    row('questions', 'Q', question_id='Q', answer_claim_ids=['A', 'B', 'D'])
    permissions = []
    if collection:
        oid, field = {'position_nodes': ('P', 'title'), 'source_fragments': ('F', 'verbatim_excerpt'),
                      'questions': ('Q', 'answer_claim_ids')}[collection]
        permissions = [cm.source.Permission(collection=collection, object_id=oid, fields=[field])]
    bound = {'path': '/not-a-live-policy', 'file_sha256': 'a'*64}
    request = cm.Request(action_id='partition-support', operation=operation, allowed_operations=[operation],
        target_keys=['claims/A', 'claims/B', 'claims/C'], source_bindings={'S': bound},
        policy=bound, authorization=bound, permissions=permissions)
    return request, before


def groups(request, before):
    return {frozenset(group) for group in cm.partition_targets(request, before)}


@pytest.mark.parametrize('collection', ['position_nodes', 'source_fragments', 'questions'])
def test_shared_write_joins_only_requested_owners(collection):
    request, before = sample(collection=collection)
    assert groups(request, before) == {frozenset(['claims/A', 'claims/B']), frozenset(['claims/C'])}
    assert 'claims/D' not in {key for group in groups(request, before) for key in group}


def test_readonly_position_fragment_question_and_source_do_not_join():
    request, before = sample()
    assert groups(request, before) == {frozenset(['claims/'+cid]) for cid in 'ABC'}


def test_reverse_evidence_ownership_is_still_affected_by_fragment_write():
    request, before = sample(collection='source_fragments', reverse_only=True)
    assert frozenset(['claims/A', 'claims/B']) in groups(request, before)


def test_claim_relation_is_not_a_second_dependency_frontier():
    request, before = sample(collection='source_fragments')
    before = deepcopy(before)
    before['evidence_steps/EA']['payload']['source_fragment_ids'] = ['OTHER']
    before['claim_relations/AB'] = {'payload': {'claim_relation_id': 'AB', 'from_id': 'A',
        'to_id': 'B', 'relation_type': 'supports'}, 'retired': False}
    assert groups(request, before) == {frozenset(['claims/'+cid]) for cid in 'ABC'}


@pytest.mark.parametrize('operation, coordinated', [('Retire', False), ('Restore', True)])
def test_retired_support_is_history_except_for_explicit_restore(operation, coordinated):
    request, before = sample(collection='source_fragments', operation=operation)
    before['evidence_steps/EA']['retired'] = True
    assert (frozenset(['claims/A', 'claims/B']) in groups(request, before)) == coordinated


def test_shared_position_and_incident_relation_form_one_retirement_transaction():
    request, before = sample(collection='position_nodes')
    before['claim_relations/AC'] = {'payload': {'claim_relation_id': 'AC', 'from_id': 'A',
        'to_id': 'C', 'relation_type': 'supports'}, 'retired': False}
    request.permissions.append(cm.source.Permission(collection='claim_relations', object_id='AC', fields=['reason']))
    assert groups(request, before) == {frozenset(['claims/A', 'claims/B', 'claims/C'])}


@pytest.mark.parametrize('write_premise', [True, False])
def test_shared_argument_input_does_not_expand_into_its_other_consumers(write_premise):
    request, before = sample(collection='source_fragments')
    before['evidence_steps/EA']['payload']['source_fragment_ids'] = ['OTHER']
    before['observations/O'] = {'payload': {'observation_id': 'O', 'source_fragment_ids': ['OTHER']}, 'retired': False}
    for cid in 'AB':
        before['knowledge_relations/OE'+cid] = {'payload': {'knowledge_relation_id': 'OE'+cid,
            'from_id': 'O', 'to_id': 'E'+cid, 'relation_type': 'supports'}, 'retired': False}
    if write_premise:
        request.permissions = [cm.source.Permission(collection='observations', object_id='O', fields=['statement'])]
    expected = {frozenset(['claims/A', 'claims/B']), frozenset(['claims/C'])} if write_premise else {
        frozenset(['claims/'+cid]) for cid in 'ABC'}
    assert groups(request, before) == expected
