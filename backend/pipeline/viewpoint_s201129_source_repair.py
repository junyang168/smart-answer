"""One exact owner-scoped source repair, using the actual terminal Opus review.

No model calls. Historical reviews are not upgraded to new approvals. The
store independently reconstructs this package; callers cannot supply a waiver.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import fcntl
import importlib.util
import json
from pathlib import Path
import re
import subprocess

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore, stored_operation_payload
from backend.api.canonical_repository.viewpoint_foundation import sha256_json, semantic_record_sha
from backend.api.canonical_repository.viewpoint_production_safety import file_sha256, exclusive_cvp_run_lock
from backend.pipeline.viewpoint_authorized_source_repair_357 import snapshot, fingerprint
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable, stable_decided_at

VERSION = 'wang_s201129_owner_opus_source_reconciliation_v1'
PROJECT = Path(__file__).resolve().parents[2]
BASE = Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357')
REVIEW_ROOT = BASE/'s201129-owner-preserve-human-hold-20261006-v1'
APPLICATION_ROOT = BASE/'s201129-owner-opus-reviewed-source-apply-20261006-v3'
PACKAGE = APPLICATION_ROOT/'effective-package.json'
SOURCE = 'SRC-S_201129-3b3ae033645d'
CAMPAIGN = BASE/'campaign-recovery-2fc29744d5b0204c7b1e472f1f0e0862a914c4ab2e1ee0c4742935aeb699914f-recovery-cfb58a2570d6-recovery-9b5039675be7-recovery-70522204e72b-recovery-dfe291992426-recovery-1e7557c492fc-recovery-2503db96ea62'
PREFIX = 'DK-3b3ae033645d-P02-'
DRAFT_SHA = '0082a4a91f83602992ead6624cc8084d305b02cbc86a2cad43285f8370fb5a9c'
WIRE_SHA = 'f7a557c81a4d9ec119f95bcfef8396aa2e4421c9eb3dd3f53c64978a06a24b8c'
RAW_SHA = '5910c0db26cd2e013f6ed57c277d5441851f2ff52c8c2c0c98cf4ec6c4220dec'
PROMPT_SHA = '5b72546460ea3618d85a5cc397a519a0d6a19edf9ef876bfe9ab418c85f467b4'
CONFIG_SHA = 'f522cb09c1262b5ce385c17eebb5c2529e0192edd21dd689d164b885cda9f2b5'
PINS = {
    'exact-draft-package.json': 'f1eec3e8a2eca2dd74e348afa4ae85a0293174f9aaf63493aa1a3bf599d8f133',
    'logical-review-packet.json': '57ab8717ba7a095d05b107196da423fdbe19fea56efbed128175e9a5802e8f18',
    'exact-review-packet.json': 'db64775ab24c9f40a0fbe41b93d1f0db60dd757f084a8475f6eb349ca19cb54d',
    'raw-exact-opus.json': '8dbdf9a2c78db03cb6e3d9578f19be5cf3e7061efe662b28fc5fb8ff7703cf36',
    'raw-exact-opus.request-encoding.json': 'ca434181a91816a112f8bd13596c648e3fa121767620d3700a3c9538ffdc6115',
    'exact-review-report.json': '66c3a2b051d7d49caaff05ffce334eeb7d9fc09ebead2383a5b76e350cbd7d5c',
    'exact-review-reservation.json': '54ec1cd90750987534289788c3f0aa25eb8bad6167e8374c037495383dea2579',
    'scope.json': '196f6b03e2273cd652802364db55e99376f894f1f63300cfdfad663a44bfb574',
    'owner-legacy-spot-check-disposition.txt': '1193f9a85915bac8c511ecfe8242dbd61776c355e8134d8e7b11833984fc979d',
}


def binding(path):
    return {'path': str(path), 'file_sha256': file_sha256(path)}


def historical_seed_projection(seed, scope):
    """Authenticate old membership without declaring old audits current.

    Only the single explicitly reviewed old-to-new physical source transition
    can be projected for prerequisite checking. Old proofs are never rewritten;
    fresh complete affected-group audits are required before a new seed exists.
    """
    projected = deepcopy(seed)
    changed = []
    original = scope['registry_source_before']['source_file_sha256']
    paths = {str(BASE.parents[2]/folder/'S 201129.json'):scope['source_files'][label]
             for label,folder in [('review','script_review'),('published','script_published')]}
    for index, row in enumerate(seed['bindings']):
        if row['path'] not in paths:
            continue
        expected = paths[row['path']]
        if row['file_sha256'] == expected:
            continue
        if row['file_sha256'] != original or file_sha256(Path(row['path'])) != expected:
            raise ValueError('historical seed source transition outside exact reviewed epoch')
        changed.append(deepcopy(row))
        projected['bindings'][index] = {'path':row['path'],'file_sha256':expected}
    if changed and seed['group_key'] not in {
            'p023:wife_clause_passive_forced_remarriage',
            'p023:marrying_man_passive_and_matt19_omission'}:
        raise ValueError('source transition found another affected completed group; audit scope must be explicit')
    return projected, changed


def _module(name, path, pin):
    if file_sha256(path) != pin:
        raise ValueError('frozen mechanical verifier drift')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_sources(scope):
    for label, folder in [('review', 'script_review'), ('published', 'script_published')]:
        path = BASE.parents[2]/folder/'S 201129.json'
        if file_sha256(path) != scope['source_files'][label]:
            raise ValueError('current physical S201129 source drift')
    if file_sha256(Path(scope['manifest_path'])) != scope['manifest_file_sha256']:
        raise ValueError('original 973/156 accepted manifest drift')


def reviewed_inputs():
    for name, pin in PINS.items():
        if file_sha256(REVIEW_ROOT/name) != pin:
            raise ValueError('exact reviewed artifact physical drift: '+name)
    draft = read_artifact(REVIEW_ROOT/'exact-draft-package.json')
    logical = read_artifact(REVIEW_ROOT/'logical-review-packet.json')
    wire = read_artifact(REVIEW_ROOT/'exact-review-packet.json')
    raw = read_artifact(REVIEW_ROOT/'raw-exact-opus.json')
    report = read_artifact(REVIEW_ROOT/'exact-review-report.json')
    scope = read_artifact(REVIEW_ROOT/'scope.json')
    reservation = read_artifact(REVIEW_ROOT/'exact-review-reservation.json')
    encoded = read_artifact(REVIEW_ROOT/'raw-exact-opus.request-encoding.json')
    codec = _module('s201129_pointer_decoder', BASE/'s201129-owner-complete-consumer-repair-20261006-v1-packet-dedup-v1/prepare-packet.py',
                    '56ec7a14cb94d35978893ae0e8f679f7a579a2d797e70c6f77c15cdbf30a010c')
    if (sha256_json(draft) != DRAFT_SHA or sha256_json(wire) != WIRE_SHA
            or codec.restore(wire) != logical or logical['draft_package_sha256'] != DRAFT_SHA
            or raw['artifact_sha256'] != RAW_SHA
            or sha256_json({k:v for k,v in raw.items() if k != 'artifact_sha256'}) != RAW_SHA
            or raw['model_id'] != 'claude-opus-5-5' or raw['backend'] != 'claude_subscription'
            or raw['prompt_sha256'] != PROMPT_SHA or raw['generation_config_sha256'] != CONFIG_SHA
            or raw['request_payload_sha256'] != WIRE_SHA
            or raw['response_sha256'] != sha256_json(raw['response'])
            or report['review'] != raw['response'] or report['draft_sha256'] != DRAFT_SHA
            or report['packet_sha256'] != WIRE_SHA
            or report['status'] != 'Opus_correction_approved_not_applied'
            or reservation['packet_sha256'] != WIRE_SHA or reservation['draft_sha256'] != DRAFT_SHA
            or reservation['prompt_sha256'] != PROMPT_SHA or reservation['config_sha256'] != CONFIG_SHA
            or reservation['GPT_calls_allowed'] != 0 or reservation['Opus_calls_allowed'] != 1):
        raise ValueError('actual terminal review/model/request/proposal binding drift')
    proof = {k:v for k,v in encoded.items() if k not in {'artifact_sha256', 'schema_version'}}
    if (encoded['artifact_sha256'] != sha256_json({k:v for k,v in encoded.items() if k != 'artifact_sha256'})
            or raw['request_encoding_proof_sha256'] != sha256_json(proof)
            or proof['semantic_payload_sha256'] != WIRE_SHA or proof['restored_payload_sha256'] != WIRE_SHA
            or not proof['exact_roundtrip_verified'] or proof['wire_request_bytes'] != 377262
            or proof['wire_request_bytes'] > 500000):
        raise ValueError('actual outgoing lossless encoding proof drift')
    review = raw['response']
    populations = [('claim_verdicts', {c['claim_id'] for c in draft['claims']}),
                   ('link_verdicts', {l['viewpoint_claim_link_id'] for l in draft['viewpoint_claim_links']}),
                   ('ancillary_verdicts', {r['object_id'] for r in logical['ancillary_consumer_before_after']})]
    if review['source_epoch']['object_id'] != SOURCE or not review['source_epoch']['approved'] or review['source_epoch']['problems']:
        raise ValueError('source epoch not approved')
    for key, expected in populations:
        rows = review[key]
        if (len(rows) != len(expected) or {r['object_id'] for r in rows} != expected
                or any(not r['approved'] or r['problems'] for r in rows)):
            raise ValueError('terminal review incomplete or refused: '+key)
    mechanics = _module('s201129_exact_mechanics', BASE/'s201129-owner-complete-consumer-repair-20261006-v1/prepare-complete-review.py',
                        '101f825db35fe6954f9e6c86bcb6e8b9d4dfacec5ee0d2c1f3d3682acd5deabe')
    mechanics.verify_mechanics(draft, logical)
    verify_sources(scope)
    return scope, draft, logical, raw


def saved_before():
    proof = read_artifact(APPLICATION_ROOT/'before.json')
    states = {(r['collection'], r['object_id']):r['payload'] for r in proof['before_records']}
    scope, _, _, _ = reviewed_inputs()
    if (proof['collections'] != scope['registry_collections']
            or fingerprint(states, proof['collections']) != scope['registry_fingerprint_sha256']
            or proof['pre_fingerprint_sha256'] != scope['registry_fingerprint_sha256']):
        raise ValueError('full before Registry fingerprint drift')
    return states, proof


def package_for(states, scope, draft, logical, raw):
    """Only deterministic provenance changes; exact approved semantic rows retained."""
    result = deepcopy(draft)
    reviewed_at = stable_decided_at(APPLICATION_ROOT)
    provenance = sha256_json({'version':VERSION, 'draft':DRAFT_SHA, 'wire':WIRE_SHA, 'raw':RAW_SHA,
                             'legacy_owner_disposition':PINS['owner-legacy-spot-check-disposition.txt']})
    for claim in result['claims']:
        if claim['claim_id'] == PREFIX+'CL004':
            if (claim['review_status'] != 'human_review_required'
                    or claim['review_note'] != states['claims',claim['claim_id']]['review_note']
                    or 'human_spot_check' not in claim['review_note']):
                raise ValueError('legacy exception cannot clear a different human hold')
        elif claim['review_status'] != 'candidate':
            raise ValueError('unexpected proposed Claim review state')
        claim.update(review_status='independent_terminal_reviewed', reviewed_by='claude-opus-5-5 independent terminal review',
                     reviewed_at=reviewed_at,
                     review_note='Exact owner-authorized corrected-source postimage approved by Opus subscription. Not fresh dual-review or human approval. Historical reviews/holds preserved; only CL004 legacy spot-check disposition consumed.',
                     source_reconciliation_review_sha256=RAW_SHA)
    claims = {c['claim_id']:c for c in result['claims']}
    result['viewpoint_identity_candidates'] = []
    result['viewpoint_identity_decisions'] = []
    for link in result['viewpoint_claim_links']:
        old = states['viewpoint_claim_links',link['viewpoint_claim_link_id']]
        previous = states['viewpoint_identity_decisions',old['decision_id']]
        candidate = deepcopy(states['viewpoint_identity_candidates',previous['identity_candidate_id']])
        seed = sha256_json({'review':provenance,'link':link['viewpoint_claim_link_id']})
        candidate.update(revision=1, candidate_claim_ids=[link['claim_id']], candidate_viewpoint_ids=[link['viewpoint_id']],
                         proposed_action='match_existing', generation_fingerprint=seed)
        identity = {'claims':candidate['candidate_claim_ids'],'viewpoints':candidate['candidate_viewpoint_ids'],
                    'relations':candidate.get('seed_relation_ids',[]),'action':candidate['proposed_action'],
                    'blockers':candidate.get('blocker_codes',[]),'coverage_snapshot_id':candidate.get('coverage_snapshot_id'),
                    'generation_fingerprint':seed}
        if candidate.get('scope_manifest_sha256'):
            identity['scope_manifest_sha256'] = candidate['scope_manifest_sha256']
        candidate['identity_candidate_id'] = 'VIC-'+sha256_json(identity)[:20]
        decision = deepcopy(previous)
        decision.update(revision=1, identity_decision_id='VID-'+seed[:20], identity_candidate_id=candidate['identity_candidate_id'],
                        input_sha256=seed, claim_link_decisions=[{'claim_id':link['claim_id'],'link_type':link['link_type']}],
                        review_artifact_sha256=RAW_SHA, semantic_call_artifact_sha256s=[RAW_SHA],
                        reviewer_model_ids=['claude-opus-5-5'], reviewer_kind='system',
                        reviewer_id='s201129_owner_scoped_opus_terminal_review',
                        approval_basis='owner_scoped_independent_terminal_review', review_status='system_approved',
                        created_at=reviewed_at, policy_version=VERSION,
                        reason='Exact owner-scoped source reconciliation and dependent link approved by terminal Opus review; no GPT preapproval, no inherited or fabricated dual-model approval.')
        link['decision_id'] = decision['identity_decision_id']
        link['component_locator']['claim_sha256'] = semantic_record_sha(claims[link['claim_id']])
        result['viewpoint_identity_candidates'].append(candidate)
        result['viewpoint_identity_decisions'].append(decision)
    body_sha = sha256_json(result)
    result['source_repair_application'] = {
        'schema_version':VERSION,'approval_status':'not_human_approved',
        'review_root':str(REVIEW_ROOT),'review_packet':logical,'review_artifacts':{'opus':raw},
        'reviewed_draft_sha256':DRAFT_SHA,'reviewed_wire_sha256':WIRE_SHA,'package_sha256':body_sha,
        'user_authorization':logical['owner_authorization'],
        'legacy_spot_check_disposition':binding(REVIEW_ROOT/'owner-legacy-spot-check-disposition.txt'),
        'review_provenance_changes_only':True,'GPT_review_calls':0,
        'before_binding':binding(APPLICATION_ROOT/'before.json'),
        'bindings':[binding(REVIEW_ROOT/name) for name in PINS]}
    return result


def validate_package(package):
    app = package.get('source_repair_application') or {}
    if app.get('schema_version') != VERSION or app.get('review_root') != str(REVIEW_ROOT):
        raise ValueError('S201129 repair outside exact authorized version/root')
    scope, draft, logical, raw = reviewed_inputs()
    states, _ = saved_before()
    if package != package_for(states, scope, draft, logical, raw):
        raise ValueError('reviewed source repair/provenance/postimages changed')


def plan_reviewed():
    scope, draft, logical, raw = reviewed_inputs()
    store = PostgresKnowledgeStore()
    with exclusive_cvp_run_lock({'global_lock_path':str(BASE/'registry-cvp.lock')}):
        states = snapshot(store)
        if fingerprint(states, scope['registry_collections']) != scope['registry_fingerprint_sha256']:
            raise ValueError('current full Registry changed since review')
        write_immutable(APPLICATION_ROOT/'before.json',{'collections':scope['registry_collections'],
            'pre_fingerprint_sha256':scope['registry_fingerprint_sha256'],
            'before_records':[{'collection':c,'object_id':o,'payload':p} for (c,o),p in sorted(states.items())]})
        package = package_for(states, scope, draft, logical, raw)
        plan = store.plan_package(package, source_kind=VERSION)
        allowed = {c for c,v in draft.items() if isinstance(v,list)} | {'viewpoint_identity_candidates','viewpoint_identity_decisions'}
        if any(o.collection not in allowed or o.operation not in {'update','create'} for o in plan.operations):
            raise ValueError('out-of-scope operation or deletion')
        for collection in allowed - {'viewpoint_identity_candidates','viewpoint_identity_decisions'}:
            if any(o.collection == collection and o.operation != 'update' for o in plan.operations):
                raise ValueError('original scope IDs must be preserved: '+collection)
        if (sum(o.collection == 'claims' for o in plan.operations) != 9
                or sum(o.collection == 'viewpoint_claim_links' for o in plan.operations) != 4
                or sum(o.collection == 'viewpoint_identity_candidates' and o.operation == 'create' for o in plan.operations) != 4
                or sum(o.collection == 'viewpoint_identity_decisions' and o.operation == 'create' for o in plan.operations) != 4):
            raise ValueError('exact Claim/link/fresh review denominator drift')
        post = deepcopy(states)
        for op in plan.operations:
            post[op.collection,op.object_id] = stored_operation_payload(op)
        write_immutable(PACKAGE,package)
        write_immutable(APPLICATION_ROOT/'dry-run-plan.json',json.loads(json.dumps(plan.as_dict())))
        write_immutable(APPLICATION_ROOT/'dry-run-result.json',{'status':'exact_store_plan_passed_not_applied',
            'operations':len(plan.operations),'pre_fingerprint_sha256':scope['registry_fingerprint_sha256'],
            'expected_post_fingerprint_sha256':fingerprint(post,scope['registry_collections']),
            'new_model_calls':0,'Registry_writes':0})
        return plan, post


def apply_reviewed():
    """Saved exact plan, fresh snapshot, one apply, full Registry readback."""
    from backend.pipeline.viewpoint_audit_link_repair import write_derived
    from backend.pipeline.viewpoint_backup_archive import verify_volume
    from backend.pipeline.viewpoint_reviewed_cvp_prepare import completed_repair_parent_snapshot
    if subprocess.check_output(['git','status','--porcelain'],cwd=PROJECT,text=True).strip():
        raise ValueError('commit exact source/store consumers and tests before writing')
    if any((APPLICATION_ROOT/n).exists() for n in ('apply-intent.json','apply-receipt.json','verified-result.json')):
        raise ValueError('single application already attempted; read receipts, never reapply')
    scope, draft, logical, raw = reviewed_inputs()
    before, proof = saved_before()
    package = package_for(before,scope,draft,logical,raw)
    if package != read_artifact(PACKAGE):
        raise ValueError('saved effective reviewed package drift')
    saved_plan = read_artifact(APPLICATION_ROOT/'dry-run-plan.json')
    store = PostgresKnowledgeStore()
    lock = {'global_lock_path':str(BASE/'registry-cvp.lock')}
    with exclusive_cvp_run_lock(lock):
        live = snapshot(store)
        if fingerprint(live,proof['collections']) != proof['pre_fingerprint_sha256']:
            raise ValueError('complete Registry drift before source apply')
        plan = store.plan_package(package,source_kind=VERSION)
        if json.loads(json.dumps(plan.as_dict())) != saved_plan:
            raise ValueError('saved/current complete operation and event plan drift')
        post = deepcopy(live)
        for op in plan.operations:
            post[op.collection,op.object_id] = stored_operation_payload(op)
        records = {(c,o):{'payload':p,'revision':p['revision'],
                         'content_sha256':semantic_record_sha(p),'retired':False} for (c,o),p in post.items()}
        # Authenticate previous repairs separately, then check the entire frozen
        # denominator against a read-only predecessor membership projection.
        manifest = read_artifact(Path(scope['manifest_path']))
        from backend.pipeline.viewpoint_reviewed_cvp_prepare import validate_completed_parent_audit
        from backend.pipeline.viewpoint_cvp_campaign_runner import binding as campaign_binding
        cfg = read_artifact(CAMPAIGN/'campaign.json')
        priors = []
        for row in cfg['completed_source_repairs']:
            if campaign_binding(row['path']) != row:
                raise ValueError('previous completed source repair physical drift')
            priors.append(read_artifact(Path(row['path'])))
        if campaign_binding(cfg['completed_proofs']['path']) != cfg['completed_proofs']:
            raise ValueError('previous 41-group seed physical drift')
        seeds = read_artifact(Path(cfg['completed_proofs']['path']))['groups']
        group_rows = {g['group_key']:g for g in manifest['groups']}
        completed = set()
        for seed in seeds:
            projected, _ = historical_seed_projection(seed,scope)
            completed.update(validate_completed_parent_audit(projected,group_rows))
        if len(seeds) != 41 or len(completed) != 228:
            raise ValueError('previous completed 41/228 denominator drift')
        completed_repair_parent_snapshot(manifest,records,[*priors,package],completed,set())
        write_immutable(APPLICATION_ROOT/'source-epoch-preflight.json',{
            'status':'all_973_member_pins_and_current_postimages_verified',
            'source_repair_version':VERSION,'accepted_claims':973,'accepted_groups':156,
            'expected_post_fingerprint_sha256':fingerprint(post,proof['collections']),
            'new_group_completion_asserted':False,'new_models':0,'Registry_writes':0})
    verify_volume()
    if __import__('shutil').disk_usage(PROJECT).free < 2*1024**3:
        raise ValueError('less than 2GiB free disk; no lowering the safety threshold')
    write_derived(APPLICATION_ROOT/'progress.json',{'status':'backup_before_apply','master_data_mutations':0})
    if (APPLICATION_ROOT/'backup.json').exists():
        raise ValueError('backup phase already attempted; verify saved artifacts, no silent rerun')
    output = subprocess.run(['scripts/backup-registry.sh','before-357-s201129-'+sha256_json(str(APPLICATION_ROOT))[:20]],
                            cwd=PROJECT,check=True,capture_output=True,text=True)
    match = re.search(r'backup-registry: wrote (.+\.dump) \(',output.stdout)
    if not match:
        raise ValueError('fresh backup did not report actual dump')
    backup = Path(match.group(1))
    listing = subprocess.run(['pg_restore','--list',str(backup)],check=True,capture_output=True,text=True)
    if not listing.stdout.strip():
        raise ValueError('fresh backup restore catalog empty')
    write_immutable(APPLICATION_ROOT/'backup.json',binding(backup))
    write_immutable(APPLICATION_ROOT/'pg-restore-proof.json',{'backup':binding(backup),
        'catalog_sha256':__import__('hashlib').sha256(listing.stdout.encode()).hexdigest(),
        'catalog_nonempty':True,'exit_code':listing.returncode})
    with exclusive_cvp_run_lock(lock):
        if (fingerprint(snapshot(store),proof['collections']) != proof['pre_fingerprint_sha256']
                or binding(backup) != read_artifact(APPLICATION_ROOT/'backup.json')):
            raise ValueError('final full Registry or backup drift')
        verify_volume()
        validate_package(package)
        replay = store.plan_package(package,source_kind=VERSION)
        if json.loads(json.dumps(replay.as_dict())) != saved_plan:
            raise ValueError('final exact dry-run/apply plan drift')
        write_immutable(APPLICATION_ROOT/'apply-intent.json',{'schema_version':VERSION,
            'package_sha256':sha256_json(package),'plan_sha256':sha256_json(saved_plan),
            'pre_fingerprint_sha256':proof['pre_fingerprint_sha256'],'single_apply_required':True,
            'backup':binding(backup),'new_model_calls':0})
        receipt = store.apply_plan(replay,metadata={'source_repair_application':VERSION,
            'reviewed_source_reconciliation_root':str(APPLICATION_ROOT)},
            expected_registry_fingerprint_sha256=proof['pre_fingerprint_sha256'],registry_collections=proof['collections'])
        write_immutable(APPLICATION_ROOT/'apply-receipt.json',receipt)
        actual = snapshot(store)
        if set(actual) != set(post) or fingerprint(actual,proof['collections']) != fingerprint(post,proof['collections']):
            raise ValueError('complete Registry postimage readback mismatch')
        verify_sources(scope)
        write_immutable(APPLICATION_ROOT/'verified-result.json',{'status':'applied_readback_verified',
            'change_set_id':replay.change_set_id,'master_data_mutations':len(replay.operations),
            'pre_fingerprint_sha256':proof['pre_fingerprint_sha256'],
            'post_fingerprint_sha256':fingerprint(actual,proof['collections']),
            'cvp_text_writes':0,'Claim_members_added_or_removed':0,'new_model_calls':0,
            'bindings':[binding(APPLICATION_ROOT/n) for n in ('before.json','effective-package.json',
                'dry-run-plan.json','backup.json','pg-restore-proof.json','apply-intent.json','apply-receipt.json')]
                + [binding(REVIEW_ROOT/name) for name in PINS]})
    write_derived(APPLICATION_ROOT/'progress.json',{'status':'affected_full_audits_required',
        'master_data_mutations':len(plan.operations),'completed_groups':41,'completed_claims':228,
        'production_resumed':False})
    return receipt


def main():
    load_dotenv(PROJECT/'.env')
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--plan-reviewed',action='store_true')
    modes.add_argument('--apply-reviewed',action='store_true')
    args = parser.parse_args()
    APPLICATION_ROOT.mkdir(exist_ok=True)
    with (BASE/'immediate-recovery-20261002-v1/recovery.lock').open('r+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            if args.apply_reviewed:
                receipt = apply_reviewed()
                print(json.dumps({'status':'applied_readback_verified_audits_required','receipt':receipt}))
            else:
                plan, _ = plan_reviewed()
                print(json.dumps({'status':'dry_run_passed_not_applied','operations':len(plan.operations),'model_calls':0}))
        except Exception as exc:
            write_immutable(APPLICATION_ROOT/'application-failure.json',{'error':str(exc),
                'Registry_writes':None if (APPLICATION_ROOT/'apply-intent.json').exists() else 0,
                'apply_receipt_present':(APPLICATION_ROOT/'apply-receipt.json').exists(),
                'automatic_model_retry':False})
            raise


if __name__ == '__main__':
    main()
