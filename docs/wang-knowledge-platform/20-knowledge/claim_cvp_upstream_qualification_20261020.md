# 人子 CVP 候选 Claim 当前资格核验记录

> **读者**：流程负责人、Developer。
> **类型**：记录。
> **状态**：2026-10-20 的核验结果；12 条 ready，6 条未决，不表示整批修复完成。
> **与代码对齐**：2026-10-20，执行于 `809daec8`；本次没有修改运行代码。
> **权威范围**：#436 委托 #435 核验的 18 条候选 Claim；不批准 CVP 连接，不代表完整人子语料覆盖。

## 核验方法与边界

18 条分成 11 个既有联合 Update action，每组最多两条，以既有内容作为候选。使用 joint Update v6、完整自身来源、自身证据与直接邻居；Opus 审阅，异议交 GPT 判定，程序提交和回读确认当前资格。Gemini 未启用，资格类型是 `opus_and_program_readback`，不是独立终审通过。

本轮绑定零内容写权限，未修改 Claim、Evidence、关系或 CVP。没有因主题相近增加经文引用或替换教授的判断。通过项保留原版本与正文；未决项不供下游作为 ready 使用。

两篇来源登记的文件哈希落后。程序确认正文及口语文本哈希均未变后，仅更新来源文件与编辑结构登记：`SRC-2022_NYSC-8954e8cd252f`、`SRC-2019_06_16-1cda85ca81c0`，ChangeSet `KCS-c1ba0f876c52c389bf5a`。来源文件未编辑，失效依赖数为 0。

## 当前合格的 12 条

以下均完成 Opus 审阅和程序回读，最终再次查询当前资格为 ready。精确 revision、content SHA、run ID 与审核文件绑定见最终 JSON。

| Claim ID | 本次内容修改 |
| --- | --- |
| `DK-16807c15404bfc4c24b2-P10-CL001` | 无 |
| `DK-584379d7e5c41649067c-P08-CL003` | 无 |
| `DK-584379d7e5c41649067c-P08-CL004` | 无 |
| `DK-1cda85ca81c0-P02-CL003` | 无 |
| `DK-1cda85ca81c0-P02-CL004` | 无 |
| `DK-5f9ef2b37e23-P04-CL005` | 无 |
| `DK-5f9ef2b37e23-P04-CL008` | 无 |
| `DK-5f9ef2b37e23-P04-CL011` | 无 |
| `DK-c9084afc125c-P11-CL011` | 无 |
| `DK-4eca17ff4e50fa50aa01-P05-CL002` | 无 |
| `DK-4eca17ff4e50fa50aa01-P05-CL003` | 无 |
| `DK-4eca17ff4e50fa50aa01-P05-CL010` | 无 |

## 未决的 6 条

| Claim ID | 未决原因 |
| --- | --- |
| `DK-225842f2b3729e70856e-P04-CL007` | 本体与自身证据审阅通过，但与 CL008 的联合操作未完成；没有当前 ready 资格。 |
| `DK-225842f2b3729e70856e-P04-CL008` | 「或耶穌的工作」未被自身 E019、E021 的片段覆盖；相关句子目前绑在邻居 CL009 的 E023。 |
| `DK-557caff2c0fb-P02-CL004` | 本体与自身证据未被质疑，但与 CL005 的联合操作未完成；没有当前 ready 资格。 |
| `DK-557caff2c0fb-P02-CL005` | 自身内容与证据成立；关联 CR004 的 supports 方向与原文推论相反。 |
| `DK-557caff2c0fb-P02-CL007` | 自身内容与证据成立；CR005 端点方向及 CR006 的 reason 误记论证作用。 |
| `DK-557caff2c0fb-P02-CL008` | 自身内容与证据成立；与 CL007 相连的 CR006 的 reason 方向错误。 |

上述实质异议均经 GPT 对照原文认可。三个 action 停在 `permission_required`，不是模型答复丢失、额度不足，也没有反复开启新审核预算。这是本轮只核验、不写内容的执行边界；未决不等于必须请人听录音或作神学裁决。

### 后续修正的具体范围

2019 年 12 月来源 S0046 确有「你把耶穌的工作當作魔鬼的工作，若不悔改，就不得赦免」。后续应判断并补足 CL008 自身证据绑定及论证说明，不能把邻居证据默认为自身证据，也不能只为通过审核删掉原文确有的教导。若确需收敛定义，须据原文论证并审核。

2012 年约拿组的三个问题均在关系，不要求修改四条 Claim 的正文：

- `DK-557caff2c0fb-P02-CR004`：原文以计日规则与三天合计支持表达等同；GPT 判定应由 CL005 支持 CL006，并相应明确 reason。
- `DK-557caff2c0fb-P02-CR005`：reason 和原文均以表达等同支持释经方法，应由 CL006 支持 CL007；现有端点相反。
- `DK-557caff2c0fb-P02-CR006`：现有 CL008 支持 CL007 的方向成立，但 reason 把方法写成不同表达无需修改的原因，应改为具体例证支持方法。

修正应沿原 action 的正式接续保留本次 findings、回答及预算历史，完成审核、事务与回读后再查询当前资格。不把单条审阅通过当作联合操作完成。

## CVP 交接与运行证据

Claim ready 仅表明本次 Claim 管理资格成立。#436 仍须独立判断它是否实际支持指定观点及论证路线；来源实际引用《马可福音》的，不因候选 CVP 属于《马太福音》而改引用。本轮没有新增或改写任何 CVP 连接。

运行根目录：

```text
/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/claim-management-435/cvp-upstream-qualification-20261020-v1
```

最终消费文件是 `final-qualification-results.json`，含全部 ID、当前资格、版本、内容 SHA、action 结果、Opus 异议及 GPT 判定路径。较早的 `qualification-results.json` 是执行中快照，不能作为最终汇总。`batch/result.json` 与 `next-batch/result.json` 保存两批执行结果；来源登记证明见 `editorial-source-bindings-proof.json` 和 `editorial-source-bindings-result.json`。
