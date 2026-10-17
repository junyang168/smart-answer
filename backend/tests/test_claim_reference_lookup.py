"""Reverse Claim lookup keeps exact ownership/reference semantics across batches."""
import pytest

from backend.api.canonical_repository.postgres_store import canonical_json, record_content_sha
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import (
    _temporary_postgres, postgres_store,
)


@pytest.mark.parametrize('size', [1, 9, 33])
def test_reverse_lookup_preserves_all_collections_and_excludes_false_matches(postgres_store, size):
    store = postgres_store
    current = set()
    expected = {('claim_relations', 'REL')}
    rows = []
    for i in range(size):
        claim, evidence, position, fragment = (f'{prefix}-{i}' for prefix in ('C', 'E', 'P', 'F'))
        current.update({('claims', claim), ('evidence_steps', evidence),
                        ('position_nodes', position), ('source_fragments', fragment)})
        owners = [
            ('claims', claim, {'evidence_step_ids': [evidence], 'opposed_position_ids': [position],
                              'source_fragment_ids': [fragment]}),
            ('evidence_steps', evidence, {'produced_claim_ids': [claim], 'source_fragment_ids': [fragment]}),
            ('observations', f'O-{i}', {'source_fragment_id': fragment}),
            ('position_nodes', position, {'source_fragment_ids': [fragment]}),
            ('questions', f'Q-{i}', {'answer_claim_ids': [claim], 'source_fragment_ids': [fragment]}),
        ]
        for collection, oid, payload in owners:
            expected.add((collection, oid))
            rows.extend([(collection, oid, payload, False),
                         (collection, oid+'-retired', payload, True)])
        # Same IDs under the wrong field, collection or JSON type are not dependencies.
        rows.extend([
            ('claims', f'WRONG-FIELD-{i}', {'unrelated_ids': [evidence, position, fragment]}, False),
            ('observations', f'WRONG-COLLECTION-{i}', {'answer_claim_ids': [claim]}, False),
            ('claims', f'WRONG-TYPE-{i}', {'evidence_step_ids': evidence}, False),
        ])
    rows.extend(('claim_relations', oid, {'from_claim_id': 'C-0', 'to_claim_id': 'OTHER',
                                        'relation_type': 'supports'}, retired)
                for oid, retired in [('REL', False), ('REL-retired', True)])
    with store.connect() as conn:
        for collection, oid, payload, retired in rows:
            payload = {**payload, 'revision': 1}
            conn.execute('''INSERT INTO wang_knowledge.objects
                (collection,object_id,revision,review_status,visibility,content_sha256,payload,retired_at)
                VALUES (%s,%s,1,'candidate','internal',%s,%s::jsonb,
                        CASE WHEN %s THEN now() ELSE NULL END)''',
                (collection, oid, record_content_sha(payload), canonical_json(payload), retired))
        for oid, retired in [('REL', False), ('REL-retired', True)]:
            conn.execute('''INSERT INTO wang_knowledge.edges
                (edge_collection,edge_id,from_id,to_id,relation_type,revision,payload,retired_at)
                VALUES ('claim_relations',%s,'C-0','OTHER','supports',1,'{}'::jsonb,
                        CASE WHEN %s THEN now() ELSE NULL END)''', (oid, retired))
        observed = store._claim_reference_consumers(conn.cursor(), current)
    assert observed == expected


def test_empty_frontier_has_no_reverse_dependencies(postgres_store):
    with postgres_store.connect() as conn:
        assert postgres_store._claim_reference_consumers(conn.cursor(), set()) == set()
