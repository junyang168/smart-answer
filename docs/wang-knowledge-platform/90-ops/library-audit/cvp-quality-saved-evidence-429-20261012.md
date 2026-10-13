# #429 历史证明与固定样本报告记录（2026-10-12）

> **读者**：Developer、评估负责人。
> **类型**：记录。
> **状态**：当前；本次只读核查完成，整体评估未验收。
> **与代码对齐**：2026-10-12，核对 `cvp-quality-saved-evidence.py`、`cvp-quality-report.py` 实际执行及 39 个相关测试。
> **权威范围**：保存证据的版本匹配、拒答原件认证和当前固定样本限制；不是内容或生产批准。

## 1. 实际核查结果

承接[有界覆盖评估执行记录](./cvp-quality-coverage-execution-429-20261012.md)，本阶段新增模型调用为零。没有重跑旧审计，没有读取整库 before／frozen／readback 或递归历史祖先链。

对 48 个正向样本涉及的 43 份精确 SHA-bound 历史审计报告，逐项读取其完整 read set：8,871 个唯一对象全部存在，重新计算语义 payload SHA 全部相符，revision／content SHA／retired 状态全部与报告匹配。核查前后结果一致，收尾 SQL 和物理来源仍与冻结基线一致。

此前“历史 pins 超出准备 read set、不能确认完整版本”这个限制已得到解决：43 份报告现在均有完整当前版本匹配证明。它们仍是历史报告，不变成新的盲评或六维度质量 pass。

43 份报告对应的 sibling group intent：34 份读取集合相同，6 份集合不同，3 份没有该位置的 intent。6 份差异中，共享对象没有版本冲突，但存在 intent-only／report-only 对象。完整差异 ID 已保存；不擅自补造当时的请求，也不把 group intent 当成实际 provider request。

## 2. 真实拒答原件

按结果内嵌的 `provider_content_block` 精确指针，另外验证 4 份 provider 拒答原件的物理 SHA、保存的 model／prompt／schema 和由 model／prompt 导出的 cache 身份。真实拒答与后续非独立补判分别保存，`independent=false`、`revisit_required=true` 不改写。

共享目录的一次粗略文件分类曾将 `.provider-block.json` 归进缓存类别；本次通过精确指针检查予以区分。不能从“普通判读缓存没有完整原回答”推断“没有保存拒答原件”。

这些原件证明实际 provider 拒答，不证明其完成语义判读；原 HTTP 请求字节没有在该原件中得到认证。完整独立语义 raw 认证单元仍为零，不产生重试权限，不解除 35／37／120 的历史限制。

## 3. 固定样本与六维报告

为原 78 个唯一正向单元（随机 60、风险 20、重叠 2）逐个保存历史候选、profile、每个正向维度的证据身份及受限原因。48 个有本次定位的历史候选；另外 30 个没有这些当前完成报告中的候选，不能据此宣称从未被审核。

12 个完整来源单元全部保留，关联原调用结果与当前 Claim／CVP 数量。另保存当前 Claim ID 与 #357 原始 973-Claim roster 的字面交集，不从字面交集推断 lineage、完整 disposition 或生产遗漏。4 个无当前 CVP 的来源，其当前 Claims 均不在原 roster 的字面列表中；这一事实不能算成 #357 的生成缺陷。

当前报告对原文忠实度与归属、限定与范围、身份归并、观点粒度与重复、连接角色、来源到观点覆盖均标 `not_assessable`。前五维的固定单元数各为 78，覆盖维的固定来源数为 12；完整独立语义评估数为 0。已通过引用验证的 6 份 source-only 观察只是中间证据，不冒充已完成来源覆盖核对。

内容缺陷数、全库缺陷率和置信区间均为 null，不能写成“零缺陷”。随机和风险分母分别保留，不从受限样本外推总体。scope 扩展抽样标签及完整来源 disposition 仍未认证。

已生成 `quality-report.md`、`metrics.json`、`fixed-unit-results.json`、`evaluation-acceptance.json`。验收文件明确 `accepted=false`：全固定样本已有证据状态或受限记录，但整体语义证据及设计覆盖还未达到验收要求。它不是生产批准。

## 4. 保存位置与证明

沿用既定 UUID-verified Jun SSD staging `preflight-20261012-v3`，不移动或改写旧原件。第一次报告 v1 保留；v2 增加精确拒答原件证明，不删除原结果或重置 once。

| 文件（相对于本次 staging） | 物理 SHA-256 |
| --- | --- |
| `saved-evidence-verification-v1/manifest.json` | `6b0d169add8e7fd6fd4ba7294b52c9e917feaa002087003194ea5446578f5e61` |
| `saved-evidence-verification-v1/current-pins.json` | `3c877ca2f6ea4c96cf4aaafbcf4f755bbc2485ca8654e1b500c666c09bab3adb` |
| `saved-evidence-verification-v1/report-proofs.json` | `834cac337d5ea473228b47e8044e742883a75f8c337bfa1ffb3fa45d4d89adc9` |
| `saved-evidence-verification-v1/forward-unit-evidence.json` | `4b717ccf41464b55cc57e3e702e601e5469f359533f2622ce183f4185c6b6f3c` |
| `quality-evidence-report-v2/metrics.json` | `8b47163a123e57d2eedce8308e11eaa3ac516121237c8ad1e7721f503c682fa5` |
| `quality-evidence-report-v2/evaluation-acceptance.json` | `f3c81a24e14471521706962c3740dd47f17dfac8cba493cce24937da409101cc` |

## 5. 验证与剩余工作

39 个相关测试通过，新增反例涵盖最后一个历史 pin 不丢失、缺对象／retired 差异、实际内容 SHA 重新计算、匹配版本不升级 raw 或质量 verdict、group intent 集合差异、重复 pin 拒绝、拒答不能成为批准、错误 prompt 身份与非拒答 raw 拒绝，以及未评估分母不能成为零缺陷率。

本阶段 Registry、教授原文和生产状态写入均为零，新增模型调用为零；原累计实际调用仍为 15。没有文章、发布、部署、migration／admit 或 #428 合并。

剩余工作是取得可合法使用的完整独立维度证据，确认 scope 标签及来源 disposition，并据此完成真实内容判断与设计验收。保存文件不足以恢复当时未保存的 provider 原回答；不能用程序重建的请求充作原始调用证明，也不能因本卡换阶段重新问耗尽或 unknown 的同题。#429 保持 open，#430 保持 draft。
