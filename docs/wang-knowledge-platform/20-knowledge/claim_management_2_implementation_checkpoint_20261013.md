# Claim Management 2.0 实施检查点

> **讀者**：Developer、Solution architect。
> **類型**：記錄。
> **狀態**：當前。
> **與代碼對齊**：未核對（实施中，不代表完整验收）。
> **權威範圍**：记录本次实施进度与局部来源修订设计审阅；规范以 [Claim Management 2.0 设计](./claim_management_2_design.md) 为准。

## 已完成的隔离验证

工作分支 `wkp-435-claim-management-design` 使用共同 SQL 基线 `9eeb64d22397c1f7ca03dc4344051e71f11d514e`；依赖 PR #428，工作 PR #437 仍为 draft。共同入口、store、独立审计器的 v2 扩展以及内容契约已接入隔离测试，未执行正式内容操作。

本次选择的 Claim v2、真实保存失败材料、共同 SQL transaction、局部来源绑定、独立来源读取及旧 source 流程测试结果为 **314 passed、43 skipped**。使用 `backend/.venv` 和临时 socket-only PostgreSQL；模型输出由合成回复提供，不能算正式独立内容审核。保存失败材料只读，未重跑旧模型、重开旧预算或改写旧 raw。

覆盖了 Create、Review 报告／当前核实、Update 既有候选与多轮修订、Retire、Restore、Split、Merge；提交后独立审计拒绝保留实际 applied 事实；未知调用停止重发；操作预算在准备时预留全部角色。真实失败反例还覆盖完整自身来源、共享只读 owner、遗漏限定、原文疑点与程序计算 fragment hashes。

本轮新增反例包括：Claim 行未变但出现新关系时旧资格失效；引文段落未变但另一段新增限定时旧来源资格不能静默沿用；正式 Review findings 原子保护当前资格并标记已审核消费者待处理。完整存储状态与历史保持可查。

## 局部来源修订的 Fable 审阅

实际 `fable` 调用解析为 `claude-fable-5-1`，effort `medium`，无工具、无内容执行。原答要求：影响审查归属现有 Review action；其分批调用共用原预算；来源交接只有一份段落对应；“无影响”与当前资格分开；实际依赖查询须覆盖间接与跨来源 owner；来源注册行／影响登记的 DB transaction 与物理文件边界分清。

上述建议已纳入设计第 9.2 节。未采用“不升语义 revision”的建议：现有 store 只有 storage revision，实际锚点／绑定字段改变必须进入 ChangeSet 和版本历史，再由精确机械迁移及来源适用性复核证明审核复用。没有新增第二套 revision。

实际审阅原件暂存于 `/var/folders/s0/899st79150z63778jn0381pr0000gn/T/claim-435-source-impact-review-h8gh59u_/`，包含 request、原始 provider 回复、metadata 和 review。此路径是本次运行记录位置，不是后续准入或完成证明。

## 尚未完成的实施与验收

以下工作仍开放，不能据上面的局部测试关闭 #435、宣称完整交付或启用正式生产维护：

- 将第 9.2 节的双版本来源交接、影响 Review policy、旧审核适用性复核事件、独立新旧原件读取和当前消费接通。现有机械换 SHA 不能代替这些判断。
- 接通一次有据澄清、原角色复核、分歧暂停及有据修正的恢复；现有执行已支持实际多轮修订，但尚未实现完整澄清出口。
- 对后续 action 保留原未决、历史分歧、原结构映射与已提交事实；完成稳定 findings 的跨 action 对账。
- 完成所有当前消费者使用同一 Claim-ready 规则；正式 Review 的产品依赖保护、资格的当前问题范围与审核字段同步仍需共同验收。
- 验证共享只读依赖跨单元变化时当前证明的完整覆盖；原 Update 获准结构修正的最终目标；Split／Merge 后修订及补偿的完整路径。
- 以真实 CVP／其他消费者验证退役的持久 pending 状态、未知字段引用阻断、Restore edge 完整后像、rollback、lost reply 与非目标完整性。
- 核实实际订阅传输的输出资源限制与资源错误分类；模型配置 fingerprint 不能冒充 provider 已执行的硬上限。
- 完成代码审阅与全路径验收，再更新 draft PR 的交付说明及 issue 关闭关系。

文档检查仅有已知的无关 `docs/notes-to-sermon-agent/functional_spec.md:120` 缺失附录锚点。前端 type-check 因现有安装缺 `react-quill-new` 在 EmailSender／FellowshipManager 失败；本次没有前端改动。另一批显式启用的旧保存输入测试仍有依赖基线上同样存在的失败，未将这些结果列为通过，也未修改历史材料掩盖它们。

## 后续来源影响实施与收敛

第 9.2 节的来源影响 Review 已接入现有 Claim operation、共同 transaction 和独立审计路径。完整新旧原件与实际 SourceDocument ChangeSet 必须匹配；分母包含没有旧审核的 owner。无影响结论与资格分开，有完整旧内容审计且必要当前依赖有效才写 source_revalidated 事件；缺审核者明确列入 pending_content_review_keys，不改变 Claim payload 或 revision。其他段落新增限定、审阅分歧、重复段落不能猜对应等反例已加入隔离测试。

实际 Fable 工程审阅指出旧 schema／prompt 复用、跨批归属、依赖 finding／片段绑定和重复 baseline 计算问题。实现保存当轮实际 audit instructions／schema 与代码绑定，只给本单元原目标登记来源复核，未决依赖和失效当前片段不能隐藏在 unaffected 结论中。旧依据与审计实际 pins 不同则不授予复用资格；损坏的已绑定原件仍停止，不吞掉篡改错误。原答暂存于 `/var/folders/s0/899st79150z63778jn0381pr0000gn/T/claim-435-source-code-review-hyeke9cp/`。

负责人提醒不要随 review 堆机制后，本轮合并为一份 source_transition 交接，影响 Review 和明确关联它的后续维护共用；没有另设 source_history。旧 baseline 在 action 中只计算／保存一次，每份不同的旧审核在该维护中只验证一次。各 unit 从同一原目标派生必要范围，不再复制整份 action 原始对象与所有单元目标。沿用既有 pipeline_runs、review_events、ChangeSet 和运行器，没有新增数据库、状态机或审核框架。

扩大后的相关测试为 **413 passed、43 skipped**，包括只读启用的七组真实旧失败材料、Claim operations、共同 SQL transaction、旧 Claim authority、独立来源坐标与 source reconciliation。旧 authority 测试替身补齐新的 store 查询接口，并新增当前 v2 失败／陈旧证明不得回退历史通过的反例。测试仅使用临时 PostgreSQL 和合成内容模型回复，旧失败材料逐字节保持不变。

这仍是实施检查点：有据澄清与暂停恢复、跨 action 未决对账、所有下游消费及结构补偿等上节剩余事项尚未完整验收。未推送这轮改动、未合并、未启用正式内容操作或部署。

## 重复实现与校验的简化验证

本轮编码没有调用 Fable。删除了仅测试使用的预算预检副本；预算由实际 SQL round reservation 统一判断，新增零修订预算的完整 Update 反例，确认 Opus 拒绝后停止且不发 GPT、恢复不新增调用或修改 Claim。删除第二个语义 fingerprint，测试改用实际执行使用的 candidate_state。Opus 收据、提交准入和 Gemini 结果共用同一 review 通过判断，来源影响的专有问题在这个入口内处理，不再由调用方覆盖一次通过结果。

Audit 收据委托独立结果校验时，只读取一次实际来源；原先重复的来源／policy／candidate 检查已移入该路径，伪造来源材料仍被拒绝。来源旧审核适用性检查也将依赖计算移出逐对象过滤循环，不依赖新增缓存、框架或状态字段。

相关完整回归仍为 **413 passed、43 skipped**，含七组只读真实失败材料。与 `e621fbb` 的两个对照另外验证：同一保存 audit 收据返回的完整结果 SHA 相同，来源校验调用由 2 次减到 1 次，读取原文字节由 172 减到 86；临时 PostgreSQL 中的同一来源维护输入，baseline 完整结果 SHA 相同，依赖计算由 5 次减到 3 次。后者包括有旧审核的 C 和缺旧审核的 D，仍只允许 C 复用。

上述冷进程单次 audit 对照耗时为改前 0.008697 秒、改后 0.012934 秒，峰值为 1,379,029／1,401,512 bytes；小输入没有测出时间或内存改善，不能据减少重复读取宣称完整流程变快，更不能当作 1,000 篇容量验收。对照脚本和结果位于 `/var/folders/s0/899st79150z63778jn0381pr0000gn/T/claim-435-simplification-8ou3di38/`，仅用于本次记录，没有加入运行器或生产证明体系。

本次只提交简化和验证，整体重构仍未完成；未修改正式数据、未合并或部署。

## 人子单条实测与 Argon 接入

目标为未连接 CVP 的 `DK-4eca17ff4e50fa50aa01-P01-CL002`。在真实数据的隔离 PostgreSQL 复制库中，首次 Opus 认可该 Claim，但报告只读上下文 Claim 引用的反方 Position 未进入审核包；已补齐引用依赖。修复后 Opus 通过，独立 Flash 返回的一个对象 ID 将 `P05-CL013` 写成 `P01-CL013`，结果被程序拒绝。加入输出前自检后，Flash 在相同完整材料上仍犯同一错误，未替换 ID 或追认为通过。

负责人指定 Gemini 4 Argon 后，API 模型清单确认 `gemini-4-argon` 可用。同一份完整 prompt、schema 和 132 个对象经一次真实 Argon 调用全部通过，ID 无遗漏、重复或清单外项，没有内容 findings。三个独立审核原答均保留于单条隔离试验的 `operation/CMR-*/` 下；Argon 对比结果位于 `gemini-4-argon-comparison/comparison-result.json`。正式库 payload 保持不变，比较结果没有冒充原 Flash policy 下的正式完成。

开发分支随后加入版本化 Argon policy；独立审计模型按原 policy 精确核验，旧 Flash policy 不变。正式审计子进程改用当前 checkout 的 backend interpreter、独立 auditor 和实际 store database_url，不再进入 #357 固定 checkout。备份复用已有 SSD 校验及 SHA／恢复目录证明，#435 写入 SSD 的 `registry-backups/435/`，#357 备份和迁移范围保留。只读上下文进入完整审核和 pins，但不因另一 Claim 的 Update 获得新资格。

本次相关回归为 **442 passed、43 skipped**，含只读启用的真实旧失败输入；新反例验证 Argon 与 Flash 不能互换证明、单条 Update 不授予只读关联 Claim 资格、完成恢复不重发模型、审计进程不能读取错误数据库或固定旧 checkout、#435 SSD 备份不能进入 #357 迁移，以及损坏备份拒绝消费。新增自检段落后，隔离模型替身的 prompt 分隔方式同步为读取最后一行 packet JSON；真实传输仍消费完整多行 instructions。

此记录对应正式单条试运行前的开发验证；整体 #435 尚未完成，没有启用整组批量维护或部署。

## 正式单条完成与移除业务备份门槛

`435-son-of-man-first-formal-argon-v1` 已在正式库完成。目标仍是 `DK-4eca17ff4e50fa50aa01-P01-CL002`，仅允许 Update 这一条未连接 CVP 的 Claim。Opus 与 Argon 各调用一次并通过；round 为 audited／completed，原 action 为 completed，目标当前资格确认通过。审核认为原有内容可以保留，实际内容写集为空，没有创建内容 ChangeSet；只读对象及其资格保持不变。完成状态重入没有增加模型调用。保存输入、原答与最终结果位于运行资料目录的 `wang-knowledge-platform/staging/claim-management-435/son-of-man-first-formal-argon-v1/`，最终记录是 `single-update-result.json`。

负责人指出数据库 transaction 与版本历史足以承担 Claim 业务保护后，移除了 Claim action 的整库备份／SSD 前置要求；共同执行和恢复只对明确的 Claim source_kind 使用此契约，旧 CVP／来源维护契约保持不变。事务原子性、精确 pins、实际写集、版本历史、回读及独立终审没有取消。删除新增 #435 备份创建和 shell 分派；已在途旧实现生成的一份备份保留真实记录，仅保留其旧逻辑路径与 SHA 的读取兼容，不再为 Claim 创建备份。

临时 PostgreSQL 的全路径测试把 Claim backup port 改为一旦调用就失败，Create、Update 修订、Review 确认、Retire、Restore、Split、Merge 均通过。新增末项验证失败的完整回滚和提交回复丢失的恢复反例，验证内容仅提交一次、审核不重跑、独立审计完成后才授予资格；同时修复了工程恢复完成后仍返回 parent 旧 paused 结果的问题。SSD 不可用不再阻止 Claim 的模型启动，旧来源流程的 SSD 门槛仍保留。

移除业务备份门槛后的相关回归为 **444 passed、43 skipped**，包含保存的旧失败材料和共同 SQL 执行／恢复。文档检查只有既有的无关 notes-to-sermon 附录锚点错误。整体 #435 的其他开放事项仍保留，未扩大整组维护、合并或部署。

## 暂停 Gemini 内容终审（2026-10-14）

第二条 `DK-4eca17ff4e50fa50aa01-P01-CL003` 在暂停前也已完成正式 Update：Opus 与 Argon 各一次通过，现有文字保留，其他对象及第一条资格不变，没有备份，完成重入没有追加模型调用。记录在 `claim-management-435/son-of-man-second-formal-argon-v1/single-update-result.json`。

负责人随后要求暂时 disable Gemini。新任务默认 policy 改为 `wang_claim_management_opus_only_v1`，明确绑定 `independent_audit=false` 和 Gemini 预算 0。Opus 审阅后沿原有事务和回读路径核对实际 SQL 与获准完整后像，以现有阶段收据保存 `opus_and_program_readback`；不建立新运行器或状态表。当前资格仍检查来源、版本、holds、范围和原始目标，但明确 `independent_audit_complete=false`，不伪造 Gemini 结果。

旧 policy 未声明开关时保留原语义和序列化形状，已完成的两条 Argon 任务及其 raw、调用预算和独立审计记录不改写。此修改没有启动新模型或内容写入。

相关回归为 **451 passed、43 skipped**。新增临时 PostgreSQL 用例覆盖关闭 Gemini 后的 Create、Update、Retire、Restore、Split、Merge，Gemini port 一旦调用即令测试失败；验证不预留 Gemini、真实 SQL 后像回读、当前资格明确标为 Opus／程序依据，以及完成重入不增加模型调用。旧独立终审、零预算拒绝和真实旧失败材料仍通过；文档检查仅有已知的无关 notes-to-sermon 锚点错误。

## 三条并行试运行后的错误修正（2026-10-14）

`son-of-man-three-opus-only-v1` 并行运行 CL004、CL005、CL008。三个 Opus 回答均认可目标 statement，但其他记录有 findings。CL004、CL005 进入修订后，Codex 接口以 HTTP 400 拒绝输出 Schema：开放的 `replacements` 字典没有固定 properties；开放 Row payload 同样不适合该严格 Schema。CL008 因只读 OBS009 的原文疑点暂停。三条均未完成，原内容均未改变；Gemini 调用和全库备份为零。旧 raw、调用预留、预算及暂停记录保持原样，没有自动重发。

本次修正复用现有 JSON records 传输做法：GPT 的 `payload_json` 和 `replacements_json` 无损解码到原 Candidate，再执行原权限、来源、处置、SHA 归一化和完整后像检查；重复 JSON 字段拒绝。通过实际 adapter 请求路径验证输出 Schema 的每个 object 都封闭且列全 properties／required，不以替身直接绕过传输 Schema。

普通 findings 增加目标影响清单和具体解释。当前任务无关的只读问题进入现有运行结果；目标或实际写项问题不能豁免。直接被拒的只读 Claim 在精确当前版本上保留正式异议，另一目标可独立完成；Review 报告模式同样保存异议。真正依赖原文疑点时仍转人工，缺少具体人工问题则作为契约错误拒绝。必要修复对象未授权时，在 GPT 调用前报告 `permission_required` 和具体对象，不为迁就错关系改写正确 Claim。

资料包确有漏载：原目标 132 项，三条本轮包各只有 117 项。Claim 读取与提交／完成范围核验改用已有关系索引及精确字段查询，补全前向依赖的入向关系和共享 owners；下游消费者仍进入事务 pins。CVP 带入的其他成员不成为当前内容闭包的新根。Create 和来源影响 Review 保留原来的来源覆盖读取要求。通用反向查询中的大型递归 OR 改为等价的小 JSON equality 路径数组，保留原 named-reference 过滤，不改内容语义。

实库只读验证三条当前分母都为 132 项，与原目标相比无缺项，分别约 2.69、2.74、2.64 秒；旧 CL002、CL003 当前资格仍有效。没有重跑真实内容模型，也没有内容写入。相关回归为 **457 passed、43 skipped**，覆盖完整 Schema 传输、无损未知字段、重复 JSON 拒绝、独立只读问题、真实阻断、权限不足不调用 GPT、Review 报告异议保留、入向依赖闭包及旧失败材料。文档检查仍只有已知的无关 notes-to-sermon 附录锚点错误。整体 #435 尚未完成；这次三条旧 action 的显式恢复未执行，不把代码回归通过说成它们 Update 完成。

## 明确授权的工程接续准备（2026-10-14）

负责人要求三条继续运行，随后明确改为逐条运行。旧 action 已冻结修复前的实现，直接调用不能重新解释旧输入。接续使用原运行表中的关联记录：新授权精确引用 `predecessor_action_id`，继承旧原目标、未决与调用事实，扣除已完成生成，并禁止原 action 再调用或分叉接续。只允许尚无获准／已提交写集、目标与来源未变的 Update 工程输入接续；已开始但不确定的调用仍禁止重发。旧 HTTP 400 Schema 拒绝须有密封的真实失败回答、无 last_message／stdout、明确 invalid_json_schema／text.format.schema，才能证明未生成内容；失败尝试原样保留，不冒充成功调用。接续的预算与模型／审阅 policy 不得比原余量增加或改变。

旧问题仍存在但不影响当前目标时，原审核角色可以在 `prior_findings` 中保持 persistent 并说明无目标影响；问题保存在上下文结果，不谎称原文冲突已 resolved。接续候选仍由 GPT 提出，再由 Opus 据完整当前原文审核，不由 Codex 手改 Claim 或关系。首条允许修旧审核指出的关系类型／理由及共享证据摘要，仍不授予其他 Claim 或 CVP 内容写权限。

准备阶段相关回归为 **459 passed、43 skipped**，新增原预算承接、生成前 Schema 拒绝证明、未知／部分回答拒绝、旧执行入口禁止重发、接续完成重入零追加调用及持续只读疑点披露。此记录是接续准备，不声明三条真实任务已完成。

## 已通过模型结果的事务 readset 恢复准备（2026-10-14）

CL004 接续的 GPT 与第二轮 Opus 都已返回，Opus 审阅通过。GPT 约 582 秒、请求 456,961 bytes、回答约 65,745 bytes；Opus 约 134 秒、请求 476,434 bytes、回答约 28,077 bytes。候选仅改 CR005、ER023 和 E032，目标 Claim statement 保留。提交前被共同事务检查挡住：已固定 458 项，而通用消费者闭包需要 649 项，缺项来自 CVP 的事务上下文，没有内容写入。

新增程序恢复入口只扩展这份已获准、未提交的交易 read pins：核原 pins 全部仍有效、Model 分母和事实完全不变、当前包仍有可重构的 GPT／Opus 原答，保留原 plan、候选、原模型收据和原 reviewed 状态的完整归档，记录精确新 pins。已经变动的内容或扩大内容审核分母不能用这个恢复入口。临时 PostgreSQL 验证恢复后原计划完成，重入不增加 GPT、Opus 或 Gemini 调用；此处不声明实库提交已完成。
