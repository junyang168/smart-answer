本次是常規 CVP management：新讲道丰富同一观点的范围／依据，不是 source 校对、Claim 修复或特例放行。
完整读取所有 current member/support Claims、自身完整正文、现有 CVR 的 core/signature/scope、相关关系及具体异议。
不评价神学是否正确，只记录教授本意。经文出现处／证据覆盖不等于命题的适用范围，不能把覆盖索引当成限制。
但不能为了吸收新材料抹掉教授真实限定。只有核心及真值条件仍相同，才提出最小 scripture_scope 修订。
保持 core_proposition、完整 proposition_signature（含条件）、归属、audience/historical scope 不变。
若一般命题被误绑最早来源的经文列表，可以提议 scripture_scope=[] 表示不按该列表限定；这不是删掉证据或 Claim 经文。
若是具体经文主张，只能扩充有自己绑定依据的经文，不硬塞相容主题；无法维持同一意思则 needs_direction/unresolved。

scope_assessment 包含 action、reason、完整唯一 claim_ids 与每条 Claim 自己正文的 citations
[{claim_id,source_id,paragraph_key,exact_quote}]。结论成员和 supports 理由分别保留，支持不冒充完整等价或独立 Claim。
各篇教授原话的论证理由分别保留，不编造综合论证，不用 AI 标题作证据。
records 使用完整 JSON 字串 payload_json；unadmitted_candidates_json=[]，sentence_dispositions=[]，来源未改变。
一份新 CVR（revision=1/revision_number=1、supersedes_revision_id=当前旧CVR），旧CVR完全保留；更新现有head的current_revision_id。
所有当前 active links 只更新validated_against_viewpoint_revision_id与stored revision+1，原Claim版本、span、evidence、作用和端点逐字保留。
当前相关 relations 只更新对应validated_against_*_revision_id和stored revision+1，原类型／reason／端点保留，review_provenance=null。
所有待写记录review_status=candidate，新CVR provenance=null、approved_by=null、approved_at=null；不继承旧批准。
程序在实际本轮 Opus 批准后生成 fresh provenance/identity decisions，不由模型伪造。
每个affected consumer和新record恰好一个disposition；未修改记录retain，历史／产品只impact_only，不执行ArgumentRoute或文章。

Opus：独立核查全部当前成员及支持来源，特别判断旧范围究竟是真限定还是误置的覆盖列表。
每个disposition给精确原文理由；若改变核心、丢真实限定、跨篇拼接未讲过的结论或有未解张力，拒绝。
允许唯一 GPT 纠正／Opus终审，仍拒绝就停，不循环审到pass。
通过之后仍需共同SQL短transaction/实际写集和历史验证、回读及全部受影响组独立审计，不把review当完成。
