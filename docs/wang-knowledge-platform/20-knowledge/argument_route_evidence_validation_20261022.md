# 论证层直接依据判准验证（2026-10-22）

> **读者**：Solution architect、Developer
> **类型**：记录
> **状态**：当前
> **与代码对齐**：2026-10-22（核对到 bf083c1f）
> **权威范围**：本次规则变更与实测结果；不宣称旧 CVP 已完成重新审核，不作神学判定。

## 变更

现行 [Canonical Viewpoint 规范](./canonical_viewpoint_design.md#5-论证路线)允许直接经文依据、原文观察和例证形成短路线，不要求额外推理桥梁。支持存在与充分性分开，逐篇实例保存支持范围说明；完整实例只表示路线必需步骤得到该来源见证，不代表结论已获穷举证明。

新默认 policy 为 `wang_route_resolution_policy_v6`；v5 的三个 prompt 和 policy 均保留原样。新 policy 指纹：`2f393abc2bcdb35b6de7205079a1c34c1aa311cf1b2b7cefaab1d81c77386dce`。v6 的 packet 带 `evidence_semantics=source_support_v1`；实例的已审阅 `reason` 保存为 `support_scope_note` 并参与实例身份，避免说明改变却复用旧实例。v5 的编译输出和身份不增加该字段。

## 正反对照

输入为 `backend/tests/fixtures/route_evidence_semantics_v6.json` 的人工合成测试材料，全部为测试数据，未写入教授知识库。预期只由本地 evaluator 比较，不送入模型。各例使用正式 whole-CVP 提议／独立审核流程：GPT 提议、Opus 5.5 high 审核。

| 测试 | 预期 | 实际 |
| --- | --- | --- |
| 经文直接引述并用于建立观点 | 有短路线 | 有，首轮通过 |
| 只有观点断言 | 无路线 | 无，首轮通过 |
| 同一个断言正反重复 | 无路线 | 无，首轮通过 |
| 明确指认一个文法用例以支持全称概括 | 保留有限支持并注明范围 | 有，首轮通过；说明只展示一例、未展示全部用例检验 |

本地原始输入、模型回答、审核与结果保存于 `staging/route-evidence-v6-controls-20261022/`，未使用 Registry writer。四例均通过预期检查。

## 真实案例

目标：`CV-22b94fdd041005a381f6`，当前 revision `CVR-b8bde94bbca3685201b3`，statement「耶稣每次说『人子』时都使用定冠词」。输入包括全部 3 条有效连接的 Claim 和 3 篇来源相关原文小节；材料 SHA：`6d98f246d956cb10c5a372099bcb1b88abede769427c70c800ea9869a8bdfec1`。

GPT 首轮提出 observation → conclusion 的一条路线和两条来源实例。Opus 指出两个节点都来自同一语法概括，来源没有另将某一经节的具体形式指认为该概括的例证；邻近经文、中文缺冠词的说明以及后续对但以理书所指的解释，不能自动成为本概括的支持。GPT 接受异议，撤回路线并保留 no_route，第二轮 Opus 通过。没有添加桥梁，没有降低标准来强留路线。

这区分了「原文在讲相关经文」与「教授将这处具体用法作为本观点的依据」；本次结果不否认教授持有这个观点，不裁定观点真伪，也不宣称全库没有其他材料。之前人工解释把邻近经文直接算成冠词例证，缺少这一步核对。

- 完整输入与记录：`staging/route-v6-definite-article-20261022/output/CRJ-191691566eafb6cff7e82577/`。
- 审核轮次：2；最终审核 SHA：`3d0876187079bbba5021c59d316384fdfc33bd3c3dd5a7bfa7384afc50346f44`。
- worker 终态：`applied_readback_verified`；实际数据库变更 0，apply 返回 `unchanged`。
- 原 v5 输入、提议和审核记录未修改。

## 程序检查

使用 `backend/.venv` 执行路线提议／编译、观点数据模型、来源 packet、运行时 projection 与生产约束相关测试，共 304 项通过。新增检查覆盖无 bridge 短路线可编译、支持范围落库及其身份变化、缺失或跨来源绑定仍拒绝、v5/v6 job 身份不同且默认采用 v6。

`python3 scripts/check-docs.py docs` 保留既有失败：另一项目的 `docs/notes-to-sermon-agent/functional_spec.md:120` 引用不存在的附录锚点；本次未修改该项目。

## 后续根因校正：相同 packet 的 v7 对照

负责人指出，审核标准才是主要根因。上节 v6 的 no_route 结果不是对该 CVP 依据的最终判断。原 packet 已包含教授指出定冠词 The、解释 The Son of Man 具体形式的文字；缺少 S0013 的以西结对比不是否认这些现有观察的理由。后续补料问题与本次误拒必须分开。

v7 修正了生成、复核与更正三方共用的判准：保存教授实际指出的语言形式／差别，不要求他额外提供希腊文字母、逐节检验或穷举证明；同一片段可以同时包含文本观察与概括，不能仅因节点使用相同 Claim／EvidenceStep／SourceFragment 就判循环。纯断言及其正反改写仍不构成路线。实现提交为 `fb67c7cd`；v6 的旧规则和原始审核保持不变。

程序确认 v6、v7 的 `route-packet.json` 完全相等，SHA 同为 `0daf86fa66fb2acb3e37fd3a83b92e2b86490c692465c1da3d7139980e233b1f`；材料 SHA 同为 `6d98f246d956cb10c5a372099bcb1b88abede769427c70c800ea9869a8bdfec1`。未添加 Claim、CVP 连接、来源小节或 Evidence。精确保存的实际原文回归材料位于 `backend/tests/fixtures/route_textual_observation_v7.json`，没有加入「这是一个例子」之类原文没有的提示。

GPT 提出一条观察 → 结论的短路线、两条来源实例。Opus 首轮确认文本观察与短路线成立，仅要求把 2021 来源独有的无冠词形式对比移出两篇共有骨架；GPT 接受并保留该对比在 2021 实例说明中，第二轮 Opus 对完整集合全部通过。这次更正处理来源差异，没有补造推理桥梁，也没有扩充输入。

结果：新增 1 条 ArgumentRoute、1 条 revision、2 条单来源 attestation，共 4 项数据库写入；ChangeSet `KCS-668fbb6ad586c6a80065`，最终审核 SHA `564d3872dee409650634e5fff2095d7daf69440e05c1b66ae2bceec3af26966a`，worker 状态 `applied_readback_verified`。又按计划内容逐条读取数据库，核对已保存内容完全一致；实例支持范围说明也已保存。

`staging/route-evidence-v7-controls-20261022/` 中两项反例再次经 GPT／Opus 运行，纯断言和同义重复均保持无路线。304 项相关程序测试通过。先前 v6 对照中的合成正例不构成真实案例已修复的证明，本次相同真实 packet 对照才检验了本次根因修正。

## 负责人要求回滚审核逻辑

负责人明确指示「你还是 roll back 论证层审核的逻辑吧，现在问题出在 claim 上」。据此撤回 v6／v7 放宽判准，whole-CVP worker 的默认与可执行入口恢复 v5，生成、审核、更正均使用原 v5 prompt；归档 policy／prompt、原始模型回答和本记录保留。v6／v7 可以作为历史数据读取，但不再允许新入队或执行。保留已存记录的可选字段解析兼容性，不把它接回当前生成／编译流程。

按原 v7 结果的精确 ID 和内容核对后，软退役仅由该测试新建的 1 条路线、1 条 revision、2 条实例，共 4 项；未修改 Claim、CVP 或连接。撤回 ChangeSet 为 `KCS-e60e68fbb5e962f7b7f0`，已逐条回读确认退役。计划和回执保存于 `staging/route-review-policy-rollback-20261022/`，没有新增模型调用。

306 项相关测试通过，包括默认恢复 v5，以及归档 v6／v7 无法新入队的反例。本记录上方的模型试验仍是当时结果，不作为当前生产判准或现行路线。后续应核实 Claim 本体、证据及 CVP 连接，不在论证层自行补救这些问题。
