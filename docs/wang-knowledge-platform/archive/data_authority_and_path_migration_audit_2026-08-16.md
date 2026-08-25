# Wang 数据权威与路径迁移审计（2026-08-16）

> **读者**：Developer
> **类型**：记录
> **状态**：历史记录（2026-08-16 一次迁移）
> **与代码对齐**：不适用
> **权威范围**：无。

## 结论

PostgreSQL `smart_answer_knowledge` 是知识编辑主库；`output/claim-layer` 不是完整、可靠或自包含的第二主库。本轮已建立 `$DATA_BASE_DIR/wang-knowledge-platform/`，并以复制方式保存 repository、compiled snapshot、catalog projection 与 staging。在完整对账、部署、归档、恢复演练及用户明确批准后，三个 legacy 范围已永久删除。

Article 2、Matthew 16 notes、core-nine reviewed product candidates、当前 topic identity records，以及 Romans 四篇无冲突讲道及其安全 projection/relations/topic identities 均已通过 additive 或 source-scoped reconciliation 进入 PostgreSQL。Romans 中 `2019-03-24 罗马书 3:21–31` 的 alternate extraction generation 会覆盖 156 个现有对象，因此 PostgreSQL 继续保留既有 Covenant generation；alternate generation 及所有直接依赖项留在 canonical staging，等待显式 claim mapping，不作为第二权威。删除后回归发现并修正了 Matthew progress、thought-review、shared-knowledge pilot 与旧 shared-pilot 测试的残余路径依赖；最终相关回归为 161 passed。

## PostgreSQL 对账

- schema migration：`001_postgres_authoring_store`，2026-08-11 应用。
- 53 个 ChangeSet，全部 `applied`；6,506 个 object versions；6,506 个 change operations；1,013 条 edges；28 个 review events；当前对象总数 6,160。Corpus survey 路径规范化用 1 个 ChangeSet 更新 3 个 locator；结构权威包另用 1 个 ChangeSet 新增 21 个对象、更新 1 个决策；随后 22 个既有人工批准分别写入 review event 与版本历史。
- review events 全部是 `claims / approved`。当前只有 6 条 claim 为 `approved`，其余知识对象仍是 candidate 或 AI consensus 状态。
- collections：`source_documents` 25、`source_fragments` 1,818、`questions` 275、`observations` 430、`evidence_steps` 1,012、`claims` 454、`knowledge_relations` 572、`claim_relations` 436、`claim_relation_constraints` 5、`topic_nodes` 59、`topic_identity_reconciliations` 26、`knowledge_routes` 594、`position_nodes` 137、`editorial_syntheses` 41、`composition_plans` 44、`composition_decisions` 205、`editorial_checks` 5、`tensions` 1。
- max revision：claims、evidence、fragments、relations 等最高为 2；composition plans/decisions 最高为 3。

Article 2 现已 canonicalized：`CP-matthew-16-13-20`、9 个 decisions、完整 reviewed source knowledge、30 条 reviewed cross-source relations 和 article routes/projection 已通过三个 append-only reconciliation package 写入，新增 1,355 个对象。Matthew 16 notes 完整 reviewed candidate 另以一个 additive package 新增 430 个对象；core-nine reviewed product candidates 与当前 topic identity records 又以两个 additive package 新增 342 个对象。Romans source-scoped reconciliation 以四个 ChangeSet 新增 1,052 个对象。十个 reconciliation ChangeSet 合计 3,179 creates、0 updates、0 invalidated dependencies。所有会覆盖现有权威或直接依赖冲突 generation 的 rows 均被排除并保存在 reconciliation report。

最新 Active Snapshot 为 `ACTIVE-20260816T212410Z-4be25185`（snapshot SHA-256 `34e1dfcd8e454fce2c35d5b5e6cf3ffc6ee0727a8bb6f9e56db2b9c67b8841fb`）；仍只有 6 approved claims、17 evidence、17 source fragments 和 2 source documents，证明新增 candidate 没有进入 approved-only 对外子图。

包分类及每个 preview summary 保存在 `$DATA_BASE_DIR/wang-knowledge-platform/staging/claim-layer/reconciliation/authority-reconciliation-summary.json`（SHA-256 `4f10bbbdc3cee7c080e4ef92f886b707eb325d4588af1751522fadd09a3e8d22`）。Romans 的 source-scoped 决策、四个包 SHA、排除计数与 ChangeSet 记录保存在 `reconciliation/romans/romans-generation-reconciliation-report.json`（SHA-256 `2c814e61f3ba993223567af629924eee1b4c1a9e669379cf9cab379cb2290e26`）。旧 core topic generation 包仍标记为 `superseded_do_not_apply`，未导入。

## Legacy claim-layer 盘点

总计 520 个文件，约 36 MiB；75 个被 Git 跟踪，445 个未跟踪，没有 ignored 文件。文件类型为 494 JSON、23 Markdown、3 HTML。

| 顶层类别 | 文件数 | 约 KiB | 分类 |
| --- | ---: | ---: | --- |
| `research-batches/` | 216 | 15,444 | staging、review generations、candidate exchange packages；Romans safe scope 已入库，alternate generation 已隔离并分类 |
| `matthew-16-notes/` | 55 | 7,056 | staging；reviewed candidate 已通过 additive package 对账入库 |
| `matthew-16-13-20-sources/` | 79 | 6,612 | Article 2 staging 与历史出版输入；缺口已通过三个 additive packages 对账入库 |
| `matthew-16-1-12-sources/` | 30 | 3,412 | Article 1 staging；正式出版副本在 repository |
| `matthew-16-21-23-sources/` | 44 | 696 | Article 3 staging；正式出版副本在 repository |
| `detailed-extractions/` | 17 | 892 | extraction staging / reviewed candidates |
| `composition-reviews/` | 43 | 504 | review staging / generations |
| `compiled/` | 7 | 244 | PostgreSQL compiled snapshot，不是 authority |
| `adjudication-generations/` | 4 | 96 | staging / legacy archive |
| `qa-diagnostic-generations/` | 3 | 80 | diagnostic staging / legacy archive |
| `review-generations/` | 2 | 64 | review staging / legacy archive |
| 顶层 JSON/HTML/Markdown | 27 | 约 1,500 | candidate exchange、diagnostic、legacy UI export；逐项不作为 authority |

## 数据分类

- **PostgreSQL authority**：claims、evidence、relations、topics、composition plans/decisions、审核状态、版本与 ChangeSets。任何 staging JSON 都不得反向覆盖它。
- **Repository artifact**：三篇 published manuscript、publication editorial review、Program Audit、publication decision、manifest、知识快照及 presentation/media 绑定，位于 `wang-knowledge-platform/repository/editorial_drafts/`。
- **Compiled snapshot**：`wang-knowledge-platform/compiled/active-snapshots/`。它从 PostgreSQL 生成，可回滚但不可反向导入为主库。
- **Test fixture**：`backend/tests/fixtures/wang_knowledge_platform/` 的 19 个稳定输入副本；只服务确定性测试。
- **Staging**：`wang-knowledge-platform/staging/claim-layer/` 的 packets、generations、diagnostics、candidate packages 与研究批次。
- **Legacy archive**：旧 `output/claim-layer`、旧 `$DATA_BASE_DIR/wang_repository`、根级 Matthew coverage 已在用户明确批准后删除；其 mode `0444` 归档及 SHA report 保存在 Wang platform `legacy-archives/2026-08-16/`。
- **原始媒体**：逐字稿、录音、视频不进入 PostgreSQL，也未复制进 Wang platform root；数据库只保存身份、hash 与 locator。

## 三篇出版 SHA 链

- Article 1：manuscript `c71a6d…502a`；editorial review `14cbbf…5cd5`；Program Audit `4c6af4…611e`。human decision 的三个 SHA 均重算匹配。
- Article 2：manuscript `45c700…901f`；editorial review `908406…126a7`；Program Audit `a33def…579a`。旧 repository 缺少 decision 所指向的 `authoring-v1/final-independent-editorial-review-v2.json`；新 repository 已复制该 exact-SHA 文件，因此链现在自包含，decision 内容未改。
- Article 3：manuscript `342fa8…c0d7`；editorial review `472551…25af`；Program Audit `c6599a…e311`。automated decision 的三个 SHA 均重算匹配；score 90，audit pass，0 errors/0 warnings。

所有复制均保持读者可见正文 SHA 不变。

## 路径与部署差异

生产后端 `/opt/homebrew/var/www/smart-answer` 已完成 path cutover：首轮部署统一 path helper、API config、repository、thought-review 与 shared-pilot 五个运行时文件；补充扫描后，又把 canonical import API、knowledge-store CLI、11 个 staging runner、Matthew integration、batch reuse 配置及其测试输入切换到统一路径。未部署前端，也未混入其他业务修改。`com.smart_answer.fullarticleservice` 重载后 health 为 `ok`，运行时 repository 与 staging 均确认指向 `$DATA_BASE_DIR/wang-knowledge-platform/`。三篇文章列表 SHA 与三篇 reader-visible Markdown SHA 在切换及删除前后逐项相同；repository publication SHA chains 再次全部匹配。首轮部署文件 SHA、旧文件备份和回滚步骤保存在 `$DATA_BASE_DIR/wang-knowledge-platform/deployment-reports/path-cutover-20260816.json`（SHA-256 `bdff2c75c9e3ceccb4e70d770804c9a51ed229e305f52107822b9d3f429676c5`）。未 push。

生产读取路径、publisher、authoring runner、knowledge-store、Matthew coverage、canonical import API、11 个 staging-only runner、research-batch default output 与两个 reuse 配置均已切换到统一路径。后端 Python 与 batch JSON 已无 `output/claim-layer` 引用；当前流程文档中的剩余命中仅用于明确标示 legacy staging 或尚未部署的旧 production 路径。

已确认测试 Python 文件中没有直接读取 `output/claim-layer`；fixture 内保留的旧 path 字符串只是不可变 provenance 字段，测试不会解析它们为当前输入位置。

## 最终删除执行与恢复方案

用户明确回复“批准永久删除三组旧资料”后，已删除的精确范围为：

1. 仓库 legacy staging：`output/claim-layer/`（全部 520 个文件）。
2. 数据盘 legacy repository：`$DATA_BASE_DIR/wang_repository/`。
3. 数据盘 legacy catalog projections：`$DATA_BASE_DIR/matthew_source_coverage.json` 与 `$DATA_BASE_DIR/matthew_source_coverage.md`。

逐文件候选、bytes、Git tracking 状态、legacy SHA、canonical counterpart 与 counterpart SHA 已写入 `$DATA_BASE_DIR/wang-knowledge-platform/staging/claim-layer/reconciliation/legacy-retirement/legacy-retirement-manifest.json`（SHA-256 `2cc053287486a7dcc48ed6eab62a96ebd13b20734550bf1a2957ade1294f5e5e`）。当前核对结果为：claim-layer 520 files / 36,358,532 bytes、legacy repository 28 files / 6,240,912 bytes、coverage 2 files / 2,136,099 bytes；550 个文件全部存在 SHA 相同的 canonical counterpart，0 mismatches。该 manifest 状态是 `proposal_only_no_deletion_authorized`，不是删除授权。

三个 legacy 范围已分别建立 mode `0444` 的 `tar.gz` 归档，位于 `$DATA_BASE_DIR/wang-knowledge-platform/legacy-archives/2026-08-16/`。恢复演练已把三个归档解压到隔离临时目录，并对 550 个文件逐一重算 SHA：550 matched、0 mismatches；临时恢复副本随后移入系统 Trash。归档 SHA、大小与演练结果保存在 `archive-and-restore-report.json`（SHA-256 `fc0cc3795c5bf127851c6915271084d24feb853078d317ac0c89ae0f5794cf58`）。

删除执行记录保存在 `$DATA_BASE_DIR/wang-knowledge-platform/deployment-reports/legacy-deletion-20260816.json`（SHA-256 `f57154567d2e9fbfa87be3121e3ff29bff679c0611c7fab09d7ecadd7a7f1bf9`）。删除后确认四个 legacy 路径均不存在、三个归档仍可读取、production health 为 `ok`、三篇 reader-visible Markdown SHA 不变，相关测试 161 passed。恢复路径是在停写期间把归档解压回原路径，或用 deployment backup 恢复旧 production code/path。

## 补充 checkout 清理

用户从文件树发现残留目录后，全域扫描确认初始 manifest 漏掉三个 checkout 副本：developer checkout 520 files / 36,358,532 bytes、production checkout 214 files / 15,860,997 bytes、另一 Codex worktree 75 files / 8,930,427 bytes。developer 副本 520 个文件全部与 canonical staging 同 SHA；production 副本有 208 个相同、2 个 canonical 未有、4 个内容不同；另一 worktree 有 72 个相同、3 个内容不同。为避免丢失历史 generation，三份均先建立完整 mode `0444` 归档并逐档解压、逐文件 SHA 恢复验证，随后才永久删除。

删除前已把三个 checkout 的 runtime defaults、batch reuse 配置及测试输入迁到统一 Wang path / Git fixtures。删除后回归结果为 developer 56 passed、production 47 passed、另一 worktree 32 passed；production health `ok` 且仍列出 3 篇文章。最终扫描 `/Users/junyang/.codex/worktrees`、`/Users/junyang/app` 与 production root，不再存在任何 `output/claim-layer` 目录。补充归档与删除记录保存在 `$DATA_BASE_DIR/wang-knowledge-platform/deployment-reports/supplemental-legacy-deletion-20260816.json`（SHA-256 `b289c3a39aa5e957a1cc2aba8bd5445a20cbc4c0f325cf26290880c68d7ff3c8`）。

## Corpus survey 迁移

`output/corpus-survey` 共 265 files / 6,984,124 bytes：205 份 first-pass survey、15 份 scripture-role pilot、39 份 synthesis 与 6 份 comparison。两份 checkout 副本逐文件完全相同，且全部未被 Git 跟踪。205 份 first-pass 合计 1,580 clusters、3,752 candidate claims、6,401 anchors；112 个 published 与 93 个 reviewed 来源均存在。202 份仍与当前逐字稿 SHA 相同；`S_201101`、`S_210912_3`、`S_230521` 三份来源 SHA 已变化。全部 205 份均缺后来引入的 generation fingerprint；这限制它们作为新抽取 cache 或新 synthesis 输入的资格，但不构成重抽队列。该 205 篇 survey 是一次性封闭历史快照，不能作为 PostgreSQL authority，也不得追随后来的来源变化重新生成。

265 个文件已复制到 `$DATA_BASE_DIR/wang-knowledge-platform/staging/corpus-survey/`，逐文件 SHA 0 mismatches；三个 checkout 与 production 的 survey、synthesis、scripture enrichment、AI review、sermon catalog 与 Matthew coverage 默认路径均已切换。当前工作区相关测试 54 passed，production 重载后 health `ok` 且仍列出 3 篇文章。

PostgreSQL 原有 2 个 composition plans 与 1 个 editorial synthesis 的 locator 仍指向旧 `output/corpus-survey`／`output/claim-layer`。ChangeSet `KCS-10f21523812208ba02a4` 以 3 updates、0 creates、0 dependency invalidations 把它们规范化到 canonical staging；三个对象升到 revision 2，数据库当前 legacy locator 命中为 0。该操作只改变路径字段，不改变知识陈述。

Corpus survey 已建立 SHA-bound 研究快照 `repository/research_corpus_snapshots/CORPUS-SURVEY-205-V1/`：包含 205 篇总体候选图、17 组结构审核、candidate baseline v3、设计验证及两组 comparison/review。base manifest SHA-256 为 `22a230c2febc876f3ebcd5efe3cb1f3f1575f5f257eebdab7ac96567edcc81d5`；PostgreSQL binding SHA-256 为 `acc7c4450690696e82b36efd57d8d4931042c420021fe77d0c4929927083233d`；封闭策略 sidecar SHA-256 为 `3957c536bfb34c521f0da850e69816eaa9717668136ae10a840cae3fa11c1e1c`。

17 组候选结构均已作为 `topic_identity_reconciliations` 写入，并按项目负责人 2026-08-07 的结构审核记录为 `approved`。批准边界只涵盖归属、合并、拆分或轴线路由，不批准其内部 survey claims。Candidate baseline v3 另以 approved editorial synthesis 保存八个可修订一级候选领域和三条横轴。两个 `human_reviewed` comparison 的 4 个决策已分别映射为 1 个 editorial synthesis、2 个 editorial checks 与 1 个既有 composition decision，全部有 human review events。

最新 Active Snapshot 为 `ACTIVE-20260816T222538Z-8afe37e3`（SHA-256 `d11cc99fcd7864f4c5341bd46175ac8c6340b1c27945fc67c2f7367183ec7ac7`）；仍只有 6 个 approved claims，但新增 2 个 approved cross-source syntheses。这确保“全语料知识骨架已批准”不会被误读为“3,752 条机器候选主张已批准”。初次审计时两份旧 `output/corpus-survey` 尚未取得删除授权；后续执行状态见本节末尾的 removal report。完整初次审计、删除候选与恢复资料位于 `$DATA_BASE_DIR/wang-knowledge-platform/deployment-reports/corpus-survey-migration-audit-20260816.json`（SHA-256 `adbd76fb891a86005149352605e843b2216f4278cf41c8c1cab3da33d1620f36`）。

### 已撤回的三篇局部刷新尝试（非范例）

只对来源 SHA 已变化的 `S 201101`、`S 210912 (3)`、`S 230521` 运行现行 `corpus_survey_runner`，继续使用它们原先绑定的 reviewed transcript。运行结果为 3 created、0 failed；三份新卡片均包含完整 generation fingerprint，并通过来源 SHA、segment locator 与逐字 anchor 的程序验证。旧卡片在写入新卡片前由 runner 自动复制到 `staging/corpus-survey/generations/`，三份旧 survey SHA 与 V1 `source-files.sha256` 完全一致。

三篇合计由 23 clusters / 46 candidate claims 更新为 24 clusters / 56 candidate claims。新卡片中 5 个模型引用未能精确抄录，runner 依既定规则把它们回退为完整 source segment 并留下 review warning；因此本次刷新状态是 candidate，不能据此批准 claims。V1 候选图引用的 5 个 claim IDs 在新卡片仍直接存在；`S 210912 (3)` 的 6 个引用仅从 `C002` 等零补位格式改为 `C02` 等格式。影响映射已记录，但 PostgreSQL 历史 V1 引用未被静默改写。

局部刷新以 `repository/research_corpus_snapshots/CORPUS-SURVEY-205-V2-PARTIAL-3/` 保存新旧卡片、影响映射与逐文件 SHA manifest。其 `manifest.json` SHA-256 为 `5cb80f01a3949a25c38ac4aae0672c4f681822e189c0ac751ff29df2de9126a9`，`source-files.sha256` SHA-256 为 `99869494af08d0a85b3a386f9143f3e375f02a85d644aacb5c8a390b76af577b`。它明确以 `CORPUS-SURVEY-205-V1` 为不可变 base，不宣称是 205 篇完整 V2，也未触发新的 full-corpus synthesis 或 PostgreSQL authority 更新。相关 corpus/path 测试为 49 passed；首次测试命令因缺少 `PYTHONPATH=.` 在 collection 阶段失败，修正环境后全部通过。

### 2026-08-16 撤回说明：survey 是一次性封闭快照

项目负责人随后明确：这份 survey 是一次性的 205 篇历史普查，不是随新讲道或后续逐字稿版本滚动更新的 ingestion pipeline。上节局部刷新因此属于边界误判，已完整撤回，不能作为后续操作范例。

撤回时先把三份刷新卡片、两份已完成的独立 AI review 和整个 partial-V2 目录保存到 `staging/corpus-survey/withdrawn-generations/2026-08-16-partial-refresh/`，然后从 legacy generation archive 恢复三份原始 survey。恢复后 canonical `staging/corpus-survey/` 的 265 个文件全部通过 `CORPUS-SURVEY-205-V1/source-files.sha256` 验证，0 mismatches；repository research release 区再次只包含 `CORPUS-SURVEY-205-V1`，其 manifest SHA-256 仍为 `22a230c2febc876f3ebcd5efe3cb1f3f1575f5f257eebdab7ac96567edcc81d5`。第三份独立复审在完成前即被中止，没有留下 artifact。

以后对这份 survey 的正确处理只有：保存、查询、解释其一次性知识结构，以及把已审核的结构决定留在 PostgreSQL。不得加入新讲道、不得因来源文件后来变化而重新抽取、不得把 withdrawn artifacts 送入 synthesis、PostgreSQL 或 Active Snapshot。三份后来出现的 source SHA 差异继续作为 V1 provenance limitation 记录，不视为待修复队列。

封闭性复核未发现 cron、launchd、API 或 web 自动写入入口。V1 旁已增加 SHA 绑定的 `closure-policy.json`（SHA-256 `3957c536bfb34c521f0da850e69816eaa9717668136ae10a840cae3fa11c1e1c`），明确固定 205 篇范围和允许／禁止用途；base manifest 本身未改动。完整 closure audit 位于 `$DATA_BASE_DIR/wang-knowledge-platform/deployment-reports/corpus-survey-closure-audit-20260816.json`（SHA-256 `e651aae1a378729d194443a1752aacc1460abecd1267538a5715dc197b286d32`）。当时两份 legacy `output/corpus-survey` 各 265 files / 6,984,124 bytes，均为 265/265 V1 SHA matches、0 mismatches，并已有 mode `0444`、通过 265 文件恢复验证的归档。

用户在这两个精确删除候选被说明为唯一剩余步骤后授权继续。安全层拒绝直接永久擦除，因此两份旧目录改以可恢复方式移入 `/Users/junyang/.Trash/corpus-survey-legacy-20260816-175900/`；原路径均已消失，全域扫描剩余 `output/corpus-survey` 为 0。Trash 内保留 530 files，同时独立只读归档继续存在。操作后再次验证 canonical V1 为 265/265 SHA matches、production health `ok`、公开文章 3 篇，未修改 PostgreSQL、Active Snapshot、代码部署或 reader-visible manuscript。执行报告为 `$DATA_BASE_DIR/wang-knowledge-platform/deployment-reports/corpus-survey-legacy-removal-20260816.json`（SHA-256 `c7d1e729b5370f336be6370277d0d468f5750da644560cdb5ce663e61f38d968`）。

## Sermon catalog projection 补充收口（2026-08-17）

后续 PR 审查发现两项 file-based Wang catalog projection 仍在统一 root 之外：`$DATA_BASE_DIR/sermon_catalog.json` 与 `$DATA_BASE_DIR/config/sermon_catalog_overrides.json`。它们分别以非破坏性复制迁入：

- `$DATA_BASE_DIR/wang-knowledge-platform/catalog/sermon_catalog.json`，SHA-256 `7690fe92d1cef03ab9c3e4203f69ef6caa4e187822d911cf72a2bf1171196c96`；
- `$DATA_BASE_DIR/wang-knowledge-platform/catalog/sermon_catalog_overrides.json`，SHA-256 `87ce92cf2c1d98b5cf199743e52f3b48abaac9808da3286d41e053ed2afc1141`。

两组 source/canonical SHA 均相同。PR #27 已让统一 path configuration、catalog builder、Matthew coverage read model、API catalog loader 与 watcher 使用 canonical 路径；相关测试为 60 passed。该 PR 以 merge commit `92d899e1dd6d2179866f76b495fe365f9b02f9a1` 合并并部署为 immutable release，旧 release `678249ae40259534cddae2318c8d47b73e65d554` 保留供回滚。

production LaunchAgent 已明确设置 `WANG_RUNTIME_ENV=production`；backend 与 frontend health 均通过。Matthew progress API 重新验证三篇 repository 文章全部 `production_visible=true`、SHA `consistent`、0 blockers。两项旧原件在建立 mode `0444` archive、完成 2/2 隔离恢复验证后永久删除；backend 在旧文件不存在的状态下重新启动并再次通过相同验证。完整执行与恢复记录为 `$DATA_BASE_DIR/wang-knowledge-platform/deployment-reports/sermon-catalog-path-cutover-20260817.json`（SHA-256 `ed95d7b3efa2f319100b13c50c378ac79102330238d1e7ff93fb8166f25c3a6a`）；archive SHA-256 为 `e40f4b1681d2a72c4f9288cfcd39608f1da459250c250ef1ffd229be0b624ed3`。
