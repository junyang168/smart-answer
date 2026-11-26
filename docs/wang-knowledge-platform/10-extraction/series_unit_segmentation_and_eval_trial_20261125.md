# 系列切分、Gate 0 校准与 U15 全链路实测记录（2026-11-25）

> **讀者**：Solution architect、Developer、神学编辑
>
> **類型**：記錄
>
> **狀態**：當前（記錄 2026-11-24 至 2026-11-25 在 `#845` 分支完成的 Layer 1、Gate 0 與 `U15` 全鏈路實測數據）
>
> **與代碼對齊**：2026-11-25（核對到 `#845` 工作樹 `wkp-845-unit-argument-design`）
>
> **權威範圍**：僅記錄本次實測結果與定量指標，不另立規範；對應設計規範見 [series_unit_segmentation_design.md](./series_unit_segmentation_design.md)。

---

## 1. Layer 1 系列级逻辑单元切分实测

使用 [series_unit_segmentation.py](../../../backend/pipeline/series_unit_segmentation.py)（Step 1 `gpt-6.1-sol` 并行 4 字段段落卡 → Step 2 `gpt-6.1-sol` 系列级切分 + 确定性 Hard Invariants → `claude-opus-5-5` 独立切开接缝复核 → 编译 `#845` `UnitManifest` 并通过 `claim_unit_plan.read_unit()` 验证）。所有 3 套系列均为**一次通过（0 重试、0 硬校验失败、0 外壳误杀）**：

| 系列 | 规模 | 单元 / 外壳 | 跨讲接缝与独立复核 | 混合段与空段表现 | `UnitManifest` |
| --- | --- | --- | --- | --- | --- |
| **Dev 1**：`2011 NYSC 馬太（二）` L1–L6 | 122 段 / 73,721 字 | 9 单元 / 14 外壳（13 空段 + 1 结束祷告） | 合并 L1→L2（`U03`）、L2→L3（`U04`）、L5→L6（`U08`）；拆开 L3→L4、L4→L5，均经 Claude Opus 5.5 复核确认为 `should_merge=False` | `L2-S0001`、`L2-S0016` 混合段保留；`L6-S0002..S0007` 空段自动剥离 | 9/9 通过 `read_unit()` |
| **Dev 2**：`2012 NYSC 馬太（三）` L1–L6 | 119 段 / 57,940 字 | 16 单元 / 14 外壳（7 空段 + 7 纯外壳） | **5/5** 跨讲接缝全部缝合，精确还原 20,134 字比喻大单元 `U15`（`L4-S0009..S0011`、`L5-S0003..S0043`、`L6-S0001..S0012`） | `L6-S0001` 报幕+续讲保留；`L5-S0001/S0002`、`L6-S0017/S0018` 纯外壳剥离；`L6-S0003` 夹心教导保留在 `U15` 内 | 16/16 通过 `read_unit()` |
| **Unseen Holdout**：`2016 NYSC 馬太（四）` L1–L4 | 77 段 / 43,325 字 | 6 单元 / 12 外壳（8 空段 + 4 纯外壳） | 冻结 Prompt 零调参迁移；3 处跨讲接缝合入 `U03`（28,752 字，`太 16:13–23` 跨 4 讲） | 0 误杀；验证了 2.8 万字单元对 `connect()` 128KB 分批修复的必要性 | 6/6 通过 `read_unit()` |

---

## 2. Layer 2 Gate 0 评测器校准实测

使用校准版 [argument_roundtrip_verify.py](../../../backend/pipeline/argument_roundtrip_verify.py)（Stage 1 `gpt-6.1-sol` 盲测重构 → Stage 2 `claude-opus-5-5` 黑盒三维度比对 → Stage 3 `claude-opus-5-5` 原图归因），在 4 组基准上达成 **11/11（100%）真错召回、0 条高中危误报**：

| 基准 | 目标 | 实测结果 |
| --- | --- | --- |
| `joy_real_e6` | `#845` `joy.json` 已知真实反向错边 **`E6`**（`N4 --refutes--> C008`） | **1/1 抓出**（Stage 1 `G01 [conflicting_or_reversed_edge]` 精准定位 `E6` 与 `E7` 方向冲突；Stage 2 确认唯一真抽取缺口，0 立场误判） |
| `blood_clean` | `#845` `blood.json` 干净基线（19 个核心单元） | **0 条 High/Medium 误报**（19/19 核心全覆盖，0 扭曲，0 真抽取缺口；仅 2 条 `low` 细节注记） |
| `blood_planted_structural` | 5 条植入结构错例：反转 `E10`/`E4`、删桥 `E9`/`E7`、删核心节点 `C008` | **5/5 抓出（100%）**，0 误报 |
| `blood_planted_semantic` | 5 条植入语义/立场/限定错例：`C001`/`C007`/`C004` 立场理由反转、`C006`/`C010` 限定漂移 | **5/5 抓出（100%）**，Stage 1 全部标 `conflicting_or_reversed_edge`，Stage 2 全部对应 `D01`–`D05` |

---

## 3. 端到端全链路实测：`2012 NYSC U15`（耶稣为什么用比喻，20,134 字）

直接消费 Layer 1 自动生成的 `U15/unit-manifest.json`，完整跑通 `claim_unit_plan` → `claim_unit_argument` → 双校验（Layer 2 Round-Trip 广角镜 + `argument_fidelity.py` 显微镜）：

1. **`claim_unit_plan.run()`**：耗时 52.4s，产出 16 个 `runs`、5 个留题 `questions`（覆盖 840 句原文）。
2. **`claim_unit_argument.run()`**：首轮 `bind_graph()` 抓出跨讲 Claim `N2`，经新增的 1 次自动重试修复（`3b1cff26`）后产出冻结候选 `92bb31e773934d1f404134afc64b9e76eab6926c014f997781ab1f4ccf5a2cc3`：**57 节点、62 条有向边、20 条 Claim、9 个论证组、`status: candidate`**。
3. **Layer 2 Round-Trip（广角镜）**：
   - **26/26（100%）核心论证单元全覆盖**，`core_units_partial = 0`，`core_units_missing = 0`，`distortion_count = 0`，`true_extraction_gap_count = 0`，**0 条 High/Medium 缺陷**；
   - 报出 6 条 `low` 细节，经 Stage 3 归因为 5 条抽取小瑕疵（漏英文 `lest` 批评、漏「解经生异端」动机、管家→比喻中心过渡桥、律师故事前后两半拆分、可 4:24 删字动机多推半句）和 1 条重构器元评论。
4. **`argument_fidelity.py`（显微镜，全量 57 节点 Claude Opus 5.5 精审）**：
   - 耗时 145.1s，**56/57 faithful（98.2%）**、`0 changed`、`1 added`；
   - 唯一抓出的 `added` 为 `C007`（多揉入「不能只凭常识」与「翻译与前后文矛盾必有问题」两句，不在其绑定句域内）。
5. **负责人同步裁定**：针对 `U15` 中律师妻子邀约被升格为独立 Claim（`N32`）的情况，负责人明确口径——**顺带提及的生活应用除非与核心论证强关联，否则只需简提，不单独升格为 Claim**，已同步回写至 `claim_unit_argument.py` 的 `GRAPH_PROMPT` 与 `argument_roundtrip_verify.py` 的 `JUDGE_SYSTEM`。
