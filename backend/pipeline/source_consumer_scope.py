"""Separate business consumers from declared context and review-only rows.

This is a program diagnostic, never a projection of the model's proposal or a
write/retry authority. Every original disposition and unresolved reason stays
visible. Unknown rows still fail closed.
"""
from backend.pipeline import source_reconciliation as c
from backend.api.canonical_repository.postgres_store import record_content_sha, sha256_json


def declared_group_keys(frozen):
    before=c.index(frozen['before_records'])
    pins={(p['collection'],p['object_id']):p['pin'] for p in frozen.get('sql_scope',{}).get('read_pins',[])}
    groups={g['group_key'] for g in frozen['manifest']['groups']}
    for k,old in before.items():
        if pins.get(c.split_key(k))!=[old.get('revision',1),record_content_sha(old),False]:continue
        groups.update(old.get('claim_group_ids',[]))
        groups.update(v for v in (old.get('group_id'),old.get('claim_group_id')) if v)
    return groups


def receipt(frozen, proposal):
    before = c.index(frozen['before_records'])
    proposed = c.index(proposal.records)
    required = set(frozen['inventory']['affected_keys']) | set(proposed)
    keys = [d.record_key for d in proposal.dispositions]
    if len(keys) != len(set(keys)):
        raise ValueError('duplicate consumer dispositions')
    pins = {(p['collection'], p['object_id']): p['pin']
            for p in frozen.get('sql_scope', {}).get('read_pins', [])}
    from backend.pipeline.claim_evidence_completion import direction_for
    direction = direction_for(frozen.get('claim_issue_context', {}), frozen.get('physical_snapshots'))
    context_claims = set(direction.get('unchanged_held_context_claim_ids', [])) if direction else set()
    review_ids = set(direction.get('withdraw_relation_ids', [])) if direction else set()
    group_keys=declared_group_keys(frozen)
    referenced_outputs = {target for consumers in frozen['inventory'].get('reverse_edges', {}).values()
                          for target in consumers}
    classified = []
    for d in proposal.dispositions:
        k = d.record_key
        if k in proposed and c.split_key(k)[0] in {'viewpoint_structures', 'viewpoint_structure_revisions'}:
            raise ValueError('historical reference annotation cannot be proposed')
        if k in proposed and k.startswith('claims/') and c.split_key(k)[1] in context_claims:
            raise ValueError('held readonly context cannot be proposed')
        if k in required:
            kind = 'business_consumer'
        elif k in proposed:
            raise ValueError('auxiliary consumer cannot be proposed')
        else:
            col, oid = c.split_key(k)
            if (col == 'claims' and oid in context_claims and k in frozen['inventory']['context_keys']
                    and k in before and d.action == 'retain'
                    and pins.get((col, oid)) == [before[k]['revision'], record_content_sha(before[k]), False]):
                kind = 'unchanged_readonly_context'
            elif col == 'groups' and oid in group_keys and d.action in {'unresolved', 'impact_only'}:
                kind = 'group_impact_annotation_not_cvp'
            elif (col == 'knowledge_relation_withdrawal_review' and oid in review_ids
                    and d.action == 'impact_only' and 'knowledge_relations/'+oid in required):
                kind = 'independent_review_annotation_not_business_record'
            elif (col in {'viewpoint_structures', 'viewpoint_structure_revisions'}
                    and k in referenced_outputs and k in before
                    and d.action in {'unresolved', 'impact_only'}
                    and k not in {p['collection']+'/'+p['object_id']
                                  for p in frozen['manifest'].get('permissions', [])}
                    and pins.get((col, oid)) == [before[k]['revision'], record_content_sha(before[k]), False]
                    and before[k].get('review_status') not in {
                        'human_hold', 'human_review_required', 'rejected', 'withheld_unreviewed'}):
                # The frozen reverse index can mention an exact historical
                # output outside the selected business denominator. Preserve
                # the model annotation and expose its real row, not a missing
                # business consumer or authority to regenerate the structure.
                c.KNOWLEDGE_COLLECTIONS[col][0].model_validate(before[k])
                kind = 'pinned_historical_output_reference_annotation'
            else:
                raise ValueError('unproven extra consumer: '+k)
        classified.append({'record_key': k, 'kind': kind, 'action': d.action, 'reason': d.reason})
    missing = sorted(required - set(keys))
    unresolved = [r['record_key'] for r in classified if r['action'] == 'unresolved']
    return {'schema_version': 'wang_source_consumer_scope_diagnostic_v1',
            'proposal_sha256': sha256_json(proposal.model_dump()),
            'required_business_keys': sorted(required), 'missing_business_keys': missing,
            'items': classified, 'unresolved_keys': unresolved,
            'content_write_authority': False, 'automatic_retry': False,
            'original_proposal_unchanged': True,
            'all_pending_completion_obligations_preserved': True}
