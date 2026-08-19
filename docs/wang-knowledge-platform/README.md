# 王守仁教授释经与思想知识平台文档

本目录记录“王守仁教授释经与专题讲论文库”作为独立知识平台的使命、产品设计、技术架构、语料普查方法和试验性出版蓝图。

它与 `notes-to-sermon-agent` 的关系是：后者负责把一份 notes 或 transcript 生成、审核并发布为 manuscript；本项目负责跨越全部讲道与笔记，保存可审核的主张、论证关系和精确来源，再由同一知识基础驱动释经、主题专论、智能问答、搜索、比较、微讲道和学习工具。编辑流程按具体交付物建立最小可发布证据网络，不要求先审完全部候选语料才交付局部成果。

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
18. [微讲道：三至五分钟短篇教导 Use Case](./micro_sermon_product_use_case.md)
19. [母题—子专题—篇章段落自动发现与双模型复核 v1](./topic_structure_discovery_workflow_v1.md)
20. [同工反馈到产品需求与验收标准 v1](./stakeholder_feedback_to_requirements_v1.md)
21. [馬太福音釋經教學文集：成書體例 v2](./matthew_exposition_publication_profile_v1.md)
22. [馬太福音釋經：來源進入論證層與跨來源整合流程 v1](./matthew_source_to_argument_workflow_v1.md)
23. [釋經初稿的程序化審計流程 v1](./editorial_draft_audit_workflow_v1.md)
24. [篇章釋經快速路徑 v1](./fast_passage_editorial_workflow_v1.md)
25. [來源逐句對帳與 claim 層完整性 v1](./source_to_claim_layer_ledger_v1.md)

共享知识模型已经开始正式落地：类型定义、关系验证、幂等迁移和版本保护位于 `backend/api/canonical_repository/knowledge_models.py`、`knowledge_importer.py` 与 `store.py`。`$DATA_BASE_DIR/wang-knowledge-platform/staging/claim-layer/shared_knowledge_pilot_v1.json` 仍是 legacy 候选交换包，不等于 PostgreSQL authoring authority，也不会直接进入公开文库。

## 当前实施进度（2026-08-15）

- 205 篇可用讲道已完成第一遍全库普查和 17 个机器候选归组的结构审核；当前上游结构为可修订的 candidate baseline v3。
- 第三讲、第四讲和 `011WSR01` 已进入逐句详细知识试验，共享包现用于太17释经、“那人子”专题、太26释经和问答等纵向验证。
- 太17释经轴与“那人子”专题轴分别保存篇章编排决定，不再把两种产品混成一份计划。
- 问答已具备完整／部分／未回答状态、来源展开、双模型答案诊断和上游错误分层。第一轮 7 个案例中，3 个直接通过；5 项模型意见形成 2 项修复共识、1 项撤回和 2 项单项人工分歧。
- `/admin/thought-review` 已把机器审计层与同工决策层分开：默认使用普通语言说明问题和动作，模型原文、内部 ID 与字段只在技术说明中展开。
- 已建立中立 `ResearchBatch` schema 与可恢复 runner。首个“约与律法”验证批次包含五篇讲道，但批次只表示一起处理，不预设它们属于同一专题；系统会先逐篇独立整理和双模型复审，再产生未分类的合并候选包。
- 五篇中立研究包的跨讲比较已经真实跑通：88 条主张产生 52 个关系候选，经 Claude 分批复核、OpenAI 仲裁与 Claude 再审后，得到 47 条正向共识关系、4 条防误配的 `unrelated` 记录、1 条删除、16 条明确未归组主张，持续分歧与人工任务均为 0。该结果是关系图，不是自动生成的专题目录。
- 上述共识关系已经继续投影为可审核的产品候选：OpenAI 建立候选编排，Claude 逐项独立复核，修改意见再由 OpenAI 仲裁。最终形成 9 个释经候选与 7 个专题候选；15 项直接取得共识，1 项由专题轴改归释经轴，没有持续分歧。候选已作为 16 个 `ProductPlan`、123 条 `KnowledgeRoute` 及其编排决定写入 PostgreSQL，管理员候选工作台现显示释经 11 项、专题 12 项（包含先前已有计划）。
- PostgreSQL authoring store 已接入现有 `/admin/thought-review` 审核工作台。管理员读取与审核写入以数据库为权威，页面显示当前数据源，并可一键重建 approved-only Active Snapshot；旧 `review_state.json` 只在迁移期间兼容同步。
- 核心九篇的跨讲关系已完成正式整合：67 条双模型共识关系经验证后以独立 ChangeSet 回写 PostgreSQL（59 条新增、8 条更新），1 条持续分歧仅保留在人工队列。整合程序同时产出关系增量、候选合并快照和审计报告；相同输入重跑为 `already_applied`，不会重复写入，也不会自动发布候选知识。
- 核心九篇已继续跑通“母题—子专题—篇章段落”自动发现：系统没有读取手工文章目录，而是从 158 条共享主张和 181 条关系得到 6 个母题、13 个子专题，并将每条主张恰好分配一次。Claude 直接认可 5 个母题，对 1 个母题提出子专题拆分，OpenAI 接受；没有持续分歧需要人工。输出仍是内部编辑候选，不是教授原话或已批准专题。
- 上述结构发现结果已接入 `/admin/thought-review/candidates?axis=structure`：同工可依次展开候选母题、子专题、篇章段落及其共享主张。该页与已经形成编排计划的“专题候选”分开，避免把 AI 发现的层级误认为已批准专题或已出版文章。
- 首个 Active Snapshot 已从正式本机主库生成，包含 6 条人工批准主张与 17 项来源版本已绑定的合格证据。编译失败不会替换上一个 active build；公开页面尚未切换，待回滚和部署流程稳定后再迁移。
- 全站讲道目录已建立可重跑的 read model，并明确拆分历史系列、圣经目录位置与内容组织分类。讲道中心默认进入圣经目录，以新约在前、旧约在后的正典顺序显示折叠书卷卡片；展开后按章列出每一篇讲道，并保留原始系列的前后讲导航。手机端隐藏桌面筛选侧栏。
- 已确认现有 `/resources/micro-sermon` 与 `/admin/micro-sermon` 可作为三至五分钟短篇教导的交付界面；共享主张、论证、来源、篇章计划、双模型复核及失效传播尚未接入。目标流程见微讲道 use case，不能把现有短片目录误当成另一套知识库。
- 教会同工提出的“教授讲了什么、好在哪里”和“有没有人总结因信成义”已转成正式产品需求与验收标准。首轮以五篇罗马书相关讲道建立 `RB-RIGHTEOUSNESS-FAITH-ROMANS-VALIDATION-01`：89 条主张形成 111 条整合关系、4 个释经候选和 5 个专题候选；验证同时抓出“成义”被 AI 静默改成“称义”的来源忠实性问题。该轮证明知识结构可用，但读者可读性仍须由第一篇“因信成义”候选专题及同工任务测试验收。
- 《馬太福音》16:1–12 已完成第一個「篇章編排計劃 → 寫作與獨立重審 → 本地 Program Audit → 人工批准 → repository 發布 → 公開讀取」閉環，並發布為〈看見神蹟，卻仍未明白基督——馬太福音 16:1–12〉。Final review 不再重送 231,233-byte authoring envelope：独立 EditorialReviewPacket 为 14,421 bytes，FinalDeltaReviewPacket 为 12,772 bytes；有效 delta request 用时 60.174 秒，仅重评四个受影响维度，其余六项从 SHA 绑定 baseline 继承，程序重算为 90 分且 hard gates 全部通过。Program Audit 继续在本地读取完整知识快照，检查 6/6 编排决定、14 条共享主张、25 个证据步骤、27 个来源片段与 18/18 个正文 provenance 段落；结果为 `pass_with_warnings`、0 错误，仅保留「小信」及「神蹟、聖經與信仰判斷」两项专题链接待办。公开 slug 为 `matthew-16-1-12`，四个读者段落共呈现六个原声播放器。
- 《馬太福音》16:13–20 已完成同一閉環並把跨來源張力正式帶入編排層。兩份筆記講稿與六篇講道形成49條限定主張、90個證據步驟和30條雙模型共識關係；關係增量投影後，篇章二審為 `argument_layer_status=solid`，9項決定中8項直接通過、1項自動澄清來源邊界，0人工分歧。初稿審計覆蓋9/9編排決定、48條所用主張、88個證據步驟、89個有效來源片段及35/35個正文段落，結果為 `pass_with_warnings`、0錯誤；兩項警告只涉及待建立的「天國鑰匙與教會權柄」及「彌賽亞秘密」專題。正文不靜默調和「磐石」與「捆綁／釋放」的兩項來源張力，也不替教授補寫太16:17及16:18b。原聲按同一 CompositionPlan 投影到7/9個段落，共19個時間範圍；無時間碼的審閱稿只在逐字引文能於發布稿取得唯一精確匹配時補回時間，`S 220206`因無發布稿不生成虛假播放器。
- 《馬太福音》16:21–23 已以 Author Agent workflow 重新生成，并在两轮修订后由程序重算为90分，0 hard gates、0 hard failures。该次诊断曾使用独立 Score-Gap Reviewer；后续全局检视确认它造成不必要的第二次 review stage，正式 runner 已将其退役。当前规则是初审一次、每轮 revision 只调用一次 Delta Reviewer；Delta 在同一响应内完成验收、受影响维度评分及必要的下一轮 findings，下一轮直接继承已验证 review 与 SHA 进入 Revision。Program Audit 本地检查4/4编排决定、4条主张、9个证据步骤、9个有效来源片段与14/14 provenance 段落，结果为 `pass`、0错误、0警告。系统据此生成 `automated-publication-decision.v1` 并写入 Wang repository；slug 为 `matthew-16-21-23`，读取投影含 4 个原声播放器。发布稿 SHA 为 `342fa88d5af7c339174bd82a301f0e204f3fd650962029024c01d35c9e97c0d7`。实现已由 PR #2 合并到 GitHub `main`（merge commit `ba7850527de1432f94016f28195ff56e8449851b`）；当前生产后端仍运行另一份部署目录中的旧代码，须在获准部署新 API schema 后，实际 public UI 才会列出该稿，不得以伪造 human decision 绕过。

马太福音文章的自动发布只适用于通过 90 分 editorial gate、hard gates 和 Program Audit 的特定稿件 SHA，不自动批准其上游候选知识、专题、篇章计划或其他产品。旧文章已有的人工 publication decision 继续有效；新文章使用明确标记为 automated 的 SHA 绑定决定，不能伪装成人工批准。

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
- [马太福音释经教学文集成书体例 v2](./matthew_exposition_publication_profile_v1.md)
- [马太福音释经：来源进入论证层与跨来源整合流程 v1](./matthew_source_to_argument_workflow_v1.md)
- [釋經初稿的程序化審計流程 v1](./editorial_draft_audit_workflow_v1.md)

## 文档边界

- 本目录定义跨讲道知识平台、论证层、双轴文库及其产品投影。
- `../notes-to-sermon-agent/` 定义单个 Project 的 notes/transcript → manuscript 生产与审核流程。
- 两者可以共享 Evidence Inventory、canonical unit、citation 和索引能力，但不得把单篇 manuscript 当作完整思想知识库。
