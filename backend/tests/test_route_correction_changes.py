"""Tiny edits expand to a complete proposal without copying unrelated bindings."""
from copy import deepcopy
import json

import pytest
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.api.canonical_repository.viewpoint_observation_support import (
    ArgumentRouteProposalV3Response, ArgumentRouteReviewV3Response, ArgumentRouteReconsiderationV3Response,
)
from backend.pipeline.viewpoint_route_correction_changes import (
    VERSION, MARKER, CorrectionChanges, correction_adapter, expand_correction,
)
from backend.pipeline.viewpoint_route_cvp_resolution import run_cvp_route_scope
from backend.pipeline.viewpoint_route_resolution_worker import OwnershipGuardedAdapter
from test_viewpoint_observation_support import Answer, approved, fixture_packet, observation_proposal


def test_empty_collections_receive_actual_creation_fields_without_program_keys():
    from backend.pipeline.viewpoint_route_update_projection import update_baseline
    from backend.pipeline.viewpoint_route_correction_changes import with_creation_shapes, CREATION_SHAPES
    baseline=update_baseline(fixture_packet())
    result=with_creation_shapes({'route_proposal_baseline':baseline})[CREATION_SHAPES]
    route=result['collections']['argument_route_candidates']
    assert 'ordered_inference_nodes' in route['required'] and 'conclusion_ref' in route['required']
    assert 'local_route_key' not in route['properties']
    attestation=result['collections']['source_route_attestations']
    assert 'claim_ids' in attestation['required']
    assert not {'local_attestation_key','source_revision_sha256'} & set(attestation['properties'])
    def check_refs(value):
        if isinstance(value,dict):
            if '$ref' in value:assert value['$ref'].rsplit('/',1)[1] in result['$defs']
            for child in value.values():check_refs(child)
        elif isinstance(value,list):
            for child in value:check_refs(child)
    check_refs(result)
    assert 'CCK-' not in json.dumps(result)


def delta(edits):
    return {'schema_version': VERSION, 'finding_dispositions': [
        {'target_kind': 'observation', 'target_key': 'OBS-1', 'disposition': 'accepted',
         'reason': '只修改实际支持范围，保留其余证据'}], 'edits': edits}


def edit(action='update', collection='observation_supports', key='OBS-1', fields=None):
    return {'action': action, 'collection': collection, 'key': key,
        'fields_json': json.dumps(fields, ensure_ascii=False) if fields is not None else None}


def expand(edits):
    packet = fixture_packet()
    proposal = ArgumentRouteProposalV3Response.model_validate(observation_proposal(packet))
    review = ArgumentRouteReviewV3Response.model_validate({
        'route_proposal_sha256': sha256_json(proposal.model_dump(mode='json')),
        'route_evidence_packet_sha256': packet['packet_sha256'],
        'change_reviews': [{'target_kind': 'observation', 'target_key': 'OBS-1',
            'decision': 'pass', 'reason': 'test'}], 'cross_source_composition_found': False, 'reason': 'test'})
    return expand_correction(delta(edits), proposal, review, packet), proposal, packet


def test_one_field_preserves_every_other_field_and_program_binds_identity():
    result, before, _ = expand([edit(fields={'supports': '仅支持这一经文用例'})])
    expected = before.model_dump(mode='json')
    expected['observation_supports'][0]['supports'] = '仅支持这一经文用例'
    assert result['revised_proposal'] == expected
    assert result['route_proposal_sha256'] == sha256_json(before.model_dump(mode='json'))
    assert 'route_proposal_sha256' not in CorrectionChanges.model_json_schema()['properties']


def test_creation_removal_and_retirement_are_explicit_and_not_sql_operations():
    source = observation_proposal(fixture_packet())['observation_supports'][0]
    fields = {k: v for k, v in source.items() if k != 'local_observation_key'}
    fields['observation_summary'] = '另一真实观察'
    result, _, _ = expand([edit('remove'), edit('create', key='OBS-2', fields=fields),
        edit('create', collection='retired_observation_support_ids', key='VOS-old')])
    assert [o['local_observation_key'] for o in result['revised_proposal']['observation_supports']] == ['OBS-2']
    assert result['revised_proposal']['retired_observation_support_ids'] == ['VOS-old']


@pytest.mark.parametrize('edits', [
    [edit(fields={'local_observation_key': 'renamed'})],
    [edit(fields={'scope_label': 'other-scope'})],
    [edit(key='missing', fields={'supports': 'x'})],
    [edit('create', fields={'supports': 'x'})],
    [edit('remove', key='missing')],
    [edit('remove', fields={'supports': 'x'})],
    [edit(fields={'supports': 'x'}), edit('remove')],
    [edit(fields={'supports': '将该词形作为观点的语言依据。'})],
    [edit('create', key='OBS-2', fields={'supports': 'missing required content'})],
    [edit('update', collection='retired_route_revision_ids', key='unknown', fields={})],
    [{'collection': 'observation_supports', 'key': 'OBS-1', 'action': 'update',
      'fields_json': '{"supports":"one","supports":"two"}'}],
])
def test_invalid_edits_cannot_change_scope_unknown_records_or_keep_ambiguous_fields(edits):
    with pytest.raises((ValueError, TypeError, KeyError)):
        expand(edits)


class Client:
    model = 'gpt-test'
    backend = 'stub_subscription'
    reasoning_effort = 'high'
    def __init__(self, response):
        self.response, self.requests = response, []
    def generate_json(self, **request):
        self.requests.append(request)
        return self.response


def adapter(client):
    return StructuredJsonReviewerAdapter(client=client, prompt='Legacy complete replacement',
        response_model=ArgumentRouteReconsiderationV3Response, schema_name='legacy-correction')


def test_small_model_answer_is_expanded_before_full_review_and_replays_without_calls(tmp_path):
    packet = fixture_packet()
    original = observation_proposal(packet)
    def review(p):
        answer = approved(p)
        if reviewer.calls == 1:
            row = next(r for r in answer['change_reviews'] if r['target_kind'] == 'observation')
            row.update(decision='correct', finding_codes=['scope'], reason='收窄实际范围', correction='保留观察、收窄 supports')
        else:
            full = p['route_proposal_context']
            assert full['viewpoints_with_no_route'] == original['viewpoints_with_no_route']
            assert full['observation_supports'][0]['evidence_bindings'] == original['observation_supports'][0]['evidence_bindings']
            assert full['observation_supports'][0]['supports'] == '仅支持本经文'
        return answer
    client = Client(delta([edit(fields={'supports': '仅支持本经文'})]))
    guards = []
    corrector = OwnershipGuardedAdapter(adapter(client), lambda: guards.append('checked'))
    reviewer = Answer(review, model_id='opus')
    args = dict(route_packet=packet, existing_routes=[], output_dir=tmp_path,
        proposer=Answer(original, model_id='gpt'), reviewer=reviewer,
        reconsiderer=corrector, max_correction_rounds=1)
    result = run_cvp_route_scope(**args)
    assert result['rounds'][-1]['outcome'] == 'pass' and len(client.requests) == 1
    assert len(guards) == 2  # Adapter copies must keep the queue ownership fence.
    raw = json.loads((tmp_path/'raw-route-reconsideration-000.json').read_text())
    expanded = json.loads((tmp_path/'route-reconsideration-000.json').read_text())
    assert 'revised_proposal' not in raw['response']
    assert expanded['correction_expansion']['raw_response_sha256'] == sha256_json(raw['response'])
    saved = {p.name: p.read_bytes() for p in tmp_path.glob('*.json')}
    run_cvp_route_scope(**args)
    assert len(client.requests) == 1 and reviewer.calls == 2
    assert saved == {p.name: p.read_bytes() for p in tmp_path.glob('*.json')}


@pytest.mark.parametrize('suffix', ['.json', '.call-intent.json', '.request-encoding.json'])
def test_existing_legacy_dispatch_cannot_be_silently_upgraded(tmp_path, suffix):
    (tmp_path/('raw'+suffix)).write_text('{}')
    old = adapter(Client({}))
    assert correction_adapter(old, tmp_path/'packet.json', tmp_path/'raw.json') is old


def test_saved_legacy_packet_also_retains_original_output_schema(tmp_path):
    path = tmp_path/'packet.json'; path.write_text('{}')
    old = adapter(Client({}))
    assert correction_adapter(old, path, tmp_path/'raw.json') is old


def test_compact_contract_cannot_fall_back_to_full_replacement():
    with pytest.raises(ValueError, match='output contract'):
        expand_correction({'schema_version': 'wang_argument_route_reconsideration_v3'},
            None, None, None, expected_contract=VERSION)


def test_connection_recovery_authenticates_compact_raw_and_expanded_proposal(tmp_path, monkeypatch):
    import test_route_connection_context as case
    from backend.pipeline import viewpoint_route_connection_recovery as recovery
    original_answer = case.Answer
    client = Client(delta([edit(fields={'supports': '限定当前经文的实际作用'})]))
    corrector = adapter(client)
    def answers(response, **kwargs):
        if callable(response) and kwargs.get('model_id') == 'gpt':
            return corrector
        return original_answer(response, **kwargs)
    monkeypatch.setattr(case, 'Answer', answers)
    packet, material, proposer, reviewer, _ = case.saved_exception(tmp_path, monkeypatch, rounds=1)
    before = {p.name: p.read_bytes() for p in tmp_path.glob('*.json')}
    seed = recovery.prepare_connection_continuation(tmp_path, packet, material,
        proposer=proposer, reviewer=reviewer, reconsiderer=corrector, max_correction_rounds=1)
    assert seed['receipt']['previous_corrections_consumed'] == 1
    assert len(client.requests) == 1
    assert all((tmp_path/name).read_bytes() == value for name, value in before.items())
