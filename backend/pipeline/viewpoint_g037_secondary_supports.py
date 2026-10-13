"""Owner-authorized single secondary connection repair after applied Claim repair.

Consume the saved post-repair audit and judgment, never replay the 19 operations
or adjudication. Keep the applied Claim package in the completed-source seed.
"""
from copy import deepcopy
from pathlib import Path

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import file_sha256
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable

ROOT = 'g037-owner-claim-condition-repair-20261005-v1'
LINK = 'VCL-c0671b32a7c13f43c06d'
CLAIM = 'DK-2bfaaa978b45d365b420-P04-CL002'
CV = 'CV-84ff210efbba6aeff1e0'
PACKET_SHA = 'ad7fdad620a7f44f0e5e651c67c5e33bf0515e8cf228d630d4d873d5ccf43d3c'
HANDOFF_SHA = 'a032cfed9ffbabb18af41529c90c0f39d879871ede8c3f73f417edc486351d02'
VERSION = 'wang-g037-owner-secondary-supports-v1'
PROOF_NAMES = ('packet.json','before.json','raw-gpt.json','raw-opus.json',
    'raw-gpt.request-encoding.json','raw-opus.request-encoding.json',
    'owner-authorization.json','review-report.json','effective-package.json',
    'dry-run-plan.json','backup.json','apply-intent.json','apply-receipt.json','verified-result.json')


def verify_transition(live, before, packet, plan, package):
    from backend.pipeline.viewpoint_authorized_source_repair_357 import fingerprint
    from backend.api.canonical_repository.postgres_store import normalize_package,record_content_sha
    normalized=normalize_package(package)
    reverse = deepcopy(live)
    originals = {('claims',packet['claim_revision']['before']['claim_id']):packet['claim_revision']['before']}
    originals.update({('viewpoint_claim_links',t['before_link']['viewpoint_claim_link_id']):t['before_link'] for t in packet['targets']})
    expected_counts = {('claims','update'):1,('viewpoint_claim_links','update'):6,
        ('viewpoint_identity_candidates','create'):6,('viewpoint_identity_decisions','create'):6}
    ops = plan['operations']
    if len(ops)!=19 or any(sum((o['collection'],o['operation'])==k for o in ops)!=n for k,n in expected_counts.items()):
        raise ValueError('not exact applied 19-operation Claim repair')
    if sorted({c for c,_ in live})!=before['collections']:
        raise ValueError('Registry collection boundary drift')
    seen=set()
    for o in ops:
        key=o['collection'],o['object_id']
        if key in seen:raise ValueError('duplicate applied operation')
        seen.add(key)
        expected=dict(o['payload'],revision=o['after_revision'])
        if live.get(key)!=expected:raise ValueError('applied Claim repair postimage drift')
        candidate=normalized.get(o['collection'],{}).get(o['object_id'])
        if candidate is None or record_content_sha(expected)!=record_content_sha(candidate):
            raise ValueError('applied plan/package binding drift')
        if o['operation']=='update':
            if key not in originals or record_content_sha(originals[key])!=o['before_sha256']:
                raise ValueError('applied predecessor binding drift')
            reverse[key]=originals[key]
        else:del reverse[key]
    if fingerprint(reverse,before['collections'])!=before['pre_fingerprint_sha256']:
        raise ValueError('whole Registry reverse fingerprint drift')
    return reverse


def saved_inputs(source_root, base, store):
    from backend.pipeline.viewpoint_authorized_source_repair_357 import snapshot
    from backend.pipeline.viewpoint_g037_claim_condition_repair import validate_package
    from backend.pipeline.viewpoint_content_blocked_fallback import validate, verify_saved_full_audit
    source_root=source_root.resolve()
    if source_root!=base/ROOT:raise ValueError('secondary repair requires exact applied root')
    package=read_artifact(source_root/'effective-package.json');validate_package(package)
    plan=read_artifact(source_root/'dry-run-plan.json')
    receipt=read_artifact(source_root/'apply-receipt.json');verified=read_artifact(source_root/'verified-result.json')
    if plan['change_set_id']!='KCS-e858cf921200b1952706' or receipt.get('status')!='applied' or receipt['change_set_id']!=plan['change_set_id'] or verified.get('status')!='applied_readback_verified' or verified['change_set_id']!=plan['change_set_id'] or verified['master_data_mutations']!=19:
        raise ValueError('missing actual applied Claim repair')
    verify_transition(snapshot(store),read_artifact(source_root/'before.json'),read_artifact(source_root/'packet.json'),plan,package)
    backup=read_artifact(source_root/'backup.json')
    if file_sha256(Path(backup['path']))!=backup['file_sha256']:raise ValueError('prior backup drift')
    adjudication=source_root/'continuation/audit-source-adjudication'
    packet=read_artifact(adjudication/'packet.json');report=read_artifact(adjudication/'gpt-final-handoff.json')
    for value,sha in ((packet,PACKET_SHA),(report,HANDOFF_SHA)):
        if value.get('artifact_sha256')!=sha or sha256_json({k:v for k,v in value.items() if k!='artifact_sha256'})!=sha:
            raise ValueError('saved post-repair judgment drift')
    audit=source_root/'continuation/content-blocked-fallback/assessment.json'
    if packet['audit_binding']!={'path':str(audit),'file_sha256':file_sha256(audit)} or not validate(read_artifact(audit)):
        raise ValueError('saved supplemental audit binding drift')
    verify_saved_full_audit(source_root/'continuation/independent-reaudit/audit.json')
    if not (adjudication/'raw-gpt.json').is_file():raise ValueError('saved adjudication missing; no model fallback')
    return audit, adjudication


def consume_report(audit, adjudication, store):
    from backend.pipeline.viewpoint_audit_source_adjudication import build_packet, source_review_adapter, validate_findings
    from backend.pipeline.viewpoint_resolution_runtime import subscription_client, call_model
    packet=build_packet(audit,store)
    if packet!=read_artifact(adjudication/'packet.json'):raise ValueError('saved post-repair packet drift')
    cache=adjudication/'raw-gpt.json'
    if not cache.is_file():raise ValueError('no saved adjudication; no fresh call permitted')
    adapter=source_review_adapter(subscription_client('codex','gpt-6.1-sol','high'),cache)
    adapter.max_request_bytes=500000
    def forbidden(_):raise ValueError('adjudication replay forbidden')
    adapter.generate=forbidden
    response,calls,_=call_model(adapter,packet,cache)
    if calls:raise ValueError('adjudication replay forbidden')
    findings=validate_findings(response,packet)
    report=read_artifact(adjudication/'gpt-final-handoff.json')
    if report['packet_sha256']!=packet['artifact_sha256'] or len(report['actions'])!=2 or any(a['gpt']!=findings[a['item_id']] or a['verdict']!=findings[a['item_id']]['verdict'] for a in report['actions']):
        raise ValueError('saved complete adjudication mismatch')
    return report


def validate_target(packet):
    if len(packet['targets'])!=1:raise ValueError('only one secondary connection authorized')
    t=packet['targets'][0];old=t['before_link']
    if (old['viewpoint_claim_link_id'],old['claim_id'],old['viewpoint_id'],old['revision'],old['link_type'],t['claim']['revision'])!=(LINK,CLAIM,CV,1,'equivalent_component',1):
        raise ValueError('secondary connection authority drift')
    expected=deepcopy(old);expected.update(revision=2,link_type='supports',component_locator=None)
    if t['after_link']!=expected:raise ValueError('secondary postimage exceeds authority')


def reserve(root, source_root):
    reservation=source_root/'owner-secondary-supports-start.json'
    if reservation.exists():raise ValueError('secondary review already reserved; no replay')
    authority={'policy_id':VERSION,'authorization_quote':'同意','link_id':LINK,
        'output_root':str(root),'source_root':str(source_root),'human_content_approval':False,
        'max_fresh_reviews_per_provider':1,'prior_claim_repair_replay':False}
    write_immutable(reservation,authority);write_immutable(root/'owner-authorization.json',authority)


def proof_bindings(source_root):
    from backend.pipeline.viewpoint_cvp_recovery_executor import binding
    from backend.pipeline.viewpoint_content_blocked_fallback import verify_saved_full_audit
    paths=[source_root/n for n in PROOF_NAMES]
    paths.extend(sorted((source_root/'continuation/audit-source-adjudication').glob('*')))
    paths.extend(sorted((source_root/'continuation/content-blocked-fallback').rglob('*')))
    return [binding(p) for p in paths if p.is_file()]+verify_saved_full_audit(source_root/'continuation/independent-reaudit/audit.json')
