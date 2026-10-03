"""Owner-authorized g010 member repair, never Claim/source or CVP prose edits.

Fresh source-bound GPT proposal and independent Opus review precede a bounded
ChangeSet. This is not recovery-budget code repair or a replay of group ten.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore, stored_operation_payload
from backend.api.canonical_repository.viewpoint_foundation import sha256_json, semantic_record_sha
from backend.api.canonical_repository.viewpoint_production_safety import exclusive_cvp_run_lock, file_sha256
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.pipeline.viewpoint_authorized_source_repair_357 import snapshot, fingerprint, Review, review_call
from backend.pipeline.viewpoint_evidence_source_context import load_context_source
from backend.pipeline.viewpoint_resolution_runtime import subscription_client, read_artifact, write_immutable, write_derived, stable_decided_at

PROJECT = Path(__file__).resolve().parents[2]
BASE = Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357')
SPECS = {
    'VCL-3c7053f2c9c443e38b79': ('CV-6394260bf24c3ac6851b', 'CVR-ebbe730dfa802608cd01', 'DK-627c8e321f3d7a08d44e-P04-CL003'),
    'VCL-84a15e86c11ea67ac44c': ('CV-ca96bfbba12b9477a40d', 'CVR-0f427014d2753ea9d3b8', 'DK-d7a3359c50c895c48b09-P08-CL001'),
}


class Repair(BaseModel):
    model_config = ConfigDict(extra='forbid')
    link_id: str
    link_type: str
    component_text: str
    reason: str


class Proposal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    repairs: list[Repair]


PROPOSE = '''修复两个经独立审计确认的 CVP 成员连接。只依据完整原文，不评判神学，不执行操作。
第一条连接只定位“从而替他们背负罪债”，丢失“耶稣接受约翰的洗、与世人连合”的使能限定；提出保留完整限定的逐字定位与真实等价类型。原CVP已经保留限定，不改它。
第二条把“而是与神建立好关系”当作“义最主要含义是好关系及促进行为”的等价，论域不同。必须保持窄命题，不能泛化它，不能为了跳过身份审计随便降级supports。只有原文确实建立具体应用或限定关系，才提出applies或qualifies；若需新CVP/其他语义修订，本轮不得硬改，用link_type=needs_scope_revision和reason说明。
不改其他成员/结构/关系，已有相邻成员与原文一并给出，必须核查修订不会破坏它们。两项repairs各含link_id/link_type/component_text/reason。component_text是自身Claim逐字唯一子串；equivalent_full必须为完整Claim。所有既有证据保留；完整条件必须在自身Claim与既有证据中成立。不得增加未讲观点。'''
REVIEW = '''独立审核两条修订连接的实际proposed_postimage，并阅读全部来源上下文和同组相邻成员。
按两个Claim各返回一个verdict。Claim、CVP核心/signature/scope、证据、原文、其他连接、关系与结构全部不变，只有明确展示的两条连接拟修订。
第一条须保留受洗/与世人连合/代负罪债的因果限定，定位与CVP真值条件等价，不能仅靠代词或跨链接拼接来补条件。
第二条窄的神人关系不是广义关系性义的等价。只有原文真的将它作为该CVP的限定或具体应用时，qualifies/applies才成立；若需独立身份，必须拒绝本轮而非为了续跑批准。核查未丢弃窄观点、未给CVP添加未经原文支持的普遍性。
完整的相邻成员和依赖后像均给出，任何需要额外修订之处须提出，不能默许。本次审批是系统双模型审核，不是人工内容批准。approved只有全部成立且problems=[]才true。'''


def prepare(states):
    targets, source_ids, related = [], set(), []
    for lid, (cv, rev, cid) in SPECS.items():
        link, claim = states['viewpoint_claim_links', lid], states['claims', cid]
        if link['revision'] != 1 or link['viewpoint_id'] != cv or link['claim_id'] != cid or link['validated_against_viewpoint_revision_id'] != rev or states['canonical_viewpoints', cv]['current_revision_id'] != rev:
            raise ValueError('target revision moved; no replay')
        links = [p for (c, _), p in states.items() if c == 'viewpoint_claim_links' and p.get('effective_state') == 'active' and (p.get('viewpoint_id') == cv or p.get('claim_id') == cid)]
        bindings = { (b['evidence_step_id'], b['source_fragment_id']) for p in links if p['claim_id'] == cid for b in p['evidence_bindings'] }
        evidence = []
        for eid, fid in sorted(bindings):
            step, fragment = states['evidence_steps', eid], states['source_fragments', fid]
            if eid not in claim['evidence_step_ids'] or fid not in step['source_fragment_ids']:
                raise ValueError('foreign source binding')
            evidence.append({'step': step, 'fragment': fragment})
        for p in links:
            for b in p['evidence_bindings']:
                source_ids.add(states['source_fragments', b['source_fragment_id']]['source_id'])
        targets.append({'link': link, 'claim': claim, 'viewpoint': states['viewpoint_revisions', rev], 'available_evidence': evidence,
                        'adjacent_links': links, 'adjacent_claims': [states['claims', k] for k in sorted({p['claim_id'] for p in links})]})
    ids = set(SPECS) | {cv for cv, _, _ in SPECS.values()}
    for (c, k), p in states.items():
        if c in {'viewpoint_relations', 'viewpoint_structures', 'viewpoint_structure_revisions', 'argument_routes', 'argument_route_revisions', 'argument_route_attestations'} and any(i in json.dumps(p) for i in ids):
            related.append({'collection': c, 'id': k, 'unchanged_postimage': p})
    sources = []
    for sid in sorted(source_ids):
        path, sha, projection = load_context_source(states['source_documents', sid])
        for item in targets:
            for row in item['available_evidence']:
                f = row['fragment']
                if f['source_id'] == sid and (not f['verbatim_excerpt'] or f['verbatim_excerpt'] not in projection.spoken_rows[int(f['paragraph_key'][1:])-1]['text']):
                    raise ValueError('physical source excerpt drift')
        sources.append({'source_id': sid, 'path': str(path), 'file_sha256': sha, 'body_sha256': projection.body_sha256, 'complete_original': [r['text'] for r in projection.spoken_rows]})
    return {'schema_version': 'wkp357_g010_link_revision_v1', 'targets': targets, 'sources': sources, 'unchanged_dependencies': related,
            'authorization': 'user 2026-10-03: 继续; repair the two independently disputed g010 members and necessary dependencies; no Claim/Evidence/source edits'}


def validate(proposal, packet):
    p = Proposal.model_validate(proposal).model_dump(mode='json')
    if len(p['repairs']) != 2 or {r['link_id'] for r in p['repairs']} != set(SPECS):
        raise ValueError('repair population mismatch')
    targets = {t['link']['viewpoint_claim_link_id']: t for t in packet['targets']}
    for r in p['repairs']:
        claim = targets[r['link_id']]['claim']
        allowed = {'equivalent_full', 'equivalent_component'} if r['link_id'] == 'VCL-3c7053f2c9c443e38b79' else {'applies', 'qualifies'}
        if r['link_type'] not in allowed or not r['reason'].strip():
            raise ValueError('repair needs broader semantic review; no forced role downgrade')
        if not r['component_text'] or claim['statement'].count(r['component_text']) != 1:
            raise ValueError('locator must be a unique exact substring')
        if r['link_id'] == 'VCL-3c7053f2c9c443e38b79' and r['component_text'] != claim['statement']:
            raise ValueError('baptism/union/cause scope requires full conditioned statement')
        if r['link_type'] == 'equivalent_full' and r['component_text'] != claim['statement']:
            raise ValueError('full equivalence must span entire Claim')
        if r['link_id'] == 'VCL-84a15e86c11ea67ac44c' and '與神建立好關係' not in r['component_text']:
            raise ValueError('narrow relationship scope must survive')
    return p


def package_for(states, packet, proposal, report, root):
    p = validate(proposal, packet)
    digest = sha256_json({'packet': packet, 'proposal': p, 'report': report})
    package = {'package_id': 'G010-LINK-REPAIR-'+digest[:20], 'viewpoint_claim_links': [], 'viewpoint_identity_candidates': [], 'viewpoint_identity_decisions': []}
    for r in p['repairs']:
        old = states['viewpoint_claim_links', r['link_id']]
        claim = states['claims', old['claim_id']]
        link = deepcopy(old)
        link.update(revision=old['revision']+1, link_type=r['link_type'])
        text = r['component_text']; start = claim['statement'].index(text)
        link['component_locator'] = None if r['link_type'] == 'equivalent_full' else {'claim_sha256': semantic_record_sha(claim), 'statement_component': text, 'canonical_spans': [{'start_char': start, 'end_char': start+len(text), 'exact_text': text}]}
        if old['viewpoint_id'] == 'CV-6394260bf24c3ac6851b':
            target = next(t for t in packet['targets'] if t['link']['viewpoint_claim_link_id'] == r['link_id'])
            link['evidence_bindings'] = sorted([{'evidence_step_id': e['step']['evidence_step_id'], 'source_fragment_id': e['fragment']['fragment_id']} for e in target['available_evidence']], key=lambda b:(b['evidence_step_id'], b['source_fragment_id']))
        decision = deepcopy(states['viewpoint_identity_decisions', old['decision_id']])
        candidate = deepcopy(states['viewpoint_identity_candidates', decision['identity_candidate_id']])
        seed = sha256_json({'repair': digest, 'link': r['link_id']})
        candidate.update(revision=1, candidate_claim_ids=[old['claim_id']], candidate_viewpoint_ids=[old['viewpoint_id']], proposed_action='match_existing', generation_fingerprint=seed)
        identity = {'claims': candidate['candidate_claim_ids'], 'viewpoints': candidate['candidate_viewpoint_ids'], 'relations': candidate.get('seed_relation_ids', []), 'action': candidate['proposed_action'], 'blockers': candidate.get('blocker_codes', []), 'coverage_snapshot_id': candidate.get('coverage_snapshot_id'), 'generation_fingerprint': seed}
        if candidate.get('scope_manifest_sha256'):
            identity['scope_manifest_sha256'] = candidate['scope_manifest_sha256']
        candidate['identity_candidate_id'] = 'VIC-'+sha256_json(identity)[:20]
        decision.update(revision=1, identity_decision_id='VID-'+seed[:20], identity_candidate_id=candidate['identity_candidate_id'], decision='match_existing', resolved_viewpoint_id=old['viewpoint_id'], claim_link_decisions=[{'claim_id': old['claim_id'], 'link_type': r['link_type']}], input_sha256=digest, review_artifact_sha256=sha256_json(report), semantic_call_artifact_sha256s=report.get('accepted_call_artifact_shas', []), policy_version='wkp357_g010_source_bound_member_revision_v1', reviewer_model_ids=['claude-opus-5-5', 'gpt-6.1-sol'], reviewer_kind='system', approval_basis='dual_model_consensus', review_status='system_approved', created_at=stable_decided_at(root), reason=r['reason'])
        link['decision_id'] = decision['identity_decision_id']
        package['viewpoint_claim_links'].append(link)
        package['viewpoint_identity_candidates'].append(candidate)
        package['viewpoint_identity_decisions'].append(decision)
    return package


def run(root, apply=False, campaign=None):
    store = PostgresKnowledgeStore()
    with exclusive_cvp_run_lock({'global_lock_path': str(BASE/'registry-cvp.lock')}):
        states = snapshot(store); packet = prepare(states)
        before = {'collections': sorted({c for c, _ in states}), 'pre_fingerprint_sha256': fingerprint(states, sorted({c for c, _ in states}))}
        write_immutable(root/'packet.json', packet); write_immutable(root/'before.json', before)
    proposer = StructuredJsonReviewerAdapter(client=subscription_client('codex', 'gpt-6.1-sol', 'high'), prompt=PROPOSE, response_model=Proposal, schema_name='g010_member_repair', compact_payload=True)
    reviewer = StructuredJsonReviewerAdapter(client=subscription_client('claude', 'claude-opus-5-5', 'high'), prompt=REVIEW, response_model=Review, schema_name='g010_member_review', compact_payload=True)
    write_derived(root/'progress.json', {'status': 'proposing', 'master_data_mutations': 0})
    raw, _, _ = review_call(proposer, packet, root, 'gpt-proposal'); effective = validate(raw, packet)
    for n in range(2):
        preview = package_for(states, packet, effective, {}, root)
        postimage = {'links': preview['viewpoint_claim_links'], 'unchanged_dependencies': packet['unchanged_dependencies']}
        write_immutable(root/f'proposed-postimage-{n}.json', postimage)
        write_derived(root/'progress.json', {'status': 'reviewing', 'round': n, 'master_data_mutations': 0})
        raw, _, _ = review_call(reviewer, packet | {'proposal': effective, 'proposed_postimage': postimage}, root, f'opus-review-{n}')
        review = Review.model_validate(raw).model_dump(mode='json')
        verdicts = review['verdicts']
        if len(verdicts) != 2 or {v['claim_id'] for v in verdicts} != {s[2] for s in SPECS.values()}:
            raise ValueError('review population mismatch')
        if all(v['approved'] and not v['problems'] for v in verdicts): break
        if n == 1: raise ValueError('content disagreement after one correction')
        raw, _, _ = review_call(proposer, packet | {'previous_proposal': effective, 'independent_review': review}, root, 'gpt-correction'); effective = validate(raw, packet)
    report = {'approved': True, 'human_content_approval': False, 'proposal': effective, 'final_review': review, 'packet_sha256': sha256_json(packet), 'accepted_call_artifact_shas': sorted(read_artifact(root/f'raw-{label}.json')['artifact_sha256'] for label in ('gpt-correction' if n else 'gpt-proposal', f'opus-review-{n}'))}
    write_immutable(root/'review-report.json', report)
    with exclusive_cvp_run_lock({'global_lock_path': str(BASE/'registry-cvp.lock')}):
        live = snapshot(store)
        if fingerprint(live, before['collections']) != before['pre_fingerprint_sha256'] or prepare(live) != packet: raise ValueError('Registry/source drift; no write')
        package = package_for(live, packet, effective, report, root)
        semantic_links = lambda rows: [{k:v for k,v in row.items() if k != 'decision_id'} for row in rows]
        if semantic_links(package['viewpoint_claim_links']) != semantic_links(postimage['links']): raise ValueError('unreviewed semantic postimage')
        plan = store.plan_package(package, source_kind='wkp357_g010_dual_reviewed_member_revision')
        if len(plan.operations) != 6 or any(op.collection not in package or (op.collection == 'viewpoint_claim_links') != (op.operation == 'update') for op in plan.operations): raise ValueError('unexpected writes')
        write_immutable(root/'effective-package.json', package); write_immutable(root/'dry-run-plan.json', json.loads(json.dumps(plan.as_dict())))
    result = {'status': 'reviewed_dry_run_pass', 'master_data_mutations': 0, 'planned_operations': len(plan.operations), 'claim_source_mutations': 0, 'apply_requires_fresh_backup_and_readback': True}
    write_derived(root/'progress.json', result)
    if apply:
        # Create and verify an independent backup AFTER review and before any
        # write. Reopen the live registry/source under the same global lock.
        import re
        label = 'before-g010-member-'+sha256_json(str(root))[:24]
        backup_output = subprocess.run(['scripts/backup-registry.sh', label], cwd=PROJECT, check=True, capture_output=True, text=True)
        match = re.search(r'backup-registry: wrote (.+\.dump) \(', backup_output.stdout)
        if not match: raise ValueError('backup not reported')
        backup = Path(match.group(1)).resolve()
        listing = subprocess.run(['pg_restore', '--list', str(backup)], check=True, capture_output=True, text=True)
        if not listing.stdout.strip(): raise ValueError('empty backup catalog')
        from backend.pipeline.viewpoint_cvp_recovery_executor import binding, resume_after_repair
        write_immutable(root/'backup.json', binding(backup))
        with exclusive_cvp_run_lock({'global_lock_path': str(BASE/'registry-cvp.lock')}):
            live = snapshot(store)
            if fingerprint(live, before['collections']) != before['pre_fingerprint_sha256'] or prepare(live) != packet: raise ValueError('pre-apply Registry/source drift')
            replay = store.plan_package(package, source_kind='wkp357_g010_dual_reviewed_member_revision')
            saved = read_artifact(root/'dry-run-plan.json'); saved.pop('artifact_sha256', None)
            if json.loads(json.dumps(replay.as_dict())) != saved: raise ValueError('dry-run/apply mismatch')
            saved_backup = read_artifact(root/'backup.json'); saved_backup.pop('artifact_sha256', None)
            if binding(backup) != saved_backup: raise ValueError('backup SHA drift')
            intent = {'authorization': packet['authorization'], 'backup': binding(backup), 'review_report_sha256': sha256_json(report)}
            write_immutable(root/'apply-intent.json', intent)
            receipt = store.apply_plan(replay, metadata={'reviewed_member_revision': intent}, expected_registry_fingerprint_sha256=before['pre_fingerprint_sha256'], registry_collections=before['collections'], expected_current_viewpoint_revisions={cv:rev for cv,rev,_ in SPECS.values()})
            write_immutable(root/'apply-receipt.json', receipt)
            expected = deepcopy(live)
            for op in replay.operations:
                expected[op.collection, op.object_id] = stored_operation_payload(op)
            post = snapshot(store)
            if set(post) != set(expected) or fingerprint(post, before['collections']) != fingerprint(expected, before['collections']): raise ValueError('exact complete Registry readback mismatch')
            if any(file_sha256(Path(s['path'])) != s['file_sha256'] for s in packet['sources']): raise ValueError('physical source changed')
            result = {'status':'applied_readback_verified', 'change_set_id':replay.change_set_id, 'master_data_mutations':len(replay.operations), 'claim_source_mutations':0, 'independent_audit':'pending'}
            write_immutable(root/'verified-result.json', result)
        if campaign:
            handoff = root/'content-repair-handoff'; handoff.mkdir()
            write_derived(root/'progress.json', result | {'status':'independent_reaudit_before_resume'})
            continuation = resume_after_repair(campaign, handoff, BASE/'immediate-recovery-20261002-v1', subprocess.check_output(['which','codex'], text=True).strip(), additional_proof_bindings=[binding(root/name) for name in ('effective-package.json','dry-run-plan.json','apply-receipt.json','review-report.json','verified-result.json')])
            result |= {'continuation':continuation, 'status':continuation['status']}
        write_derived(root/'progress.json', result)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__); ap.add_argument('--output-root', type=Path, required=True); ap.add_argument('--apply', action='store_true'); ap.add_argument('--resume-campaign-root', type=Path); args = ap.parse_args()
    if PROJECT.name != 'wkp-357-worktree-per-session' or args.output_root.resolve().parent != BASE: raise ValueError('only independent #357 repair root allowed')
    load_dotenv(PROJECT/'.env'); root = args.output_root.resolve(); root.mkdir(parents=True, exist_ok=True)
    import fcntl
    with (BASE/'immediate-recovery-20261002-v1/recovery.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        from backend.pipeline.viewpoint_cvp_recovery_supervisor import remaining_producers, process_table, current_job_process_ids
        if any(p not in current_job_process_ids() for p in remaining_producers(process_table(), BASE)): raise ValueError('another producer active')
        if args.resume_campaign_root and (not args.apply or args.resume_campaign_root.resolve().parent != BASE): raise ValueError('invalid continuation scope')
        if subprocess.check_output(['git', 'status', '--porcelain'], cwd=PROJECT, text=True).strip(): raise ValueError('committed clean code required')
        if (root/'packet.json').exists(): raise ValueError('immutable repair already started; no replay')
        try: print(json.dumps(run(root, args.apply, args.resume_campaign_root.resolve() if args.resume_campaign_root else None), ensure_ascii=False), flush=True)
        except Exception as exc:
            write_derived(root/'progress.json', {'status':'stopped', 'error':f'{type(exc).__name__}: {exc}', 'master_data_mutations':6 if (root/'apply-receipt.json').exists() else 0, 'no_next_group_started':True}); raise


if __name__ == '__main__': main()
