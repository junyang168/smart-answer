from copy import deepcopy
import pytest

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline import viewpoint_audit_owner_resolution as resolution
from backend.pipeline import viewpoint_audit_source_adjudication as adjudication
from backend.pipeline.viewpoint_cvp_campaign_runner import validate_audit
from backend.pipeline.viewpoint_resolution_runtime import write_immutable, read_artifact
from backend.tests.test_viewpoint_deferred_source_check import Adapter


def seal(body):
    return body | {'artifact_sha256': sha256_json(body)}


def setup(tmp_path, monkeypatch, verdict='audit_objection_rejected', layer='4'):
    source = tmp_path / 'source.md'
    source.write_text('自己的原文明确解释。')
    row = {'viewpoint_id': 'v', 'revision_id': 'r', 'claim_ids_in_question': ['c'],
           'verdict': 'disputed', 'reason': '原异议'}
    claim_row = {'claim_id': 'c', 'verdict': 'supported'}
    if layer == '3':
        row['verdict'] = 'same'
        claim_row['verdict'] = 'disputed'
    audit = {'meta': {'model': 'gemini-3.8-flash'}, 'layers': {
        '3': {'complete': True, 'judged': 1, 'disputed': int(layer == '3'), 'results': [claim_row]},
        '4': {'complete': True, 'judged': 1, 'disputed': int(layer == '4'), 'results': [row]}}}
    audit_path = tmp_path / 'audit.json'
    write_immutable(audit_path, audit)
    item_id = '4:v:c' if layer == '4' else '3:c:c'
    packet = seal({'audit_binding': resolution.binding(audit_path), 'items': [{
        'item_id': item_id, 'claim': {'claim_id': 'c'}, 'viewpoint': {'viewpoint_id': 'v'},
        'sources': [{'source_id': 's', **resolution.binding(source),
                     'paragraphs': [{'paragraph_key': 'S0001', 'text': '自己的原文明确解释。'}]}]}]})
    answer = {'findings': [{'item_id': item_id, 'verdict': verdict, 'source_id': 's',
                           'paragraph_key': 'S0001', 'exact_quote': '原文明确解释',
                           'reason': '回应原异议，自己的原文明确解释。'}]}
    adapter = Adapter(answer)
    adapter.model_id = 'gpt-6.1-sol'
    adapter.backend = 'codex_subscription'
    monkeypatch.setattr(adjudication, 'build_packet', lambda *args: deepcopy(packet))
    monkeypatch.setattr(resolution, 'build_packet', lambda *args: deepcopy(packet))
    providers = []
    monkeypatch.setattr(adjudication, 'subscription_client', lambda *args: providers.append(args))
    monkeypatch.setattr(adjudication, 'source_review_adapter', lambda client, cache: adapter)
    root = tmp_path / 'adjudication'
    out = tmp_path / 'resolution.json'
    return audit, audit_path, root, out, adapter, providers, packet, source


@pytest.mark.parametrize('layer', ['3', '4'])
def test_gpt_final_without_opus_or_gemini_recheck(tmp_path, monkeypatch, layer):
    audit, audit_path, root, out, adapter, providers, packet, source = setup(tmp_path, monkeypatch, layer=layer)
    with pytest.raises(ValueError):
        validate_audit(audit, ['c'], ['v'])
    proof = resolution.create_gpt_resolution(audit_path, root, out, object())
    assert adapter.calls == 1
    assert providers == [('codex', 'gpt-6.1-sol', 'high')]
    assert not (root / 'raw-opus.json').exists()
    assert proof['approval_basis'] == 'owner_authorized_gpt_source_final'
    assert proof['master_data_mutations'] == 0
    validate_audit(audit, ['c'], ['v'], owner_resolution_path=out)
    assert audit['layers'][layer]['results'][0]['verdict'] == 'disputed'
    # A second consumer must reuse the cached answer, not initiate a new call.
    resolution.create_gpt_resolution(audit_path, root, out, object())
    assert adapter.calls == 1


@pytest.mark.parametrize('verdict', ['unresolved', 'audit_objection_upheld'])
def test_upheld_and_unresolved_never_grant_completion(tmp_path, monkeypatch, verdict):
    audit, audit_path, root, out, adapter, providers, packet, source = setup(tmp_path, monkeypatch, verdict)
    with pytest.raises(ValueError, match='upheld/unresolved'):
        resolution.create_gpt_resolution(audit_path, root, out, object())
    assert adapter.calls == 1
    assert not out.exists()
    assert (root / 'raw-gpt.json').exists()


@pytest.mark.parametrize('mutation', ['foreign_quote', 'missing', 'duplicate', 'empty_reason'])
def test_bad_answer_kept_and_never_retried(tmp_path, monkeypatch, mutation):
    audit, audit_path, root, out, adapter, providers, packet, source = setup(tmp_path, monkeypatch)
    if mutation == 'foreign_quote': adapter.raw['findings'][0]['exact_quote'] = '不是原文'
    elif mutation == 'missing': adapter.raw['findings'] = []
    elif mutation == 'duplicate': adapter.raw['findings'] *= 2
    else: adapter.raw['findings'][0]['reason'] = ''
    with pytest.raises(ValueError): resolution.create_gpt_resolution(audit_path, root, out, object())
    assert adapter.calls == 1 and not out.exists()
    assert (root / 'raw-gpt.json').exists()


@pytest.mark.parametrize('mutation', ['raw', 'source', 'audit', 'packet', 'policy'])
def test_physical_proof_drift_rejected_by_all_consumers(tmp_path, monkeypatch, mutation):
    audit, audit_path, root, out, adapter, providers, packet, source = setup(tmp_path, monkeypatch)
    resolution.create_gpt_resolution(audit_path, root, out, object())
    if mutation == 'source': source.write_text('改变后的原文')
    elif mutation == 'raw': (root / 'raw-gpt.json').write_text('{}')
    elif mutation == 'packet': (root / 'packet.json').write_text('{}')
    elif mutation == 'policy':
        monkeypatch.setattr(resolution, 'GPT_POLICY', tmp_path / 'other-policy.json')
        write_immutable(tmp_path / 'other-policy.json', {'policy_id': 'other'})
    else: audit['layers']['4']['results'][0]['reason'] = '新异议'
    with pytest.raises(ValueError): validate_audit(audit, ['c'], ['v'], owner_resolution_path=out)


def test_completed_source_prepare_consumes_same_gpt_proof(tmp_path, monkeypatch):
    from backend.pipeline.viewpoint_reviewed_cvp_prepare import validate_completed_parent_audit
    audit, audit_path, root, out, adapter, providers, packet, source = setup(tmp_path, monkeypatch)
    proof = resolution.create_gpt_resolution(audit_path, root, out, object())
    row = {'group_key': 'g', 'audit_path': str(audit_path), 'viewpoint_ids': ['v'],
           'owner_resolution_path': str(out), 'bindings': [resolution.binding(audit_path),
                                                         resolution.binding(out), *proof['bindings']]}
    assert validate_completed_parent_audit(row, {'g': {'claim_ids': ['c']}}) == ['c']


def test_cached_dual_review_reuses_only_gpt_and_preserves_old_handoff(tmp_path, monkeypatch):
    audit, audit_path, root, out, adapter, providers, packet, source = setup(tmp_path, monkeypatch)
    adjudication.run(audit_path, root, object(), [adapter, Adapter(adapter.raw)])
    old = (root / 'handoff.json').read_bytes()
    assert adapter.calls == 1
    resolution.create_gpt_resolution(audit_path, root, out, object())
    assert adapter.calls == 1
    assert (root / 'handoff.json').read_bytes() == old
    assert (root / 'raw-opus.json').exists()
