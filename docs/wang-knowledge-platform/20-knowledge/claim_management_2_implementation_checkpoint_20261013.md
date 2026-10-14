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

## 三条逐条接续完成（2026-10-14）

CL004 已使用原 GPT／第二轮 Opus 结果完成事务 readset 恢复并提交，未追加模型调用。ChangeSet 为 `KCS-aebdcd61672ed4572ad8`，仅修正 CR005、ER023 和 E032；目标 statement 保留，当前资格通过。原提交失败记录与恢复后结果分别保存，没有覆盖失败历史。

后续 `31a78a2` 在常规准备阶段补齐与提交相同的消费者 pins，并把模型输入里的相同原快照改为引用完整当前表。程序验证引用展开后逐项等于原事实；原事实不同的旧状态和完整原文仍独立保留。以 CL004 保存输入对照，模型包由 455,776 bytes 降至 217,601 bytes，约减少 52.3%，不据此推定模型耗时比例。接续可以先将现有内容送 Opus，避免没有修改需要时调用 GPT；旧 policy 序列化与已保存运行包不变。相关回归为 **461 passed、43 skipped**。

CL005 第二轮 Opus 通过，实际写集为空。提交后读回一度出现 PostgreSQL statement timeout：代码把整个内容包里的依赖记录全部作为消费者闭包起点，扩大了查询。按原授权目标与实际候选读取完整闭包，再由普通 readback 收据事务校验所有真实后像完成恢复，没有追加模型调用。`becd12b` 把相同根范围修复进入常规流程；相关执行与来源影响回归 **41 passed**。CL008 随后沿修复后的常规流程完成，也没有内容修改。

三条 action 的 `*-continuation-20261014-v1` 均为 completed／qualification_confirmed，round 为 audited／completed；CL005、CL008 各仅新增一次 Opus 调用，未调用 GPT。三条的 Gemini 调用和全库备份均为零。CL005、CL008 最终报告保存于 `son-of-man-three-opus-only-v1/CL005/continuation-20261014-v1/continuation-result.json` 和对应 CL008 目录；CL004 恢复结果为对应目录的 `continuation-result-after-readset-recovery.json`。

CL008 的 OBS009 原文疑点保持 persistent，原答说明它不影响 CL008 的证据与 statement，保存在 context_findings 中；具体人工问题仍要求核对 S0004／S0006 的录音及文本层次，没有虚称已解决。CL004 修正共享依赖后，CL002、CL003 的旧资格当前返回 `current_independent_qualification_required`；内容未改，原资格事实未删除，但不能宣称当前有效。此次未擅自重审或授予这两条新资格。整体 #435 仍未完成，未合并或部署。

## 新两条按顺序实测（2026-10-14）

负责人要求再测两条、一次一条，并避免逐案堆补丁。选择未连接 CVP、尚未运行本轮维护的 CL006、CL007；运行冻结于 `b2b6875`，CL006 返回后才启动 CL007。记录保存在 `claim-management-435/son-of-man-two-sequential-20261014-v1/` 两个目标目录的 `single-update-result.json`。两条各实际调用一次 Opus，没有 GPT、Gemini 或全库备份，均无内容写入。

CL007 Opus 没有 findings，普通事务／readback／原目标完成检查通过，资格为 ready；完成重入结果一致、调用预留不增加。CL006 的 Opus 报告目标 statement「猶太學者」比讲道的「對希伯來文很強的學者／希伯來文專家」多了译者身份限定，建议贴近来源修正。另报告 S0004／S0006 冠词疑点不影响 CL006 的主张，但拒绝 E006 和 OBS004。正式 finding 只绑定 E006，OBS004 仅在该 finding 的 component／reason 与自身 verdict 中被提及，未获同 record_key 的独立 finding。因此程序拒绝这份不完整 Review，action 为 engineering_required，未执行 GPT 修正或提交；不把内容意见当作已接受的审阅收据。原 raw 及实际调用事实保留，不能把调用状态仍为 started 解释为没有调用或重新发送许可。

这是通用「对象 verdict 与正式 finding 的逐项对应」问题：不按 CL006／OBS004 特判，不让自然语言提及充当另一对象的结构化问题，也不放宽来源疑点处理。审阅 prompt 现在明确要求每个 rejected record_key 有相同 target_key 的 finding 或该对象的 persistent／reopened prior_finding；同一问题涉及多个对象须逐个登记，affected_target_keys 仍只表示本轮 Claim 目标影响范围。错误信息列出缺项 ID，新增反例验证 prose 跨对象提及不能满足契约，补齐逐项 finding 也仍保持内容拒绝。相关回归 **462 passed、43 skipped**。

此修改仅验证新 prompt 契约与现有严格校验，未重新调用 CL006 模型，也未人工复制其 finding 冒充新原答；CL006 仍未完成。两条的内容审核材料分别为 21 项与 35 项，对应 Opus packet 文件约 70,603 bytes／89,953 bytes，没有强制把整篇其他 Claim 带入当前审核分母。旧冠词疑点仍待人工核对；整体 #435 未完成，未合并或部署。

## 审阅问题登记与多轮状态的同类漏洞检查（2026-10-14）

负责人要求检查 CL006 以外的类似漏洞。本次检查共同 Review 校验、问题登记、多轮输入与完成输出，未调用真实模型或修改正式内容。反例确认三类缺陷：同一稳定 finding 重复输出可覆盖登记；显式有问题的对象可同时给出无问题通过 verdict；上一轮只读 context_findings 没有进入下一轮 unresolved，已解决的问题可能继续留在 parent 最终输出，反之上下文问题是否已转成目标阻断也未要求重新判断。

修复继续使用原 finding identity 和 SQL metadata：新 findings 内不准重复同一稳定问题，也不能把已经进入 prior_findings 的旧问题另写一条 new finding；普通内容审阅中，显式 scope 的新问题或 persistent／reopened 旧问题不得与该对象的无问题通过 verdict 同时出现。旧记录缺 scope 字段时保留原保守阻断语义；来源影响 Review 的 approved 判断影响结论，仍沿用它的专属契约，不误套内容通过语义。

后续轮次的 unresolved 合并当前 unit 可见的 blockers 与 context_findings，要求原审核角色逐项说明 resolved／persistent／reopened。问题真正解决或转成 blocker 时，只清除 parent 当前上下文清单中的对应条目，旧轮次、raw 与历史问题收据保留；其他 unit 的上下文问题不删除。没有新增状态表、框架、审核角色、模型预算或按 Claim ID 的特殊逻辑。

临时 PostgreSQL 三种出口验证：resolved 后完成结果不再显示该上下文问题；persistent 且不影响目标时，目标可以完成，但上下文问题继续显示；同一问题转为影响实际目标时，不再显示为无关 context，并在无修复权限时返回 permission_required、指明所需对象，未提交新候选。另有反例拒绝重复稳定问题、新／旧问题双重登记，以及 finding 与无问题通过 verdict 自相矛盾。相关回归为 **468 passed、43 skipped**，含共同 transaction、恢复、来源影响与保存旧失败材料；文档检查仍仅有既有 notes-to-sermon 附录锚点错误。

CL006 的原不完整回答仍保留，状态未改成通过，也未自动重发。真实模型回答违反契约后需要保留原答并沿合法接续处理，这项工程恢复仍与内容修正分开；prompt 自检不能保证模型永不犯错，本次不声称已真实恢复 CL006。整体 #435 尚未完成，未合并或部署。

## CL006 接续、异议回应及提议者疑点的边界（2026-10-14）

负责人要求继续，强调 Opus 只是提出异议。proposal prompt 现明确允许 GPT 据完整自身原文否决异议、引用原话说明 retain，不把 finding 或 acceptance_condition 当修改命令；语境概括不要求原文每个词逐字连用，也不准以外部史实给当前来源新增独立结论。Opus 仍须独立判断回应，不以 GPT 或负责人的意见代替内容审核。

CL006 原回答的工程接续以 `18e1d6b` 开始。原 Opus raw、请求、预留及冻结 role prompt 经过共同传输校验，原回答确属已返回但缺少对象对应 finding 的程序契约错误。那次生成计入已用一次 Opus，不把 started 标记解释成未调用；旧模型结论没有追认为通过。关联 action 继承旧目标、全部原答和已声明的问题，只使用原余量的一次 GPT／一次 Opus。临时 PostgreSQL 覆盖剩余预算限制、旧入口禁止重发、原回答与 prompt 篡改拒绝、接续完成重入零调用；该阶段相关回归 **469 passed、43 skipped**。

本次 GPT 实际没有否决措辞异议，而是提出将 CL006 statement 中「猶太學者」改成「精通希伯來文的學者」，其余 payload 保留；Opus 审阅认可这份候选。GPT 同时重复报告 E006 的冠词原文疑点，且提议者的 affected_target_keys 错填了 Observation ID；独立 Opus 将该疑点判为 persistent、目标影响为空，并补齐 OBS004 的独立问题条目。程序却因 Candidate.findings 非空直接标 human_required，随后又把提议者疑点追加为 blocker，覆盖独立判断。这是程序／角色边界错误，不是教授原文被修改或 Opus 判 CL006 须人工。

`c111cf4` 修正此边界：提议者报告疑点不自动等于内容拒绝；独立审阅必须明确覆盖其稳定问题，并据原文决定影响。既有 returned-review 工程恢复只重新校验未写入的同一候选与真实原答，完整归档旧 parent／round／材料／收据，核原 transaction pins 未变、模型输入完全相同、role prompt 相同、全部调用确已返回，重新冻结当前程序实现后沿原提交与读回路径完成。不新增模型调用、不改模型 raw、不生成新语义判断或人工批准；真实目标拒绝仍阻止恢复。相关回归 **474 passed、43 skipped**。

CL006 已实际完成，revision 为 2、当前资格 ready，内容 SHA 为 `52e59a4b91b41c214990836fcc18a776477d8a3c500cf642b9ae4e1342c6b72a`，ChangeSet 为 `KCS-9bc233a11aaca1b93a12`。唯一内容写入为 CL006 statement；讲道原文件和其他内容记录未改。恢复没有追加 GPT、Opus 或 Gemini，也没有全库备份；已有一次 GPT、一次 Opus 接续调用及最初不完整 Opus 回答均保留。最终记录为对应 CL006 的 `continuation-20261014-v1/continuation-result-after-review-recovery.json`，旧 paused 结果独立保留。CL007 复查仍为 ready、revision 1；已连接 CVP 的 CL001 未处理。

后续把首次提议者疑点直接纳入现有 unresolved／prior_findings 协议，复用已有稳定 ID，不新增结果字段或审核角色。独立审核可以据原文 resolved 否决疑点、persistent 保留并界定目标影响、或判为目标 blocker；不得遗漏，也不能用一句「有问题」自动代替人工出口。Opus 与启用时的独立审计读取相同义务，已登记的同一问题不重复新增 ID；当前问题状态只由审阅结论维护。反例及完整 SQL／独立审计用例证明，首次 GPT source_uncertain 也能在完整来源下被独立审核否决，而不自动卡入人工；漏回应仍拒绝。完整回归 **476 passed、43 skipped**。

E006／OBS004 的冠词疑点依然待人工核对，与 CL008 中的 OBS009 问题属于同一段讲道的冠词核实线索，未冒称已解决。这里完成的是 CL006 Update，不是所有人工问题或整组 cleanup；整体 #435 仍未完成，未合并或部署。

## CL002、CL003 当前资格逐条重新确认（2026-10-14）

负责人要求再处理两条。核对本组后，未连接 CVP 的 CL004–CL008 当前资格均为 ready，CL002／CL003 的原审阅因共享关系与证据修正而不再适用于全部当前 pins；已连接 CVP 的 CL001 排除。因此按顺序执行 CL002，再执行 CL003，使用当前 Opus-only policy 和冻结实现 `aeb829a`。这是获授权的新当前版本 Update，不重写旧 Argon action、原答或历史资格。

两条均完成，Opus 各实际调用一次，findings 为零，程序事务／readback 与原目标核对通过。完整审核分母各 132 项，实际请求各 242,735 bytes。内容写集为空，没有内容 ChangeSet，Claim 仍为 revision 1；完成状态重入结果一致，未增加调用。没有 GPT、Gemini 或全库备份。本次没有修改实现代码或 prompt，也没有因某条遇到问题添加特殊规则。

CL002 当前资格 run 为 `CMR-d93e05b0280c834516452f4ff84cfd1e2dd422d5d80f552f11459341d3da184f`，CL003 为 `CMR-e2dd4d1c087d4c8df55f523f5c5047b85eecf79e292d6a72d294fcd5a314ac1f`，均为 audited／completed、`opus_and_program_readback`，明确 `independent_audit_complete=false`。两条实际结果保存于 `claim-management-435/son-of-man-revalidate-two-sequential-20261014-v1/CL002/single-update-result.json` 及对应 CL003 目录。

执行后再次读取本组当前状态：CL002–CL008 七条未连接 CVP 的 Claim 均 ready；CL006 为 revision 2，其余为 revision 1；CL001 仍因已连接 CVP 而未处理。汇总保存为同一运行根目录的 `group-status-after-revalidation.json`。E006／OBS004／OBS009 的旧冠词人工核对问题及原回答仍保留，未以本次通过结果冒称解决。本组目标 Claim 的当前资格确认完成，不代表所有来源人工问题或整体 #435 完成；未合并或部署。
