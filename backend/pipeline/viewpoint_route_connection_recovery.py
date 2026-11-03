"""Bounded continuation of a saved CVP-link exception, in the original job.

Supply omitted approved connection reasons and/or an upstream member restored
for an exact saved exception. Reuse the last actual proposal. Every old verdict,
call and consumed correction remains immutable; never start another proposal.
"""
import json
import subprocess
import hashlib
from pathlib import Path

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_batch_resolution import canonicalize_proposal, canonicalize_review
from backend.api.canonical_repository.viewpoint_observation_support import (
    ArgumentRouteProposalV3Response, ArgumentRouteReviewV3Response, ArgumentRouteReconsiderationV3Response,
)
from backend.pipeline.viewpoint_resolution_runtime import read_saved_model_response, write_immutable

DIRECTORY = 'approved-connection-followup-v1'


def _fixed_implementation(require_committed):
    project=Path(__file__).resolve().parents[2]
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=project,text=True).strip()
    paths=['backend/pipeline/'+name for name in (
        'viewpoint_route_connection_recovery.py','viewpoint_route_cvp_worker.py',
        'viewpoint_route_cvp_resolution.py','viewpoint_route_materials.py','viewpoint_route_resolution.py',
        'viewpoint_route_review_scope.py')]
    paths+=['backend/api/canonical_repository/'+name for name in ('postgres_store.py','cvp_link_judgment.py')]
    bindings={}
    for name in paths:
        content=(project/name).read_bytes()
        if require_committed and content != subprocess.check_output(['git','show',commit+':'+name],cwd=project):
            raise ValueError('connection continuation implementation is not committed')
        bindings[name]=hashlib.sha256(content).hexdigest()
    return commit, bindings


def verify_connection_reservation(parent, *, original_material_sha256=None, execution_dir=None):
    parent = Path(parent).resolve()
    receipt=json.loads((parent/'connection-continuation-reservation.json').read_text())
    if (receipt['artifact_sha256'] != sha256_json({k:v for k,v in receipt.items() if k != 'artifact_sha256'})
            or receipt['parent_job_root'] != str(parent)
            or (original_material_sha256 is not None and receipt['old_material_sha256'] != original_material_sha256)
            or any(sha256_json(json.loads((parent/name).read_text())) != sha
                   for name,sha in receipt['original_files'].items())):
        raise ValueError('connection continuation original job or saved files changed')
    if execution_dir is not None:
        current=json.loads((Path(execution_dir)/'cvp-route-materials.json').read_text())
        if current['artifact_sha256'] != receipt['material_sha256']:
            raise ValueError('connection continuation applied material differs from its reservation')
    return receipt


def prepare_connection_continuation(parent, packet, materials, *, proposer, reviewer,
                                    reconsiderer, max_correction_rounds, reserve=True,
                                    clarify_review_scope=False):
    from backend.pipeline.viewpoint_route_cvp_resolution import bind_attestation_source_ids
    parent = Path(parent).resolve()
    def read(name):return json.loads((parent / name).read_text())
    old, old_material, run = (read(n) for n in
        ('route-packet.json','cvp-route-materials.json','route-cvp-run.json'))
    for value, field in ((old,'packet_sha256'),(packet,'packet_sha256'),
                         (old_material,'artifact_sha256'),(materials,'artifact_sha256'),(run,'artifact_sha256')):
        if value[field] != sha256_json({k:v for k,v in value.items() if k != field}):
            raise ValueError('connection continuation artifact changed')
    if ((parent/'route-change-plan.json').exists() or not run['rounds']
            or run['rounds'][-1]['outcome'] != 'pass' or not run['cvp_re_review_exceptions']
            or run.get('proposal_validation_repairs') or run.get('input_recovery')
            or materials['viewpoint_revision_id'] != old_material['viewpoint_revision_id']
            or old['scope_label'] != packet['scope_label']
            or not packet.get('approved_connection_judgments')):
        raise ValueError('connection continuation requires an uncommitted, ordinary-pass CVP exception')
    # Only the exact exception's context Claim can acquire new active links.
    keys={key for e in run['cvp_re_review_exceptions'] for key in e['evidence_claim_component_keys']}
    allowed_claims={c['claim_id'] for c in old['claim_components'] if c['claim_component_key'] in keys}
    before={l['viewpoint_claim_link_id']:l for l in old_material['links']}
    after={l['viewpoint_claim_link_id']:l for l in materials['links']}
    added=set(after)-set(before)
    if clarify_review_scope and (old != packet or old_material != materials):
        raise ValueError('review scope clarification cannot change materials or membership')
    if old.get('approved_connection_judgments') is not None and not added and not clarify_review_scope:
        raise ValueError('connection continuation with existing rationale needs the exact repaired member')
    if (not set(before) <= set(after) or any(after[k] != v for k,v in before.items())
            or any(after[k]['claim_id'] not in allowed_claims for k in added)):
        raise ValueError('connection continuation changed unrelated membership')
    old_pins={(r['collection'],r['object_id']):r for r in old_material['record_pins']}
    new_pins={(r['collection'],r['object_id']):r for r in materials['record_pins']}
    mutable_head=('canonical_viewpoints',materials['viewpoint_id'])
    for key, pin in old_pins.items():
        if key == mutable_head and added:
            continue
        if new_pins.get(key) != pin:
            raise ValueError('connection continuation changed an existing source or Claim pin')
    if any(col != 'viewpoint_identity_decisions' and (col != 'viewpoint_claim_links' or oid not in added)
           for col,oid in set(new_pins)-set(old_pins)):
        raise ValueError('connection continuation introduced unrelated records')
    promoted_claims={after[k]['claim_id'] for k in added}
    if set(materials.get('context_claim_ids',[])) != set(old_material.get('context_claim_ids',[]))-promoted_claims:
        raise ValueError('connection continuation changed unrelated context membership')
    ignored={'artifact_sha256','record_pins','links','approved_connection_judgments','viewpoint','context_claim_ids'}
    if ({k:v for k,v in old_material.items() if k not in ignored}
            != {k:v for k,v in materials.items() if k not in ignored}):
        raise ValueError('connection continuation changed evidence or physical source material')
    packet_delta={'packet_sha256','approved_connection_judgments','connection_judgment_note'}
    if added:
        packet_delta |= {'claim_components','membership_ledger','approved_viewpoints','context_claim_ids'}
    if ({k:v for k,v in old.items() if k not in packet_delta}
            != {k:v for k,v in packet.items() if k not in packet_delta}
            or packet['approved_connection_judgments'] != materials['approved_connection_judgments']):
        raise ValueError('connection continuation changed unrelated model inputs')
    head_ignored={'summary','summary_basis','revision'} if added else set()
    if ({k:v for k,v in old_material['viewpoint'].items() if k not in head_ignored}
            != {k:v for k,v in materials['viewpoint'].items() if k not in head_ignored}):
        raise ValueError('connection continuation changed the CVP identity')
    n=run['rounds'][-1]['round']
    if [r['round'] for r in run['rounds']] != list(range(n+1)) or n > max_correction_rounds:
        raise ValueError('connection continuation original correction budget is inconsistent')
    expected={'raw-route-proposal-000.call-intent.json'}
    previous=None
    from backend.pipeline.viewpoint_route_review_continuity import continuity_packet, review_history_entry
    from backend.pipeline.viewpoint_route_validation_repair import (
        read_initial_saved_proposal,read_validated_changes,REVIEW_REPAIR_INSTRUCTION,
    )
    raw, initial_calls=read_initial_saved_proposal(proposer,old,parent)
    expected={Path(name).stem+'.call-intent.json' for name in initial_calls}
    technical_repairs=len(initial_calls)-1
    current=ArgumentRouteProposalV3Response.model_validate(canonicalize_proposal(
        bind_attestation_source_ids(raw,old)[0])[0]).model_dump(mode='json')
    last_review=None
    review_history=[]
    for i in range(n+1):
        proposal_doc=read(f'route-proposal-{i:03d}.json')
        review_doc=read(f'route-review-{i:03d}.json')
        review_packet=read(f'route-review-packet-{i:03d}.json')
        if (proposal_doc['proposal'] != current or proposal_doc['proposal_sha256'] != sha256_json(current)
                or review_packet['route_evidence_context'] != old
                or review_packet['route_proposal_sha256'] != sha256_json(current)):
            raise ValueError('connection continuation saved proposal chain changed')
        raw_review,review_calls=read_validated_changes(reviewer,review_packet,
            parent/f'raw-route-review-{i:03d}.json',repair_instruction=REVIEW_REPAIR_INSTRUCTION)
        last_review=ArgumentRouteReviewV3Response.model_validate(canonicalize_review(raw_review)[0]).model_dump(mode='json')
        if (review_doc['review'] != last_review or review_doc['review_sha256'] != sha256_json(last_review)
                or last_review['route_evidence_packet_sha256'] != old['packet_sha256']
                or last_review['route_proposal_sha256'] != sha256_json(current)):
            raise ValueError('connection continuation original reviewer answer changed')
        expected.update(Path(name).stem+'.call-intent.json' for name in review_calls)
        review_history.append(review_history_entry(i,sha256_json(current),sha256_json(last_review),last_review))
        if i == n:break
        correction_packet={'scope_label':old['scope_label'],'route_evidence_packet':old,
            'route_proposal_sha256':sha256_json(current),'route_proposal':current,
            'route_review_sha256':sha256_json(last_review),'route_review':last_review,
            'previous_correction':previous,
            'instruction':'Return a complete revised route set; the next review sees every route and attestation.'}
        from backend.pipeline.viewpoint_route_correction_changes import (
            correction_adapter, expand_correction, CorrectionChanges, MARKER, VERSION as CHANGES_VERSION)
        correction_path=parent/f'route-reconsideration-packet-{i:03d}.json'
        response_path=parent/f'raw-route-reconsideration-{i:03d}.json'
        active_reconsiderer=correction_adapter(reconsiderer,correction_path,response_path)
        if getattr(active_reconsiderer,'_response_model',None) is CorrectionChanges:
            correction_packet[MARKER]=CHANGES_VERSION
            correction_packet['instruction']='Return only actual edits and finding dispositions; the program preserves omitted fields and submits the complete expanded result for review.'
        correction_packet=continuity_packet(correction_packet,review_history,
            packet_path=correction_path,response_path=response_path)
        raw, correction_calls=read_validated_changes(active_reconsiderer,correction_packet,response_path)
        technical_repairs+=len(correction_calls)-1
        expected.update(Path(name).stem+'.call-intent.json' for name in correction_calls)
        from backend.pipeline.viewpoint_route_observation_restore import MARKER as RESTORE,contract
        last_response=parent/correction_calls[-1]
        response_context=(read(correction_path.name) if len(correction_calls)==1
            else read(last_response.stem+'-packet.json'))
        expanded=expand_correction(raw,ArgumentRouteProposalV3Response.model_validate(current),
            ArgumentRouteReviewV3Response.model_validate(last_review),old,
            expected_contract=correction_packet.get(MARKER),
            allow_observation_restore=response_context.get(RESTORE)==contract(old))
        previous=ArgumentRouteReconsiderationV3Response.model_validate(canonicalize_proposal(expanded)[0]).model_dump(mode='json')
        if 'program_validation_repairs' in run:
            from backend.api.canonical_repository.viewpoint_observation_support import bind_revision_retirements
            bound, _ = bind_revision_retirements(
                ArgumentRouteProposalV3Response.model_validate(previous['revised_proposal']), old)
            previous['revised_proposal'] = bound.model_dump(mode='json')
        review_history[-1]['finding_dispositions']=previous['finding_dispositions']
        if read(f'route-reconsideration-{i:03d}.json')['reconsideration'] != previous:
            raise ValueError('connection continuation original correction answer changed')
        current=ArgumentRouteReconsiderationV3Response.model_validate(
            bind_attestation_source_ids(previous,old)[0]).revised_proposal.model_dump(mode='json')
        expected.add(f'raw-route-reconsideration-{i:03d}.call-intent.json')
    from backend.pipeline.viewpoint_route_resolution import apply_confirmed_unattested_members
    current=apply_confirmed_unattested_members(
        proposal=ArgumentRouteProposalV3Response.model_validate(current),
        review=ArgumentRouteReviewV3Response.model_validate(last_review)).model_dump(mode='json')
    technical_limit=max_correction_rounds
    if run.get('correction_round_extension_sha256'):
        from backend.pipeline.viewpoint_route_round_extension import round_extension, FILENAME
        from backend.pipeline.viewpoint_route_cvp_worker import DEFAULT_POLICY,load_route_policy
        first=read(FILENAME);extension=round_extension(parent,first['job'],load_route_policy(DEFAULT_POLICY))
        if extension['artifact_sha256'] != run['correction_round_extension_sha256']:
            raise ValueError('connection continuation changed its original validation extension')
        technical_limit=extension['effective_max_correction_rounds']
    if (technical_repairs != run.get('program_validation_repairs',0)
            or technical_repairs > technical_limit
            or {p.name for p in parent.glob('raw-*.call-intent.json')} != expected
            or run['review_sha256'] != sha256_json(last_review)
            or run['proposal_sha256'] != sha256_json(current)
            or last_review['cvp_re_review_exceptions'] != run['cvp_re_review_exceptions']):
        raise ValueError('connection continuation has unknown calls or changed final verdicts')
    if added and n == max_correction_rounds:
        raise ValueError('new membership requires a remaining original correction')
    if clarify_review_scope:
        from backend.pipeline.viewpoint_route_review_scope import validate_scope_continuation, MARKER as SCOPE
        if SCOPE in review_packet:
            raise ValueError('review scope contract was already offered; no repeated clarification')
        validate_scope_continuation(current,last_review)
    path=parent/'connection-continuation-reservation.json'
    saved=json.loads(path.read_text()) if path.exists() else None
    names=saved['original_files'] if saved else sorted(p.name for p in parent.glob('*.json'))
    commit, implementation = _fixed_implementation(reserve)
    receipt={'schema_version':'wang_route_approved_connection_continuation_v1',
        'parent_job_root':str(parent),'original_files':{name:sha256_json(read(name)) for name in names},
        'old_material_sha256':old_material['artifact_sha256'],'material_sha256':materials['artifact_sha256'],
        'old_packet_sha256':old['packet_sha256'],'packet_sha256':packet['packet_sha256'],
        'original_correction_limit':max_correction_rounds,'previous_corrections_consumed':n,
        'mode':'correction' if added else 'review','added_link_ids':sorted(added),
        'defect':('exception_identified_missing_member_connection' if old.get('approved_connection_judgments') is not None
                  else 'approved_connection_rationale_omitted_from_route_packet'),
        'original_exceptions':run['cvp_re_review_exceptions'],'new_initial_proposal_calls':0,
        'human_content_approval':False,'automatic_retry_unknown_calls':False,
        'fixed_code_commit':commit, 'implementation_sha256s':implementation}
    if clarify_review_scope:
        from backend.pipeline.viewpoint_route_review_scope import CONTRACT
        receipt['defect']='upstream_enrichment_conflated_with_current_invalidity'
        receipt['review_scope_contract']=CONTRACT
    if 'program_validation_repairs' in run:
        receipt['previous_program_validation'] = {
            'used': run['program_validation_repairs'],
            'attempts': run.get('program_validation_attempts', [])}
        receipt['original_program_validation_limit']=technical_limit
    receipt['artifact_sha256']=sha256_json(receipt)
    if saved is not None and saved != receipt:
        raise ValueError('connection continuation reservation or fixed implementation changed')
    if reserve:write_immutable(path,receipt)
    return {'proposal':current,'proposal_sha256':sha256_json(current),
        'review':last_review,'review_sha256':sha256_json(last_review),
        'previous_correction':previous,'receipt':receipt}
