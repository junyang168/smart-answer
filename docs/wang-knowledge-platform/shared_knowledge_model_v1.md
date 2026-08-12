# 共享知识模型 v1

## 一、目的

本模型保存的是王守仁教授在不同讲道中的问题、观察、主张、证据步骤及其关系。它不是某一篇文章的目录，也不以释经文章、专题文章或问答集中的任何一种为中心。

同一份经过审核的知识，可以服务于：逐段释经、跨讲专题、问答、搜索、智能问答、释经方法研究、思想发展比较、课程及后续事实核查。

## 二、核心对象

| 对象 | 保存什么 | 关键边界 |
|---|---|---|
| `SourceDocument` | 一篇讲道、逐字稿或笔记 | 记录来源身份，不代替精确引文 |
| `SourceFragment` | 可高亮、可定位的原始片段 | 必须外键到 Canonical Citation；分析包里的段落号与 offset 不能独立充当来源权威 |
| `Question` | 听众或教授提出的问题 | 允许已回答、部分回答、未回答 |
| `Observation` | 对经文、原文、背景或文体的观察 | 尚未等同于最终结论 |
| `Claim` | 教授主张什么 | 必须有归属、语料范围、成熟度和证据 |
| `TopicNode` | 全平台共享的主题身份 | Canonical Repository 拥有 ID；搜索、文章和论证层只能引用或保存显式旧 ID 映射 |
| `EvidenceStep` | 教授怎样从问题与证据走到结论 | 除功能外，必须记录说话者、立场、话语角色、锚点质量与支持资格 |
| `KnowledgeRelation` | 两个知识对象怎样相连 | 如 `supports`、`answers`、`qualifies`、`contrasts`、`extends` |
| `PositionNode` | 教授引用、质疑或驳斥的外部立场 | 反方立场必须成为独立节点，不能伪装成教授自己的主张 |
| `ClaimRelation` | 主张与主张（或反方立场）怎样相连 | 如 `supports`、`explains`、`contextualizes`、`corroborates`、`refutes`；跨讲印证保留各自来源，不合并为同一证据 |
| `ClaimRelationConstraint` | 哪些看似相关的主张目前不得连成某种论证边 | 例如两项材料只能作为 `parallel_context`，在补足证据前禁止标成 `supports`；构建时必须执行约束 |
| `KnowledgeRoute` | 每条主张下一步去哪里 | 可进入释经、专题、问答、方法研究或思想发展；不得审核后失去去向 |
| `EditorialSynthesis` | 编辑跨来源归纳出的模式或候选专题 | 必须标为编辑归纳，不能冒充教授原话 |
| `ProductPlan` | 为某一具体交付物作出的取舍与编排 | Carson-style 释经、专题专论或问答集各有自己的计划 |
| `ProductDependency` | 某个具体产品实际采用了哪个 claim revision | 用于反向影响分析，不可只依赖 claim → route 的正向意图 |
| `ImpactEvent` | 主张修订后哪些产品已经失效 | 保存撤回、重建和 cache 失效的待办及处理状态 |
| `AIReviewArtifact` | 第二模型对候选抽取的独立复审、问题与风险分流 | 必须绑定来源抽取世代与 reviewer fingerprint；不是批准记录 |
| `ReviewRecord` | 人工审核结论 | AI 只能提出候选和分流，不能自行批准 |

## 三、三条不可破坏的边界

1. **教授与编辑分开。** 教授的主张、编辑的跨讲归纳、文章的篇章编排必须分别署名。
2. **知识与产品分开。** 文章使用知识库重新写作，而不是机械“投影”；详略、顺序、岔题处理和缺口属于 `ProductPlan`。
3. **局部与全库分开。** 两讲中反复出现的内容只能称“两讲中的跨来源模式”或“全库检索线索”，不能直接称教授完整的专题思想。

## 四、问答材料的处理

问答的对话形式保存在 `Question` 与 `answers` 关系中；教授回答里形成的主张和证据仍进入共享知识。这样既能出版问答集，也能在释经、专题和智能问答中复用同一证据。

## 五、来源资格与归属门槛

每个证据步骤都要经过五项判断：谁在说话、说话者是否认同这句话、这句话在对话中扮演什么角色、能否回到原始录音，以及当前片段是否足以支持所归属的主张。

`support_eligibility` 分成三类：

- `eligible` / `eligible_with_label`：可以进入教授的论证过程；戏剧化代言等材料必须带标签。
- `contextual_only`：听众发言、提问或教授转述的反方立场，只用于说明上下文。
- `withheld_*`：没有原话、没有时间码或引文不足，先保留但不得作为支持证据。

单纯的字面匹配分数不能决定高亮是否合格。完整语义、说话者和立场优先于字符串相似度。

来源完整性由现有 Canonical Citation 统一负责，而不是由论证层另建一套 offset join：

- `SourceDocument.source_sha256` 绑定逐字稿版本；
- source map 保存 `paragraph_text_sha256`；
- `CitationLocator` 保存精确引文、引文 SHA256、段落 key、字符范围和媒体时间；
- `SourceFragment.citation_id` 与 `EvidenceStep.citation_ids` 只引用该 Citation；
- 读取或批准时重新解析 Citation；来源版本变化、段落变化或精确引文消失时标为 stale/unresolved；
- `eligible` / `eligible_with_label` 证据若没有有效 Citation，导入与批准都会失败。

因此字符 offset 只是命中后的显示提示，不是资料完整性的最终依据。精确引文与版本 hash 才是可重新定位、可检测失效的基础。

## 六、频率、重要性与产品相关性

`recurrence` 只表示某项内容在当前材料中出现多少次，不能直接当作思想重要性。系统分别保存：出现频率、对当前产品的相关程度，以及依据主张关系计算的候选中心性；后两者仍须人工审核。

## 七、编辑核查与开放张力

文本异文、明显口误和外部事实核查属于独立的 `EditorialCheck`，不可静默改写教授的说法。尚未解决的经文张力保存为 `Tension`；例如「小信不是数量问题」与芥菜种大小意象之间的关系，必须在出版前说明。

## 八、当前试验包

`output/claim-layer/shared_knowledge_pilot_v1.json` 是可重复迁移的试验结果。随着 011WSR01 逐句整理加入，当前 main build 已不再固定为早期“2 来源／7 条主张关系”的静态数字；实际数量以包内 `summary.counts` 为准。6条专题 route 均同时保留旧分析 target 和正式 `canonical_topic_ids`。它用于验证模型，不是 205 篇全库的最终分类。

### 主张关系的正边与负约束

篇章审核不能只产生“应补一条关系”的自然语言备注。AI 复审与 OpenAI 仲裁一致接受的结构修复保存于 `output/claim-layer/claim_relation_consensus_v1.json`，并在每次重建时合并：

- `corroborates` 表示不同讲道分别形成、彼此印证的主张；两条主张和两组来源都保留。
- `contextualizes` 表示一条主张提供理解另一条主张所需的概念或历史背景，不夸大为演绎支持。
- `ClaimRelationConstraint` 保存明确的非关系判断。例如两条解释虽进入同一段落，却只能并列；若后续抽取器生成被禁止的 `supports` 边，构建必须失败，而不能靠编辑事后发现。

这类 AI 共识结构仍保留 reviewer、仲裁 artifact 与适用语料范围。它可以按既定“双 AI 同意即可自动应用”的规则进入候选知识包，但不因此变成教授原话或神学事实批准。

## 九、证据归属、ID 与审批闸门

同一段原始材料可以同时支持多个主张，因此 `EvidenceStep` 使用 `claim_group_ids` 保存多重归属，不能再假设一个 evidence node 只能属于一个 `topic`。导出器在生成时强制检查：每一条 `Claim` 的 `group_id` 必须至少出现在一个 evidence node 上；任何漏接都会让生成失败，而不是到 UI 才暴露为空白。

原始候选文件中的证据编号是讲次内的局部编号，例如 `E033`；共享知识包明确区分：

- `local_source_evidence_ids`：来源讲次内部编号；
- `canonical_evidence_step_ids`：加入讲次前缀的全局编号，例如 `L3-E033`；
- `evidence_step_ids`：主张在共享包中实际关联的全局证据清单。

审核 API 以合格证据为硬门槛。主张没有 `eligible` 或 `eligible_with_label` 证据时，不但 UI 的“批准”按钮会停用，服务端也会拒绝批准请求；只有一条合格证据时仍可审核，但界面必须显示薄弱证据警告，提醒补强来源或降低篇章权重。

## 十、篇章层级与出版核查

篇章计划通过 `claim_hierarchy` 区分段落主旨、支持论据、神学根据和编排说明，避免把相关主张平铺后写成重复段落。每条主张的 `KnowledgeRoute` 必须在审核界面显示，让同工知道它进入释经、专题、方法研究或思想发展中的哪一条产品路线。

`EditorialCheck` 和 `Tension` 也必须由 API 与审核 UI 呈现。它们不是隐藏在 JSON 里的备注，而是出版前必须看见并处理的编辑约束。

## 十一、正式落地位置与三层边界

截至当前版本，同一个模型有三个不同层次，不能混称：

1. **概念与治理层**：本文件定义对象的意义、边界和审核规则。
2. **候选交换包**：`output/claim-layer/shared_knowledge_pilot_v1.json` 保存第三、第四讲的可重建试验结果，仍是导入来源，不是正式资料库。
3. **Canonical authoring store**：`backend/api/canonical_repository/knowledge_models.py` 定义版本化 Pydantic records；`postgres_store.py` 以 PostgreSQL 保存对象、版本、关系、ChangeSet、审核事件及影响传播。原来的 `knowledge_importer.py` 与 `store.py` 仍作为 JSON 迁移兼容层，不再是 205 篇规模下的最终主库。

正式 store 目前包含：来源文件、来源片段、问题、观察、主张、权威主题节点、证据步骤、证据关系、主张关系、主张关系约束、外部立场、知识去向、编辑综合、篇章计划、篇章决定、编辑核查与开放张力。每条 record 都保留稳定 ID、`schema_version`、`review_status`、`visibility` 与 `revision`；尚在演进中的研究字段会原样保留，避免新一轮普查先于 schema 更新时丢失资料。

导入采取以下保护：

- 全包先验证、后写入；重复 ID、悬空来源、悬空证据或悬空关系会拒绝导入；
- 允许后续小包引用 repository 中已经存在的对象，以支持按交付物逐批审核；
- 重复导入是幂等的；已进入人工审核状态的字段不会被新的 AI candidate 静默覆盖；
- 人工修改采用 revision guard，避免两位同工同时编辑时后写覆盖先写；
- 每次导入保存 package manifest、来源 SHA256、对象数量及对象 ID 清单。

### 主题身份治理

`canonical_repository/knowledge/topic_nodes/` 是主题身份的唯一权威。三类旧对象必须明确区分：

1. `topic_###` 是旧 sermon search 的结果 ID；它代表 passage/concept 搜索卡，不是神学分类 ID。对账时映射到 `CanonicalUnit`，搜索只作为 projection。
2. `CanonicalUnit.topic_assignments.topic_ids` 是指向 `TopicNode` 的外键；候选记录可按已审核 alias 迁移，已发布记录不得静默改写。
3. 论证层的 `TOPIC-*` 是分析期 route target；原值保留供审计，同时写入 `canonical_topic_ids`。

`sermon_search/topic_index.json` 中的 `canonical_ref` 是经文的 OSIS 参照，并不是 canonical topic 外键。旧搜索卡通过 reconciliation report 投影到 CanonicalUnit；专题身份只能引用 `TopicNode.topic_id`。字段名称相似不能作为 join 条件。

系统不根据标题相似度自动宣告两个主题相同。所有跨命名空间连接必须来自显式 alias/identity mapping，并由 `knowledge/reconciliation/topic_identity.json` 报告未知 unit topic、未解析 route 和旧搜索投影。当前马太试验建立 55 个 TopicNode；未知 unit topic与未解析专题 route 均为 0。

这一步只是把共享知识从“一个大型试验 JSON”提升为可审核、可版本化、可逐批迁移的正式对象。公开释经、专题、问答和智能问答仍须读取经过批准并编译的 active build，不能直接公开 authoring store 中的 candidate。PostgreSQL 的详细边界、命令及验收结果见 [PostgreSQL 共享知识主库 v1](./postgresql_authoring_store_v1.md)。

## 十二、抽取世代与反向失效

普查结果的来源 hash 只说明逐字稿没有变化，不足以证明两条 candidate claim 来自同一抽取过程。每次抽取保存两种相关但不同的指纹：

- `generation_fingerprint_sha256`：resolved prompt、model ID、reasoning effort、输出 token 限额、schema version 与 response-schema SHA256；同一次全库运行的所有讲道应相同。
- `fingerprint_sha256`：在 generation identity 上再加入该篇 source SHA256；作为单篇 cache key，并写入其中每条 candidate claim。

cache 只在完整 extraction fingerprint 相同时命中。被新世代取代的 JSON 先复制到 `output/corpus-survey/generations/`，因此可追查旧抽取而不会误作当前结果。旧产物没有 generation fingerprint，下一次运行会自动重抽；跨讲综合会拒绝任何 legacy 输入或多个 generation fingerprint，避免形成混合语料。

`KnowledgeRoute` 表示候选去向，`ProductDependency` 才表示某一产品实际采用的依赖快照。它固定 `consumer_kind + consumer_id + claim_id + pinned_claim_revision`。主张文字、归属、证据、经文、主题或审核有效性发生实质变化时，系统：

1. 反查 route、composition decision/plan、editorial synthesis、question answer、claim relation 和显式 product dependency；
2. 将相关 dependency 标为 `invalidated`；
3. 建立版本化 `ImpactEvent`，不删除旧版本；
4. 阻止带失效 dependency 的 knowledge-managed unit 进入新 public build；
5. 由管理员选择撤回或重建；撤回已发布 unit 与 active build 刷新必须原子执行，失败时恢复原状态。

`ImpactEvent` 是可执行的变更清单，不是日志备注。问答与搜索消费者必须以 dependency revision 或 active build ID 作为 cache namespace；收到事件后先停止提供失效 revision，再重建缓存。尚未登记 `ProductDependency` 的旧产品属于 migration backlog，不能被宣称具有自动撤回能力。

## 十三、独立 AI 复审的边界

第一次抽取与第二次复审必须使用独立 prompt；复审模型直接读取完整逐字稿，不得只依据候选结论。复审 artifact 逐条保存 decision、issues、受影响 anchor、修改建议、置信度、人工审核理由和程序分流结果，并绑定原抽取 fingerprint、review prompt、模型、参数和 schema。

`ai_reviewed` 只表示第二模型未发现抽取问题，不等于教授主张已获人工批准，更不等于神学事实核查通过。说话者、反方归属、听众被当证据、来源不支持、编辑推论、开放张力及高严重度问题必须进入人工队列；其余项目仍按固定比例进行确定性人工抽样。公开产品实际采用的最小知识子图最终仍要写入 `ReviewRecord`。完整流程见 [独立 AI 复审与人工分流 v1](./independent_ai_review_v1.md)。
