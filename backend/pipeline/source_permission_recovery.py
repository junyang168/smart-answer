"""Fixed-parent engineering repair of an incorrectly described create contract.

Original answers and normal correction stay immutable. One complete proposal
sees the exact field/parent contract, then the original UNUSED final review.
No second correction, write approval, changed fields or source epoch is granted.
"""
from pathlib import Path
from copy import copy
from backend.api.canonical_repository.postgres_store import sha256_json
from backend.pipeline.source_reconciliation_runner import body,read,validate_raw,build_proposal_packet
from backend.pipeline.viewpoint_resolution_runtime import read_artifact,write_immutable

VERSION='wang_source_parent_permission_input_recovery_v1'
PROVENANCE_VERSION='wang_source_provenance_permission_input_recovery_v1'
NAME='source-permission-recovery-binding.json'
STAGE='permission_input_recovery_proposal'


def program_contract():
    from backend.pipeline import source_reconciliation as c
    return {'schema_version':PROVENANCE_VERSION,'program_owned_fields':sorted(c.PROVENANCE),
        'old_approval_is_history_only':True,'candidate_review_status':'candidate',
        'deterministic_after_review_changes':['fresh review fields','fresh candidate/decision IDs','dependent final Claim SHA'],
        'protected_review_states_not_lifted':True,'content_permission_expanded':False,
        'explanation':'Review history fields are program-owned, not extra semantic permission. '
            'Keep old history visible; do not inherit its approval. Only actual independent approval permits '
            'the compiler to bind fresh provenance. All semantic field permissions and real holds remain strict.'}


def packet(root,frozen,*,authorization=None):
    from backend.pipeline.cvp_sql_commit import check_binding, binding
    from backend.pipeline import source_reconciliation as c
    root=Path(root);authorization=authorization or read_artifact(root/NAME)['authorization'];check_binding(authorization)
    d=read_artifact(authorization['path'])
    provenance=d.get('schema_version')==PROVENANCE_VERSION
    if (d.get('schema_version') not in {VERSION,PROVENANCE_VERSION} or d.get('authorized_by')!='owner_engineering'
            or not d.get('authorization_quote') or d.get('human_content_approval') is not False
            or d.get('new_proposal_calls')!=1 or d.get('additional_corrections')!=0
            or d.get('review_stage')!='original_unused_final_review'
            or d.get('artifact_sha256')!=sha256_json({k:v for k,v in d.items() if k!='artifact_sha256'})
            or Path(d['action_root']).resolve()!=root.resolve()
            or d['materials']!=binding(root/'scoped-materials.json')
            or body(read(root/'scoped-materials.json'))!=frozen
            or d['run_id']!=frozen['sql_scope']['run_id']
            or not (frozen['manifest'].get('claim_issue') if provenance else frozen['manifest'].get('evidence_binding_repair'))):
        raise ValueError('source permission recovery fixed-parent/scope drift')
    creates=[p for p in frozen['manifest']['permissions'] if p['create']]
    if not provenance and (len(creates)!=1 or creates[0]['collection']!='source_fragments'
            or creates[0].get('parent_id')!=frozen['manifest']['source_id']):
        raise ValueError('source permission recovery requires one same-source field contract')
    original=[]
    for stage in ('proposal','initial_review','correction'):
        if d['original_leaves'][stage]!=binding(root/(stage+'.raw.json')):
            raise ValueError('source permission recovery original leaf drift')
        raw=validate_raw(root,stage,body(read(root/(stage+'.packet.json'))))
        original.append({'stage':stage,'response':raw['response'],'raw_artifact_sha256':raw['artifact_sha256']})
    if body(read(root/'proposal.packet.json'))!=build_proposal_packet(frozen):
        raise ValueError('source permission recovery original complete source input drift')
    # An already-issued final belongs to its exact original input; never use
    # this recovery to replace it, or turn an unknown/rejected call into retry.
    if not (root/(STAGE+'.raw.json')).exists() and any(root.glob('final_review.*')):
        raise ValueError('source permission recovery final stage already issued')
    c.guard_files(frozen)
    if provenance:
        contract=provenance_contract(frozen,original,
            body(read(root/'initial_review.packet.json')),body(read(root/'correction.packet.json')))
        receipt={'schema_version':PROVENANCE_VERSION,'authorization':authorization,
            'original_answers':original,'actual_program_provenance_contract':contract,
            'human_content_approval':False,'additional_corrections':0,
            'explanation':'The original correction input omitted the independent reviewer provenance contract. '
                'Review metadata is program-owned, excluded from semantic permission comparison and deterministically '
                'rebound ONLY after actual independent approval. This is not permission to promote a hold, inherit old '
                'approval, change Evidence qualification/ownership/source or edit CVPs. Supply one complete proposal '
                'under the ORIGINAL semantic permissions; original unused final must independently approve it. '
                'Original rejected answers remain history; zero further corrections.'}
        return {**build_proposal_packet(frozen),'permission_input_recovery':receipt}
    receipt={'schema_version':VERSION,'authorization':authorization,'original_answers':original,
        'actual_create_field_contract':creates[0],
        'new_object_count_limit':None,'each_object_must_have_exact_same_source_parent':True,
        'all_old_fragments_and_bindings_preserved':True,'human_content_approval':False,
        'explanation':'parent_id is a field contract for EVERY new own-source fragment, not a one-object allocation slot. '
            'Use separate exact paragraph-bound fragments for separate paragraphs/anchors. No concatenation, changed source, new Evidence, '
            'new Claim, ownership/type/qualification/relationship/CVP permission is added. Original rejection/unresolved remain history. '
            'Supply one complete candidate; strict original unused Opus final must approve the full postimage. Zero further corrections.'}
    return {**build_proposal_packet(frozen),'permission_input_recovery':receipt}


def provenance_contract(frozen,original,initial_packet,correction_packet):
    """Structural evidence of omitted program contract; never parse model prose."""
    from backend.pipeline import source_reconciliation as c
    if (not frozen['manifest'].get('claim_issue') or correction_packet.get('provenance_contract')
            or not initial_packet.get('provenance_contract',{}).get('old_approval_is_history_only')
            or not initial_packet['provenance_contract'].get('deterministic_after_review_changes')):
        raise ValueError('provenance recovery requires exact omitted correction contract')
    from backend.pipeline.source_reconciliation_runner import effective_proposal
    proposal=effective_proposal(frozen,original[-1]['response'])
    unresolved=[x.record_key for x in proposal.dispositions if x.action=='unresolved']
    before=c.index(frozen['before_records'])
    permissions={c.key(p['collection'],p['object_id']):p for p in frozen['manifest']['permissions'] if not p['create']}
    if (proposal.records or len(unresolved)!=1 or not unresolved[0].startswith('claims/')
            or unresolved[0] not in before or unresolved[0] not in permissions
            or 'statement' not in permissions[unresolved[0]]['fields']
            or before[unresolved[0]].get('review_status') in c.HUMAN_HOLDS|{'human_hold','rejected','withheld_unreviewed'}
            or len(proposal.claim_assessments)!=1
            or proposal.claim_assessments[0].cause not in {'evidence_binding','claim_statement'}
            or any(x.action not in {'retain','impact_only','unresolved'} for x in proposal.dispositions)):
        raise ValueError('provenance recovery requires one unheld writable missing Claim draft')
    return {'schema_version':PROVENANCE_VERSION,'pending_claim_key':unresolved[0],
        'program_owned_fields':sorted(c.PROVENANCE),
        'current_program_contract':program_contract(),
        'original_independent_review_contract':initial_packet['provenance_contract'],
        'old_approval_is_history_only':True,'candidate_requires_independent_review':True,
        'protected_review_states_not_lifted':True,'content_permission_expanded':False}


def adapter(original,packet):
    if not packet.get('permission_input_recovery'):return original
    result=copy(original)
    provenance=packet['permission_input_recovery'].get('schema_version')==PROVENANCE_VERSION
    suffix=('\n工程输入修复：审核履历字段由程序管理，不是新的语义字段权限；旧批准只作历史，新候选须实际独立审核后才由程序确定性重绑。真hold不得解除。按原权限与完整原文一次提出完整后像，禁止从理由代造后像，零追加纠正。' if provenance else '\n工程输入修复：原 parent_id create permission 是每一新片段的同源字段契约，不是单个对象数量槽位。不得扩权，按所附原三回答与完整原文一次提出全部完整后像；原纠正预算不重置，零追加纠正。')
    result._base_prompt=original._base_prompt+suffix;result._prompt=original._prompt+suffix
    result.prompt_sha256=sha256_json({'prompt':result._prompt})
    result.generation_config_sha256=sha256_json({'base':original.generation_config_sha256,'engineering_input_recovery':PROVENANCE_VERSION if provenance else VERSION})
    return result


def enable(store,authorization,output_root):
    from backend.pipeline.cvp_sql_commit import check_binding
    check_binding(authorization);d=read_artifact(authorization['path']);root=Path(d['action_root'])
    group=store.get_cvp_group(d['run_id']);action=store.get_cvp_action(d['run_id'],d['action_id'])
    if (not group or not action or group['campaign_sha256']!=d['campaign_sha256']
            or group.get('execution_roots',{}).get(d['action_id'])!=str(root.resolve())
            or not root.resolve().is_relative_to(Path(output_root).resolve()/d['run_id'])):
        raise ValueError('source permission recovery original SQL action/campaign/root drift')
    if store.cvp_action_committed(d['run_id'],d['action_id']):
        if not (root/NAME).exists() or read_artifact(root/NAME)!={'authorization':authorization}:
            raise ValueError('applied source recovery cannot acquire new authority')
        return  # Exact common SQL committed-action cache; no new model/apply.
    materials=action['materials']
    packet(root,materials,authorization=authorization)
    write_immutable(root/NAME,{'authorization':authorization})


def reviewed(stages,frozen):
    from backend.pipeline.source_reconciliation_runner import effective_proposal
    from backend.pipeline import source_reconciliation as c
    p=packet(stages.root,frozen);stages.guard(frozen)
    raw=stages.call(STAGE,p);proposal=effective_proposal(frozen,raw['response'])
    final=final_packet(stages.root,frozen,proposal,raw,p)
    stages.guard(frozen);review=stages.call('final_review',final)
    if not c.validate_review(final,review['response']):
        raise ValueError('source permission recovery actual final rejected; zero further correction')
    return proposal,review


def final_packet(root,frozen,proposal,raw,recovery_input):
    from backend.pipeline import source_reconciliation as c
    from backend.pipeline.source_reconciliation_runner import disclosed_review_base
    from backend.pipeline.source_review_input_repair import add_receipt
    base=c.review_packet(frozen,proposal)
    path=Path(root)/'final_review.packet.json'
    saved=body(read(path)) if path.exists() else None
    base=disclosed_review_base(frozen,base,saved=saved)
    final=add_receipt(frozen,raw['response'],base)
    final['permission_input_recovery']=recovery_input['permission_input_recovery']
    final['permission_recovery_proposal_artifact_sha256']=raw['artifact_sha256']
    return final
