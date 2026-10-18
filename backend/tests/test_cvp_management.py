"""CVP2 semantic writes require exact components, authority and finite review."""
from copy import deepcopy
import json

import pytest

from backend.api.canonical_repository import cvp_management as cvp
from backend.api.canonical_repository.cvp_claim_input import compile_input
from backend.api.canonical_repository.cvp_recall import seal
from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha


def materials(*, qualifier=False):
    claim = {'claim_id': 'C1', 'statement': '人子是神。', 'claim_type': 'exegesis',
             'revision': 2, 'evidence_step_ids': ['E1']}
    if qualifier:
        claim['qualifications'] = ['在此語境。']
    policy = {'schema_version': 'wang_cvp_claim_input_v1', 'policy_id': 'test',
              'position_fields': ['statement', 'qualifications'], 'context_fields': ['claim_type'],
              'mechanical_fields': ['claim_id', 'revision', 'evidence_step_ids']}
    records = [('claims', 'C1', claim),
               ('evidence_steps', 'E1', {'evidence_step_id': 'E1', 'source_fragment_id': 'F1'}),
               ('source_fragments', 'F1', {'fragment_id': 'F1', 'source_id': 'S1'})]
    return seal({'schema_version': cvp.MATERIAL_VERSION, 'group_key': 'group-1', 'material': {'claims': [claim]},
        'claim_input': compile_input([claim], policy, claim_refs={'C1': 'c1'}),
        'references': {'claims': {'C1': 'c1'}, 'evidence_steps': {'E1': 'e1'},
                       'source_fragments': {'F1': 'f1'}},
        'records': [{'collection': col, 'object_id': oid, 'payload': payload,
                     'revision': payload.get('revision', 1), 'content_sha256': semantic_record_sha(payload),
                     'retired': False} for col, oid, payload in records],
        'maintenance_viewpoint_ids': []})


def request(**values):
    return cvp.Request.model_validate({'action_id': 'A',
        'input': seal({'schema_version': 'wang_cvp_recall_preflight_v1'}),
        'authorization': {'path': '/exact/authorization.json', 'file_sha256': 'a' * 64},
        'budget': {'max_calls': 6, 'max_revisions': 2, 'max_request_bytes': 500000},
        'creation_slots': {'canonical_viewpoints': 1, 'viewpoint_revisions': 1, 'viewpoint_claim_links': 1},
        **values})


def change(ref, **fields):
    return {'ref': ref, 'fields_json': json.dumps(fields, ensure_ascii=False)}


def proposal():
    return {'component_dispositions': [{'component_ref': 'k1', 'disposition': 'connected',
                'target_ref': 'nv1', 'link_ref': 'nl1', 'reason': '同一判斷。'}],
        'changes': [change('nv1', current_revision_id='nr1'),
                    change('nr1', viewpoint_id='nv1', core_proposition='人子是神。',
                        proposition_signature={'subject': '人子', 'predicate': '是', 'object': '神',
                                               'polarity': 'affirmed', 'modality': 'asserted'}, scope={}),
                    change('nl1', viewpoint_id='nv1', claim_id='c1', link_type='equivalent_full')],
        'summaries': [{'ref': 'nv1', 'summary': '此處的人子稱號指向神性。'}]}


def passing(candidate):
    verdict = lambda refs: [{'ref': ref, 'decision': 'pass', 'finding_refs': []} for ref in refs]
    return {'component_reviews': verdict(candidate['review_sets']['components']),
            'object_reviews': verdict(candidate['review_sets']['objects']),
            'prior_findings': [], 'findings': [], 'novelty_approved': True}


def provenance():
    return {'proposal_model': 'gpt-6.1-sol', 'review_model': 'claude-opus-5-5',
            'proposal_artifact_sha256': 'a' * 64, 'review_artifact_sha256': 'b' * 64,
            'created_at': '2026-10-17T00:00:00Z'}


def test_compact_creation_full_candidate_review_and_package_are_typed_and_pure():
    req, bundle, raw = request(), materials(qualifier=True), proposal()
    before = deepcopy(bundle)
    candidate = cvp.compile_candidate(req, bundle, raw)
    assert len(candidate['component_plan']['components'][0]['spans']) == 2
    assert {row['ref'] for row in candidate['writes']} == {'nv1', 'nr1', 'nl1'}
    link = next(row['payload'] for row in candidate['writes'] if row['ref'] == 'nl1')
    assert link['pinned_claim_revision'] == 2
    assert link['evidence_bindings'] == [{'evidence_step_id': 'E1', 'source_fragment_id': 'F1'}]
    approval = cvp.validate_review(req, bundle, candidate, passing(candidate))
    assert approval['approved'] is True
    package = cvp.finalize_package(req, bundle, candidate, approval, provenance())
    assert len(package['viewpoint_identity_decisions']) == 3
    assert package['viewpoint_revisions'][0]['approved_at'] == provenance()['created_at']
    assert package['metadata']['independent_audit'] == 'not_performed'
    assert bundle == before
    assert cvp.revalidate_candidate(req, bundle, candidate, raw) == candidate
    assert cvp.proposal_packet(req, bundle)['context']['creation_slots']
    assert cvp.review_packet(req, bundle, candidate)['candidate'] == candidate


def test_empty_delta_retains_previous_candidate_without_copying_records():
    req, bundle = request(), materials()
    first = cvp.compile_candidate(req, bundle, proposal())
    second = cvp.compile_candidate(req, bundle, {}, previous=first)
    assert second == first


@pytest.mark.parametrize('bad', ['omit', 'foreign', 'duplicate', 'contradiction'])
def test_review_exact_sets_and_findings_cannot_be_bypassed(bad):
    req, bundle = request(), materials()
    candidate = cvp.compile_candidate(req, bundle, proposal())
    review = passing(candidate)
    if bad == 'omit':
        review['object_reviews'].pop()
    elif bad == 'foreign':
        review['object_reviews'][0]['ref'] = 'unknown'
    elif bad == 'duplicate':
        review['object_reviews'].append(review['object_reviews'][0])
    else:
        review['findings'] = [{'slot': 'f1', 'target_refs': ['k1'], 'category': 'wording', 'reason': '缺限定。'}]
    with pytest.raises(ValueError):
        cvp.validate_review(req, bundle, candidate, review)


def test_unresolved_novelty_prevents_publication_even_when_all_objects_pass():
    req, bundle = request(), materials()
    candidate = cvp.compile_candidate(req, bundle, proposal())
    raw = passing(candidate)
    raw['novelty_approved'] = False
    approval = cvp.validate_review(req, bundle, candidate, raw)
    assert approval['approved'] is False
    with pytest.raises(ValueError, match='passing'):
        cvp.finalize_package(req, bundle, candidate, approval, provenance())


def test_full_text_coverage_and_split_mappings_are_mandatory():
    req, bundle = request(), materials(qualifier=True)
    first = cvp.compile_candidate(req, bundle, proposal())
    split = {'components': [
        {'claim_ref': 'c1', 'spans': [{'text_ref': 't1', 'start_char': 0, 'end_char': 5}]}]}
    with pytest.raises(ValueError, match='uncovered'):
        cvp.compile_candidate(req, bundle, split, previous=first)
    split['components'].append({'claim_ref': 'c1', 'spans': [{'text_ref': 't2', 'start_char': 0, 'end_char': 5}]})
    with pytest.raises(ValueError, match='old-to-new mapping'):
        cvp.compile_candidate(req, bundle, split, previous=first)


def test_old_findings_cannot_be_dropped_or_contradicted_by_pass():
    req, bundle = request(), materials()
    finding = {'finding_id': 'old', 'target_refs': ['k1'], 'category': 'wording', 'reason': '缺限定。'}
    candidate = cvp.compile_candidate(req, bundle, proposal(), prior_findings=[finding])
    review = passing(candidate)
    with pytest.raises(ValueError, match='every old finding'):
        cvp.validate_review(req, bundle, candidate, review)
    review['prior_findings'] = [{'ref': 'pf1', 'disposition': 'persistent', 'reason': '仍缺限定。'}]
    with pytest.raises(ValueError, match='persistent'):
        cvp.validate_review(req, bundle, candidate, review)
    review['prior_findings'][0]['disposition'] = 'resolved'
    assert cvp.validate_review(req, bundle, candidate, review)['approved']


def test_compact_output_cannot_copy_ids_sha_or_create_outside_slots():
    req, bundle = request(), materials()
    for bad in [change('nl1', claim_id='C1'), change('nl1', revision=9), change('unknown', reason='x')]:
        raw = proposal()
        raw['changes'][-1] = bad
        with pytest.raises(ValueError):
            cvp.compile_candidate(req, bundle, raw)


def maintained():
    req, bundle = request(), materials()
    candidate = cvp.compile_candidate(req, bundle, proposal())
    approval = cvp.validate_review(req, bundle, candidate, passing(candidate))
    package = cvp.finalize_package(req, bundle, candidate, approval, provenance())
    updated = deepcopy(bundle)
    updated.pop('artifact_sha256')
    refs = {'canonical_viewpoints': 'v1', 'viewpoint_revisions': 'r1', 'viewpoint_claim_links': 'l1'}
    ids = {}
    for col, ref in refs.items():
        payload = package[col][0]
        oid = payload[cvp.KNOWLEDGE_COLLECTIONS[col][1]]
        ids[col] = oid
        updated['references'][col] = {oid: ref}
        updated['records'].append({'collection': col, 'object_id': oid, 'payload': payload,
            'revision': 1, 'content_sha256': semantic_record_sha(payload), 'retired': False})
    updated['maintenance_viewpoint_ids'] = [ids['canonical_viewpoints']]
    return seal(updated), ids


def maintenance_proposal():
    return {'component_dispositions': [{'component_ref': 'k1', 'disposition': 'existing_coverage',
        'target_ref': 'v1', 'link_ref': 'l1', 'reason': '原判斷保持。'}],
        'member_dispositions': [{'member_ref': 'l1', 'disposition': 'retained', 'reason': '原關係仍成立。'}]}


def test_maintenance_noop_has_no_writes_and_full_member_review():
    bundle, ids = maintained()
    req = request(action_id='maintenance', mode='maintain', maintenance_viewpoint_ids=[ids['canonical_viewpoints']])
    candidate = cvp.compile_candidate(req, bundle, maintenance_proposal())
    assert candidate['writes'] == []
    assert candidate['review_sets']['objects'] == ['l1']
    assert cvp.validate_review(req, bundle, candidate, passing(candidate))['approved']


def test_maintenance_permission_hold_and_noop_cannot_expand_scope():
    bundle, ids = maintained()
    raw = maintenance_proposal()
    raw['changes'] = [change('v1', identity_status='retired')]
    with pytest.raises(ValueError, match='maintenance permission'):
        cvp.compile_candidate(request(action_id='maintenance'), bundle, raw)
    req = request(action_id='maintenance', mode='maintain', maintenance_viewpoint_ids=[ids['canonical_viewpoints']],
        permissions=[{'collection': 'canonical_viewpoints', 'object_id': ids['canonical_viewpoints'], 'fields': ['identity_status']}])
    with pytest.raises(ValueError, match='inactive viewpoint'):
        cvp.compile_candidate(req, bundle, raw)
    raw['changes'] = [change('v1', identity_status='active')]
    candidate = cvp.compile_candidate(req, bundle, raw)
    assert candidate['writes'] == []


def test_logical_retire_requires_member_coverage_disposition_in_same_afterimage():
    bundle, ids = maintained()
    req = request(action_id='maintenance', mode='maintain', maintenance_viewpoint_ids=[ids['canonical_viewpoints']],
        permissions=[{'collection': 'canonical_viewpoints', 'object_id': ids['canonical_viewpoints'], 'fields': ['identity_status']},
                     {'collection': 'viewpoint_claim_links', 'object_id': ids['viewpoint_claim_links'], 'fields': ['effective_state']}])
    raw = {'component_dispositions': [{'component_ref': 'k1', 'disposition': 'background', 'reason': '不再作觀點連接。'}],
           'changes': [change('v1', identity_status='retired'), change('l1', effective_state='retired')],
           'member_dispositions': [{'member_ref': 'l1', 'disposition': 'changed', 'reason': '撤回。'}]}
    with pytest.raises(ValueError, match='coverage gap'):
        cvp.compile_candidate(req, bundle, raw)
    raw['member_dispositions'][0]['disposition'] = 'background'
    candidate = cvp.compile_candidate(req, bundle, raw)
    approval = cvp.validate_review(req, bundle, candidate, passing(candidate))
    package = cvp.finalize_package(req, bundle, candidate, approval, provenance())
    assert package['canonical_viewpoints'][0]['identity_status'] == 'retired'
    assert package['viewpoint_claim_links'][0]['effective_state'] == 'retired'


def test_same_action_different_groups_never_reuse_program_ids():
    req, first_bundle = request(), materials()
    second_bundle = seal({**{k: v for k, v in first_bundle.items() if k != 'artifact_sha256'}, 'group_key': 'group-2'})
    first = cvp.compile_candidate(req, first_bundle, proposal())
    second = cvp.compile_candidate(req, second_bundle, proposal())
    assert {row['object_id'] for row in first['writes']}.isdisjoint(row['object_id'] for row in second['writes'])


def test_source_expansion_rebind_preserves_history_and_invalidates_approval():
    req, bundle = request(), materials()
    candidate = cvp.compile_candidate(req, bundle, proposal())
    approval = cvp.validate_review(req, bundle, candidate, passing(candidate))
    expanded = seal({**{k: v for k, v in bundle.items() if k != 'artifact_sha256'}, 'material': {'claims': [], 'expanded_source': '更多原文。'}})
    updated = cvp.rebind_candidate(req, bundle, expanded, candidate)
    assert updated['artifact_sha256'] != candidate['artifact_sha256']
    assert updated['rebase_history'][0]['candidate_sha256'] == candidate['artifact_sha256']
    with pytest.raises(ValueError, match='exact passing'):
        cvp.finalize_package(req, expanded, updated, approval, provenance())
    assert cvp.validate_review(req, expanded, updated, passing(updated))['approved']


def test_split_qualifier_uses_v2_field_locator_and_preserves_old_component_history():
    req = request(creation_slots={'canonical_viewpoints': 1, 'viewpoint_revisions': 1, 'viewpoint_claim_links': 2})
    bundle = materials(qualifier=True)
    first = cvp.compile_candidate(req, bundle, proposal())
    raw = {'components': [
        {'claim_ref': 'c1', 'spans': [{'text_ref': 't1', 'start_char': 0, 'end_char': 5}]},
        {'claim_ref': 'c1', 'spans': [{'text_ref': 't2', 'start_char': 0, 'end_char': 5}]}],
        'component_mappings': [{'old_ref': 'k1', 'new_refs': ['k1', 'k2']}],
        'component_dispositions': [
            {'component_ref': 'k1', 'disposition': 'connected', 'target_ref': 'nv1', 'link_ref': 'nl1', 'reason': '核心。'},
            {'component_ref': 'k2', 'disposition': 'connected', 'target_ref': 'nv1', 'link_ref': 'nl2', 'reason': '限定。'}],
        'changes': [change('nl1', link_type='equivalent_component'),
                    change('nl2', viewpoint_id='nv1', claim_id='c1', link_type='qualifies')]}
    second = cvp.compile_candidate(req, bundle, raw, previous=first)
    link = next(row['payload'] for row in second['writes'] if row['ref'] == 'nl2')
    assert link['schema_version'] == 'wang_viewpoint_claim_link_v2'
    assert link['component_position_locator']['spans'][0] == {
        'path': ['qualifications', 0], 'start_char': 0, 'end_char': 5, 'exact_text': '在此語境。'}
    assert link['component_locator'] is None
    assert second['component_plan']['old_components'] == first['component_plan']['components']
    assert second['component_plan']['components'][0]['component_id'] != first['component_plan']['components'][0]['component_id']
    third = cvp.compile_candidate(req, bundle, {}, previous=second)
    assert third == second


def test_changed_claim_rebind_requires_explicit_plan_and_keeps_old_findings():
    req, old = request(), materials()
    first = cvp.compile_candidate(req, old, proposal())
    new = deepcopy(old)
    new.pop('artifact_sha256')
    claim = new['records'][0]['payload']
    claim['statement'] = '人子。'
    claim['revision'] = 3
    new['records'][0].update(revision=3, content_sha256=semantic_record_sha(claim))
    new['claim_input'] = compile_input([claim], old['claim_input']['policy'], claim_refs={'C1': 'c1'})
    new = seal(new)
    with pytest.raises(ValueError, match='explicit replacement plan'):
        cvp.rebind_candidate(req, old, new, first)
    raw = {'components': [{'claim_ref': 'c1', 'spans': [{'text_ref': 't1', 'start_char': 0, 'end_char': 3}]}],
           'component_mappings': [{'old_ref': 'k1', 'new_refs': ['k1']}],
           'component_dispositions': proposal()['component_dispositions']}
    old_finding = {'finding_id': 'old', 'target_refs': ['k1'], 'category': 'wording', 'reason': '舊異議。'}
    frame = cvp.prepare_rebase_frame(req, old, new, first, prior_findings=[old_finding])
    assert frame['candidate_valid_for_commit'] is False
    assert frame['requires_explicit_component_plan'] is True
    assert frame['old_bundle'] == old and frame['previous_candidate'] == first
    packet = cvp.proposal_packet(req, new, previous=frame)
    runtime = packet['previous']
    assert runtime['durable_frame_sha256'] == frame['artifact_sha256']
    assert 'old_bundle' not in runtime and 'previous_candidate' not in runtime
    assert runtime['component_plan'] == first['component_plan']
    assert cvp.proposal_packet(req, new, previous=runtime) == packet
    with pytest.raises(ValueError, match='explicit replacement plan'):
        cvp.compile_candidate(req, new, {}, previous=frame)
    from_frame = cvp.compile_candidate(req, new, raw, previous=frame)
    assert cvp.revalidate_candidate(req, new, from_frame, raw, previous=frame) == from_frame
    forged = deepcopy(frame)
    forged['old_bundle']['group_key'] = 'another-group'
    forged = seal({k: v for k, v in forged.items() if k != 'artifact_sha256'})
    with pytest.raises(ValueError, match='artifact|group'):
        cvp.compile_candidate(req, new, raw, previous=forged)
    rebound = cvp.rebind_candidate(req, old, new, first, replacement_raw=raw, prior_findings=[old_finding])
    assert from_frame == rebound
    assert rebound['component_plan']['components'][0]['spans'][0]['exact_text'] == '人子。'
    assert rebound['component_plan']['old_components'] == first['component_plan']['components']
    assert rebound['prior_findings'] == [old_finding]
    assert rebound['rebase_history'][0]['wording_changed'] is True
    assert next(row['payload'] for row in rebound['writes'] if row['ref'] == 'nl1')['pinned_claim_revision'] == 3
    assert cvp.compile_candidate(req, new, {}, previous=rebound)['rebase_history'] == rebound['rebase_history']


def test_existing_summary_is_metadata_authorized_and_has_finite_review_subject():
    bundle, ids = maintained()
    raw = maintenance_proposal()
    raw['summaries'] = [{'ref': 'v1', 'summary': '新的編輯摘要。'}]
    req = request(action_id='maintenance', mode='maintain', maintenance_viewpoint_ids=[ids['canonical_viewpoints']])
    with pytest.raises(ValueError, match='metadata maintenance permission'):
        cvp.compile_candidate(req, bundle, raw)
    req = request(action_id='maintenance', mode='maintain', maintenance_viewpoint_ids=[ids['canonical_viewpoints']],
        permissions=[{'collection': 'canonical_viewpoints', 'object_id': ids['canonical_viewpoints'], 'fields': ['summary']}])
    candidate = cvp.compile_candidate(req, bundle, raw)
    assert candidate['writes'] == []
    assert candidate['review_sets']['objects'] == ['l1', 'v1']
    assert cvp.validate_review(req, bundle, candidate, passing(candidate))['approved']


def test_proposer_finding_cannot_disappear_on_sparse_revision():
    req, bundle = request(), materials()
    raw = proposal()
    raw['findings'] = [{'slot': 'f1', 'target_refs': ['k1'], 'category': 'wording', 'reason': '原文待核。'}]
    first = cvp.compile_candidate(req, bundle, raw)
    second = cvp.compile_candidate(req, bundle, {}, previous=first)
    assert second['prior_findings'][0]['finding_id'] == first['findings'][0]['finding_id']
    assert second['prior_findings'][0]['target_bindings']['k1'] == first['component_plan']['components'][0]['component_id']
    with pytest.raises(ValueError, match='every old finding'):
        cvp.validate_review(req, bundle, second, passing(second))


def test_merge_moves_old_member_and_preserves_old_identity_as_logical_redirect():
    bundle, ids = maintained()
    req = request(action_id='merge', mode='maintain', maintenance_viewpoint_ids=[ids['canonical_viewpoints']],
        permissions=[{'collection': 'canonical_viewpoints', 'object_id': ids['canonical_viewpoints'],
                      'fields': ['identity_status', 'redirect_to_viewpoint_id']},
                     {'collection': 'viewpoint_claim_links', 'object_id': ids['viewpoint_claim_links'],
                      'fields': ['viewpoint_id', 'validated_against_viewpoint_revision_id']}])
    raw = proposal()
    raw['changes'] = [*raw['changes'][:2],
        change('v1', identity_status='merged', redirect_to_viewpoint_id='nv1'),
        change('l1', viewpoint_id='nv1')]
    raw['component_dispositions'][0]['link_ref'] = 'l1'
    raw['member_dispositions'] = [{'member_ref': 'l1', 'disposition': 'changed', 'reason': '轉至同一判斷的保留身份。'}]
    candidate = cvp.compile_candidate(req, bundle, raw)
    approval = cvp.validate_review(req, bundle, candidate, passing(candidate))
    package = cvp.finalize_package(req, bundle, candidate, approval, provenance())
    old = next(row for row in package['canonical_viewpoints'] if row['viewpoint_id'] == ids['canonical_viewpoints'])
    link = package['viewpoint_claim_links'][0]
    assert old['identity_status'] == 'merged'
    assert old['redirect_to_viewpoint_id'] == link['viewpoint_id']
    assert link['viewpoint_claim_link_id'] == ids['viewpoint_claim_links']
    assert link['revision'] == 2
    assert any(row['decision'] == 'merge_identities' for row in package['viewpoint_identity_decisions'])


def test_protected_existing_viewpoint_cannot_receive_a_new_connection():
    bundle, ids = maintained()
    bundle = deepcopy(bundle)
    bundle.pop('artifact_sha256')
    view = next(row for row in bundle['records'] if row['collection'] == 'canonical_viewpoints')
    view['payload']['review_status'] = 'human_review_required'
    view['content_sha256'] = semantic_record_sha(view['payload'])
    bundle = seal(bundle)
    req = request(action_id='protected', mode='maintain', maintenance_viewpoint_ids=[ids['canonical_viewpoints']])
    with pytest.raises(ValueError, match='inactive viewpoint'):
        cvp.compile_candidate(req, bundle, maintenance_proposal())


def disputed(*, req=None, bundle=None, raw=None):
    req, bundle = req or request(), deepcopy(bundle or materials())
    bundle.pop('artifact_sha256')
    bundle['material']['sources'] = [{'source_ref': 's1', 'source_body_sha256': 'c' * 64,
        'sections': [{'section_ref': 'p1', 'paragraphs': [
            {'paragraph_key': 'S0001', 'row': {'text': '人子是神。祂不是普通人。'}}]}]}]
    bundle = seal(bundle)
    raw = raw or proposal()
    candidate = cvp.compile_candidate(req, bundle, raw)
    review = passing(candidate)
    review['component_reviews'][0] = {'ref': 'k1', 'decision': 'revise', 'finding_refs': ['f1']}
    review['findings'] = [{'slot': 'f1', 'target_refs': ['k1'], 'category': 'identity',
                           'reason': '此處稱號的神性與另一身份判斷應分開。'}]
    proposal_raw = {'result': 'candidate', 'candidate': raw, 'review': None, 'context_requests': [], 'member_requests': []}
    review_raw = {'result': 'review', 'candidate': None, 'review': review, 'context_requests': [], 'member_requests': []}
    def artifact(response, role, packet):
        return seal({'schema_version': 'wang_canonical_viewpoint_batch_raw_response_v1',
            'model_id': 'gpt-6.1-sol' if role == 'proposal' else 'claude-opus-5-5',
            'backend': 'codex_subscription' if role == 'proposal' else 'claude_subscription',
            'request_payload_sha256': cvp.sha256_json(packet), 'response': response,
            'response_sha256': cvp.sha256_json(response)})
    cite = {'source_ref': 's1', 'section_ref': 'p1', 'paragraph_key': 'S0001', 'start_char': 0, 'end_char': 5}
    values = {'candidate': candidate,
        'proposal_judgment': {'artifact': artifact(proposal_raw, 'proposal', cvp.proposal_packet(req, bundle)),
            'position_path': ['candidate', 'component_dispositions', 0, 'reason'], 'citations': [cite]},
        'review_judgment': {'artifact': artifact(review_raw, 'review', cvp.review_packet(req, bundle, candidate)),
            'position_path': ['review', 'findings', 0, 'reason'], 'citations': [cite]},
        'diagnostics': {'material_complete': True, 'versions_consistent': True, 'schema_valid': True,
            'engineering_clear': True, 'evidence_cannot_resolve': True, 'reason': '雙方已讀同一完整小節，現有材料不能決定身份粒度。'},
        'options': [{'ref': 'keep', 'label': '保留不同判斷'}, {'ref': 'join', 'label': '認作同一判斷'}],
        'category': 'identity', 'standard': '同一真值條件，而非僅有同一稱號。'}
    return req, bundle, candidate, values


def decision(*, kind='interpretation', targets=None, ident='H1'):
    return {'decision_id': ident, 'actor_kind': 'human', 'kind': kind,
        'target_refs': targets or ['k1'], 'reason': '依教授原文保留這項區別。', 'selected_option': 'keep',
        'authorization': {'path': '/human/decision.json', 'file_sha256': 'd' * 64}}


def test_content_dispute_requires_actual_opposed_source_grounded_roles_after_engineering_checks():
    req, bundle, candidate, args = disputed()
    dispute = cvp.build_content_dispute(req, bundle, **args)
    assert dispute['judgments'][0]['citations'][0]['exact_text'] == '人子是神。'
    assert dispute['automatic_content_approval'] is False
    assert dispute['budget_reset'] is False
    for name in ['material_complete', 'versions_consistent', 'schema_valid', 'engineering_clear', 'evidence_cannot_resolve']:
        bad = deepcopy(args)
        bad['diagnostics'][name] = False
        with pytest.raises(ValueError):
            cvp.build_content_dispute(req, bundle, **bad)
    bad = deepcopy(args)
    bad['review_judgment']['citations'][0]['section_ref'] = 'missing'
    with pytest.raises(ValueError, match='unsupplied'):
        cvp.build_content_dispute(req, bundle, **bad)
    bad = deepcopy(args)
    bad['review_judgment']['artifact']['backend'] = 'codex_subscription'
    bad['review_judgment']['artifact'] = seal({k: v for k, v in bad['review_judgment']['artifact'].items() if k != 'artifact_sha256'})
    with pytest.raises(ValueError, match='independent semantic roles'):
        cvp.build_content_dispute(req, bundle, **bad)


def test_human_interpretation_is_retained_as_constraint_and_requires_normal_model_review():
    req, bundle, candidate, args = disputed()
    dispute = cvp.build_content_dispute(req, bundle, **args)
    constraint = cvp.validate_human_constraint(req, dispute, decision())
    assert constraint['approval_granted'] is False
    assert constraint['original_model_judgments_unchanged'] is True
    updated = seal({**{k: v for k, v in bundle.items() if k != 'artifact_sha256'}, 'human_constraints': [constraint]})
    rebound = cvp.rebind_candidate(req, bundle, updated, candidate)
    assert rebound['prior_findings'][0]['finding_id'] == constraint['constraint_id']
    assert cvp.proposal_packet(req, updated)['context']['human_constraints'] == [constraint]
    review = passing(rebound)
    with pytest.raises(ValueError, match='every old finding'):
        cvp.validate_review(req, updated, rebound, review)
    review['prior_findings'] = [{'ref': 'pf1', 'disposition': 'resolved', 'reason': '依人提供的區分重新核對完整後像。'}]
    assert cvp.validate_review(req, updated, rebound, review)['approved']
    with pytest.raises(ValueError, match='discard historical human'):
        cvp.rebind_candidate(req, updated, bundle, rebound)


def test_exact_human_hold_and_retention_cannot_be_overridden_by_model_permissions():
    req, bundle, candidate, args = disputed()
    constraint = cvp.validate_human_constraint(req, cvp.build_content_dispute(req, bundle, **args), decision(kind='hold'))
    held = seal({**{k: v for k, v in bundle.items() if k != 'artifact_sha256'}, 'human_constraints': [constraint]})
    with pytest.raises(ValueError, match='unresolved exact human hold'):
        cvp.compile_candidate(req, held, proposal())
    base, ids = maintained()
    req = request(action_id='retain', mode='maintain', maintenance_viewpoint_ids=[ids['canonical_viewpoints']],
        permissions=[{'collection': 'viewpoint_claim_links', 'object_id': ids['viewpoint_claim_links'], 'fields': ['effective_state']}])
    req, base, candidate, args = disputed(req=req, bundle=base, raw=maintenance_proposal())
    dispute = cvp.build_content_dispute(req, base, **args)
    keep = cvp.validate_human_constraint(req, dispute, decision(kind='exact_retain', targets=['l1']))
    retained = seal({**{k: v for k, v in base.items() if k != 'artifact_sha256'}, 'human_constraints': [keep]})
    raw = {'component_dispositions': [{'component_ref': 'k1', 'disposition': 'background', 'reason': '撤回。'}],
        'changes': [change('l1', effective_state='retired')],
        'member_dispositions': [{'member_ref': 'l1', 'disposition': 'background', 'reason': '撤回。'}]}
    with pytest.raises(ValueError, match='exact human retention'):
        cvp.compile_candidate(req, retained, raw)


def quality_registration(bundle, *, sample=None):
    current = {(row['collection'], row['object_id']): row for row in bundle['records']}
    view = next((key, row) for key, row in current.items() if key[0] == 'canonical_viewpoints')
    key, row = view
    target = '/'.join(key)
    audit = {'audit_id': 'Q1', 'report_binding': {'path': '/audit/report.json', 'file_sha256': 'e' * 64},
        'raw_report': {'results': [{'reason': '此觀點遺漏必要限定。'}]},
        'reader': {'model_id': 'independent-model', 'participating_model_ids': ['gpt-6.1-sol', 'claude-opus-5-5'],
            'code_path': 'scripts/audit-library.py', 'participating_code_paths': ['backend/pipeline/cvp_materials.py'],
            'source_mode': 'direct_physical_originals', 'imports_pipeline_readers': False},
        'cohort_pins': [cvp._pin_for(key, row)], 'sample_keys': [target],
        'findings': [{'ref': 'f1', 'category': 'wording', 'target_keys': [target], 'report_path': ['results', 0, 'reason']}],
        'cohort_description': '此次指定樣本，不代表全庫。'}
    return cvp.register_quality_findings(audit, current), current, target, audit


def test_quality_objection_is_current_version_bound_and_enters_normal_maintenance_review():
    bundle, ids = maintained()
    registration, current, target, audit = quality_registration(bundle)
    assert registration['findings'][0]['reason'] == audit['raw_report']['results'][0]['reason']
    assert registration['operation_completion_changed'] is False
    quality = seal({**{k: v for k, v in bundle.items() if k != 'artifact_sha256'}, 'quality_findings': [registration]})
    req = request(action_id='quality', mode='maintain', maintenance_viewpoint_ids=[ids['canonical_viewpoints']])
    candidate = cvp.compile_candidate(req, quality, maintenance_proposal())
    assert candidate['prior_findings'][0]['finding_id'] == registration['findings'][0]['finding_id']
    assert 'v1' in candidate['review_sets']['objects']
    with pytest.raises(ValueError, match='every old finding'):
        cvp.validate_review(req, quality, candidate, passing(candidate))
    admission = cvp.assess_usage_admission([target], current, [registration])
    assert admission['status'] == 'blocked'
    assert admission['operation_completion_changed'] is False
    bad = deepcopy(audit)
    bad['reader']['model_id'] = 'claude-opus-5-5'
    with pytest.raises(ValueError, match='participating model'):
        cvp.register_quality_findings(bad, current)
    bad = deepcopy(audit)
    bad['cohort_pins'][0]['revision'] += 1
    with pytest.raises(ValueError, match='different current version'):
        cvp.register_quality_findings(bad, current)


def test_formal_usage_requires_separate_exact_scope_but_existing_use_does_not_add_audit_call():
    bundle, _ = maintained()
    _, current, target, _ = quality_registration(bundle)
    assert cvp.assess_usage_admission([target], current)['status'] == 'eligible'
    assert cvp.assess_usage_admission([target], current, purpose='formal_downstream')['status'] == 'blocked'
    scope = seal({'schema_version': cvp.VERSION + '_scope_admission', 'scope_id': 'approved-cohort',
        'purpose': 'formal_downstream', 'approved': True,
        'authorization': {'path': '/scope/authorization.json', 'file_sha256': 'a' * 64},
        'evidence_bindings': [{'path': '/scope/assessment.json', 'file_sha256': 'b' * 64}],
        'target_pins': [cvp._pin_for(tuple(target.split('/', 1)), current[tuple(target.split('/', 1))])]})
    assert cvp.assess_usage_admission([target], current, purpose='formal_downstream', scope_admission=scope)['status'] == 'eligible'
    registration, _, _, _ = quality_registration(bundle)
    assert cvp.assess_usage_admission([target], current, [registration], purpose='formal_downstream', scope_admission=scope)['status'] == 'blocked'


def test_cvr_immutability_and_program_derived_head_member_repin_need_exact_permissions():
    bundle, ids = maintained()
    req = request(action_id='revision', mode='maintain', maintenance_viewpoint_ids=[ids['canonical_viewpoints']],
        permissions=[{'collection': 'viewpoint_revisions', 'object_id': ids['viewpoint_revisions'], 'fields': ['core_proposition']}])
    raw = maintenance_proposal()
    raw['changes'] = [change('r1', core_proposition='人子稱號表達神性。')]
    with pytest.raises(ValueError, match='CVR is immutable'):
        cvp.compile_candidate(req, bundle, raw)
    permissions = [{'collection': 'canonical_viewpoints', 'object_id': ids['canonical_viewpoints'], 'fields': ['current_revision_id', 'summary']}]
    req = request(action_id='revision', mode='maintain', maintenance_viewpoint_ids=[ids['canonical_viewpoints']], permissions=permissions)
    revision = json.loads(proposal()['changes'][1]['fields_json'])
    revision['viewpoint_id'] = 'v1'
    revision['core_proposition'] = '人子稱號表達神性。'
    raw['changes'] = [change('v1', current_revision_id='nr1'), change('nr1', **revision)]
    raw['summaries'] = [{'ref': 'v1', 'summary': '此處人子稱號表達神性。'}]
    with pytest.raises(ValueError, match='program-only member/relation repin permission'):
        cvp.compile_candidate(req, bundle, raw)
    permissions.append({'collection': 'viewpoint_claim_links', 'object_id': ids['viewpoint_claim_links'],
                        'fields': ['validated_against_viewpoint_revision_id']})
    req = request(action_id='revision', mode='maintain', maintenance_viewpoint_ids=[ids['canonical_viewpoints']], permissions=permissions)
    candidate = cvp.compile_candidate(req, bundle, raw)
    assert {row['ref'] for row in candidate['writes']} == {'v1', 'nr1', 'l1'}
    revision = next(row['payload'] for row in candidate['writes'] if row['ref'] == 'nr1')
    link = next(row['payload'] for row in candidate['writes'] if row['ref'] == 'l1')
    assert revision['supersedes_revision_id'] == ids['viewpoint_revisions']
    assert revision['revision'] == revision['revision_number'] == 1
    assert link['validated_against_viewpoint_revision_id'] == revision['viewpoint_revision_id']
    assert candidate['member_dispositions'][0]['disposition'] == 'retained'
    # Program-owned validation fields cannot be copied into a model delta.
    raw['changes'].append(change('l1', validated_against_viewpoint_revision_id='nr1'))
    with pytest.raises(ValueError, match='mechanical or forbidden'):
        cvp.compile_candidate(req, bundle, raw)


def test_cleaned_upstream_claim_repin_is_derived_without_copying_same_business_fields():
    bundle, ids = maintained()
    updated = deepcopy(bundle)
    updated.pop('artifact_sha256')
    claim = next(row for row in updated['records'] if row['collection'] == 'claims')
    claim['payload'].update(statement='人子確實是神。', revision=3)
    claim.update(revision=3, content_sha256=semantic_record_sha(claim['payload']))
    updated['claim_input'] = compile_input([claim['payload']], updated['claim_input']['policy'], claim_refs={'C1': 'c1'})
    updated = seal(updated)
    req = request(action_id='claim-change', mode='maintain', maintenance_viewpoint_ids=[ids['canonical_viewpoints']])
    with pytest.raises(ValueError, match='program-only member/relation repin permission'):
        cvp.compile_candidate(req, updated, maintenance_proposal())
    req = request(action_id='claim-change', mode='maintain', maintenance_viewpoint_ids=[ids['canonical_viewpoints']],
        permissions=[{'collection': 'viewpoint_claim_links', 'object_id': ids['viewpoint_claim_links'], 'fields': ['pinned_claim_revision']}])
    candidate = cvp.compile_candidate(req, updated, maintenance_proposal())
    assert len(candidate['writes']) == 1
    assert candidate['writes'][0]['payload']['pinned_claim_revision'] == 3
    assert candidate['writes'][0]['payload']['claim_id'] == 'C1'
    assert candidate['member_dispositions'][0]['disposition'] == 'retained'
    assert cvp.validate_review(req, updated, candidate, passing(candidate))['approved']
    with pytest.raises(ValueError, match='source Claim identity is immutable'):
        request(action_id='bad', mode='maintain', permissions=[{
            'collection': 'viewpoint_claim_links', 'object_id': ids['viewpoint_claim_links'], 'fields': ['claim_id']}])


def test_retire_identity_can_withdraw_its_relationship_to_readonly_endpoint():
    bundle, ids = maintained()
    expanded = deepcopy(bundle)
    expanded.pop('artifact_sha256')
    view = deepcopy(next(row for row in expanded['records'] if row['collection'] == 'canonical_viewpoints'))
    revision = deepcopy(next(row for row in expanded['records'] if row['collection'] == 'viewpoint_revisions'))
    view['object_id'] = view['payload']['viewpoint_id'] = 'OTHER'
    view['payload']['current_revision_id'] = 'OTHER-R'
    view['content_sha256'] = semantic_record_sha(view['payload'])
    revision['object_id'] = revision['payload']['viewpoint_revision_id'] = 'OTHER-R'
    revision['payload']['viewpoint_id'] = 'OTHER'
    revision['content_sha256'] = semantic_record_sha(revision['payload'])
    relation = cvp.KNOWLEDGE_COLLECTIONS['viewpoint_relations'][0].model_validate({
        'viewpoint_relation_id': 'REL', 'source_viewpoint_id': ids['canonical_viewpoints'],
        'target_viewpoint_id': 'OTHER', 'validated_source_viewpoint_revision_id': ids['viewpoint_revisions'],
        'validated_target_viewpoint_revision_id': 'OTHER-R', 'relation_type': 'qualifies',
        'reason': '已批准的關係。', 'review_status': 'system_approved'}).model_dump(mode='json')
    expanded['records'].extend([view, revision, {'collection': 'viewpoint_relations', 'object_id': 'REL',
        'payload': relation, 'revision': 1, 'content_sha256': semantic_record_sha(relation), 'retired': False}])
    expanded['references']['canonical_viewpoints']['OTHER'] = 'v2'
    expanded['references']['viewpoint_revisions']['OTHER-R'] = 'r2'
    expanded['references']['viewpoint_relations'] = {'REL': 'q1'}
    expanded = seal(expanded)
    req = request(action_id='retire-related', mode='maintain', maintenance_viewpoint_ids=[ids['canonical_viewpoints']],
        permissions=[{'collection': 'canonical_viewpoints', 'object_id': ids['canonical_viewpoints'], 'fields': ['identity_status']},
            {'collection': 'viewpoint_claim_links', 'object_id': ids['viewpoint_claim_links'], 'fields': ['effective_state']},
            {'collection': 'viewpoint_relations', 'object_id': 'REL', 'fields': ['effective_state']}])
    raw = {'component_dispositions': [{'component_ref': 'k1', 'disposition': 'background', 'reason': '此身份退役。'}],
        'member_dispositions': [{'member_ref': 'l1', 'disposition': 'background', 'reason': '不繼續連接。'}],
        'changes': [change('v1', identity_status='retired'), change('l1', effective_state='retired'), change('q1', effective_state='retired')]}
    candidate = cvp.compile_candidate(req, expanded, raw)
    assert {row['ref'] for row in candidate['writes']} == {'v1', 'l1', 'q1'}
    assert 'v2' not in candidate['review_sets']['objects']
    assert cvp.validate_review(req, expanded, candidate, passing(candidate))['approved']


def test_support_only_rebase_frame_keeps_plan_and_findings_without_fictitious_mapping():
    req, old = request(), materials()
    candidate = cvp.compile_candidate(req, old, proposal())
    approval = cvp.validate_review(req, old, candidate, passing(candidate))
    new = deepcopy(old)
    new.pop('artifact_sha256')
    new['records'][1]['payload']['statement'] = '完整語境的支持論證。'
    new['records'][1]['revision'] = 2
    new['records'][1]['content_sha256'] = semantic_record_sha(new['records'][1]['payload'])
    new = seal(new)
    finding = {'finding_id': 'old-source', 'target_refs': ['k1'], 'category': 'source', 'reason': '核對原證據。'}
    frame = cvp.prepare_rebase_frame(req, old, new, candidate, prior_findings=[finding])
    assert frame['requires_explicit_component_plan'] is False
    current = cvp.compile_candidate(req, new, {}, previous=frame)
    assert current['component_plan'] == candidate['component_plan']
    assert current['prior_findings'] == [finding]
    assert current['rebase_history'][-1]['wording_changed'] is False
    assert current['artifact_sha256'] != candidate['artifact_sha256']
    with pytest.raises(ValueError, match='exact passing'):
        cvp.finalize_package(req, new, current, approval, provenance())


def test_revision_can_discard_duplicate_new_identity_without_losing_findings():
    bundle, ids = maintained()
    req = request(action_id='corrected-maintenance', mode='maintain', maintenance_viewpoint_ids=[ids['canonical_viewpoints']])
    raw = proposal()
    raw['member_dispositions'] = maintenance_proposal()['member_dispositions']
    first = cvp.compile_candidate(req, bundle, raw)
    finding = {'finding_id': 'duplicate', 'target_refs': ['nv1'], 'category': 'identity',
               'reason': 'Existing viewpoint already expresses this identity.'}
    correction = {**maintenance_proposal(), 'discard_refs': ['nv1', 'nr1', 'nl1']}
    candidate = cvp.compile_candidate(req, bundle, correction, previous=first, prior_findings=[finding])
    assert candidate['writes'] == []
    assert candidate['summaries'] == []
    assert candidate['component_plan'] == first['component_plan']
    assert candidate['prior_findings'] == [finding]
    review = passing(candidate)
    with pytest.raises(ValueError, match='every old finding'):
        cvp.validate_review(req, bundle, candidate, review)
    review['prior_findings'] = [{'ref': 'pf1', 'disposition': 'resolved', 'reason': 'Duplicate removed.'}]
    assert cvp.validate_review(req, bundle, candidate, review)['approved']
    assert cvp.revalidate_candidate(req, bundle, candidate, correction, previous=first,
                                    prior_findings=[finding]) == candidate
    with pytest.raises(ValueError):
        cvp.compile_candidate(req, bundle, {'discard_refs': ['nv1']}, previous=first)
    for bad in (['v1'], ['unknown'], ['nv1', 'nv1']):
        with pytest.raises(ValueError, match='discard requires'):
            cvp.compile_candidate(req, bundle, {'discard_refs': bad}, previous=first)


def test_revision_can_reset_authorized_existing_head_without_copying_old_content():
    bundle, ids = maintained()
    req = request(action_id='corrected-maintenance', mode='maintain', maintenance_viewpoint_ids=[ids['canonical_viewpoints']], permissions=[
        {'collection': 'canonical_viewpoints', 'object_id': ids['canonical_viewpoints'],
         'fields': ['current_revision_id', 'summary']},
        {'collection': 'viewpoint_claim_links', 'object_id': ids['viewpoint_claim_links'],
         'fields': ['validated_against_viewpoint_revision_id']}])
    raw = maintenance_proposal()
    raw['changes'] = [change('v1', current_revision_id='nr1'), change('nr1', scope={'scripture_scope': ['但以理書7章']})]
    raw['summaries'] = [{'ref': 'v1', 'summary': '此處的人子稱號指向神性。'}]
    first = cvp.compile_candidate(req, bundle, raw)
    assert {r['ref'] for r in first['writes']} == {'v1', 'nr1', 'l1'}
    corrected = cvp.compile_candidate(req, bundle, {'discard_refs': ['v1', 'nr1', 'l1']}, previous=first)
    assert corrected['writes'] == []
    assert corrected['summaries'] == []
    assert corrected['records'] == [
        {'collection': col, 'object_id': oid, 'payload': row['payload']}
        for (col, oid), row in sorted(cvp._records(bundle).items())]
    assert cvp.validate_review(req, bundle, corrected, passing(corrected))['approved']


def test_revision_can_merge_proposed_identities_with_complete_component_mapping():
    req = request(creation_slots={'canonical_viewpoints': 2, 'viewpoint_revisions': 2, 'viewpoint_claim_links': 2})
    bundle = materials(qualifier=True)
    raw = proposal()
    raw['components'] = [
        {'claim_ref': 'c1', 'spans': [{'text_ref': 't1', 'start_char': 0, 'end_char': 5}]},
        {'claim_ref': 'c1', 'spans': [{'text_ref': 't2', 'start_char': 0, 'end_char': 5}]}]
    raw['changes'][-1] = change('nl1', viewpoint_id='nv1', claim_id='c1', link_type='equivalent_component')
    raw['component_dispositions'].append({'component_ref': 'k2', 'disposition': 'connected',
        'target_ref': 'nv2', 'link_ref': 'nl2', 'reason': 'Second candidate.'})
    raw['changes'] += [change('nv2', current_revision_id='nr2'),
        change('nr2', **{**json.loads(proposal()['changes'][1]['fields_json']), 'viewpoint_id': 'nv2'}),
        change('nl2', viewpoint_id='nv2', claim_id='c1', link_type='equivalent_component')]
    raw['summaries'].append({'ref': 'nv2', 'summary': '此語境的限定。'})
    first = cvp.compile_candidate(req, bundle, raw)
    correction = {'discard_refs': ['nv2', 'nr2', 'nl2'],
        'components': [{'claim_ref': 'c1', 'spans': [span for part in raw['components'] for span in part['spans']]}],
        'component_mappings': [{'old_ref': ref, 'new_refs': ['k1']} for ref in ('k1', 'k2')],
        'component_dispositions': proposal()['component_dispositions'],
        'changes': [change('nl1', link_type='equivalent_full')]}
    corrected = cvp.compile_candidate(req, bundle, correction, previous=first)
    assert {r['ref'] for r in corrected['writes']} == {'nv1', 'nr1', 'nl1'}
    assert [r['ref'] for r in corrected['summaries']] == ['nv1']
    assert cvp.validate_review(req, bundle, corrected, passing(corrected))['approved']


def test_resolved_quality_finding_can_be_revalidated_after_target_version_changes():
    bundle, ids = maintained()
    registration, _, target, _ = quality_registration(bundle)

    def review_and_resolve(bundle, registration):
        quality = seal({**{k: v for k, v in bundle.items() if k != 'artifact_sha256'},
                        'quality_findings': [registration]})
        req = request(action_id='quality', mode='maintain', maintenance_viewpoint_ids=[ids['canonical_viewpoints']])
        raw = maintenance_proposal()
        candidate = cvp.compile_candidate(req, quality, raw)
        review = passing(candidate)
        review['prior_findings'] = [{'ref': ref, 'disposition': 'resolved', 'reason': 'Current target reviewed.'}
                                   for ref in candidate['prior_finding_refs']]
        approval = cvp.validate_review(req, quality, candidate, review)
        assert approval['approved'] and len(candidate['prior_findings']) == 1
        evidence = {'request': req.model_dump(mode='json'), 'bundle': quality, 'candidate': candidate,
            'approval': approval, 'proposal_raw': {'result': 'candidate', 'candidate': raw},
            'review_raw': {'result': 'review', 'review': review}, 'previous': None, 'prior_findings': [],
            'provenance': provenance(), 'call_keys': {'gpt': 'g', 'opus': 'o'}, 'round_id': 'R'}
        current = {(r['collection'], r['object_id']): r for r in bundle['records']}
        readback = seal({'schema_version': cvp.VERSION + '_readback',
            'candidate_sha256': candidate['artifact_sha256'], 'approval_sha256': approval['artifact_sha256'],
            'pending_impacts': [], 'independent_audit': 'not_performed', 'actual_records': bundle['records']})
        return cvp.resolve_quality_findings(registration, evidence, readback, current), current

    resolved, current = review_and_resolve(bundle, registration)
    assert cvp.assess_usage_admission([target], current, [resolved])['status'] == 'eligible'
    changed = deepcopy(bundle)
    view = next(r for r in changed['records'] if r['collection'] == 'canonical_viewpoints')
    view['revision'] += 1
    view['payload']['revision'] += 1
    view['content_sha256'] = semantic_record_sha(view['payload'])
    changed = seal({k: v for k, v in changed.items() if k != 'artifact_sha256'})
    current = {(r['collection'], r['object_id']): r for r in changed['records']}
    assert cvp.assess_usage_admission([target], current, [resolved])['status'] == 'blocked'
    revalidated, current = review_and_resolve(changed, resolved)
    assert revalidated['observation'] == registration['observation']
    assert revalidated['independent_audit'] == registration['independent_audit']
    assert revalidated['findings'][0]['resolution']['current_target_pins'][0]['revision'] == 2
    assert cvp.assess_usage_admission([target], current, [revalidated])['status'] == 'eligible'


@pytest.mark.parametrize('discarded', [
    ['nv1'], ['nr1'], ['nl1'], ['nv1', 'nr1'], ['nv1', 'nl1'], ['nr1', 'nl1'],
])
def test_partial_discard_rejects_dangling_new_identity_before_review(discarded):
    req, bundle = request(), materials()
    first = cvp.compile_candidate(req, bundle, proposal())
    correction = {'discard_refs': discarded, 'component_dispositions': [
        {'component_ref': 'k1', 'disposition': 'background', 'reason': 'Candidate identity withdrawn.'}]}
    with pytest.raises(ValueError):
        cvp.compile_candidate(req, bundle, correction, previous=first)
