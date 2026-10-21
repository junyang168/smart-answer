"""Same-batch new-candidate integration; no committed identity or source edits.

Legacy prompts/answers are never upgraded in place. New groups use a separately
identified schema, complete existing paragraph context, and one shared correction
budget. Every effective proposal still faces the full independent review.
"""
from copy import deepcopy
from contextvars import ContextVar
from typing import Literal
from pydantic import Field
from backend.api.canonical_repository.viewpoint_batch_resolution import (
    StrictBatchModel, NewViewpointCandidate, ConsolidationVerdict,
    UnresolvedConsolidationItem, IdentityConsolidationResponse,
    CanonicalViewpointProposalResponse, validate_consolidation,
)

VERSION = 'wang_cvp_batch_identity_integration_v1'
ENABLED = ContextVar('cvp_batch_identity_integration', default=False)


class CandidateGroup(StrictBatchModel):
    member_local_keys: list[str] = Field(min_length=2)
    merged_candidate: NewViewpointCandidate
    reason: str = Field(min_length=1)


class IntegrationResponse(StrictBatchModel):
    schema_version: Literal['wang_cvp_batch_identity_integration_v1'] = VERSION
    same_batch_groups: list[CandidateGroup] = Field(default_factory=list)
    verdicts: list[ConsolidationVerdict] = Field(default_factory=list)
    unresolved_items: list[UnresolvedConsolidationItem] = Field(default_factory=list)


PROMPT = '''
## 同批新候选整合与 Registry 身份
返回 wang_cvp_batch_identity_integration_v1，不返回旧 consolidation schema。
目的先是整合不同 Claim 中教授同一个观点，再匹配 Registry。不同讲道、
不同措辞、同一判断的结论/论据侧重，不要求是同一次录音，也不自动算不同观点。
先阅读 attested_by 的 source_context 完整正文段落，解释指代、省略、限定；
它不是新 Evidence，AI 小标题不得作证据。不得用别条 Claim 的原文补本条缺失论据。
对同身份的新候选，用 same_batch_groups 列出所有 member_local_keys，
merged_candidate.local_key 必须保留其中一个原 key，返回完整 NewViewpointCandidate。
合并命题是编辑归一化，不冒充教授逐字话，保留全部承重限定和各 Claim 自身证据。
只合并本批尚未提交候选；不合并、退役或改换任何已提交 CVP 身份。
合并后只对保留 key 给 Registry verdict；未合并的每个 key 给一个 verdict 或 unresolved。
同组删去 key 不另给 verdict/unresolved。同组不得重叠，不得跨组引用。
两个候选同指一个既有 CVR 时，先明确其同批身份和共同措辞，不凭程序选一个答案。
真值条件、否定、模态、对象、因果或重要范围真冲突，保留具体 unresolved，不强合。
不以「可能同录音/转录不同」作为普通跨讲整合的前置要求；只有正文无法解释的
真实分歧才报告原话与误读后果。所有未决保存；第一次未决交 GPT 唯一纠正，
再由完整 Opus 审核，仍不解决即停止，不循环到 pass。Claim/source/证据/spans 不改。
'''


def adapter(base):
    from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
    if not isinstance(base, StructuredJsonReviewerAdapter):
        raise ValueError('identity integration requires an explicit structured adapter')
    # Replace only the obsolete role instructions, retain the shared semantics.
    marker = '## 本阶段任务与输出约束'
    shared = base._base_prompt.split(marker, 1)[0]
    result=StructuredJsonReviewerAdapter(client=base._client,
        prompt=shared + marker + '\n' + PROMPT,
        response_model=IntegrationResponse, schema_name=VERSION,
        compact_payload=True, shared_text_payload=True, shared_record_payload=True)
    if hasattr(base,'max_request_bytes'):result.max_request_bytes=base.max_request_bytes
    return result


def parse_and_apply(raw, proposal, registry_revision_ids):
    response = IntegrationResponse.model_validate(raw)
    original = proposal.model_dump(mode='json')
    keys = {c['local_key'] for c in original['new_viewpoint_candidates']}
    consumed = set(); removed = set(); payload = deepcopy(original)
    from backend.pipeline.cvp_owner_candidate_identity import template
    for group in response.same_batch_groups:
        members = group.member_local_keys; keep = group.merged_candidate.local_key
        if (len(members) != len(set(members)) or not set(members) <= keys
                or keep not in members or consumed.intersection(members)):
            raise ValueError('same-batch group unknown, overlapping, duplicate or foreign retained identity')
        consumed.update(members); removed.update(set(members)-{keep})
        payload = template(payload, members, keep)
        for i,c in enumerate(payload['new_viewpoint_candidates']):
            if c['local_key']==keep:
                payload['new_viewpoint_candidates'][i]=group.merged_candidate.model_dump(mode='json')
    dispositions=[v.local_key for v in [*response.verdicts,*response.unresolved_items]]
    if len(dispositions)!=len(set(dispositions)) or set(dispositions)!=keys-removed:
        raise ValueError('integrated candidate dispositions must cover surviving keys exactly once')
    if any(v.verdict=='matches_but_wording_too_narrow' for v in response.verdicts):
        raise ValueError('committed CVP wording revision requires separate CVP management permission')
    effective=CanonicalViewpointProposalResponse.model_validate(payload)
    if not response.unresolved_items:
        validate_consolidation(consolidation=IdentityConsolidationResponse(verdicts=response.verdicts),
            proposal=effective,registry_revision_ids=registry_revision_ids)
    return response,effective
