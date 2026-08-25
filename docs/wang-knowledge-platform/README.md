# 王守仁教授释经与思想知识平台文档

> **读者**：所有人。本文只是索引。
> **类型**：说明。
> **状态**：当前。
> **权威范围**：无。本文不定规则，只指路；规则在它指向的文档里。

本目录记录「王守仁教授释经与专题讲论文库」作为独立知识平台的使命、产品设计、技术架构、语料普查方法和出版蓝图。

它与 `notes-to-sermon-agent` 的关系是：后者负责把一份 notes 或 transcript 生成、审核并发布为 manuscript；本项目负责跨越全部讲道与笔记，保存可审核的主张、论证关系和精确来源，再由同一知识基础驱动释经、主题专论、智能问答、搜索、比较、微讲道和学习工具。

## 按你的身份进入

| 你是 | 从这里开始 |
| --- | --- |
| **普通基督徒 / 教会同工** | [Project Mission Statement](./00-overview/project_mission_statement.md) —— 不需要技术背景，读完就知道这个平台在做什么。 |
| **神学编辑（审稿同工）** | 上面那份，加[馬太福音釋經教學文集：成書體例](./30-authoring/matthew_exposition_publication_profile_v1.md)和[思想与篇章审核工作台使用说明](./90-ops/reviewer_ui_guide.md)。 |
| **Solution architect** | [知识平台总体设计](./00-overview/knowledge_platform_design.md) → [共享知识模型](./00-overview/shared_knowledge_model_v1.md) → 下面各子系统。 |
| **Developer** | 先读该子系统的规范，再读对应流程文档。写文章相关的从 `30-authoring/` 开始。 |
| **要修改本目录文档的人** | [文檔維護約定](./CONVENTIONS.md)。 |

`AGENTS.md` 点名必读的四份，在下面用 **★** 标出。

## 设计治理顺序

本项目必须遵循：**Project Mission → 共享知识模型 → 205 篇全库普查、跨讲综合与结构级人工审核 → 多 use-case 验证 → 正式产品与公开 UI**。use case 验证发现的问题必须回写上游模型或候选基线，不能只在某个产品中修补。`/admin/thought-review` 等页面是可以提前建设的内部审核基础设施，不代表已经跳到正式产品阶段。详细规则见总体设计的「不可倒置的上下游治理顺序」。

## 00-overview —— 平台级

| 文档 | 它回答什么 |
| --- | --- |
| [Project Mission Statement](./00-overview/project_mission_statement.md) | 这个计划为什么存在，忠实与署名的底线在哪里 |
| [知识平台总体设计](./00-overview/knowledge_platform_design.md) | 目标架构与产品定位；与实现冲突时先改设计 |
| [共享知识模型 v1](./00-overview/shared_knowledge_model_v1.md) | Claim、EvidenceStep、Relation、TopicNode 的意义与边界 |
| [王守仁教授学术思想整理：核心 Use Case](./00-overview/scholarly_thought_reconstruction_use_case.md) | 把教授的学术思想整体重建出来，需要什么 |
| [同工反馈到产品需求与验收标准 v1](./00-overview/stakeholder_feedback_to_requirements_v1.md) | 同工的一句话怎样变成可验收的需求 |
| [独立 AI 复审、双模型仲裁与人工分歧处理 v1](./00-overview/independent_ai_review_v1.md) | 双模型仲裁政策（跨子系统通用） |

## 10-extraction —— 来源进入 claim 层

| 文档 | 它回答什么 |
| --- | --- |
| [第一遍普查格式 v1](./10-extraction/corpus_survey_format_v1.md) | 全库普查的 JSON 格式 |
| [逐句详细知识整理与双模型复审流程 v1](./10-extraction/detailed_knowledge_extraction_workflow_v1.md) | 一篇讲道怎样被逐句拆成主张与证据 |
| [來源逐句對帳與 claim 層完整性 v1](./10-extraction/source_to_claim_layer_ledger_v1.md) | 来源里的东西有没有真的进入论证图 |
| [講道口語抽取實測 v1](./10-extraction/transcript_extraction_on_spoken_sermon_v1.md) | 抽取在口语讲道上的实际表现（记录） |
| [分節抽取後獨立複審的價值 v1](./10-extraction/independent_review_value_after_section_extraction_v1.md) | 分节之后 AI 复审还抓得到什么（记录） |
| [论证层视图 v1](./10-extraction/argument_layer_view_v1.md) | 怎样查看单篇来源的论证层 |
| [講道目錄分類與 ingestion 邊界](./10-extraction/sermon_catalog_ingestion.md) | 目录导航、内容分类与跨讲归组的分界 |
| [候选基线 v3](./10-extraction/candidate_baseline_v3.md) | 205 篇普查后经批准的 8 个候选领域与 3 条轴 |
| [经文角色与释经覆盖格式 v2 校准](./10-extraction/scripture_roles_v2_pilot_validation.md) | 经文角色能否无损抽取（记录） |

## 20-knowledge —— 知识层

| 文档 | 它回答什么 |
| --- | --- |
| ★ [Canonical Viewpoint Registry 与跨讲论证路径设计 v1](./canonical_viewpoint_registry_design_v1.md) | 跨讲观点身份、ArgumentRoute、覆盖披露与下游消费。**本文档暂留在顶层**：PR #214 正在大幅改写它，待合并后移入本子系统。 |
| [跨讲道主张比较与双模型归并流程 v1](./20-knowledge/cross_sermon_relation_workflow_v1.md) | 不同讲道的主张怎样比较成关系 |
| [母题—子专题—篇章段落自动发现与双模型复核 v1](./20-knowledge/topic_structure_discovery_workflow_v1.md) | 主题层级怎样从主张与关系里长出来 |
| [PostgreSQL 共享知识主库 v1](./20-knowledge/postgresql_authoring_store_v1.md) | 谁是编辑权威，ChangeSet 怎么写入 |
| [文库 Functional Specification](./20-knowledge/exegesis_topic_repository_functional_spec.md) | 面向读者的文库：canonical unit、圣经/主题索引、来源可追溯 |
| [文库 Technical Specification](./20-knowledge/repository-tech-spec/README.md) | 文库的实现架构、存储布局与标识符。原为一份 1,994 行文档，已拆成五个： |
| &nbsp;&nbsp;└ [Data Models](./20-knowledge/repository-tech-spec/data-models.md) | 23 个存储记录类型，开头有速查表 |
| &nbsp;&nbsp;└ [Compiler & Read Model](./20-knowledge/repository-tech-spec/compiler.md) | 来源怎样变成可查询状态，以及什么会让它失效 |
| &nbsp;&nbsp;└ [API & UI](./20-knowledge/repository-tech-spec/api-and-ui.md) | 对外暴露的接口、前端、来源定位与权限 |
| &nbsp;&nbsp;└ [Delivery](./20-knowledge/repository-tech-spec/delivery.md) | 可观察性、测试、实施阶段、部署与验收 |

## 30-authoring —— 写文章引擎

| 文档 | 它回答什么 |
| --- | --- |
| ★ [当前写作会话状态](./30-authoring/CURRENT_AUTHORING_SESSION.md) | 现在哪几篇已发布、SHA 是什么、边界在哪 |
| ★ [馬太福音釋經多 Agent 寫作流程 v1](./30-authoring/matthew_exposition_multi_agent_authoring_workflow_v1.md) | 写一篇文章的 agent 状态机 |
| ★ [篇章釋經快速路徑 v1](./30-authoring/fast_passage_editorial_workflow_v1.md) | 抽取已存在时的连续释经快速路径 |
| [馬太福音釋經教學文集：成書體例 v2](./30-authoring/matthew_exposition_publication_profile_v1.md) | 这套书的体例与编辑纪律 |
| [釋經文章寫作品質評分表 v1](./30-authoring/matthew_exposition_writing_quality_rubric_v1.md) | 正文的评分维度与 hard gates |
| [釋經初稿的程序化審計流程 v1](./30-authoring/editorial_draft_audit_workflow_v1.md) | 编排计划与初稿之间的程序化审计 |
| [篇章编排双模型审核 v1](./30-authoring/composition_ai_review_v1.md) | 编排计划怎样被双模型复核 |
| [馬太福音釋經：來源進入論證層與跨來源整合 v1](./30-authoring/matthew_source_to_argument_workflow_v1.md) | 笔记与讲稿两种来源怎样进入论证层 |
| [馬太福音十六章來源地圖 v1](./30-authoring/matthew_16_source_map_v1.md) | 十六章有哪些来源、哪些算独立证据 |
| [马太福音第 17 章释经篇章蓝图](./30-authoring/matthew_17_exposition_blueprint.md) | 太17 释经文章的结构（候选，应迁移为 CompositionPlan） |
| [马太福音文章进度页设计 v1](./30-authoring/matthew_exposition_progress_design_v1.md) | `/admin/wang/matthew-progress` 只读进度页的设计 |

## 40-qa-search —— 智能问答与搜索

| 文档 | 它回答什么 |
| --- | --- |
| [知识驱动的 Search 与 QA Specification](./40-qa-search/sermon_search_functional_spec.md) | 有来源依据的搜索与问答功能规范 |
| [独立问答产品验证 v1](./40-qa-search/qa_product_validation_v1.md) | 问答作为独立产品的三种答案状态与读者要求 |
| [问答答案双模型诊断与上游修复协议 v1](./40-qa-search/qa_answer_diagnostic_protocol_v1.md) | 一个坏答案怎样定位到最早出错的那一层 |

## 50-micro-sermon —— 微讲道

| 文档 | 它回答什么 |
| --- | --- |
| [微讲道：三至五分钟短篇教导 Use Case](./50-micro-sermon/micro_sermon_product_use_case.md) | 微讲道产品的目标设计 |

## 90-ops —— 运营与工作台

| 文档 | 它回答什么 |
| --- | --- |
| [王教授文庫營運總表 v1](./90-ops/operational_dashboard_v1.md) | 讲道线与文章线的运营台账 |
| [思想与篇章审核工作台使用说明](./90-ops/reviewer_ui_guide.md) | 非技术同工怎样使用 `/admin/thought-review` |
| [base-contract-coverage/](./90-ops/base-contract-coverage/README.md) | 基础契约覆盖率量测输出（由代码生成） |
| [observation-argument-coverage/](./90-ops/observation-argument-coverage/README.md) | observation→argument 覆盖率量测输出（由代码生成） |

## archive —— 已被取代或已结束

保留供审计，**不代表当前做法**。详见各文件头部的状态说明。

[候选基线 v2](./archive/candidate_baseline_v2.md)（已由 v3 取代）· [全语料第一遍普查发现 v1](./archive/full_corpus_survey_findings_v1.md)（111 篇阶段的历史结果）· [代表样本普查 v1](./archive/corpus_survey_sample_v1.md) · [205 篇总体设计验证 v1](./archive/full_corpus_205_design_validation_v1.md) · [205 篇 17 个候选归组结构审核 v1](./archive/candidate_group_review_205_v1.md) · [第三、第四讲多用途验证规范 v1](./archive/two_lecture_multi_use_validation_spec_v1.md) · [双轴纵向验证 v1](./archive/dual_axis_vertical_validation_v1.md) · [太16:13-20 写作诊断 v1](./archive/matt16-13-20_writing_diagnostic_v1.md) · [数据权威与路径迁移审计 2026-08-16](./archive/data_authority_and_path_migration_audit_2026-08-16.md) · [实施进度日志 2026-08](./archive/implementation_progress_log_2026-08.md) · [实施验证记录 2026-08](./archive/implementation_validation_notes_2026-08.md) · [文库 TS 实施状态 2026-08](./archive/repository_tech_spec_implementation_status_2026-08.md)

## 文档边界

- 本目录定义跨讲道知识平台、论证层、双轴文库及其产品投影。
- `../notes-to-sermon-agent/` 定义单个 Project 的 notes/transcript → manuscript 生产与审核流程。
- 两者可以共享 Evidence Inventory、canonical unit、citation 和索引能力，但不得把单篇 manuscript 当作完整思想知识库。

共享知识模型的类型定义、关系验证、幂等迁移和版本保护位于 `backend/api/canonical_repository/knowledge_models.py`、`knowledge_importer.py` 与 `store.py`。`$DATA_BASE_DIR/wang-knowledge-platform/staging/claim-layer/shared_knowledge_pilot_v1.json` 是 legacy 候选交换包，不等于 PostgreSQL authoring authority，也不会直接进入公开文库。
