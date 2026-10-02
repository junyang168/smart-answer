from copy import deepcopy
from types import SimpleNamespace
import pytest
from backend.pipeline import viewpoint_357_scope_revision as m


def proposal():
    return {'core_proposition': '路12:20中的ψυχή应理解为魂，不应译成灵魂。',
            'scope': {'scripture_scope': ['路加福音 12:20'], 'audience_scope': [], 'historical_scope': []},
            'proposition_signature': {'subject': 'ψυχή', 'predicate': '理解为', 'object': '魂而非灵魂',
                'polarity': 'affirmed', 'modality': '应当', 'conditions': ['路12:20'],
                'temporal_scope': [], 'population_scope': []}, 'reason': '两处译法批评不同'}


def states():
    return {('canonical_viewpoints', m.TARGET): {'viewpoint_id': m.TARGET, 'current_revision_id': m.OLD, 'review_status': 'system_approved'},
            ('viewpoint_revisions', m.OLD): {'viewpoint_revision_id': m.OLD, 'viewpoint_id': m.TARGET,
                'core_proposition': 'old', 'scope': {}, 'provenance': {'basis_identity_decision_ids': ['old-decision']},
                'revision': 1, 'revision_number': 1},
            ('viewpoint_claim_links', m.LINK): {'viewpoint_claim_link_id': m.LINK, 'viewpoint_id': m.TARGET,
                'validated_against_viewpoint_revision_id': m.OLD, 'effective_state': 'active', 'claim_id': m.CLAIM}}


def test_successor_preserves_history_and_never_fabricates_human_approval(tmp_path):
    original = states()
    old = deepcopy(original)
    package, manifest = m.package_for(original, proposal(), {'approved': True, 'approval_basis': 'dual_model_consensus'}, tmp_path)
    assert original == old
    assert set(package) == {'schema_version', 'package_id', 'canonical_viewpoints', 'viewpoint_revisions', 'viewpoint_claim_links'}
    revision = package['viewpoint_revisions'][0]
    assert revision['supersedes_revision_id'] == m.OLD
    assert revision['scope']['scripture_scope'] == ['路加福音 12:20']
    assert revision['provenance']['basis_identity_decision_ids'] == ['old-decision']
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
