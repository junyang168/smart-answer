# CVP 2.0 执行入口与恢复

> **讀者**：Developer。
> **類型**：說明。
> **狀態**：當前；完整代码入口已实现，真实模型质量及扩大生产准入另行验收。
> **與代碼對齊**：2026-10-18（共同材料来源事实核对到 `a17540f5`；来源摘要核对到 `974ef41f`；statement／经文范围配置核对到本次实现与回归）。
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
  "recall_profile": "statements_scripture_components_v1",
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

新任务在内层输入绑定 `recall_profile=statements_scripture_components_v1`，由程序从完整当前 active head cut 构造索引；模型只收到短引用、完整 statement 和已有 `scope.scripture_scope`，不收到 summary、完整 signature 或原文。无需先生成全库摘要。Prompt 接受同一判断、直接补充／限定／不同解释／张力，也接受对应 Claim 中一项明确判断的候选；不要求覆盖整条，不自行补推论，允许空集。索引及 prompt 配置进入请求／缓存绑定。

省略该字段的旧任务继续使用原 statement／scope／signature／已复核短summary索引与原prompt，原回答按原配置认证。不能在恢复时改变输入配置。新配置沿用已有分片、增量、退役移除、原始回答认证与预算机制，配置变化不会复用旧配置的候选判断。新 CVP／新 head 在生成阶段的摘要仍由候选及同轮 Opus 一起批准；它不进入新配置的 recall 输入。

Recall 只返回本组各 Claim 对应的 CVP ID；全部卡片分片，每片保留完整 Claim 组，显式空结果，无 top-K。已有 active 连接并入候选。各工作单元顺序提案、复核、提交；后一个单元开始前对此前新卡批量增量 recall，未变卡片的合法结果复用。

共同材料中，目标 Claim 带自身 Evidence、fragment、citation 及相关 H2 小节下完整段落；候选初始带完整 CVR、全部成员表述和连接角色。维护加载全成员自身依据、相关关系、原身份判定理由与保护。关系另一端明确为只读上下文，不递归取得它的全部成员维护权。未标小标题的部分以稳定段落定位。小标题始终是 metadata，不改 Claim／CVP 身份。

成员依据可按引用请求；原文按“相邻范围→完整正文”各扩展一次，拒绝跳级、重复或其他来源替代。GPT／Opus 消费同一份已绑定材料。所有实际编码请求在调用前测量；完整组过大时只按完整 Claim 及共享待改对象依赖簇划分工作单元。全部候选保留，不可分簇超限则 `resource_hold`；原 Claim 分母、预算和召回证明不重置。

当前 statement recall 配置不要求 propose 生成／review 审核旧的 150 字 summary，也不维护其摘要证明；独立上下文摘要见第 6 节。Recall、propose、review 使用固定输出 schema，合法引用由程序校验，不再按本批编号生成枚举／正则。持久材料不变，模型只接收去除内部映射、路径、哈希和预算后的语义投影；propose 与 review 分开输出结构，完整后像及最新异议仍保留。

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

## 6. 带来源的上下文摘要

`backend.pipeline.cvp_contextual_summary` 按负责人 2026-10-18 的要求生成最多 500 个 Unicode 字符的摘要。输入包括当前 CVP、全部 active 连接的完整 Claim 记录、其 Evidence（包含不合格与暂缓标记）、反对立场、来源片段、引文和相关原文小节，并附适用的人工来源事实。原文按现有小节解析器加载，不默认加入整篇讲道。摘要需说明背景、论据和必要限定，不能只改写 statement，也不为凑字数补充内容。

准备、模型调用及结果保存分别进行；复用已有 store 查询、来源解析、结构化 adapter 和 immutable call cache。每批在调用前检查真实请求字节数；GPT／Opus 读相同完整材料，最多两轮修订且受同一保存计划总调用预算约束。返回记录缺项、多项、超过 500 字符或复核拒绝均不得算完成。每次调用及最终保存核对当前 head、成员集合、依赖版本、来源文件和人工来源事实。

输出 `wang_cvp_contextual_summary_v1` 包含 statement、summary、材料 SHA 与原始生成／复核记录路径。它是来源绑定的编辑摘要，不改 Claim 或 CVP 命题。原有少于 150 字符的 recall 索引及其历史审核仍保留；新摘要不伪装成旧索引卡，也不进入上述 statement／经文范围 recall 输入。

完整 action 的 `input.contextual_summary_root` 可指定已完成的上下文摘要目录（绝对路径，内含 `plan.json` 与 `summaries.json`）。程序核对保存的生成／审核结果、当前 CVP、成员、依赖、原文及人工事实，将有效摘要加入 propose/review 的共同材料。模型默认不展开这些 CVP 的未请求成员表述，仍能通过成员引用请求依据；待处理 Claim、已请求成员及维护任务的必要全成员材料保留。缺失或过期摘要回退到当前成员表述，不增加模型调用。恢复及每次调用前复核摘要依赖；recall 仍不读取摘要。

## 7. 验证边界

本次实际测试和保存数据复验见[完整编码与隔离验证记录](../90-ops/cvp2_coding_verification_20261017.md)。

`backend/tests/test_cvp_management*.py`、召回、材料和字段定位测试覆盖纯规则、真实临时 PostgreSQL、假 provider、累计预算、多轮、拆分、维护和恢复；假回答不充作真实内容批准。历史保存材料的只读预检继续提供零模型／零正式写入验证。

正式 recall 遗漏／误召回、分片位置与大小敏感性、summary 效果及真实全流程成本仍须按固定 cohort 有界验收。代码与隔离验证完成不表示已运行真实内容生产、已通过独立质量评估或已部署；#436 和 #357 在各自完整验收前保持开放。
