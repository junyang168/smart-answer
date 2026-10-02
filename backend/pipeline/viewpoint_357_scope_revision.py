"""One source-bound group-eight CVP successor; no Claim/source edits.

GPT proposes, Opus independently reviews; at most one corrected proposal and
one final review. Only a passing exact proposal may reach ChangeSet/readback.
This is content repair authorized by the owner's '继续吧', not code recovery.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_resolution import (
    StructuredJsonReviewerAdapter, ViewpointPropositionSignature, ViewpointScope,
)
from backend.api.canonical_repository.viewpoint_production_safety import exclusive_cvp_run_lock, file_sha256
from backend.pipeline.viewpoint_authorized_source_repair_357 import snapshot, fingerprint, Review, review_call
from backend.pipeline.viewpoint_evidence_source_context import load_context_source
from backend.pipeline.viewpoint_ruling_revision_runner import build_revision_package
from backend.pipeline.viewpoint_resolution_runtime import subscription_client, write_immutable, write_derived, read_artifact, stable_decided_at

PROJECT = Path(__file__).resolve().parents[2]
TARGET = 'CV-9db7716917f5ff8c41b9'
OLD = 'CVR-6fc2033fa639391d7998'
CLAIM = 'DK-f2169ae66018ae261759-P02-CL001'
LINK = 'VCL-8d5008139e1cef7930c8'


class Proposal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    core_proposition: str
    proposition_signature: ViewpointPropositionSignature
    scope: ViewpointScope
    reason: str


PROPOSE = '''修订一个因 scope_mismatch 被独立审计拒绝的 CVP。只读给定来源包，不执行操作，不评价教授神学对错。
Claim 和原文不得修改。现有 CVP 把路12:20与太2:20一起归入“不应译成灵魂”，但太2:20原文针对的是“性命”译法。
根据完整原文提出最小 CVP 后继修订：保留本 Claim 的“ψυχή应理解为魂，不应译成灵魂”判断，将该译法批评准确限定于它真正讨论的路12:20；不要把太2:20的不同译法批评并入该命题，也不要泛化为全圣经词义规则。
输出 core_proposition、proposition_signature、scope、reason。不能给该单独观点增补第二个不同观点。scope.scripture_scope 必须仅含“路加福音 12:20”。'''
REVIEW = '''独立内容审核一个 CVP 后继修订及其唯一成员连接。只判断是否忠于完整教授原文，不能为了续跑而批准。
返回恰好一个 verdict，claim_id 使用给定 claim_id。核实 core_proposition/signature/scope 一致，Claim 的定位成分与新 CVP 真值条件等价，所有既有证据 bindings仍可解析且支持该判定。
路12:20对“灵魂”译法的批评不得套到太2:20对“性命”的批评；不能凭两段有ψυχή就归并为同一个翻译命题。是否保留完整限定、有无新增未讲内容或忽略反例，都必须检查。
只审核实际给定 effective_proposal，不修改它。全部成立才 approved=true且problems=[]；否则说明问题。系统审核不是人工内容批准。'''


def records(states):
    names = ('viewpoint_revisions', 'canonical_viewpoints', 'viewpoint_claim_links', 'viewpoint_relations',
             'argument_routes', 'argument_route_revisions', 'argument_route_attestations',
             'viewpoint_structures', 'viewpoint_structure_revisions')
    return {c: [p for (col, _), p in states.items() if col == c] for c in names}


def prepare(states):
    head, old, link, claim = [states[(c, k)] for c, k in (
        ('canonical_viewpoints', TARGET), ('viewpoint_revisions', OLD),
        ('viewpoint_claim_links', LINK), ('claims', CLAIM))]
    if head['current_revision_id'] != OLD or link['validated_against_viewpoint_revision_id'] != OLD:
        raise ValueError('target/pinned revision moved; no replay')
    links = [p for (c, _), p in states.items() if c == 'viewpoint_claim_links' and p.get('viewpoint_id') == TARGET and p.get('effective_state') == 'active']
    if [p['viewpoint_claim_link_id'] for p in links] != [LINK] or link['claim_id'] != CLAIM:
        raise ValueError('member population drift')
    source_ids = {states[('source_fragments', b['source_fragment_id'])]['source_id'] for b in link['evidence_bindings']}
    if len(source_ids) != 1:
        raise ValueError('repair cannot splice multiple sources')
    source = states[('source_documents', next(iter(source_ids)))]
    if claim['occurrences'][0]['source_id'] not in {source['source_id'], source.get('transcript_id')}:
        raise ValueError('Claim occurrence does not identify the bound source')
    path, sha, projection = load_context_source(source)
    evidence = []
    for b in link['evidence_bindings']:
        step = states[('evidence_steps', b['evidence_step_id'])]
        fragment = states[('source_fragments', b['source_fragment_id'])]
        if b['evidence_step_id'] not in claim['evidence_step_ids'] or b['source_fragment_id'] not in step['source_fragment_ids'] or fragment['source_id'] != source['source_id']:
            raise ValueError('source evidence binding drift')
        text = projection.spoken_rows[int(fragment['paragraph_key'][1:]) - 1]['text']
        if not fragment['verbatim_excerpt'] or fragment['verbatim_excerpt'] not in text:
            raise ValueError('source quote no longer matches the complete original')
        evidence.append({'step': step, 'fragment': fragment})
    return {'schema_version': 'wkp357_g008_scope_revision_packet_v1', 'claim_id': CLAIM,
            'claim': claim, 'head': head, 'old_revision': old, 'member_link': link, 'evidence': evidence,
            'source_path': str(path), 'source_file_sha256': sha, 'source_body_sha256': projection.body_sha256,
            'complete_original': [row['text'] for row in projection.spoken_rows],
            'authorization': 'user: 继续吧; repair remaining group-eight scope mismatch; no Claim/source mutation'}


def validate_proposal(proposal):
    p = Proposal.model_validate(proposal).model_dump(mode='json')
    if p['scope']['scripture_scope'] != ['路加福音 12:20'] or not p['core_proposition'].strip():
        raise ValueError('repair cannot extend passage scope or empty the proposition')
    return p


def package_for(states, proposal, report, root):
    if not report.get('approved'):
        raise ValueError('content review did not approve the exact revision')
    p = validate_proposal(proposal)
    authority = {'artifact_sha256': sha256_json(report), 'target_viewpoint_revision_id': OLD,
                 'expected_core_proposition': states[('viewpoint_revisions', OLD)]['core_proposition'],
                 'new_core_proposition': p['core_proposition'], 'new_proposition_signature': p['proposition_signature'],
                 'new_scope': p['scope'], 'decided_by': 'gpt-6.1-sol+claude-opus-5-5:dual_model_consensus',
                 'decided_at': stable_decided_at(root), 'expected_dependents': {'viewpoint_claim_links': [LINK]}}
    package, retiring, manifest = build_revision_package(authority, store_records=records(states))
    if retiring or set(package) - {'schema_version', 'package_id', 'canonical_viewpoints', 'viewpoint_revisions', 'viewpoint_claim_links'}:
        raise ValueError('unexpected dependent/retirement; stop rather than silently repin')
    # Reuse successor/dependent compilation, NOT its owner-approval semantics.
    # The owner authorized the workflow, not the model's final content.
    for collection in ('canonical_viewpoints', 'viewpoint_revisions', 'viewpoint_claim_links'):
        for row in package[collection]:
            row['review_status'] = 'system_approved'
    package['package_id'] = 'DUAL-REVIEW-SCOPE-357-' + sha256_json(report)[:20]
    return package, manifest


def run(root, apply, backup):
    store = PostgresKnowledgeStore()
    with exclusive_cvp_run_lock({'global_lock_path': str(root.parent / 'registry-cvp.lock')}):
        states = snapshot(store)
        packet = prepare(states)
        before = {'collections': sorted({c for c, _ in states}), 'pre_fingerprint_sha256': fingerprint(states, sorted({c for c, _ in states}))}
        write_immutable(root / 'packet.json', packet)
        write_immutable(root / 'before.json', before)
    write_derived(root / 'progress.json', {'status': 'proposing', 'master_data_mutations': 0})
    adapter = StructuredJsonReviewerAdapter(client=subscription_client('codex', 'gpt-6.1-sol', 'high'),
                                            prompt=PROPOSE, response_model=Proposal, schema_name='cvp_scope_revision_v1')
    raw, _, _ = review_call(adapter, packet, root, 'gpt-proposal')
    effective = validate_proposal(raw)
    reviewer = StructuredJsonReviewerAdapter(client=subscription_client('claude', 'claude-opus-5-5', 'high'),
                                             prompt=REVIEW, response_model=Review, schema_name='cvp_scope_revision_review_v1')
    for round_number in range(2):
        write_derived(root / 'progress.json', {'status': 'reviewing', 'round': round_number, 'master_data_mutations': 0})
        request = packet | {'effective_proposal': effective}
        raw, _, _ = review_call(reviewer, request, root, f'opus-review-{round_number}')
        review = Review.model_validate(raw).model_dump(mode='json')
        if len(review['verdicts']) != 1 or review['verdicts'][0]['claim_id'] != CLAIM:
            raise ValueError('review exact population mismatch')
        verdict = review['verdicts'][0]
        if verdict['approved'] and not verdict['problems']:
            break
        if round_number == 1:
            raise ValueError('content disagreement after one correction; owner review required')
        write_derived(root / 'progress.json', {'status': 'one_correction', 'master_data_mutations': 0})
        corrected, _, _ = review_call(adapter, packet | {'previous_proposal': effective, 'independent_review': review}, root, 'gpt-correction')
        effective = validate_proposal(corrected)
    report = {'packet_sha256': sha256_json(packet), 'proposal_sha256': sha256_json(effective),
              'effective_proposal': effective, 'final_review': review, 'approved': True,
              'approval_basis': 'dual_model_consensus', 'human_content_approval': False,
              'raw_artifact_shas': [read_artifact(p)['artifact_sha256'] for p in sorted(root.glob('raw-*.json'))]}
    write_immutable(root / 'review-report.json', report)
    with exclusive_cvp_run_lock({'global_lock_path': str(root.parent / 'registry-cvp.lock')}):
        live = snapshot(store)
        if sorted({c for c, _ in live}) != before['collections'] or fingerprint(live, before['collections']) != before['pre_fingerprint_sha256'] or prepare(live) != packet:
            raise ValueError('Registry/source drift during review; no write')
        package, manifest = package_for(live, effective, report, root)
        plan = store.plan_package(package, source_kind='wkp357_dual_reviewed_scope_revision')
        if len(plan.operations) != 3 or {op.collection for op in plan.operations} != {'canonical_viewpoints', 'viewpoint_revisions', 'viewpoint_claim_links'}:
            raise ValueError('unexpected revision ChangeSet')
        write_immutable(root / 'effective-package.json', package)
        write_immutable(root / 'dry-run-plan.json', plan.as_dict())
        write_immutable(root / 'successor-manifest.json', manifest)
        if not apply:
            return {'status': 'dry_run_pass', 'master_data_mutations': 0}
        if not backup or not backup.is_absolute() or not backup.is_file():
            raise ValueError('verified independent backup required')
        subprocess.run(['pg_restore', '--list', str(backup)], check=True, capture_output=True)
        if subprocess.check_output(['git', 'status', '--porcelain'], cwd=PROJECT, text=True).strip():
            raise ValueError('apply requires clean committed code')
        intent = {'authorization': packet['authorization'], 'review_report_sha256': sha256_json(report),
                  'backup_path': str(backup), 'backup_sha256': file_sha256(backup)}
        write_immutable(root / 'apply-intent.json', intent)
        receipt = store.apply_plan(plan, metadata={'reviewed_scope_revision': intent},
                                   expected_registry_fingerprint_sha256=before['pre_fingerprint_sha256'],
                                   registry_collections=before['collections'],
                                   expected_current_viewpoint_revisions={TARGET: OLD})
        write_immutable(root / 'apply-receipt.json', receipt)
        expected = deepcopy(live)
        for op in plan.operations:
            if store.get_record_state(op.collection, op.object_id)['payload'] != op.payload:
                raise ValueError('readback differs from exact normalized plan')
            expected[(op.collection, op.object_id)] = op.payload
        post = snapshot(store)
        if set(post) != set(expected) or fingerprint(post, before['collections']) != fingerprint(expected, before['collections']):
            raise ValueError('unexpected non-target mutation')
        if file_sha256(Path(packet['source_path'])) != packet['source_file_sha256']:
            raise ValueError('source changed after apply')
        result = {'status': 'applied_readback_verified', 'change_set_id': plan.change_set_id, **manifest,
                  'claim_source_mutations': 0, 'independent_audit': 'pending'}
        write_immutable(root / 'verified-result.json', result)
        return result


def main():
    if PROJECT.name != 'wkp-357-worktree-per-session':
        raise ValueError('only #357 worktree allowed')
    load_dotenv(PROJECT / '.env')
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output-root', type=Path, required=True)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--backup', type=Path)
    ap.add_argument('--resume-campaign-root', type=Path)
    ap.add_argument('--recovery-root', type=Path)
    ap.add_argument('--codex-executable')
    args = ap.parse_args()
    root = args.output_root.resolve()
    if root.parent != Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357'):
        raise ValueError('content repair needs its own #357 staging root')
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'job.lock').open('a') as lock:
        import fcntl
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (root / 'apply-receipt.json').exists():
            raise ValueError('already applied; verify/audit without reapplying')
        try:
            result = run(root, args.apply, args.backup)
            if args.resume_campaign_root:
                if not args.apply or result['status'] != 'applied_readback_verified' or not args.recovery_root or not args.codex_executable:
                    raise ValueError('reviewed/readback content repair and recovery inputs required')
                from backend.pipeline.viewpoint_cvp_recovery_executor import resume_after_repair, binding
                from backend.pipeline.viewpoint_cvp_recovery_supervisor import remaining_producers, process_table
                if any(p != __import__('os').getpid() for p in remaining_producers(process_table(), root.parent)):
                    raise ValueError('another producer active; no audit/restart race')
                handoff = root / 'content-repair-handoff'
                handoff.mkdir()
                write_derived(root / 'progress.json', result | {'status': 'independent_reaudit_before_resume'})
                with (args.recovery_root / 'recovery.lock').open('a') as repair_lock:
                    fcntl.flock(repair_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    continuation = resume_after_repair(args.resume_campaign_root.resolve(), handoff,
                        args.recovery_root.resolve(), args.codex_executable,
                        additional_proof_bindings=[binding(root / n) for n in (
                            'effective-package.json', 'dry-run-plan.json', 'apply-receipt.json',
                            'review-report.json', 'verified-result.json')])
                result = result | {'continuation': continuation, 'status': continuation['status']}
            write_derived(root / 'progress.json', result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception as exc:
            write_derived(root / 'progress.json', {'status': 'stopped', 'error': f'{type(exc).__name__}: {exc}',
                                                 'no_next_group_started': True})
            raise


if __name__ == '__main__':
    main()
