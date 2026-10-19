"""Detail screening is triggered by actual oversized model packets."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.api.canonical_repository.cvp_recall import seal
from backend.pipeline import cvp_candidate_screening as screening
from backend.pipeline.viewpoint_resolution_runtime import write_immutable
from test_cvp_materials import MaterialStore, material


def test_filter_input_carries_member_evidence_and_relevant_source_section():
    bundle = material(MaterialStore(members=1), member_support_ids=['C1'])
    packet = screening._payload(bundle, 'C0', ['V1'])
    assert packet['claim']['claim_id'] == 'C0'
    assert packet['candidates'][0]['cvp_id'] == 'V1'
    assert packet['candidates'][0]['members'][0]['claim_id'] == 'C1'
    assert packet['candidates'][0]['members'][0]['evidence'][0]['excerpts']
    assert packet['source_sections']
    broken = {**bundle, 'material': {**bundle['material'], 'sources': []}}
    with pytest.raises(ValueError, match='relevant original section'):
        screening._payload(broken, 'C0', ['V1'])


def test_largest_recall_is_screened_first_and_stops_when_packet_fits(monkeypatch, tmp_path):
    original = {'C1': {'V1', 'V2', 'V3'}, 'C2': {'V4', 'V5'}}
    recall = seal({'claim_candidates': [
        {'claim_id': cid, 'viewpoint_ids': sorted(vids)} for cid, vids in original.items()]})
    preflight = {'recall_plans': [{'group_key': 'g', 'plan': {'artifact_sha256': 'plan'}}],
                 'existing_links': []}
    request = SimpleNamespace(maintenance_viewpoint_ids=[], permissions=[], input={},
                              budget=SimpleNamespace(max_request_bytes=120))
    class Store:
        def read_cvp_links(self, **kwargs):
            return {}
    def combine(plan, selected, **kwargs):
        return seal({'claim_candidates': [
            {'claim_id': cid, 'viewpoint_ids': sorted(vids)} for cid, vids in selected.items()]})
    def prepare(store, preflight, group, current, **kwargs):
        vids = {vid for row in current['claim_candidates'] for vid in row['viewpoint_ids']}
        return seal({'candidate_viewpoint_ids': sorted(vids), 'material': {'screening': None}})
    class Adapter:
        def request_bytes(self, packet):
            return len(packet['material']['viewpoint_ids']) * 30
    monkeypatch.setattr(screening, 'combine_recall_candidates', combine)
    monkeypatch.setattr(screening, 'prepare_materials', prepare)
    monkeypatch.setattr(screening, '_payload', lambda bundle, cid, vids: {'claim': {'claim_id': cid}, 'candidates': vids})
    monkeypatch.setattr(screening.domain, 'proposal_packet',
                        lambda request, bundle: {'material': {'viewpoint_ids': bundle['candidate_viewpoint_ids']}})
    monkeypatch.setattr(screening, 'management_adapter', lambda *args, **kwargs: Adapter())
    calls = []
    def call(model, packet, path):
        cid = packet['claim']['claim_id']
        calls.append(cid)
        assert cid == 'C1'
        write_immutable(Path(path), seal({'trial': cid}))
        return {'decisions': [{'cvp_id': vid, 'decision': 'retain' if vid == 'V1' else 'exclude',
                               'reason': '具体判断不同'} for vid in packet['candidates']]}, 1, 0.0
    narrowed, bundle, size = screening.screen_to_fit(Store(), preflight, 'g', recall, request,
        proposer=SimpleNamespace(), reviewer=SimpleNamespace(model='claude-opus-5-5', backend='claude_subscription'),
        screening_root=tmp_path, call=call)
    assert calls == ['C1']
    assert size == 90
    assert len(bundle['material']['screening']['excluded']) == 2
    assert next(r for r in narrowed['claim_candidates'] if r['claim_id'] == 'C2')['viewpoint_ids'] == ['V4', 'V5']


def test_action_screening_keeps_original_recall_and_stops_after_actual_packet_fits(monkeypatch, tmp_path):
    recall = seal({'claim_candidates': [
        {'claim_id': 'C1', 'viewpoint_ids': ['V1', 'V2', 'V3']},
        {'claim_id': 'C2', 'viewpoint_ids': ['V4', 'V5']}]})
    preflight = {'recall_plans': [{'group_key': 'g', 'plan': {'artifact_sha256': 'plan'}}],
                 'existing_links': []}
    request = SimpleNamespace(maintenance_viewpoint_ids=[], permissions=[], input={},
                              budget=SimpleNamespace(max_request_bytes=120))
    class Store:
        def read_cvp_links(self, **kwargs):
            return {}
    monkeypatch.setattr(screening, 'combine_recall_candidates',
        lambda plan, selected, **kwargs: seal({'claim_candidates': [
            {'claim_id': cid, 'viewpoint_ids': sorted(vids)} for cid, vids in selected.items()]}))
    monkeypatch.setattr(screening, 'prepare_materials', lambda *args, **kwargs: seal({'material': {}}))
    monkeypatch.setattr(screening, '_payload',
        lambda bundle, cid, vids: {'claim': {'claim_id': cid}, 'candidates': [{'cvp_id': vid} for vid in vids]})
    calls = []
    def fake(model, packet, path):
        calls.append(packet['claim']['claim_id'])
        write_immutable(Path(path), seal({'trial': calls[-1]}))
        return {'decisions': [{'cvp_id': row['cvp_id'], 'decision': 'retain' if row['cvp_id'] == 'V1' else 'exclude',
                              'reason': '具体判断不同'} for row in packet['candidates']]}, 1, 0.0
    sizes = []
    def measure(narrowed, decisions):
        size = sum(len(row['viewpoint_ids']) for row in narrowed['claim_candidates']) * 30
        sizes.append(size)
        return size
    narrowed, decisions, size = screening.screen_expansion_to_fit(
        Store(), preflight, 'g', recall, request,
        reviewer=SimpleNamespace(model='claude-opus-5-5', backend='claude_subscription'),
        screening_root=tmp_path, call=fake, measure=measure,
        applicable_claim_ids={'C1', 'C2'})
    assert calls == ['C1']
    assert sizes == [150, 90]
    assert size == 90 and len(decisions) == 1
    assert len(recall['claim_candidates'][0]['viewpoint_ids']) == 3
    assert next(row for row in narrowed['claim_candidates'] if row['claim_id'] == 'C1')['viewpoint_ids'] == ['V1']


def test_initial_screening_proof_does_not_remove_candidates_twice():
    narrowed = seal({'claim_candidates': [{'claim_id': 'C1', 'viewpoint_ids': ['V1']}]})
    bundle = {'recall_sha256': narrowed['artifact_sha256'],
              'material': {'screening': {'original_recall_sha256': 'older-full-recall',
                                         'excluded': [{'claim_id': 'C1', 'cvp_id': 'V2'}]}}}
    assert screening.effective_recall(narrowed, bundle, {}, 'g') is narrowed
    current = screening.with_screening(bundle, narrowed, {'C1': seal({
        'decisions': [{'cvp_id': 'V1', 'decision': 'exclude', 'reason': '不同'}]})})
    assert current['material']['initial_screening'] == bundle['material']['screening']
    assert current['material']['screening']['original_recall_sha256'] == narrowed['artifact_sha256']
