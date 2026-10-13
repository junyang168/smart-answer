"""One owner-authorized contextual review, never an automatic semantic retry."""
from copy import deepcopy
from pathlib import Path

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import file_sha256
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable

CLAIM = 'DK-78f4b0b8eb95-P01-CL001'
LINK = 'VCL-47f3564e3ffced0a73e0'
VIEWPOINT = 'CV-3acf0eff7081bef6e16e'
OLD_ROOT_NAME = 'g037-link-review-shared-text-supports-20261005-v1'
PACKET_SHA = 'd6f8b0bea200e6668094a749f04bb4acad914990c2baddc0bd42ff590b95b111'
RAW_SHA = '849f01a241b67c2da59d333015ebbf7cbdd15f5b11b611829b2162a98c10d7b5'
POLICY_ID = 'wang-g037-owner-context-review-v1'
QUOTE = '你還沒有做，你第一次決定的時候你就犯罪了。'
PROMPT = '''独立审核五条精确连接后像。四条为原equivalent改supports；仅owner_context指定的一条重新审核保留原equivalent_component与原locator的后像。
完整自身原文、Claim、目标、旧审计/裁决与旧拒绝均提供。负责人解释不是批准，允许拒绝，不强求同义。
指定Claim“包含意志上决定”须结合自身原文S0003理解：区分觉得漂亮/引诱与实际意志决定；原文说“你還沒有做，你第一次決定的時候你就犯罪了。”
核查这是该Claim语境中的完整含义还是Claim确实未表达的额外结论，并核查原component_locator是否足以承载目标的充分性/时点限定；若不成立拒绝，不能靠上下文硬补Claim。
其余四条只核查真实支持方向，相关不等于支持。不改Claim/Evidence/source/CVP文字、端点、来源、其他成员；本轮既有证据全部保留。
每指定Claim一个verdict；只有精确后像全部成立才approved=true且problems=[]。不评价教授神学对错，不执行操作。'''


def contextual_packet(packet, old_packet, old_raw):
    if sha256_json(packet) != PACKET_SHA or packet != old_packet:
        raise ValueError('context review frozen semantic packet drift')
    body = {k:v for k,v in old_raw.items() if k != 'artifact_sha256'}
    if old_raw.get('artifact_sha256') != RAW_SHA or sha256_json(body) != RAW_SHA:
        raise ValueError('context review old raw SHA drift')
    if (old_raw.get('model_id'),old_raw.get('backend'),old_raw.get('request_payload_sha256')) != ('gpt-6.1-sol','codex_subscription',PACKET_SHA):
        raise ValueError('context review old model/input drift')
    if sha256_json(old_raw['response']) != old_raw['response_sha256']:
        raise ValueError('context review old response drift')
    rows = old_raw['response']['verdicts']
    ids = {t['claim']['claim_id'] for t in packet['targets']}
    if len(ids)!=5 or len(rows)!=5 or {r['claim_id'] for r in rows}!=ids:
        raise ValueError('context review population drift')
    rejected = [r for r in rows if not r['approved'] or r['problems']]
    if len(rejected)!=1 or rejected[0]['claim_id']!=CLAIM:
        raise ValueError('context review outside sole refusal')
    result=deepcopy(packet)
    target=next(t for t in result['targets'] if t['claim']['claim_id']==CLAIM)
    old=target['before_link']
    if old['viewpoint_claim_link_id']!=LINK or old['viewpoint_id']!=VIEWPOINT or old['link_type']!='equivalent_component':
        raise ValueError('context review foreign target')
    own=[s for s in target['sources'] if s['source_id']=='SRC-2017_NYSC_4-78f4b0b8eb95']
    if len(own)!=1 or not any(p['paragraph_key']=='S0003' and QUOTE in p['text'] for p in own[0]['paragraphs']):
        raise ValueError('context review missing own exact context')
    after=deepcopy(old);after['revision']+=1
    target['after_link']=after
    result['owner_context']={'claim_id':CLAIM,'link_id':LINK,'old_raw_sha256':RAW_SHA,
        'old_refusal':rejected[0], 'interpretation':'内心决定犯奸淫，即使未行动也是犯罪；请按Claim自身上下文判断是否同义。',
        'authorization_quote':'同意，继续','human_content_approval':False,'max_fresh_reviews_per_provider':1}
    result['policy']={'policy_id':POLICY_ID,'authorized_by':'owner',
        'automatic_future_context_retry':False,'claim_source_viewpoint_text_writes':0}
    return result


def prepare(packet, states, source_root, base):
    from backend.pipeline.viewpoint_authorized_source_repair_357 import fingerprint
    from backend.pipeline.viewpoint_audit_link_repair import link_review_adapter, PROMPT as OLD_PROMPT
    from backend.pipeline.viewpoint_resolution_runtime import subscription_client
    if source_root.resolve()!=base/OLD_ROOT_NAME:
        raise ValueError('context review requires exact frozen old root')
    if any((source_root/n).exists() for n in ('apply-intent.json','apply-receipt.json','raw-opus.json')):
        raise ValueError('context review old attempt already advanced')
    before=read_artifact(source_root/'before.json')
    if fingerprint(states,before['collections'])!=before['pre_fingerprint_sha256']:
        raise ValueError('context review full Registry drift')
    old_packet=read_artifact(source_root/'packet.json'); raw=read_artifact(source_root/'raw-gpt.json')
    adapter=link_review_adapter(subscription_client('codex','gpt-6.1-sol','high'),OLD_PROMPT)
    proof=read_artifact(source_root/'raw-gpt.request-encoding.json')
    proof_body={k:v for k,v in proof.items() if k!='artifact_sha256'}
    expected={'schema_version':'wang_model_request_encoding_proof_v1',**adapter.request_encoding_proof(old_packet)}
    if proof_body!=expected or proof.get('artifact_sha256')!=sha256_json(expected) or raw.get('request_encoding_proof_sha256')!=sha256_json(adapter.request_encoding_proof(old_packet)):
        raise ValueError('context review old encoding proof drift')
    if raw.get('prompt_sha256')!=adapter.prompt_sha256 or raw.get('generation_config_sha256')!=adapter.generation_config_sha256:
        raise ValueError('context review old review identity drift')
    for t in packet['targets']:
        for s in t['sources']:
            if file_sha256(Path(s['path']))!=s['file_sha256']:
                raise ValueError('context review physical source drift')
    return contextual_packet(packet,old_packet,raw)


def reserve(root, source_root, base):
    reservation=source_root/'owner-context-review-start.json'
    if reservation.exists():
        raise ValueError('owner context review already reserved; no repeat')
    authority={'policy_id':POLICY_ID,'authorization_quote':'同意，继续',
        'old_root':str(source_root),'old_raw_sha256':RAW_SHA,'old_packet_sha256':PACKET_SHA,
        'output_root':str(root),'per_provider_review_budget':1,'human_content_approval':False,
        'Claim_Evidence_source_CVP_text_writes':0,'automatic_future_context_retry':False}
    authority['historical_bindings']=[{'path':str(source_root/n),'file_sha256':file_sha256(source_root/n)}
        for n in ('packet.json','before.json','raw-gpt.json','raw-gpt.request-encoding.json','failure.json')]
    write_immutable(reservation,authority)
    write_immutable(root/'owner-authorization.json',authority)


def validate_postimages(packet):
    context=packet.get('owner_context',{})
    if context.get('claim_id')!=CLAIM or context.get('link_id')!=LINK or context.get('old_raw_sha256')!=RAW_SHA or context.get('human_content_approval') is not False:
        raise ValueError('context package authority drift')
    if len(packet['targets'])!=5:
        raise ValueError('context package population drift')
    for t in packet['targets']:
        expected=deepcopy(t['before_link']);expected['revision']+=1
        if expected['viewpoint_claim_link_id']!=LINK:
            expected.update(link_type='supports',component_locator=None)
        elif expected['claim_id']!=CLAIM or expected['viewpoint_id']!=VIEWPOINT:
            raise ValueError('context package foreign target')
        if t['after_link']!=expected:
            raise ValueError('context package postimage drift')
