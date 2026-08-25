# 母题—子专题—篇章段落自动发现与双模型复核 v1

> **读者**：Developer
> **类型**：流程
> **状态**：当前
> **与代码对齐**：未核对
> **权威范围**：母题—子专题—篇章段落的自动发现与双模型复核步骤。

## 一、要解决的问题

逐篇抽取主张、建立跨讲关系后，系统仍不能要求同工手工先找出“母题—子专题—篇章段落”。那会把项目最困难、也最需要跨 205 篇扩展的思想综合工作重新推回个人经验。

本流程把这项工作定义为共享知识模型上的独立编辑阶段：

```mermaid
flowchart LR
    C["可追溯的共享主张"] --> R["经复核的跨讲关系图"]
    R --> D["OpenAI 发现母题、子专题与篇章段落"]
    D --> V["机械检查：无重复、无遗漏"]
    V --> A["Claude 按母题独立复核"]
    A -->|同意| P["候选主题结构（尚无 canonical ID）"]
    A -->|建议替换| O["OpenAI 仲裁"]
    O -->|接受| P
    O -->|拒绝| C2["Claude 再审"]
    C2 -->|接受 OpenAI| P
    C2 -->|仍不同意| H["只把这一项交给人工"]
    P --> I["主题身份对账：复用既有主题或建立新主题"]
    I --> W["生成 canonical 写入包"]
```

它不从讲道标题生成目录，也不读取现有手工文章来反推答案。现有文章只在结果生成后作为外部验证材料，检查系统是否发现了相近的主要议题，并发现人工文章遗漏或无溯源之处。

## 二、三个层级

1. **母题（Topic Family）**：跨多个问题、经文或讲道反复出现的高层研究领域，例如“约、恩典与人与神的关系”。
2. **子专题（Subtopic）**：围绕一个明确中心问题、可以形成独立专题文章的论证范围，例如“关系性义、信与恩典”。
3. **篇章段落（Composition Section）**：为回答中心问题而安排的文章次序，例如提出问题、核心主旨、经文证据、推理、限定、应用和附录。

这三层都是**编辑综合候选**，不是新增的教授主张。教授主张仍由 `Claim` 保存，原始讲道与精确引文仍由 `SourceFragment` 和证据步骤保存。

Topic Discovery 接入跨讲观点身份时，必须遵守 [Canonical Viewpoint Registry 与跨讲论证路径设计 v1](../canonical_viewpoint_registry_design_v1.md)：`TopicNode` 继续拥有层级，`CanonicalViewpoint` 不成为另一棵主题树；runner 只消费 scoped、SHA-bound `ViewpointKnowledgeProjection`，并继续以原 Claim graph 验证无遗漏。

## 三、硬性守门规则

- `ResearchBatch` 只是处理范围，不得自动成为母题；批次也不得进入主题的**身份**。
- 自动发现阶段只产生 `TCAND-*` 候选 ID；候选 ID 可以随批次或重新分析而改变，**永远不得作为 canonical topic ID 写入主库**。
- canonical topic ID 是独立、不可变的身份。身份确认时只能做两种选择：复用一个既有 `TopicNode`，或为新主题分配一次不含标题、批次和 claim 集合的 opaque ID。日后增加讲道、修改标题或调整归组，只形成新 revision，不更换 canonical ID。
- 名称改变或重新归组会进入持久化的 `topic_identity_reconciliations` 队列，状态为 `pending_match` 或 `pending_new`，并附共用主张数与 Jaccard 作为参考。**系统不会把它直接写成另一棵 canonical 主题树**；确认前，canonical `topic_nodes / knowledge_routes / product_plans` 都保持为空。
- 只有同层级、同父主题的子专题才可以按名称自动复用；同名但属于不同母题的子专题不得误合并。
- 结构必须优先来自主张及其 `supports / explains / qualifies / refutes / answers` 等关系，不能以讲道标题代替思想分析。
- 每条主张只能有一个主要专题归宿，或者明确进入 `unassigned_claim_ids`；未来可以建立交叉链接，但不能复制主张制造重复知识。
- 替换一个母题时，Claude 必须保存原母题完全相同的 claim ID 集合，防止审核意见偷偷增加或删除教授材料。
- AI 不做神学批评、事实核查或教义裁判；只审核归属、范围、逻辑次序和可追溯性。
- 所有输出保持 `candidate / internal`，双模型共识不等于人工出版批准。
- 来源、prompts、模型、reasoning effort 与 response schemas 都进入生成指纹；旧世代归档，输入相同时可安全复用。

## 四、核心九篇实测（2026-08-12）

输入为核心九篇的已整合共享知识包，包含 158 条主张和 181 条关系。流程没有读取手工文章《血与盟约》的目录。

实跑结果：

| 项目 | 数量 |
|---|---:|
| 自动发现母题 | 6 |
| 自动发现子专题 | 13 |
| 被恰好分配一次的主张 | 158 |
| 未归组主张 | 0 |
| Claude 直接认可的母题 | 5 |
| Claude 建议拆分、OpenAI 接受 | 1 |
| 两轮后仍分歧、转人工 | 0 |

六个候选母题是：

1. 约、恩典与人与神的关系；
2. 摩西律法的完成与基督律法；
3. 基督身份、受难与立约之血；
4. 天国、弥赛亚与门徒品格；
5. 外邦人纳入与教会辨识；
6. 释经方法与研究者本分。

Claude 指出“外邦人纳入与教会辨识”下原来的单一子专题与母题几乎同名，不能呈现材料中的两条不同论证线。OpenAI 接受后拆为“外邦人得救的基础与十字架的和睦”及“地方性禁戒与犹太外邦相处”。这说明审核不是装饰：它确实改变了篇章结构。

`未归组=0` 只说明本批 158 条主张都能找到候选主要归宿，不证明未来批次也必须为零。强迫归组仍被禁止。

## 五、代码与产物

- 数据结构及机械验证：`backend/pipeline/topic_structure_discovery.py`
- 双模型 runner：`backend/pipeline/topic_structure_discovery_runner.py`
- prompts：`backend/pipeline/prompts/topic_structure_*.md`
- tests：`backend/tests/test_topic_structure_discovery.py`
- 核心九篇报告：`$DATA_BASE_DIR/wang-knowledge-platform/staging/claim-layer/research-batches/RB-COVENANT-LAW-CORE-NINE-01/topic-structure/topic-structure-report.md`
- 身份待确认的候选包：同目录 `candidate-package.json`
- 身份确认后才生成的 canonical 包：同目录 `canonical-write-package.json`

运行：

```bash
PYTHONPATH=. .venv/bin/python -m backend.pipeline.topic_structure_discovery_runner \
  --batch-root "$DATA_BASE_DIR/wang-knowledge-platform/staging/claim-layer/research-batches/RB-COVENANT-LAW-CORE-NINE-01"
```

默认只生成候选文件。加 `--apply` 时，系统只把 `topic_identity_reconciliations` 写入待确认队列；若仍有未确认身份，会返回 `identity_review_required`，不会写入 canonical TopicNode。

同工确认后，提供一个 resolution 文件，再次执行：

```bash
PYTHONPATH=. .venv/bin/python -m backend.pipeline.topic_structure_discovery_runner \
  --batch-root "$DATA_BASE_DIR/wang-knowledge-platform/staging/claim-layer/research-batches/RB-COVENANT-LAW-CORE-NINE-01" \
  --apply \
  --identity-resolutions /path/to/topic-identity-resolutions.json
```

resolution 以候选 ID 为键，只允许：

- `{"action": "match_existing", "canonical_topic_id": "..."}`：复用既有主题；
- `{"action": "create_new"}`：分配一次 opaque canonical ID。

只有所有身份都解决后，runner 才生成并写入 `canonical-write-package.json`。因此重复执行和后续批次不会堆出平行主题树。

旧版 `candidate-package.json` 若曾把候选主题直接放进 canonical collections，runner 会在缓存命中时自动归档旧文件，并以已经完成双模型复核的 `reviewed-topic-structure.json` 重建 v3 候选包。这个迁移只改变持久化投影，不重新调用模型，也不改变已经复核的母题、子专题和篇章段落内容。

PostgreSQL 的 compiled snapshot 必须同时输出 `topic_identity_reconciliations`。这样管理员工作台可以看到：候选主题来自哪个批次、它与哪些既有主题重叠、最终选择了复用还是新建；不能只在一次 runner 的临时 JSON 中保留这些决定。

## 六、管理员 UI（2026-08-12）

自动发现结果已经接入思想审核的候选工作台：

`/admin/thought-review/candidates?axis=structure`

页面把这一阶段单独显示为“专题结构”，不与已经形成 `ProductPlan` 的“专题候选”混在一起。审核者可以逐层展开：

1. **候选母题**：显示组织问题、形成理由、子专题数、主张数，以及双模型共识／需要同工判断状态；
2. **候选子专题**：显示中心问题、篇章段落数和所覆盖的主张数；
3. **篇章段落**：显示该段落在文章中的作用、安排目的，以及所依据的共享主张。

当前核心九篇的 UI 投影显示 6 个母题、13 个子专题和 158 条恰好分配一次的主张。页面读取的是可重建的 `reviewed-topic-structure.json`，因此它是**结构发现结果的审核投影**，不是另一份私有知识库，也不表示已经取得人工出版批准。

对应实现：

- 后端投影：`backend/api/thought_review.py::_topic_structure_candidates`
- 管理员页面：`web/src/app/admin/thought-review/candidates/page.tsx`
- 守门测试：`backend/tests/test_thought_review.py::test_candidates_show_discovered_topic_hierarchy_as_separate_stage`
