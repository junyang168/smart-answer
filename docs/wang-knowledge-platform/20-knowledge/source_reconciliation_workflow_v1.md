# 来源修订 → Claim → CVP 更新流程

> **读者**：Solution architect、Developer、维护执行者。
> **类型**：流程。
> **状态**：当前；新通用入口，不追认或替换历史修订审批。
> **与代码对齐**：2026-10-07，核对通用执行器与 deferred Claim 入口的代码及离线回归；提议、Opus 审核、应用、回读、正常新 CVP 续接和完整组审计已实际运行。完整组遇到未授权异议仍停止，不能将 Claim 已修订当成 campaign 完成；性能验证改为真实保存输入的完整等价与耗时验证，不再把独立规模矩阵作为续跑前置条件。局部投影或合成测试不能放行生产。具体生产结果另存独立运行记录。
> **权威范围**：已授权来源版本变化及来源未变的 deferred Claim 修订，怎样传递到既有知识记录、观点连接和受影响分组；不授权新增 campaign 成员、路线、文章、发布或部署。

**交易与恢复改版**：新的目标设计见 [CVP 交易與恢復設計 v2](./cvp_transaction_workflow_v2.md)。本文的整库冻结、全 Registry 指纹／回读及历史投影描述保留为 v1 实现契约；v2 将由同一 transaction 的实际写集验证、SQL changelog 与分组状态接替，完成验收前不能直接关闭旧 guard。内容权限、指定模型、完整组审计与原失败预算继续适用。本次仅完成设计，生产仍暂停。

## 1. 更新原则

**增量写入，完整检查受影响的依赖。** 文件改了，不是只刷新 SHA，也不是重抽整库。
遵守 [Project Mission Statement](../00-overview/project_mission_statement.md)、[Solution Architecture](../00-overview/solution_architecture.md)、[Claim 设计](./claim_design.md) 和 [CanonicalViewpoint 设计](./canonical_viewpoint_design.md)。

来源校对由同工完成；本入口不编辑原文文件，不把局部校对说成整篇校对。标题等编辑内容不占 spoken-body 段落编号。
旧文件原始字节、旧知识版本、原模型回答和拒绝保留。文件 SHA、正文 SHA、逐字文本 SHA、段落与记录版本分别绑定，不能互换。

## 2. 输入与冻结

执行者构造 `source_reconciliation.Manifest`，程序验证，不要求 owner 人工审阅技术包：

| 字段 | 必须提供什么 |
| --- | --- |
| `source_id` | 现有 SourceDocument 身份，不按名称模糊寻找 |
| `before_source` | 完整旧逐字稿的绝对路径、`file_sha256`；必须与已登记旧版本相同 |
| `current_sources` | 完整新逐字稿副本的路径与 SHA；首份保留登记路径，各副本正文一致 |
| `authorized_paragraph_keys` | 明确许可的 S 段落；新旧变化不能越界 |
| `authorization` | 原始授权 JSON 的路径与 SHA；含稳定 `authorization_id`、`authorization_quote`、同一 `source_id`、精确 `permissions`、`human_content_approval=false` |
| `permissions` | 每个现有对象允许改变的字段；不使用 collection 白名单代替逐对象权限 |
| `groups`、`campaign_claim_ids` | 原分组顺序、全体成员、既有 CVP 与复审 flags；不得静默增删成员 |
| `accepted_membership` | 原已接受 grouping manifest 的完整文件与 SHA，不改旧接受产物 |
| `prior_updates` | 同一来源的上一份已完成通用更新包；最多一个直接前驱 |
| `created_at` | 本次输入建立时间；审核时间取真实模型响应完成时间，不能冒用此字段 |

新 fragment 或新 CVR 可使用 `create=true` 和确切 `parent_id` 分配槽位：仅属于当前 SourceDocument，或一个现有 CVP。
不允许新增 Claim/CVP 身份。CVP 正文变化必须新建 CVR，`revision=revision_number=1`，用 `supersedes_revision_id` 连回旧 CVR；不得覆盖旧修订。

冻结包含完整 Registry 快照、当前来源、完整反向引用及只读上下文。新旧物理原文另存 `source-versions/<file-sha>.json`，供下一次更新使用。
任务目录由来源新旧版本、稳定授权身份、成员文件 SHA 决定；时间戳、搬目录或 Registry 后续变化不能重置调用预算。

## 3. 完整影响清单与拟后像

1. 从来源和原引用出发，按精确 ID 枚举 fragment、Evidence、Claim、内嵌 anchors、Question、Observation、Position、关系、反向索引及 CVP 连接；未知 consumer 停止。
2. 将目标 CVP、其当前修订、其他成员及其完整自身来源放入只读上下文；别篇只能作对照，不能补为本 Claim 的来源证据。
3. 每个受影响记录恰好一种处置：`retain`、`update`、`impact_only` 或 `unresolved`。无变化就保留，不虚增版本；历史审核／产品／路线不自动重生成。
4. 授权新正文逐句建立独立分母。每句须绑定既有记录、明确背景处置或独立未纳入候选；不能用抽取结果自行定义“没有漏项”。
5. 新增、拆分或未覆盖判断留在 `unadmitted_candidates`：独立 ID、自身逐字引文、实际句子 ID；不加入冻结 campaign。
6. 验证 Claim → Evidence → fragment → 自身精确段落，双向索引及归属、否定、假设、主体、必要／充分条件。逐字匹配不等于语义已经正确。
7. 重叠 Claim anchors 保存同一 `evidence_independence.basis_group_id`，每组恰好一代表且 `count_once_per_basis_group=true`。link 的严格 `evidence_bindings` 不增字段，重叠项只留一个完整代表，原 fragment、anchor、旧 binding 版本保留。

来源变化不必修改 CVP 正文。完整自身语境只支持部分命题时，应给具体 supports 依据；不能硬造等价，不能为单一 Claim 自动合并、拆分或退役 CVP。

可扩展性是 [Solution Architecture](../00-overview/solution_architecture.md) 的硬约束，不因审核通过或程序结果正确而免除。反向 consumer 必须使用精确依赖索引；固定变更时，不得逐操作扫描整个 Registry 来找引用。

## 4. 提议、一次纠正、Opus 审核

固定顺序：GPT6.1Sol high **订阅提议** → Opus5.5 high **订阅独立审核**。
初审有合法内容异议，最多一次 GPT 纠正，再由 Opus 对实际纠正稿终审。没有额外 GPT 前置审核，没有第二轮纠正，没有“循环审到通过”。

完整 consumer 分母逐项覆盖，partial approved 不构成整包批准。包内含完整来源、拟后像和确定性 provenance 生成约定，不给运行模型整份架构规范。
实际请求固定上限 500000 bytes；仅完全相同文本／记录可无损编码，逐字段还原 SHA 必须一致，不摘要、删来源或拆分原 group。
新调用前须已提交 clean 实现、足够空间及规定 SSD 环境，再写不可重置 reservation；实际模型、后端、prompt、config、编码、原始响应与完成时间逐项绑定。合法 cache 零新调用；错误、额度或有 reservation 无回应立即停止，不自动重试。

新批准只绑定新版本：程序生成 fresh candidate／decision、准确单一 Opus 审核依据，并重绑最终 Claim SHA、当前 CVR 及 locator。
`owner_scoped_independent_terminal_review` 不是假双模型共识，更不是 human approval。旧批准只保留为历史。
有效人工 hold 不得清除；过时 legacy 要求也不能全域 ignore，须走其独立、精确的已授权处置证明。

## 5. 单次应用与回读

完整 Opus 批准后，使用真实 store planner 校验整个 live graph 与严格拟后像，生成 immutable plan。
原字段未声明即保留，不让 schema 新默认值变成清空旧字段的写入。真正改变的记录才递增版本。

先检查已提交 clean worktree、至少 2GiB 本机空间、规定的 Jun SSD 卷身份；真实完整备份及非空 `pg_restore --list` 通过，再写 apply intent。
SQL 写边界重新验证原文／审核／plan／backup，事务内强制完整 Registry 指纹与全局互斥锁。
一个 ChangeSet 原子应用精确操作，同时登记所有产品依赖失效原因；不只保留第一个原因，不在此流程重建产品。

回读必须匹配整个 Registry：所有已批准后像、未变记录和确定性依赖失效副作用；物理原文 SHA 仍相同。
若 SQL 已 commit、进程在回执前退出，恢复查真实 ChangeSet 并回读，绝不重 apply。有 intent 却不能证明 commit 时标为 `apply_uncertain`，不可盲重放。

## 6. 审计与完成证明

写入成功只到 `committed_waiting_audit`。随后 Gemini3.8Flash API 两 worker 审计所有受影响**完整原 group**，包括其中未改的 Claims 和所有当前目标 CVPs；不只审 changed rows。
不在 campaign 的受影响旧 Claim 单列审计，不改变分组分母。真正 Gemini content blocked 才可按既有条件一次 GPT 订阅补充判断，并保持 `revisit_required=true`、`independent_audit_complete=false`。

有效完整独立审计的语义异议，按 standing own-source GPT 一次终局规则处理；全部异议被否决且物理 proof 完整才可消费 final resolution。认可或未解决异议停止，不在此自动另开内容纠正。
完成证明绑定完整 audit job 分母、原文、准确包、真实 KCS、backup、readback 及所有物理 SHA；旧临时复审 flags 只能保留，不能升级为独立通过。

只有这条链完成才到 `audited_complete`。通用 epoch adapter 可只读还原原分组的旧成员 pins，并要求新作用域使用当前来源、当前 Claim 与新审核；这不是回滚数据库，也不借旧批准。
下一次来源变化以同一来源的真实通用前驱证明接链；缺少、歧义或冲突的 legacy 前驱不能自动转换成通用批准。

完成证明只在对应验证阶段建立一次，后续交接消费它，不再次调用建立函数。证明必须包含完整审核／来源／授权／编译器版本、实际 ChangeSet、已批准写集、准确回读与受影响依赖边界；历史内容不变时，不重建原审核请求或全库前后像。当前数据库中的相关版本、实际应用状态、物理来源与真实 holds 仍须重新核验，不能将历史证明当成当前批准。

历史快照的精确正／负读集可在显式维护阶段一次流式认证并物化为小投影；常规消费只读声明的投影，不回读整份历史快照。投影必须绑定原完成产物与快照的物理 SHA、精确读集、编译器和运行环境身份；缺失、代码漂移或读集变化时停止，不自动重新编译。归档 SHA 只说明这份历史资料的出处，投影本身不授予语义或当前 SQL 批准。新的准入验证器仍须独立证明当前来源、模型审核、版本、holds、实际应用状态和非目标完整性；精确 SQL 读集也只是当前观察，不得冒充全局完整性通过。

正常启动只校验已完成证明及待执行组的当前资格；不得同时运行独立冷启动 dry-run 和另一个重新冷启动的生产器。若需要首次重建历史证明，显式进入有预算、可停止、零模型零写入的维护阶段，保留失败与产物，不静默回退或循环重建。

性能验证以真实保存输入为主：优化前后完整结果逐字段一致，并测实际冷进程与热进程的读取量、SQL 行数、重建次数、内存和耗时。正常续跑与组后机械消费不得解析历史整库快照，不得加载所有 active rows，也不得重复执行已完成的历史证明。正确性反例仍覆盖来源／代码／审核变更、缺失证明、歧义、真实 hold、SQL 未提交、重复 intent 和非目标漂移。局部函数加速或合成测试不得冒充完整流程验证。

容量规划基准为 1,000 个独立注册的讲道来源，母本、编辑视图与历史版本另计，具体规划见 [Solution Architecture 的质量属性](../00-overview/solution_architecture.md#2-质量属性)。规划不增加本次独立规模验收，也不新增内容权限；全部成员、来源、限定和原有上限保持，不为压低时间或请求大小而抽样、截断或缩减审核分母。

续跑启动器必须先消费与当前实现文件逐一绑定的真实保存输入验证记录，缺失或过期时，在冷预检、备份和后台启动之前停止。记录覆盖启动和组后证明消费的冷／热路径，并验证完整结果与保存的原始结果一致；一个工作负载只使用一个持续运行的验证器。既有启动接口名称保留，但不再要求 1×／10×／100× 合成增长矩阵；仅逻辑测试或局部演算法加速记录仍不能放行。

常规机械阶段还须有读入量与时间预算，包含实际文件认证、解析和阶段结束的重新认证。超预算作为程序设计问题停止，不解释为内容拒绝，不清理旧证据或预算，也不自动改用无上限重建。此护栏只是限制故障成本，不证明流程已达到可扩展性要求；模型调用、独立语义审计和新备份的时间单独报告。

## 7. 执行与状态交接

从专用 worktree 使用 `backend/.venv`，`.env` 中的绝对 `DATA_BASE_DIR` 决定 runtime 目录；与已有维护 `recovery.lock` 非阻塞互斥。

```bash
backend/.venv/bin/python -m backend.pipeline.source_reconciliation_runner --manifest /absolute/manifest.json --prepare-only
backend/.venv/bin/python -m backend.pipeline.source_reconciliation_runner --manifest /absolute/manifest.json --apply --audit
```

第一条零模型、零 Registry 写入；第二条在既有 bounded 授权内执行完整流程。省略 `--apply` 只审核不写库，省略 `--audit` 不宣布完成；恢复使用原 manifest、原任务，不换 root 绕预算。
无监测文件变化即自动授权的 watcher，也没有 campaign／路线／文章／发布／部署入口。生产续跑是读取真实完成证明后的独立操作，不由本入口启动。

`state.json` 明确 `status`、`waiting_for`、`next_action`、`production_resumed=false`。
`system` 表示程序故障／证明恢复，`operator` 表示执行者未启用应用阶段，均不要求 owner 重复审稿；`owner` 只用于真实内容未决／新范围／模型错误或额度等停止。
不能把后台启动、CLI 0、审核批准、apply receipt 或部分审计说成完成。未完成 reserved audit 仅复用已完整物化的结果，不猜测性重新调用。

## 8. 实现与适用边界

### 8.1. deferred Claim 自动修订入口

`backend.pipeline.deferred_claim_repair_runner` 将已保存的来源调查作为诊断而非批准，进入同一个通用 Claim 事务。入口区分「自身原文已有支持但证据链缺绑定」与「Claim 措辞／归属需要修订」；证据已经充分但缺 CVP 的情形返回现有 CVP 解析，不另建生成流程。

此入口不改物理来源、不增删 Claim 身份或原分组成员。冻结目标 Claim、全部 Evidence／anchors／反向 consumer、已有 CVP 连接、当前 CVR 和其他成员的完整自身来源；未知 consumer、真实 human hold、来源冲突停止。证据补绑定模式不改 Claim 文字、连接谓词或 locator 范围；措辞修订模式仍逐一审核全部受影响连接，不自动改 CVP 正文或合并、拆分、退役观点。

提议使用 GPT6.1Sol high 订阅，独立审核使用 Opus5.5 high 订阅；最多一次 GPT 纠正和一次 Opus 终审，不增加 GPT 前置批准。程序在审核前确定性计算引文哈希、重叠引用单一依据标记及拟 Claim locator SHA，完整拟后像交给 Opus；原始模型回答不覆盖。每个 Claim／来源版本／问题类别共用跨 root 的一次调用预算，已有 intent 无回答视为结果未知，不自动重试。

审核后仍走本流程的备份、单次应用、全库回读与受影响完整组审计。`audited_complete` 只证明 Claim 修订完成；deferred 成分必须以当前 Claim 和新证据返回原有 CVP 覆盖解析，再经完整组审计和 completion 才能推进后一组。原 scope／ledger、旧审核和已应用 ChangeSet 均保留，不重放。

`--complete-group` 启用 `deferred_claim_cvp_continuation` 衔接：直接调用现有 `run_batch`、v6 覆盖审核及标准 compiler，不另建缺 CVP 提议器；只追加未覆盖成分，保留已有连接。独立 completion verifier 核对原批次、Claim 修订和正常 CVP 增量的真实 SQL／原始审核／完整 Registry 逆向边界；完整原组审计及后组备份、dry-run、实际 continuation receipt 通过后才恢复队列。其他旧连接的新异议不属于本 Claim 自动权限，必须明确停报。

负责人明确批准精确修订后，后续动作由代码完成，不逐阶段等待新的「继续」。`owner_approved_link_correction` 将独立审核、备份、单次写入、全库回读、完整原组审计、原有 CVP 覆盖解析和安全续跑接成持久流程。当前契约仅允许获准的一条全文 locator 的 `equivalent_component` → `equivalent_full`，不授权未来其他连接或正文自动修改。全文连接按既有 schema 使用空 locator；旧全文定位、原审计和仲裁仍保留。

子修订有独立持久调用／写入 intent 和 SQL store gate；新 Opus 拒绝、未知调用结果、新内容范围或证明冲突停止，不重复调用到通过。来源完成证明消费精确子修订后像并反向验证原来源事务，不放宽旧写入／当前版本边界；旧审计按已验证历史切面重建，不能用已修改的当前连接伪称原输入，也不将旧异议改成 pass。新完整原组审计真正通过后才完成来源证明并接回现有队列。离线验证和真实运行结果分别记录。

实现分别在 `source_reconciliation.py`（契约／完整图验证）、`source_reconciliation_runner.py`（持久阶段）、`source_reconciliation_runtime.py`（订阅／SQL／备份／审计）及 `source_reconciliation_epoch.py`（完成证明消费）。
测试为 `backend/tests/test_source_reconciliation.py`；既有来源修订与分组／store 回归仍须通过。
v1 主来源只处理可重建 spoken-body 的 JSON 逐字稿，不支持视觉内容、缺失旧原文、未知 consumer、未证明历史链或有效 human hold 的自动放行。超过请求上限也停止，不降低门槛。
新入口的离线测试、历史包只读重放与未来真实模型／SQL 运行必须分别报告；测试通过不是自动生产批准。
