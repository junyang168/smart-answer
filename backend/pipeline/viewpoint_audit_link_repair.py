"""One bounded, source-reviewed equivalent-to-supports repair after GPT upheld an audit.

No Claim/source/CVP text edits, no retargeting, no repeated semantic retries.
The immutable adjudication remains a disagreement, never a forged audit pass.
"""
import argparse
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import time

from dotenv import load_dotenv
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore, stored_operation_payload
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import exclusive_cvp_run_lock, file_sha256
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.pipeline.viewpoint_authorized_source_repair_357 import snapshot, fingerprint, Review, review_call
from backend.pipeline.viewpoint_audit_source_adjudication import build_packet, run as adjudicate
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable, write_derived, subscription_client, stable_decided_at

PROJECT = Path(__file__).resolve().parents[2]
BASE = Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357')
POLICY = PROJECT/'backend/pipeline/policies/wang_audit_link_repair_v1.json'
RETIRE_LINK = 'VCL-c585f66750fd00c8e99b'
RETIRE_PROMPT = '''独立核查负责人明确授权的退役后像和处置记录：只撤销指定错误等价连接，保留Claim作为结构辨识观察背景，不建立supports或新观点。
完整自身原文、Claim、旧目标、既有证据、相邻连接与依赖均提供。不能给Claim补结论；Claim/Evidence/来源/CVP正文、其他连接、依赖必须原样。
after_link只允许revision+1及effective_state=retired，原身份判定仅作历史保留，不冒充当前有效成员。
核查disposition的observation_only/no_registry_assertion是否适当，目标仍有独立有效等价成员且依赖不受错误连接退役影响。
每条指定Claim一个verdict，全部符合才approved=true且problems=[]。不要评价教授神学对错。'''
PROMPT = '''独立审核实际 before_link/after_link：只允许将误标的 equivalent 连接改成 supports。
完整自身原文、原审计与GPT原文裁决、Claim、目标CVP、证据、相邻成员均提供。
支持方向必须是该Claim作为目标观点的真实论据；不能仅因两者相关而批准。
Claim/证据/来源/CVP文字与目标、其他成员、依赖必须不变；本轮全部既有证据保留。
after_link必须忠于自身Claim完整命题，不补入原文中另有但Claim没说的结论。
如果支持关系也不成立、须改观点/Claim/证据/目标或出现其他范围问题，拒绝并说明，不能为了续跑批准。
每个指定Claim恰好一个verdict，approved=true仅当全部条件成立且problems=[]。不评价教授神学对错。
这是系统独立内容审核，不是人工内容批准，不执行操作。'''


def select_repairs(packet, report):
    actions = report.get('actions', [])
    items = {i['item_id']: i for i in packet['items']}
    if (len(items)!=len(packet['items']) or not actions or len(actions) != len(items)
            or {a['item_id'] for a in actions} != set(items)):
        raise ValueError('adjudication population mismatch')
    selected = []
    for action in actions:
        item = items[action['item_id']]
        if action['verdict'] == 'audit_objection_rejected':
            continue  # Preserved in the complete validated handoff, never re-audited to pass.
        if action['verdict'] != 'audit_objection_upheld' or not action['item_id'].startswith('4:'):
            raise ValueError('not a bounded upheld viewpoint connection objection')
        links = item['connections']
        if len(links) != 1 or links[0]['link_type'] not in {'equivalent_full', 'equivalent_component'}:
            raise ValueError('ambiguous/non-equivalent connection; needs owner scope')
        old = links[0]
        if old.get('effective_state', 'active') != 'active':
            raise ValueError('inactive connection cannot be repaired')
        if old['claim_id'] != item['claim']['claim_id'] or old['viewpoint_id'] != item['viewpoint']['viewpoint_id']:
            raise ValueError('foreign connection')
        new = deepcopy(old)
        new.update(revision=old['revision']+1, link_type='supports', component_locator=None)
        selected.append({'item_id': action['item_id'], 'claim': item['claim'],
                         'before_link': old, 'after_link': new,
                         'adjudication': action['gpt'], 'evidence': item['evidence'], 'sources': item['sources']})
    if not selected:
        raise ValueError('no upheld connection to repair')
    if len({i['before_link']['viewpoint_claim_link_id'] for i in selected}) != len(selected):
        raise ValueError('duplicate repair connection')
    if len({(i['claim']['claim_id'], i['before_link']['viewpoint_id']) for i in selected}) != len(selected):
        raise ValueError('ambiguous multiple repair components for one Claim/viewpoint')
    claims = {}
    for item in selected:
        cid = item['claim']['claim_id']
        if cid in claims and claims[cid] != item['claim']:
            raise ValueError('conflicting repair Claim generations')
        claims[cid] = item['claim']
    return selected


def link_review_adapter(client, prompt, *, legacy_serialization=False, contextual_semantics=False,
                        multiple_targets_per_claim=False):
    # New bounded supports reviews use the same proven lossless transport as
    # production. Explicit historical/human/other owner scopes retain their
    # exact cached configuration; never relabel old raw replies as new format.
    if contextual_semantics:
        from backend.pipeline.viewpoint_semantic_contract import semantic_prompt
        if prompt != PROMPT or legacy_serialization:
            raise ValueError('contextual supports adapter cannot relabel a historical owner scope')
        prompt = semantic_prompt('''独立审核 before_link/after_link：仅等价连接改 supports 的精确后像。
完整自身原文、Claim、实际 CVP、证据、原审计和原裁决均提供；不要求通过。
说明具体哪句 Claim、以什么论证作用支持目标哪一部分；不要求单条复述或推出整个 CVP。
不能仅同主题或相容，不能借其他 Claim 补结论，不能以部分依据冒充完整证明。
Claim/Evidence/来源/CVP文字、端点、其他成员及依赖须保持不变，只检查已授权连接修订。
每个指定 Claim 恰好一个 verdict；全部成立才 approved=true、problems=[]。
拒绝须说明缺失的论证作用或实质范围差异；证据不足或需新范围诚实拒绝，不修改内容。''')
    if multiple_targets_per_claim:
        if not contextual_semantics or legacy_serialization:
            raise ValueError('multiple-target review requires current contextual contract')
        prompt += '\n同一 Claim 可有多个不同目标的 targets。必须逐条按 connection ID 和目标 CVR 核查完整后像；每个不同 Claim 恰好一个 verdict，只有其全部 targets 均获批准才能 approved=true。任一连接不成立就拒绝该 Claim，problems 明列连接 ID 和理由；不得只审核其中一个目标。'
    adapter = StructuredJsonReviewerAdapter(client=client, prompt=prompt,
        response_model=Review, schema_name='audit_link_repair_review_v1',
        compact_payload=True, shared_text_payload=not legacy_serialization,
        shared_record_payload=contextual_semantics)
    adapter.max_request_bytes = 500000
    return adapter


def validate_supports_review(result, targets):
    """One verdict per Claim covers every disclosed, distinct target link."""
    ids={i['claim']['claim_id'] for i in targets}
    if (len(result.verdicts)!=len(ids) or {v.claim_id for v in result.verdicts}!=ids
            or any(not v.approved or v.problems for v in result.verdicts)):
        raise ValueError('bounded supports rejected; no second review or automatic content expansion')


def bound_group_audit(group, packet):
    """Consume the exact audited input, including a proven blocked-content supplement."""
    original = group/'independent-audit/audit.json'
    supplemental = group/'content-blocked-fallback/assessment.json'
    bound = packet.get('audit_binding', {})
    if bound.get('path') == str(original):
        selected = original
    elif bound.get('path') == str(supplemental):
        from backend.pipeline.viewpoint_content_blocked_fallback import validate
        assessment = read_artifact(supplemental)
        if not validate(assessment):
            raise ValueError('supplemental audit lacks exact content-blocked proof')
        selected = supplemental
    else:
        raise ValueError('adjudication audit outside exact group inputs')
    if bound != {'path':str(selected), 'file_sha256':file_sha256(selected)}:
        raise ValueError('adjudication audit physical binding drift')
    return selected


def prepare(campaign, store, *, owner_retire=False, saved_source_assessment_root=None, owner_g037_secondary_from=None):
    state = read_artifact(campaign/'progress.json')
    group = campaign/f"g{state['sequence']:03d}"
    audit = group/'independent-audit/audit.json'
    root = group/'audit-source-adjudication'
    if owner_g037_secondary_from:
        from backend.pipeline.viewpoint_g037_secondary_supports import saved_inputs
        if state['sequence']!=37:raise ValueError('secondary authority only permits g037')
        audit,root=saved_inputs(owner_g037_secondary_from,BASE,store)
    elif saved_source_assessment_root is None:
        audit = bound_group_audit(group, read_artifact(root/'packet.json'))
    if saved_source_assessment_root is not None:
        audit = saved_source_assessment_root/'temporary-assessment/assessment.json'
        root = saved_source_assessment_root/'source-adjudication'
        from backend.pipeline.viewpoint_g035_temporary_audit import validate
        if not validate(read_artifact(audit)):
            raise ValueError('saved assessment lacks exact exception proof')
        from backend.pipeline.viewpoint_cvp_recovery_executor import load_campaign
        from backend.pipeline.viewpoint_audit_source_adjudication import validate_population
        _,_,_,groups=load_campaign(campaign)
        plans=list((group/'production/run/batches').glob('*/cvp-change-plan.json'))
        if len(plans)!=1: raise ValueError('ambiguous saved group plan')
        targets=sorted({o['payload']['viewpoint_id'] for o in read_artifact(plans[0])['operations'] if o['collection']=='viewpoint_claim_links'})
        validate_population(read_artifact(audit),groups[state['group_key']]['claim_ids'],targets)
    # Existing exact SHA-bound GPT response is reused; drift is never fallback.
    packet = build_packet(audit, store)
    if read_artifact(root/'packet.json') != packet:
        raise ValueError('original adjudication packet drift')
    if owner_g037_secondary_from:
        from backend.pipeline.viewpoint_g037_secondary_supports import consume_report
        report=consume_report(audit,root,store)
    else:
        report = adjudicate(audit, root, store, final_gpt=True)
    targets = select_repairs(packet, report)
    states = snapshot(store)
    for item in targets:
        old = item['before_link']; cv = old['viewpoint_id']
        if states['viewpoint_claim_links', old['viewpoint_claim_link_id']] != old:
            raise ValueError('live connection drift')
        item['viewpoint_revision'] = states['viewpoint_revisions', old['validated_against_viewpoint_revision_id']]
        item['adjacent_links'] = [p for (c, _), p in states.items() if c=='viewpoint_claim_links' and p.get('effective_state')=='active' and p.get('viewpoint_id')==cv]
        item['adjacent_claims'] = [states['claims', cid] for cid in sorted({p['claim_id'] for p in item['adjacent_links']})]
        if any(p['claim_id']==old['claim_id'] and p['link_type']=='supports' for p in item['adjacent_links']):
            raise ValueError('existing supports edge; needs separately authorized merge')
        # Existing dependencies are read-only review context, never rewritten.
        item['dependent_records'] = [p for (c, _), p in states.items()
            if c in {'viewpoint_structures', 'argument_routes', 'argument_route_attestations', 'viewpoint_relations'}
            and (cv in json.dumps(p, ensure_ascii=False) or old['viewpoint_claim_link_id'] in json.dumps(p, ensure_ascii=False))]
    policy = read_artifact(POLICY)
    if policy['policy_id'] != 'wang-audit-link-repair-v1': raise ValueError('unknown repair policy')
    if owner_retire:
        if len(targets)!=1 or targets[0]['before_link']['viewpoint_claim_link_id']!=RETIRE_LINK:
            raise ValueError('owner retirement authorizes only exact g015 connection')
        item=targets[0]
        if item['dependent_records']:
            raise ValueError('retirement with dependent structures needs additional scope')
        if not any(p['viewpoint_claim_link_id']!=RETIRE_LINK and p['link_type'] in {'equivalent_full','equivalent_component'} for p in item['adjacent_links']):
            raise ValueError('cannot orphan viewpoint membership')
        if any(p['claim_id']==item['claim']['claim_id'] and p['viewpoint_claim_link_id']!=RETIRE_LINK for p in item['adjacent_links']):
            raise ValueError('Claim has another active connection; disposition ambiguous')
        after=deepcopy(item['before_link'])
        after.update(revision=after['revision']+1,effective_state='retired')
        item['after_link']=after
        item['disposition']={'claim_id':item['claim']['claim_id'], 'claim_revision':item['claim']['revision'],
            'processing_status':'resolved','resolution_kind':'no_registry_assertion','resolution_reason_code':'observation_only',
            'reason':'Retained structural-identification observation; neither equivalent nor argument for the target section-start conclusion.',
            'retired_connection_id':RETIRE_LINK, 'claim_preserved':True}
        policy={'policy_id':'wang-g015-owner-link-retirement-v1','authorized_by':'owner','authorization_quote':'好',
            'allowed_link_id':RETIRE_LINK,'claim_source_viewpoint_text_writes':0,'automatic_future_retirement':False}
    result={'schema_version':'wang_audit_link_repair_packet_v1', 'targets':targets,
                   'adjudication_packet_sha256':packet['artifact_sha256'], 'adjudication_handoff':report,
                   'policy':policy, 'claim_source_viewpoint_text_writes':0}
    if owner_g037_secondary_from:
        from backend.pipeline.viewpoint_g037_secondary_supports import validate_target,VERSION
        validate_target(result)
        result['owner_authorization']={'policy_id':VERSION,'authorization_quote':'同意',
            'human_content_approval':False,'future_repairs_authorized':False,
            'source_root':str(owner_g037_secondary_from),'prior_claim_repair_replay':False}
    return states,result


def package_for(states, packet, reviews, root, *, identity_digest=None):
    context_review=packet.get('policy',{}).get('policy_id')=='wang-g037-owner-context-review-v1'
    if context_review:
        from backend.pipeline.viewpoint_g037_context_review import validate_postimages
        validate_postimages(packet)
    digest = identity_digest or sha256_json({'packet':packet, 'reviews':reviews})
    if packet.get('policy',{}).get('policy_id')=='wang-g015-owner-link-retirement-v1':
        if len(packet['targets'])!=1 or packet['targets'][0]['before_link']['viewpoint_claim_link_id']!=RETIRE_LINK:
            raise ValueError('retirement package outside owner scope')
        old=packet['targets'][0]['before_link']; new=packet['targets'][0]['after_link']
        expected=deepcopy(old); expected.update(revision=old['revision']+1,effective_state='retired')
        if new!=expected: raise ValueError('retirement changed fields outside revision/state')
        return {'package_id':'AUDIT-LINK-RETIRE-'+digest[:20],
                'viewpoint_claim_links':[deepcopy(i['after_link']) for i in packet['targets']]}
    package = {'package_id':'AUDIT-LINK-REPAIR-'+digest[:20], 'viewpoint_claim_links':[],
               'viewpoint_identity_candidates':[], 'viewpoint_identity_decisions':[]}
    for item in packet['targets']:
        old = item['before_link']; link = deepcopy(item['after_link'])
        previous = states['viewpoint_identity_decisions', old['decision_id']]
        candidate = deepcopy(states['viewpoint_identity_candidates', previous['identity_candidate_id']])
        seed = sha256_json({'repair':digest, 'link':old['viewpoint_claim_link_id']})
        candidate.update(revision=1, candidate_claim_ids=[old['claim_id']], candidate_viewpoint_ids=[old['viewpoint_id']], proposed_action='match_existing', generation_fingerprint=seed)
        identity = {'claims':candidate['candidate_claim_ids'], 'viewpoints':candidate['candidate_viewpoint_ids'],
                    'relations':candidate.get('seed_relation_ids',[]), 'action':candidate['proposed_action'],
                    'blockers':candidate.get('blocker_codes',[]), 'coverage_snapshot_id':candidate.get('coverage_snapshot_id'), 'generation_fingerprint':seed}
        if candidate.get('scope_manifest_sha256'): identity['scope_manifest_sha256']=candidate['scope_manifest_sha256']
        candidate['identity_candidate_id']='VIC-'+sha256_json(identity)[:20]
        decision = deepcopy(previous)
        decision.update(revision=1, identity_decision_id='VID-'+seed[:20], identity_candidate_id=candidate['identity_candidate_id'],
            input_sha256=seed, claim_link_decisions=[{'claim_id':old['claim_id'],'link_type':link['link_type']}],
            review_artifact_sha256=sha256_json(reviews), semantic_call_artifact_sha256s=sorted(read_artifact(root/f'raw-{label}.json')['artifact_sha256'] for label in ('gpt','opus')),
            reviewer_model_ids=sorted(['gpt-6.1-sol','claude-opus-5-5']), reviewer_kind='system', reviewer_id='audit_link_repair_dual_model_review', approval_basis='dual_model_consensus',
            review_status='system_approved', created_at=stable_decided_at(root), policy_version='wang-audit-link-repair-v1',
            reason='Full-own-source GPT upheld equivalent-link objection; exact supports postimage independently dual reviewed; source Claim and viewpoint unchanged.')
        link['decision_id']=decision['identity_decision_id']
        if context_review:
            decision.update(policy_version='wang-g037-owner-context-review-v1',
                reason='Owner-authorized single contextual re-review; exact preserved equivalence or supports postimage independently dual reviewed; historical refusal retained; Claim and viewpoint unchanged.')
        package['viewpoint_claim_links'].append(link); package['viewpoint_identity_candidates'].append(candidate); package['viewpoint_identity_decisions'].append(decision)
    return package


def run(campaign, root, *, owner_retire=False, owner_identity_g033=False, storage_review_from=None,
        human_review_from=None, human_handoff=None, human_decisions=(), relation_review_from=None,
        saved_source_assessment_root=None, owner_g037_context_from=None, owner_g037_claim_statement=False,
        owner_g037_claim_condition=False, owner_g037_secondary_from=None):
    from backend.pipeline.cvp_retired_runtime import reject_legacy_execution
    reject_legacy_execution()
    store = PostgresKnowledgeStore()
    global_lock={'global_lock_path':str(BASE/'registry-cvp.lock')}
    if owner_g037_secondary_from and any((owner_retire,owner_identity_g033,storage_review_from,human_review_from,human_handoff,human_decisions,relation_review_from,saved_source_assessment_root,owner_g037_context_from,owner_g037_claim_statement,owner_g037_claim_condition)):
        raise ValueError('secondary connection authority cannot combine other scopes')
    if owner_retire and owner_identity_g033:
        raise ValueError('owner scopes cannot be combined')
    if owner_g037_claim_condition and owner_g037_claim_statement:
        raise ValueError('distinct Claim revision authorities cannot combine')
    if owner_g037_claim_condition:
        owner_g037_claim_statement=True
    if owner_g037_claim_statement and any((owner_retire,owner_identity_g033,storage_review_from,human_review_from,human_handoff,human_decisions,relation_review_from,saved_source_assessment_root,owner_g037_context_from)):
        raise ValueError('g037 Claim revision cannot combine other authorities')
    if owner_g037_claim_condition:
        from backend.pipeline import viewpoint_g037_claim_condition_repair as claim_repair
    elif owner_g037_claim_statement:
        from backend.pipeline import viewpoint_g037_claim_statement_repair as claim_repair
    if owner_g037_context_from and any((owner_retire,owner_identity_g033,storage_review_from,human_review_from,human_handoff,human_decisions,relation_review_from,saved_source_assessment_root)):
        raise ValueError('g037 context review cannot combine other authorities')
    if saved_source_assessment_root and any((owner_retire,owner_identity_g033,human_review_from)):
        raise ValueError('saved source assessment permits only existing narrow supports route')
    if storage_review_from and not owner_identity_g033: raise ValueError('storage correction requires exact g033 owner scope')
    if relation_review_from and (not owner_identity_g033 or storage_review_from or human_review_from):
        raise ValueError('relation revision requires separate exact g033 scope, not another correction/waiver')
    if any((human_review_from,human_handoff,human_decisions)) and not all((human_review_from,human_handoff,human_decisions)):
        raise ValueError('human resume requires saved review root, exact handoff and decisions')
    authority = None
    if human_review_from:
        if storage_review_from: raise ValueError('human resume cannot start another model correction')
        from backend.pipeline.viewpoint_human_review import execution_authority
        with exclusive_cvp_run_lock(global_lock):
            old_root = human_review_from.resolve()
            if old_root.parent != BASE or old_root == root or any((old_root/n).exists() for n in ('apply-intent.json','apply-receipt.json')):
                raise ValueError('human resume requires an unapplied frozen review root')
            old_packet = read_artifact(old_root/'packet.json')
            old_before = read_artifact(old_root/'before.json')
            from backend.pipeline.viewpoint_human_review import repair_handoff
            saved_reviews = {label: read_artifact(old_root/f'raw-{label}.json')['response'] for label in ('gpt','opus')}
            # Reconstruct the complete disputed population from the exact saved
            # responses. A custom handoff cannot omit an inconvenient finding.
            repair_handoff(old_root,old_packet,saved_reviews,output=human_handoff)
            live = snapshot(store)
            authority = execution_authority(human_handoff, human_decisions, packet=old_packet,
                registry_fingerprint_sha256=fingerprint(live,old_before['collections']))
            from backend.pipeline.viewpoint_human_review import validate_handoff
            if Path(validate_handoff(human_handoff)['packet_path']).resolve() != old_root/'packet.json':
                raise ValueError('human handoff belongs to another review root')
            reservation = BASE/'human-review-consumptions'/f"{authority['handoff_sha256']}.json"
            if reservation.exists(): raise ValueError('human postimage already reserved; inspect receipts, do not replay')
            for name in ('raw-gpt.json','raw-opus.json'):
                if not (old_root/name).is_file(): raise ValueError('saved independent review missing; no model fallback')
            write_immutable(reservation,{'authority':authority,'output_root':str(root),'model_calls_allowed':0})
            for name in ('raw-gpt.json','raw-opus.json','proposal-input.json','proposal-before.json',
                         'raw-proposal.json','owner-authorization.json','request-preflight.json','mechanical-review-source.json'):
                if (old_root/name).is_file(): write_immutable(root/name,read_artifact(old_root/name))
            write_immutable(root/'human-execution-authority.json',authority)
    if owner_identity_g033:
        from backend.pipeline import viewpoint_g033_identity_repair as identity_repair
        if relation_review_from:
            from backend.pipeline import viewpoint_g033_relation_package as identity_repair
            identity_repair.propose(campaign,root,store,relation_review_from=relation_review_from)
        elif not human_review_from:
            identity_repair.propose(campaign,root,store,storage_review_from=storage_review_from)
    def prepare_current():
        states,packet=identity_repair.prepare(campaign,root,store) if owner_identity_g033 else prepare(campaign,store,owner_retire=owner_retire,
            saved_source_assessment_root=saved_source_assessment_root,owner_g037_secondary_from=owner_g037_secondary_from)
        if owner_g037_context_from:
            from backend.pipeline.viewpoint_g037_context_review import prepare as context_prepare
            if read_artifact(campaign/'progress.json')['sequence']!=37:
                raise ValueError('context review authorizes only g037')
            packet=context_prepare(packet,states,owner_g037_context_from,BASE)
        if owner_g037_claim_statement:
            if read_artifact(campaign/'progress.json')['sequence']!=37:
                raise ValueError('Claim statement authority authorizes only g037')
            packet=claim_repair.prepare(packet,states,root,BASE)
        return states,packet
    with exclusive_cvp_run_lock(global_lock):
        states, packet=prepare_current()
        collections=sorted({c for c,_ in states}); before=fingerprint(states,collections)
        if human_review_from and packet != old_packet: raise ValueError('human resume semantic postimage drift')
        write_immutable(root/'packet.json',packet); write_immutable(root/'before.json',{'collections':collections,'pre_fingerprint_sha256':before})
        if owner_g037_secondary_from:
            from backend.pipeline.viewpoint_g037_secondary_supports import reserve,proof_bindings
            preflight={}
            for provider,model in [('codex','gpt-6.1-sol'),('claude','claude-opus-5-5')]:
                adapter=link_review_adapter(subscription_client(provider,model,'high'),PROMPT)
                if adapter.request_bytes(packet)>500000:raise ValueError('secondary supports review exceeds frozen ceiling')
                preflight[provider]=adapter.request_encoding_proof(packet)
            write_immutable(root/'request-preflight.json',preflight)
            write_immutable(root/'prior-applied-proof.json',{'bindings':proof_bindings(owner_g037_secondary_from),'registry_writes':0,'model_replays':0})
            reserve(root,owner_g037_secondary_from)
        if owner_g037_context_from:
            from backend.pipeline.viewpoint_g037_context_review import reserve, PROMPT as context_prompt
            # Verify both exact transports before reserving/calling either model.
            preflight={}
            for provider,model in [('codex','gpt-6.1-sol'),('claude','claude-opus-5-5')]:
                adapter=link_review_adapter(subscription_client(provider,model,'high'),context_prompt)
                proof=adapter.request_encoding_proof(packet)
                if adapter.request_bytes(packet)>500000:
                    raise ValueError('context review request exceeds frozen ceiling')
                preflight[provider]=proof
            write_immutable(root/'request-preflight.json',preflight)
            reserve(root,owner_g037_context_from,BASE)
        if owner_g037_claim_statement:
            preflight={}
            for provider,model in [('codex','gpt-6.1-sol'),('claude','claude-opus-5-5')]:
                adapter=claim_repair.review_adapter(subscription_client(provider,model,'high'))
                proof=adapter.request_encoding_proof(packet)
                if adapter.request_bytes(packet)>500000:
                    raise ValueError('Claim statement review request exceeds frozen ceiling')
                preflight[provider]=proof
            write_immutable(root/'request-preflight.json',preflight)
            claim_repair.reserve(root,BASE)
    write_derived(root/'progress.json',{'status':'reviewing','master_data_mutations':0})
    reviews={}
    ids={i['claim']['claim_id'] for i in packet['targets']}
    for label,provider,model in [('gpt','codex','gpt-6.1-sol'),('opus','claude','claude-opus-5-5')]:
        prompt=identity_repair.PROMPT if owner_identity_g033 else RETIRE_PROMPT if owner_retire else PROMPT
        if owner_g037_context_from: prompt=context_prompt
        if owner_g037_claim_statement: prompt=claim_repair.PROMPT
        adapter=link_review_adapter(subscription_client(provider,model,'high'),prompt,
            legacy_serialization=bool(human_review_from or owner_identity_g033 or owner_retire),
            contextual_semantics=bool(not (human_review_from or owner_identity_g033 or owner_retire or owner_g037_context_from or owner_g037_claim_statement or owner_g037_secondary_from)
                and (not (root/f'raw-{label}.json').exists() or
                     read_artifact(root/f'raw-{label}.json').get('prompt_sha256') != sha256_json({'prompt':PROMPT}))))
        if owner_g037_claim_statement: adapter=claim_repair.review_adapter(subscription_client(provider,model,'high'))
        raw,calls,_=review_call(adapter,packet,root,label)
        if human_review_from and calls: raise ValueError('human resume must not call models')
        result=Review.model_validate(raw)
        if len(result.verdicts)!=len(ids) or {v.claim_id for v in result.verdicts}!=ids:
            raise ValueError('repair review population mismatch')
        reviews[label]=result.model_dump(mode='json')
        if any(not v.approved or v.problems for v in result.verdicts):
            if authority is None:
                from backend.pipeline.viewpoint_human_review import repair_handoff
                repair_handoff(root, packet, reviews)
                raise ValueError('owner identity repair not approved; no semantic retry' if owner_identity_g033 else 'bounded supports repair not approved; no semantic retry')
    write_immutable(root/'review-report.json',{'packet_sha256':sha256_json(packet),'reviews':reviews,
        'approved':all(v['approved'] and not v['problems'] for r in reviews.values() for v in r['verdicts']),
        'human_content_approval':authority is not None,'human_execution_authority':authority})
    with exclusive_cvp_run_lock(global_lock):
        live,current=prepare_current()
        if current!=packet or fingerprint(live,collections)!=before: raise ValueError('pre-write Registry/source drift')
        if authority is not None:
            from backend.pipeline.viewpoint_human_review import execution_authority, mark_human_package
            if execution_authority(human_handoff,human_decisions,packet=packet,registry_fingerprint_sha256=before)!=authority:
                raise ValueError('human execution permission drift')
        package=identity_repair.package_for(live,packet,reviews,root) if owner_identity_g033 else package_for(live,packet,reviews,root)
        if owner_g037_claim_statement: package=claim_repair.package_for(packet,reviews,root)
        if authority is not None: package=mark_human_package(package,authority)
        plan=store.plan_package(package,source_kind='wang_source_adjudicated_link_repair')
        if owner_identity_g033:
            expected_ops={'viewpoint_revisions':('create',1),'canonical_viewpoints':('update',1),
                'viewpoint_claim_links':('update',2),'viewpoint_relations':('update',3),
                'viewpoint_identity_candidates':('create',2),'viewpoint_identity_decisions':('create',2)}
            if len(plan.operations)!=11 or any(sum(o.collection==c and o.operation==kind for o in plan.operations)!=count for c,(kind,count) in expected_ops.items()):
                raise ValueError('unexpected owner identity repair writes')
        elif owner_g037_claim_statement:
            expected_ops={'claims':('update',1),'viewpoint_claim_links':('update',6),
                'viewpoint_identity_candidates':('create',6),'viewpoint_identity_decisions':('create',6)}
            if len(plan.operations)!=19 or any(sum(o.collection==c and o.operation==kind for o in plan.operations)!=count for c,(kind,count) in expected_ops.items()):
                raise ValueError('unexpected owner Claim statement repair writes')
        elif len(plan.operations)!=(1 if owner_retire else 3)*len(ids) or any(o.collection not in package or (o.collection=='viewpoint_claim_links')!=(o.operation=='update') for o in plan.operations): raise ValueError('unexpected repair writes')
        if owner_retire:
            write_immutable(root/'claim-disposition.json',{'schema_version':'wang_owner_reviewed_claim_disposition_v1',
                'rows':[i['disposition'] for i in packet['targets']], 'reviews_sha256':sha256_json(reviews),
                'packet_sha256':sha256_json(packet),'historical_scope_run_preserved':True})
        write_immutable(root/'effective-package.json',package); write_immutable(root/'dry-run-plan.json',json.loads(json.dumps(plan.as_dict())))
    write_derived(root/'progress.json',{'status':'backup_before_apply','master_data_mutations':0})
    out=subprocess.run(['scripts/backup-registry.sh','before-audit-link-'+sha256_json(str(root))[:24]],cwd=PROJECT,check=True,capture_output=True,text=True)
    match=re.search(r'backup-registry: wrote (.+\.dump) \(',out.stdout)
    if not match: raise ValueError('backup not reported')
    backup=Path(match.group(1)).resolve()
    listing=subprocess.run(['pg_restore','--list',str(backup)],check=True,capture_output=True,text=True)
    if not listing.stdout.strip(): raise ValueError('empty restore catalog')
    from backend.pipeline.viewpoint_cvp_recovery_executor import binding, resume_after_repair, load_campaign, run_logged, PYTHON
    write_immutable(root/'backup.json',binding(backup))
    with exclusive_cvp_run_lock(global_lock):
        live,current=prepare_current()
        if current!=packet or fingerprint(live,collections)!=before: raise ValueError('pre-apply drift')
        if authority is not None and execution_authority(human_handoff,human_decisions,packet=packet,registry_fingerprint_sha256=before)!=authority:
            raise ValueError('pre-apply human permission drift')
        replay=store.plan_package(package,source_kind='wang_source_adjudicated_link_repair')
        if replay.as_dict()!=plan.as_dict(): raise ValueError('dry-run/apply drift')
        if binding(backup)!=read_artifact(root/'backup.json'): raise ValueError('backup drift')
        write_immutable(root/'apply-intent.json',{'policy':packet['policy'],'backup':binding(backup),'packet_sha256':sha256_json(packet)})
        receipt=store.apply_plan(replay,metadata={'source_adjudicated_link_repair':str(root)},expected_registry_fingerprint_sha256=before,registry_collections=collections)
        write_immutable(root/'apply-receipt.json',receipt)
        expected=deepcopy(live)
        for op in replay.operations: expected[op.collection,op.object_id]=stored_operation_payload(op)
        post=snapshot(store)
        if set(post)!=set(expected) or fingerprint(post,collections)!=fingerprint(expected,collections): raise ValueError('complete Registry readback mismatch')
        for item in packet['targets']:
            for source in item['sources']:
                if file_sha256(Path(source['path']))!=source['file_sha256']: raise ValueError('physical source drift')
        write_immutable(root/'verified-result.json',{'status':'applied_readback_verified','change_set_id':replay.change_set_id,'master_data_mutations':len(replay.operations),'claim_source_viewpoint_text_writes':1 if owner_identity_g033 or owner_g037_claim_statement else 0,'claim_evidence_source_writes':1 if owner_g037_claim_statement else 0})
    write_derived(root/'progress.json',{'status':'independent_audit_before_resume','master_data_mutations':len(replay.operations)})
    continuation=root/'continuation'; continuation.mkdir(exist_ok=False)
    # Audit once. A fresh disputed report goes to the standing own-source GPT
    # final route, not back to Gemini until it happens to return pass.
    cfg, schedule, manifest, groups = load_campaign(campaign)
    state = read_artifact(campaign/'progress.json')
    group = campaign/f"g{state['sequence']:03d}"
    plans = list((group/'production/run/batches').glob('*/cvp-change-plan.json'))
    if len(plans) != 1: raise ValueError('ambiguous original applied plan')
    from backend.pipeline.viewpoint_group_audit_population import group_audit_targets
    targets = group_audit_targets(plans[0])
    audit_root = continuation/'independent-reaudit'
    run_logged([PYTHON, 'scripts/audit-library.py', '--scope', 'all', '--layers', '1,2,3,4',
        '--claim-ids', ','.join(groups[state['group_key']]['claim_ids']), '--viewpoint-ids', ','.join(targets),
        '--workers', '2', '--model', 'gemini-3.8-flash', '--out', audit_root, '--no-status-file', '--preserve-full-context'], continuation, 'independent-reaudit')
    audit_path = audit_root/'audit.json'
    from backend.pipeline.viewpoint_content_blocked_fallback import apply as apply_content_blocked_fallback
    audit_path = apply_content_blocked_fallback(audit_path, continuation/'content-blocked-fallback')
    from backend.pipeline.viewpoint_audit_source_adjudication import disputes, validate_population
    resolution_path = None
    if disputes(read_artifact(audit_path)):
        validate_population(read_artifact(audit_path), groups[state['group_key']]['claim_ids'], targets)
        adjudication_root = continuation/'audit-source-adjudication'
        report = adjudicate(audit_path, adjudication_root, store, final_gpt=True)
        if not report['actions'] or any(a['verdict']!='audit_objection_rejected' for a in report['actions']):
            raise ValueError('post-repair own-source GPT upholds/unresolved objection; one repair exhausted, needs owner')
        from backend.pipeline.viewpoint_audit_owner_resolution import create_gpt_resolution
        resolution_path = continuation/'gpt-source-final-resolution.json'
        create_gpt_resolution(audit_path, adjudication_root, resolution_path, store)
    executable=Path(os.environ['CODEX_EXECUTABLE'])
    proof_names=['packet.json','review-report.json','effective-package.json','dry-run-plan.json','apply-receipt.json','verified-result.json']
    if owner_retire: proof_names.append('claim-disposition.json')
    if owner_identity_g033: proof_names.extend(['owner-authorization.json','proposal-input.json','proposal-before.json','raw-proposal.json'])
    if storage_review_from: proof_names.append('mechanical-review-source.json')
    if relation_review_from: proof_names.extend(['relation-review-source.json','metadata-materialization-contract.json'])
    if authority is not None: proof_names.append('human-execution-authority.json')
    if owner_g037_context_from: proof_names.extend(['owner-authorization.json','request-preflight.json'])
    if owner_g037_claim_statement: proof_names.extend(['owner-authorization.json','request-preflight.json'])
    previous_proofs=[]
    source_repairs=[root/'effective-package.json'] if owner_g037_claim_statement else []
    if owner_g037_secondary_from:
        # After this new write the old audit input necessarily changes. Consume
        # the pre-write validated physical bindings, not a fresh old-input audit.
        previous_proofs=read_artifact(root/'prior-applied-proof.json')['bindings']
        for b in previous_proofs:
            if binding(b['path'])!=b:raise ValueError('prior applied proof changed')
        source_repairs.append(owner_g037_secondary_from/'effective-package.json')
        proof_names.extend(['owner-authorization.json','request-preflight.json','prior-applied-proof.json'])
    result=resume_after_repair(campaign,continuation,BASE/'immediate-recovery-20261002-v1',executable,
        saved_audit_path=audit_path, owner_resolution_path=resolution_path,
        additional_source_repair_paths=source_repairs,
        additional_proof_bindings=[binding(root/name) for name in proof_names]+previous_proofs)
    write_derived(root/'progress.json',{'status':result['status'],'continuation':result})
    return result


def launch_repair(campaign, packet, report):
    """One detached, locked execution per exact adjudication; no restart loop."""
    select_repairs(packet, report)  # Out-of-scope decisions never launch a writer.
    from backend.pipeline.viewpoint_cvp_campaign_runner import PYTHON
    identity = sha256_json({'campaign':str(campaign.resolve()), 'handoff':report})
    output = BASE/('audit-link-repair-'+identity[:24])
    group = campaign/f"g{read_artifact(campaign/'progress.json')['sequence']:03d}"
    started = group/'controlled-link-repair-start.json'
    if output.exists() or started.exists(): raise ValueError('repair already attempted; no automatic retry')
    # Reserve before launch so a crash cannot silently create a second attempt.
    write_immutable(started, {'repair_root':str(output), 'handoff_sha256':report['artifact_sha256'], 'completed':False})
    subprocess.run(['screen', '-dmS', 'wkp357-audit-link-repair', str(PYTHON), '-m',
        'backend.pipeline.viewpoint_audit_link_repair', '--campaign-root', str(campaign),
        '--output-root', str(output), '--wait-parent-pid', str(os.getpid())], cwd=PROJECT, check=True)
    return output


def main():
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('--campaign-root',type=Path,required=True); ap.add_argument('--output-root',type=Path,required=True); ap.add_argument('--wait-parent-pid',type=int); ap.add_argument('--owner-retire-g015-link',action='store_true'); ap.add_argument('--owner-identity-g033',action='store_true')
    ap.add_argument('--owner-g033-storage-review-from',type=Path)
    ap.add_argument('--owner-g033-relation-review-from',type=Path)
    ap.add_argument('--codex-executable',type=Path)
    ap.add_argument('--saved-source-assessment-root',type=Path)
    ap.add_argument('--owner-g037-context-from',type=Path)
    ap.add_argument('--owner-g037-claim-statement',action='store_true')
    ap.add_argument('--owner-g037-claim-condition',action='store_true')
    ap.add_argument('--owner-g037-secondary-from',type=Path)
    ap.add_argument('--human-review-from',type=Path)
    ap.add_argument('--human-handoff',type=Path)
    ap.add_argument('--human-decision',type=Path,action='append',default=[])
    args=ap.parse_args()
    campaign=args.campaign_root.resolve(); root=args.output_root.resolve()
    if PROJECT.name!='wkp-357-worktree-per-session' or campaign.parent!=BASE or root.parent!=BASE: raise ValueError('only #357 independent roots allowed')
    load_dotenv(PROJECT/'.env')
    if args.codex_executable: os.environ['CODEX_EXECUTABLE']=str(args.codex_executable.resolve(strict=True))
    if args.owner_g033_relation_review_from and not args.codex_executable:
        raise ValueError('relation package requires the explicit canonical subscription executable')
    if args.wait_parent_pid:
        for _ in range(60):
            try: os.kill(args.wait_parent_pid,0)
            except ProcessLookupError: break
            time.sleep(1)
        else: raise ValueError('parent campaign did not exit; no parallel repair')
    with (BASE/'immediate-recovery-20261002-v1/recovery.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        from backend.pipeline.viewpoint_cvp_recovery_supervisor import remaining_producers, process_table, current_job_process_ids
        if any(p not in current_job_process_ids() for p in remaining_producers(process_table(),BASE)): raise ValueError('another producer active')
        if subprocess.check_output(['git','status','--porcelain'],cwd=PROJECT,text=True).strip(): raise ValueError('committed clean code required')
        root.mkdir(exist_ok=False)
        try: print(json.dumps(run(campaign,root,owner_retire=args.owner_retire_g015_link,owner_identity_g033=args.owner_identity_g033,storage_review_from=args.owner_g033_storage_review_from,
            human_review_from=args.human_review_from,human_handoff=args.human_handoff,human_decisions=args.human_decision,
            relation_review_from=args.owner_g033_relation_review_from,
            saved_source_assessment_root=args.saved_source_assessment_root,owner_g037_context_from=args.owner_g037_context_from,
            owner_g037_claim_statement=args.owner_g037_claim_statement,
            owner_g037_claim_condition=args.owner_g037_claim_condition,owner_g037_secondary_from=args.owner_g037_secondary_from)),flush=True)
        except Exception as exc:
            write_derived(root/'failure.json',{'error':str(exc),'applied':(root/'apply-receipt.json').exists(),'no_next_group_started':True})
            write_derived(root/'progress.json',{'status':'stopped','error':str(exc),'applied':(root/'apply-receipt.json').exists()}); raise


if __name__=='__main__': main()
