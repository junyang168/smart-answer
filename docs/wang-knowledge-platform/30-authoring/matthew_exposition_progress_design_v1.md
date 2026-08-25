# 马太福音释经文章处理进度：调查与设计 v1

> **读者**：Developer
> **类型**：规范
> **状态**：当前
> **与代码对齐**：未核对
> **权威范围**：/admin/wang/matthew-progress 只读进度页的数据模型与边界。

> 状态：第一阶段实现候选，尚未合并或部署。
> 日期：2026-08-16（America/Chicago）
> 范围：Wang Knowledge Platform 内部只读管理页面；不生成或修改释经文章正文，不运行 notes-to-sermon fidelity audit，不 push、不 deploy。

## 1. 结论

### 1.1 页面位置

新增独立页面：

```text
/admin/wang/matthew-progress
```

它属于统一 Wang 管理入口 `/admin/wang`。该入口按对象和责任分成五个区域：总览、文章进度、内容候选、知识审核、出版单元。现有 `/admin/thought-review` 与 `/admin/canonical-repository` 在第一轮保留为专业工作台，避免在视觉改版中同时改写审核和出版行为；全局 Admin 导航只保留一个 “Wang 文库” 入口，减少 POC 时期多个名称指向相邻对象的问题。

并从以下位置加入入口：

- `/admin/thought-review` 顶部的“马太福音文章进度”；
- `/admin/thought-review/candidates` 的释经候选区；
- `/admin` 的“讲道与文库”分组。

不把它做成现有 `/admin/thought-review` 的第五个 tab。现有页面已经同时承担共享知识、跨讲综合、问答验证和篇章编排审核，而且单文件页面较大；文章进度是跨 artifact、跨部署边界的只读 read model，单独路由更清楚，也不会把“知识审核状态”误写成“文章发布状态”。

### 1.2 权威来源

进度不是单一数据库字段，也不新增一份手工维护的 `status.json`。统一 API 按各层真正拥有的事实做确定性聚合：

| 事实 | 权威来源 | 说明 |
| --- | --- | --- |
| 计划单元、经文范围、article readiness、跨章边界 | PostgreSQL 中的 `CompositionPlan` / `CompositionDecision` | 这是目标权威；现有未入库的正式候选 CompositionPlan 只作迁移期 artifact fallback，并必须返回来源警告 |
| 知识是否足以进入写作 | CompositionPlan 引用的 claims、evidence 与 passage knowledge slice | 以结构和证据资格计算，不以文章文件或标题猜测 |
| Authoring、初审、修订、Delta Review | `matthew_exposition_authoring_runner` 产物 | 只认 schema、generation、结果与 manuscript SHA；不按文件名数量或 Git commit 推断 |
| Program Audit | manifest `audit_config.audit_output_path` 指向的 audit | 不扫描并任选“最新 audit”；manifest 绑定路径才是当前 audit |
| 出版决定 | manifest `publication_decision_path` 指向的 decision | 明确区分 `human-publication-decision.v1` 与 `automated-publication-decision.v1` |
| Wang repository 已发布 | `$DATA_BASE_DIR/wang-knowledge-platform/repository/editorial_drafts/<draft_id>/` | 必须通过与 publisher 相同的 manuscript/review/audit/decision SHA 校验 |
| production 可见 | 明确配置的 production public API 实时投影 | 必须由实际部署服务的 HTTP projection 成功来判定；本地函数、磁盘代码、Git 状态、manifest `status` 都不能代替 |
| 全书来源材料覆盖 | `$DATA_BASE_DIR/wang-knowledge-platform/catalog/matthew_source_coverage.json` | 只表示来源候选材料，不表示已有 CompositionPlan、文章或出版批准 |

### 1.3 需要统一 progress API

需要。建议新增：

```text
GET /admin/wang/matthew-progress
GET /admin/wang/matthew-progress/artifacts/{draft_id}/{artifact_kind}
```

第一项返回版本化、只读、已归一化的 read model；第二项只允许白名单 artifact kind，并返回安全的 JSON/Markdown 查看内容，不把本机绝对路径暴露给浏览器。

建议顶层 schema：

```json
{
  "schema_version": "wang-matthew-exposition-progress.v1",
  "generated_at": "...",
  "book": {"osis": "Matt", "label": "马太福音", "chapter_count": 28},
  "runtime": {},
  "summary": {},
  "chapters": [],
  "articles": [],
  "warnings": [],
  "sources": []
}
```

API 必须在每次读取时重新计算，不保存第二份可写进度状态。

## 2. 调查结果

### 2.1 现有 UI 与 API

- `/admin/thought-review` 读取 PostgreSQL authoring store；未配置数据库时才回退 `shared_knowledge_pilot_v1.json`。
- `/admin/thought-review/candidates` 已能显示 CompositionPlan、CompositionDecision、editorial draft 和经卷/章节导航，但只表达“已有材料/已有编排/有初稿”，没有完整文章阶段链。
- editorial draft 页面在读取时重新跑确定性 Program Audit，适合单稿审阅，但没有全书覆盖、SHA 链矩阵或 production deployment lag。
- `/public/wang-articles` 只列出通过 publication decision 与 manuscript SHA 校验的 repository 稿件。它不会以 manifest 的 `status` 字段决定是否公开。
- 当前前端 TypeScript DTO 都内嵌在大型 page 文件中。新页面应使用独立的 progress types 和小组件，避免继续扩大 POC 单文件。

### 2.2 PostgreSQL 的边界

PostgreSQL 是共享知识和 CompositionPlan 的编辑权威，但不是 editorial manuscript repository。当前数据库有 23 个 CompositionPlan、108 个 CompositionDecision；太16:1–12、太16:21–23 和太17计划可见，但太16:13–20的完整计划目前主要随 repository presentation package 保存，太16章笔记基线计划也仍是 workspace artifact。

因此第一版实现需要：

1. PostgreSQL 优先读取计划；
2. 对 manifest 绑定但尚未入库的 CompositionPlan，从该 manifest 的 presentation package 读取；
3. 对 `composition_plan_matthew_16_notes.json` 这类迁移期 canonical authoring artifact，使用明确配置的 fallback，而不是递归扫描 `generations/`；
4. API 对每项返回 `authority`、`source_schema_version` 和 `source_warning`；
5. 后续把缺少的计划作为 candidate CompositionPlan 正式导入 PostgreSQL，移除 fallback。此迁移不改变 reader-visible prose，也不等于人工批准。

不应把 `$DATA_BASE_DIR/wang-knowledge-platform/catalog/matthew_source_coverage.json` 当作文章计划。它可以说明某章存在来源材料，却不能证明该章已规划成篇。

### 2.3 production/workspace 边界

设计调查曾遇到文档、磁盘代码与运行中服务状态不一致的情况。页面不能把其中任一项单独解释为 production visible；必须由明确配置的 production HTTP projection 给出运行时事实。API 应返回：

```json
{
  "runtime": {
    "api_schema_version": "wang-matthew-exposition-progress.v1",
    "recognized_publication_decision_schemas": [
      "human-publication-decision.v1",
      "automated-publication-decision.v1"
    ],
    "environment": "development|production|unknown",
    "production_probe_configured": true,
    "production_probe_checked_at": "...",
    "production_probe_available": true,
    "deployment_state": "current|lagging|unreachable|unknown"
  }
}
```

判定规则：

- `repository_published=true` 且明确配置的 production public API 通过 HTTP 返回 slug：`production_visible=true`；
- repository 有有效稿件，但 production API 不返回 slug，且 capability probe 显示它不接受该 decision schema：`deployment_state=lagging`，显示“部署版本尚未识别此出版决定”；
- production probe 未配置或无法连通：`production_visible=null`，显示“无法确认”，绝不把本地 Python 投影成功降格或升级为 production 事实；
- 只有当服务明确声明 `environment=production` 时，才允许把同进程 public projection 当作 production probe；开发环境必须使用单独配置的只读 production origin；
- frontend 期待的 progress schema 高于 backend 返回版本：页面显示 API 版本滞后警告；
- 不使用 Git commit 数量推断任何文章阶段。构建标识只能作为诊断 metadata，不能成为进度证据。

## 3. 文章单元与经文覆盖模型

### 3.1 文章单元身份

一篇文章不是一个 CompositionDecision。现有资料同时存在“章节级计划中的一项 article-ready decision”和“单篇文章计划中的多个段落 decisions”。统一模型必须引入归一化的只读 `article_unit`：

```json
{
  "article_unit_id": "Matt.16.24-Matt.16.27",
  "passage": {
    "start": {"chapter": 16, "verse": 24},
    "end": {"chapter": 16, "verse": 27},
    "osis": "Matt.16.24-Matt.16.27",
    "display": "太16:24–27",
    "cross_chapter": false
  },
  "plan_refs": [],
  "draft_id": null
}
```

`article_unit_id` 由规范化 OSIS 范围确定；若未来同一范围允许多个不同产品版本，再加入 publication profile/version，不用标题做身份。

归并顺序：

1. 读取章节级 CompositionPlan 中明确标为 article-ready 的单元；
2. 读取单篇 CompositionPlan 和 repository manifest 的明确 passage；
3. manifest-bound 已生成文章先成为实际 article units；它们的经节覆盖从较早章节级 article-ready 候选范围中扣除，已被一个或多个实际文章完整覆盖的旧候选不再重复计为“尚未生成”；
4. 候选范围扣除实际文章覆盖后仍有连续残余时，残余保留为 planned gap，并附原 plan ref；若扣除后形成多个不连续残余，分别显示并产生 `plan_boundary_superseded` warning，要求补一份新的结构化 CompositionPlan，而不是静默猜测新文章边界；
5. 相邻或重叠的实际文章只在同一 manifest/plan 明确表明同一文章时合并，不能仅因重叠自动合并；
6. 无法解析的 passage 保留为 `unresolved_scope` warning，不从标题猜测。

### 3.2 全书覆盖

全书覆盖需要一个稳定的马太福音 28 章 verse universe。它是圣经结构 metadata，不是文章进度。实现时应放入可测试的 canonical book metadata，而不是向外部 API 临时查询。

每节分别计算：

- `planned`：至少被一个有效 article unit 覆盖；
- `generated`：至少被一个 manifest-bound manuscript 覆盖；
- `repository_published`：至少被一个通过 publisher 校验的 repository unit 覆盖；
- `production_visible`：至少被当前运行 backend 的 public projection 覆盖；
- `coverage_gap`：未被任何有效 article unit 覆盖；
- `overlap_warning`：被多个未声明关系的 article unit 覆盖。

完成率必须有明确分母，页面同时显示：

- 文章单元完成率：已生成篇数 / 已规划 article-ready 单元数；
- 经节覆盖率：各状态覆盖经节数 / 马太福音总经节数；
- 章节覆盖不是简单“完成章数”，而是每章经节比例。

跨章单元在两个章节都显示同一 `article_unit_id`，并带 `cross_chapter=true`；统计文章篇数时只计一次。

## 4. 工作流阶段的确定性计算

`current_stage` 是“通过验证的最远阶段”，不是目录中最靠后的文件名。所有阶段同时返回 `state: complete|active|blocked|not_started|unknown` 和 evidence refs。

| 阶段 | 完成条件 |
| --- | --- |
| `composition_ready` | 结构化 article unit 存在，readiness 允许成篇，passage 可解析 |
| `knowledge_ready` | 计划引用的 claims 可解析，passage fast path 判定没有经节缺口，且至少存在 `eligible`、`eligible_candidate` 或 `eligible_with_label` 证据；媒体 readiness 单独计算 |
| `authoring` | runner 的 author result 通过 schema/contract 校验并产生 manuscript SHA |
| `independent_editorial_review` | 初次独立 review artifact 的 manuscript SHA 与当前稿一致 |
| `revision` | revision artifact 通过 schema，完整 revised manuscript 存在且 SHA 可算 |
| `final_delta_review` | Delta Review 通过 packet、anchor、维度集合与 manuscript SHA 校验 |
| `program_audit` | manifest 绑定的 audit 存在；pass/fail 和 error count 分开显示 |
| `publication_decision` | human/automated decision schema 合法，且绑定 manuscript/review/audit SHA |
| `repository_published` | Wang repository 中 manifest 与全部声明 artifact 存在，并通过 publisher 等价校验 |
| `production_visible` | 明确配置的 production public API 实际返回该 slug；未配置或不可达时为 unknown |

旧稿可能没有完整 runner artifact 链，但已有合法 human publication decision 和 repository publication。API 应将其最高阶段判为 production visible，同时在 `warnings` 中标记 `legacy_intermediate_artifacts_incomplete`，不能把合法旧出版物错误降级。

### 4.1 阻塞项与下一步

blocker 使用稳定 code，UI 再投影为普通语言：

| code | 含义 | 下一步 |
| --- | --- | --- |
| `unresolved_passage_scope` | CompositionPlan 无结构化经文范围 | 补齐 plan passage metadata |
| `knowledge_claim_missing` | 计划引用主张不存在 | 修复/导入知识包，不生成正文 |
| `knowledge_evidence_ineligible` | 关键主张无合格证据 | 完成来源与证据资格处理 |
| `editorial_below_threshold` | score < 90 | 按既有 finding 进入 Revision；不增加 Score-Gap Review |
| `editorial_hard_gate_failed` | hard gate/failure 未清零 | 处理对应 finding 或转人工 |
| `program_audit_errors` | Program Audit error_total > 0 | 修复可程序验证问题后重跑 |
| `publication_schema_unknown` | decision schema 不受当前 runtime 支持 | 部署支持该 schema 的 backend；不可伪造 human approval |
| `sha_mismatch` | manuscript/review/audit/decision 任一链不一致 | 停止出版，重新生成绑定 artifact |
| `repository_copy_missing` | 决定有效但 repository 中没有完整副本 | 运行既有 repository publisher |
| `production_deployment_lag` | repository 已发布但 live projection 不可见 | 部署/重启正确 backend 并复核 public projection |
| `runtime_unreachable` | 无法探测 production | 恢复服务后重新确认，不推断为未上线 |

普通的下一阶段也由固定映射产生，例如 `composition_ready → 准备并验证 passage knowledge slice`，不手写每篇文章的重复状态。

## 5. Article DTO

每篇至少返回：

```json
{
  "article_unit_id": "Matt.16.21-Matt.16.23",
  "passage": {},
  "title": "...",
  "draft_id": "DRAFT-M16-003-V1",
  "current_stage": "production_visible",
  "stages": [],
  "editorial": {
    "score": 90,
    "passed": true,
    "hard_gate_failures": [],
    "declared_hard_failures": []
  },
  "program_audit": {
    "status": "pass",
    "error_count": 0,
    "warning_count": 0
  },
  "publication_decision": {
    "kind": "automated",
    "schema_version": "automated-publication-decision.v1",
    "authority": "automated_quality_gates",
    "valid": true
  },
  "sha_integrity": {
    "status": "consistent",
    "checks": []
  },
  "media": {
    "covered_decision_count": 4,
    "decision_count": 4,
    "player_count": 4,
    "requires_media_projection": false
  },
  "repository_published": true,
  "production_visible": true,
  "blockers": [],
  "next_step": null,
  "updated_at": "...",
  "updated_at_source": "artifact_timestamp|filesystem_mtime|database_change_set",
  "links": {}
}
```

SHA 不只返回一个布尔值。`checks` 至少包括：

- manuscript actual SHA vs audit fingerprint draft SHA；
- manuscript actual SHA vs editorial review manuscript SHA；
- manuscript actual SHA vs publication decision manuscript SHA；
- audit file actual SHA vs decision technical audit SHA；
- review file actual SHA vs decision editorial review SHA；
- normalization 前后稿 SHA 与 `reader_visible_text_unchanged`（存在时）。

缺字段返回 `unknown`，旧 schema 返回 `legacy_unverifiable`；不能把 missing 当作 match。

## 6. 页面信息架构

### 6.1 顶部

显示四个不能互相替代的核心数字：

1. 已规划 article-ready 单元；
2. 已生成 manuscript；
3. Wang repository 已发布；
4. production 当前可见。

另显示经节覆盖率和 runtime 状态。若 production 无法探测或 API schema 落后，顶部显示全宽警告。

### 6.2 全书与章节覆盖

- 28 章紧凑矩阵，每章显示 planned/generated/repository/production 的经节比例；
- 点击一章后显示逐节横条，缺口保持空白并标“未规划”；
- 跨章单元使用连接标识和同一 unit ID，不在两章重复计篇数；
- 来源材料覆盖作为次级信息，例如“本章有 12 个候选来源”，不能使用与文章完成相同的颜色或文案。

### 6.3 文章流水线

- 每篇一行十阶段 stepper；
- 默认按经文顺序，不按更新时间或 draft ID；
- 可筛选章节、当前阶段、仅看 blocker；
- 行内直接显示 score、hard gate、audit errors、decision kind、repository 和 production 两个独立状态；
- 点击展开详情，显示 SHA 校验矩阵、媒体覆盖、blocker、下一步和 artifact links；
- 普通同工默认看到普通语言，schema、SHA、artifact 文件名收在“技术详情”。

### 6.4 现有 POC 的改进原则

- 不继续在 `/admin/thought-review/page.tsx` 内堆叠新类型与视图；
- 新 progress 页面拆成 coverage、pipeline、article detail、warning banner 等小组件；
- 后端使用独立 progress service，不调用会写状态的审核路径；
- 将 progress DTO 定义抽到共享 TypeScript 文件，并为未知 enum 值提供安全 fallback；
- 保留现有 POC 的优点：普通语言优先、技术资料折叠、明确显示数据 authority；
- 修正当前 candidate UI 的语义混合：`editorial_draft` manifest status、有效 publication decision 与 public visibility 分开显示。

## 7. 第 16 章基线核验

以下为调查时由当前 workspace 代码和 Wang repository artifacts 得到的基线；它是验收 fixture，不是手工维护的运行状态：

| 经文 | 当前确定性事实 | 备注 |
| --- | --- | --- |
| 太16:1–12 | manuscript、90 分 editorial pass、audit `pass_with_warnings`/0 errors、human decision、repository 完整；当前 workspace public projection 可返回；6 个播放器 | 2 个 audit warnings；manifest status 为 `published` |
| 太16:13–20 | manuscript、93 分 editorial pass、audit `pass_with_warnings`/0 errors、human decision、repository 完整；当前 workspace public projection 可返回；19 个播放器 | manifest status 仍为 `editorial_draft`，证明不能用该字段判断公开状态 |
| 太16:21–23 | manuscript、90 分、0 hard gates/failures、Program Audit `pass`/0 errors、automated decision、repository 完整；当前 workspace public projection 可返回；4 个播放器 | repository 中还保留一个较早、SHA 不同的 `editorial-draft-audit.json`；当前 audit 必须服从 manifest 绑定的 `program-audit.json` |
| 太16:24–27 | CompositionPlan 标为 `article_ready`，尚无 manifest-bound manuscript | 应从 composition/knowledge 阶段继续，不得显示已生成 |
| 太16:28–17:8 | CompositionPlan 明确要求跨章且 `article_ready`，尚无 manifest-bound manuscript | UI 必须在第16、17章共同显示，但篇数只计一次 |

调查时三篇 repository manuscript SHA 与各自 publication decision 一致。workspace Python projection 能列出三篇，只证明当前代码能够识别 artifacts；live production 可见性仍须以明确配置的 production 服务实际 HTTP 响应为准。

## 8. 实施分解与测试

用户确认本设计后，按以下顺序实施：

1. 后端 `matthew_exposition_progress` 纯函数与 DTO；
2. 计划来源 adapter（PostgreSQL、manifest-bound package、明确的迁移 fallback）；
3. artifact chain validator，复用 publisher/public projection 的校验逻辑，避免两套 gate 漂移；
4. versioned admin progress API 与安全 artifact viewer；
5. Next.js proxy、共享 TS types 和独立 progress 页面；
6. admin/thought-review/candidates 入口；
7. fixtures 覆盖 human、automated、旧 schema、missing、SHA mismatch、cross-chapter、repository copy missing、production lag/unreachable；
8. 后端单元测试、API 测试、前端 TypeScript/lint/build；必要时本地运行页面做桌面与窄屏检查。

必须新增的关键测试：

- 太16五个基线单元状态符合本节；
- 旧 human decision 与 automated decision 都能正确识别且不混淆 authority；
- manifest status 为 `editorial_draft` 但决定有效时，repository/public 状态仍由真实 gate 决定；
- 同目录存在旧 audit 时，只使用 manifest 绑定 audit；
- 任一 SHA 不一致时 publication/repository/production 阶段不得通过；
- production 无法探测返回 `null/unknown`，不是 false；
- 跨章单元在两章显示、全书篇数只计一次；
- 计划缺少结构化 passage 时显示 warning，不能从标题猜测；
- source coverage 不会被误计为 planned/generated；
- API schema 和未知 enum 向后兼容。

## 9. 非目标与安全边界

- 本功能只读，不批准 claim、CompositionPlan 或文章；
- 不生成、修改或规范化 reader-visible article prose；
- 不调用任何模型；
- 不运行 notes-to-sermon fidelity audit；
- 不新增 Score-Gap Review，不改变 reviewer-call invariant；
- automated publication decision 始终显示为 automated，不伪装 human；
- 不 push、不 deploy；
- 不以 Git commit 数量、文件数量或标题模式推断文章进度。

## 10. 文档偏差

任务要求的 `docs/README.md` 在当前工作树不存在。调查已完整阅读同一权威目录中的 `docs/wang-knowledge-platform/README.md`；实施前应由维护者确认任务描述是否原本指向该文件。此偏差不影响本设计对 Wang Knowledge Platform 边界的遵守。
