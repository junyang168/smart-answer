"""One sealed complete-input repair within the original Claim action.

Only the proven historical-occurrence scope/reviewer-adapter omissions are
accepted. Original model replies, issues and reservations remain immutable.
The existing source runner/store own generation, review and transaction.
"""
from pathlib import Path
from backend.pipeline import source_reconciliation as c
from backend.pipeline.source_reconciliation_runner import body, read, validate_raw
from backend.api.canonical_repository.postgres_store import sha256_json

SCHEMA = 'wang_claim_complete_input_repair_v1'
NAME = 'claim-complete-input-repair-binding.json'
PROFILE = 'current_source_anchors_historical_exact_v1'


def validate(direction, *, snapshots=None):
    if (direction.get('schema_version') != SCHEMA or direction.get('authorized_by') != 'owner'
            or not direction.get('authorization_quote') or direction.get('human_content_approval') is not False
            or direction.get('additional_proposal_limit') != 1 or direction.get('additional_review_limit') != 1
            or direction.get('additional_correction_limit') != 0
            or direction.get('defects') != ['current_anchor_scope_overrestricted', 'review_readonly_adapter_omitted']
            or direction.get('artifact_sha256') != sha256_json({k:v for k,v in direction.items() if k!='artifact_sha256'})):
        raise ValueError('complete-input repair lacks exact bounded authority')
    root=Path(direction['old_action_root'])
    if root.name != direction['old_action_id']:
        raise ValueError('complete-input repair original action identity drift')
    for name,file in [('old_materials','scoped-materials.json'),('old_packet','proposal.packet.json'),('old_raw','proposal.raw.json'),
                      ('old_initial_packet','initial_review.packet.json'),('old_initial_raw','initial_review.raw.json'),
                      ('old_correction_packet','correction.packet.json'),('old_correction_raw','correction.raw.json')]:
        if Path(direction[name]['path']) != root/file:
            raise ValueError('complete-input repair must pin all original stages')
        c.bound_bytes(direction[name], snapshots)
    old=body(read(root/'scoped-materials.json'))
    if (old['sql_scope']['run_id'] != direction['run_id'] or old['claim_issue_context'].get('scope_amendment')
            or direction['new_policy'] != old['claim_issue_context']['policy']):
        raise ValueError('complete-input repair cannot change policy, issue or group')
    c.guard_files(old, historical=bool(snapshots))
    from backend.pipeline.source_reconciliation_runner import build_proposal_packet, initial_review_base
    from backend.pipeline.source_review_input_repair import check_packet
    packet=body(read(root/'proposal.packet.json'))
    if packet != build_proposal_packet(old):raise ValueError('original full proposal packet drift')
    first=validate_raw(root,'proposal',packet)
    initial_packet=body(read(root/'initial_review.packet.json'))
    initial=validate_raw(root,'initial_review',initial_packet)
    from backend.pipeline.source_reconciliation_runner import effective_proposal
    check_packet(old,first['response'],initial_packet,initial_review_base(old,effective_proposal(old,first['response'])))
    correction_packet=body(read(root/'correction.packet.json'))
    expected={**packet,'initial_proposal':effective_proposal(old,first['response']).model_dump(),
              'Opus_review':initial['response'],'correction_round':1,'another_correction_allowed':False}
    if 'unresolved_proposal_review' in initial_packet:
        expected['unresolved_proposal_review']=initial_packet['unresolved_proposal_review']
    if correction_packet != expected:raise ValueError('original correction chain drift')
    correction=validate_raw(root,'correction',correction_packet)
    if (c.validate_review(initial_packet,initial['response'])
            or any((root/(stage+'.'+suffix)).exists() for stage in ('final_review','final_review_input_repair')
                   for suffix in ('raw.json','reservation.json','raw.call-intent.json'))):
        raise ValueError('complete-input repair cannot retry a terminal call or approved input')
    receipt=packet.get('read_only_adapter_receipt')
    if (not receipt or receipt.get('content_write_authority') is not False
            or 'read_only_adapter_receipt' in initial_packet):
        raise ValueError('review adapter omission not proved')
    retained=old['claim_issue_context'].get('retained_occurrence_context', [])
    permissions={p['object_id']:p for p in old['manifest']['permissions'] if p['collection']=='claims'}
    if (not retained or any('occurrences' in permissions[r['claim_id']]['fields'] for r in retained)
            or not any(d['action']=='unresolved' and d['record_key'] in {
                'claims/'+r['claim_id'] for r in retained} for d in correction['response']['dispositions'])):
        raise ValueError('current anchor scope omission not proved')
    from backend.pipeline.claim_issue_management import read_policy
    if 'occurrences' not in read_policy(direction['new_policy'],snapshots).claim_fields:
        raise ValueError('repair cannot acquire a field outside original policy')
    wanted=[c.split_key(k)[1] for k in old['inventory']['affected_keys'] if k.startswith('evidence_steps/')]
    if sorted(direction['evidence_ids'])!=sorted(wanted) or direction['link_permissions'] or direction['context_claim_ids']:
        raise ValueError('complete-input repair cannot expand graph or link permissions')
    return old,first


def input_receipt(frozen):
    """Original findings stay visible; corrected inputs are not model approval."""
    import json
    context=frozen['claim_issue_context']
    direction=json.loads(c.bound_bytes(context['scope_amendment']))
    old,_=validate(direction)
    root=Path(direction['old_action_root'])
    return {'schema_version':SCHEMA,'original_action_id':direction['old_action_id'],
        'input_defects':direction['defects'],
        'original_answers':{stage:read(root/(stage+'.raw.json'))['response']
                            for stage in ('proposal','initial_review','correction')},
        'historical_occurrences':old['claim_issue_context']['retained_occurrence_context'],
        'current_source_anchor_scope':PROFILE,
        'requirements':[
            'Return one complete strict proposal; the original rejected and unresolved answers are not approvals.',
            'Only current-source occurrence anchors may change; historical occurrences remain byte-equivalent and retired Evidence remains retired.',
            'Preserve existing Evidence fragment IDs and bindings; add only exact own-source fragments permitted by the original policy.',
            'Use precise quotations without inserting causal wording or joining independent paragraphs into a fabricated quote.',
            'Give exactly one disposition for each actual affected consumer; do not retain dispositions for abandoned uncreated candidates.',
            'Internal knowledge routes are read-only impact_only; unchanged approved links need no new semantic approval.',
            'One independent review follows this proposal; no extra correction or approval loop is available.'],
        'content_approval':False,'proposal_limit':1,'review_limit':1,'correction_limit':0}


def mark_context(context):
    context['current_occurrence_anchor_scope']=PROFILE
    context['complete_input_repair']={'schema_version':SCHEMA,'proposal_limit':1,'review_limit':1,
        'correction_limit':0,'original_answers_preserved':True,'content_approval':False}
