from copy import deepcopy
from types import SimpleNamespace
from contextlib import nullcontext
import pytest
from backend.pipeline import viewpoint_357_scope_revision as m


def approved_report():
    return {'approved': True, 'approval_basis': 'dual_model_consensus',
            'raw_artifact_shas': ['initial-proposal', 'initial-review', 'final-proposal', 'final-review'],
            'accepted_call_artifact_shas': ['final-proposal', 'final-review']}


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
    package, manifest = m.package_for(original, proposal(), approved_report(), tmp_path)
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
    final, _ = m.package_for(live, p, approved_report(), tmp_path)
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
    final, _ = m.package_for(live, proposal(), approved_report(), tmp_path)
    assert m.semantic_postimage(preview) == m.semantic_postimage(final)
    structure = final['viewpoint_structure_revisions'][0]
    assert structure['central_synthesis'] == proposal()['core_proposition']
    assert structure['supersedes_revision_id'] == m.STRUCTURE_REVISION
    assert structure['review_status'] == 'system_approved'
    assert live == old


@pytest.mark.parametrize('needs_correction', [False, True])
def test_runner_reviews_compiled_member_postimage_not_old_link(tmp_path, monkeypatch, needs_correction):
    # The obsolete orchestration is no longer a supported solution.
    # Current content validators and SQL action tests carry its safety rules.
    from backend.pipeline import viewpoint_357_scope_revision as worker
    with pytest.raises(RuntimeError,match='runtime is retired'):
        worker.run(None,False,None)


def test_corrected_identity_provenance_keeps_only_accepted_pair_and_preserves_history(tmp_path):
    from backend.api.canonical_repository.knowledge_models import ViewpointIdentityDecisionRecord
    live = states()
    live['viewpoint_identity_decisions', 'old-decision'].update(
        reviewer_id='old-reviewer', policy_version='old-policy', created_at='2026-10-02T00:00:00Z')
    report = approved_report()
    package, _ = m.package_for(live, proposal(), report, tmp_path)
    decision = ViewpointIdentityDecisionRecord.model_validate(package['viewpoint_identity_decisions'][0])
    assert decision.semantic_call_artifact_sha256s == ['final-proposal', 'final-review']
    assert decision.policy_version == 'wkp357_scope_revision_complete_postimage_v2'
    assert decision.input_sha256 == package['viewpoint_identity_candidates'][0]['generation_fingerprint']
    assert report['raw_artifact_shas'] == ['initial-proposal', 'initial-review', 'final-proposal', 'final-review']
    assert live['viewpoint_identity_decisions', 'old-decision']['policy_version'] == 'old-policy'


@pytest.mark.parametrize('pair', [[], ['final-proposal'], ['final-proposal']*2,
                                 ['final-proposal', 'foreign'], ['initial-proposal', 'final-proposal', 'final-review']])
def test_identity_rejects_missing_duplicate_foreign_or_extra_call_provenance(pair):
    report = approved_report(); report['accepted_call_artifact_shas'] = pair
    with pytest.raises(ValueError, match='accepted proposal/review pair'):
        m.accepted_review_pair(report)


@pytest.mark.parametrize('fault', [None, 'content', 'unrelated', 'history', 'source', 'unapplied'])
def test_saved_apply_readback_checks_store_assigned_revision_without_any_write(tmp_path, monkeypatch, fault):
    from contextlib import contextmanager
    before = {('claims', 'claim'): {'revision': 1, 'statement': 'unchanged'},
              ('canonical_viewpoints', 'v'): {'revision': 1, 'current_revision_id': 'old'}}
    after = deepcopy(before); after['canonical_viewpoints', 'v'] = {'revision': 2, 'current_revision_id': 'new'}
    after['viewpoint_revisions', 'new'] = {'revision': 1, 'core_proposition': 'reviewed'}
    update = m.ChangeOperation('update', 'canonical_viewpoints', 'v', m.record_content_sha(before['canonical_viewpoints', 'v']),
        m.record_content_sha(after['canonical_viewpoints', 'v']), 1, 2, {'revision': 1, 'current_revision_id': 'new'})
    create = m.ChangeOperation('create', 'viewpoint_revisions', 'new', None,
        m.record_content_sha(after['viewpoint_revisions', 'new']), None, 1, after['viewpoint_revisions', 'new'])
    from dataclasses import asdict
    docs = {'dry-run-plan.json': {'change_set_id': 'KCS-test', 'fingerprint_sha256': 'fp',
                                'operations': [asdict(update), asdict(create)]},
            'apply-receipt.json': {'status': 'applied', 'change_set_id': 'KCS-test'},
            'review-report.json': {'reviewed_postimage_sha256': m.sha256_json('reviewed')},
            'effective-package.json': {}, 'packet.json': {'source_path': '/source', 'source_file_sha256': 'sha'},
            'before.json': {'collections': ['canonical_viewpoints', 'claims'],
                            'pre_fingerprint_sha256': m.fingerprint(before, ['canonical_viewpoints', 'claims'])},
            'successor-manifest.json': {}}
    monkeypatch.setattr(m, 'read_artifact', lambda p: docs[p.name])
    monkeypatch.setattr(m, 'semantic_postimage', lambda p: 'reviewed')
    monkeypatch.setattr(m, 'file_sha256', lambda p: 'wrong' if fault == 'source' else 'sha')
    if fault == 'content': after['canonical_viewpoints', 'v']['current_revision_id'] = 'unexpected'
    if fault == 'unrelated': after['claims', 'claim']['statement'] = 'changed'
    monkeypatch.setattr(m, 'snapshot', lambda s: deepcopy(after))
    class Cursor:
        def execute(self, sql, args=None):
            self.sql = sql
            assert sql.startswith(('SET TRANSACTION', 'SELECT'))
        def fetchone(self):
            if self.sql.startswith('SELECT payload'):
                return None if fault == 'history' else (before['canonical_viewpoints', 'v'],)
            return ('KCS-test',)
    @contextmanager
    def cursor(): yield Cursor()
    @contextmanager
    def connect(): yield SimpleNamespace(cursor=cursor)
    # No apply_plan/plan_package or model client exists on this fake store.
    store = SimpleNamespace(connect=connect, get_change_set_status=lambda fp: 'failed' if fault == 'unapplied' else 'applied')
    if fault:
        with pytest.raises(ValueError): m.verify_saved_apply(tmp_path, store)
    else:
        result = m.verify_saved_apply(tmp_path, store)
        assert result['read_only_recovery'] and result['claim_source_mutations'] == 0
        assert result['status'] == 'applied_readback_verified'


def test_process_guard_excludes_only_own_screen_login_chain_not_other_writer():
    from backend.pipeline.viewpoint_cvp_recovery_supervisor import current_job_process_ids, remaining_producers
    cmd = 'python -m backend.pipeline.viewpoint_357_scope_revision --output-root /repair/root'
    table = {10: (9, cmd), 9: (8, 'login '+cmd), 8: (1, 'SCREEN '+cmd), 1: (0, 'launchd'),
             20: (19, cmd), 19: (18, 'login '+cmd), 18: (1, 'SCREEN '+cmd)}
    ours = current_job_process_ids(table, 10)
    assert ours == {8, 9, 10}
    assert set(remaining_producers({p: r[1] for p, r in table.items()}, '/repair')) - ours == {18, 19, 20}
    # A Python writing parent is not a launcher wrapper and cannot be hidden.
    table[9] = (8, cmd)
    assert current_job_process_ids(table, 10) == {10}
