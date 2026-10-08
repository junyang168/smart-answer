"""Owner-authorized Claim qualifier repair; all originals/evidence preserved."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore, ChangeOperation, stored_operation_payload, record_content_sha
from backend.api.canonical_repository.viewpoint_foundation import sha256_json, semantic_record_sha
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.api.canonical_repository.viewpoint_production_safety import exclusive_cvp_run_lock, file_sha256
from backend.pipeline.viewpoint_authorized_source_repair_357 import snapshot, fingerprint, Review, review_call
from backend.pipeline.viewpoint_evidence_source_context import load_context_source
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable, write_derived, subscription_client, stable_decided_at

PROJECT = Path(__file__).resolve().parents[2]
CLAIM = 'DK-f2169ae66018ae261759-P02-CL001'
LINK = 'VCL-8d5008139e1cef7930c8'
CVR = 'CVR-f2a08c052fe147d2c0a6'
PREFIX = '在路加福音 12:20 中，'
SOURCE_SHA = 'cb5f48816a4d86eeb42d3f8645db1c68394fb9dce112467536ab3bea54141257'
PROMPT = '''独立审核一个获用户授权的 Claim 限定修复，及实际成员连接后像。只读材料，禁止执行操作，不评价教授神学对错。
返回恰好一个 verdict，claim_id 为给定目标。必须读完整原文，检查在旧句前加“在路加福音12:20中”是否准确保存该局部合取命题。
不要把魂可指整个人的一般判断，或太2:20针对“性命”的批评，抹掉或误作全圣经“不应译成灵魂”规则。它们保存在现有 sibling Claims、EvidenceStep 和原文中。
所有 evidence_step_ids、scripture_refs、occurrences 原样保留，scripture_refs是引用目录不是全称量词；E003保留为相关旁证而非此局部合取的充分证据。实际CVP成员仍只绑定E001三个片段。
必须判断保留全部旧证据是否造成新Claim错误；如有真实问题应拒绝，不为续跑批准。新 component_locator覆盖加限定后的整句，不以选择bindings掩盖scope差异；equivalent_component允许该显式定位，不宣称其他证据全部等价。
CVP、结构及EvidenceStep文字不变。旧身份decision只保留历史判定，当前成员重新校验由本次精确双模型审核及ChangeSet记录提供权威，不把旧审核当新Claim的审核。
candidate旧跨节关系含泛化理由，记录为未审核失效线索，不能拿它作支持事实；不擅自修改、批准该关系。
只在拟写入Claim和连接均忠于原文、没有遗漏或扩大限定时 approved=true，problems=[]；否则具体说明。'''


def prepare(states, root):
    old = states['claims', CLAIM]
    before_link = states['viewpoint_claim_links', LINK]
    if old['revision'] != 1 or old['statement'] != 'ψυχή應當理解為「魂」，不應譯成「靈魂」。':
        raise ValueError('Claim moved; never replay repair')
    links = [p for (c, _), p in states.items() if c == 'viewpoint_claim_links' and p.get('claim_id') == CLAIM]
    if links != [before_link] or before_link['revision'] != 2 or before_link['validated_against_viewpoint_revision_id'] != CVR:
        raise ValueError('dependent link population or revision drift')
    forbidden = {'claim_concept_units', 'viewpoint_resolution_ledgers', 'argument_route_revisions', 'argument_route_attestations', 'product_dependencies'}
    blocked = [(c,o) for (c,o),p in states.items() if c in forbidden and CLAIM in json.dumps(p)]
    if blocked:
        raise ValueError(f'unplanned pinned dependents: {blocked}')
    source = states['source_documents', 'SRC-2019_2_15_mp4-3399771e82cb']
    path, sha, projection = load_context_source(source)
    if sha != SOURCE_SHA:
        raise ValueError('physical original SHA drift')
    evidence = []
    for eid in old['evidence_step_ids']:
        step = states['evidence_steps', eid]
        if CLAIM not in step['produced_claim_ids']:
            raise ValueError('nonreciprocal evidence')
        for fid in step['source_fragment_ids']:
            fragment = states['source_fragments', fid]
            text = projection.spoken_rows[int(fragment['paragraph_key'][1:])-1]['text']
            if fragment['source_id'] != source['source_id'] or not fragment['verbatim_excerpt'] or fragment['verbatim_excerpt'] not in text:
                raise ValueError('source-bound evidence mismatch')
            evidence.append({'step': step, 'fragment': fragment, 'paragraph_text': text})
    new = deepcopy(old)
    new.update(revision=2, statement=PREFIX+old['statement'], reviewed_at=stable_decided_at(root),
               reviewed_by='gpt-6.1-sol+claude-opus-5-5',
               review_note=f'Owner-authorized local qualifier repair; exact dual subscription reviews: {root}/review-report.json. Prior version and all evidence retained.')
    link = deepcopy(before_link)
    link.update(revision=3, pinned_claim_revision=2, component_locator={
        'claim_sha256': semantic_record_sha(new), 'statement_component': new['statement'],
        'canonical_spans': [{'start_char':0, 'end_char':len(new['statement']), 'exact_text':new['statement']}]})
    package = {'package_id':'CLAIM-QUALIFIER-357-'+sha256_json(new)[:20], 'claims':[new], 'viewpoint_claim_links':[link]}
    candidates = [p for (c,_),p in states.items() if c=='claim_relations' and CLAIM in {p.get('from_id'),p.get('to_id')}]
    if any(p['review_status'] != 'candidate' for p in candidates):
        raise ValueError('approved Claim relationship requires separate content disposition')
    packet = {'schema_version':'wkp357_claim_qualifier_packet_v2',
        'authorization':'user: 好，同意; one Claim Luke12:20 qualifier and dependency revalidation; no evidence/source content change',
        'claim_id':CLAIM, 'before_claim':old, 'before_link':before_link, 'qualifier_prefix':PREFIX,
        'after_claim_semantic_sha256':semantic_record_sha(new), 'proposed_package':deepcopy(package),
        'package_sha256':sha256_json(package), 'source_path':str(path), 'source_file_sha256':sha,
        'source_body_sha256':projection.body_sha256, 'complete_original':[r['text'] for r in projection.spoken_rows],
        'bound_evidence':evidence, 'current_viewpoint_revision':states['viewpoint_revisions',CVR],
        'historical_identity_decision':states['viewpoint_identity_decisions',before_link['decision_id']],
        'preserved_sibling_claims':[p for (c,o),p in states.items() if c=='claims' and o in {
            'DK-f2169ae66018ae261759-P02-CL002','DK-f2169ae66018ae261759-P02-CL006','DK-f2169ae66018ae261759-P03-CL009'}],
        'candidate_relation_dispositions':[{'record':p,'status':'unreviewed_stale_scope_lead_not_support_authority',
            'reason':'Local Luke qualifier does not authorize general translation-rule application.'} for p in candidates]}
    return package, packet


def verify(store, root):
    plan, before = read_artifact(root/'dry-run-plan.json'), read_artifact(root/'before.json')
    receipt = read_artifact(root/'apply-receipt.json')
    if receipt['status'] != 'applied' or receipt['change_set_id'] != plan['change_set_id']:
        raise ValueError('no applied ChangeSet authority')
    live = snapshot(store)
    for op in plan['operations']:
        key = op['collection'], op['object_id']
        if live[key] != stored_operation_payload(ChangeOperation(**op)):
            raise ValueError(f'exact stored readback mismatch: {key}')
        with store.connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT payload, change_set_id FROM wang_knowledge.object_versions WHERE collection=%s AND object_id=%s AND revision=%s', (*key,op['after_revision']))
            row = cur.fetchone()
            if not row or row[0] != live[key] or row[1] != plan['change_set_id']:
                raise ValueError('ObjectVersion lineage mismatch')
            cur.execute('SELECT payload FROM wang_knowledge.object_versions WHERE collection=%s AND object_id=%s AND revision=%s AND content_sha256=%s',
                        (*key,op['before_revision'],op['before_sha256']))
            prior=cur.fetchone()
            if not prior or record_content_sha(prior[0]) != op['before_sha256']:
                raise ValueError('missing exact retained historical version')
    for record in before['before_records']:
        live[record['collection'],record['object_id']] = record['payload']
    if fingerprint(live,before['collections']) != before['pre_fingerprint_sha256']:
        raise ValueError('unexpected non-target write')
    packet = read_artifact(root/'packet.json')
    if file_sha256(Path(packet['source_path'])) != SOURCE_SHA:
        raise ValueError('original changed')
    result = {'status':'applied_readback_verified', 'change_set_id':plan['change_set_id'],
        'claim_revision':2, 'link_revision':3, 'all_evidence_and_originals_preserved':True,
        'non_target_records_unchanged':True, 'independent_audit':'pending'}
    write_immutable(root/'verified-result.json',result)
    return result


def run_main():
    from backend.pipeline.cvp_retired_runtime import reject_legacy_execution
    reject_legacy_execution()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root',required=True,type=Path)
    parser.add_argument('--review',action='store_true')
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--verify-applied',action='store_true')
    parser.add_argument('--backup',type=Path)
    parser.add_argument('--resume-campaign-root',type=Path)
    parser.add_argument('--codex-executable',type=Path)
    parser.add_argument('--resume-saved-audit',action='store_true')
    args=parser.parse_args(); root=args.output_root.resolve()
    if PROJECT.name != 'wkp-357-worktree-per-session' or root.parent != Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357'):
        raise ValueError('only #357 worktree and independent staging root allowed')
    root.mkdir(parents=True,exist_ok=True)
    load_dotenv(PROJECT/'.env'); store=PostgresKnowledgeStore()
    if args.verify_applied:
        with exclusive_cvp_run_lock({'global_lock_path':str(root.parent/'registry-cvp.lock')}):
            result=verify(store,root)
        if args.resume_campaign_root:
            if not args.codex_executable or not args.codex_executable.is_file():
                raise ValueError('explicit subscription executable required')
            from backend.pipeline.viewpoint_cvp_recovery_executor import resume_after_repair, binding
            handoff=root/('claim-repair-proof-resume' if args.resume_saved_audit else 'claim-repair-handoff'); handoff.mkdir()
            write_derived(root/'progress.json',result|{'status':'independent_reaudit_before_resume'})
            old_scope=root.parent/'g008-scope-revision-20261002-v6'
            proofs=[binding(root/n) for n in ('reviewed-package.json','dry-run-plan.json','apply-receipt.json','review-report.json','verified-result.json')]
            proofs.extend(binding(old_scope/n) for n in ('effective-package.json','dry-run-plan.json','apply-receipt.json','review-report.json','verified-result.json'))
            try:
                saved=None
                if args.resume_saved_audit:
                    proof=read_artifact(root/'claim-repair-handoff/verified-group/completion-with-content-repair.json')
                    from backend.pipeline.viewpoint_cvp_recovery_executor import verify_binding
                    for b in proof['bindings']: verify_binding(b,parse_json=False)
                    saved=Path(proof['audit_path'])
                continuation=resume_after_repair(args.resume_campaign_root.resolve(),handoff,
                    root.parent/'immediate-recovery-20261002-v1',args.codex_executable,
                    additional_proof_bindings=proofs,additional_source_repair_paths=[root/'reviewed-package.json'],saved_audit_path=saved)
                result=result|{'status':continuation['status'],'continuation':continuation}
                write_derived(root/'progress.json',result)
            except Exception as exc:
                write_derived(root/'progress.json',result|{'status':'stopped','error':str(exc),'no_next_group_started':True})
                raise
        print(json.dumps(result)); return
    if (root/'apply-receipt.json').exists():
        raise ValueError('already applied; use read-only verify-applied')
    if args.review:
        packet=read_artifact(root/'packet.json'); results={}
        for label,provider,model in [('gpt','codex','gpt-6.1-sol'),('opus','claude','claude-opus-5-5')]:
            adapter=StructuredJsonReviewerAdapter(client=subscription_client(provider,model,'high'),
                prompt=PROMPT,response_model=Review,schema_name='claim_qualifier_review_v2')
            raw,_,_=review_call(adapter,packet,root,label)
            verdict=Review.model_validate(raw).model_dump(mode='json')
            if len(verdict['verdicts'])!=1 or verdict['verdicts'][0]['claim_id']!=CLAIM:
                raise ValueError('review population mismatch')
            results[label]=verdict
        approved=all(v['approved'] and not v['problems'] for r in results.values() for v in r['verdicts'])
        write_immutable(root/'review-report.json',{'packet_sha256':sha256_json(packet),'reviews':results,'approved':approved})
        print(json.dumps({'approved':approved})); return
    with exclusive_cvp_run_lock({'global_lock_path':str(root.parent/'registry-cvp.lock')}):
        states=snapshot(store); package,packet=prepare(states,root)
        cols=sorted({c for c,_ in states}); before={'collections':cols,'pre_fingerprint_sha256':fingerprint(states,cols),
            'before_records':[{'collection':c,'object_id':o,'payload':states[c,o]} for c,o in [('claims',CLAIM),('viewpoint_claim_links',LINK)]]}
        write_immutable(root/'packet.json',packet); write_immutable(root/'before.json',before)
        if not (root/'review-report.json').exists():
            if args.apply: raise ValueError('fresh reviews required')
            print('prepared; zero writes'); return
        review=read_artifact(root/'review-report.json')
        if not review['approved'] or review['packet_sha256']!=sha256_json(packet):
            raise ValueError('exact dual reviews required')
        package['source_repair_application']={'schema_version':'wang_claim_qualifier_repair_v2',
            'user_authorization':packet['authorization'],'approval_status':'not_human_approved','review_packet':packet,
            'review_artifacts':[read_artifact(root/f'raw-{label}.json') for label in ('gpt','opus')]}
        write_immutable(root/'reviewed-package.json',package)
        plan=store.plan_package(package,source_kind='wkp357_claim_qualifier_repair')
        if {(o.collection,o.object_id) for o in plan.operations}!={('claims',CLAIM),('viewpoint_claim_links',LINK)} or len(plan.operations)!=2:
            raise ValueError('unexpected write population')
        normalized=json.loads(json.dumps(plan.as_dict()))
        write_immutable(root/'dry-run-plan.json',normalized)
        if not args.apply: print('dry_run_pass: one Claim, one link'); return
        if not args.backup or not args.backup.is_absolute() or not args.backup.is_file():
            raise ValueError('verified backup required')
        subprocess.run(['pg_restore','--list',str(args.backup)],check=True,capture_output=True)
        if subprocess.check_output(['git','status','--porcelain'],cwd=PROJECT,text=True).strip():
            raise ValueError('commit code before apply')
        intent={'authorization':packet['authorization'],'backup_path':str(args.backup),'backup_sha256':file_sha256(args.backup),
                'review_report_sha256':sha256_json(review),'runner_sha256':file_sha256(Path(__file__))}
        write_immutable(root/'apply-intent.json',intent)
        receipt=store.apply_plan(plan,metadata={'claim_qualifier_repair':intent},
            expected_registry_fingerprint_sha256=before['pre_fingerprint_sha256'],registry_collections=cols)
        write_immutable(root/'apply-receipt.json',receipt)
        print(json.dumps(verify(store,root)))


def main():
    import fcntl
    # Shared with immediate recovery; nonblocking and held through review/write.
    recovery=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/immediate-recovery-20261002-v1')
    with (recovery/'recovery.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        from backend.pipeline.viewpoint_cvp_recovery_supervisor import remaining_producers, process_table, current_job_process_ids
        if [p for p in remaining_producers(process_table(),recovery.parent) if p not in current_job_process_ids()]:
            raise ValueError('producer or maintenance active; no concurrent repair')
        run_main()


if __name__=='__main__': main()
