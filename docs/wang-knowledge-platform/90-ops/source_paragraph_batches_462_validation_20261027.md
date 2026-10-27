# #462 段落 Claim batch 接续验收记录

> **读者**：Developer、维护执行者。
> **类型**：记录。
> **状态**：2026-10-27 的实现验证；不代表示例内容已完成修订。
> **与代码对齐**：2026-10-27，`claim_management_batch.load_paragraphs`／`run_paragraphs` 及 `test_claim_paragraph_batches.py`。
> **权威范围**：本次顺序调用、隔离 SQL 反例及真实来源零写入预检的观测事实；不授予模型调用或内容批准。

## 1. 实现与隔离验证

设计入口见 [Claim Management 2.0 第 9.2 节](../20-knowledge/claim_management_2_design.md#92-原文只改一段或几段)。普通 batch 的调度、模型角色、修订循环、事务及恢复入口没有替换；顺序调用位于同一模块的外层。

在 socket-only 临时 PostgreSQL 中，使用真实 Update 执行器、保存材料、事务、回读及当前资格核实；模型回答均为明确的合成回答，没有 provider 调用。覆盖：

- 输入乱序时按实际来源 S0049、S0050 排序；subtitle 不占正文段号。
- p1 完成初审、GPT 修订与 Opus 复审后，p2 实际读取 p1 的新 Claim 版本；模型材料仍含完整来源及 slide 文字。
- 同段多个 job 仍交给原 batch，保留其 `max_workers=2`；跨段相同完整 action 去重，不修改 request。
- 部分重叠的联合 Update 不被静默删减；原 action/root/预算不得换址。
- unknown 回答、旧完成标记、源文件／注册行／输入漂移均不能越过前批完成条件。
- 同一输入恢复不重发已完成模型；新增 Claim 同样需要当前资格，最终重新核对全部已处理 Claim。
- 结果中的 Claim 完成与 `downstream_handoff.status=not_executed` 分开，不宣称已完成 CVP 或论证层维护。

执行：

```bash
PATH=/opt/homebrew/opt/postgresql@14/bin:$PATH PYTHONPATH=. backend/.venv/bin/python -m pytest -q \
  backend/tests/test_claim_paragraph_batches.py \
  backend/tests/test_claim_management_batch_sources.py \
  backend/tests/test_claim_management_execution_postgres.py \
  backend/tests/test_claim_joint_update.py \
  backend/tests/test_claim_qualification_reuse.py \
  backend/tests/test_source_paragraph_impact.py
```

结果：267 passed，23 skipped，78.04 秒。跳过项属于原有保存输入验收／提交阶段条件，不作为本次真实模型验收。`git diff --check` 及前端 `tsc --noEmit` 通过。文档检查只报告已知的另一项目失效锚点 `docs/notes-to-sermon-agent/functional_spec.md:120`。

## 2. S 210613 提前2 1-7 中保的零写入预检

来源身份：`SRC-S_210613_2_1_7-8c320ede3e26`。完整旧正文与数据库登记的正文 SHA 一致；新旧均为 61 个正文段落，实际差异仅为 S0049、S0050。负责人确认：「我改正了轉錄的錯誤,並且加入教授 slide」。本次未把此确认当作内容审核通过。

| 项目 | SHA-256 |
| --- | --- |
| 数据库登记的来源文件 | `2d626060f871075a810ec93179d917089ce48624b491bd74676ac357699e4bb3` |
| 实际修改后的来源文件 | `41a96aca86a528638c4a6c8618cd44b429457a2651fa78956d3d1157fdef5f1f` |

下表仅为已经识别的直接引用候选，不冒充全部语境影响分母：

| 段落 | Claim ID |
| --- | --- |
| S0049 | `DK-d7a3359c50c895c48b09-P09-CL008`、`DK-d7a3359c50c895c48b09-P09-CL009` |
| S0050 | `DK-d7a3359c50c895c48b09-P10-CL001`、`DK-d7a3359c50c895c48b09-P10-CL002`、`DK-d7a3359c50c895c48b09-P10-CL003` |

预检使用零角色调用预算、零字段写权限，调用 `load_paragraphs`，没有调用 batch 执行或模型端口。由于源文件已改而数据库仍登记旧版本，在授权检查处准确拒绝：`registered source physical version drift; new source version/action required`。因此先前的来源版本交接仍需经已有来源维护流程完成，不能通过新入口直接重绑 SHA 绕过。

前后逐项核对这五条 Claim 和来源注册行未变，新预检 action 行为 0，模型调用为 0，内容写入为 0。这不是这篇讲道已完成修订、语义审阅或 CVP 同步的证明。

## 3. Claim ID 清单入口补充验证

2026-10-27 按负责人「先把這個補上」的方向，新增 `claim_management_intake.prepare`／`run`，batch CLI 提供 `--claim-ids`、`--instruction`、`--max-workers`，沿用 `--detach` 与 `--status`。调用者提供 ID 数组即可准备并执行普通 Update，不需要逐条手工构造 request。

在同一隔离 PostgreSQL 验证边界内，实际调用 Update 执行器；模型输出仍为明确合成回复。验证了完整清单去重、逐条结果、Opus 异议 → GPT 修订 → Opus 复审 → SQL 提交／当前资格、同 root 恢复零重复调用、未知答复保留原调用而另一条完成、不存在及人工 hold 的明确未完成结果、只读邻居不得变成隐藏目标、输入／来源／policy 漂移拒绝、任务内 policy 副本、冻结后台清单、继承 worker 锁的真实状态查询，以及失败时不保留误导的旧 completed 结果。

针对性四文件验证为 46 passed。最终回归运行本记录第 1 节的六个文件，另加入 `backend/tests/test_claim_management_intake.py` 与 `backend/tests/test_claim_context_snapshot.py`：**293 passed，23 skipped，74.35 秒**。跳过项仍为原有保存输入验收／提交阶段条件。`git diff --check` 通过；文档检查仍只有前述另一项目的既有失效锚点。

此次清单入口验证未调用真实 GPT／Opus，未改动生产 Claims、来源记录或 CVP。清单 Update 完成与来源修订、来源覆盖和 CVP／论证层完成继续分别报告。

## 4. PR #463 的 Claude 审阅修正

2026-10-27 处理 [Claude 的实跑意见](https://github.com/junyang168/smart-answer/pull/463#issuecomment-6435124742)。从 #461 以保留原提交出处的 cherry-pick 接入八项 Claim 共用修正：`79722381`、`790a2f3c`、`f74e5cd2`、`effd8013`、`d7dd186f`、`5ab8c647`、`939e56de`、`1958dcb5`。未引入该分支的整套主题探索功能。

- batch 在原读写冲突调度中加入来源占用：同一来源的 action 排队，不同来源才可并行。受控 worker 测试观察到 `max_workers=2` 时，同源最大同时启动数为 1，不同源为 2。
- 新清单默认 v8，以 v7 创建能力为基础增加版本化 `claim_changes_v4`。既有任务自动消费自己的冻结 policy，不升级旧 schema、原答或预算。
- 程序查询已登记／退休身份，在既有 SQL 运行账本中为各 action/source/root 预留一个 Claim ID；其他 collection 的同名身份及另一任务的预留也不能占用。并发预留与同 root 重复执行由隔离 SQL 验证；模型输出受该 ID 枚举及候选／提交校验约束。
- 使用真实 Update 执行器验证同源新 Claim、Evidence 双向 owner 和 Question 答案关联共同完成。
- 使用同一执行器验证目标 Claim 与关系退休、Evidence／Question 清理在一笔 transaction 提交；晚期失败全笔回滚，恢复复用原模型回答。退休历史 payload 保留，列表报告已处理的 `retired_keys`，而不伪称退休 Claim-ready。
- 原子 artifact 写入沿用共用 `write_immutable`，不留下另一个半写文件恢复协议。

八个针对性文件先得到 141 passed。随后对 Claim Management、compact 输出、联合更新、证据审阅、权限接续、资格复用、段落批次及来源查询共 21 个文件做回归：**487 passed，58 skipped，114.13 秒**。跳过项是原有外部保存输入／阶段条件；模型答复均为合成，没有真实 provider 或 batch 调用。文档检查仍只有另一项目的既有失效锚点。

最后补充退休评估指令与跨来源同前缀 ID 预留后，重新验证清单、身份／退休和来源排队三个文件：**34 passed，9.93 秒**。不同来源但同一 ID 前缀的并发预留同样得到不同身份。

审阅第 4 点的多处来源校订、版本登记、完整影响判定及 Claim/CVP 自动接续另记 [#464](https://github.com/junyang168/smart-answer/issues/464)，已登记 E02 与项目看板。该事项在本记录时仍未完成；S 210613 的来源版本、Claims 与 CVP 均未因本次审阅修正而写入。
