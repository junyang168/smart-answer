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
