# CVP 2.0 执行入口与恢复

> **讀者**：Developer。
> **類型**：說明。
> **狀態**：當前；完整代码入口已实现，真实模型质量及扩大生产准入另行验收。
> **與代碼對齊**：2026-10-17（核对到 `81b9ae1b`）。
> **權威範圍**：CVP 2.0 的请求、执行、恢复与质量状态接口；业务规则以[主控设计](./claim_cvp_management_2_design.md)第 8、9 节及 [Canonical Viewpoint 设计](./canonical_viewpoint_design.md)为准。

## 1. 三个入口

三个入口均位于 `backend.pipeline.cvp_sql_flow`。使用 `backend/.venv/bin/python`，路径使用绝对路径。

| action | 用途 | 调用与写入 |
| --- | --- | --- |
| `recall-preflight` | 完整 Claim 清单、已有 grouping、当前资格及索引的严格只读预检 | 零模型、零 SQL 写入；保存结果文件 |
| `cvp-management` | 新建、已有连接维护、scope、新 CVR、merge、逻辑退役 | 默认只读预检；只有显式 `--execute` 才执行已绑定授权 |
| `cvp-management-control` | 内容争议、人工约束、独立审计 findings 登记及使用准入查询 | 零模型；登记需 `--execute`，使用查询禁止 `--execute` |

新入口拒绝旧 campaign、旧 run ID 及历史恢复参数。旧 action 的审核、预算和备份契约不改变。新 Claim／CVP 2.0 提交不以常规全库备份或外接 SSD 为前提；CVP 最后完整 Opus 批准后直接走共同 SQL，不另加 Gemini 终审。

## 2. 输入与权限

内层输入使用 `backend.api.canonical_repository.cvp_recall.seal` 封装：

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
  "recall_max_calls": 100
}
```

复用现有 grouping 契约，每组最多 20 条目标 Claim，完整清单无漏无重；只读成员没有这个截断。严格预检遇未合格 Claim 即拒绝；完整执行另保存逐条 admission，在原任务保留阻塞组及理由，让独立合格组继续。当前 `opus_and_program_readback` 资格原样披露，不称独立审计或 Gemini 通过。

外层 `cvp_management.Request` 是普通 JSON，不再追加 `artifact_sha256`。字段为：`schema_version=wang_cvp_management_v2`、`action_id`、上述 `input`、`authorization={path,file_sha256}`、`budget={max_calls,max_revisions,max_request_bytes,max_output_tokens}`、`mode=create|maintain`、`maintenance_viewpoint_ids`、`permissions`、`creation_slots`。具体类型可由 `Request.model_json_schema()` 读取。

授权文件必须精确等于 `cvp_management_runner.validate_authorization` 构造的 `wang_cvp_management_authorization_v1`：负责人身份、同一 action、内层输入 SHA、模式、维护 ID、逐对象字段权限、创建槽位和预算全部绑定。设计文档不充作执行授权。创建槽位只允许 CVP、CVR、连接和观点关系，程序生成真实 ID。

已有对象变更需要 `maintain` 和精确字段权限。旧 CVR 不覆盖，改 scope／表述建立新 CVR 并移动 head。成员及关系的版本重绑是程序派生的实际写入，须另有对应 pin 字段权限并进入完整 Opus 复核；模型不能抄写 SHA、provenance 或机械字段。Claim、Evidence、教授原文及人工保护不能借此改写。

修订可用 `discard_refs` 撤回上一候选中尚未提交的对象改动或摘要：新槽位从候选移除，已有对象恢复输入原像，不要求模型抄回旧内容。撤回后仍须保留成分与旧异议，修正全部依赖并接受完整复核；这不是删除已提交对象的入口。

```bash
backend/.venv/bin/python -m backend.pipeline.cvp_sql_flow \
  --action cvp-management --manifest /absolute/cvp-request.json \
  --output-root /absolute/stable-action-root --dry-run

backend/.venv/bin/python -m backend.pipeline.cvp_sql_flow \
  --action cvp-management --manifest /absolute/cvp-request.json \
  --output-root /absolute/stable-action-root --execute
```

## 3. 正常执行与容量

索引卡覆盖完整当前 active head cut，含 statement、scope、signature 与少于 150 个 Unicode 码点的已复核 summary。已有索引由直接原始回答证明认证；缺索引时在本任务总预算内初始化。只更新新增／改变 head，退休卡片移出。新 CVP／新 head 的摘要由正常候选及同轮 Opus 一起批准，后续索引复用该证明，不另调摘要模型。

Recall 只返回本组各 Claim 对应的 CVP ID；全部卡片分片，每片保留完整 Claim 组，显式空结果，无 top-K。已有 active 连接并入候选。各工作单元顺序提案、复核、提交；后一个单元开始前对此前新卡批量增量 recall，未变卡片的合法结果复用。

共同材料中，目标 Claim 带自身 Evidence、fragment、citation 及相关 H2 小节下完整段落；候选初始带完整 CVR、全部成员表述和连接角色。维护加载全成员自身依据、相关关系、原身份判定理由与保护。关系另一端明确为只读上下文，不递归取得它的全部成员维护权。未标小标题的部分以稳定段落定位。小标题始终是 metadata，不改 Claim／CVP 身份。

成员依据可按引用请求；原文按“相邻范围→完整正文”各扩展一次，拒绝跳级、重复或其他来源替代。GPT／Opus 消费同一份已绑定材料。所有实际编码请求在调用前测量；完整组过大时只按完整 Claim 及共享待改对象依赖簇划分工作单元。全部候选保留，不可分簇超限则 `resource_hold`；原 Claim 分母、预算和召回证明不重置。

程序从明确的 Claim 字段及原字符串 Unicode 位置提取成分。GPT 仅输出短引用、位置、实际业务 delta、处置与理由。首次 Opus 前冻结成分计划；拆合保留全部旧到新映射。每轮 Opus 必须精确覆盖成分、实际写入／必要成员和旧 findings，不能以总体 pass 掩盖未决。重复后像、振荡或无进展进入根因检查；调用及修订均受原总预算约束。

## 4. 提交、恢复与结案

调用事实、子组、材料版本、原分母及总预算保存在现有 `pipeline_runs`，沿用既有 call intent／raw cache。共同提交入口为 `cvp_sql_commit`；SQL 独立重编译批准后像，核实际角色回答、权限、完整写集、当前资格、成员和关系集合及 pins，保留 RETURNING、history 和原事务反例防线。

写后程序回读检查实际当前成员、关系、必要反向依赖及闭环，区分下游产物失效与 CVP 本身未结案。子组成功不代表整个父任务完成。`completed` 表示该操作已获末轮 Opus 批准、提交并完成程序回读，`independent_audit` 初始仍为 `not_performed`。

恢复使用完全相同的外层请求、action ID 和 root。已返回回答认证原 cache 后复用；commit 回执丢失先验已应用事实，不重复 apply。写后结案失败从已提交事实继续。unknown 缺少合法回答时保留 hold，不能换目录、输入版本或 action 自动取得新调用。已完成操作回放直接返回原事实，不因它提交后的旧索引快照失效而重复执行。

上游 Claim／资格／必要来源变动使旧候选失效。当前新资格建立后，回到同一任务、同一累计预算；实质文字变化生成待重接材料，保留旧候选、成分和 findings，GPT 必须提供新位置与完整旧映射，再走正常 Opus。程序不直接删除旧成分或把旧 pass 贴到新版本。

## 5. 人工约束、独立审计与正式使用

控制请求为 `schema_version=wang_cvp_management_v2_control`、`parent_run_id` 及明确 `operation`：

| operation | 输入与效果 |
| --- | --- |
| `content_dispute` | 原 step、两份真实回答中的判断位置、各自原文位置、全部已排除的材料／版本／schema／工程原因、争议标准和选项；登记必要内容人工包 |
| `human_constraint` | 原争议及文件绑定的人类决定；解释、hold、精确保留或明确 supersession 回同一任务，保留原意见，不批准未知后像、不重置预算 |
| `independent_audit` | 已实际执行的独立报告、直接原件 reader、精确 cohort／sample／当前 pins 和负责人绑定；登记当前 findings，不调模型、不改原提交事实 |
| `usage_admission` | 精确目标及 `existing_use`、`expanded_production` 或 `formal_downstream`；依据实际 head／成员／关系依赖查询当前质量限制，扩大或正式使用另需独立范围准入证明 |

原始审计异议保留。后续正常维护明确处理旧 findings、获完整批准并实际结案后，记录版本绑定的解决依据，不伪造新独立审计 pass。相关 link 的异议会影响其实际 CVP 使用，独立可成立的其他 CVP 可另行合格。操作完成、独立审计及正式使用准入分别报告。

## 6. 验证边界

本次实际测试和保存数据复验见[完整编码与隔离验证记录](../90-ops/cvp2_coding_verification_20261017.md)。

`backend/tests/test_cvp_management*.py`、召回、材料和字段定位测试覆盖纯规则、真实临时 PostgreSQL、假 provider、累计预算、多轮、拆分、维护和恢复；假回答不充作真实内容批准。历史保存材料的只读预检继续提供零模型／零正式写入验证。

正式 recall 遗漏／误召回、分片位置与大小敏感性、summary 效果及真实全流程成本仍须按固定 cohort 有界验收。代码与隔离验证完成不表示已运行真实内容生产、已通过独立质量评估或已部署；#436 和 #357 在各自完整验收前保持开放。
