"""Distinguish an invalid current postimage from optional upstream enrichment."""
from copy import deepcopy

MARKER = 'route_review_scope_contract'
CONTRACT = {
    'schema_version': 'wang_route_review_scope_v1',
    'rule': ('cvp_re_review_exceptions 用于具体影响当前 CVP 身份、命题范围、连接或待写论证有效性的问题。'
        '支持来源各步骤有同篇证据，不等于现有 Claim 与 CVP 必须等价；'
        '若该 Claim 的命题更宽，已批准的支持连接及 partial 实例可以保持有效。'
        '仅建议另提取更窄 Claim、增加等价成员或补充将来的覆盖时，在 review.reason 中完整记录建议及依据，'
        '不要仅为这项完善建议填阻塞异常，也不要自动提升连接或把实例改为 full。'
        '若现有连接确实错误、终端无效或缺失使当前实例不能成立，仍须报具体异常或 correct/defer/reject。'
        '区分“当前内容错误”和“另有可增加的内容”，不能为写入而降低标准。'),
}


def with_review_scope(packet):
    return packet | {MARKER: deepcopy(CONTRACT)}


def validate_scope_continuation(proposal, review):
    """Only an already-passing partial instance can enter this clarification."""
    decisions={(r['target_kind'],r['target_key']):r['decision'] for r in review['change_reviews']}
    partial={a['local_attestation_key'] for a in proposal['source_route_attestations']
             if a['completeness']=='partial'}
    exceptions=review['cvp_re_review_exceptions']
    if (not exceptions or review.get('cross_source_composition_found')
            or any(value not in {'pass','confirmed_unattestable'} for value in decisions.values())
            or any(e['triggering_target_kind']!='attestation'
                   or e['triggering_target_key'] not in partial
                   or decisions.get(('attestation',e['triggering_target_key']))!='pass'
                   for e in exceptions)):
        raise ValueError('review scope clarification requires passing partial-instance exceptions')
