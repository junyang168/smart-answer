"""Exact owner-directed completion of withdrawn Evidence and erroneous edges.

Uses the original source runner and common SQL transaction. This is a newly
authorized write scope, not a retry of the old incomplete proposal. All old
answers remain immutable and the fixed parent owns one bounded amendment.
"""
import json
from pathlib import Path

from backend.api.canonical_repository.postgres_store import sha256_json, record_content_sha
from backend.pipeline import source_reconciliation as c

SCHEMA = 'wang_claim_evidence_completion_v1'
NAME = 'claim-evidence-completion-binding.json'
FIELDS = {'source_fragment_id', 'source_fragment_ids', 'anchor_quality',
          'support_eligibility', 'valid_for_identity_review'}
BUCKETS = {'eligible_evidence_step_ids', 'withheld_evidence_step_ids', 'occurrences'}


def validate(direction, *, snapshots=None):
    from backend.pipeline.source_reconciliation_runner import read, body, validate_raw, build_proposal_packet
    if (direction.get('schema_version') != SCHEMA or direction.get('authorized_by') != 'owner'
            or not direction.get('authorization_quote') or direction.get('human_content_approval') is not False
            or direction.get('additional_proposal_limit') != 1
            or direction.get('additional_review_limit') != 1
            or direction.get('additional_correction_limit') != 1
            or direction.get('artifact_sha256') != sha256_json({k:v for k,v in direction.items() if k!='artifact_sha256'})):
        raise ValueError('Evidence completion requires exact bounded owner revision authority')
    root = Path(direction['old_action_root'])
    if root.name != direction['old_action_id']:
        raise ValueError('Evidence completion original action identity differs')
    for name, file in [('old_materials','scoped-materials.json'), ('old_packet','proposal.packet.json'),
                       ('old_raw','proposal.raw.json'), ('old_initial_packet','initial_review.packet.json'),
                       ('old_initial_raw','initial_review.raw.json'), ('old_correction_packet','correction.packet.json'),
                       ('old_correction_raw','correction.raw.json')]:
        if Path(direction[name]['path']) != root/file:
            raise ValueError('Evidence completion must preserve all original input/answers')
        c.bound_bytes(direction[name], snapshots)
    old = body(read(root/'scoped-materials.json'))
    if (old['sql_scope']['run_id'] != direction['run_id'] or old['claim_issue_context'].get('scope_amendment')
            or direction['new_policy'] != old['claim_issue_context']['policy']):
        raise ValueError('Evidence completion cannot change original policy/root/group')
    c.guard_files(old, historical=bool(snapshots))
    packet = body(read(root/'proposal.packet.json'))
    if packet != build_proposal_packet(old):
        raise ValueError('original Evidence completion packet drift')
    first = validate_raw(root, 'proposal', packet)
    for stage in ('initial_review','correction'):
        validate_raw(root, stage, body(read(root/(stage+'.packet.json'))))
    if any((root/(stage+'.'+suffix)).exists() for stage in ('final_review','final_review_input_repair')
           for suffix in ('raw.json','reservation.json','raw.call-intent.json')):
        raise ValueError('Evidence completion cannot supersede a terminal/unknown call')
    states = c.index(old['before_records'])
    requested = direction['evidence_ids']
    inherited=sorted(p['object_id'] for p in old['manifest']['permissions'] if p['collection']=='evidence_steps' and not p.get('create'))
    if direction.get('retained_evidence_repair_ids') != inherited:
        raise ValueError('Evidence completion must preserve original authorized binding repair scope')
    if not requested or requested != sorted(set(requested)):
        raise ValueError('Evidence completion denominator duplicated/empty')
    owners = set()
    for eid in requested:
        ev = states.get('evidence_steps/'+eid)
        if (not ev or ev.get('support_eligibility') != 'withheld_missing_anchor'
                or ev.get('anchor_quality') != 'missing' or ev.get('source_fragment_id')
                or ev.get('source_fragment_ids') or ev.get('review_status') in c.HUMAN_HOLDS | {'rejected','human_hold'}
                or ev.get('speaker') != 'professor' or ev.get('stance') not in {'asserted','endorsed'}):
            raise ValueError('Evidence completion is not exact withdrawn missing-anchor material')
        owners.update(ev['produced_claim_ids'])
    if sorted(owners) != direction['context_claim_ids']:
        raise ValueError('Evidence completion must include every shared Claim owner')
    for cid in owners:
        claim = states.get('claims/'+cid)
        if not claim or claim.get('review_status') in {'human_hold','rejected','withheld_unreviewed'}:
            raise ValueError('Evidence completion Claim missing/held')
        for eid in requested:
            if (cid in states['evidence_steps/'+eid]['produced_claim_ids']) != (eid in claim['evidence_step_ids']):
                raise ValueError('Evidence completion ownership is not reciprocal')
    held=sorted(cid for cid in owners if states['claims/'+cid].get('review_status') in c.HUMAN_HOLDS)
    if direction.get('unchanged_held_context_claim_ids') != held:
        raise ValueError('Evidence completion must disclose and preserve contextual Claim review holds')
    withdrawn = direction['withdraw_relation_ids']
    if withdrawn != sorted(set(withdrawn)) or not withdrawn:
        raise ValueError('Evidence completion withdrawal denominator duplicated/empty')
    for rid in withdrawn:
        relation = states.get('knowledge_relations/'+rid)
        if (not relation or relation.get('review_status') in c.HUMAN_HOLDS | {'human_hold','rejected'}
                or relation['from_id'] not in requested or relation['to_id'] not in requested
                or relation['relation_type'] != 'supports'):
            raise ValueError('Evidence completion withdrawal is outside exact approved endpoints')
    if direction.get('link_permissions'):
        raise ValueError('Evidence completion does not authorize Claim-CVP edits')
    pins = {(r['collection'],r['object_id']):r['pin'] for r in old['sql_scope']['read_pins']}
    for key in [('evidence_steps',i) for i in requested] + [('claims',i) for i in owners] + [('knowledge_relations',i) for i in withdrawn]:
        value=states[c.key(*key)]
        if pins.get(key) != [value['revision'],record_content_sha(value),False]:
            raise ValueError('Evidence completion current proof missing/retired')
    return old, first


def evidence_permission_ids(direction):
    return set(direction['evidence_ids']) | set(direction.get('retained_evidence_repair_ids',[]))


def direction_for(context, snapshots=None):
    proof=context.get('scope_amendment')
    if not proof:return None
    direction=json.loads(c.bound_bytes(proof,snapshots))
    return direction if direction.get('schema_version') == SCHEMA else None


def extra_permissions(direction, states):
    result=[c.Permission(collection='evidence_steps',object_id=eid,fields=sorted(FIELDS))
            for eid in direction['evidence_ids']]
    # Only Evidence bucket transitions and exact own-source anchors, never text.
    result += [c.Permission(collection='claims',object_id=cid,fields=sorted(BUCKETS))
               for cid in direction['context_claim_ids'] if cid not in direction['unchanged_held_context_claim_ids']]
    return result


def merge_permissions(base, extra):
    by={p.key:p.model_copy(deep=True) for p in base}
    for p in extra:
        if p.key in by:
            by[p.key].fields=sorted(set(by[p.key].fields)|set(p.fields))
        else:by[p.key]=p
    return list(by.values())


def validate_postimages(frozen, proposal, post):
    direction=direction_for(frozen.get('claim_issue_context',{}),frozen.get('physical_snapshots'))
    if not direction:return
    before=c.index(frozen['before_records'])
    allowed=set(direction['evidence_ids'])
    for eid in allowed:
        k='evidence_steps/'+eid;old,new=before[k],post[k]
        if (new==old or new.get('support_eligibility')!='eligible_with_label'
                or new.get('anchor_quality')!='verified_candidate' or new.get('valid_for_identity_review') is False
                or {f:v for f,v in c._semantic(old).items() if f not in FIELDS}
                   != {f:v for f,v in c._semantic(new).items() if f not in FIELDS}):
            raise ValueError('Evidence completion must restore only own binding/qualification, not its teaching')
        fids=set(new.get('source_fragment_ids',[]))|({new['source_fragment_id']} if new.get('source_fragment_id') else set())
        if not fids:
            raise ValueError('Evidence completion omitted its own actual source proof')
        for fid in fids:
            fragment=post.get('source_fragments/'+fid)
            if (not fragment or fragment['source_id'] != frozen['manifest']['source_id']
                    or fragment.get('anchor_state')!='source_version_bound'):
                raise ValueError('Evidence completion bound foreign/unverified quote')
            pin=next((p['pin'] for p in frozen.get('sql_scope',{}).get('read_pins',[])
                      if p['collection']=='source_fragments' and p['object_id']==fid),None)
            if pin and pin[2]:raise ValueError('Evidence completion cannot revive a retired fragment')
            if frozen.get('after'):
                source=frozen['after'][0]
                text=source['paragraphs'].get(fragment.get('paragraph_key'),{}).get('text','')
                quote=fragment.get('verbatim_excerpt','')
                if len(c.containing_anchor_fragments([fragment],quote,text,
                        frozen['manifest']['source_id'],fragment.get('paragraph_key'),source['body_sha256']))!=1:
                    raise ValueError('Evidence completion quote is not exact unique physical own body')
    for cid in direction['context_claim_ids']:
        k='claims/'+cid;old,new=before[k],post[k]
        if cid in direction['unchanged_held_context_claim_ids']:
            if new!=old:raise ValueError('Evidence completion cannot clear or mutate contextual Claim hold')
            continue
        if {f:v for f,v in c._semantic(old).items() if f not in BUCKETS} != {f:v for f,v in c._semantic(new).items() if f not in BUCKETS}:
            raise ValueError('Evidence completion cannot rewrite Claim or ownership')
        restored=allowed & set(old['evidence_step_ids'])
        for field,expected in [('eligible_evidence_step_ids',set(old.get('eligible_evidence_step_ids',[]))|restored),
                               ('withheld_evidence_step_ids',set(old.get('withheld_evidence_step_ids',[]))-restored)]:
            if set(new.get(field,[]))!=expected or len(new.get(field,[]))!=len(expected):
                raise ValueError('Evidence completion changed unrelated qualification buckets')
    for rid in direction['withdraw_relation_ids']:
        if post['knowledge_relations/'+rid] != before['knowledge_relations/'+rid]:
            raise ValueError('withdrawal preserves original relation payload/history')


def review_items(frozen):
    direction=direction_for(frozen.get('claim_issue_context',{}),frozen.get('physical_snapshots'))
    if not direction:return []
    before=c.index(frozen['before_records'])
    result=[{'record_key':'knowledge_relation_withdrawal/'+rid,
             'before':before['knowledge_relations/'+rid], 'operation':'retire',
             'instruction':'Independently verify the erroneous supporting argument under the complete professor source. '
                           'Approve logical withdrawal only if both endpoint teachings remain accurately source-bound. '
                           'Original payload is preserved; no source, Claim, CVP or endpoint rewrite.'}
            for rid in direction['withdraw_relation_ids']]
    result += [{'record_key':'evidence_shared_owner/'+cid,'before':before['claims/'+cid],
                'operation':'unchanged_context',
                'instruction':'Review all restored own Evidence against this shared Claim and its complete own sources. '
                              'This does not approve or clear its historical Claim hold. Claim payload and buckets stay unchanged.'}
               for cid in direction['unchanged_held_context_claim_ids']]
    return result


def initial_draft_review(frozen, proposal):
    """Expose an incomplete joint draft to its ORIGINAL initial reviewer.

    No records/dispositions are discarded or repaired here. In particular an
    explicit context retain or a misplaced withdrawal-review row stays visible.
    This packet can never authorize writing, even if every verdict approves it;
    the original single correction must produce a strictly valid proposal.
    """
    direction=direction_for(frozen.get('claim_issue_context',{}))
    if not direction or not frozen.get('sql_scope'):
        raise ValueError('joint Evidence draft requires exact bound SQL authority')
    c.guard_files(frozen)
    try:
        return c.review_packet(frozen, proposal,
            review_only=any(d.action=='unresolved' for d in proposal.dispositions))
    except ValueError as exc:
        finding=str(exc)
    before=c.index(frozen['before_records'])
    selected=frozen['inventory']['affected_keys']+frozen['inventory']['context_keys']
    return {
        'schema_version':c.VERSION, 'update_id':frozen['update_id'],
        'manifest':frozen['manifest'], 'before_source':frozen['before'],
        'current_sources':frozen['after'], 'context_sources':frozen.get('context_sources',[]),
        'sentence_inventory':frozen['sentence_inventory'], 'inventory':frozen['inventory'],
        'before_records':c.rows({k:before[k] for k in selected}),
        'claim_issue_context':frozen['claim_issue_context'],
        'proposal':proposal.model_dump(), 'proposal_sha256':sha256_json(proposal.model_dump()),
        'knowledge_relation_withdrawal_review':review_items(frozen),
        'provenance_contract':{'old_approval_is_history_only':True,
            'approval_basis':'unapproved_initial_joint_evidence_draft','correction_limit':1},
        'unresolved_proposal_review':{
            'schema_version':'joint_evidence_initial_draft_review.v1',
            'program_findings':[finding], 'content_write_authority':False,
            'mandatory_original_unique_correction':True,
            'explanation':'All raw candidate rows, reasons and dispositions are preserved. '
                'Review all source/postimages/consumer problems, including readonly context '
                'and misplaced review-only rows. A withdrawal is a separate review item, '
                'not a business record. Original inherited Evidence repair permissions remain '
                'available. No all-approved initial verdict can authorize this incomplete draft. '
                'Only the original unique correction followed by strict final validation '
                'and independent review can permit a write.'}}


def packet_receipt(frozen):
    from backend.pipeline.source_reconciliation_runner import read
    direction=direction_for(frozen.get('claim_issue_context',{}))
    if not direction:return None
    root=Path(direction['old_action_root'])
    return {'schema_version':SCHEMA,'original_action_id':direction['old_action_id'],
        'original_answers':{stage:read(root/(stage+'.raw.json'))['response'] for stage in ('proposal','initial_review','correction')},
        'evidence_ids':direction['evidence_ids'],'shared_claim_ids':direction['context_claim_ids'],
        'logical_withdrawals':direction['withdraw_relation_ids'],
        'unchanged_held_context_claim_ids':direction['unchanged_held_context_claim_ids'],
        'requirements':['Complete BOTH endpoint Evidence own-source bindings and qualification, never only remove the erroneous edge.',
            'Preserve Evidence statements, Claim statements, ownership, original sources, CVP core and all unaffected relations.',
            'Restore only the named Evidence in shared Claim eligible/withheld buckets; exact source anchors may be rebound.',
            'Revalidate retained AR/support relations independently; do not retain an unresolved semantic argument.',
            'Original answers and consumed correction remain history, not approval. This fixed owner amendment permits one proposal, one independent review, at most one correction/final.',
            'A withdrawal is represented by retaining its old payload plus the separate explicit retirement review item, not by changing endpoints/type or deleting an Evidence record.'],
        'human_content_approval':False}


def scoped_adapter(adapter, packet):
    from copy import copy
    direction=direction_for(packet.get('claim_issue_context',{}))
    if not direction:return adapter
    prompt='''本次是精确 owner 授权的 Evidence 完整修复，不是重试原未决候选。
必须补齐所有 evidence_completion_receipt.evidence_ids 各自教授逐字原文绑定，并独立判断摘要是否忠实；不得仅撤回错误关系就称完成。
Evidence 原表述、说话者、角色、所有权和 Claim 正文保持。只有具名 Evidence 的片段绑定、anchor_quality、support_eligibility 可提议修订；所有共用 Claim 的 eligible/withheld buckets 与自身 anchors 必须一致。
原关系 payload 保留，不改端点/type。错误关系的逻辑退役通过 knowledge_relation_withdrawal_review 单独完整审核。
若是审核包，除 proposal.dispositions 的全部 verdict 外，必须对 knowledge_relation_withdrawal_review 每个 record_key 各返回一个独立 verdict；缺失、拒绝或未决不能提交。
旧三个回答仅是诊断历史，不是批准；所有既有 CVP 影响与保留关系必须完整处理，不把 knowledge_relations 当 CVP impact。正常有界唯一纠正/终审，不能循环。'''
    result=copy(adapter)
    result._base_prompt=adapter._base_prompt+'\n'+prompt
    result._prompt=adapter._prompt+'\n'+prompt
    result.prompt_sha256=sha256_json({'prompt':result._prompt})
    return result


def package_withdrawals(package, materials):
    direction=direction_for(materials.get('claim_issue_context',{}))
    stated=package['source_repair_application'].get('knowledge_relation_withdrawals',[])
    expected=direction['withdraw_relation_ids'] if direction else []
    if stated!=expected:
        raise ValueError('reviewed source package withdrawal scope differs')
    return tuple(('knowledge_relations',rid) for rid in expected)
