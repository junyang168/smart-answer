"""Scoped mandatory-CVP coverage instructions for new policy identities only."""

VERSION = "wang_cvp_required_coverage_v1"
PROMPT = """## 证据充分的 Claim 不得遗漏 CVP

逐条检查完整 Claim 及每个实质成分。自身证据充分、归属清楚的可复用判断必须有对应 CVP：优先匹配实际成立的现有观点或建立有具体依据的支持／限定关系；没有合适目标时提出 new_viewpoint。Registry 尚无匹配项不是 deferred 或 no_registry_assertion 的理由。不能为避免漏项强造等价、支持或新命题。
独立审核每个 component 时，显式填写 coverage_requirement、coverage_claim_sha256（packet 中当前 Claim 的 claim_revision_sha256）、coverage_evidence_step_ids（自身实际依据）、coverage_reason 及 cvp_covered。cvp_covered 仅表示 proposal 已有可解析的现有或新 CVP 目标，不代表这条关系语义正确，语义仍独立审核。
证据充分且为实质判断用 cvp_required；仍无目标时必须 decision=correct，finding_codes 含 missing_cvp_coverage，correction 写明应匹配什么实际判断或补建什么来源支持的新 CVP，交回 GPT 正常唯一一轮纠正。不得 pass/defer 这一遗漏。终局审核同样检查，仍遗漏则停止，不追加纠正。
确有自身证据不足、归属未决、来源冲突或真实 human hold，分别用 evidence_insufficient、attribution_unresolved、source_conflict、human_hold 并具体说明；纯背景、例证或连接语而非可复用判断可用 observation_only。不能仅凭“没有现有 CVP”选择这些例外；也不能把 ID 齐全当作证据在语义上充分。保留真实待决，不补教授没有讲过的结论。
本要求不授权修改 Claim／Evidence／原文、不扩大 scope、不新增审核轮次。
"""


def enabled(policy):
    version = policy.get("coverage_contract_version")
    if version is not None and version != VERSION:
        raise ValueError("unknown CVP coverage contract")
    return version == VERSION
