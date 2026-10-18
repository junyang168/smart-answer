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

## 5. 独立代码审查与修复

同日对 PR #438 的 `dddd1a82` 相对其 stacked base 开展三路独立代码审查，覆盖 SQL 权威及恢复、模型与召回编排、来源 metadata 及 v2 reader 兼容；主 agent 复核发现与修复。本次是实现审查，区别于此前 Fable 的设计审查。

| 已复现问题 | 修复于 `81b9ae1b` |
| --- | --- |
| 被否决的新候选一直留在后续写集中，无法正常撤回或整合同义身份 | `discard_refs` 仅撤销尚未提交的候选改动；保留成分、历史异议及完整后像检查 |
| 已解决质量异议在目标版本变化后，重新完整审核仍不能更新解决依据 | 按实际本轮旧异议裁决更新 resolution pins，原独立报告保留 |
| 加载同来源成员支持时，旧扩展覆盖新成员需要的小节 | 同版本小节取并集；来源漂移拒绝；保留扩展历史 |
| 一次请求同来源多个位置时，第二个位置错误消耗下一扩展梯度 | 按来源合并位置，一轮推进一次梯度 |
| 索引及召回中断恢复将缓存调用再次计入剩余调用需求 | 阶段规划计入该阶段已有 reservation，原 SQL 总预算及 unknown 规则继续生效 |
| 维护一个关系端点错误要求完整维护另一个端点 | 材料范围与已有 domain 权限一致，邻端点保持只读 |
| v2 非 statement 的支持连接只传 ID，未向 QA projection 传角色、字段范围及依赖 | 补齐这些相关连接与必要支持，保留原成员身份；纯 v1 四种 consumer 序列化输出逐字不变 |

修复后联合回归 **505 passed**，覆盖 CVP management、recall、材料、projection、foundation 和来源小标题。新增测试包含临时 PostgreSQL 的索引／召回缓存恢复、被否决候选撤回后的完整第二轮复核及零 CVP 写入、部分撤回拒绝、质量异议版本再核验。前端 `tsc --noEmit`、`git diff --check` 通过；文档检查仍仅有第 2 节记录的既有无关锚点问题。

`81b9ae1b` 的保存数据只读复验为 `staging/cvp-management-436/coding-readonly-20261017-v8/verification.json`，SHA `732703b9ae19366cd762f31039ae07e84b026703509a6f885b68817ede704a1c`。51 条 Claim 投影、两条现存资格及 preflight 复验通过；161,975 条语义 pins 与 21,305 条任务记录前后一致，模型调用／语义写入／任务写入均为零。此次审查确认的问题均已修复；第 4 节的真实模型验收范围仍未执行。

## 6. 2026-10-18：人子摘要实际生成与来源扩展

负责人先要求生成全部人子相关摘要，随后指出短摘要只是重复 statement，要求加入 Claim 的所有信息和原文小节，将上限放宽至 500 字。两次实验的原材料、回答和审核分别保存，后一次不改写前一次事实。

第一批包含 31 个 active CVP：head 内容匹配“人子／son of man”，或其 active 连接的 Claim 内容匹配；范围包含相关语境观点，不只标题含“人子”的观点。仅用 statement／scope／signature 的短摘要经 4 次真实调用全部批准，长度 25–104 字符，但负责人认为信息增益不足。证据位于 `staging/cvp-management-436/son-of-man-summaries-20261018-v1/`。更早的单条尝试在过期 CLI 路径检查处失败，未启动模型；原失败记录保留。后续使用当前应用内 CLI，登录和版本预检通过，不更换模型。

新实现 `974ef41f` 为同一 31 个 CVP 加入 71 条已连接 Claim 的完整记录、Evidence、反对立场、引用和 28 份来源中的相关小节；未发现完全缺少来源片段的 Claim。材料保留暂缓／不合格证据、来源限制与人工事实，不改写教授正文。程序按真实请求大小分成 7 批（5、4、5、5、5、5、2），共 16 次真实订阅调用（GPT 8、Opus 8）。最后一批的登山变像摘要因加入原文没有的告诫被拒绝，修正后复核通过；原拒绝保留。

全部 31 条新摘要通过末轮 Opus 审阅，长度 116–319 字符，上限为 500。结果位于 `staging/cvp-management-436/son-of-man-context-summaries-20261018-v1/summaries.json`，artifact SHA 为 `ce0ef2b686abd48f14794a2d226f13b738dececb81ac580bd70597a1cc0587f2`；同目录 `summaries.html` 提供 statement／summary 对照。生成没有 Claim、CVP 或 pipeline SQL 写入，也没有运行两条 Claim 的 CVP 生成／连接试验。新上下文摘要不自动进入旧短索引召回契约。

相关回归 62 passed；文档检查仍仅有其他项目既有缺失锚点。此次证明带来源摘要可以生成和审阅，不证明主题路由、几千个 CVP 的召回质量或正式全流程生产已验收。

随后以禁止新调用的 replay transport 认证全部 16 份原始回答，逐批及最终重新核对当前材料；结果逐字复现，原 raw 文件未变，新模型调用为 0。证据为同目录 `replay-verification.json`。

## 7. 2026-10-18：statement-only recall 单条观察

负责人要求召回不使用 summary，并在首次批量调用已经发出后收窄为先查看一条。实现 `cf426251` 增加只读的 statement-only 测试计划，保留旧生产流程；相关测试 32 passed。当前 active CVP 为 705，CVP 输入只有短引用和完整 statement，完整 20-Claim 请求约 117 KB。最初准备的 75 条、双模型及反序比较计划没有全部执行：首批 20 条调用返回保存后立即停止父进程，确认下一批 intent 不存在，没有第二次调用，也没有修改连接。

仅查看 `DK-1c98d4a95cd5-P10-CL005`（耶稣宣称自己就是但以理书7:13那位特定人子）的结果，返回 8 个 CVP，包含直接的人物身份／称号指涉、神性与自称，以及定冠词和四兽帝国等较宽背景。没有使用 summary、既有连接注入或先行主题筛选。原始回答经过缓存身份验证，当前 705 个 head 再核一致。结果为 `staging/cvp-management-436/statement-recall-benchmark-20261018-v1/single-claim-result.json`；原批次与停止说明同目录保存。这是从已经发出的 20 条一组调用中观察一条，不是单 Claim 独立调用；不能据此报告整体召回率、精确率或排序稳定性。
