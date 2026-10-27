"""Program-bound obsolete versions still need complete independent review."""
from copy import deepcopy
import json

import pytest

from backend.api.canonical_repository.viewpoint_observation_support import (
    ArgumentRouteProposalV3Response, bind_revision_retirements,
    compile_observation_package, revision_retirement_binding,
)
from backend.api.canonical_repository.viewpoint_batch_resolution import BatchResolutionError
from backend.pipeline.viewpoint_route_cvp_resolution import CvpRouteReviewIncomplete, run_cvp_route_scope
from backend.pipeline.viewpoint_route_resolution import build_registry_route_packet
from test_viewpoint_observation_support import (
    Answer, approved, fixture_packet, observation_proposal, validate,
)
from test_viewpoint_batch_resolution import _claim, _registry_link, _approved_viewpoint, ROCK_STATEMENT


def revised_packet(*, same_revision=False, binding=True, claim_revision=1, stale_source=False):
    old = fixture_packet()
    old_proposal = ArgumentRouteProposalV3Response.model_validate(observation_proposal(old))
    observations, _, _ = compile_observation_package(old_proposal, old, review_sha256='old-review')
    claim = _claim('C1', ROCK_STATEMENT)
    if stale_source:
        claim = claim.model_copy(update={'evidence':[
            e.model_copy(update={'source_sha256':'source-sha-v2'}) for e in claim.evidence]})
    if claim_revision != 1:
        claim = claim.model_copy(update={'pinned_claim_revision':claim_revision,
            'claim_revision_sha256':'sha-C1-v2'})
    revision = 'CVR-1' if same_revision else 'CVR-2'
    packet = build_registry_route_packet(scope_label='observation-test',
        approved_viewpoints=[_approved_viewpoint() | {'viewpoint_revision_id':revision}],
        claims=[claim], viewpoint_claim_links=[_registry_link(claim, component_locator=None) |
            {'validated_against_viewpoint_revision_id':revision}], existing_routes=[],
        complete_cvp=True, observation_supports=True, claim_evidence_for_intermediate_steps=True,
        existing_observation_supports=observations, program_bound_observation_retirements=binding)
    return packet, observations[0]['observation_support_id']


def test_new_revision_ids_are_program_bound_before_first_full_review(tmp_path):
    packet, obsolete = revised_packet()
    raw = observation_proposal(packet)
    assert raw['retired_observation_support_ids'] == []
    proposer = Answer(deepcopy(raw), model_id='gpt')
    reviewer = Answer(approved, model_id='opus')
    result = run_cvp_route_scope(route_packet=packet, existing_routes=[], output_dir=tmp_path,
        proposer=proposer, reviewer=reviewer, reconsiderer=Answer({}), max_correction_rounds=0)
    assert proposer.calls == reviewer.calls == 1
    assert result['proposal_validation_repairs'] == 0
    effective = result['_effective_proposal']
    assert effective.retired_observation_support_ids == [obsolete]
    assert effective.observation_supports == ArgumentRouteProposalV3Response.model_validate(raw).observation_supports
    assert ('observation_retirement', obsolete) in {
        (t['target_kind'], t['target_key']) for t in reviewer.payloads[0]['review_targets']}
    saved = json.loads((tmp_path/'raw-route-proposal-000.json').read_text())
    assert saved['response'] == raw  # No rewriting a provider response to pass.
    receipt = json.loads(next((tmp_path/'observation-retirement-bindings').glob('*.json')).read_text())
    assert receipt['program_added_retirement_ids'] == [obsolete]
    assert receipt['requires_independent_review'] is True
    created, retired, unchanged = compile_observation_package(effective, packet, review_sha256='new-review')
    assert len(created) == 1 and not unchanged
    assert retired == [('viewpoint_observation_supports', obsolete)]
    replay = run_cvp_route_scope(route_packet=packet, existing_routes=[], output_dir=tmp_path,
        proposer=proposer, reviewer=reviewer, reconsiderer=Answer({}), max_correction_rounds=0)
    assert replay['proposal_sha256'] == result['proposal_sha256']
    assert proposer.calls == reviewer.calls == 1


def test_legacy_packet_keeps_original_explicit_retirement_contract():
    packet, _ = revised_packet(binding=False)
    assert 'observation_retirement_binding' not in packet
    raw = observation_proposal(packet)
    proposal = ArgumentRouteProposalV3Response.model_validate(raw)
    assert bind_revision_retirements(proposal, packet) == (proposal, None)
    with pytest.raises(BatchResolutionError, match='explicitly retire'):
        validate(packet, raw)


def test_current_revision_observation_is_not_silently_retired(tmp_path):
    packet, _ = revised_packet(same_revision=True)
    assert packet['observation_retirement_binding']['required_retirement_ids'] == []
    raw = observation_proposal(packet)
    raw['observation_supports'][0]['supports'] = 'Different semantic replacement needs an explicit decision.'
    reviewer = Answer(approved, model_id='opus')
    with pytest.raises(CvpRouteReviewIncomplete, match='explicitly retire'):
        run_cvp_route_scope(route_packet=packet, existing_routes=[], output_dir=tmp_path,
            proposer=Answer(raw, model_id='gpt'), reviewer=reviewer,
            reconsiderer=Answer({}), max_correction_rounds=0)
    assert reviewer.calls == 0


def test_forged_retirement_binding_and_foreign_viewpoint_are_rejected():
    packet, _ = revised_packet()
    proposal = ArgumentRouteProposalV3Response.model_validate(observation_proposal(packet))
    packet['observation_retirement_binding']['required_retirement_ids'].append('foreign')
    with pytest.raises(ValueError, match='differs from current complete material'):
        bind_revision_retirements(proposal, packet)
    foreign = deepcopy(packet['existing_observation_supports'])
    foreign[0]['viewpoint_id'] = 'unrelated-CVP'
    with pytest.raises(ValueError, match='foreign CVP'):
        revision_retirement_binding(packet['approved_viewpoints'], foreign)


def test_binding_does_not_hide_foreign_model_retirement():
    packet, _ = revised_packet()
    raw = observation_proposal(packet)
    raw['retired_observation_support_ids'] = ['foreign']
    proposal, _ = bind_revision_retirements(ArgumentRouteProposalV3Response.model_validate(raw), packet)
    with pytest.raises(BatchResolutionError, match='retirement is outside'):
        validate(packet, proposal.model_dump(mode='json'))


def test_binding_does_not_make_missing_retirement_review_pass(tmp_path):
    packet, _ = revised_packet()
    def omit_retirement_review(p):
        result = approved(p)
        result['change_reviews'] = [r for r in result['change_reviews'] if r['target_kind'] != 'observation_retirement']
        return result
    with pytest.raises(BatchResolutionError, match='no route review decision'):
        run_cvp_route_scope(route_packet=packet, existing_routes=[], output_dir=tmp_path,
            proposer=Answer(observation_proposal, model_id='gpt'),
            reviewer=Answer(omit_retirement_review, model_id='opus'),
            reconsiderer=Answer({}), max_correction_rounds=0)


def test_correction_does_not_need_to_copy_forced_retirement_ids(tmp_path):
    packet, obsolete = revised_packet()
    original = observation_proposal(packet)
    revised = deepcopy(original)
    revised['observation_supports'][0]['support_scope_note'] = '仅记录原文实际展示的观察。'
    def first_review(p):
        raw = approved(p)
        row = next(r for r in raw['change_reviews'] if r['target_kind'] == 'observation')
        row.update(decision='correct', finding_codes=['scope_overstates'],
            reason='须明确观察范围', correction='收窄范围说明')
        return raw
    def correction(p):
        return {'schema_version':'wang_argument_route_reconsideration_v3',
            'route_proposal_sha256':p['route_proposal_sha256'],
            'route_review_sha256':p['route_review_sha256'],
            'finding_dispositions':[{'target_kind':'observation', 'target_key':'OBS-1',
                'disposition':'accepted', 'reason':'已收窄实际支持范围'}],
            'revised_proposal':revised}
    reviewer = Answer(lambda p:first_review(p) if reviewer.calls == 1 else approved(p), model_id='opus')
    corrector = Answer(correction, model_id='gpt')
    result = run_cvp_route_scope(route_packet=packet, existing_routes=[], output_dir=tmp_path,
        proposer=Answer(original, model_id='gpt'), reviewer=reviewer,
        reconsiderer=corrector, max_correction_rounds=1)
    assert reviewer.calls == 2 and corrector.calls == 1
    assert result['_effective_proposal'].retired_observation_support_ids == [obsolete]
    assert all(('observation_retirement', obsolete) in {
        (t['target_kind'], t['target_key']) for t in p['review_targets']} for p in reviewer.payloads)
    assert revised['retired_observation_support_ids'] == []


@pytest.mark.parametrize('change', ['claim', 'source'])
def test_unchanged_cvp_revision_still_replaces_obsolete_dependency_pins(tmp_path, change):
    packet, obsolete = revised_packet(same_revision=True,
        claim_revision=2 if change == 'claim' else 1, stale_source=change == 'source')
    raw = observation_proposal(packet)
    assert raw['retired_observation_support_ids'] == []
    reviewer = Answer(approved, model_id='opus')
    result = run_cvp_route_scope(route_packet=packet, existing_routes=[], output_dir=tmp_path,
        proposer=Answer(raw, model_id='gpt'), reviewer=reviewer,
        reconsiderer=Answer({}), max_correction_rounds=0)
    assert result['_effective_proposal'].retired_observation_support_ids == [obsolete]
    assert reviewer.calls == 1


def test_missing_claim_is_not_treated_as_explicitly_unusable():
    packet, obsolete = revised_packet(same_revision=True)
    old = deepcopy(packet['existing_observation_supports'])
    old[0]['evidence_bindings'][0]['claim_id'] = 'C-no-longer-in-context'
    args = dict(claims=packet['claims'], source_revisions=packet['source_revisions'])
    missing = revision_retirement_binding(packet['approved_viewpoints'], old, **args)
    assert missing['required_retirement_ids'] == []
    unavailable = revision_retirement_binding(packet['approved_viewpoints'], old,
        **args, unusable_context_claims=[{'claim_id':'C-no-longer-in-context'}])
    assert unavailable['required_retirement_ids'] == [obsolete]
