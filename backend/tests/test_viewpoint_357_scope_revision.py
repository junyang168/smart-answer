from copy import deepcopy
from types import SimpleNamespace
from contextlib import nullcontext
import pytest
from backend.pipeline import viewpoint_357_scope_revision as m


def proposal():
    return {'core_proposition': '路12:20中的ψυχή应理解为魂，不应译成灵魂。',
            'scope': {'scripture_scope': ['路加福音 12:20'], 'audience_scope': [], 'historical_scope': []},
            'proposition_signature': {'subject': 'ψυχή', 'predicate': '理解为', 'object': '魂而非灵魂',
                'polarity': 'affirmed', 'modality': '应当', 'conditions': ['路12:20'],
                'temporal_scope': [], 'population_scope': []}, 'reason': '两处译法批评不同',
            'member_link_repair': {'link_type': 'equivalent_component', 'component_text': '魂，不应译成灵魂',
                'included_binding_indices': [0], 'excluded_binding_reason': '另一个binding是太2:20的性命译法'}}


def states():
    return {('canonical_viewpoints', m.TARGET): {'viewpoint_id': m.TARGET, 'current_revision_id': m.OLD, 'review_status': 'system_approved'},
            ('viewpoint_revisions', m.OLD): {'viewpoint_revision_id': m.OLD, 'viewpoint_id': m.TARGET,
                'core_proposition': 'old', 'scope': {}, 'provenance': {'basis_identity_decision_ids': ['old-decision']},
                'revision': 1, 'revision_number': 1},
            ('viewpoint_claim_links', m.LINK): {'viewpoint_claim_link_id': m.LINK, 'viewpoint_id': m.TARGET,
                'validated_against_viewpoint_revision_id': m.OLD, 'effective_state': 'active', 'claim_id': m.CLAIM,
                'pinned_claim_revision': 1, 'decision_id': 'old-decision', 'occurrence_refs': ['occ'], 'supporting_relation_ids': [],
                'evidence_bindings': [{'evidence_step_id': 'e1', 'source_fragment_id': 'luke'},
                                      {'evidence_step_id': 'e2', 'source_fragment_id': 'matt'}]},
            ('claims', m.CLAIM): {'statement': 'ψυχή应当理解为魂，不应译成灵魂。', 'revision': 1},
            ('viewpoint_identity_decisions', 'old-decision'): {'identity_candidate_id': 'old-candidate'},
            ('viewpoint_identity_candidates', 'old-candidate'): {'seed_relation_ids': [], 'blocker_codes': [],
                    'coverage_snapshot_id': None, 'scope_manifest_sha256': 'scope'}}


def test_successor_preserves_history_and_never_fabricates_human_approval(tmp_path):
    original = states()
    old = deepcopy(original)
    package, manifest = m.package_for(original, proposal(), {'approved': True, 'approval_basis': 'dual_model_consensus', 'raw_artifact_shas': ['raw-sha']}, tmp_path)
    assert original == old
    assert set(package) == {'schema_version', 'package_id', 'canonical_viewpoints', 'viewpoint_revisions', 'viewpoint_claim_links', 'viewpoint_identity_candidates', 'viewpoint_identity_decisions'}
    revision = package['viewpoint_revisions'][0]
    assert revision['supersedes_revision_id'] == m.OLD
    assert revision['scope']['scripture_scope'] == ['路加福音 12:20']
    assert revision['provenance']['basis_identity_decision_ids'] == [package['viewpoint_identity_decisions'][0]['identity_decision_id']]
    link = package['viewpoint_claim_links'][0]
    assert link['decision_id'] != 'old-decision'
    assert link['evidence_bindings'] == original['viewpoint_claim_links', m.LINK]['evidence_bindings'][:1]
    assert link['validated_against_viewpoint_revision_id'] == revision['viewpoint_revision_id']
    assert all(row['review_status'] == 'system_approved' for c in ('canonical_viewpoints', 'viewpoint_revisions', 'viewpoint_claim_links') for row in package[c])
    assert 'dual_model_consensus' in revision['approved_by']
    assert manifest['dragged']['viewpoint_claim_links'] == [m.LINK]


def test_cross_passage_or_blank_scope_rejected():
    p = proposal()
    p['scope']['scripture_scope'].append('馬太福音 2:20')
    with pytest.raises(ValueError, match='scope'):
        m.validate_proposal(p)
    p = proposal(); p['core_proposition'] = ' '
    with pytest.raises(ValueError):
        m.validate_proposal(p)


def test_unreviewed_extra_dependent_refuses_silent_repin(tmp_path):
    live = states()
    live['viewpoint_claim_links', 'unexpected'] = {**live['viewpoint_claim_links', m.LINK], 'viewpoint_claim_link_id': 'unexpected'}
    with pytest.raises(ValueError, match='dependents diverge'):
        m.package_for(live, proposal(), {'approved': True}, tmp_path)


def test_old_or_moved_revision_refuses_replay(tmp_path):
    live = states()
    live['canonical_viewpoints', m.TARGET]['current_revision_id'] = 'newer'
    with pytest.raises(ValueError, match='not the current'):
        m.package_for(live, proposal(), {'approved': True}, tmp_path)


def test_prepare_uses_bound_source_identity_not_legacy_display_id(monkeypatch):
    live = states()
    live['claims', m.CLAIM] = {'occurrences': [{'source_id': 'lecture'}], 'evidence_step_ids': ['e']}
    live['viewpoint_claim_links', m.LINK]['evidence_bindings'] = [{'evidence_step_id': 'e', 'source_fragment_id': 'f'}]
    live['viewpoint_claim_links', m.LINK]['claim_id'] = m.CLAIM
    live['evidence_steps', 'e'] = {'source_fragment_ids': ['f']}
    live['source_fragments', 'f'] = {'source_id': 'SRC-canonical', 'paragraph_key': 'S0001', 'verbatim_excerpt': 'quote'}
    live['source_documents', 'SRC-canonical'] = {'source_id': 'SRC-canonical', 'transcript_id': 'lecture'}
    monkeypatch.setattr(m, 'load_context_source', lambda *a: ('/original', 'file-sha', SimpleNamespace(body_sha256='body', spoken_rows=[{'text': 'exact quote'}])))
    packet = m.prepare(live)
    assert packet['source_file_sha256'] == 'file-sha' and packet['complete_original'] == ['exact quote']
    live['source_fragments', 'f']['verbatim_excerpt'] = 'not in source'
    with pytest.raises(ValueError, match='quote'):
        m.prepare(live)


@pytest.mark.parametrize('change', ['invented_component', 'empty', 'foreign', 'duplicate', 'missing_reason', 'false_full'])
def test_link_repair_cannot_invent_hide_or_duplicate_evidence(change):
    p = proposal(); repair = p['member_link_repair']
    if change == 'invented_component': repair['component_text'] = '未讲的限定'
    elif change == 'empty': repair['included_binding_indices'] = []
    elif change == 'foreign': repair['included_binding_indices'] = [2]
    elif change == 'duplicate': repair['included_binding_indices'] = [0, 0]
    elif change == 'missing_reason': repair['excluded_binding_reason'] = ''
    else: repair['link_type'] = 'equivalent_full'
    with pytest.raises(ValueError): m.validate_proposal(p, states())


def test_review_receives_actual_link_postimage_identical_to_write(tmp_path):
    live, p = states(), proposal()
    preview, _ = m.package_for(live, p, {}, tmp_path, preview=True)
    final, _ = m.package_for(live, p, {'approved': True, 'raw_artifact_shas': ['raw']}, tmp_path)
    assert m.semantic_postimage(preview) == m.semantic_postimage(final)
    expected = m.semantic_postimage(final)['member_link']
    assert expected['evidence_bindings'] == [{'evidence_step_id': 'e1', 'source_fragment_id': 'luke'}]
    assert expected['validated_against_viewpoint_revision_id'] != m.OLD
    assert live['viewpoint_claim_links', m.LINK]['evidence_bindings'][1]['source_fragment_id'] == 'matt'
    # Late binding changes cannot inherit the content-review SHA.
    final['viewpoint_claim_links'][0]['evidence_bindings'].append(live['viewpoint_claim_links', m.LINK]['evidence_bindings'][1])
    assert m.semantic_postimage(preview) != m.semantic_postimage(final)


def test_single_focal_dependent_synopsis_is_reviewed_and_succeeds_without_lost_history(tmp_path):
    live = states()
    live['viewpoint_structures', m.STRUCTURE] = {'structure_id': m.STRUCTURE, 'current_revision_id': m.STRUCTURE_REVISION}
    live['viewpoint_structure_revisions', m.STRUCTURE_REVISION] = {'structure_id': m.STRUCTURE,
        'structure_revision_id': m.STRUCTURE_REVISION, 'revision': 1, 'revision_number': 1,
        'central_synthesis': 'old broad synopsis', 'unresolved_items': [],
        'focal_viewpoints': [{'viewpoint_revision_id': m.OLD, 'structure_role': 'central_claim'}]}
    old = deepcopy(live)
    preview, _ = m.package_for(live, proposal(), {}, tmp_path, preview=True)
    final, _ = m.package_for(live, proposal(), {'approved': True, 'raw_artifact_shas': ['raw']}, tmp_path)
    assert m.semantic_postimage(preview) == m.semantic_postimage(final)
    structure = final['viewpoint_structure_revisions'][0]
    assert structure['central_synthesis'] == proposal()['core_proposition']
    assert structure['supersedes_revision_id'] == m.STRUCTURE_REVISION
    assert structure['review_status'] == 'system_approved'
    assert live == old


def test_runner_reviews_compiled_member_postimage_not_old_link(tmp_path, monkeypatch):
    live = states()
    operations = [SimpleNamespace(collection=c) for c in ('canonical_viewpoints', 'viewpoint_revisions',
        'viewpoint_claim_links', 'viewpoint_identity_candidates', 'viewpoint_identity_decisions')]
    fake_store = SimpleNamespace(plan_package=lambda *a, **k: SimpleNamespace(operations=operations, as_dict=lambda: {'operations': []}))
    monkeypatch.setattr(m, 'PostgresKnowledgeStore', lambda: fake_store)
    monkeypatch.setattr(m, 'snapshot', lambda *a: deepcopy(live))
    monkeypatch.setattr(m, 'prepare', lambda *a: {'claim_id': m.CLAIM, 'source_path': '/source'})
    monkeypatch.setattr(m, 'exclusive_cvp_run_lock', lambda *a: nullcontext())
    monkeypatch.setattr(m, 'subscription_client', lambda *a: None)
    monkeypatch.setattr(m, 'StructuredJsonReviewerAdapter', lambda **k: None)
    calls = []
    def review(adapter, request, root, label):
        calls.append(label)
        m.write_immutable(root / f'raw-{label}.json', {'artifact_sha256': m.sha256_json(label)})
        if label == 'gpt-proposal':
            return proposal(), 1, 0
        after = request['proposed_postimage']['member_link']
        assert after['validated_against_viewpoint_revision_id'] != m.OLD
        assert after['evidence_bindings'] == live['viewpoint_claim_links', m.LINK]['evidence_bindings'][:1]
        assert request['effective_proposal']['member_link_repair']['excluded_binding_reason']
        return {'verdicts': [{'claim_id': m.CLAIM, 'approved': True, 'reason': 'reviewed exact postimage', 'problems': []}]}, 1, 0
    monkeypatch.setattr(m, 'review_call', review)
    assert m.run(tmp_path, False, None)['status'] == 'dry_run_pass'
    assert calls == ['gpt-proposal', 'opus-review-0']
    report = m.read_artifact(tmp_path / 'review-report.json')
    package = m.read_artifact(tmp_path / 'effective-package.json')
    assert report['reviewed_postimage_sha256'] == m.sha256_json(m.semantic_postimage(package))
    assert report['human_content_approval'] is False
