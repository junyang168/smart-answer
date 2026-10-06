"""Apply the exact once-reviewed CL005 support connection, never the source repair twice."""
from copy import deepcopy
import fcntl
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

from dotenv import load_dotenv
from backend.pipeline import viewpoint_s201129_link_support_review as review
from backend.pipeline import viewpoint_s201129_source_repair as source
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable, stable_decided_at
from backend.pipeline.viewpoint_validation_context import validated_history
from backend.api.canonical_repository.postgres_store import stored_operation_payload
from backend.api.canonical_repository.viewpoint_production_safety import exclusive_cvp_run_lock

ROOT = source.BASE/'s201129-owner-cl005-supports-apply-20261006-v1'
PACKAGE = ROOT/'effective-package.json'
VERSION = review.VERSION
REVIEWER = 's201129_cl005_owner_scoped_opus_terminal_review'
RAW = '0e04492f18a10673d27b45d8ff7eac5c7951fb8cfd8826fa8e837c2cd31b363b'
PINS = {
    'packet.json':'9dd508d8d36690612202cd723bc54aeb9be65a784ae59bf06eb0b646986b9a9a',
    'raw-opus.json':'596737ee6c4f4aa0d612e84bc2e51f0d8fd1ff637920d798214c7c3a95c13d39',
    'raw-opus.request-encoding.json':'632f7532cf517d02d77ada86477193d054fc90995b9924fc878fbf7263fa614e',
    'review-report.json':'e0aea85a46cee8271818df1b8a3abc4922b5b94b870648b7ce5e88347fcd7359',
    'review-reservation.json':'8b49e7742dc0260ee554b2d29ee6761c7a85e4c3ed946b52c84efbc5e899dde2',
}


@validated_history
def reviewed_inputs():
    for name, pin in PINS.items():
        if source.file_sha256(review.ROOT/name) != pin:
            raise ValueError('exact support review physical drift: '+name)
    packet = read_artifact(review.ROOT/'packet.json')
    review.validate_target(packet)
    raw = read_artifact(review.ROOT/'raw-opus.json')
    report = read_artifact(review.ROOT/'review-report.json')
    reservation = read_artifact(review.ROOT/'review-reservation.json')
    encoding = read_artifact(review.ROOT/'raw-opus.request-encoding.json')
    if (raw['artifact_sha256'] != RAW or source.sha256_json({k:v for k,v in raw.items() if k!='artifact_sha256'}) != RAW
            or raw['model_id'] != 'claude-opus-5-5' or raw['backend'] != 'claude_subscription'
            or raw['prompt_sha256'] != reservation['prompt_sha256']
            or raw['generation_config_sha256'] != reservation['config_sha256']
            or raw['request_payload_sha256'] != source.sha256_json(packet)
            or raw['response_sha256'] != source.sha256_json(raw['response'])
            or report['review'] != raw['response'] or report['status'] != 'Opus_approved_not_applied'
            or report['packet_sha256'] != source.sha256_json(packet)
            or reservation['packet_sha256'] != source.sha256_json(packet)
            or reservation['request_bytes'] != 82949 or reservation['GPT_new_calls'] != 0):
        raise ValueError('exact actual Opus model/request/proposal binding drift')
    proof = {k:v for k,v in encoding.items() if k not in {'artifact_sha256','schema_version'}}
    if (encoding['artifact_sha256'] != source.sha256_json({k:v for k,v in encoding.items() if k!='artifact_sha256'})
            or raw['request_encoding_proof_sha256'] != source.sha256_json(proof)
            or proof['semantic_payload_sha256'] != source.sha256_json(packet)
            or proof['restored_payload_sha256'] != source.sha256_json(packet)
            or not proof['exact_roundtrip_verified'] or proof['wire_request_bytes'] != 82949):
        raise ValueError('actual lossless support review request proof drift')
    verdicts = review.Review.model_validate(raw['response']).verdicts
    if len(verdicts)!=1 or verdicts[0].claim_id!=review.CLAIM or not verdicts[0].approved or verdicts[0].problems:
        raise ValueError('exact supports review incomplete or refused')
    scope, _, _, _ = source.reviewed_inputs()
    source.verify_sources(scope)
    return packet,raw


@validated_history
def parent_snapshot():
    """Rebuild the real 401-operation postimage from immutable applied proofs."""
    package = read_artifact(source.PACKAGE)
    source.validate_package(package)
    before, _ = source.saved_before()
    plan = read_artifact(source.APPLICATION_ROOT/'dry-run-plan.json')
    verified = read_artifact(source.APPLICATION_ROOT/'verified-result.json')
    receipt = read_artifact(source.APPLICATION_ROOT/'apply-receipt.json')
    if (plan['change_set_id'] != 'KCS-d398cd94915d1e4f669f' or receipt['status'] != 'applied'
            or receipt['change_set_id'] != plan['change_set_id'] or verified['change_set_id'] != plan['change_set_id']
            or verified['master_data_mutations'] != 401 or verified['status'] != 'applied_readback_verified'):
        raise ValueError('real parent single application proof missing')
    for row in verified['bindings']:
        if source.binding(Path(row['path'])) != row:
            raise ValueError('parent application proof physical drift')
    states = deepcopy(before)
    for op in plan['operations']:
        states[op['collection'],op['object_id']] = {**op['payload'],'revision':op['after_revision']}
    if source.fingerprint(states,sorted({c for c,_ in states})) != verified['post_fingerprint_sha256']:
        raise ValueError('complete parent postimage fingerprint drift')
    return states


def package_for(states,packet,raw):
    if source.fingerprint(states,sorted({c for c,_ in states})) != packet['current_Registry_fingerprint_sha256']:
        raise ValueError('reviewed complete parent Registry fingerprint drift')
    target=packet['targets'][0];old=target['before_link'];link=deepcopy(target['after_link'])
    if states['viewpoint_claim_links',review.LINK]!=old or states['claims',review.CLAIM]!=target['claim']:
        raise ValueError('review did not use actual applied source postimages')
    previous=states['viewpoint_identity_decisions',old['decision_id']]
    candidate=deepcopy(states['viewpoint_identity_candidates',previous['identity_candidate_id']])
    seed=source.sha256_json({'policy':VERSION,'packet':source.sha256_json(packet),'raw':RAW,'link':review.LINK})
    candidate.update(revision=1,candidate_claim_ids=[review.CLAIM],candidate_viewpoint_ids=[review.CV],
        proposed_action='match_existing',generation_fingerprint=seed)
    identity={'claims':candidate['candidate_claim_ids'],'viewpoints':candidate['candidate_viewpoint_ids'],
        'relations':candidate.get('seed_relation_ids',[]),'action':candidate['proposed_action'],
        'blockers':candidate.get('blocker_codes',[]),'coverage_snapshot_id':candidate.get('coverage_snapshot_id'),
        'generation_fingerprint':seed}
    if candidate.get('scope_manifest_sha256'):identity['scope_manifest_sha256']=candidate['scope_manifest_sha256']
    candidate['identity_candidate_id']='VIC-'+source.sha256_json(identity)[:20]
    decision=deepcopy(previous)
    decision.update(revision=1,identity_decision_id='VID-'+seed[:20],
        identity_candidate_id=candidate['identity_candidate_id'],input_sha256=seed,
        claim_link_decisions=[{'claim_id':review.CLAIM,'link_type':'supports'}],
        review_artifact_sha256=RAW,semantic_call_artifact_sha256s=[RAW],
        reviewer_model_ids=['claude-opus-5-5'],reviewer_kind='system',reviewer_id=REVIEWER,
        approval_basis='owner_scoped_independent_terminal_review',review_status='system_approved',
        created_at=stable_decided_at(ROOT),policy_version=VERSION,reason=raw['response']['verdicts'][0]['reason'])
    link['decision_id']=decision['identity_decision_id']
    body={'package_id':'S201129-CL005-SUPPORT-'+seed[:20],'viewpoint_claim_links':[link],
        'viewpoint_identity_candidates':[candidate],'viewpoint_identity_decisions':[decision]}
    return {**body,'source_repair_application':{'schema_version':VERSION,'approval_status':'not_human_approved',
        'review_root':str(review.ROOT),'review_packet_sha256':source.sha256_json(packet),'raw_sha256':RAW,
        'package_sha256':source.sha256_json(body),'parent_source_application':source.binding(source.PACKAGE),
        'user_authorization':packet['owner_authorization'],'GPT_new_calls':0,
        'bindings':[source.binding(review.ROOT/n) for n in PINS]}}


@validated_history
def validate_package(package):
    packet,raw=reviewed_inputs()
    if package != package_for(parent_snapshot(),packet,raw):
        raise ValueError('support store gate rejects unreviewed records or provenance')


def verify_operations(plan):
    ops=plan['operations']
    if (len(ops)!=3 or sum(o['collection']=='viewpoint_claim_links' and o['operation']=='update' and o['object_id']==review.LINK for o in ops)!=1
            or sum(o['collection']=='viewpoint_identity_candidates' and o['operation']=='create' for o in ops)!=1
            or sum(o['collection']=='viewpoint_identity_decisions' and o['operation']=='create' for o in ops)!=1):
        raise ValueError('supports requires exactly one link update and two fresh identities')


def validate_plan_package(plan,package):
    from backend.api.canonical_repository.postgres_store import normalize_package,record_content_sha
    verify_operations(plan)
    normalized=normalize_package(package)
    expected={(c,o):p for c,rows in normalized.items() for o,p in rows.items()}
    seen=set()
    for op in plan['operations']:
        key=op['collection'],op['object_id']
        if key in seen or key not in expected or record_content_sha(expected[key])!=record_content_sha(op['payload']):
            raise ValueError('plan includes duplicate, foreign, or unreviewed postimage')
        seen.add(key)
    if seen!=set(expected):raise ValueError('reviewed package/plan population drift')


def applied_child_object_ids():
    """Include the exact child proof rows in a partial parent read set.

    This is data loading, not admission: restore_parent_records still checks
    the original review, all physical proofs, SQL KCS and every postimage.
    Omitting either newly created identity must never mean ignoring its guard.
    """
    if not (ROOT/'apply-receipt.json').exists():
        return set()
    package=read_artifact(PACKAGE)
    plan=read_artifact(ROOT/'dry-run-plan.json')
    validate_plan_package(plan,package)
    return {op['object_id'] for op in plan['operations']}


def restore_parent_records(records):
    """Exact proof composition, not a waiver of the parent source postimage guard."""
    if not (ROOT/'apply-receipt.json').exists():return records
    packet,_=reviewed_inputs()
    package=read_artifact(PACKAGE);validate_package(package)
    plan=read_artifact(ROOT/'dry-run-plan.json');validate_plan_package(plan,package)
    receipt=read_artifact(ROOT/'apply-receipt.json');verified=read_artifact(ROOT/'verified-result.json')
    if (receipt['status']!='applied' or receipt['change_set_id']!=plan['change_set_id']
            or verified['status']!='applied_readback_verified' or verified['change_set_id']!=plan['change_set_id']
            or verified['master_data_mutations']!=3):
        raise ValueError('child support application not actually verified')
    for row in verified['bindings']:
        if source.binding(Path(row['path']))!=row:raise ValueError('child applied proof physical drift')
    result=restore_exact_child_postimages(records,plan,packet['targets'][0]['before_link'])
    with source.PostgresKnowledgeStore().connect() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        row=conn.execute('SELECT source_kind,source_sha256,status FROM wang_knowledge.change_sets WHERE change_set_id=%s',
            (plan['change_set_id'],)).fetchone()
        if not row or tuple(row)!=(VERSION,plan['source_sha256'],'applied'):
            raise ValueError('child actual KCS source/version/status drift')
    return result


def restore_exact_child_postimages(records,plan,old):
    verify_operations(plan)
    result=dict(records)
    for op in plan['operations']:
        key=op['collection'],op['object_id'];expected={**op['payload'],'revision':op['after_revision']}
        current=records.get(key)
        if (not current or current.get('retired') or current['payload']!=expected
                or current['revision']!=expected['revision']
                or current['content_sha256']!=source.semantic_record_sha(expected)):
            raise ValueError('child exact postimage drift: '+op['object_id'])
        if op['operation']=='create':del result[key]
    result['viewpoint_claim_links',review.LINK]={'payload':old,'revision':old['revision'],
        'content_sha256':source.semantic_record_sha(old),'retired':False}
    return result


def run():
    if ROOT.exists():raise ValueError('support apply already attempted; inspect receipts, no replay')
    if subprocess.check_output(['git','status','--porcelain'],cwd=source.PROJECT,text=True).strip():
        raise ValueError('commit store and source proof consumers before applying')
    packet,raw=reviewed_inputs();parent=parent_snapshot();collections=sorted({c for c,_ in parent})
    package=package_for(parent,packet,raw);store=source.PostgresKnowledgeStore()
    lock={'global_lock_path':str(source.BASE/'registry-cvp.lock')}
    with exclusive_cvp_run_lock(lock):
        live=source.snapshot(store)
        if live!=parent:raise ValueError('full current Registry is not exact applied parent')
        plan=store.plan_package(package,source_kind=VERSION)
        saved=json.loads(json.dumps(plan.as_dict()));validate_plan_package(saved,package)
        post=deepcopy(live)
        for op in plan.operations:post[op.collection,op.object_id]=stored_operation_payload(op)
        write_immutable(PACKAGE,package)
        write_immutable(ROOT/'dry-run-plan.json',saved)
        write_immutable(ROOT/'before-proof.json',{'parent':source.binding(source.PACKAGE),
            'collections':collections,'pre_fingerprint_sha256':source.fingerprint(live,collections),
            'before_link':packet['targets'][0]['before_link'],'Registry_writes':0})
    from backend.pipeline.viewpoint_backup_archive import verify_volume
    verify_volume()
    if shutil.disk_usage(source.PROJECT).free < 2*1024**3:raise ValueError('less than 2GiB free; stop')
    output=subprocess.run(['scripts/backup-registry.sh','before-357-cl005-support-'+source.sha256_json(str(ROOT))[:20]],
        cwd=source.PROJECT,check=True,capture_output=True,text=True)
    match=re.search(r'backup-registry: wrote (.+\.dump) \(',output.stdout)
    if not match:raise ValueError('backup did not report actual dump')
    backup=Path(match.group(1));catalog=subprocess.run(['pg_restore','--list',str(backup)],check=True,capture_output=True,text=True)
    if not catalog.stdout.strip():raise ValueError('empty restore catalog')
    write_immutable(ROOT/'backup.json',source.binding(backup))
    write_immutable(ROOT/'pg-restore-proof.json',{'backup':source.binding(backup),
        'catalog_sha256':hashlib.sha256(catalog.stdout.encode()).hexdigest(),'catalog_nonempty':True,'exit_code':0})
    with exclusive_cvp_run_lock(lock):
        if source.snapshot(store)!=parent or source.binding(backup)!=read_artifact(ROOT/'backup.json'):
            raise ValueError('final Registry or backup drift')
        validate_package(package);verify_volume()
        replay=store.plan_package(package,source_kind=VERSION)
        if json.loads(json.dumps(replay.as_dict()))!=saved:raise ValueError('exact dry-run replay drift')
        write_immutable(ROOT/'apply-intent.json',{'schema_version':VERSION,'package_sha256':source.sha256_json(package),
            'plan_sha256':source.sha256_json(saved),'single_apply_required':True,'backup':source.binding(backup)})
        receipt=store.apply_plan(replay,metadata={'source_repair_application':VERSION},
            expected_registry_fingerprint_sha256=source.fingerprint(parent,collections),registry_collections=collections)
        write_immutable(ROOT/'apply-receipt.json',receipt)
        actual=source.snapshot(store)
        if actual!=post:raise ValueError('full Registry exact postimage readback mismatch')
        source.verify_sources(source.reviewed_inputs()[0])
        write_immutable(ROOT/'verified-result.json',{'status':'applied_readback_verified',
            'change_set_id':replay.change_set_id,'master_data_mutations':3,
            'pre_fingerprint_sha256':source.fingerprint(parent,collections),
            'post_fingerprint_sha256':source.fingerprint(actual,collections),
            'Claim_Evidence_source_CVP_text_writes':0,'old_401_operations_replayed':False,
            'bindings':[source.binding(ROOT/n) for n in ('effective-package.json','dry-run-plan.json','before-proof.json',
                'backup.json','pg-restore-proof.json','apply-intent.json','apply-receipt.json')]
                +[source.binding(review.ROOT/n) for n in PINS]})
    return receipt


if __name__=='__main__':
    load_dotenv(source.PROJECT/'.env')
    with (source.BASE/'immediate-recovery-20261002-v1/recovery.lock').open('r+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:print(json.dumps(run()))
        except Exception as exc:
            if ROOT.exists():write_immutable(ROOT/'failure.json',{'error':str(exc),
                'apply_intent_present':(ROOT/'apply-intent.json').exists(),'automatic_retry':False})
            raise
