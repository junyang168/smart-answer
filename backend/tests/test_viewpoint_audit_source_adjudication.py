from copy import deepcopy
import pytest
from backend.pipeline import viewpoint_audit_source_adjudication as module
from backend.pipeline.viewpoint_resolution_runtime import write_immutable, read_artifact
from backend.tests.test_viewpoint_deferred_source_check import Adapter


def packet():
    return {'artifact_sha256': 'pin', 'items': [{'item_id': '4:v:c', 'sources': [
        {'source_id': 's', 'paragraphs': [{'paragraph_key': 'S0001', 'text': '王就是這位彌賽亞。'}]}]}]}


@pytest.mark.parametrize('case', ['registered_alias', 'canonical', 'foreign', 'title_only',
                                  'ambiguous', 'retired', 'notes'])
def test_own_source_identity_only_accepts_unique_active_registered_sermon_alias(case):
    source = {'source_id': 's', 'transcript_id': 'lecture',
              'title': 'other', 'source_type': 'sermon_transcript'}
    records = [source]
    own = {'lecture'}
    if case == 'canonical': own = {'s'}
    if case == 'foreign': own = {'another'}
    if case == 'title_only': own = {'other'}
    if case == 'ambiguous': records += [source | {'source_id': 's2'}]
    if case == 'notes': source['source_type'] = 'notes_manuscript'
    class Store:
        def list_records(self, collection): return records
        def get_record_state(self, collection, identity):
            return {'retired': case == 'retired',
                    'payload': next(r for r in records if r['source_id'] == identity)}
    if case in ('registered_alias', 'canonical'):
        module.validate_own_source_identity(source, own, Store())
    else:
        with pytest.raises(ValueError, match='foreign source'):
            module.validate_own_source_identity(source, own, Store())


@pytest.mark.parametrize('legacy_cache', [False, True])
def test_source_review_compact_and_legacy_cache_both_reuse_without_new_calls(tmp_path, legacy_cache):
    import json
    from types import SimpleNamespace
    from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
    from backend.pipeline.viewpoint_resolution_runtime import call_model
    calls = []
    client = SimpleNamespace(model='gpt-6.1-sol', backend='codex_subscription',
        reasoning_effort='high', max_output_tokens=None,
        generate_json=lambda **kw: calls.append(kw) or answer())
    cache = tmp_path / 'raw-gpt.json'
    initial = module.source_review_adapter(client, cache)
    assert initial._compact_payload
    if legacy_cache:
        initial = StructuredJsonReviewerAdapter(client=client, prompt=module.PROMPT,
            response_model=module.Findings, schema_name='wang_audit_source_findings_v1')
    payload = packet()
    call_model(initial, payload, cache)
    original = cache.read_bytes()
    restored = module.source_review_adapter(client, cache)
    assert restored._compact_payload is not legacy_cache
    call_model(restored, payload, cache)
    assert len(calls) == 1 and cache.read_bytes() == original
    assert json.loads(calls[0]['user_prompt']) == payload
    with pytest.raises(ValueError, match='another request payload|immutable artifact differs'):
        call_model(restored, payload | {'source_drift': True}, cache)
    assert len(calls) == 1


def test_source_review_frozen_ceiling_stops_without_transport(tmp_path):
    from types import SimpleNamespace
    from backend.pipeline.viewpoint_resolution_runtime import call_model
    client = SimpleNamespace(model='gpt-6.1-sol', backend='codex_subscription',
        reasoning_effort='high', max_output_tokens=None,
        generate_json=lambda **kw: pytest.fail('overflow must not call model'))
    a = module.source_review_adapter(client, tmp_path / 'raw.json')
    a.max_request_bytes = 500000
    with pytest.raises(ValueError, match='frozen ceiling'):
        call_model(a, {'original': 'X' * 500001}, tmp_path / 'raw.json')
    assert not (tmp_path / 'raw.json').exists()


def answer(verdict='audit_objection_rejected'):
    return {'findings': [{'item_id': '4:v:c', 'verdict': verdict, 'source_id': 's',
                          'paragraph_key': 'S0001', 'exact_quote': '王就是這位彌賽亞。',
                          'reason': '同一原文明確消解身份指代。'}]}


@pytest.mark.parametrize('mutation', ['foreign_source', 'foreign_paragraph', 'forged_quote', 'duplicate', 'missing', 'empty_quote'])
def test_invalid_findings_rejected(mutation):
    raw = answer()
    row = raw['findings'][0]
    if mutation == 'foreign_source': row['source_id'] = 'foreign'
    elif mutation == 'foreign_paragraph': row['paragraph_key'] = 'S9999'
    elif mutation == 'forged_quote': row['exact_quote'] = '教授沒說過'
    elif mutation == 'duplicate': raw['findings'] *= 2
    elif mutation == 'missing': raw['findings'] = []
    else: row['exact_quote'] = ''
    with pytest.raises(ValueError): module.validate_findings(raw, packet())


@pytest.mark.parametrize('right,action', [
    ('audit_objection_rejected', 'independent_resolution_required'),
    ('audit_objection_upheld', 'owner_judgment_required'),
    ('unresolved', 'owner_judgment_required')])
def test_bounded_blind_adjudication_never_grants_completion(tmp_path, monkeypatch, right, action):
    monkeypatch.setattr(module, 'build_packet', lambda *args: packet())
    adapters = [Adapter(answer()), Adapter(answer(right))]
    report = module.run(tmp_path/'audit.json', tmp_path/'out', object(), adapters)
    assert report['actions'][0]['next_action'] == action
    assert report['group_complete'] is False and report['master_data_mutations'] == 0
    assert adapters[0].payloads == adapters[1].payloads
    module.run(tmp_path/'audit.json', tmp_path/'out', object(), adapters)
    assert [a.calls for a in adapters] == [1, 1]


def test_failure_preserves_raw_and_does_not_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'build_packet', lambda *args: packet())
    bad = answer(); bad['findings'][0]['exact_quote'] = '假的'
    adapters = [Adapter(bad), Adapter(answer())]
    with pytest.raises(ValueError): module.run(tmp_path/'audit.json', tmp_path/'out', object(), adapters)
    assert (tmp_path/'out/raw-gpt.json').exists()
    assert (tmp_path/'out/failure.json').exists()
    assert not (tmp_path/'out/handoff.json').exists()
    assert [a.calls for a in adapters] == [1, 0]


def test_incomplete_audit_not_routed_as_content():
    audit = {'layers': {'4': {'complete': False, 'judged': 1, 'disputed': 1,
                            'results': [{'verdict': 'disputed'}]}}}
    with pytest.raises(ValueError, match='incomplete'): module.disputes(audit)


def test_passing_audit_has_no_adjudication():
    assert module.disputes({'layers': {'4': {'results': [{'verdict': 'same'}]}}}) == []


def test_missing_population_cannot_be_routed():
    audit = {'layers': {'3': {'complete': True, 'judged': 1, 'results': [{'claim_id': 'other'}]}}}
    with pytest.raises(ValueError, match='population'):
        module.validate_population(audit, ['c'], ['v'])
