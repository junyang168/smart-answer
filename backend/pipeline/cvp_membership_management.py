"""Routine membership revalidation intake; never a semantic approval.

The old Claim action cannot repair a different domain by pretending unresolved
means retain. Preserve that action/budget and hand its exact finding to CVP
management. Membership proposals still need an independent review and the
shared SQL executor; this intake grants no write or retry permission.
"""
from backend.pipeline import source_reconciliation as c
from backend.api.canonical_repository.viewpoint_foundation import sha256_json

VERSION = 'wang_cvp_membership_management_v1'


def retirement_handoff(frozen, proposal, correction):
    unresolved = {d.record_key for d in proposal.dispositions if d.action == 'unresolved'}
    if not unresolved or any(c.split_key(k)[0] not in
            {'canonical_viewpoints', 'viewpoint_revisions'} for k in unresolved):
        return None
    # This is a domain transfer, not permission to bypass missing graph/source,
    # an unknown consumer, or any original proposal validator.
    if not frozen.get('claim_issue_context', {}).get('scope_amendment'):
        return None
    c.validate_proposal(frozen, proposal, review_only=True)
    states = c.index(frozen['before_records'])
    retired = {(p['collection'], p['object_id']) for p in
               frozen.get('sql_scope', {}).get('read_pins', []) if p['pin'][2] is True}
    targets = []
    covered = set()
    for k in sorted(unresolved):
        if not k.startswith('canonical_viewpoints/'):
            continue
        head = states[k]
        rk = c.key('viewpoint_revisions', head['current_revision_id'])
        revision = states.get(rk)
        candidate = head.get('created_from_candidate_id')
        if (rk not in unresolved or revision is None or revision['viewpoint_id'] != head['viewpoint_id']
                or head.get('identity_status') not in {'active', 'provisional'}
                or head.get('review_status') != 'system_approved'
                or revision.get('review_status') != 'system_approved'
                or not candidate or ('viewpoint_identity_candidates', candidate) not in retired):
            return None
        links = [p for key,p in states.items() if key.startswith('viewpoint_claim_links/')
                 and p['viewpoint_id'] == head['viewpoint_id']
                 and p.get('effective_state', 'active') == 'active']
        if any(p['link_type'] in {'equivalent_full', 'equivalent_component'} for p in links):
            return None  # A different finding, not the retired-membership intake.
        targets.append({'viewpoint_id': head['viewpoint_id'],
                        'viewpoint_revision_id': head['current_revision_id'],
                        'retired_creation_candidate_id': candidate,
                        'current_link_ids': sorted(p['viewpoint_claim_link_id'] for p in links)})
        covered |= {k, rk}
    if covered != unresolved:
        return None
    return {'schema_version': VERSION,
            'handoff_id': sha256_json({'update_id': frozen['update_id'], 'targets': targets}),
            'source_update_id': frozen['update_id'], 'targets': targets,
            'original_correction_artifact_sha256': correction.get('artifact_sha256'),
            'original_response_sha256': sha256_json(correction['response']),
            'original_proposal_sha256': sha256_json(proposal.model_dump()),
            'original_correction_budget_exhausted': True,
            'next_action': 'current_membership_revalidation',
            'waiting_for': 'system', 'write_authority': False,
            'old_unresolved_is_not_pass': True,
            'retired_members_must_not_be_revived': True,
            'supports_is_not_equivalent': True,
            'preserve_core_claims_sources_campaign': True}
