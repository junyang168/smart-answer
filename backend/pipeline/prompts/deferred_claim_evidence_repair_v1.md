# 通用 deferred Claim 修订模式

本包不是整篇重抽，也不编辑原文。完整自身原文未变。保存的 source investigation
只是发现问题，不是拟后像批准。旧批准、owner执行许可和诊断均不批准新 Claim 版本。

deferred_repair.kind=evidence_binding：保留 Claim 完整 statement/归属/类型，只把确切缺失
支持收入本 Claim 已够格的 Evidence，添加唯一逐字 fragment 和完整 occurrence anchor，
必要时准确更新该 Evidence 的 statement。原引文、anchors和版本保留。
kind=claim_statement：只修目标既有 Claim 对自身原文的实际误读，包括主体、归属、
强度/限定或类型，不增加/删掉教授判断，不借其他Claim或讲道。无法忠实修复则 unresolved。

所有改动必须在 permissions 内。context_only/withheld/human hold 的 Evidence 不升级，
不换证据身份、不删旧引文、不自动改物理原文、CVP正文、身份、端点、关系或问题答案。
每个 consumer 恰好一个处置；现有 CVP 的全部其他成员及其完整来源必须影响复核。
若修订使保留consumer不安全、需改CVP核心或扩大范围，明确unresolved，不硬通过。

修改 Claim 增加一版，所有活动 CVP 连接同步新 pinned_claim_revision，并独立复核原
predicate/locator/evidence_bindings。evidence_binding 模式原 locator spans 和类型不变。
claim_statement 模式可准确重定位，仅允许有自身具体依据的 equivalent→supports；
不能以supports掩盖无论证作用，不能默默退役。其他成员和当前CVR不变。

新 fragment permission.parent_id 指定本 SourceDocument，object_id 只是分配槽；
fragment生成唯一稳定ID（不复用旧ID），source_id及paragraph_key必须来自自身许可段。
anchor.evidence_id 属于本Claim，highlight.text与Evidence新fragment逐字相同。重叠anchor
设同basis_group_id/count_once_per_basis_group=true，恰好一代表。link绑定不支持分组字段，
只留不重叠的完整代表；旧引用对象仍保留。

哈希及重叠分组不要由语言模型计算。保留旧anchor全部引用/角色字段，程序会在原有
和新增anchor的重叠连通组加明确的一次计数标记，最长引文作唯一代表，不改变证据资格。
新 fragment 的source_sha256/paragraph_text_sha256/
verbatim_excerpt_sha256、link.component_locator.claim_sha256可留空：程序按实际逐字
文字与完整Claim确定性生成，并归一化schema默认值，在Opus看到完整拟后像之前完成。
程序同时把机械逐字已验证的新fragment标为source_version_bound，不改变Evidence资格。
程序不改statement/quote/spans/归属/类型/连接语义。Opus实际审核确定性后像，不审核空哈希。
实际批准后仅生成fresh review provenance及最终Claim SHA，绝不继承旧批准。

Evidence补齐/Claim改正不等于原组CVP完成；仍须单次应用、全库回读、完整受影响组
审计，之后回到现有CVP流程检查剩余成分。最多一轮纠正，终审不通过不得反复重问。
