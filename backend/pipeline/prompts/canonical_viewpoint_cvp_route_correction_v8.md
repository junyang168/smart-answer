你是同一 CVP 的 ArgumentRoute 提案员。使用 `wang_argument_route_reconsideration_v3` 响应 schema。独立审核员给了具体 `correct` 异议。请交回**完整替换的路线集合**，包括所有 route、逐篇 attestation、no_route、unattested_members 和 `retired_route_revision_ids`；不能只给补丁，也不能漏掉原任务覆盖的等价成员来源。只有 `member_existing` 来源必须交代有无实例；`support_existing` 来源若有同篇 Claim component、EvidenceStep、SourceFragment 支持的实际推理步骤，可以保留 `partial` 实例并标明缺步，不因此另立同骨架路线或跨篇补全。只转述或提供背景的不强造实例，也不列入 `unattested_members`。

修改任何 `partial` 实例时，也要核对非空的 `terminal_claim_component_key`：它必须属于同篇所列 Claim，并以 active 正向连接指向**本次 CVP revision**。只有旧 revision 连接的限定来源不能建立实例，也不能用空终端占位；仍可保留为输入上下文。

若异议指出路线只是引文或 CVP 结论的复述，而该来源并无独立推理，应撤去这条路线及其实例，改用 `viewpoints_with_no_route`；有旧路线则明确退役，不为保留写入而虚构前提。

对每条 `correct` 异议输出一个 `finding_dispositions`。若接受，按 acceptance criteria 改实际路线、来源实例或旧路线处置；若不能接受，说明并标 `rebutted/deferred`，程序将暂缓整个 CVP。合并、拆分路线可以同时改变多条 route 和 attestation，但仍须只围绕这个 CVP 和已给材料。任何新造 ID 都只用于 proposal 的 local key；既有 Registry ID 必须引用输入。下轮独立审核会重新看整套待写结果及原始材料。不要抄写输入全文，保留短命题和精确 ID 引用。

每个 attestation 的 `route_ref.local_route_key` 必须指向本次候选路线的 `local_route_key`，其 `route_ref.target_argument_route_revision_id` 留空；候选路线自身才用 `target_argument_route_revision_id` 指向旧 revision。`inference_method_codes` 只能从 `lexical_semantics`、`morphology`、`syntax`、`literary_context`、`historical_context`、`cross_scripture`、`contrast_elimination`、`analogy_typology`、`causal_reasoning`、`theological_synthesis`、`pastoral_application`、`other` 中选取；不要创造新代码。每个 `attested` 步必须同时绑定至少一个该来源的 Claim component、EvidenceStep 和 SourceFragment；原文有细节但无可用 Claim component 的步骤标 `missing`，来源实例降为 `partial`，不能虚构绑定。

原样返回 `route_proposal_sha256`、`route_review_sha256`，保持 `scope_label` 与批准的 CVP revision 不变。


本次同时整理 `observation_supports`，它与 ArgumentRoute 分开。教授对经文、原文词形、冠词、语法、译文或经文对比所作的具体观察，以及他用其支持观点的作用，应保存为观察支持项；即使没有独立推理桥梁，也不因没有穷举全部用例而丢掉。不得强造 observation→conclusion 的路线，推理路线仍沿用上面的原判准。`no_route` 只说明没有推导路线，不说明没有观察或依据。观察可以同时成为路线的依据，二者不互斥。

每条观察填写短 `observation_summary`、`supports`（原文中它怎样支持当前 CVP 的哪一部分）、`support_scope_note`（实际支持范围及来源限制），并选择同篇 Claim component/EvidenceStep/SourceFragment 的精确实际配对。文本明确指出定冠词 The 或 a/the 形式对比，可以保存这个语言观察；不要求另引希腊文字母或逐节列举。纯粹重复观点或任何出现的相关单词不能自动成为观察。只记录教授实际使用的材料，不裁定观察正确与否，不扩大它对全称判断的证明力，不评价神学真假。描述是编辑归纳，原话由绑定片段提供。

`context_claim_ids` 是成员证据引用段落中的只读 Claim（以及已有观察必须复核的 Claim）：可提供本篇观察依据，不自动成为 CVP 成员，不修改其上游内容或连接。新观察所属来源须有当前 CVP 的有效正向连接；已有观察涉及的来源也须复核，失去支持的旧观察须明确退役；同一个观察不能跨来源拼接。一个来源的相同观察合并证据绑定，不因同篇多条 Claim 重复新建；不同来源各留自己的记录。

对全部有效正向连接及已有观察涉及的来源逐一交代观察覆盖：有可保存的观察就列 `observation_supports`，没有就在 `sources_without_observation` 写具体原因；不能同时列入两者，也不能遗漏来源。已有 `existing_observation_supports` 须在完整候选中保留，或将其ID明确列入 `retired_observation_support_ids`；不要静默丢掉。字段填实际新业务内容和必要引用，程序生成记录ID、Claim版本、来源版本与SHA，不抄写整段输入。

回复完整 observation_supports、sources_without_observation、retired_observation_support_ids，并逐项回应所有 correct 异议。观察来源覆盖和既有记录处置仍须完整；修正观察不意味着必须新增推理路线。
