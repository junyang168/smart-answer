# 王守仁教授释经与思想知识平台文档

本目录记录“王守仁教授释经与专题讲论文库”作为独立知识平台的使命、产品设计、技术架构、语料普查方法和试验性出版蓝图。

它与 `notes-to-sermon-agent` 的关系是：后者负责把一份 notes 或 transcript 生成、审核并发布为 manuscript；本项目负责跨越全部讲道与笔记，保存可审核的主张、论证关系和精确来源，再由同一知识基础驱动释经、主题专论、智能问答、搜索、比较和学习工具。编辑流程按具体交付物建立最小可发布证据网络，不要求先审完全部候选语料才交付局部成果。

## 设计治理顺序

本项目必须遵循：**Project Mission → 共享知识模型 → 205 篇全库普查、跨讲综合与结构级人工审核 → 多 use-case 验证 → 正式产品与公开 UI**。use case 验证发现的问题必须回写上游模型或候选基线，不能只在某个产品中修补。`/admin/thought-review` 等页面是可以提前建设的内部审核基础设施，不代表已经跳到正式产品阶段。详细规则见总体设计的“不可倒置的上下游治理顺序”。

## 核心文档

1. [Project Mission Statement](./project_mission_statement.md)
2. [知识平台总体设计](./knowledge_platform_design.md)
3. [文库 Functional Specification](./exegesis_topic_repository_functional_spec.md)
4. [文库 Technical Specification](./exegesis_topic_repository_tech_spec.md)
5. [知识驱动的 Search 与 QA Specification](./sermon_search_functional_spec.md)
6. [思想与篇章审核工作台使用说明](./reviewer_ui_guide.md)
7. [共享知识模型 v1](./shared_knowledge_model_v1.md)
8. [王守仁教授学术思想整理：核心 Use Case](./scholarly_thought_reconstruction_use_case.md)
9. [独立 AI 复审、双模型仲裁与人工分歧处理 v1](./independent_ai_review_v1.md)
10. [第三、第四讲多用途设计验证规范 v1](./two_lecture_multi_use_validation_spec_v1.md)
11. [双轴纵向验证 v1](./dual_axis_vertical_validation_v1.md)
12. [逐句详细知识整理与双模型复审流程 v1](./detailed_knowledge_extraction_workflow_v1.md)
13. [篇章编排双模型审核 v1](./composition_ai_review_v1.md)
14. [独立问答产品验证 v1](./qa_product_validation_v1.md)
15. [问答答案双模型诊断与上游修复协议 v1](./qa_answer_diagnostic_protocol_v1.md)
16. [跨讲道主张比较与双模型归并流程 v1](./cross_sermon_relation_workflow_v1.md)
17. [PostgreSQL 共享知识主库 v1](./postgresql_authoring_store_v1.md)

共享知识模型已经开始正式落地：类型定义、关系验证、幂等迁移和版本保护位于 `backend/api/canonical_repository/knowledge_models.py`、`knowledge_importer.py` 与 `store.py`。`output/claim-layer/shared_knowledge_pilot_v1.json` 仍是候选交换包，不等于 canonical repository，也不会直接进入公开文库。

## 当前实施进度（2026-08-11）

- 205 篇可用讲道已完成第一遍全库普查和 17 个机器候选归组的结构审核；当前上游结构为可修订的 candidate baseline v3。
- 第三讲、第四讲和 `011WSR01` 已进入逐句详细知识试验，共享包现用于太17释经、“那人子”专题、太26释经和问答等纵向验证。
- 太17释经轴与“那人子”专题轴分别保存篇章编排决定，不再把两种产品混成一份计划。
- 问答已具备完整／部分／未回答状态、来源展开、双模型答案诊断和上游错误分层。第一轮 7 个案例中，3 个直接通过；5 项模型意见形成 2 项修复共识、1 项撤回和 2 项单项人工分歧。
- `/admin/thought-review` 已把机器审计层与同工决策层分开：默认使用普通语言说明问题和动作，模型原文、内部 ID 与字段只在技术说明中展开。
- 已建立中立 `ResearchBatch` schema 与可恢复 runner。首个“约与律法”验证批次包含五篇讲道，但批次只表示一起处理，不预设它们属于同一专题；系统会先逐篇独立整理和双模型复审，再产生未分类的合并候选包。
- 五篇中立研究包的跨讲比较已经真实跑通：88 条主张产生 52 个关系候选，经 Claude 分批复核、OpenAI 仲裁与 Claude 再审后，得到 47 条正向共识关系、4 条防误配的 `unrelated` 记录、1 条删除、16 条明确未归组主张，持续分歧与人工任务均为 0。该结果是关系图，不是自动生成的专题目录。
- 上述共识关系已经继续投影为可审核的产品候选：OpenAI 建立候选编排，Claude 逐项独立复核，修改意见再由 OpenAI 仲裁。最终形成 9 个释经候选与 7 个专题候选；15 项直接取得共识，1 项由专题轴改归释经轴，没有持续分歧。候选已作为 16 个 `ProductPlan`、123 条 `KnowledgeRoute` 及其编排决定写入 PostgreSQL，管理员候选工作台现显示释经 11 项、专题 12 项（包含先前已有计划）。
- PostgreSQL authoring store 已接入现有 `/admin/thought-review` 审核工作台。管理员读取与审核写入以数据库为权威，页面显示当前数据源，并可一键重建 approved-only Active Snapshot；旧 `review_state.json` 只在迁移期间兼容同步。
- 首个 Active Snapshot 已从正式本机主库生成，包含 6 条人工批准主张与 17 项来源版本已绑定的合格证据。编译失败不会替换上一个 active build；公开页面尚未切换，待回滚和部署流程稳定后再迁移。
- 全站讲道目录已建立可重跑的 read model，并明确拆分历史系列、圣经目录位置与内容组织分类。讲道中心默认进入圣经目录，以新约在前、旧约在后的正典顺序显示折叠书卷卡片；展开后按章列出每一篇讲道，并保留原始系列的前后讲导航。手机端隐藏桌面筛选侧栏。

上述均属于内部设计验证和候选资料，不表示相关主张、篇章计划或产品已经取得人工出版批准。

## 语料普查与试验

- [第一遍普查格式 v1](./corpus_survey_format_v1.md)
- [代表样本普查 v1](./corpus_survey_sample_v1.md)
- [经文角色与释经覆盖格式 v2：15 篇校准报告](./scripture_roles_v2_pilot_validation.md)
- [全语料第一遍普查与设计审查 v1](./full_corpus_survey_findings_v1.md)
- [講道目錄分類與 ingestion 邊界](./sermon_catalog_ingestion.md)
- [205 篇讲道普查后的总体设计验证 v1](./full_corpus_205_design_validation_v1.md)
- [205 篇讲道：17 个候选归组结构审核 v1](./candidate_group_review_205_v1.md)
- [王守仁教授释经与思想候选基线 v3（当前）](./candidate_baseline_v3.md)
- [王守仁教授释经与思想候选基线 v2（历史）](./candidate_baseline_v2.md)
- [马太福音第 17 章释经篇章蓝图](./matthew_17_exposition_blueprint.md)

## 文档边界

- 本目录定义跨讲道知识平台、论证层、双轴文库及其产品投影。
- `../notes-to-sermon-agent/` 定义单个 Project 的 notes/transcript → manuscript 生产与审核流程。
- 两者可以共享 Evidence Inventory、canonical unit、citation 和索引能力，但不得把单篇 manuscript 当作完整思想知识库。
