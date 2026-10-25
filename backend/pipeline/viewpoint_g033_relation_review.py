"""One owner-authorized, read-only reconsideration of two g033 relations.

This produces a new candidate and an independent verdict, never a Registry write.
It cannot accept the old rejected postimage or invent an intermediate viewpoint.
"""
from copy import deepcopy
import argparse
import fcntl
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import exclusive_cvp_run_lock
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.pipeline.viewpoint_authorized_source_repair_357 import snapshot, fingerprint, review_call
from backend.pipeline.viewpoint_human_review import validate_handoff, record_decision, binding
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable, subscription_client

RELATIONS = {'VREL-4c932217b381b031ea4e', 'VREL-d9e9f558b1cf878479b5'}

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Quote(Strict):
    source_id: str
    paragraph_key: str
    exact_text: str

class Choice(Strict):
    relation_id: str
    disposition: Literal['proposed_relation', 'no_supported_relation', 'needs_new_scope']
    relation_type: Literal['generalizes', 'specializes', 'entails', 'extends', 'qualifies', 'applies'] | None
    reason: str
    quotes: list[Quote]

class Proposal(Strict):
    relations: list[Choice]

class Verdict(Strict):
    relation_id: str
    approved: bool
    reason: str
    problems: list[str]

class Review(Strict):
    verdicts: list[Verdict]

PROPOSE = '''只重新提议给定两个观点关系。负责人认可「门徒使命整体可用发光概括」与盐、光并列相容，并授权依教授原文重新审核连接，不授权直接写库。
读全部自身原文、Claim、具体盐观点与发光综述命题。两个原有端点和方向固定，不改Claim、Evidence、任何观点文字、成员连接、第三条光的关系，不新增/退役记录。
可考察盐的具体作用是否具体化同一使命，但这是待查候选方向，不是来源事实。不得把盐说成综述之外的另一使命，亦不得把「发光」与「产生影响」硬判同义。
只能使用现有类型：generalizes一般化；specializes在保留目标真值条件下具体化；entails源蕴含目标；extends加内容改变真值条件；qualifies限定目标；applies用目标到具体行动/对象。
同经文、同主题或人觉得相容不够证明关系。逐条引自己的物理原文逐字片段，并解释固定方向和命题关系。
没有支持则no_supported_relation、relation_type=null。若需「做盐」中间观点或新端点则needs_new_scope、relation_type=null；不能造中间节点或为过关硬选类型。
只输出两行，不执行操作，不评教授神学对错。'''

REVIEW = '''独立审核两个候选关系。读完整自身原文、实际Claim和两端命题；不要信提议者或负责人意见作为教授来源。
逐条验证类型方向和理由、逐字引用、是否仅主题关联、是否把盐置于使命综述之外、是否强造发光与影响同义。
负责人认可总括与盐光并列相容只是解释边界，不是批准某个类型。不新增中间观点，不改端点或文字。
no_supported_relation和needs_new_scope只是提议/暂缓处置，不代表批准退役。没有足够原文依据就拒绝，不能为续跑给pass。
每个relation_id恰好一个verdict；approved=true必须problems=[]。不修正、不执行、不把原拒绝改pass。'''

def validate_choices(context, raw):
    value = Proposal.model_validate(raw).model_dump(mode='json')
    rows = value['relations']
    if len(rows) != 2 or {r['relation_id'] for r in rows} != RELATIONS:
        raise ValueError('relation reconsideration population drift')
    sources = {s['source_id']: {p['paragraph_key']: p['text'] for p in s['paragraphs']} for s in context['sources']}
    for row in rows:
        if not row['reason'].strip() or not row['quotes']:
            raise ValueError('missing relation rationale/source quotes')
        if (row['disposition'] == 'proposed_relation') != (row['relation_type'] is not None):
            raise ValueError('disposition cannot pretend a type or retirement approval')
        for quote in row['quotes']:
            text = sources.get(quote['source_id'], {}).get(quote['paragraph_key'], '')
            if not quote['exact_text'] or quote['exact_text'] not in text:
                raise ValueError('relation quote is not exact physical source')
    return value

def adapter(provider, model, prompt, response, schema):
    result = StructuredJsonReviewerAdapter(client=subscription_client(provider, model, 'high'),
        prompt=prompt, response_model=response, schema_name=schema, compact_payload=True)
    result.max_request_bytes = 500000
    return result

def validate_pre_model_resume(old_root, handoff_path, packet):
    failure=read_artifact(old_root/'failure.json')
    if not failure['error'].startswith('Codex executable unavailable: ') or failure['registry_writes'] != 0:
        raise ValueError('only proven missing executable before inference may resume')
    if any((old_root/name).exists() for name in ('raw-proposal.json','raw-opus.json','review-packet.json','apply-intent.json','apply-receipt.json')):
        raise ValueError('saved model/write receipts forbid pre-model resume')
    if read_artifact(old_root/'proposal-input.json') != packet or packet['old_handoff_binding'] != binding(handoff_path):
        raise ValueError('pre-model continuation semantic input drift')

def run(root, handoff_path, *, resume_pre_model_from=None):
    from backend.pipeline.cvp_retired_runtime import reject_legacy_execution
    reject_legacy_execution()
    from backend.pipeline.viewpoint_audit_link_repair import BASE
    from backend.pipeline.viewpoint_g033_identity_repair import context
    from backend.pipeline.viewpoint_cvp_recovery_supervisor import remaining_producers, process_table, current_job_process_ids
    campaign = BASE/'campaign-recovery-76196a80bb08997f3b3c0abd2fdc38bb5cbf4fa4c46c60330099c9723d0f6a84-recovery-cbb24c76faeb'
    if root.parent != BASE or root.exists(): raise ValueError('new independent #357 root required')
    store = PostgresKnowledgeStore()
    with (BASE/'immediate-recovery-20261002-v1/recovery.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if any(p not in current_job_process_ids() for p in remaining_producers(process_table(), BASE)):
            raise ValueError('another producer active')
        with exclusive_cvp_run_lock({'global_lock_path': str(BASE/'registry-cvp.lock')}):
            handoff = validate_handoff(handoff_path)
            old = read_artifact(Path(handoff['packet_path']))
            before = read_artifact(Path(handoff['before_binding']['path']))
            states, source_context = context(campaign, store)
            if fingerprint(states, before['collections']) != before['pre_fingerprint_sha256'] or source_context != old['context']:
                raise ValueError('old handoff live/source drift')
            reservation = Path(handoff['packet_path']).parent/'owner-relation-reconsideration-start.json'
            if reservation.exists() and resume_pre_model_from is None:
                raise ValueError('this authorized relation reconsideration was already reserved; inspect receipts, no replay')
            prior=read_artifact(reservation) if resume_pre_model_from else None
            if prior and (Path(prior['output_root']) != resume_pre_model_from or resume_pre_model_from.parent != BASE):
                raise ValueError('foreign pre-model continuation')
            root.mkdir(exist_ok=False)
            decision = record_decision(handoff_path, {
                'handoff_sha256': handoff['artifact_sha256'], 'decided_by': 'junyang',
                'authorization_quotes': ['同意门徒的使命整体可以用发光概括”——可以与做盐做光相容。', '同意'],
                'item_ids': [r['item_id'] for r in handoff['items']], 'action': 'authorize_revision',
                'rationale': '用户同意依原文重新提议并审核两条盐与使命综述关系；不批准旧extends后像，不直接退役，不新增中间观点，原文不足则不强行建立。',
                'accepted_packet_sha256': None}, root/'owner-revision-authorization.json')
            reservation_body = {'handoff_sha256': handoff['artifact_sha256'],
                'decision_binding': binding(root/'owner-revision-authorization.json'),
                'output_root': str(root), 'max_proposal_calls':1, 'max_independent_review_calls':1,
                'registry_writes_allowed':0, 'old_failed_receipts_and_budgets_preserved':True}
            packet = {'schema_version': 'wang_g033_relation_reconsideration_v1',
                'authorization': decision, 'old_handoff_binding': binding(handoff_path),
                'context': source_context, 'target_revision': old['after_revision'],
                'preserved_links': old['proposal']['links'],
                'preserved_other_relation': [r for r in old['proposal']['relations'] if r['relation_id'] not in RELATIONS],
                'relation_ids': sorted(RELATIONS), 'registry_fingerprint': before,
                'execution_boundary': 'candidate-only; no apply, retirement, new endpoints or viewpoint text change'}
            # Historical approvals are evidence about the old state, not candidate approval.
            packet['target_revision'] = deepcopy(packet['target_revision'])
            packet['target_revision'].update(review_status='candidate', approved_at=None, approved_by=None)
            packet['target_revision'].pop('provenance', None)
            if resume_pre_model_from:
                validate_pre_model_resume(resume_pre_model_from,handoff_path,packet)
                write_immutable(resume_pre_model_from/'runtime-continuation.json',
                    {'output_root':str(root),'original_reservation':binding(reservation),
                     'same_packet_sha256':sha256_json(packet),'old_model_calls':0,'old_registry_writes':0,
                     'budget_reset':False,'single_continuation_only':True})
            else:
                write_immutable(reservation, reservation_body)
            write_immutable(root/'proposal-input.json', packet)
        proposer = adapter('codex', 'gpt-6.1-sol', PROPOSE, Proposal, 'g033_relation_reconsideration_v1')
        from backend.pipeline.codex_subscription_preflight import verify_runtime
        runtime=verify_runtime(proposer._client,BASE/'immediate-recovery-20261002-v1/codex-runtime-proofs')
        write_immutable(root/'codex-runtime-preflight.json',runtime)
        write_immutable(root/'request-preflight.json', {'request_bytes': proposer.request_bytes(packet), 'limit':500000, 'registry_writes':0})
        raw, calls, _ = review_call(proposer, packet, root, 'proposal')
        proposal = validate_choices(source_context, raw)
        review_packet = {**packet, 'proposal': proposal}
        write_immutable(root/'review-packet.json', review_packet)
        reviewer = adapter('claude', 'claude-opus-5-5', REVIEW, Review, 'g033_relation_independent_review_v1')
        raw_review, review_calls, _ = review_call(reviewer, review_packet, root, 'opus')
        review = Review.model_validate(raw_review).model_dump(mode='json')
        if len(review['verdicts']) != 2 or {v['relation_id'] for v in review['verdicts']} != RELATIONS:
            raise ValueError('independent relation review population drift')
        if any(v['approved'] and v['problems'] for v in review['verdicts']):
            raise ValueError('independent review contradictory approval')
        with exclusive_cvp_run_lock({'global_lock_path':str(BASE/'registry-cvp.lock')}):
            validate_handoff(handoff_path)
            after = fingerprint(snapshot(store), before['collections'])
            if after != before['pre_fingerprint_sha256']: raise ValueError('live Registry changed during review')
            result = {'status':'candidate_reviewed' if all(v['approved'] and not v['problems'] for v in review['verdicts']) else 'content_disagreement_stopped',
                'proposal':proposal, 'review':review, 'packet_sha256':sha256_json(review_packet),
                'model_calls':calls+review_calls, 'registry_writes':0, 'apply_allowed':False,
                'completed_groups':32,'completed_claims':148,'g034_started':False,
                'registry_fingerprint_before':before['pre_fingerprint_sha256'],'registry_fingerprint_after':after}
            write_immutable(root/'verified-result.json', result)
        return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root',type=Path,required=True)
    parser.add_argument('--handoff',type=Path,required=True)
    parser.add_argument('--codex-executable',type=Path,required=True)
    parser.add_argument('--resume-pre-model-from',type=Path)
    args=parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[2]/'.env')
    os.environ['CODEX_EXECUTABLE']=str(args.codex_executable.resolve(strict=True))
    root=args.output_root.resolve()
    try: result=run(root,args.handoff.resolve(),resume_pre_model_from=args.resume_pre_model_from.resolve() if args.resume_pre_model_from else None)
    except Exception as exc:
        if root.is_dir():
            write_immutable(root/'failure.json', {'error':str(exc),'registry_writes':0,'automatic_retry':False})
        raise
    print(result['status'], flush=True)

if __name__=='__main__': main()
