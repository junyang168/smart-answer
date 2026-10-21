"""Growth gates for full-cut compatibility algorithms, not O(D) admission.

Count library traversals as well as checking results. A faster machine must not
hide a W*N or C*N regression. Cold/warm bounded admission remains a separate
release gate; these tests do not authorize a campaign restart.
"""
from copy import deepcopy
import random

import pytest

from backend.pipeline import source_reconciliation as c
from backend.pipeline import source_reconciliation_runtime as runtime


class CountedStates(dict):
    passes = 0
    visited = 0

    def items(self):
        self.passes += 1
        for item in super().items():
            self.visited += 1
            yield item


def old_fingerprint(states, collections=None):
    collections = (sorted(set(c.KNOWLEDGE_COLLECTIONS) | {c.split_key(k)[0] for k in states})
                   if collections is None else sorted(collections))
    return c.sha256_json({col: sorted(c.sha256_json(p) for k, p in states.items()
                                    if c.split_key(k)[0] == col) for col in collections})


def old_invalidations(before, planned, plan):
    """Frozen pre-index algorithm: comparison oracle, never production fallback."""
    result = deepcopy(planned)
    causes = {}
    for op in plan['operations']:
        col, oid = op['collection'], op['object_id']
        ids = []
        for k, p in before.items():
            if not k.startswith('product_dependencies/') or p.get('status', 'current') != 'current':
                continue
            if ((col == 'claims' and p.get('claim_id') == oid) or any(
                    r.get('collection') == col and r.get('record_id') == oid
                    for r in p.get('dependency_manifest', []))):
                ids.append(c.split_key(k)[1])
                causes.setdefault(k, []).append(op)
        if ids:
            eid = f"IMPACT-{plan['change_set_id']}-{col}-{oid}"
            if c.key('impact_events', eid) in before:
                raise ValueError('derived impact event already exists before commit')
            result[c.key('impact_events', eid)] = {
                'impact_event_id': eid, 'changed_record_type': col, 'changed_record_id': oid,
                'from_revision': op['before_revision'] or 0, 'to_revision': op['after_revision'],
                'affected_dependency_ids': sorted(ids),
                'required_actions': ['review_affected_products', 'withdraw_or_rebuild_published_consumers',
                                     'invalidate_qa_and_search_cache'],
                'status': 'open', 'review_status': 'system_generated', 'visibility': 'internal', 'revision': 1}
    for k, ops in causes.items():
        p = deepcopy(before[k])
        events = {f"IMPACT-{plan['change_set_id']}-{o['collection']}-{o['object_id']}" for o in ops}
        p.update(status='invalidated', revision=p['revision']+1,
                 invalidation_change_set_ids=sorted(set(p.get('invalidation_change_set_ids', [])) | {plan['change_set_id']}),
                 invalidation_event_ids=sorted(set(p.get('invalidation_event_ids', [])) | events))
        result[k] = p
    return result


@pytest.mark.parametrize('scale', [1, 10, 100])
def test_more_collections_do_not_multiply_full_library_traversals(monkeypatch, scale):
    states = CountedStates({f'claims/C{i}': {'statement': '原文', 'revision': i} for i in range(20)})
    cuts = ['claims', 'claims', *[f'empty-{i}' for i in range(scale)]]
    expected = old_fingerprint(dict(states), cuts)
    split = c.split_key
    calls = []
    monkeypatch.setattr(c, 'split_key', lambda key: (calls.append(key), split(key))[1])
    assert c.fingerprint(states, collections=cuts) == expected
    assert states.passes == 1 and states.visited == 20 and len(calls) == 20


@pytest.mark.parametrize('scale', [1, 10, 100])
def test_unrelated_rows_are_hashed_once_not_once_per_collection(monkeypatch, scale):
    states = CountedStates({f'claims/C{i}': {'statement': '原文', 'revision': i} for i in range(20*scale)})
    expected = old_fingerprint(dict(states))
    digest = c.sha256_json
    calls = []
    monkeypatch.setattr(c, 'sha256_json', lambda value: (calls.append(1), digest(value))[1])
    assert c.fingerprint(states) == expected
    assert states.passes == 1 and states.visited == len(states)
    assert len(calls) == len(states)+1  # rows plus the final complete cut


@pytest.mark.parametrize('rows_scale', [1, 10, 100])
@pytest.mark.parametrize('writes_scale', [1, 10, 100])
def test_writes_do_not_multiply_unrelated_dependency_scan(rows_scale, writes_scale):
    states = CountedStates({f'claims/U{i}': {'revision': 1} for i in range(20*rows_scale)})
    states['product_dependencies/D'] = {'dependency_id': 'D', 'claim_id': 'target', 'revision': 3,
        'dependency_manifest': [{'collection': 'claims', 'record_id': 'target'}]*2}
    plan = {'change_set_id': 'KCS-test', 'operations': [
        {'collection': 'claims', 'object_id': 'target' if i == 0 else f'changed-{i}',
         'before_revision': 1, 'after_revision': 2} for i in range(writes_scale)]}
    planned = dict(states)  # isolate traversal counts from full-cut deepcopy
    post = runtime.expected_invalidations(states, planned, plan)
    assert states.passes == 1 and states.visited == len(states)
    assert post['product_dependencies/D']['revision'] == 4
    assert len(post['product_dependencies/D']['invalidation_event_ids']) == 1
    post['claims/U0']['revision'] = 9
    assert planned['claims/U0']['revision'] == 1  # output ownership unchanged


def test_indexed_effects_equal_old_algorithm_for_all_causes_and_states():
    rng = random.Random(357)
    references = [('claims', f'C{i}') for i in range(4)] + [('source_fragments', f'F{i}') for i in range(4)]
    for _ in range(50):
        before = {f'claims/U{i}': {'revision': 1} for i in range(10)}
        for i in range(10):
            refs = [rng.choice(references) for _ in range(rng.randrange(8))]
            p = {'dependency_id': f'D{i}', 'revision': rng.randrange(1, 5),
                 'claim_id': rng.choice(references)[1],
                 'dependency_manifest': [{'collection': col, 'record_id': oid} for col, oid in refs],
                 'invalidation_change_set_ids': ['old'], 'invalidation_event_ids': ['old-event']}
            if i % 3:
                p['status'] = 'current' if i % 3 == 1 else 'invalidated'
            before[f'product_dependencies/D{i}'] = p
        plan = {'change_set_id': 'KCS-all', 'operations': [
            dict(collection=col, object_id=oid, before_revision=None if col == 'source_fragments' else 1,
                 after_revision=2) for col, oid in references]}
        assert runtime.expected_invalidations(before, before, plan) == old_invalidations(before, before, plan)


def test_existing_impact_event_still_rejects_duplicate_effect():
    before = {'product_dependencies/D': {'claim_id': 'C', 'revision': 1},
              'impact_events/IMPACT-KCS-claims-C': {'revision': 1}}
    plan = {'change_set_id': 'KCS', 'operations': [
        dict(collection='claims', object_id='C', before_revision=1, after_revision=2)]}
    with pytest.raises(ValueError, match='already exists'):
        runtime.expected_invalidations(before, before, plan)


@pytest.mark.parametrize('cut', [None, [], ['claims', 'claims'], ['unknown', 'empty']])
def test_fingerprint_wire_identity_unchanged(cut):
    states = {'claims/a': {'value': '中文/Own words', 'revision': 2},
              'claims/b': {'value': '中文/Own words', 'revision': 2},
              'unknown/c': {'number': 1.5, 'null': None, 'nested': [True, {'x': 'é'}]}}
    assert c.fingerprint(states, collections=cut) == old_fingerprint(states, cut)
    assert c.fingerprint({}, collections=cut) == old_fingerprint({}, cut)


def test_empty_collection_addition_still_changes_complete_fingerprint():
    before = {'claims/a': {'revision': 1}}
    after = {**before, 'viewpoint_identity_candidates/new': {'revision': 1}}
    assert c.fingerprint(before) != c.fingerprint(after)


def test_invalid_key_not_ignored_even_outside_explicit_cut():
    with pytest.raises(ValueError, match='invalid record key'):
        c.fingerprint({'invalid': {'revision': 1}}, collections=['claims'])
