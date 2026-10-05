"""Separately authorized first-marriage qualifier and passive Evidence repair.

The predecessor refusal is evidence, not approval. This entry point obtains
one complete fresh review per subscription provider; applying requires a
separate exact package/store/source-version gate, never a review-only receipt.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import fcntl
import json
from pathlib import Path
import subprocess

from dotenv import load_dotenv

from backend.pipeline import viewpoint_g041_transcript_repair as old
from backend.pipeline.viewpoint_semantic_contract import semantic_prompt, CONTRACT_PATH
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter

VERSION = 'wang_g041_owner_qualified_transcript_reconciliation_v2'
PRIOR = old.BASE / 'g041-owner-transcript-reconciliation-runtime-20261005-v2'
ROOT = old.BASE / 'g041-owner-qualified-transcript-reconciliation-20261005-v1'
PREFIX = 'DK-c082110ed14e364955d7-P01-'
CLAIM = PREFIX + 'CL005'
EVIDENCE = PREFIX + 'E007'
OLD_STATEMENT = '按照所討論的姦淫觀念，娶被休婦人的男子自己沒有犯姦淫，而女子是不得不陷入姦淫。'
STATEMENT = '按照所討論的姦淫觀念，在聽眾提出男子首次結婚而娶被休婦人的情形下，男子自己沒有犯姦淫，而女子是不得不陷入姦淫。'
EVIDENCE_BEFORE = '引述經文：人若娶被休的婦人，也是犯姦淫。'
EVIDENCE_AFTER = '引述經文：人若娶被休的婦人，也是被犯姦淫。'
LINKS = {'VCL-7657863b7e2b4eaa4554': 'supports',
         'VCL-de742b1856d66636d1ac': 'equivalent_component'}
RAW_SHA = '72edaee5333864a2ff379072b0628b72a07e3a94b6b3419f72c630702cd71be6'
ROLE = '''独立审核一份负责人重新听录音后亲自更正的逐字稿及精确后像。负责人授权修改不是内容批准；旧拒绝、旧审核和旧版本完整保留。
只判断是否忠于教授自身原文，不评价教授神学对错，不借其他讲道补证。完整新旧七段、七条Claim及所有本段Evidence、相关连接和完整目标CVP均在包中。
沿用先前授权的五处新引文、两处Evidence归属/代词修复、CL002/CL004引文定位修复；本次新增仅CL005 statement保留问答中男子首次结婚的人群条件、E007 statement保留被犯姦淫的被动措辞，以及CL005两条既有连接的新Claim版本绑定和精确成分定位。CL004的qualifies连接也重新绑定。
连接类型、端点、证据、CVP正文及其他Claim正文均不可改；supports女子成分原文不扩大。旧链接decision只作历史背景，不作为本次新版批准；真正写入必须生成新双审provenance。
不得因为E014确为教授回答就忽略首次结婚/所讨论定义限定；E007忠实记录教授引述的被动措辞，不代表平台裁判经文翻译或神学。完整Claim保持男子与女子两个分句，必要充分/归属/责任不能混淆。
changed_paragraph_claims的七条Claim每条恰好一个verdict。精确新后像全部忠实且相关连接成立才approved=true、problems=[]；否则明确拒绝且停止整包，不部分批准写入、不循环到pass。human hold不得解除，973成员/156组不增删不重排。'''


def adapter(provider, model):
    result = StructuredJsonReviewerAdapter(client=old.subscription_client(provider, model, 'high'),
        prompt=semantic_prompt(ROLE), response_model=old.Review, schema_name=VERSION,
        compact_payload=True, shared_text_payload=True, shared_record_payload=True)
    result.max_request_bytes = 500000
    return result


def predecessor(states):
    packet = old.read_artifact(PRIOR/'packet.json')
    raw = old.read_artifact(PRIOR/'raw-gpt.json')
    before = old.read_artifact(PRIOR/'before.json')
    proof = old.read_artifact(PRIOR/'raw-gpt.request-encoding.json')
    legacy = old.adapter('codex', 'gpt-6.1-sol')
    encoding = legacy.request_encoding_proof(packet)
    expected_proof = {'schema_version':'wang_model_request_encoding_proof_v1', **encoding}
    digest = old.sha256_json({k:v for k,v in raw.items() if k != 'artifact_sha256'})
    parsed = old.Review.model_validate(raw['response'])
    denied = [v for v in parsed.verdicts if not v.approved or v.problems]
    if (raw['artifact_sha256'] != RAW_SHA or digest != RAW_SHA
            or raw['model_id'] != legacy.model_id or raw['backend'] != legacy.backend
            or raw['prompt_sha256'] != legacy.prompt_sha256
            or raw['generation_config_sha256'] != legacy.generation_config_sha256
            or raw['request_payload_sha256'] != old.sha256_json(packet)
            or raw['response_sha256'] != old.sha256_json(raw['response'])
            or raw['request_encoding_proof_sha256'] != old.sha256_json(encoding)
            or proof != {**expected_proof, 'artifact_sha256':old.sha256_json(expected_proof)}
            or len(parsed.verdicts) != 7 or {v.claim_id for v in parsed.verdicts} != {c['claim_id'] for c in packet['changed_paragraph_claims']}
            or len(denied) != 1 or denied[0].claim_id != CLAIM or len(denied[0].problems) != 2
            or old.fingerprint(states, before['collections']) != before['pre_fingerprint_sha256']
            or any((PRIOR/n).exists() for n in ('raw-opus.json','review-report.json','apply-intent.json','apply-receipt.json'))):
        raise ValueError('exact refused, unapplied predecessor or live Registry drift')
    return [{'path':str(PRIOR/n), 'file_sha256':old.file_sha256(PRIOR/n)}
        for n in ('packet.json','draft-package.json','before.json','raw-gpt.json',
                  'raw-gpt.request-encoding.json','failure.json','owner-authorization.json')]


def extend(states, package, packet, root):
    """Only the two new semantic fields and two dependent link pins/locators."""
    package, packet = deepcopy(package), deepcopy(packet)
    before = states['claims', CLAIM]
    evidence = states['evidence_steps', EVIDENCE]
    if (before['statement'] != OLD_STATEMENT or before['revision'] != 1
            or before['review_status'] != 'ai_consensus_reviewed'
            or evidence['statement'] != EVIDENCE_BEFORE or evidence['revision'] != 1
            or any(p.get('claim_id') == CLAIM for p in package['claims'])
            or any(p.get('evidence_step_id') == EVIDENCE for p in package['evidence_steps'])):
        raise ValueError('new authorized exact predecessor/hold drift')
    forbidden = {'argument_routes','argument_route_revisions','argument_route_attestations',
        'claim_concept_units','viewpoint_resolution_ledgers','product_dependencies'}
    if any(CLAIM in json.dumps(p) for (c,_),p in states.items() if c in forbidden):
        raise ValueError('unplanned qualified Claim downstream consumer')
    after = deepcopy(before)
    after.update(statement=STATEMENT, revision=2, reviewed_at=old.stable_decided_at(root),
        reviewed_by='gpt-6.1-sol+claude-opus-5-5',
        review_note=f'Owner first-marriage qualifier; fresh complete dual review {root}/review-report.json; historical rejection retained.')
    package['claims'].append(after)
    step = deepcopy(evidence); step.update(statement=EVIDENCE_AFTER, revision=2)
    package['evidence_steps'].append(step)
    dependent = {o:p for (c,o),p in states.items() if c == 'viewpoint_claim_links'
        and p.get('claim_id') == CLAIM and p.get('effective_state') == 'active'}
    if set(dependent) != set(LINKS):
        raise ValueError('qualified Claim exact dependent link denominator drift')
    for lid, kind in LINKS.items():
        previous = dependent[lid]
        if previous['link_type'] != kind or previous['revision'] != 1 or previous['pinned_claim_revision'] != 1:
            raise ValueError('qualified link type/version drift')
        quote = ('女子是不得不陷入姦淫。' if kind == 'supports' else STATEMENT.split('，而女子')[0])
        old_quote = ('女子是不得不陷入姦淫。' if kind == 'supports' else OLD_STATEMENT.split('，而女子')[0])
        locator = previous['component_locator']
        if (locator['statement_component'] != old_quote or locator['claim_sha256'] != old.semantic_record_sha(before)
                or locator['canonical_spans'] != [{'start_char':OLD_STATEMENT.index(old_quote),
                    'end_char':OLD_STATEMENT.index(old_quote)+len(old_quote),'exact_text':old_quote}]):
            raise ValueError('qualified link old component drift')
        link = deepcopy(previous); link.update(revision=2, pinned_claim_revision=2)
        start = STATEMENT.index(quote)
        link['component_locator'] = {'claim_sha256':old.semantic_record_sha(after),
            'statement_component':quote, 'canonical_spans':[{'start_char':start,'end_char':start+len(quote),'exact_text':quote}]}
        package['viewpoint_claim_links'].append(link)
    package['package_id'] = 'G041-QUALIFIED-TRANSCRIPT-'+old.sha256_json({'root':str(root),'version':VERSION})[:20]
    packet.update(schema_version=VERSION,
        owner_authorization='Owner said continue to precisely proposed CL005 first-marriage qualifier and E007 passive wording; not model/human content approval',
        semantic_after={k:v for k,v in package.items() if k not in {'source_fragments','package_id'}},
        prior_refusal_preserved=True, contextual_semantics_binding={'path':str(CONTRACT_PATH),
            'file_sha256':old.file_sha256(CONTRACT_PATH)}, draft_package_sha256=old.sha256_json(package))
    packet['mechanical_proof'].update(claim_statement_changes=1, evidence_statement_changes=3,
        changed_claims=3, revised_links=3, pending_fresh_identity_records=6)
    return package, packet


def saved_before(root=ROOT):
    proof = old.read_artifact(root/'before.json')
    states = {(r['collection'],r['object_id']):r['payload'] for r in proof['before_records']}
    if len(states) != len(proof['before_records']) or old.fingerprint(states,proof['collections']) != proof['pre_fingerprint_sha256']:
        raise ValueError('immutable complete Registry before proof drift')
    return states, proof


def reviewed_inputs(root=ROOT):
    states, proof = saved_before(root)
    bindings = predecessor(states)
    report, proposal = old.frozen_inputs()
    draft, packet = old.materialize(states, report, proposal, root)
    draft, packet = extend(states, draft, packet, root)
    packet['prior_refusal_bindings'] = bindings
    if old.read_artifact(root/'packet.json') != packet or old.read_artifact(root/'draft-package.json') != draft:
        raise ValueError('reviewed exact source/Claim/link packet reconstruction drift')
    reservation = old.read_artifact(PRIOR/'owner-qualified-transcript-start.json')
    if (reservation != old.read_artifact(root/'owner-authorization.json')
            or reservation.get('schema_version') != VERSION or reservation.get('output_root') != str(root)
            or reservation.get('packet_sha256') != old.sha256_json(packet)
            or reservation.get('fresh_reviews_per_provider') != 1 or reservation.get('human_content_approval') is not False
            or reservation.get('prior_refusal_bindings') != bindings):
        raise ValueError('single-use owner qualifier authorization drift')
    reviews, raws = {}, []
    for label,provider,model in [('gpt','codex','gpt-6.1-sol'),('opus','claude','claude-opus-5-5')]:
        a = adapter(provider,model); raw = old.read_artifact(root/f'raw-{label}.json')
        encoding = a.request_encoding_proof(packet)
        sealed = {'schema_version':'wang_model_request_encoding_proof_v1',**encoding}
        expected = {'model_id':model,'backend':a.backend,'prompt_sha256':a.prompt_sha256,
            'generation_config_sha256':a.generation_config_sha256,'request_payload_sha256':old.sha256_json(packet),
            'request_encoding_proof_sha256':old.sha256_json(encoding)}
        if (any(raw.get(k) != v for k,v in expected.items())
                or raw['artifact_sha256'] != old.sha256_json({k:v for k,v in raw.items() if k != 'artifact_sha256'})
                or raw['response_sha256'] != old.sha256_json(raw['response'])
                or old.read_artifact(root/f'raw-{label}.request-encoding.json') != {**sealed,'artifact_sha256':old.sha256_json(sealed)}
                or a.request_bytes(packet) > 500000):
            raise ValueError('fresh complete dual review actual model/transport/integrity drift')
        reviews[label] = old.require_approval(raw['response'],packet); raws.append(raw)
    if old.read_artifact(root/'review-report.json') != {'status':'dual_review_approved_not_applied',
            'packet_sha256':old.sha256_json(packet),'reviews':reviews,'master_data_mutations':0}:
        raise ValueError('fresh dual review report drift')
    return states, proof, draft, packet, reviews, raws


def package_for(states, draft, packet, reviews, raws, root=ROOT):
    from backend.pipeline.viewpoint_audit_link_repair import package_for as identity_package
    targets = [{'before_link':states['viewpoint_claim_links',link['viewpoint_claim_link_id']],
        'after_link':link} for link in draft['viewpoint_claim_links']]
    identities = identity_package(states,{'targets':targets},reviews,root,
        identity_digest=old.sha256_json({'packet':packet,'reviews':reviews,'version':VERSION}))
    package = deepcopy(draft)
    for key in ('viewpoint_claim_links','viewpoint_identity_candidates','viewpoint_identity_decisions'):
        package[key] = identities[key]
    for d in package['viewpoint_identity_decisions']:
        d.update(policy_version=VERSION, reviewer_id='owner_qualified_transcript_dual_model_review',
            reason='Owner-corrected source epoch with first-marriage qualifier and passive Evidence; exact fresh dual-reviewed Claim/link postimage; prior rejection and old source versions retained.')
    package['source_repair_application'] = {'schema_version':VERSION,'user_authorization':packet['owner_authorization'],
        'approval_status':'not_human_approved','review_packet':packet,'review_artifacts':raws,
        'review_root':str(root),'before_binding':{'path':str(root/'before.json'),'file_sha256':old.file_sha256(root/'before.json')},
        'package_sha256':old.sha256_json(package)}
    return package


def validate_package(package):
    app = package.get('source_repair_application') or {}
    if app.get('schema_version') != VERSION or app.get('review_root') != str(ROOT):
        raise ValueError('qualified source repair outside exact version/root')
    states,_,draft,packet,reviews,raws = reviewed_inputs(ROOT)
    if package != package_for(states,draft,packet,reviews,raws,ROOT):
        raise ValueError('qualified source repair package/postimage/identity drift')


def plan_reviewed(root=ROOT):
    """Full store dry-run only. Never infer application from a passing plan."""
    from backend.api.canonical_repository.viewpoint_production_safety import exclusive_cvp_run_lock
    from backend.api.canonical_repository.postgres_store import stored_operation_payload
    load_dotenv(old.PROJECT/'.env')
    states,before,draft,packet,reviews,raws = reviewed_inputs(root)
    package = package_for(states,draft,packet,reviews,raws,root)
    store = old.PostgresKnowledgeStore()
    with exclusive_cvp_run_lock({'global_lock_path':str(old.BASE/'registry-cvp.lock')}):
        live = old.snapshot(store)
        if old.fingerprint(live,before['collections']) != before['pre_fingerprint_sha256']:
            raise ValueError('pre-plan live Registry drift')
        plan = store.plan_package(package,source_kind=VERSION)
        expected = {'source_documents':('update',1),'source_fragments':('update',596),
            'evidence_steps':('update',3),'claims':('update',3),'viewpoint_claim_links':('update',3),
            'viewpoint_identity_candidates':('create',3),'viewpoint_identity_decisions':('create',3)}
        if len(plan.operations) != 612 or any(sum(o.collection==c and o.operation==kind for o in plan.operations)!=n for c,(kind,n) in expected.items()):
            raise ValueError('qualified transcript unexpected operations; no write')
        post = deepcopy(live)
        for op in plan.operations:
            post[op.collection,op.object_id] = stored_operation_payload(op)
        old.write_immutable(root/'effective-package.json',package)
        old.write_immutable(root/'dry-run-plan.json',json.loads(json.dumps(plan.as_dict())))
        old.write_immutable(root/'dry-run-result.json',{'status':'exact_store_plan_passed_not_applied',
            'operations':612,'pre_fingerprint_sha256':before['pre_fingerprint_sha256'],
            'expected_post_fingerprint_sha256':old.fingerprint(post,before['collections']),
            'registry_writes':0,'new_model_calls':0,'source_version_consumer_required':True})
    print('Exact 612-operation store dry-run passed; no writes or new model calls')


def run(root, review=False):
    if root != ROOT:
        raise ValueError('one exact independent qualifier root required')
    if subprocess.check_output(['git','status','--porcelain'],cwd=old.PROJECT,text=True).strip():
        raise ValueError('commit bounded implementation before source review')
    load_dotenv(old.PROJECT/'.env')
    runtime = old.canonical_runtime()
    states = old.snapshot(old.PostgresKnowledgeStore())
    bindings = predecessor(states)
    report, proposal = old.frozen_inputs()
    package, packet = old.materialize(states, report, proposal, root)
    package, packet = extend(states, package, packet, root)
    packet['prior_refusal_bindings'] = bindings
    collections = sorted({c for c,_ in states}); pre = old.fingerprint(states, collections)
    old.write_immutable(root/'before.json', {'collections':collections,'pre_fingerprint_sha256':pre,
        'before_records':[{'collection':c,'object_id':o,'payload':p} for (c,o),p in sorted(states.items())]})
    for name, value in [('draft-package',package),('packet',packet),('canonical-runtime',runtime)]:
        old.write_immutable(root/f'{name}.json',value)
    preflights = {}
    for label, provider, model in [('gpt','codex','gpt-6.1-sol'),('opus','claude','claude-opus-5-5')]:
        a = adapter(provider, model); size = a.request_bytes(packet)
        if size > 500000:
            raise ValueError('qualified source review exceeds frozen request ceiling')
        preflights[label] = {'model':model,'backend':a.backend,'request_bytes':size,
            'request_encoding_proof':a.request_encoding_proof(packet)}
    old.write_immutable(root/'preflight.json',{'packet_sha256':old.sha256_json(packet),
        'draft_package_sha256':old.sha256_json(package),'requests':preflights,'registry_writes':0,'models_called':0})
    if not review:
        print(json.dumps({'status':'preflight_passed_no_models_no_writes','requests':{k:v['request_bytes'] for k,v in preflights.items()}})); return
    reservation = PRIOR/'owner-qualified-transcript-start.json'
    if reservation.exists():
        raise ValueError('new qualifier review already reserved; no repeat')
    authority = {'schema_version':VERSION,'output_root':str(root),'packet_sha256':old.sha256_json(packet),
        'owner_authorization':packet['owner_authorization'],'human_content_approval':False,
        'fresh_reviews_per_provider':1,'prior_refusal_bindings':bindings,'old_failures_budgets_preserved':True}
    old.write_immutable(reservation,authority); old.write_immutable(root/'owner-authorization.json',authority)
    reviews = {}
    for label, provider, model in [('gpt','codex','gpt-6.1-sol'),('opus','claude','claude-opus-5-5')]:
        old.write_immutable(root/f'progress-{label}.json',{'status':'reviewing','master_data_mutations':0})
        raw,_,_ = old.review_call(adapter(provider, model),packet,root,label)
        reviews[label] = old.require_approval(raw, packet)
        if old.fingerprint(old.snapshot(old.PostgresKnowledgeStore()),collections) != pre:
            raise ValueError('Registry changed during qualified transcript review')
        old.verify_source(report)
    old.write_immutable(root/'review-report.json',{'status':'dual_review_approved_not_applied',
        'packet_sha256':old.sha256_json(packet),'reviews':reviews,'master_data_mutations':0})
    print('Fresh qualified transcript dual review approved; exact store/source-version/audit gates still required')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root',type=Path,required=True)
    parser.add_argument('--review',action='store_true')
    parser.add_argument('--plan-reviewed',action='store_true')
    args = parser.parse_args(); root = args.output_root.resolve()
    if root != ROOT:
        raise ValueError('exact independent qualified transcript root required')
    root.mkdir(parents=True,exist_ok=True)
    with (old.BASE/'immediate-recovery-20261002-v1/recovery.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            if args.plan_reviewed:
                if args.review:
                    raise ValueError('saved dual-review planning cannot call models')
                plan_reviewed(root)
            else:
                run(root,args.review)
        except Exception as exc:
            old.write_immutable(root/'failure.json',{'error':str(exc),'master_data_mutations':0,'automatic_repeat':False})
            raise


if __name__ == '__main__':
    main()
