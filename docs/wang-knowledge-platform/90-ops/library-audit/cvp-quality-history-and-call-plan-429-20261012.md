# #429 历史、反向成员与调用方案记录（2026-10-12）

> **读者**：Developer、评估负责人。
> **类型**：记录。
> **状态**：当前；零模型调用准备，尚未得到调用授权。
> **与代码对齐**：2026-10-12，核对本次 `cvp-quality-history.py`、`cvp-quality-call-plan.py` 的实际运行及 staging 内代码 SHA。
> **权威范围**：本次准备事实与拟议调用范围；不批准内容、模型执行或知识库写入。

## 1. 当前材料与历史身份

本次续接[零调用预检](./cvp-quality-preflight-429-20261012.md)的同一基线，输出仍在该记录指定的独立 Jun SSD staging，UUID 已核实。原文和历史判读不进 Git，没有读取旧整库 before／frozen／readback 或递归祖先链。

为 12 个反向来源样本展开全部当前关联 CVP、有效连接、完整其他成员及各成员完整自身来源。与原 78 个正向单元合计涉及 144 个 CVP。反向 source-only 观察材料与完整对照材料分开；完整对照包含目标来源本身，不依赖模型记住上一次输入。

实际通过 SQL 中当前 CVR provenance 和连接 decision ID 读取精确身份记录。原正向样本 78 个单元全部找到所引用的 decision；按这些历史记录的模型标签，53 个只涉及新版 GPT／Opus，18 个只涉及旧版 GPT／Opus，6 个涉及混合版本，1 个只有 Opus 标签。标签仅说明历史声明，不冒充新运行或 provider raw 认证。

多值 policy 标签覆盖普通批次、来源 reconciliation、人工保留、Claim 修订、连接修复、跨分区支持、已提交观点合并等。原正向样本中有 1 个明确 `human_exception_review`，其方向没有改成模型一致。scope enrichment 的完整标签覆盖仍需单独核对，不宣称全采样设计已最终验收。

## 2. 历史碰撞不是新批准

从指定 campaign 的 SQL completion 读取精确 audit path／物理 SHA，逐个只读验证保存报告文件。48 个原正向样本出现于这些历史报告中；旧 verdict、revision 和 read pins 分别保存，未传给盲评材料。

目前没有任何一份相关报告获得“完整整组当前 read set 已逐项确认”的结论：存在超出本次准备 read set 的历史 pins，不能把没有读取的部分当作相符。当前已读取的相关 pins 未发现版本差异，但这不认证原 provider request/raw/cache，也不把旧报告升级为六维度完整评估。程序将 `provider_raw_authenticated=false`、`fresh_blind_evidence=false` 和 `new_forward_calls_allowed=false` 分开保存。

35／37／120 的拒答与 independent-incomplete 标记保持。旧 report／raw／reject／upheld／人工保留／once／unknown 不改写，不因本卡换 root 再仲裁。原正向 78 单元的新调用上限仍为零；未获认证的历史证据只作碰撞警示，不用来声称质量通过。

## 3. 拟议的新覆盖调用

新问题限于“完整来源里重要教学判断及限定，在当前知识库有没有明确去向”。不是重新问旧身份等价或连接异议。第一阶段只看完整来源，列出带逐字位置的独立观察；第二阶段以这些观察核对完整当前 Claim／Evidence／CVP／连接及其他成员原文。缺少 disposition 时只报告 coverage gap／uncertain，不把尚未生产的部分误判为生成失败。

调用材料进一步递归移除审核状态、理由、批准信息、历史 provenance 和 rejected highlights，保留来源归属与角色语义。旧裁决只留在分开的历史文件；本轮仍待完成运行前盲评投影检查、逐字回答校验与 once 执行控制。

按实际原 Claim 的 source alias 解析到物理路径，对 35／37／120 采取保守的来源及关联上下文限制：`source-03`、`source-09` 不发新调用；`source-10` 可拟议 source-only 初读，但完整对照含受保护来源，第二阶段限制。固定 12 个来源样本全部保留，不替换、缩文或换 provider 绕过限制。

| 拟议预算 | 上限 |
| --- | ---: |
| 完整来源初读 | 10 次 |
| 条件式覆盖对照 | 9 次 |
| 新调用合计 | 19 次 |
| 新正向重审／旧同题仲裁 | 0 次 |
| 每次输出 | 16,384 tokens |
| 每次请求绝对上限 | 2,500,000 bytes |
| 独立观察回答用于第二包 | 每单元 131,072 bytes |
| 全部实际请求及第二包回答追加预算 | 7,567,737 bytes |

模型拟采用历史实际第三方独立审计使用的 `gemini-3.8-flash`，backend 固定为 Google Generative Language REST，temperature 为 0，串行执行，每单元每阶段最多一次。输入逐值无损编解码，完整请求 JSON／schema 已编译；第二阶段模板在实际观察验证后才能形成最终请求。精确每条输入上限与 conditional stage 写在 `call-proposal.json`，不只使用宽松的全局单次上限。

这是一份新的、单独有界的覆盖评估预算，不修改原生产 600 KB 请求契约。预算以调用数、实际 UTF-8 请求 bytes 和输出 tokens 表示，没有美元费用估计。当前 `authorized_new_calls=0`，工具没有 HTTP 执行入口，也没有创建任何模型 intent；方案本身不是用户授权。

调用授权请求依据 #429 中“新评估调用方案、范围与预算须先明确”及交接中未授权额外调用的边界。即使后续获准本方案，也只允许这 19 个条件式阶段；不扩成全库六维度重审、不修改数据、不重开旧争议预算。

## 4. 验证与未完成项

本次 18 个相关测试通过，新增反例包括：嵌套旧裁决移除、来源角色保留、SHA-bound 报告必须逐字文件相符、缺失最后一个 read pin 不丢弃、报告碰撞不产生新调用权限、受保护源按物理 alias 判断，以及固定 schema／输出上限的请求编译。

实际历史与 source／SQL baseline 在准备前后核对一致。物理文件 SHA：

| 文件 | SHA-256 |
| --- | --- |
| `history-and-closure-v1/history.json` | `43d1052d4bb87925c1ab995b67804b062358182def3b2463aa99b46d1bc8ac50` |
| `history-and-closure-v1/manifest.json` | `c29f8d9f84947c0742f2172d33d109545016fd9ce40d30f02a7a6f5064f037fe` |
| `coverage-call-proposal-v1/call-proposal.json` | `05397fe31553dd96a6de9019ba930b46f933d6ef5f911b879a893ce2589f04d1` |

未完成：provider 原始回答认证、完整历史 pin 局部核查、scope 扩展标签覆盖、来源 disposition、回答引用与完整分母校验、once／unknown 实际执行反例、模型执行授权、真实语义回答、历史语境复核与指标报告。#429 未结案，#430 保持 draft；仍没有新的六维度质量 verdict 或 `evaluation-acceptance.json`。
