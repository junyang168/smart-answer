"""Read-only, exact g034 applied component disposition proof.

Historical deferred ledgers remain immutable. Only the three actually applied,
independently reviewed owner repairs can resolve this one deferred Claim.
"""
from copy import deepcopy
from pathlib import Path

from backend.api.canonical_repository.postgres_store import ChangeOperation, stored_operation_payload
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_authorized_source_repair_357 import snapshot, fingerprint
from backend.pipeline.viewpoint_resolution_runtime import read_artifact as read, call_model, subscription_client
from backend.pipeline.viewpoint_g034_theme_support_repair import BASE, CLAIM, CV, LINK, RECOVERY_ROOT, ROLES, adapter, verify_sources
from backend.pipeline.viewpoint_g034_prayer_link_review import ROOT as PRAYER, LINK as PRAYER_LINK, ANNOTATION, EVIDENCE, PROMPT as PRAYER_PROMPT
from backend.pipeline.viewpoint_owner_evidence_annotation import Verdict, validate_package
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.api.canonical_repository.viewpoint_production_safety import file_sha256

VERSION='wang_g034_applied_component_completion_v1'
PARENT=BASE/'campaign-recovery-76196a80bb08997f3b3c0abd2fdc38bb5cbf4fa4c46c60330099c9723d0f6a84-recovery-cbb24c76faeb-recovery-7041591295f4'


def binding(path):
    return {'path':str(path),'file_sha256':file_sha256(path)}


def validate_coverage(claim, links):
    if claim['claim_id']!=CLAIM or {l['viewpoint_claim_link_id'] for l in links}!={LINK,PRAYER_LINK}:
        raise ValueError('not exact authorized Claim components')
    expected={LINK:(0,15),PRAYER_LINK:(15,57)}
    for link in links:
        start,end=expected[link['viewpoint_claim_link_id']]
        if (link['claim_id']!=CLAIM or link['viewpoint_id']!=CV or link['revision']!=2
                or link['link_type']!='supports' or link['effective_state']!='active'
                or link['pinned_claim_revision']!=claim['revision']
                or link['component_locator']['canonical_spans']!=[
                    {'start_char':start,'end_char':end,'exact_text':claim['statement'][start:end]}]):
            raise ValueError('resolved support component drift or gap')
    if len(claim['statement'])!=57: raise ValueError('Claim text length drift')


def verify_ledger(ledger, claim_ids):
    if ledger['artifact_sha256']!=sha256_json({k:v for k,v in ledger.items() if k!='artifact_sha256'}):
        raise ValueError('historical ledger SHA mismatch')
    rows=ledger['claim_dispositions']
    if (ledger['status']!='incomplete' or ledger['findings'] or ledger['blocked_claims']
            or ledger['excluded_claims'] or ledger['unresolved_claim_ids']
            or sorted(r['claim_id'] for r in rows)!=sorted(claim_ids) or len(rows)!=10
            or any(r.get('apply_status')!='applied' for r in rows)):
        raise ValueError('not exact intact g034 deferred ledger')
    deferred=[r for r in rows if r['resolution_status']!='resolved']
    if len(deferred)!=1 or deferred[0]['claim_id']!=CLAIM or deferred[0]['resolution_status']!='deferred':
        raise ValueError('new deferred scope not authorized')
    row=deferred[0]
    if row['apply_status']!='applied' or [c['disposition'] for c in row['components']]!=['new_viewpoint','support_existing','deferred']:
        raise ValueError('original components or apply state drift')
    if row['components'][2]['spans']!=[{'start_char':51,'end_char':53,'exact_text':'禱告'}]:
        raise ValueError('not exact prayer deferral')
    return row


def build_proof(store, campaign_root, claim_ids):
    if Path(campaign_root).resolve()!=PARENT: raise ValueError('not authorized g034 campaign')
    states=snapshot(store);collections=sorted({c for c,_ in states})
    ledger_path=PARENT/'g034/production/run/scope-disposition-ledger.json'
    scope_path=ledger_path.with_name('scope-run.json')
    ledger=read(ledger_path);verify_ledger(ledger,claim_ids)
    scope=read(scope_path)
    if scope['disposition_status']!='incomplete' or scope['disposition_ledger_sha256']!=ledger['artifact_sha256']:
        raise ValueError('scope/ledger binding drift')
    claim=states['claims',CLAIM]
    theme_packet=read(RECOVERY_ROOT/'packet.json');prayer_packet=read(PRAYER/'packet.json')
    if claim!=theme_packet['claim'] or claim!=prayer_packet['claim']:
        raise ValueError('Claim changed since reviews')
    validate_coverage(claim,[states['viewpoint_claim_links',lid] for lid in (LINK,PRAYER_LINK)])
    validate_package(read(ANNOTATION/'reviewed-package.json'))
    # Validate saved raw answers against exact runtime/config/prompt/semantic
    # payload. Existence checked first, so this path cannot generate a response.
    for root,packet,identity in ((RECOVERY_ROOT,theme_packet,LINK),(PRAYER,prayer_packet,EVIDENCE)):
        verify_sources(packet)
        for label,provider,model in ROLES:
            raw=root/f'raw-{label}.json'
            if not raw.is_file(): raise ValueError('missing frozen approval; no model fallback')
            obj=adapter(provider,model) if root==RECOVERY_ROOT else StructuredJsonReviewerAdapter(
                client=subscription_client(provider,model,'high'),prompt=PRAYER_PROMPT,response_model=Verdict,
                schema_name='wang_g034_prayer_link_verdict_v1',compact_payload=True)
            obj.max_request_bytes=500000
            response,calls,_=call_model(obj,packet,raw);v=Verdict.model_validate(response)
            if calls or not v.approved or v.problems or v.evidence_step_id!=identity:
                raise ValueError('not exact saved dual approval')
    # Undo each exact applied normalized postimage in reverse. Every complete
    # Registry boundary must match its frozen before SHA, not only target rows.
    rewind=deepcopy(states);bindings=[binding(ledger_path),binding(scope_path)];changes=[]
    for root,collection,oid,before,pre_sha in (
            (RECOVERY_ROOT,'viewpoint_claim_links',LINK,theme_packet['before_link'],theme_packet['registry_before_sha256']),
            (PRAYER,'viewpoint_claim_links',PRAYER_LINK,prayer_packet['before_link'],prayer_packet['registry_before_sha256']),
            (ANNOTATION,'evidence_steps',EVIDENCE,read(ANNOTATION/'packet.json')['before_evidence'],read(ANNOTATION/'before.json')['pre_fingerprint_sha256'])):
        plan=read(root/'dry-run-plan.json');receipt=read(root/'apply-receipt.json')
        if (receipt.get('status')!='applied' or receipt.get('change_set_id')!=plan['change_set_id']
                or store.get_change_set_status(plan['fingerprint_sha256'])!='applied'):
            raise ValueError('repair not actually applied')
        if len(plan['operations'])!=(1 if root==ANNOTATION else 3): raise ValueError('repair operation scope drift')
        updates=[]
        for item in plan['operations']:
            op=ChangeOperation(**item);key=op.collection,op.object_id
            if rewind.get(key)!=stored_operation_payload(op): raise ValueError('exact normalized repair readback drift')
            if op.operation=='create' and op.collection in {'viewpoint_identity_candidates','viewpoint_identity_decisions'}:
                del rewind[key]
            elif op.operation=='update' and key==(collection,oid):
                updates.append(key);rewind[key]=before
            else: raise ValueError('unauthorized repair operation')
        if updates!=[(collection,oid)] or fingerprint(rewind,collections)!=pre_sha:
            raise ValueError('non-target Registry drift across repair boundary')
        changes.append(plan['change_set_id'])
        for name in ('packet.json','dry-run-plan.json','apply-receipt.json','verified-result.json','backup.json','raw-gpt.json','raw-opus.json'):
            bindings.append(binding(root/name))
        for b in read(root/'packet.json')['physical_bindings']:
            if file_sha256(Path(b['path']))!=b['file_sha256']: raise ValueError('physical source binding drift')
            bindings.append(binding(Path(b['path'])))
    return {'schema_version':VERSION,'campaign_root':str(PARENT),'group_key':'p019:p019:whole',
        'claim_ids':sorted(claim_ids),'resolved_claim_id':CLAIM,'status':'complete',
        'historical_scope_run_preserved':True,'current_registry_sha256':fingerprint(states,collections),
        'applied_change_sets':changes,'resolved_links':[states['viewpoint_claim_links',lid] for lid in (LINK,PRAYER_LINK)],
        'bindings':bindings,'new_model_calls':0,'registry_writes':0}


def verify_proof(campaign_root,path,claim_ids,store=None):
    from backend.pipeline.viewpoint_g052_new_cvp_repair import ROOT as G052_ROOT, verify_proof as verify_g052
    if Path(path).resolve()==G052_ROOT/'component-completion.json':
        return verify_g052(campaign_root,path,claim_ids,store)
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    if Path(path).resolve()!=RECOVERY_ROOT/'component-completion.json': raise ValueError('wrong bounded completion proof path')
    proof=read(Path(path))
    if proof!=build_proof(store or PostgresKnowledgeStore(),campaign_root,claim_ids):
        raise ValueError('applied component completion proof drift')
    return proof
