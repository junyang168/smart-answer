"""Carry validated review decisions forward without replaying source packets."""
from copy import deepcopy
from pathlib import Path

from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable

CONTEXT = 'route_review_continuity'
VERSION = 'wang_route_review_continuity_v1'
OBSERVATION_IDENTITIES = 'observation_record_identities'


def with_observation_identities(packet):
    """Expose compiled IDs so draft labels cannot be mistaken for SQL keys."""
    proposal=packet.get('route_proposal') or packet.get('route_proposal_context',{})
    evidence=packet.get('route_evidence_context') or packet.get('route_evidence_packet')
    if not evidence or 'observation_supports' not in proposal:
        return packet
    from backend.api.canonical_repository.viewpoint_observation_support import (
        ProposedObservationSupport, observation_payload, observation_id)
    rows=[{'local_observation_key':o['local_observation_key'],
        'compiled_observation_support_id':observation_id(observation_payload(
            ProposedObservationSupport.model_validate(o),evidence))} for o in proposal['observation_supports']]
    return packet | {OBSERVATION_IDENTITIES:{'records':rows,
        'rule':'local_observation_key只是提案标签，不是写入ID。实际ID由完整内容生成。修改内容会产生新记录，旧ID须退役；即使local key恰好等于旧ID，也不表示新旧数据库记录相同。请按本映射判断是否保留／退役冲突，不得要求以旧ID原地改写不可变观察。'}}

# One shared rubric for proposal, correction and independent review. Historical
# requests retain their actual input; this context applies only to new calls.
RULES = [
    '观察记录教授实际指出的经文用词、形式、对照或范围及其用途；不因缺少独立推理桥梁而删除真实观察。'
    '纯结论复述不自动成为观察。观察本身成立但 supports 夸大时，保留真实观察、修正作用和范围；'
    '若材料实际支持另一命题，不能仅因段落邻近就改称它支持本 CVP。',
    '推理桥梁须有来源实际连接证据与本结论的解释；只引用经文或重复结论不足。'
    '一个片段若实际同时包含观察和推理，可支持不同步骤；不能仅因重复使用同一片段就判缺桥梁，'
    '也不能靠给引文换步骤标签制造推理。区分原文中的观察、教授的解释与编辑补出的因果方向。',
    '本历史是历轮审核及提案员处理记录，不是内容批准，也不是新增来源。仍独立审核完整当前集合。'
    '若推翻此前通过项、改变此前验收条件，或要求补回按旧意见删除的内容，须在现有 reason/correction '
    '说明对应旧轮次、旧判断为何不适用以及当前原文或绑定依据；区分新修改引入、此前漏审和旧判断更正。'
    '不得只为与历史一致而保留错误，也不得仅因后轮意见更新就不作解释地来回删改。',
    '提案员须对照历轮异议及处理记录修改。旧要求与新要求冲突时，在 finding_dispositions.reason '
    '说明采用何种判断及来源依据；不能只机械执行最新标签。审核员应在本轮完整报告全部可识别问题。'
    '不抄写整段原文或旧提案，引用现有精确证据 ID。',
]


def continuity_packet(packet, history, *, packet_path: Path, response_path: Path):
    """Bind new requests, but reconstruct already-dispatched legacy calls exactly."""
    saved = None
    if packet_path.exists():
        saved = read_artifact(packet_path)
        if CONTEXT not in saved:
            return packet
    elif any(path.exists() for path in (
        response_path,
        response_path.with_name(response_path.stem + '.call-intent.json'),
        response_path.with_name(response_path.stem + '.request-encoding.json'),
    )):
        return packet
    if saved is None or OBSERVATION_IDENTITIES in saved:
        packet=with_observation_identities(packet)
    from backend.pipeline.viewpoint_route_correction_changes import with_creation_shapes, CREATION_SHAPES
    if saved is None or CREATION_SHAPES in saved:
        packet=with_creation_shapes(packet)
    enriched = packet | {CONTEXT: {
        'schema_version': VERSION,
        'shared_rules': list(RULES),
        'rounds': deepcopy(history),
    }}
    if saved is not None:
        if saved != enriched:
            raise ValueError('saved Route review continuity differs from validated history')
    else:
        write_immutable(packet_path, enriched)
    return enriched


def review_history_entry(round_number, proposal_sha, review_sha, review):
    """Keep pass identities compact; retain exact objections, not source copies."""
    decisions = []
    for item in review['change_reviews']:
        row = {key: item[key] for key in ('target_kind', 'target_key', 'decision')}
        if item['decision'] not in ('pass', 'confirmed_unattestable'):
            row.update({key: deepcopy(item[key]) for key in ('finding_codes', 'reason', 'correction')})
        decisions.append(row)
    return {
        'round': round_number,
        'route_proposal_sha256': proposal_sha,
        'route_review_sha256': review_sha,
        'change_reviews': decisions,
        'finding_dispositions': [],
    }
