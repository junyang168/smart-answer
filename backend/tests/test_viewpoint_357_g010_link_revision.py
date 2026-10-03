from copy import deepcopy

import pytest

from backend.pipeline.viewpoint_357_g010_link_revision import SPECS, validate, package_for


def fixture():
    states, targets, repairs = {}, [], []
    for i, (lid, (cv, rev, cid)) in enumerate(SPECS.items()):
        statement = '耶穌接受約翰的洗，是要與世上的人連在一起，從而替他們背負罪債。' if i == 0 else '「義」不是審判、滅罪，而是與神建立好關係；這種關係需要相應的具體行動。'
        claim = {'claim_id': cid, 'revision': 1, 'statement': statement}
        link = {'viewpoint_claim_link_id': lid, 'claim_id': cid, 'viewpoint_id': cv, 'validated_against_viewpoint_revision_id': rev, 'revision': 1, 'decision_id': 'old-'+lid, 'evidence_bindings': [{'evidence_step_id': 'E1', 'source_fragment_id': 'F1'}], 'component_locator': {}, 'link_type': 'equivalent_component'}
        decision = {'identity_candidate_id': 'candidate-'+lid}
        states['claims', cid] = claim
        states['viewpoint_claim_links', lid] = link
        states['viewpoint_identity_decisions', link['decision_id']] = decision
        states['viewpoint_identity_candidates', decision['identity_candidate_id']] = {'seed_relation_ids': []}
        targets.append({'link': link, 'claim': claim, 'available_evidence': [{'step': {'evidence_step_id': 'E1'}, 'fragment': {'fragment_id': 'F1'}}, {'step': {'evidence_step_id': 'E2'}, 'fragment': {'fragment_id': 'F2'}}]})
        repairs.append({'link_id': lid, 'link_type': 'equivalent_full' if i == 0 else 'applies', 'component_text': statement if i == 0 else '而是與神建立好關係', 'reason': '逐字上下文支持保留限定与真实角色'})
    return states, {'targets': targets}, {'repairs': repairs}


def test_bounded_package_preserves_every_other_record(tmp_path):
    states, packet, proposal = fixture(); before = deepcopy(states)
    package = package_for(states, packet, proposal, {'accepted_call_artifact_shas': ['a', 'b']}, tmp_path)
    assert states == before
    assert set(package) == {'package_id', 'viewpoint_claim_links', 'viewpoint_identity_candidates', 'viewpoint_identity_decisions'}
    first, second = package['viewpoint_claim_links']
    assert first['component_locator'] is None
    assert len(first['evidence_bindings']) == 2
    assert second['link_type'] == 'applies'
    assert second['component_locator']['statement_component'] == '而是與神建立好關係'
    assert second['evidence_bindings'] == states['viewpoint_claim_links', second['viewpoint_claim_link_id']]['evidence_bindings']
    assert all(d['approval_basis'] == 'dual_model_consensus' and d['review_status'] == 'system_approved' for d in package['viewpoint_identity_decisions'])


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'partial', 'forced_support', 'wide_equivalence', 'foreign_text', 'drop_god'])
def test_rejects_scope_shortcuts(mutation):
    _, packet, proposal = fixture()
    if mutation == 'missing': proposal['repairs'].pop()
    if mutation == 'duplicate': proposal['repairs'][1] = deepcopy(proposal['repairs'][0])
    if mutation == 'partial': proposal['repairs'][0]['component_text'] = '從而替他們背負罪債。'
    if mutation == 'forced_support': proposal['repairs'][1]['link_type'] = 'supports'
    if mutation == 'wide_equivalence': proposal['repairs'][1]['link_type'] = 'equivalent_component'
    if mutation == 'foreign_text': proposal['repairs'][1]['component_text'] = '與所有人建立好關係'
    if mutation == 'drop_god': proposal['repairs'][1]['component_text'] = '這種關係需要相應的具體行動。'
    with pytest.raises(ValueError): validate(proposal, packet)
