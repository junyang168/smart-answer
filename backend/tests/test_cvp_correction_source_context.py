"""Related judgment scope is not the frozen SQL group scope."""
from copy import deepcopy

import pytest

from backend.pipeline import cvp_correction_source_context as scope


def packet():
    return {'packet': {'claims': [{'claim_id': c, 'source_id': s}
            for c, s in [('C1', 'S1'), ('C2', 'S2'), ('C3', 'S3')]]},
        'proposal': {'claim_decisions': [{'claim_id': c, 'components': [
            {'local_new_viewpoint_key': k}]} for c, k in [('C1', 'A'), ('C2', 'B'), ('C3', 'D')]],
            'structures': [{'focal': [{'local_key': 'A'}, {'local_key': 'B'}]},
                           {'focal': [{'local_key': 'D'}]}],
            'new_viewpoint_candidates': [{'local_key': k, 'core': k} for k in ('A', 'B', 'D')],
            'viewpoint_revisions': [], 'viewpoint_relations': []},
        'review': {'change_reviews': [{'claim_id': 'C1', 'decision': 'correct'}],
            'structure_reviews': [], 'relation_reviews': [], 'revision_reviews': [],
            'novelty_review': {'status': 'pass', 'missed_claim_ids': []}}}


def sources():
    return [{'source_id': s, 'paragraph_texts': ['全文第一段', '全文最后段'],
             'source_file_sha256': s + '-file', 'source_body_sha256': s + '-body'}
            for s in ('S1', 'S2', 'S3')]


def test_component_only_gets_own_complete_source_and_keeps_group():
    p = packet(); before = deepcopy(p)
    result = scope.complete_packet(p, sources(), {'write_authority': False})
    assert result['own_source_bodies'] == sources()[:1]
    assert result['packet'] == before['packet'] and p == before
    assert result['correction_source_context']['unrelated_source_ids_not_attached'] == ['S2', 'S3']
    assert result['correction_source_context']['source_bodies_truncated'] is False


def test_cross_source_structure_includes_passing_participant_not_unrelated_source():
    p = packet(); p['review']['structure_reviews'] = [{'structure_index': 0, 'decision': 'correct'}]
    assert scope.related_claim_ids(p) == ['C1', 'C2']
    assert scope.complete_packet(p, sources(), {})['own_source_bodies'] == sources()[:2]


def test_same_candidate_all_claim_members_are_related():
    p = packet(); p['proposal']['claim_decisions'][1]['components'][0]['local_new_viewpoint_key'] = 'A'
    assert scope.related_claim_ids(p) == ['C1', 'C2']


def test_existing_identity_scope_cannot_claim_complete_members_from_batch_subset():
    p = packet()
    p['proposal']['structures'][0]['focal'][0] = {'viewpoint_revision_id': 'CURRENT-CVR'}
    p['review']['structure_reviews'] = [{'structure_index': 0, 'decision': 'correct'}]
    with pytest.raises(ValueError, match='complete current management members'):
        scope.related_claim_ids(p)


def test_passing_context_co_member_is_not_a_write_target():
    p = packet(); new = deepcopy(p['proposal'])
    new['claim_decisions'][1]['components'][0]['reason'] = 'context does not grant write approval'
    with pytest.raises(ValueError):
        scope.validate_scope_preservation(p['proposal'], new, p['review'], ['C1', 'C2'])


def test_relation_both_endpoints_are_related():
    p = packet(); p['review']['relation_reviews'] = [{'source_ref': 'A', 'target_ref': 'B', 'decision': 'correct'}]
    assert scope.related_claim_ids(p) == ['C1', 'C2']


@pytest.mark.parametrize('kind', ['missing_claim', 'unknown_endpoint', 'revision', 'no_finding'])
def test_missing_judgment_scope_refuses(kind):
    p = packet()
    if kind == 'missing_claim': p['review']['change_reviews'][0]['claim_id'] = 'FOREIGN'
    if kind == 'unknown_endpoint': p['review']['relation_reviews'] = [{'source_ref': 'A', 'target_ref': 'UNKNOWN', 'decision': 'correct'}]
    if kind == 'revision': p['review']['revision_reviews'] = [{'decision': 'reject'}]
    if kind == 'no_finding': p['review']['change_reviews'] = []
    with pytest.raises(ValueError): scope.related_claim_ids(p)


@pytest.mark.parametrize('kind', ['duplicate', 'missing', 'empty_body', 'missing_physical', 'missing_body_sha'])
def test_source_denominator_and_full_body_proofs_refuse(kind):
    ss = sources()
    if kind == 'duplicate': ss.append(deepcopy(ss[0]))
    if kind == 'missing': ss.pop()
    if kind == 'empty_body': ss[0]['paragraph_texts'] = []
    if kind == 'missing_physical': ss[0].pop('source_file_sha256')
    if kind == 'missing_body_sha': ss[0].pop('source_body_sha256')
    with pytest.raises(ValueError): scope.complete_packet(packet(), ss, {})


@pytest.mark.parametrize('kind', ['claim', 'candidate', 'structure', 'relation', 'revision', 'denominator'])
def test_unrelated_output_cannot_borrow_focused_source_approval(kind):
    p = packet(); old = p['proposal']; new = deepcopy(old)
    p['review']['structure_reviews'] = [{'structure_index': 1, 'decision': 'pass'}]
    if kind == 'claim': new['claim_decisions'][2]['components'][0]['reason'] = 'changed'
    if kind == 'candidate': new['new_viewpoint_candidates'][2]['core'] = 'changed'
    if kind == 'structure': new['structures'][1]['reason'] = 'changed'
    if kind == 'relation': new['viewpoint_relations'] = [{'source_local_key': 'D'}]
    if kind == 'revision': new['viewpoint_revisions'] = [{}]
    if kind == 'denominator': new['claim_decisions'].pop()
    with pytest.raises(ValueError): scope.validate_scope_preservation(old, new, p['review'], ['C1'])


def test_allowed_related_candidate_change_keeps_unrelated_records_exact():
    p = packet(); old = p['proposal']; new = deepcopy(old)
    new['new_viewpoint_candidates'][0]['core'] = 'source-grounded candidate, not approval'
    scope.validate_scope_preservation(old, new, p['review'], ['C1'])


def test_actual_saved_g153_related_source_preflight(monkeypatch):
    import os
    if os.environ.get('WKP_CVP_G153_READONLY') != '1':
        pytest.skip('explicit actual SQL/source readonly acceptance')
    from pathlib import Path
    from dotenv import load_dotenv
    from backend.pipeline import cvp_sql_flow as sql
    from backend.pipeline import viewpoint_batch_resolution_runner as runner
    from backend.pipeline.viewpoint_resolution_runtime import (
        read_artifact, response_adapter, read_saved_model_response, bound_model_response)
    from backend.pipeline.cvp_batch_identity_integration import ENABLED
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    load_dotenv('.env')
    store = PostgresKnowledgeStore()
    run = 'CVPG-ccedd0dafabf6ce13119762c527935d4813cd0d849e34d0b2f6bf4f0c6e2744b'
    root = Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution') / run / 'normal-review'
    before = store.get_cvp_group(run)
    files = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    captured = {}; authenticated = []
    class Boundary(Exception): pass
    def no_write(path, value, **kwargs):
        assert path.exists() and read_artifact(path) == value
    def call(model, payload, cache):
        if cache.name == scope.RAW:
            assert not cache.exists() and not cache.with_name(cache.stem + '.call-intent.json').exists()
            selected = response_adapter(model)
            candidates = [selected]
            for name in ('shared_keys_copy', 'shared_keys_v2_copy', 'shared_values_copy',
                         'shared_shapes_copy', 'shared_shapes_v2_copy', 'shared_shapes_v3_copy',
                         'shared_shapes_v4_copy', 'shared_shapes_v5_copy'):
                candidates.append(getattr(selected, name)())
            selected = min(candidates, key=lambda x: x.request_bytes(payload))
            proof = selected.request_encoding_proof(payload)
            assert proof['exact_roundtrip_verified']
            assert proof['semantic_payload_sha256'] == proof['restored_payload_sha256']
            assert selected.request_bytes(payload) < 600000
            assert len(payload['packet']['claims']) == 9
            assert sorted(len(s['paragraph_texts']) for s in payload['own_source_bodies']) == [35, 81]
            assert len(payload['correction_source_context']['unrelated_source_ids_not_attached']) == 1
            captured.update(request=deepcopy(payload), bytes=selected.request_bytes(payload), proof=proof)
            raise Boundary()
        assert cache.name in {'raw-proposal.json', 'raw-consolidation.json', 'raw-review.json', 'raw-reconsideration.json'}
        selected = response_adapter(model, read_artifact(cache))
        raw = read_saved_model_response(selected, payload, cache)
        authenticated.append(cache.name)
        return bound_model_response(selected, payload, raw, cache, persist=False), 0, 0.0
    monkeypatch.setattr(runner, '_write_immutable', no_write)
    monkeypatch.setattr(runner, '_call', call)
    token = ENABLED.set(True)
    try:
        with pytest.raises(Boundary): sql.reviewed_cvp_package(root, before['materials'], store, require_resolved=True)
    finally:
        ENABLED.reset(token)
    assert len(authenticated) == 4
    assert before == store.get_cvp_group(run)
    assert files == {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    print('G153_RELATED_PREFLIGHT', captured['bytes'], '9 Claims / 2 full related sources / 4 authenticated saved responses / zero calls and writes')
