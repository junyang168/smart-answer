from copy import deepcopy
import pytest
from backend.pipeline import viewpoint_audit_source_adjudication as module
from backend.pipeline.viewpoint_resolution_runtime import write_immutable, read_artifact
from backend.tests.test_viewpoint_deferred_source_check import Adapter


def packet():
    return {'artifact_sha256': 'pin', 'items': [{'item_id': '4:v:c', 'sources': [
        {'source_id': 's', 'paragraphs': [{'paragraph_key': 'S0001', 'text': '王就是這位彌賽亞。'}]}]}]}


def test_new_subset_never_resends_unchanged_known_target(tmp_path,monkeypatch):
    from backend.api.canonical_repository.postgres_store import sha256_json
    full=packet();known=deepcopy(full['items'][0]);known['item_id']='4:known:c'
    full['items'].append(known)
    full={k:v for k,v in full.items() if k!='artifact_sha256'}
    full={**full,'artifact_sha256':sha256_json(full)}
    monkeypatch.setattr(module,'build_packet',lambda *args:full)
    adapter=Adapter(answer())
    report=module.run(tmp_path/'audit.json',tmp_path/'out',object(),[adapter],final_gpt=True,
                      history_roots=[],only_item_ids={'4:v:c'})
    assert len(adapter.payloads)==1
    assert [i['item_id'] for i in adapter.payloads[0]['items']]==['4:v:c']
    assert len(report['actions'])==1
    with pytest.raises(ValueError):module.restricted_packet(full,{'foreign'})


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
    with pytest.raises(ValueError, match='configured ceiling'):
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


@pytest.mark.parametrize('fault', [None, 'missing', 'retired', 'foreign', 'wrong_id',
                                  'missing_scope', 'missing_signature', 'empty_core', 'unapproved'])
def test_complete_adjudication_packet_reads_actual_current_cvr_before_call(tmp_path, monkeypatch, fault):
    from types import SimpleNamespace
    from backend.pipeline.viewpoint_adjudication_history import target_identity
    audit = tmp_path/'audit.json'; audit.write_text('{}')
    row = {'viewpoint_id': 'v', 'revision_id': 'r', 'claim_ids_in_question': ['c']}
    monkeypatch.setattr(module, 'disputes', lambda _: [('4', 'viewpoint_id', row)])
    monkeypatch.setattr(module, 'read_artifact', lambda _: {})
    source = tmp_path/'source.txt'; source.write_text('教授原話。')
    monkeypatch.setattr(module, 'load_context_source', lambda _: (source, 'source-pin',
        SimpleNamespace(body_sha256='body-pin', spoken_rows=[{'text': '教授原話。'}])))
    revision = {'viewpoint_revision_id': 'r', 'viewpoint_id': 'v', 'core_proposition': '現行命題。',
                'scope': {'scripture_scope': ['太17:1–2']}, 'proposition_signature': {'subject': '耶穌'},
                'review_status': 'system_approved', 'provenance': {'review_artifact_sha256': 'review-pin'}}
    records = {
        ('canonical_viewpoints', 'v'): {'viewpoint_id': 'v', 'current_revision_id': 'r'},
        ('viewpoint_revisions', 'r'): revision,
        ('claims', 'c'): {'claim_id': 'c', 'revision': 1, 'statement': '主張。',
                         'evidence_step_ids': ['e'], 'occurrences': [{'source_id': 's'}]},
        ('evidence_steps', 'e'): {'source_fragment_ids': ['f']},
        ('source_fragments', 'f'): {'source_id': 's', 'paragraph_key': 'S0001', 'verbatim_excerpt': '教授原話。'},
        ('source_documents', 's'): {'source_id': 's'},
    }
    if fault == 'foreign': revision['viewpoint_id'] = 'other'
    if fault == 'wrong_id': revision['viewpoint_revision_id'] = 'other'
    if fault == 'missing_scope': revision.pop('scope')
    if fault == 'missing_signature': revision.pop('proposition_signature')
    if fault == 'empty_core': revision['core_proposition'] = ''
    if fault == 'unapproved': revision['review_status'] = 'pending'
    class Store:
        reads = []
        def claim_links(self, ids):
            return [{'claim_id': 'c', 'viewpoint_id': 'v', 'effective_state': 'active',
                     'pinned_claim_revision': 1, 'validated_against_viewpoint_revision_id': 'r',
                     'evidence_bindings': [{'evidence_step_id': 'e', 'source_fragment_id': 'f'}]}]
        def get_record_state(self, collection, identity):
            self.reads.append((collection, identity))
            if fault == 'missing' and collection == 'viewpoint_revisions': return None
            return {'retired': fault == 'retired' and collection == 'viewpoint_revisions',
                    'payload': deepcopy(records[collection, identity])}
    store = Store()
    if fault:
        with pytest.raises(ValueError): module.build_packet(audit, store, True)
        return
    full = module.build_packet(audit, store, True)
    assert full['schema_version'] == 'wang_audit_source_adjudication_packet_v2'
    assert full['items'][0]['viewpoint_revision'] == revision
    assert ('viewpoint_revisions', 'r') in store.reads
    legacy = module.build_packet(audit, store)
    assert 'viewpoint_revision' not in legacy['items'][0]
    assert target_identity(full['items'][0]) == target_identity(legacy['items'][0])
    assert module.build_packet_for_saved(audit, store, legacy) == legacy
    assert module.build_packet_for_saved(audit, store, full) == full


def test_adding_missing_cvr_does_not_grant_another_model_budget(tmp_path, monkeypatch):
    from backend.pipeline import viewpoint_adjudication_history as history
    legacy = packet(); legacy['items'][0]['viewpoint'] = {'current_revision_id': 'r'}
    full = deepcopy(legacy); full['items'][0]['viewpoint_revision'] = {'viewpoint_revision_id': 'r', 'scope': {}}
    monkeypatch.setattr(history, 'read_artifact', lambda _: legacy)
    monkeypatch.setattr(history, 'validated_history', lambda _: (legacy, {
        legacy['items'][0]['item_id']: {'verdict': 'unresolved'}}))
    with pytest.raises(history.AdjudicationAlreadyRecorded): history.assert_no_readjudication(full, [tmp_path])
