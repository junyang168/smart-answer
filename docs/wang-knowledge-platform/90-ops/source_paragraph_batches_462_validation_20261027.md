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
