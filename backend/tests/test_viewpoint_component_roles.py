from copy import deepcopy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.api.canonical_repository.viewpoint_component_roles import component_role_findings, connection_counts
from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha, validate_foundation_change_set, ViewpointFoundationValidationError

spec = importlib.util.spec_from_file_location('component_role_independent_audit', Path(__file__).parents[2]/'scripts/audit-library.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def graph():
    claim = {'claim_id': 'C', 'revision': 3, 'statement': '結論成立，因為理由。', 'evidence_step_ids': ['E1', 'E2']}
    steps = {f'E{i}': {'evidence_step_id': f'E{i}', 'source_fragment_id': f'F{i}', 'support_eligibility': 'eligible_candidate'} for i in (1, 2)}
    fragments = {f'F{i}': {'fragment_id': f'F{i}', 'anchor_state': 'source_version_bound', 'verbatim_excerpt': f'自身正文{i}'} for i in (1, 2)}
    def link(i, role, start, end):
        return {'viewpoint_claim_link_id': f'L{i}', 'claim_id': 'C', 'viewpoint_id': 'V',
                'pinned_claim_revision': 3, 'validated_against_viewpoint_revision_id': 'R',
                'link_type': role, 'effective_state': 'active', 'review_status': 'system_approved',
                'decision_id': 'D', 'evidence_bindings': [{'evidence_step_id': f'E{i}', 'source_fragment_id': f'F{i}'}],
                'component_locator': {'claim_sha256': semantic_record_sha(claim),
                    'statement_component': claim['statement'][start:end],
                    'canonical_spans': [{'start_char': start, 'end_char': end, 'exact_text': claim['statement'][start:end]}]}}
    return [link(1, 'equivalent_component', 0, 4), link(2, 'supports', 5, 10)], {'C': claim}, steps, fragments


def independent(g):
    links, claims, steps, fragments = g
    collections = {'claims': claims, 'evidence_steps': steps, 'source_fragments': fragments,
                   'viewpoint_claim_links': {r['viewpoint_claim_link_id']: r for r in links}}
    store = SimpleNamespace(collection=lambda col: [{'object_id': k, 'payload': v} for k, v in collections[col].items()])
    return audit._audit_component_roles(store)


def findings(g):
    return component_role_findings(*g, claim_sha=semantic_record_sha)


def test_conclusion_and_reason_are_two_connections_but_one_claim_and_member():
    g = graph(); before = deepcopy(g)
    assert findings(g) == independent(g) == []
    assert connection_counts(g[0]) == {'linked_claims': 1, 'linked_connections': 2, 'member_claims': 1}
    assert g == before


@pytest.mark.parametrize('fault', ['overlap', 'same_component', 'whole', 'missing_locator', 'missing_spans',
    'missing_evidence', 'foreign_evidence', 'missing_fragment', 'context_only', 'hold', 'unbound_source',
    'empty_quote', 'fragment_hold', 'wrong_revision', 'wrong_sha', 'wrong_cvr', 'unapproved',
    'negative_offset', 'empty_span', 'wrong_text', 'bool_offset', 'internally_overlapping'])
def test_both_independent_and_transaction_validators_reject_invalid_pairs_without_mutation(fault):
    g = graph(); links, claims, steps, fragments = g; loc = links[1]['component_locator']
    if fault == 'overlap':
        loc['canonical_spans'] = [{'start_char': 3, 'end_char': 7, 'exact_text': claims['C']['statement'][3:7]}]
        loc['statement_component'] = claims['C']['statement'][3:7]
    elif fault == 'same_component': links[1]['component_locator'] = deepcopy(links[0]['component_locator'])
    elif fault == 'whole': links[0]['link_type'] = 'equivalent_full'; links[0]['component_locator'] = None
    elif fault == 'missing_locator': links[1]['component_locator'] = None
    elif fault == 'missing_spans': loc['canonical_spans'] = []
    elif fault == 'missing_evidence': links[1]['evidence_bindings'] = []
    elif fault == 'foreign_evidence': claims['C']['evidence_step_ids'] = ['E1']
    elif fault == 'missing_fragment': del fragments['F2']
    elif fault in ('context_only', 'hold'): steps['E2']['support_eligibility'] = 'context_only' if fault == 'context_only' else 'withheld_unreviewed'
    elif fault == 'unbound_source': fragments['F2']['anchor_state'] = 'unbound'
    elif fault == 'empty_quote': fragments['F2']['verbatim_excerpt'] = ''
    elif fault == 'fragment_hold': fragments['F2']['valid_for_identity_review'] = False
    elif fault == 'wrong_revision': links[1]['pinned_claim_revision'] = 2
    elif fault == 'wrong_sha': loc['claim_sha256'] = 'f' * 64
    elif fault == 'wrong_cvr': links[1]['validated_against_viewpoint_revision_id'] = 'old'
    elif fault == 'unapproved': links[1]['review_status'] = 'candidate'
    elif fault == 'negative_offset': loc['canonical_spans'][0]['start_char'] = -1
    elif fault == 'empty_span': loc['canonical_spans'][0]['end_char'] = 5
    elif fault == 'wrong_text': loc['canonical_spans'][0]['exact_text'] = '別句'
    elif fault == 'bool_offset': loc['canonical_spans'][0]['start_char'] = True
    elif fault == 'internally_overlapping': loc['canonical_spans'] *= 2; loc['statement_component'] *= 2
    before = deepcopy(g)
    assert findings(g) and independent(g)
    assert before == g


def test_adjacent_half_open_ranges_are_disjoint_and_retired_links_not_counted():
    g = graph(); claim = g[1]['C']; loc = g[0][1]['component_locator']
    loc.update(statement_component=claim['statement'][4:], canonical_spans=[{'start_char': 4, 'end_char': len(claim['statement']), 'exact_text': claim['statement'][4:]}])
    assert findings(g) == independent(g) == []
    g[0][1]['effective_state'] = 'retired'
    assert connection_counts(g[0]) == {'linked_claims': 1, 'linked_connections': 1, 'member_claims': 1}


def test_foundation_guard_checks_both_existing_and_new_link_together():
    g = graph(); g[0][1]['component_locator'] = deepcopy(g[0][0]['component_locator'])
    normalized = {'viewpoint_claim_links': {'L2': g[0][1]}}
    existing = {(col, oid): {'payload': value} for col, records in
                [('claims', g[1]), ('evidence_steps', g[2]), ('source_fragments', g[3]),
                 ('viewpoint_claim_links', {'L1': g[0][0]})] for oid, value in records.items()}
    with pytest.raises(ViewpointFoundationValidationError, match='conclusion/reason components overlap'):
        validate_foundation_change_set(normalized, existing)


def test_old_prompt_byte_identity_and_new_roles_are_separately_named():
    old = audit._viewpoint_prompt('V', {'core_proposition': '結論'}, [], semantic_contract_version=audit.CONTEXTUAL_SEMANTICS)
    new = audit._viewpoint_prompt('V', {'core_proposition': '結論'}, [], semantic_contract_version=audit.COMPONENT_SEMANTICS)
    assert 'wang_cvp_component_roles_v1' not in old
    assert '不是兩條 Claim' in new and '字元區間不得重疊' in new
    assert audit.contextual_semantic_rules(audit.CONTEXTUAL_SEMANTICS) == audit.SEMANTIC_RULES_PATH.read_text()


def test_new_model_identity_and_saved_legacy_identity_are_both_exact():
    from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
    from backend.api.canonical_repository.viewpoint_batch_resolution import CanonicalViewpointProposalResponse as Proposal
    from backend.pipeline.viewpoint_resolution_runtime import response_adapter
    from backend.pipeline.viewpoint_semantic_contract import semantic_prompt
    client = SimpleNamespace(model='gpt-6.1-sol', backend='codex_subscription', reasoning_effort='high')
    base = StructuredJsonReviewerAdapter(client=client, prompt=semantic_prompt('提議'), response_model=Proposal, schema_name='proposal')
    legacy = base.program_bound_copy(); new = response_adapter(base)
    keys = ('model_id', 'backend', 'prompt_sha256', 'generation_config_sha256')
    assert new.prompt_sha256 != legacy.prompt_sha256
    assert new.generation_config_sha256 != legacy.generation_config_sha256
    for expected in (base, legacy, new):
        restored = response_adapter(base, {k: getattr(expected, k) for k in keys})
        assert all(getattr(restored, k) == getattr(expected, k) for k in keys)
    assert base._prompt == base._base_prompt
    assert new.component_roles_copy() is new
