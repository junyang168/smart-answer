# 人子 CVP 上游六条 Claim 修复完成记录

> **读者**：流程负责人、Developer。
> **类型**：记录。
> **状态**：2026-10-20 修复完成；六条当前 ready，整批 18 条回读均 ready。
> **与代码对齐**：执行于 `9b8473c2`；本次未修改运行代码。
> **权威范围**：前次核验未决六条的实际修复及当前资格，不批准 CVP 连接。

## 完成结果

本记录接续[18 条核验记录](./claim_cvp_upstream_qualification_20261020.md)中的六条未决，不改写前次核验事实。既有 `context_review_actions` 的 scope repair 路径导入三个原 action 的全部 findings，保留旧回答和调用记录。每组新调用一次 GPT 提议、一次 Opus 审阅，三个操作均完成事务和程序回读，无剩余异议。资格类型为 `opus_and_program_readback`，Gemini 未启用。

| Claim ID | 当前资格 | revision | content SHA-256 |
| --- | --- | --- | --- |
| `DK-225842f2b3729e70856e-P04-CL007` | ready | 2 | `1ac5dd0523ecdd41c783c297af05e4a1e6821fa1b18c1e139ba5e9cfefed1b3c` |
| `DK-225842f2b3729e70856e-P04-CL008` | ready | 3 | `309432bf3c18279d9d47553f3ab62087d9fb99067507306cf9dade71395d040a` |
| `DK-557caff2c0fb-P02-CL004` | ready | 3 | `5996e285a23cd7bb755eb137d5d1b1aaa2de7be2a3e556581e8c8807eafae71c` |
| `DK-557caff2c0fb-P02-CL005` | ready | 3 | `dabacac94927fc7eda4a040982dbc3ed73e06a032b0d695c9639b4a13508a327` |
| `DK-557caff2c0fb-P02-CL007` | ready | 3 | `f188872b34a2776bce879a0bb365e7bd373bbdca8939903bb6587cae2a43ab76` |
| `DK-557caff2c0fb-P02-CL008` | ready | 3 | `209bdf9e5b3a0f3d07c8f3912b42c08207b3eefead774d7fc21220ded52040c8` |

## 实际改动及核验

2019 年 12 月组保留 CL008 的 statement；将其自身 E021-01 片段扩为同段连续原话，直接覆盖「耶穌的工作」并保留中间推论及「若不悔改」限定。同步 E021 摘要、论述角色与 CL008 occurrence。Opus 确认这是同一论证中的展开，没有借邻居 CL009 的证据，也没有把不悔改推论另塞进 CL008 的判断。

2012 年约拿组仅修改三条关系：

- CR004 改为 CL005 → CL006，并说明计日规则及三天合计支持表达等同的具体范围。
- CR005 改为 CL006 → CL007，与已有 reason 的论证方向一致。
- CR006 保留 CL008 → CL007，只改 reason，说明具体表达例证支持释经方法。

共六个数据库对象改变：CL008 的 occurrences、E021、E021-01，以及 CR004、CR005、CR006。程序逐字段核对实际后像与获准范围；六条 Claim 的 statement 均未变。来源物理文件哈希不变，未授予或执行 CVP 内容修改。三个操作全部完成后重新查询，六条目标及之前十二条的当前资格均为 ready，没有用较早的单组通过替代最终状态。

## CVP 交接与证据

#436 可消费以下最终结果中的当前资格及精确版本，再由 CVP 维护流程判断是否接入相应观点；Claim ready 本身不证明连接成立。此批没有待人工听录音或内容裁决的问题。

运行根目录：

```text
/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/claim-management-435/cvp-upstream-repair-20261020-v1
```

`verification.json` 保存六条当前资格、全批 18 条资格、实际变化的完整前后像、源文件不变证明与 round ID；`batch/result.json` 保存三个操作的终态。各组 `prior-metadata.json` 和 `owner-direction.json` 绑定原 action、全部旧 findings 与本次具体修复权限，原核验记录继续保留。
