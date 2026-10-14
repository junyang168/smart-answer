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
