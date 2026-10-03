"""One source-bound group-eight CVP successor; no Claim/source edits.

GPT proposes, Opus independently reviews; at most one corrected proposal and
one final review. Only a passing exact proposal may reach ChangeSet/readback.
This is content repair authorized by the owner's '继续吧', not code recovery.
"""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_foundation import sha256_json, semantic_record_sha
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
STRUCTURE = 'VS-b8babbb987cfff0bda1d'
STRUCTURE_REVISION = 'VSR-fde02bf2e16da0f3be28'


class LinkRepair(BaseModel):
    model_config = ConfigDict(extra='forbid')
    link_type: Literal['equivalent_component', 'equivalent_full']
    component_text: str
    included_binding_indices: list[int]
    excluded_binding_reason: str


class Proposal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    core_proposition: str
    proposition_signature: ViewpointPropositionSignature
    scope: ViewpointScope
    member_link_repair: LinkRepair
    reason: str


PROPOSE = '''修订一个因 scope_mismatch 被独立审计拒绝的 CVP。只读给定来源包，不执行操作，不评价教授神学对错。
Claim 和原文不得修改。现有 CVP 把路12:20与太2:20一起归入“不应译成灵魂”，但太2:20原文针对的是“性命”译法。
根据完整原文提出最小 CVP 后继修订：保留本 Claim 的“ψυχή应理解为魂，不应译成灵魂”判断，将该译法批评准确限定于它真正讨论的路12:20；不要把太2:20的不同译法批评并入该命题，也不要泛化为全圣经词义规则。
输出 core_proposition、proposition_signature、scope、member_link_repair、reason。scope.scripture_scope 必须仅含“路加福音 12:20”。
member_link_repair 同时修订实际成员连接：component_text只能是给定Claim statement的逐字唯一子串；included_binding_indices是member_link.evidence_bindings里保留的零起始序号，必须非空、唯一递增。excluded_binding_reason说明为何某些旧binding不属于这个CVP，不能删除Claim/EvidenceStep/原文。
只可在真值条件等价时用equivalent_component或equivalent_full；full必须覆盖整个Claim。不能为了过审改为supports而躲开身份审计。若等价不能建立，明确提出问题而非虚构定位。Claim scripture_refs是所引经文目录，不自动表示每个statement成分在每节经文上都断言同一内容；仍须结合自身原文上下文判断。'''
REVIEW = '''独立内容审核一个 CVP 后继修订及其唯一成员连接。只判断是否忠于完整教授原文，不能为了续跑而批准。
返回恰好一个 verdict，claim_id 使用给定 claim_id。审核 proposed_postimage（实际拟写入的语义后像），不是把old member_link当新连接。核实 core_proposition/signature/scope 一致，Claim 的定位成分与新 CVP 真值条件等价，拟保留的bindings可解析且支持该判定，排除旧binding有无遗漏本命题限定。
路12:20对“灵魂”译法的批评不得套到太2:20对“性命”的批评；不能凭两段有ψυχή就归并为同一个翻译命题。是否保留完整限定、有无新增未讲内容或忽略反例，都必须检查。
Claim scripture_refs本身是引用目录，不自动给每个成分施加全经文量词；要读完整原文辨明该成分的论域，不能忽视真正的真值条件差异。旧连接、旧decision与原文都保留作历史，不因后继binding选择而删除。
proposed_postimage中dependent_structures必须也审核：这个单观点结构的central_synthesis须等于新核心命题，focal只应改指本CVP后继；不得保留旧的跨经文过宽概述或新增论证。结构旧版本也保留。
只审核实际 effective_proposal 与 proposed_postimage，不修改它。全部成立才 approved=true且problems=[]；否则说明问题。审核metadata/新decision按实际有效proposal和review SHA在通过后确定性附加，不是未审核的新语义。系统审核不是人工内容批准。'''


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
    structure = states.get(('viewpoint_structure_revisions', STRUCTURE_REVISION))
    if structure:
        if states[('viewpoint_structures', STRUCTURE)]['current_revision_id'] != STRUCTURE_REVISION or structure['focal_viewpoints'][0]['viewpoint_revision_id'] != OLD or len(structure['focal_viewpoints']) != 1:
            raise ValueError('dependent structure moved or has unreviewed focal population')
    return {'schema_version': 'wkp357_g008_scope_revision_packet_v2', 'claim_id': CLAIM,
            'claim': claim, 'head': head, 'old_revision': old, 'member_link': link, 'evidence': evidence,
            'source_path': str(path), 'source_file_sha256': sha, 'source_body_sha256': projection.body_sha256,
            'complete_original': [row['text'] for row in projection.spoken_rows],
            'dependent_structure': structure,
            'authorization': 'user: 再改; repair complete CVP and member-link package; no Claim/source mutation'}


def validate_proposal(proposal, states=None):
    p = Proposal.model_validate(proposal).model_dump(mode='json')
    if p['scope']['scripture_scope'] != ['路加福音 12:20'] or not p['core_proposition'].strip():
        raise ValueError('repair cannot extend passage scope or empty the proposition')
    repair = p['member_link_repair']
    indices = repair['included_binding_indices']
    if not indices or indices != sorted(set(indices)) or any(i < 0 for i in indices):
        raise ValueError('evidence binding indices must be nonempty sorted unique')
    if states is not None:
        claim, link = states[('claims', CLAIM)], states[('viewpoint_claim_links', LINK)]
        text = repair['component_text']
        if not text or claim['statement'].count(text) != 1:
            raise ValueError('component must be a unique exact Claim substring')
        if repair['link_type'] == 'equivalent_full' and text != claim['statement']:
            raise ValueError('full equivalence must cover the full Claim')
        if indices[-1] >= len(link['evidence_bindings']):
            raise ValueError('foreign binding index')
        if len(indices) != len(link['evidence_bindings']) and not repair['excluded_binding_reason'].strip():
            raise ValueError('excluded bindings need an explicit disposition reason')
    return p


def package_for(states, proposal, report, root, *, preview=False):
    if not preview and not report.get('approved'):
        raise ValueError('content review did not approve the exact revision')
    p = validate_proposal(proposal, states)
    dependents = {'viewpoint_claim_links': [LINK]}
    if ('viewpoint_structure_revisions', STRUCTURE_REVISION) in states:
        dependents['viewpoint_structure_revisions'] = [STRUCTURE_REVISION]
    authority = {'artifact_sha256': sha256_json(report), 'target_viewpoint_revision_id': OLD,
                 'expected_core_proposition': states[('viewpoint_revisions', OLD)]['core_proposition'],
                 'new_core_proposition': p['core_proposition'], 'new_proposition_signature': p['proposition_signature'],
                 'new_scope': p['scope'], 'decided_by': 'gpt-6.1-sol+claude-opus-5-5:dual_model_consensus',
                 'decided_at': stable_decided_at(root), 'expected_dependents': dependents}
    package, retiring, manifest = build_revision_package(authority, store_records=records(states))
    if retiring or set(package) - {'schema_version', 'package_id', 'canonical_viewpoints', 'viewpoint_revisions', 'viewpoint_claim_links', 'viewpoint_structures', 'viewpoint_structure_revisions'}:
        raise ValueError('unexpected dependent/retirement; stop rather than silently repin')
    # Reuse successor/dependent compilation, NOT its owner-approval semantics.
    # The owner authorized the workflow, not the model's final content.
    for collection in ('canonical_viewpoints', 'viewpoint_revisions', 'viewpoint_claim_links', 'viewpoint_structures', 'viewpoint_structure_revisions'):
        for row in package.get(collection, []):
            row['review_status'] = 'system_approved'
    for row in package.get('viewpoint_structure_revisions', []):
        # This structure contains this single focal CVP only. Its old broad
        # synopsis must not survive while merely pointing at a narrower CVP.
        row['central_synthesis'] = p['core_proposition']
    new_link = package['viewpoint_claim_links'][0]
    repair = p['member_link_repair']
    claim, old_link = states[('claims', CLAIM)], states[('viewpoint_claim_links', LINK)]
    new_link['link_type'] = repair['link_type']
    text = repair['component_text']; start = claim['statement'].index(text)
    new_link['component_locator'] = None if repair['link_type'] == 'equivalent_full' else {
        'claim_sha256': semantic_record_sha(claim), 'statement_component': text,
        'canonical_spans': [{'start_char': start, 'end_char': start+len(text), 'exact_text': text}]}
    new_link['evidence_bindings'] = [deepcopy(old_link['evidence_bindings'][i]) for i in repair['included_binding_indices']]
    package['package_id'] = 'DUAL-REVIEW-SCOPE-357-' + sha256_json(report)[:20]
    if not preview:
        # Append authority, never change the immutable decision that approved
        # the old evidence selection. The successor pins the new decision.
        old_decision = states[('viewpoint_identity_decisions', old_link['decision_id'])]
        candidate = deepcopy(states[('viewpoint_identity_candidates', old_decision['identity_candidate_id'])])
        digest = sha256_json({'proposal': p, 'review': report})
        candidate.update(revision=1, proposed_action='match_existing', candidate_claim_ids=[CLAIM],
                         candidate_viewpoint_ids=[TARGET], generation_fingerprint=digest,
                         proposed_proposition_signature=p['proposition_signature'])
        identity = {'claims': [CLAIM], 'viewpoints': [TARGET], 'relations': candidate['seed_relation_ids'],
                    'action': 'match_existing', 'blockers': candidate['blocker_codes'],
                    'coverage_snapshot_id': candidate['coverage_snapshot_id'], 'generation_fingerprint': digest,
                    'scope_manifest_sha256': candidate['scope_manifest_sha256']}
        candidate['identity_candidate_id'] = 'VIC-' + sha256_json(identity)[:20]
        decision = deepcopy(old_decision)
        decision.update(revision=1, identity_decision_id='VID-'+digest[:20],
            identity_candidate_id=candidate['identity_candidate_id'], decision='match_existing',
            resolved_viewpoint_id=TARGET, claim_link_decisions=[{'claim_id': CLAIM, 'link_type': repair['link_type']}],
            input_sha256=digest, review_artifact_sha256=sha256_json(report),
            semantic_call_artifact_sha256s=accepted_review_pair(report),
            policy_version='wkp357_scope_revision_complete_postimage_v2',
            reviewer_model_ids=['claude-opus-5-5', 'gpt-6.1-sol'], reviewer_kind='system',
            approval_basis='dual_model_consensus', review_status='system_approved',
            created_at=stable_decided_at(root), reason='Exact successor CVP/member-link postimage passed independent review; '+p['reason'])
        new_link['decision_id'] = decision['identity_decision_id']
        package['viewpoint_revisions'][0]['provenance']['basis_identity_decision_ids'] = [decision['identity_decision_id']]
        for row in package.get('viewpoint_structure_revisions', []):
            row['review_provenance']['basis_identity_decision_ids'] = [decision['identity_decision_id']]
        package.update(viewpoint_identity_candidates=[candidate], viewpoint_identity_decisions=[decision])
    return package, manifest


def accepted_review_pair(report):
    # Decision provenance is exactly the agreeing final proposal/review pair,
    # not the rejected initial round. The report retains every raw response.
    pair = report.get('accepted_call_artifact_shas', [])
    if len(pair) != 2 or len(set(pair)) != 2 or not set(pair).issubset(report['raw_artifact_shas']):
        raise ValueError('identity authority requires the accepted proposal/review pair')
    return sorted(pair)


def semantic_postimage(package):
    r, link = package['viewpoint_revisions'][0], package['viewpoint_claim_links'][0]
    return {'viewpoint_revision': {k: r[k] for k in ('viewpoint_id', 'viewpoint_revision_id', 'supersedes_revision_id',
                'core_proposition', 'proposition_signature', 'scope')},
            'member_link': {k: link[k] for k in ('viewpoint_claim_link_id', 'viewpoint_id',
                'validated_against_viewpoint_revision_id', 'claim_id', 'pinned_claim_revision',
                'link_type', 'component_locator', 'evidence_bindings', 'occurrence_refs', 'supporting_relation_ids', 'effective_state')},
            'dependent_structures': [{k: row[k] for k in ('structure_id', 'structure_revision_id',
                'supersedes_revision_id', 'central_synthesis', 'focal_viewpoints', 'unresolved_items')}
                for row in package.get('viewpoint_structure_revisions', [])]}


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
    effective = validate_proposal(raw, states)
    reviewer = StructuredJsonReviewerAdapter(client=subscription_client('claude', 'claude-opus-5-5', 'high'),
                                             prompt=REVIEW, response_model=Review, schema_name='cvp_scope_revision_review_v1')
    for round_number in range(2):
        write_derived(root / 'progress.json', {'status': 'reviewing', 'round': round_number, 'master_data_mutations': 0})
        preview_package, _ = package_for(states, effective, {}, root, preview=True)
        postimage = semantic_postimage(preview_package)
        write_immutable(root / f'proposed-postimage-{round_number}.json', postimage)
        request = packet | {'effective_proposal': effective, 'proposed_postimage': postimage}
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
        effective = validate_proposal(corrected, states)
    report = {'packet_sha256': sha256_json(packet), 'proposal_sha256': sha256_json(effective),
              'effective_proposal': effective, 'final_review': review, 'approved': True,
              'approval_basis': 'dual_model_consensus', 'human_content_approval': False,
              'reviewed_postimage_sha256': sha256_json(postimage),
              'accepted_call_artifact_shas': sorted(read_artifact(root / f'raw-{label}.json')['artifact_sha256']
                  for label in ('gpt-correction' if round_number else 'gpt-proposal', f'opus-review-{round_number}')),
              'raw_artifact_shas': [read_artifact(p)['artifact_sha256'] for p in sorted(root.glob('raw-*.json'))]}
    write_immutable(root / 'review-report.json', report)
    with exclusive_cvp_run_lock({'global_lock_path': str(root.parent / 'registry-cvp.lock')}):
        live = snapshot(store)
        if sorted({c for c, _ in live}) != before['collections'] or fingerprint(live, before['collections']) != before['pre_fingerprint_sha256'] or prepare(live) != packet:
            raise ValueError('Registry/source drift during review; no write')
        package, manifest = package_for(live, effective, report, root)
        if sha256_json(semantic_postimage(package)) != report['reviewed_postimage_sha256']:
            raise ValueError('effective write postimage differs from independently reviewed postimage')
        plan = store.plan_package(package, source_kind='wkp357_dual_reviewed_scope_revision')
        expected_collections = {'canonical_viewpoints', 'viewpoint_revisions', 'viewpoint_claim_links', 'viewpoint_identity_candidates', 'viewpoint_identity_decisions'}
        if package.get('viewpoint_structure_revisions'):
            expected_collections |= {'viewpoint_structures', 'viewpoint_structure_revisions'}
        if len(plan.operations) != len(expected_collections) or {op.collection for op in plan.operations} != expected_collections:
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
                # This process and its detached screen parent are the current
                # repair job, not a competing producer. All others still block.
                if any(p not in {os.getpid(), os.getppid()} for p in remaining_producers(process_table(), root.parent)):
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
