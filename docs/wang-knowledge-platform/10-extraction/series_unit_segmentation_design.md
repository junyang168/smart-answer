# 系列级无重叠逻辑单元切分与两层评测设计

> **讀者**：神学编辑、Solution architect、Developer
>
> **類型**：規範
>
> **狀態**：当前设计草案（已根据 2026-11-24 红队自审精简修订），#845，待审阅
>
> **與代碼對齊**：2026-11-24（核对到 `c0ad6f6e`；下游 `claim_unit_plan.py` 与 `claim_unit_argument.py` 已存在，本文所述的上游系列切分、两层 Eval 及第 5.3 节列出的两处下游前置修复尚未实作）
>
> **權威範圍**：2–6 讲连续系列自动切分为平铺无重叠逻辑单元（产出 `#845` 可消费的 `UnitManifest`），以及面向 500 篇规模化交付的两层 Eval 与分级阻断契约。来源归属、跨讲观点身份与 D1–D4 架构约束遵循[方案架构](../00-overview/solution_architecture.md)与[单元论证设计](./unit_argument_design.md)。本次修订依据的对抗审查记录见[红队自审记录（2026-11-24）](./series_unit_segmentation_self_review_20261124.md)。

### 目录

| 节 | 内容 |
| --- | --- |
| [1. 问题与架构定位](#1-问题与架构定位) | 为什么按系列切分，以及与 `#845` 现有模块的职责边界 |
| [2. 两步系列切分流水线](#2-两步系列切分流水线) | Step 1 极简段落卡、Step 2 系列级切分与非外壳接缝注入 |
| [3. 真实语料边界规则](#3-真实语料边界规则) | 混合段保护、夹心短教导与隔讲遥指的明确处置 |
| [4. 程序硬校验与 500 篇放量契约](#4-程序硬校验与-500-篇放量契约) | Hard Invariants、跨讲错切复核、完整缓存键与阻断闭环 |
| [5. 输出 Schema、UnitManifest 桥接与下游前置修复](#5-输出-schemaunitmanifest-桥接与下游前置修复) | 极简平铺输出、视觉核验透传与两处必修代码前置 |
| [6. 两层 Eval 策略与规则映射](#6-两层-eval-策略与规则映射) | Layer 1 切分验收、Layer 2 校准版 Round-Trip 与正负反例表 |

---

## 1. 问题与架构定位

「一讲」只是录音与课时的物理边界，不是教授的论证边界。例如在 `2012 NYSC 專題：馬太福音釋經（三）`（L1–L6，共 119 段、57,940 字）中，L1→L2 到 L5→L6 的 5 处下课断点均发生在正在展开的论证中途（如 `L5-S0043` 讲到 `esthíō` 截断，`L6-S0001` 首句报幕后紧接续讲编字典与 `Apple` 例证）。

现有 `#845` 代码已实现**单单元内部**的逐句分组（[claim_unit_plan.py](../../../backend/pipeline/claim_unit_plan.py)）与整单元论证图生成（[claim_unit_argument.py](../../../backend/pipeline/claim_unit_argument.py)），但上游仍靠人工手写 `unit-manifest.json`。本设计只补齐「2–6 讲系列 → 平铺逻辑单元 `UnitManifest`」这一上游环节，**不做嵌套子块，也不把跨单元框架引用绑死在切分器里**：

```mermaid
flowchart LR
    S["2-6 讲连续系列逐字稿"] --> P["Step 1: 单讲并行极简段落卡"]
    P --> U["Step 2: 系列级切分 + 硬校验 + 接缝复核"]
    U --> M["平铺逻辑单元 UnitManifest"]
    M --> C["#845 claim_unit_plan + claim_unit_argument"]
    C --> E["Layer 2: 校准版 Round-Trip 抽检/验收"]
```

---

## 2. 两步系列切分流水线

### 2.1 Step 1：单讲并行压缩为 4 字段「段落卡」

使用 [source_projection.py](../../../backend/pipeline/source_projection.py) 的 `project_script(data["script"])` 投影出每讲 `body_rows`，按 `L{i}-S{idx+1:04d}` 编号（0 字空段由程序直接标为 `is_pure_shell = true`，不送模型）。

每讲独立并行调用轻量模型，将非空段压缩为仅含 **4 个字段**的临时段落卡 JSON（不入库，下游论证抽取仍读完整逐字稿）：

| 字段 | 含义 |
| --- | --- |
| `pk` | 段落锚点，如 `"L5-S0043"` |
| `chars` | 程序计算的该段字符数 |
| `summary` | 40–60 字一句话摘要，必须包含当前主解经文（区分佐证引经）及跨段悬念/承接 |
| `is_pure_shell` | 布尔值：仅当整段为**纯**开场/结束祷告、纯行政报幕或 0 字空段时为 `true`；任何夹带实质教学/复述的混合段必须为 `false` |

### 2.2 Step 2：系列级 Root Topic 切分

输入：
1. Step 1 产出的全系列段落卡表（2–6 讲）；
2. **每处相邻讲次交界的非外壳原文**：取前讲**最后 2 个 `is_pure_shell == false` 的段落** + 后讲**最前 2 个 `is_pure_shell == false` 的段落**的完整原文。这样可以避免 `2012 L5-S0001`（报幕）与 `L5-S0002`（祷告）把真正续讲的 `L5-S0003` 挤出接缝窗口。

切分信号（输出平铺的顶层 `units` 列表）：
1. **主解经文推进**：主经文未变、中间引旧约或平行经文作论据的，仍属同一单元。
2. **设问的「起」与「落」**：从抛出张力/问题开始，到显式收束为止。
3. **跨讲接缝合并**：前讲结尾留题、留作业或截断举例、后讲开头接续作答的，合并进同一 `cross_lecture: true` 单元，并在 `seam_evidence` 中摘录两端真实原文子串。
4. **纯外壳剥离**：仅将纯祷告、纯行政通知与 0 字空段归入 `non_argument_paragraphs`。**空段由程序后处理自动从单元 `ranges` 中剔除并归入 `non_argument_paragraphs`**，不要求模型手工绕开分散空段（如 `2012 L3-S0005..S0008, S0010` 或 `2011 L6-S0002..S0007`）。

---

## 3. 真实语料边界规则

基于 `2011` / `2012 NYSC` 真实脏样本，明确以下 4 条边界裁定：

1. **混合段保护（段首与段中）**：凡同时含报幕/场务与实质教学的段落（如 `2012 L6-S0001` 报幕+字典例证、`2011 L2-S0001` 报幕+律法矛盾复述、`2011 L2-S0016` 护教短评+下课时间），必须留在论证单元内，不得归入 `non_argument_paragraphs`。
2. **夹心短教导不撕裂大单元**：当一段独立短教导夹在跨讲长论证正中（如 `2012 L6-S0003`「抓比喻中心 + 不义管家」夹在 `L4-S0009..L6-S0012` 比喻论证中间），上游切分器**保留其在大单元内**，不把大单元撕成不连续两截；交由下游 `claim_unit_plan.py` 在单元内按句粒度划成独立 `group`（与 [unit_argument_eval.md](./unit_argument_eval.md) 第 2 节一致）。
3. **不拆 `parent_unit` 子块**：超长论证（如 2 万字比喻）保持单一顶层单元，不造 `U09a/b/c` 嵌套字段；单元内分组由 `claim_unit_plan.py` 承担，128KB 字节限制由第 5.3 节的下游分批修复解决。
4. **隔讲遥指与远距离概念复用不破 0 重叠**：
   - 若一段同时预告多讲后的主题（如 `2011 L2-S0015 / S0024` 既谈徒 15 / 动淫念，又隔着 L3 预告 L4 离婚再婚），该段只归属其主要所属单元，**绝不复制段落制造重叠**；L4 独立成单元，跨单元的前后呼应留待下游跨讲观点层（CVP）或后续检索层承接。
   - 远距离框架复用（如 `2011 L1` 定义 `inclusio` 在 `L5 / L6` 复用）属双向门，**不在上游切分器中维护 `uses_frameworks` DAG**，避免与单元线性编号耦合。

---

## 4. 程序硬校验与 500 篇放量契约

### 4.1 确定性硬校验（Hard Invariants）

Step 2 输出经程序自动剥离 0 字空段后，必须通过以下确定性 Python 断言（失败最多重试 2 次，仍失败则停止报错）：
1. **100% 覆盖、0 重叠**：所有 `units[*].ranges` 展开后的非空段落集合与 `non_argument_paragraphs` 互不相交，且二者并集与全系列 `project_script` 段落全集**完全相等**。
2. **物理锚点与单调序**：每个 `pk` 均解析回合法 `(source_id, paragraph_key)` 且源文件 `sha256` 一致；单元内每讲 `ranges` 有序且不重叠。
3. **跨讲证据真实性**：`cross_lecture == true` 当且仅当单元实际跨越 ≥ 2 个 `source_id`，且 `seam_evidence` 非空并包含可在对应讲次原文中逐字命中的引用片段。
4. **防退化与字节预算检查**：拒绝全系列合并为 1 个巨型单元或按单段碎切的退化结果；程序按真实 `body_rows` 重算每个单元的 `chars` 与 UTF-8 字节数，若超过下游可承接上限则显式报错，不静默送入 `connect()` 崩溃。

### 4.2 独立接缝复核（专拦「跨讲静默错切」）

由于 4.1 节硬校验和 Layer 2 单单元比对都拦不住「Step 2 把本应合并的跨讲论证切成了两半」，对 Step 2 **决定切开（未合并）的每一处讲次交界**，增加一次极轻量的跨厂商复核：
- 输入：仅喂入该交界前讲最后 2 个非外壳段 + 后讲最前 2 个非外壳段的原文（每系列仅 2–5 次调用）。
- 判定：若独立厂商模型判定后讲仍在直接续讲前讲未完的同一问题，记为 **High-Severity 接缝告警**，阻止静默截断入库。

### 4.3 断点续跑、防覆盖与阻断闭环（500 篇放量）

1. **完整缓存键**：
   - Step 1 段落卡按 `(source_id, body_sha256, step1_model_id, step1_prompt_sha256)` 落盘缓存；
   - Step 2 切分按 `(series_cards_sha256, seam_bodies_sha256, step2_model_id, step2_prompt_sha256)` 落盘缓存。任一 Prompt、模型或接缝原文变更均自动失效重算，绝不静默命中旧缓存。
2. **按单元粒度保护人工裁定（`ruling.json`）**：重跑时绝不覆盖已有 `ruling.json`；若某讲原文修订导致哈希变化，只将**触及该讲的单元**标为 `stale_ruling` 并联动标记其下游 `unit-manifest.json` 失效，不冻结整套系列的其他正常单元。
3. **分级阻断与明确修复出口**：
   - **High-Severity（阻断该系列/单元入库）**：Hard Invariant 失败、独立接缝复核报跨讲截断、或 Layer 2 归因确认为高危 `extraction_defect`。
   - **修复闭环**：接缝或边界阻断 → 回写该系列的 `ruling.json` 锁定边界后仅重跑受影响单元；抽取层阻断 → 走 `#845` 单单元修订或人工例外，不堆死无出口队列。
   - **Medium / Low-Severity（记录报告、不阻断流水线）**：外壳非空段疑似误判（仅记告警待批量抽检，避免祷告段假阳性炸毁人工队列）、次一级细节压缩、或 `illustrates` 归入 `supports` 类标签粒度差异。

---

## 5. 输出 Schema、UnitManifest 桥接与下游前置修复

### 5.1 系列切分输出 JSON

```json
{
  "version": "series_unit_segmentation_v1",
  "series": "2012 NYSC 專題：馬太福音釋經（三）",
  "sources": [
    {"lecture": "L1", "source_id": "...", "file": "...", "sha256": "..."}
  ],
  "units": [
    {
      "unit_id": "U02",
      "title": "馬太 10 章兩個差派與批駁史懷哲",
      "passage": "太 10:1–23",
      "cross_lecture": true,
      "seam_evidence": {
        "prev_pk": "L1-S0012",
        "prev_quote": "...",
        "next_pk": "L2-S0001",
        "next_quote": "..."
      },
      "ranges": ["L1-S0010..L1-S0012", "L2-S0001..L2-S0006"],
      "chars": 7798
    }
  ],
  "non_argument_paragraphs": [
    {"pk": "L1-S0013", "reason": "empty"},
    {"pk": "L5-S0001", "reason": "logistics"},
    {"pk": "L5-S0002", "reason": "prayer"}
  ]
}
```

### 5.2 桥接到 `#845` `UnitManifest`

程序将每个顶层 `unit` 直接编译为 [claim_unit_plan.py](../../../backend/pipeline/claim_unit_plan.py) 严格校验的 `UnitManifest`（`StrictModel(extra="forbid")`，只含 `sources` 字段）：
- 按讲次聚合 `ranges` 为 `UnitSource(source_id, file, sha256, ranges=[SourceRange(first, last)], visual_attestations=...)`；
- **必须透传各讲的 `visual_attestations`**，满足 `claim_unit_plan.py:72` 的 `validate_visual_source_attestations()` 强校验；
- 保留原始 `(source_id, paragraph_key)` 锚点，确保下游 `SourceFragment` 精准回指原讲道 JSON 与音视频时间戳。

### 5.3 500 篇放量前必须先修的两处 `#845` 下游代码前置

红队 Dry-run 在 `c0ad6f6e` 现有代码中发现两处真实缺陷，必须在进入 500 篇放量前修复并补单测：
1. **`claim_unit_plan.py:164` 多批目录碰撞**：`plan_unit()` 的 `while` 分批循环每批均传入同一目录 `root / "plan"`，超过单批字节上限时第 2 批会覆盖/命中第 1 批缓存，须改为按批号隔离（如 `root / f"plan-{batch:02d}"`）。
2. **`claim_unit_argument.py:201` 128KB 硬墙**：`connect()` 将全单元逐句原文与全部 `claims` 一次性塞入 `creation.call()`，无分批逻辑；2 万字单元已贴近 `GPT_BYTES = 128000` 上限，>2.5 万字必触发 `ValueError`。放量前须为超限单元实现按句域/组摘要分批连线或显式预算上限控制。

---

## 6. 两层 Eval 策略与规则映射

### 6.1 Layer 1：系列切分验收（Dev Set → Unseen Holdout）

1. **Dev Set 1**：`2011 NYSC 馬太福音釋經（二）`（L1–L6，122 段、73,721 字），对照 `2011-nysc-matthew-2-series-trial/digest.md` 验证正向合并（L1→L2 律法矛盾、L2→L3 动淫念）与**负向必拆**（L3→L4 动淫念/离婚、L4→L5 离婚/爱仇敌）。
2. **Dev Set 2**：`2012 NYSC 馬太福音釋經（三）`（L1–L6，119 段、57,940 字），验证 5 处跨讲接缝（含被 `L5-S0001/S0002` 遮挡的 `L4-S0011 → L5-S0003` 与 `L5-S0043 → L6-S0001`）、混合段 `L6-S0001` 未被误杀、夹心段 `L6-S0003` 保留在大单元内，以及 119/119 零重叠校验。
3. **外壳核对**：Dev Set 上用**确定性人工白/黑名单**直接断言，不用未校准的黑盒 LLM 当单测裁判；生产跑批时对 `non_argument_paragraphs` 中的非空段作低优告警抽检。
4. **Unseen Holdout**：在 Dev Set 冻结 Prompt 后，另取 1 套从未参与调参的系列跑一次，报告接缝误切数、无关主题过合并数与外壳误杀数。

### 6.2 Layer 2：单元论证图验收（校准版 Round-Trip Verification）

遵循[方案架构](../00-overview/solution_architecture.md) D4 跨厂商独立原则，并先过 **Gate 0**：

1. **Gate 0（评测器量化校准）**：在 `#845` 含已知反向边 `E6` 的 `joy.json`、`blood.json` 以及不少于 **10 条覆盖断桥 / 反向边 / 立场颠倒 / 丢限定条件的人工植入错例**上测试评测器本身。量化通过门槛：**植入真错召回率 = 100%，正常样本假阳性率 ≤ 5%**（不误杀合法支持故事与写明举例的 `supports`），通过后方可启用。
2. **三阶段跨厂商执行（生产按预算分级采样 + High-Risk 必检）**：
   - **Stage 1（盲测重构，GPT）**：只输入剥离原文与引文的单元论证图（按连通分量分组），强防脑补，断裂或边方向冲突时强制标 `[GAP]`。
   - **Stage 2（黑盒比对，Claude Opus 5.5）**：不看论证层 JSON，直接比对重构叙述与完整单元逐字稿（缺失 / 立场与逻辑扭曲 / 非论证噪音）。
   - **Stage 3（事后原图归因，仅针对 Stage 2 报出项）**：回查论证层 JSON，区分 `extraction_defect` 与 `reconstructor_compression`。
   - **500 篇成本控制**：非高危常规单元采用分层抽检（如每系列必检 1 个跨讲长单元 + 随机抽检 20% 单元），不盲目全量对 2,500+ 单元跑 Opus 全文比对。

### 6.3 规则 ↔ Eval 正负反例映射表

遵循[论证层重建设计](./argument_layer_rebuild_design.md)第 0 节「每条规则至少对应一个可使其不及格的案例」：

| 设计规则 | 正向验证案例 | 必补的负向 / 会 Fail 的反例 |
| --- | --- | --- |
| R1：100% 覆盖、0 重叠、空段自动剥离、防退化 | `2012 L1–L6`（119 段）与 `2011 L1–L6`（122 段） | 5 个合成负向单测（重叠 1 段、漏 1 段、逆序 `ranges`、空段未剥离、`cross_lecture` 矛盾）+ 整系列合 1 单元 / 逐段碎切退化反例，断言必抛错 |
| R2：跨讲接缝准确合并且不无脑全合并 | `2012 L4-S0011→L5-S0003`（跨过外壳）、`L5-S0043→L6-S0001`；`2011 L1→L2`、`L2→L3` | 1) **必拆负样本**：`2011 L3→L4`、`L4→L5` 误合并判 Fail；2) 捏造非原文子串的 `seam_evidence` 判 Fail；3) 人为切断 `L5-S0043/L6-S0001` 验证独立接缝复核必告警 |
| R3：混合段留单元、纯外壳进 `non_argument_paragraphs` | `2012 L6-S0001`、`2011 L2-S0001`、`L2-S0016`（中段混合）留单元内 | 确定性白/黑名单断言：上述混合段进外壳判 Fail；纯外壳 `2012 L5-S0001/S0002`、`L6-S0017/S0018` 混入论证单元判 Fail |
| R4：夹心短教导不撕裂大单元 & 端到端不撞 128KB | `2012 L4-S0009..L6-S0012`（含 `L6-S0003`）完整编译为单一 `UnitManifest`（含 `visual_attestations`） | 将 `2012` 2 万字单元及构造的 2.8 万字超长单元实跑 `claim_unit_plan.run()` + `claim_unit_argument.run()`，验证不触发多批目录覆盖与 128KB 崩溃 |
| R5：Layer 2 Gate 0 量化校准 | `blood.json`、`2011 L3`（合法故事与示范） | `joy.json`（已知反向边 `E6`）+ ≥10 条植入错例，召回 < 100% 或假阳性 > 5% 即 Gate 0 不通过 |
