# CVP 2.0 完整编码与隔离验证记录

> **讀者**：Developer。
> **類型**：記錄。
> **狀態**：2026-10-17 的编码及离线验证记录，不是正式内容生产或质量准入。
> **與代碼對齊**：2026-10-17（最终代码 `5e9412d1`；此前实现 `78d951a4`、SQL 字段成分补齐 `9add7215`）。
> **權威範圍**：本次执行的代码、测试与只读复验事实；操作说明见 [CVP 2.0 执行入口与恢复](../20-knowledge/cvp2_recall_preparation_v1.md)，目标验收以[主控设计](../20-knowledge/claim_cvp_management_2_design.md)为准。

## 1. 编码交付

共同 SQL CLI 已接入 `cvp-management` 和 `cvp-management-control`。完整 action 使用原 `pipeline_runs`、call intent／raw cache、累计调用及修订预算，支持索引维护／增量 recall、共同小节材料、程序生成成分与完整候选、有界多轮 Opus、顺序子任务、精确维护权限、共同提交与当前影响回读。新流程不调用额外模型终审或常规全库备份。

新建、已有连接维护、新 CVR／scope、merge 与逻辑退役走同一执行链。仅 scope 差异的 CVR 更新由程序继承未变字段；旧 CVR 与教授原文不覆盖。上游 Claim／Evidence 重新取得资格后可接回原任务，旧计划、候选、异议和调用事实保留。旧连接撤回保留原历史绑定，不能机械套用当前 Claim 版本；独立审计直接读取 SQL 历史验证该撤回。

共用 binding／pins／提交从主入口移出，维护模块同向依赖共享入口；材料经公开 store 接口读取，业务编译保持纯函数，SQL 不启动模型或读取本版材料文件。历史契约的备份、旧审核和预算没有升级。

## 2. 测试结果

| 检查 | 实际结果 |
| --- | --- |
| 286 个 Claim／CVP／viewpoint／相关 source 测试文件的大范围回归 | **5,652 passed，521 skipped** |
| 最后新增边界与完整新链路定向回归 | **272 passed** |
| 前端 `tsc --noEmit` | 通过 |
| `git diff --check` | 通过 |
| `python3 scripts/check-docs.py docs` | 仅剩另一项目既有的 `docs/notes-to-sermon-agent/functional_spec.md:120` 缺失锚点 |

定向回归包含真实临时 PostgreSQL 与假 provider 的实际 cache／事务：初轮直接通过、四次完整复核、context 扩展、无进展／unknown／预算耗尽、完整 Claim 容量拆分、首个子组提交后中断恢复、摘要批准复用、新卡增量 recall、Claim 文字变化与 Evidence 单独变化后同任务重接。

维护测试覆盖新 CVR 及成员重绑、merge 保留原身份 redirect、逻辑退役／撤回成员、上游更新后撤回历史连接；安全测试覆盖错借兄弟组结案、无资格成员新连接、人工保护 supersession、当前质量限制与使用依赖传播。晚项 rollback、commit lost reply、completion rollback 都沿已保存事实恢复，不增模型调用。

大范围回归发现的三类旧测试问题由旧 HEAD 实现独立复现后修复：历史来源测试使用已认证 metadata proof 的精确旧字节在临时目录重建，未知调用测试保存真实 reservation 后中断，容量测试匹配实际诊断并保留无调用断言。生产来源 SHA、请求身份和预算门槛未放宽。

## 3. 保存数据零调用／零写入复验

最终证据保存在 `staging/cvp-management-436/coding-readonly-20261017-v7/verification.json`，对应代码 `5e9412d156e0011523433208ca63c6f4a92937ae`，证据 SHA 为 `e0b8e7a406948d6a19714fcb311bbc098fa99a9cbebac831357c85d3435b087c`。

- 两条原资格探针在 **705** 个 active CVP 的当前 cut 上确定性重放，均返回 `index_required`；没有补发摘要或 recall。
- 两条实际 `opus_and_program_readback` 资格通过只读 SQL 权威校验，保留 `independent_audit_complete=false`。
- 原 **51** 条 Claim 的字段投影通过。
- 前后 **161,975** 条原语义记录的 revision／SHA／retired 向量一致；pipeline run 数前后同为 **21,305**。
- 模型调用、语义写入、pipeline 写入均为 **0**；未登记新 CVP action，未启动旧 campaign。

完整证据保留每个实现文件 SHA、资格与 preflight 绑定及前后计数。此次机械复验耗时约 **7.73 秒**，不是模型耗时或完整生产成本测量。

## 4. 仍未进行的验收

本次完成编码与隔离程序验证，没有运行真实 CVP 2.0 内容模型、生产 CVP 修改、正式独立质量评估、合并或部署。假 provider 只验证契约及恢复，不证明召回质量或教授内容判断正确。

固定真实 cohort 的召回遗漏／误召回、summary 对照、分片位置与容量敏感性、两个规模下的真实全流程成本和正式使用范围准入仍待有界验收；#436 和 #357 保持开放。
