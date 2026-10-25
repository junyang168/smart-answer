# 来源版本迁移提议

只读取 packet 中的完整新旧自身原文、受影响记录、只读上下文与负责人授权。
不评价教授神学对错，不从别篇补自身结论，不使用工具或修改文件。

一次构建完整一致的拟后像，不逐条写库。inventory 的每个 affected_key 恰好一个 disposition：
retain 必须逐字段保留；update 必须有确切 permission 并返回完整记录；历史/产品记录只作 impact_only；
缺失来源、未知 consumer、需扩大范围或真实未决，明确 unresolved，不为通过编造依据。
新增、拆分、未覆盖判断放 unadmitted_candidates，不添加到冻结的 Claim/group 成员。
每个独立候选给 candidate_id、statement、source_id、sentence_ids，以及 citations[{paragraph_key,verbatim_excerpt}]；
必须逐字绑定此次许可段落，相关句子处置为 unadmitted_candidate，不得用一个无关候选覆盖全部遗漏。

对校对段落逐句核对遗漏、说话者、否定、假设、必要/充分条件及经文范围。原引文、Evidence、Claim anchors、
问题答案绑定、关系 reason、反向索引、CVP连接一起检查。重复或重叠引文不是独立证据；保存 basis_group_id
和恰好一个代表：anchor.evidence_independence={basis_group_id, count_once_per_basis_group:true, is_basis_representative}。
link evidence_bindings 的严格结构不支持分组字段，重叠项仅保留一个完整代表，其他 fragment/Claim anchor/历史版本保留。
来源的文件、正文及段落身份不可混淆；标题不进入 spoken_body S 编号。

来源修订不必改 CVP 正文。关系变成支持须说明具体论证作用；仅在现有观点意思确实改变且明确获准时，
创建新 viewpoint_revision，revision/revision_number=1，supersedes_revision_id 指向旧修订，旧记录保留。
create permission 有 parent_id 时仅可为该现有 SourceDocument 创建 fragment，或为该现有 CVP 创建一个新 CVR；
object_id 是分配槽位，实际新对象仍须唯一 ID，不授权新增 Claim/CVP 身份。
检查目标 CVP 所有其他成员及限定，不为单一 Claim 改写观点或自动合并/拆分/退役。

更新记录 revision 为旧 revision+1；新对象为1。无变化记录用 retain，不虚增版本。
所有拟后像 review_status=candidate；这是暂存状态，不是旧批准。保留历史审核记录，但不得用其批准新版本。
不要生成新 candidate/decision 或假的审核 SHA；程序在实际 Opus 批准后生成准确 provenance，重新绑定最终 Claim SHA。
Claim locator 现在须绑定提议的完整 Claim SHA及逐字 span；不能删除重要主体或只取有利半句。
sentence_inventory 中每句恰好一个 sentence_dispositions：represented 给实际 record_keys；unadmitted_candidate 保留独立候选；
no_registry_assertion 说明实际背景/问题等处置；未决明确 unresolved。此处置经 Opus 复核，不冒充既有人工 ExclusionRecord。
完整返回 records、dispositions、unadmitted_candidates、sentence_dispositions；只能按 permission.fields 修改，不暗改原文文件。
