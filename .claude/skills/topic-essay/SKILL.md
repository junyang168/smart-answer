---
name: topic-essay
description: 写一篇 draft-first 跨讲道专题文章（如人子、萬人的贖價）：与负责人定读者问题与范围，讲清覆盖候选并记下纳入决定，跑写稿与审稿，把审不完的问题和负责人的意见交回，登记到审稿页等负责人终审。负责人输入 /topic-essay <题目> 开始，或不带参数接着做。
---

# 专题文章

设计见 `docs/wang-knowledge-platform/30-authoring/draft_first_repeatable_pipeline_solution.md`。

**这个技能只管和负责人的对话，不定规矩。** 写作与审稿的规矩在作者和审稿的 prompt 里，由测试守着两边一致；步骤次序、停点和检查在 `backend.pipeline.draft_first_essay` 里。不要在这里复述或补充写作规则，也不要动手改正文：正文只由作者修订、经审稿完整审过才算数。

所有命令在 card 的 worktree 根目录跑，用 `backend/.venv/bin/python`。`DATA_BASE_DIR` 从 `.env` 读。每篇文章一个运行目录：

```
RUN=$DATA_BASE_DIR/wang-knowledge-platform/staging/essays/<review-id>
```

**运行目录是跨会话的唯一记忆**：对话会结束、会被压缩，`state.json` 不会。负责人用中文讨论编辑问题时用简短的中文回答，并直接引稿子里的句子。

## 1. 开始或接着做

**带题目**（`/topic-essay 萬人的贖價`）：先开卡（`scripts/ticket.sh --epic E04 …`）和 worktree。然后和负责人定下：

- **读者问题**：一句话，用负责人认可的说法。可以先起草一个，说明它依据原文里实际出现的经文与讲道；最后由负责人定。
- **经文**：文章要讲的几处经文。
- **这篇讲什么、不讲什么**：负责人自己的话，原样写进 `--decision-basis`。

定了再建运行目录：

```bash
backend/.venv/bin/python -m backend.pipeline.draft_first_essay --run-dir "$RUN" --init \
  --review-id ransom-for-many-v1 --title "萬人的贖價" \
  --question "<读者问题>" --passage 太20:28 --passage 提前2:6 \
  --decision-basis "<负责人的原话>"
```

**不带参数**：列出 `staging/essays/` 下的运行，请负责人选一个，然后：

```bash
backend/.venv/bin/python -m backend.pipeline.draft_first_essay --run-dir "$RUN" --status
```

说明做到哪一步、停在哪里。

## 2. 往下走

```bash
backend/.venv/bin/python -m backend.pipeline.draft_first_essay --run-dir "$RUN"
```

它能做的一路做下去，到要负责人决定的地方停下（退出码 3），把要决定的内容打印出来并写进 `state.json` 的 `stopped`。每次停下，用负责人看得懂的话讲清楚停在哪里、要定什么，然后等负责人的话。**不要替负责人决定**，也不要自己加一轮。负责人定了，写成下面的文件，再跑同一条命令。

## 3. 每个停点怎么做

**选材决定（`decisions`）**：逐个讲候选——它说什么、为什么相关、覆盖搜索的建议。负责人对每个说「纳入」或「相邻」，并给理由；纳入的观点要定它在文中的角色（`central_claim`、`supporting_conclusion`、`positive_identification`、`qualification`、`tension_side`）。写成：

```json
[{"id": "<候选 id>", "decision": "include", "structure_role": "supporting_conclusion", "reason": "<负责人的理由>"},
 {"id": "<候选 id>", "decision": "adjacent", "reason": "<负责人的理由>"}]
```

存为 `$RUN/coverage-decisions.json`。

**CVP 缺口（`cvp_gap`）**：纳入的 Claim 不属于任何观点，要先走 CVP 2.0（另一条线，交接给它）。CVP 完成后：`--redo coverage`。负责人也可以改成「相邻」。

**校准（`calibration`）**：审稿 prompt 或模型变了，程序已自动跑过校准；停在这里说明结果与负责人的判定表不合。把不合的项讲给负责人，由负责人定改规则还是改判定表，不要自己调。

**审稿没通过（`review`）**：把打印出的未解决问题逐条讲清楚（引稿子原句）。负责人可以：

- 给意见：写成 `$RUN/owner-notes/NN.json`（编号接着往下），`{"notes": [{"anchor": "<稿中逐字的一句>", "summary": "<负责人的意见>", "required_change": "<怎么改>"}]}`。`anchor` 必须逐字取自当前稿。
- 只加一轮修订、不给新意见：写 `{"notes": []}`。
- 停下不做：什么都不写。

**终审（`final_review`）**：给负责人审稿页地址。发布只有负责人在审稿页上按；发布不是部署。

## 4. 每次停下前

更新 `docs/wang-knowledge-platform/30-authoring/CURRENT_AUTHORING_SESSION.md`：做到哪一步、等负责人什么、运行目录在哪里；提交，并请负责人同意推送。负责人在讨论中定下的写作或审稿规矩，不记在这里——开卡改 prompt 和测试。
