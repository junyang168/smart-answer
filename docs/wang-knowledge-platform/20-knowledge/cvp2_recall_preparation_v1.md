# CVP 2.0 召回与共同材料准备入口

> **讀者**：Developer。
> **類型**：說明。
> **狀態**：當前；第一阶段实现，未启用正式 CVP 2.0 写入。
> **與代碼對齊**：2026-10-17（核对到 `fabd0a65`；`cvp_recall`、`cvp_claim_input`、`cvp_materials` 与 SQL `recall-preflight` 入口）。
> **權威範圍**：已实现的只读预检、模型阶段函数及共同材料接口；完整目标仍由[主控设计](./claim_cvp_management_2_design.md)第 8、9 节规定。

`backend.pipeline.cvp_sql_flow --action recall-preflight` 接受完整 Claim 清单和现有 grouping 契约，检查每条 Claim 当前资格及组内完整覆盖，规划后续召回。它不创建 grouping、不调用模型、不写 SQL；结果文件写入指定的工作目录。禁止带 `--execute`、旧 campaign 或 run ID，不能借入口升级历史任务。

## 请求与结果

请求是带 `artifact_sha256` 的 JSON；用 `backend.api.canonical_repository.cvp_recall.seal` 封装以下正文。所有路径必须为绝对路径。

```json
{
  "schema_version": "wang_cvp_recall_preflight_v1",
  "claim_ids": ["CL-example"],
  "grouping": {
    "scope_label": "实际主题",
    "groups": [{"group_key": "accepted-group", "claim_ids": ["CL-example"], "rationale": "已接受分组理由"}]
  },
  "cvp_policy": "/checkout/backend/pipeline/policies/wang_cvp_resolution_policy_v6.json",
  "claim_input_policy": "/checkout/backend/pipeline/policies/wang_cvp_claim_input_v1.json",
  "index_root": null,
  "recall_request_bytes": 500000,
  "recall_max_calls": 0
}
```

```bash
backend/.venv/bin/python -m backend.pipeline.cvp_sql_flow \
  --action recall-preflight --manifest /absolute/request.json \
  --output-root /absolute/readonly-preflight
```

每组最多 20 条目标 Claim，清单必须无漏无重。检查实际当前资格，原样保留 `opus_and_program_readback` 及独立审计未执行的披露；真 hold、陈旧证明、未登记的新字段均拒绝。输入 policy 明确登记定位字段、语义背景字段和机械字段，实际使用的完整 policy 绑定在输出中。

`index_root=null` 且存在 active CVP 时，返回 `status=index_required`，列明当前 head、资格及已有连接，不能当作可以新建 CVP。空 Registry 的召回为零调用。提供索引目录时，只复验已保存 GPT summary 与 Opus review 的请求、回答和当前 head；缺文件或不通过时不补发模型请求。只有完整当前索引有效，且所有组／分片的实际编码请求与总额度都合格，才返回 `prepared`。

`prepared` 只表示召回输入已准备，不是模型批准、提交许可、完整 operation 完成或生产准入。字面主题清单的语义穷尽和 grouping 是否合理仍须有原来的业务依据。

## 已实现的阶段函数

`backend.pipeline.cvp_recall` 在既有 `viewpoint_resolution_runtime` 上提供索引初始化及召回阶段函数。先规划所有真实请求大小，再调用；保留完整输入材料、intent 和 raw 回答，绑定实际模型／prompt／schema／生成配置。失败、拒绝和 unknown 不自动重复。召回逐 Claim 返回合法短引用并合并全部分片，无 top-K，已有 active 连接直接带入。索引 summary 少于 150 个 Unicode 码点，必须经独立复核。初始化函数目前要求完整当前 head cut；尚无正式存量维护 action 或增量卡片更新入口，不应用它每次重做全库摘要。

这些函数供后续获准 action 集成使用；本阶段 CLI 没有暴露真实模型调用或提交按钮。调用者仍须使用原 action 的稳定 root、总预算及当前输入核验，不能拿函数参数或换目录替代持久授权。

`backend.pipeline.cvp_materials.prepare_materials` 经公开 store 方法按候选读取共同材料。本组 Claim 带实际 Evidence、fragment、citation 及其相关 H2 小节完整段落；候选默认只有完整 CVR、全部当前成员表述及连接角色。成员依据按明确请求加载；维护范围可准备全成员材料，不受目标 Claim 的 20 条上限截断，但材料准备不授予维护写权限。

无小标题的范围以稳定段落定位，不默认载入整篇。`expand_source` 要求合法来源／当前位置／具体理由，依次允许相邻小节和完整正文各一次，拒绝重复、跳级、来源漂移；新版本材料保留上版绑定和请求。小标题始终标为 metadata。GPT／Opus 的调用方必须消费同一份 `material`，不能各自另找原文。

`backend.api.canonical_repository.cvp_claim_input` 给判断及限定字段分配短引用，保存原字符串的零基、半开 Unicode 码点位置。模型只选择引用与位置；程序提取逐字文本并检查完整覆盖、越界及重叠。此检查不宣称语义完整，不批准观点身份。只读成员不进入目标成分分母。

## 尚待接入与验收

完整 CVP 2.0 尚需：持久 action／总预算接入、卡片维护及增量 recall、实际候选 delta 编译与首轮计划冻结、有界多轮完整 Opus 复核、维护权限与影响后像、共用 SQL 提交／回读／恢复／影响结案。新流程不调用常规全库备份或额外模型终审的要求，须在该提交路径接入时验证；历史入口仍按原契约运行。

正式召回质量、分片位置／大小敏感性、summary 对照、真实模型调用和两种规模全流程成本仍待有界验收。本阶段的模拟回答测试只验证程序契约，不充作真实内容批准；#436 和 #357 保持开放。
