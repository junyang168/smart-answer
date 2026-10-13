"""One owner-authorized Opus review of the post-source-repair connection.

Review only: no Registry mutation, no production restart, no GPT preapproval.
The already-applied source package and original upheld finding remain immutable.
"""
from copy import deepcopy
import fcntl
from pathlib import Path

from dotenv import load_dotenv

from backend.pipeline import viewpoint_s201129_source_repair as source
from backend.pipeline import viewpoint_s201129_source_epoch as epoch
from backend.pipeline.viewpoint_resolution_runtime import (
    read_artifact, write_immutable, subscription_client, call_model,
)
from backend.pipeline.viewpoint_audit_link_repair import link_review_adapter
from backend.pipeline.viewpoint_authorized_source_repair_357 import Review, review_call
from backend.pipeline.viewpoint_audit_source_adjudication import (
    build_packet, source_review_adapter, validate_findings,
)

ROOT = source.BASE/'s201129-owner-cl005-supports-review-20261006-v1'
PRIOR = source.APPLICATION_ROOT/'continuation/local-repair-audit'
LINK = 'VCL-ed1a6432bb1de5f70816'
CLAIM = source.PREFIX+'CL005'
CV = 'CV-a3d878c01ce2f2b457d8'
VERSION = 'wang_s201129_owner_cl005_supports_v1'
HANDOFF_SHA = '31137c2ae6328cf5207df3691cc2df1214bbf9471dd4e05eb5e09cd2ac9538af'
RAW_SHA = '92514c79501d26030459f829511b21e32037c39e402404d03a500bfda8570160'


def derive(old, claim):
    if (old['viewpoint_claim_link_id'], old['claim_id'], old['viewpoint_id'],
            old['revision'], old['link_type'], claim['claim_id'], claim['revision']) != (
            LINK, CLAIM, CV, 2, 'equivalent_component', CLAIM, 3):
        raise ValueError('owner repair target/version drift')
    text = claim['statement']
    if text != '馬太福音五章三十二節關於被休婦人及娶她之人的兩個表達，都應按被動語態理解。':
        raise ValueError('complete two-subject Claim drift')
    expected = deepcopy(old)
    expected.update(revision=3, link_type='supports', component_locator={
        'claim_sha256':source.semantic_record_sha(claim),
        'statement_component':text,
        'canonical_spans':[{'start_char':0,'end_char':len(text),'exact_text':text}],
    })
    return expected


def validate_target(packet):
    if len(packet['targets']) != 1:
        raise ValueError('exactly one owner-authorized connection')
    target = packet['targets'][0]
    if target['after_link'] != derive(target['before_link'], target['claim']):
        raise ValueError('supports postimage exceeds owner type/locator authority')
    from backend.api.canonical_repository.knowledge_models import ViewpointClaimLinkRecord
    ViewpointClaimLinkRecord.model_validate(target['after_link'])


def prepare(store):
    _, _, states = epoch.verified_applied(epoch.binding(),store)
    verified = read_artifact(source.APPLICATION_ROOT/'verified-result.json')
    collections = sorted({c for c,_ in states})
    if source.fingerprint(states,collections) != verified['post_fingerprint_sha256']:
        raise ValueError('full Registry changed since the applied source repair')
    audit = PRIOR/'independent-reaudit/audit.json'
    from backend.pipeline.viewpoint_content_blocked_fallback import verify_saved_full_audit
    audit_proofs = verify_saved_full_audit(audit)
    adjudication = PRIOR/'audit-source-adjudication'
    original = build_packet(audit,store)
    if original != read_artifact(adjudication/'packet.json'):
        raise ValueError('saved full-own-source adjudication input drift')
    raw = read_artifact(adjudication/'raw-gpt.json')
    handoff = read_artifact(adjudication/'gpt-final-handoff.json')
    for artifact, sha in ((raw,RAW_SHA),(handoff,HANDOFF_SHA)):
        if artifact.get('artifact_sha256') != sha or source.sha256_json(
                {k:v for k,v in artifact.items() if k != 'artifact_sha256'}) != sha:
            raise ValueError('saved raw/adjudication SHA drift')
    # Authenticate the actual model/config/request/encoding without a new call.
    adapter = source_review_adapter(subscription_client('codex','gpt-6.1-sol','high'),
                                    adjudication/'raw-gpt.json')
    def forbidden(_):
        raise ValueError('saved source arbitration replay forbidden')
    adapter.generate = forbidden
    response,calls,_ = call_model(adapter,original,adjudication/'raw-gpt.json')
    findings = validate_findings(response,original)
    if calls or len(handoff['actions']) != 1 or len(original['items']) != 1:
        raise ValueError('saved adjudication must cover the sole dispute without replay')
    action = handoff['actions'][0]
    if (action['gpt'] != findings[action['item_id']]
            or action['verdict'] != 'audit_objection_upheld'
            or handoff['packet_sha256'] != original['artifact_sha256']):
        raise ValueError('saved upheld source finding binding drift')
    item = original['items'][0]
    old = states['viewpoint_claim_links',LINK]
    claim = states['claims',CLAIM]
    if item['connections'] != [old] or item['claim'] != claim:
        raise ValueError('saved target/current Claim or connection drift')
    view = states['canonical_viewpoints',CV]
    packet = {'schema_version':VERSION,'targets':[{
        'claim':claim,'before_link':old,'after_link':derive(old,claim),
        'viewpoint':view,'viewpoint_revision':states['viewpoint_revisions',view['current_revision_id']],
        'evidence':item['evidence'],'sources':item['sources'],
        'adjudication':action['gpt'],
    }], 'owner_authorization':{
        'authorization_quote':'是的，这个是支持关系',
        'scope':'Only this connection type and complete two-subject locator',
        'human_content_approval':False,'Opus_calls_allowed':1,'GPT_review_calls_allowed':0,
        'Claim_Evidence_source_CVP_text_writes':0,'old_401_operations_replayed':False,
    }, 'prior_source_application':epoch.binding(),
        'current_Registry_fingerprint_sha256':verified['post_fingerprint_sha256'],
        'prior_upheld_handoff':source.binding(adjudication/'gpt-final-handoff.json'),
        'prior_raw':source.binding(adjudication/'raw-gpt.json'),
        'audit_proof_bindings':audit_proofs,
        'unchanged_context_links':[p for (c,_),p in states.items()
            if c == 'viewpoint_claim_links' and p['viewpoint_id'] == CV
            and p['viewpoint_claim_link_id'] != LINK],
        'proposed_identity_provenance':{
            'status':'pending_this_single_Opus_review','fresh_candidate_and_decision_required':True,
            'inherited_old_decision_is_history_not_new_approval':True,
        },
    }
    validate_target(packet)
    return packet


def run():
    from backend.pipeline.cvp_retired_runtime import reject_legacy_execution
    reject_legacy_execution()
    if ROOT.exists() or (source.APPLICATION_ROOT/'owner-cl005-supports-start.json').exists():
        raise ValueError('review already reserved or attempted; inspect receipts, no retry')
    packet = prepare(source.PostgresKnowledgeStore())
    from backend.pipeline.viewpoint_semantic_contract import semantic_prompt
    prompt = semantic_prompt('''独立终局审核一个负责人授权的连接修订。仅将 equivalent_component 改为 supports，定位恢复完整两主体 Claim 原句。
完整自身原文、实际 CVP、当前 Claim、证据、原异议及原仲裁均提供。原仲裁不是本次批准，owner 意见不是内容批准。
说明该 Claim 哪句以什么论证作用支持 CVP 哪一部分。支持不要求单条复述或证明整个观点，也不能仅相关或相容。
完整两主体定位是论据范围；不得把男子判断计为妇人的第二判断，或重新冒充等价成员。
只允许这一 link_type/locator 修订及写入时衍生的 revision、fresh decision_id；Claim/Evidence/来源/CVP文字、端点、其它连接与依赖不变。
packet 明示 fresh pending identity provenance，旧 decision 仅历史，不是此次审核。通过后只建精确 fresh Opus candidate/decision，不伪造双审或人工批准。
每个指定 Claim 恰好一个 verdict。全部符合才 approved=true 且 problems=[]；拒绝给实质理由，不为续跑放行。不评价神学对错。''')
    adapter = link_review_adapter(subscription_client('claude','claude-opus-5-5','high'),prompt)
    size = adapter.request_bytes(packet)
    if size > 500000:
        raise ValueError('request ceiling exceeded before reservation/model')
    write_immutable(ROOT/'packet.json',packet)
    reservation = {'policy':VERSION,'output_root':str(ROOT),'authorization':packet['owner_authorization'],
        'packet_sha256':source.sha256_json(packet),'prompt_sha256':adapter.prompt_sha256,
        'config_sha256':adapter.generation_config_sha256,'model':adapter.model_id,'backend':adapter.backend,
        'request_bytes':size,'Registry_writes':0,'GPT_new_calls':0,'automatic_retry':False}
    write_immutable(source.APPLICATION_ROOT/'owner-cl005-supports-start.json',reservation)
    write_immutable(ROOT/'review-reservation.json',reservation)
    write_immutable(ROOT/'progress.json',{'status':'Opus_reviewing','Registry_writes':0,'production_resumed':False})
    try:
        response,calls,_ = review_call(adapter,packet,ROOT,'opus')
        verdicts = Review.model_validate(response).verdicts
        if (len(verdicts) != 1 or verdicts[0].claim_id != CLAIM
                or not verdicts[0].approved or verdicts[0].problems):
            raise ValueError('Opus rejected/unresolved exact supports postimage; no automatic correction loop')
        write_immutable(ROOT/'review-report.json',{'status':'Opus_approved_not_applied','review':response,
            'packet_sha256':source.sha256_json(packet),'calls_executed':calls,
            'raw_binding':source.binding(ROOT/'raw-opus.json'),'Registry_writes':0,'production_resumed':False})
    except Exception as exc:
        write_immutable(ROOT/'failure.json',{'error':str(exc),'Registry_writes':0,
            'automatic_retry':False,'production_resumed':False})
        raise


if __name__ == '__main__':
    load_dotenv(source.PROJECT/'.env')
    with (source.BASE/'immediate-recovery-20261002-v1/recovery.lock').open('r+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        run()
