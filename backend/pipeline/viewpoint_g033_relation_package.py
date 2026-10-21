"""Compile the exact owner-authorized g033 postimage from saved relation reviews.

No semantic generation or replay here. The existing repair executor owns fresh
full-postimage dual review, backup, apply, readback and independent audit gates.
"""
from copy import deepcopy
from pathlib import Path

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import exclusive_cvp_run_lock
from backend.pipeline.viewpoint_authorized_source_repair_357 import fingerprint
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable, call_model, stable_decided_at
from backend.pipeline.viewpoint_human_review import validate_handoff, binding, verify_bindings
from backend.pipeline import viewpoint_g033_identity_repair as identity
from backend.pipeline import viewpoint_g033_relation_review as relations

POLICY='wang-g033-owner-reviewed-relation-package-v1'
SEED_PACKET_SHA='45e5b5b4088d6894066a949c92faa9abc623ad48124cf9a318b742be94cbb3ae'
PENDING='PENDING_FRESH_FULL_POSTIMAGE_DUAL_REVIEW'
PROMPT=identity.PROMPT+'''
本包是负责人允许的全后像审核，不是复用先前两条关系的pass替整包背书。
after字段是计划状态，不是已经写库或批准。新revision的approved_at/by为空，provenance引用本包预分配新decision ID，review_artifact_sha256为PENDING_FRESH_FULL_POSTIMAGE_DUAL_REVIEW占位；这是待审，不是旧批准复制。
metadata_materialization_contract明确：仅在本轮GPT及Opus都批准后，执行器将占位改为本轮review-report的内容SHA，写入本轮stable_decided_at/政策approved_by，并将计划记录标system_approved。新decision ID由冻结语义种子确定，批准从旧记录不继承。核查这个机械合同，不把占位当作已审核。
两条盐关系是以完整原文综述/角色/具体要求推出的编辑关系，不是教授直接说“调味/防腐就是发光”，也不把发光与影响硬判同义。不能用用户相容判断代替源证据。
所有成员、三关系和新核心命题逐项核查；任何问题必须拒绝，本轮不再自动改语义或重试到pass。'''

def load_seed(campaign, seed_root, store):
    from backend.pipeline.viewpoint_audit_link_repair import BASE
    if seed_root.resolve()!=BASE/'g033-owner-relation-reconsideration-runtime-20261004-v1':
        raise ValueError('only exact saved relation review seed authorized')
    for name in ('raw-proposal.json','raw-opus.json'):
        if not (seed_root/name).is_file():
            raise ValueError('saved model cache missing; new-call fallback forbidden')
    packet=read_artifact(seed_root/'proposal-input.json')
    handoff_path=Path(packet['old_handoff_binding']['path'])
    if binding(handoff_path)!=packet['old_handoff_binding']: raise ValueError('old handoff binding drift')
    handoff=validate_handoff(handoff_path)
    old=read_artifact(Path(handoff['packet_path']))
    states,context=identity.context(campaign,store)
    before=read_artifact(Path(handoff['before_binding']['path']))
    if context!=packet['context'] or context!=old['context'] or fingerprint(states,before['collections'])!=before['pre_fingerprint_sha256']:
        raise ValueError('saved relation source/Registry drift')
    proposer=relations.adapter('codex','gpt-6.1-sol',relations.PROPOSE,relations.Proposal,'g033_relation_reconsideration_v1')
    raw,calls,_=call_model(proposer,packet,seed_root/'raw-proposal.json')
    if calls: raise ValueError('relation seed must not make new model calls')
    proposed=relations.validate_choices(context,raw)
    reviewed_packet={**packet,'proposal':proposed}
    if reviewed_packet!=read_artifact(seed_root/'review-packet.json') or sha256_json(reviewed_packet)!=SEED_PACKET_SHA:
        raise ValueError('reviewed relation semantic packet drift')
    reviewer=relations.adapter('claude','claude-opus-5-5',relations.REVIEW,relations.Review,'g033_relation_independent_review_v1')
    raw,calls,_=call_model(reviewer,reviewed_packet,seed_root/'raw-opus.json')
    if calls: raise ValueError('saved independent relation review required')
    review=relations.Review.model_validate(raw)
    if len(review.verdicts)!=2 or {v.relation_id for v in review.verdicts}!=relations.RELATIONS or any(not v.approved or v.problems for v in review.verdicts):
        raise ValueError('relation seed is not independently approved')
    if any(r['disposition']!='proposed_relation' for r in proposed['relations']):
        raise ValueError('no-supported/new-scope disposition is not write permission')
    result=read_artifact(seed_root/'verified-result.json')
    if result['status']!='candidate_reviewed' or result['packet_sha256']!=SEED_PACKET_SHA or result['registry_writes']!=0 or result['proposal']!=proposed or result['review']!=review.model_dump(mode='json'):
        raise ValueError('relation completion receipt drift')
    proof=[binding(seed_root/n) for n in ('proposal-input.json','review-packet.json','raw-proposal.json','raw-opus.json','verified-result.json','owner-revision-authorization.json')]
    return states,old,proposed,proof

def compile_packet(old, selected, proof):
    proposal=deepcopy(old['proposal'])
    choices={r['relation_id']:r for r in selected['relations']}
    if set(choices)!=relations.RELATIONS or len(selected['relations'])!=2:
        raise ValueError('relation revision population drift')
    for row in proposal['relations']:
        if row['relation_id'] in choices:
            choice=choices[row['relation_id']]
            if choice['disposition']!='proposed_relation' or not choice['relation_type']:
                raise ValueError('cannot write a deferred relation')
            row.update(relation_type=choice['relation_type'],reason=choice['reason'])
    # The obsolete aggregate rationale described the rejected relation types.
    # Use only the saved model-authored rationales, without new semantic prose.
    proposal['reason']='\n'.join(choices[rid]['reason'] for rid in sorted(choices))
    identity.validate_proposal(old['context'],proposal)
    # Only the two model-authored relation type/reason pairs change semantics.
    digest=sha256_json({'policy':POLICY,'proposal':proposal,'source_proofs':proof})
    rid='CVR-'+sha256_json({'policy':POLICY,'old_revision':identity.OLD_REVISION,'proposal':proposal})[:20]
    packet=deepcopy(old)
    packet.pop('mechanical_storage_proof',None)
    packet['schema_version']=POLICY
    packet['policy']={'policy_id':POLICY,'authorization_quote':'好，继续','automatic_future_identity_repair':False}
    packet['proposal']=proposal
    packet['identity_digest']=digest
    packet['relation_review_proof_bindings']=proof
    packet['relation_attribution_note']='Cross-source editorial relation inference, not a direct professor quote or salt/shining synonym assertion.'
    ids=[]
    for target in packet['targets']:
        link=target['after_link']
        seed=sha256_json({'repair':digest,'link':link['viewpoint_claim_link_id']})
        link.update(validated_against_viewpoint_revision_id=rid,decision_id='VID-'+seed[:20])
        ids.append(link['decision_id'])
    revision=packet['after_revision']
    revision.update(viewpoint_revision_id=rid,review_status='candidate',approved_by=None,approved_at=None,
        provenance={'basis_identity_decision_ids':sorted(ids),'review_artifact_sha256':PENDING})
    by_id={r['relation_id']:r for r in proposal['relations']}
    for relation in packet['after_relations']:
        chosen=by_id[relation['viewpoint_relation_id']]
        relation.update(relation_type=chosen['relation_type'],reason=chosen['reason'],
            review_provenance={'basis_identity_decision_ids':sorted(ids),'review_artifact_sha256':PENDING})
        for side in ('source','target'):
            if relation[side+'_viewpoint_id']==identity.VIEWPOINT:
                relation['validated_'+side+'_viewpoint_revision_id']=rid
    packet['metadata_materialization_contract']={'review_artifact_sha256':PENDING,
        'review_status_after_dual_pass':'system_approved','approved_by_after_dual_pass':POLICY,
        'approved_at_after_dual_pass':'stable_decided_at of this independent execution root',
        'new_decision_ids':sorted(ids),'new_identity_seed':digest,
        'allowed_metadata_changes':['review_status','approved_by','approved_at','provenance.review_artifact_sha256','review_provenance.review_artifact_sha256'],
        'semantic_changes_after_review_allowed':False,'historical_approval_inherited':False}
    packet['mechanical_storage_proof']=identity.storage_proof(packet)
    return packet

def propose(campaign,root,store,*,relation_review_from):
    from backend.pipeline.viewpoint_audit_link_repair import BASE
    with exclusive_cvp_run_lock({'global_lock_path':str(BASE/'registry-cvp.lock')}):
        states,old,selected,proof=load_seed(campaign,relation_review_from,store)
        reservation=relation_review_from/'full-package-execution-start.json'
        if reservation.exists(): raise ValueError('full relation package already reserved; inspect receipts, never replay')
        packet=compile_packet(old,selected,proof)
        from backend.pipeline.viewpoint_authorized_source_repair_357 import Review
        from backend.pipeline.codex_subscription_preflight import verify_runtime
        adapter=relations.adapter('codex','gpt-6.1-sol',PROMPT,Review,'audit_link_repair_review_v1')
        size=adapter.request_bytes(packet)
        if size>500000: raise ValueError('full postimage request exceeds frozen 500000 ceiling')
        runtime=verify_runtime(adapter._client,BASE/'immediate-recovery-20261002-v1/codex-runtime-proofs')
        write_immutable(root/'codex-runtime-preflight.json',runtime)
        write_immutable(root/'request-preflight.json',{'request_bytes':size,'limit':500000,'registry_writes':0})
        cols=sorted({c for c,_ in states})
        write_immutable(root/'relation-review-source.json',{'root':str(relation_review_from),'bindings':proof,'proposal_new_model_calls':0})
        write_immutable(root/'proposal-input.json',packet['context'])
        write_immutable(root/'proposal-before.json',{'collections':cols,'pre_fingerprint_sha256':fingerprint(states,cols)})
        write_immutable(root/'raw-proposal.json',read_artifact(relation_review_from/'raw-proposal.json'))
        write_immutable(root/'owner-authorization.json',{'authorization_quote':'好，继续','source_review_root':str(relation_review_from),
            'viewpoint_id':identity.VIEWPOINT,'link_ids':sorted(identity.LINKS),'relation_ids':sorted(identity.RELATIONS),
            'semantic_scope':'saved core/member proposal and third light relation; replace only two salt type/reason pairs with saved reviewed choices',
            'claim_evidence_source_writes':0,'no_retirement_or_new_endpoints':True,'old_failures_and_budgets_preserved':True})
        write_immutable(root/'metadata-materialization-contract.json',packet['metadata_materialization_contract'])
        write_immutable(reservation,{'output_root':str(root),'packet_sha256':sha256_json(packet),'max_full_gpt_review_calls':1,
            'max_full_opus_review_calls':1,'automatic_semantic_retry':False,'original_model_calls_replayed':0})

def prepare(campaign,root,store):
    ref=read_artifact(root/'relation-review-source.json'); verify_bindings(ref['bindings'])
    states,old,selected,proof=load_seed(campaign,Path(ref['root']),store)
    if proof!=ref['bindings']: raise ValueError('relation source proof population drift')
    before=read_artifact(root/'proposal-before.json')
    if fingerprint(states,before['collections'])!=before['pre_fingerprint_sha256']: raise ValueError('full postimage baseline drift')
    return states,compile_packet(old,selected,proof)

def package_for(states,packet,reviews,root):
    from backend.pipeline.viewpoint_audit_link_repair import package_for as link_package
    package=link_package(states,packet,reviews,root,identity_digest=packet['identity_digest'])
    for link,decision,candidate,target in zip(package['viewpoint_claim_links'],package['viewpoint_identity_decisions'],package['viewpoint_identity_candidates'],packet['targets'],strict=True):
        if link['decision_id']!=target['after_link']['decision_id']: raise ValueError('planned decision ID drift')
        decision.update(claim_link_decisions=[{'claim_id':link['claim_id'],'link_type':link['link_type']}],policy_version=POLICY,
            reason='Owner-authorized full g033 postimage independently dual reviewed. '+target['reason'])
        candidate['proposed_proposition_signature']=packet['after_revision']['proposition_signature']
    ids=sorted(d['identity_decision_id'] for d in package['viewpoint_identity_decisions'])
    if ids!=packet['metadata_materialization_contract']['new_decision_ids']: raise ValueError('new decision population drift')
    revision=deepcopy(packet['after_revision'])
    revision.update(review_status='system_approved',approved_by=POLICY,approved_at=stable_decided_at(root),
        provenance={'basis_identity_decision_ids':ids,'review_artifact_sha256':sha256_json(reviews)})
    cv=deepcopy(packet['context']['before_viewpoint'])
    cv.update(revision=cv['revision']+1,current_revision_id=revision['viewpoint_revision_id'])
    after_relations=deepcopy(packet['after_relations'])
    for row in after_relations:
        row['review_provenance']={'basis_identity_decision_ids':ids,'review_artifact_sha256':sha256_json(reviews)}
    package.update(viewpoint_revisions=[revision],canonical_viewpoints=[cv],viewpoint_relations=after_relations)
    # Undo only the disclosed mechanical materialization and demand exact
    # equality with the frozen reviewed postimage.
    planned=deepcopy(revision)
    for key in ('review_status','approved_by','approved_at','provenance'):
        planned[key]=deepcopy(packet['after_revision'][key])
    if planned!=packet['after_revision']: raise ValueError('reviewed revision semantic drift')
    for actual,expected in zip(after_relations,packet['after_relations'],strict=True):
        planned=deepcopy(actual); planned['review_provenance']=deepcopy(expected['review_provenance'])
        if planned!=expected: raise ValueError('reviewed relation semantic drift')
    for actual,target in zip(package['viewpoint_claim_links'],packet['targets'],strict=True):
        if actual!=target['after_link']: raise ValueError('reviewed link postimage drift')
    package['package_id']='G033-RELATION-PACKAGE-'+sha256_json({'packet':packet,'reviews':reviews})[:20]
    return package
