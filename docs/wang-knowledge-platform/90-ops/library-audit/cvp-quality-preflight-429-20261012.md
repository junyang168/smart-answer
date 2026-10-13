# #429 零模型调用预检记录（2026-10-12）

> **读者**：Developer、评估负责人。
> **类型**：记录。
> **状态**：当前；只记录预检，不是完整质量评估。
> **与代码对齐**：2026-10-12，核对本次 `scripts/cvp-quality-preflight.py`、`scripts/cvp-quality-packets.py` 的实际运行及其 staging 内代码 SHA。
> **权威范围**：本次只读预检事实；不授予模型调用、数据修改或质量批准权限。

## 1. 范围与真实基线

本次使用 #429 独立工作树。程序没有 import `backend/` 模块，没有模型客户端、SQL 写入或 production checkpoint 更新。使用 `psql -XAtq -f -`，在 `REPEATABLE READ READ ONLY` transaction 中读取当前对象、抽样框和有限运行状态；不解析历史完整快照或祖先证明链。

独立输出位于 `/Volumes/Jun SSD/WangKnowledgePlatform/staging/cvp-quality-429/preflight-20261012-v3/`，实际核对 Volume UUID `8D2A5FB1-CC0A-4230-B338-188BF47B06F3`。包含原文的 snapshot、材料预览和来源路径均不进入 Git。

| 分母 | 实际数量 |
| --- | ---: |
| active CVP | 705 |
| active CVP 的有效连接 | 1,427 |
| 当前登记来源记录 | 224 |
| 可入选的物理来源 | 218 |
| 当前 active Claim 抽样框 | 15,228 |

来源框包含 204 篇讲道和 14 份母本，包含没有 CVP 连接的登记来源。另有 6 份母本缺少明确的 Project 类型，保留为资格未明，未进入反向来源抽样；不将未知类型自动算作合格来源。来源 aliases 按同一解析后物理路径去重，尚未推断不同路径是否同一录音。未登记的磁盘语料不在当前来源框内，不能以本框宣称全语料覆盖。

快照语义内容 SHA 按公开存储契约独立计算，排除作为存储 metadata 的 `revision`，与 SQL 保存 SHA 一致。读取前后 SQL 快照和物理文件／Project metadata SHA 一致。实际 worker lock 为 `immediate-recovery-20261002-v1/recovery.lock`，取得只读非阻塞共享锁后执行预检，没有写锁文件或启 worker。

只读 SQL 核对 #357 指定 campaign 的 156 个 sequence 行均为 `audited/succeeded`；35、37、120 的 `revisit_required=true`、`independent_audit_complete=false` 原样保留。其他旧 action 行的 `running` 不作为当前 worker 活跃证明，生产状态与本卡质量结论分开。

## 2. 暂定样本与材料预览

种子为 `42920261012`；按稳定 ID／物理路径排序，无放回分层抽样。CVP 按已知来源类型、单源／跨源、唯一身份 Claim 数量分层，逐层保存母体、抽取数和入选概率。来源单独抽样，最低每类 4 份；样本并非仅从已选 CVP 的来源取得。

| 样本 | 数量 | 说明 |
| --- | ---: | --- |
| 随机 CVP | 60 | 9 层；初步质量基线，不是全库质量认证 |
| 定向风险 CVP | 20 | 结构风险优先，再按角色复杂度、身份成员数量、ID 排序 |
| 两类 CVP 重合 | 2 | 保留两种入选身份，只组包一次 |
| 唯一正向材料单元 | 78 | 完整自身来源、全部有效连接及身份成员 |
| 完整来源反向样本 | 12 | 4 份母本、8 篇讲道 |

机械筛查发现 6 个 active CVP 没有有效等价身份成员，全部纳入风险样本。696 个 CVP 有成分角色，220 个有非身份角色；后两项是复杂度标签，不是错误数量。当前没有任何语义质量 verdict，不能据此报告缺陷率。

正向材料保留完整物理段落、原始 source index、时间、编辑标识、删除线和全部正文，不截尾。反向初读材料不含知识库结论；反向比较仅生成 context preview，尚缺独立观察、相关 CVP 全部其他成员、处置记录及最终盲评投影，不是可直接发送的完整请求。

实际生成 102 份材料预览：78 份正向材料，12 份反向观察材料，12 份反向比较 context。最大正向原材料为 832,812 bytes。使用可逆字段字典及从物理文字确定性重建 live text 的编码后，最大正向材料为 469,033 bytes；全部 102 份完成解码逐值一致验证。不宣称这些 bytes 已含模型 prompt／schema 或反向第二阶段观察，也不以此提前批准 600 KB 请求预算。

第一版曾过量读取历史运行 metadata，快照约 281 MB；第二版收窄为精确状态和引用，第三版完善风险优先顺序。旧版本保留为诊断材料，未删除或冒充最终冻结样本。第三版 snapshot 为 11,662,571 bytes。样本仍是暂定版，生成 profile、历史处置覆盖标签和调用碰撞证明尚未完成。

## 3. 验证与证据定位

使用 `backend/.venv`，本次新增 12 个测试通过，覆盖：输入排序不影响样本、无放回及入选概率、遗漏最后成员保留风险、身份与支持角色分开、alias 不重复计源、版本不自动改指、Project 类型未知／编辑视图排除、物理与 metadata 漂移、语义 SHA 不符、只读 SQL、既有输出不可覆盖、全文尾段／编辑文字／删除线边界和无损编码逐值还原。

以下是物理文件 SHA，不与文件内部的 canonical JSON SHA 混用：

| 文件 | SHA-256 |
| --- | --- |
| `baseline.json` | `078af523367889d602293bb2bbd579137d41462f9b8898f8e581f06da2363802` |
| `evaluation-plan.json` | `b8d664a97e81dd8b4a7cb94693f7f97895a511d9c7eb2fd5177e9b724f2fecb9` |
| `snapshot.json` | `f7d1b459475dddba2dfb59f871d42b608cb7286d971f7dd6bd92a51743844a74` |
| `packet-previews/manifest.json` | `633b72b210be4e09cd225ad7f424b0947bb10b41350724179f6ad698c58ab91e` |
| `compact-previews/manifest.json` | `51a377b9c5fdf47912285a6a4e1f5c21581f268f25e13e1fb11bab7f24092f3c` |

## 4. 未完成项

本轮模型调用为零，未修改 Claim／Evidence／CVP／连接／来源或生产状态。完整依赖及逐字锚点审计、生成 profile 与人工保留／merge／scope 扩展标签、精确历史同题预算碰撞、盲评输入去除旧裁决的递归检查、反向完整关联成员和 disposition、最终模型配置／实际请求 bytes／成本上限、语义判断、历史语境复核、加权区间和收尾验收仍待完成。

当前工具没有发送模型的能力。没有 `evaluation-acceptance.json`，没有全库质量 pass。#429 不结案，#430 保持 draft；只读预检或合成测试通过不能触发关闭本卡。质量定义与后续验收仍遵守[设计](../../20-knowledge/cvp_generation_quality_design_v1.md)及[流程](../../20-knowledge/cvp_generation_quality_workflow_v1.md)。
