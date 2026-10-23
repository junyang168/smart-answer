from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore, ChangeSetConflict


def link(identifier, start, text):
    return dict(viewpoint_claim_link_id=identifier, viewpoint_id='CV-1', claim_id='CL-1',
                validated_against_viewpoint_revision_id='CVR-1', pinned_claim_revision=3,
                revision=1, review_status='system_approved', effective_state='active',
                link_type='supports', decision_id='VID-1',
                evidence_bindings=[dict(evidence_step_id='E-1', source_fragment_id='F-1')],
                component_locator=dict(claim_sha256='a'*64, statement_component=text,
                    canonical_spans=[dict(start_char=start, end_char=start+len(text), exact_text=text)]))


class Cursor:
    def __init__(self, existing):
        self.existing = existing
    def execute(self, sql, params=None):
        self.sql, self.params = sql, params
    def fetchall(self):
        if 'SELECT collection, object_id' in self.sql:
            return [('canonical_viewpoints', 'CV-1'), ('claims', 'CL-1')]
        if 'SELECT payload' in self.sql:
            return [(self.existing,)] if self.existing else []
        if 'FROM wang_knowledge.edges' in self.sql and self.existing:
            return [('viewpoint_claim_links', self.existing['viewpoint_claim_link_id'], 'CV-1', 'CL-1', 'supports')]
        return []


def operation(payload):
    return SimpleNamespace(collection='viewpoint_claim_links', object_id=payload['viewpoint_claim_link_id'],
                           operation='create', payload=payload, after_revision=1)


@pytest.mark.parametrize('existing', [False, True])
def test_disjoint_components_are_distinct_incoming_and_current_edges(existing):
    a, b = link('VCL-a', 0, 'abc'), link('VCL-b', 3, 'def')
    ops = [operation(a)] if existing else [operation(a), operation(b)]
    PostgresKnowledgeStore._assert_edge_integrity(Cursor(b if existing else None), SimpleNamespace(operations=ops))


@pytest.mark.parametrize('mutation', ['overlap', 'same', 'full', 'revision', 'sha', 'target_revision',
                                    'unapproved', 'retired', 'empty_evidence', 'bad_span', 'bad_component', 'other_type'])
@pytest.mark.parametrize('existing', [False, True])
def test_unsafe_component_duplicates_remain_rejected(mutation, existing):
    a, b = link('VCL-a', 0, 'abc'), link('VCL-b', 3, 'def')
    if mutation in ('overlap', 'same'):
        b = link('VCL-b', 2 if mutation == 'overlap' else 0, 'abc')
    elif mutation == 'full': b['component_locator'] = None
    elif mutation == 'revision': b['pinned_claim_revision'] = 4
    elif mutation == 'sha': b['component_locator']['claim_sha256'] = 'b'*64
    elif mutation == 'target_revision': b['validated_against_viewpoint_revision_id'] = 'CVR-2'
    elif mutation == 'unapproved': b['review_status'] = 'candidate'
    elif mutation == 'retired': b['effective_state'] = 'retired'
    elif mutation == 'empty_evidence': b['evidence_bindings'] = []
    elif mutation == 'bad_span': b['component_locator']['canonical_spans'][0]['end_char'] = 7
    elif mutation == 'bad_component': b['component_locator']['statement_component'] = 'changed'
    elif mutation == 'other_type': b['link_type'] = 'extends'
    if mutation == 'other_type':
        # Test the component exception itself; different relation signatures
        # are governed independently by the unchanged foundation validator.
        assert not PostgresKnowledgeStore._disjoint_support_components(a, b)
        return
    ops = [operation(a)] if existing else [operation(a), operation(b)]
    with pytest.raises(ChangeSetConflict, match='semantic edge|Semantic edge'):
        PostgresKnowledgeStore._assert_edge_integrity(Cursor(b if existing else None), SimpleNamespace(operations=ops))


def test_every_colliding_component_is_checked_not_only_the_first():
    a, b, c = link('VCL-a', 0, 'abc'), link('VCL-b', 3, 'def'), link('VCL-c', 4, 'ghi')
    with pytest.raises(ChangeSetConflict, match='Duplicate semantic edge'):
        PostgresKnowledgeStore._assert_edge_integrity(Cursor(None), SimpleNamespace(operations=list(map(operation, [a,b,c]))))


def test_planned_current_edge_postimage_is_used():
    a, old = link('VCL-a', 0, 'abc'), link('VCL-b', 0, 'abc')
    updated = operation(link('VCL-b', 3, 'def')); updated.operation = 'update'
    PostgresKnowledgeStore._assert_edge_integrity(Cursor(old), SimpleNamespace(operations=[operation(a),updated]))
