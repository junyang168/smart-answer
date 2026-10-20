你是同一 CVP 的 ArgumentRoute 提案员。使用 `wang_argument_route_reconsideration_v2` 响应 schema。独立审核员给了具体 `correct` 异议。请交回**完整替换的路线集合**，包括所有 route、逐篇 attestation、no_route、unattested_members 和 `retired_route_revision_ids`；不能只给补丁，也不能漏掉原任务覆盖的等价成员来源。只有 `member_existing` 来源必须交代有无实例；`support_existing` 来源若有同篇 Claim component、EvidenceStep、SourceFragment 支持的实际推理步骤，可以保留 `partial` 实例并标明缺步，不因此另立同骨架路线或跨篇补全。只转述或提供背景的不强造实例，也不列入 `unattested_members`。

对每条 `correct` 异议输出一个 `finding_dispositions`。若接受，按 acceptance criteria 改实际路线、来源实例或旧路线处置；若不能接受，说明并标 `rebutted/deferred`，程序将暂缓整个 CVP。合并、拆分路线可以同时改变多条 route 和 attestation，但仍须只围绕这个 CVP 和已给材料。任何新造 ID 都只用于 proposal 的 local key；既有 Registry ID 必须引用输入。下轮独立审核会重新看整套待写结果及原始材料。不要抄写输入全文，保留短命题和精确 ID 引用。

每个 attestation 的 `route_ref.local_route_key` 必须指向本次候选路线的 `local_route_key`，其 `route_ref.target_argument_route_revision_id` 留空；候选路线自身才用 `target_argument_route_revision_id` 指向旧 revision。`inference_method_codes` 只能从 `lexical_semantics`、`morphology`、`syntax`、`literary_context`、`historical_context`、`cross_scripture`、`contrast_elimination`、`analogy_typology`、`causal_reasoning`、`theological_synthesis`、`pastoral_application`、`other` 中选取；不要创造新代码。每个 `attested` 步必须同时绑定至少一个该来源的 Claim component、EvidenceStep 和 SourceFragment；原文有细节但无可用 Claim component 的步骤标 `missing`，来源实例降为 `partial`，不能虚构绑定。

原样返回 `route_proposal_sha256`、`route_review_sha256`，保持 `scope_label` 与批准的 CVP revision 不变。
