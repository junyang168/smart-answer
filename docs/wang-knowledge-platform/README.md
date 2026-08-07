# 王守仁教授释经与思想知识平台文档

本目录记录“王守仁教授释经与专题讲论文库”作为独立知识平台的使命、产品设计、技术架构、语料普查方法和试验性出版蓝图。

它与 `notes-to-sermon-agent` 的关系是：后者负责把一份 notes 或 transcript 生成、审核并发布为 manuscript；本项目负责跨越全部讲道与笔记，保存可审核的主张、论证关系和精确来源，再由同一知识基础驱动释经、主题专论、智能问答、搜索、比较和学习工具。

## 核心文档

1. [Project Mission Statement](./project_mission_statement.md)
2. [知识平台总体设计](./knowledge_platform_design.md)
3. [文库 Functional Specification](./exegesis_topic_repository_functional_spec.md)
4. [文库 Technical Specification](./exegesis_topic_repository_tech_spec.md)
5. [知识驱动的 Search 与 QA Specification](./sermon_search_functional_spec.md)

## 语料普查与试验

- [第一遍普查格式 v1](./corpus_survey_format_v1.md)
- [代表样本普查 v1](./corpus_survey_sample_v1.md)
- [马太福音第 17 章释经篇章蓝图](./matthew_17_exposition_blueprint.md)

## 文档边界

- 本目录定义跨讲道知识平台、论证层、双轴文库及其产品投影。
- `../notes-to-sermon-agent/` 定义单个 Project 的 notes/transcript → manuscript 生产与审核流程。
- 两者可以共享 Evidence Inventory、canonical unit、citation 和索引能力，但不得把单篇 manuscript 当作完整思想知识库。
