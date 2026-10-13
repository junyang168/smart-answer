"""Fixed-parent, once-only review of a proven program normalization defect.

This is engineering recovery, not a second content correction. Original answers
and normal budgets remain immutable; an actual independent review is required.
"""
from copy import deepcopy, copy
from pathlib import Path

from backend.pipeline import source_reconciliation as c
from backend.api.canonical_repository.viewpoint_foundation import sha256_json

SCHEMA = 'wang_source_normalization_engineering_recovery_v1'
NAME = 'normalization-engineering-recovery.json'
BINDING = 'normalization-engineering-recovery-binding.json'
STAGE = 'normalization_recovery_review'
PROMPT = '''This is an independently budgeted engineering recovery review, not another GPT content correction.
The normalization_engineering_recovery receipt proves a program-created flag-only defect and preserves the old rejected answer.
Review the entire corrected proposal, own sources, citations and every consumer anew. Return all required verdicts, including citation-purpose verdicts.
The receipt and the original rejection's suggestions are not approval. No content/permission/endpoint/source change or further correction is authorized.'''


def scoped_adapter(adapter, packet):
    if 'normalization_engineering_recovery' not in packet:return adapter
    result=copy(adapter)
    result._base_prompt=adapter._base_prompt+'\n'+PROMPT
    result._prompt=adapter._prompt+'\n'+PROMPT
    result.prompt_sha256=sha256_json({'prompt':result._prompt})
    return result


def corrected(frozen, response):
    from backend.pipeline.source_reconciliation_runner import effective_proposal
    from backend.pipeline.deferred_claim_repair import BASIS_PROFILE
    scoped = deepcopy(frozen)
    scoped['claim_issue_context']['basis_normalization_profile'] = BASIS_PROFILE
    return effective_proposal(scoped, response)


def prove_delta(old, new):
    """Only independence flags may differ, never text, citations or decisions."""
    before, after = old.model_dump(), new.model_dump()
    stripped = deepcopy(after)
    changes = []
    if len(before['records']) != len(after['records']):
        raise ValueError('normalization recovery record denominator drift')
    for a, b, target in zip(before['records'], after['records'], stripped['records']):
        if a['collection'] != b['collection'] or a['object_id'] != b['object_id']:
            raise ValueError('normalization recovery record identity drift')
        if a == b:
            continue
        if a['collection'] != 'claims':
            raise ValueError('normalization recovery cannot change non-Claim records')
        occurrences_a, occurrences_b = a['payload']['occurrences'], b['payload']['occurrences']
        if len(occurrences_a) != len(occurrences_b):
            raise ValueError('normalization recovery occurrence denominator drift')
        for oi, (oa, ob) in enumerate(zip(occurrences_a, occurrences_b)):
            anchors_a, anchors_b = oa['anchors'], ob['anchors']
            if len(anchors_a) != len(anchors_b):
                raise ValueError('normalization recovery anchor denominator drift')
            for i, (left, right) in enumerate(zip(anchors_a, anchors_b)):
                if left != right:
                    candidate = deepcopy(right)
                    candidate['evidence_independence'] = left.get('evidence_independence')
                    if candidate != left:
                        raise ValueError('normalization recovery changed anchor content')
                    target['payload']['occurrences'][oi]['anchors'][i] = deepcopy(left)
                    changes.append({'record_key':'claims/'+a['object_id'], 'occurrence_index':oi,'anchor_index':i,
                        'before':left.get('evidence_independence'), 'after':right.get('evidence_independence')})
    if stripped != before or not changes:
        raise ValueError('normalization recovery lacks exact flag-only defect')
    return changes


def packet(root, frozen):
    from backend.pipeline.source_reconciliation_runner import (
        read, body, validate_raw, effective_proposal, build_proposal_packet,
        initial_review_base, disclosed_review_base)
    from backend.pipeline.source_review_input_repair import check_packet, add_receipt
    root = Path(root)
    direction = read(root/NAME)
    expected = {'schema_version':SCHEMA, 'action_root':str(root.resolve()),
        'materials_sha256':sha256_json(frozen), 'run_id':frozen['sql_scope']['run_id'],
        'additional_review_limit':1, 'additional_proposal_limit':0,
        'additional_correction_limit':0, 'human_content_approval':False,
        'defect':'declared_basis_group_overwritten'}
    if any(direction.get(k) != v for k,v in expected.items()) or not direction.get('engineering_direction'):
        raise ValueError('normalization recovery lacks fixed engineering authority')
    if not frozen.get('claim_issue_context', {}).get('complete_input_repair'):
        raise ValueError('normalization recovery requires strict complete-input parent')
    c.guard_files(frozen)
    for name in ('scoped-materials', 'proposal.packet', 'proposal.raw', 'initial_review.packet', 'initial_review.raw'):
        leaf = direction['original_files'][name]
        if Path(leaf['path']).resolve() != root/(name+'.json'):
            raise ValueError('normalization recovery foreign original leaf')
        c.bound_bytes(leaf)
    first_packet = body(read(root/'proposal.packet.json'))
    if first_packet != build_proposal_packet(frozen):
        raise ValueError('normalization recovery original proposal input drift')
    first = validate_raw(root,'proposal',first_packet)
    old = effective_proposal(frozen, first['response'])
    initial_packet = body(read(root/'initial_review.packet.json'))
    initial = validate_raw(root,'initial_review',initial_packet)
    check_packet(frozen,first['response'],initial_packet,
        disclosed_review_base(frozen,initial_review_base(frozen,old),saved=initial_packet))
    if c.validate_review(initial_packet,initial['response']):
        raise ValueError('normalization recovery cannot repeat an approved review')
    new = corrected(frozen,first['response'])
    delta = prove_delta(old,new)
    if direction['delta_sha256'] != sha256_json(delta):
        raise ValueError('normalization recovery defect proof drift')
    changed = {d['record_key'] for d in delta}
    rejected = {v['record_key'] for v in initial['response']['verdicts'] if not v['approved']}
    if rejected != changed:
        raise ValueError('normalization recovery cannot hide other review disagreements')
    result = add_receipt(frozen,first['response'],
        disclosed_review_base(frozen,c.review_packet(frozen,new)))
    # add_receipt binds source hashes only; the corrected proposal is already
    # independently derived above. Do not replace it with legacy normalization.
    result['normalization_engineering_recovery'] = {'authorization':direction,
        'exact_flag_delta':delta, 'original_review':initial['response'],
        'original_proposal_response_sha256':first['response_sha256'],
        'normal_content_budget_unchanged':True, 'another_recovery_allowed':False,
        'instruction':'Review the FULL corrected postimage independently. The program overwrote the model-declared basis group. Only the disclosed independence flags are restored; all original content/reasons/decisions are unchanged. Neither the old rejection nor this engineering receipt is approval. Return every required verdict, including diagnostic citation purpose.'}
    return new, result
