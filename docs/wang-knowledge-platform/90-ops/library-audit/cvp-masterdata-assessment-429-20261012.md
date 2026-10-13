# #429 跨讲道观点 Master Data 补充评估（2026-10-12）

> **读者**：评估负责人、Solution architect、Developer。
> **类型**：记录。
> **状态**：当前；本轮补充模型评估完成，整体质量尚未验收。
> **与代码对齐**：2026-10-12，核对本轮独立脚本实际输出及 47 个相关测试。
> **权威范围**：本轮观点身份、共同成分与连接角色的评估证据；不是生产批准或神学判断。

## 1. 本轮目的和授权

用户明确指出 CVP 是 master data，要求重点判断分散在不同讲道中的相同或相似观点能否准确识别。本轮检查同一 CVP 内的跨来源身份成员，也检查不同 CVP 间的重复、共同成分和应保留的差异。

按用户指定使用 `claude-opus-5-5`、`high`，provider 的 `modelUsage` 验证实际模型。用户“调用大模型次数不应受限”取代本卡此前固定总调用额度；每次新请求仍保存独立 intent、请求、原回答和结果，不覆盖旧原件。旧 Gemini 结果及生产历史方向保留。

使用 Claude Code 订阅，移除 API billing/routing 环境变量；评估进程没有工具、MCP 或仓库会话持久化权限。运行材料包含完整当前成员来源和限定角色说明，不发送完整架构规范，不导入 backend 生产代码。

原生成流程使用过 Claude，本轮明确 `independent_from_original_generation=false`。这是新的补充语义意见，不能当作独立模型家族一致通过。

## 2. 固定样本实际结果

原随机 60、风险 20、重叠 2 的 78 个唯一 CVP 均取得完整回答。逐条覆盖其 275 条当前连接：模型给出 254 条 appropriate、12 条 misclassified、9 条 uncertain。这些是角色评估意见，尚不是确认缺陷或修订批准。

| 模型意见 | 唯一 78 单元 | 随机 60 | 风险 20 |
| --- | ---: | ---: | ---: |
| canonical faithful | 74 | 58 | 17 |
| canonical over_generalized | 1 | 1 | 0 |
| canonical unsupported | 1 | 0 | 1 |
| canonical uncertain | 2 | 1 | 2 |
| cross_sermon supported | 26 | 13 | 14 |
| single_source | 46 | 46 | 0 |
| no_identity_members | 6 | 1 | 6 |

随机与风险有重叠，不能把两列相加；`faithful` 也不是整个单元质量 pass。69 个单元的全部当前来源满足原始来源资格，9 个单元含资格元数据未满足的来源。已提供文字的语义意见保留，但不把来源资格缺口当作内容错误，也不宣称已验证教授原始来源身份。物理文件不同不自动意味着讲道内容独立：笔记与录音可能同源。

例：`CV-06c61e676e052d27d0a2` 在 2016 NYSC 和 011 WSR 两份不同讲道原文中，将耶稣句首 Amen 的权威宣告识别为共同核心。另一份来源中的相同成分目前是 supports、另有原文成分可能未抽取 Claim，均保留为召回线索，不能自动改角色或生成新观点。

## 3. 全库提名及完整来源配对

向提名模型提供冻结的 705 个 active master record 的命题、范围、身份来源和成员数量，不提供旧判决。模型提名 54 对：22 对 possible_same_identity，32 对 related_distinct_control。提名是候选发现，不是全库召回真值集。

54 对涉及 104 个不同 CVP。91 个节点能构造完整成员与原始来源包；13 个节点含资格未满足的来源，影响 11 对。43 对完成完整来源评估，11 对保留 `source_provenance_not_assessable`，不从分母移除。

最终意见为 6 对 shared_component_distinct_records、37 对 related_but_distinct。possible_same_identity 提名中，18 对可评估，3 对共享成分、15 对相关但不同；控制提名中，25 对可评估，3 对共享成分、22 对相关但不同。不能据此声称全库没有重复或达到某个召回率。

初始二分类回答曾将 3 对称为 same_master_identity，却在理由中要求删改范围或条件才能合并。补充 schema 明确区分完整命题身份与共同成分，再对这 3 对重新阅读全文，全部给出 shared_component_distinct_records。旧回答与新回答同时保留，不能把它们算成两个独立投票。

例：`CV-04aeb1023dcda17a358f` 与 `CV-f525c6f429ab3b4d16c5` 在 2007、2020 两篇讲道中共享“再临荣耀使太阳相形失色”的解释。一条命题包含日月，另一条明确只涉及太阳，当前完整记录不等价。共同成分应可被发现和关联，月亮及范围条件必须保留，不能为合并而删除教学内容。

## 4. 引用、上下文和历史区别

引用逐字绑定与语义正确性分开记录。source-05 原 7 条观察经新模型阅读全文，全部被判断为 faithful；其中有摘要或跨删除片段的引用问题。这证明不能从引用漏字直接推出观点错误。模型还给出 3 条额外覆盖线索，未自动加入内容库。

`CV-031b1f6eec4157cd37fe` 当前只有 supports，没有身份成员；新模型认为当前提供来源不能支持其完整 canonical。历史创建 decision 的身份依据却是另一条 Claim，现不在当前身份成员中。因此首先是当前锚点及历史方向需核对，不能把本次意见写成“教授从未教过这个观点”。本轮报告逐单元保存原 basis decision 的成员与当前身份成员差异，不覆盖历史批准。

78 单元与配对评估共有 4 个 prompt cohort，后续加强了语境解释、来源资格披露和共同成分区别。报告保存每次 prompt/schema/code SHA，不能假装是完全相同协议下的独立重复实验。

## 5. 保存、验证和剩余范围

原文与 raw 留在 UUID-verified Jun SSD 的 `preflight-20261012-v3`，不进入 Git。本轮保存 127 个 CLI 请求 intent：master-data 125 次中 124 个完整回答、1 个格式失败；另有 1 次全库提名和 1 次 source-05 补评。CLI 内部模型轮次分别记录，127 不是实际内部模型轮次数或新的额度。一次 allocation lock 冲突发生在调用前，修复后另行执行，未伪造回答。

最终报告 `masterdata-opus-evaluation-v1/report-7533018a2a46de1cf3e5.json` 的物理 SHA-256 为 `3c0d0ed6c39fe24cc324d62219be54552a1109006ce0f048e50fba000dfe20a7`。每个意见重新核对 request、intent、provider raw、实际 modelUsage 和 structured_output。

完整材料稳定性核对覆盖 169 个包、359 个不同 Claim、3,057 个依赖对象，SQL snapshot 与物理原文均未变化。47 个相关测试通过。文档检查只有 `docs/notes-to-sermon-agent/functional_spec.md:120` 的既有缺失锚点，不修改另一项目。

本轮 Registry、原文与生产状态写入均为零，没有文章生成、发布、部署、迁移或合并。缺陷数、全库准确率与召回率保持 null，`quality_accepted=false`。

剩余范围是原 basis 与当前身份缺口的语义复核、角色及粒度线索的裁定、未满足来源资格的材料核对，以及原六维质量设计需要的独立证据与来源覆盖。#429 保持 open，#430 保持 draft。本记录补充先前的零独立证据报告，不改写那个阶段的结论。
