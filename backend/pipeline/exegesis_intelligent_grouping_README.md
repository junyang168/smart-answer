# #411 释经 Intelligent Grouping

入口：`backend.pipeline.exegesis_intelligent_grouping_job`。只生成文件，使用订阅 CLI，
不调用 CVP，不写 Claim／Registry。单段 >20 的实际调用复用
`viewpoint_passage_grouping_sample_runner.split_reviewed_unit` 和现有论证边界校验。

## 输入契约

正式 v8 ledger 和冻结 packet 的 SHA 固定绑定本次 #409 交付。
`--ownership` 是 SHA-bound `wang_exegesis_reviewed_ownership_v1`，不能传 preview、
GPT-only candidates 或未完成的定位 checkpoint。必须包含：

- `role_ledger_sha256`、`role_packet_sha256`；
- `decisions`：3843 条释经 exact-once（不得含 other／延期170）；
- 每条 Claim 的 `claim_id`、`claim_revision`、`claim_content_sha256`、`source_id`、
  `source_revision`、`source_content_sha256`、`secondary`（保留原次要经文和关系）；
- 已审核项：`status: reviewed`、`primary`、`reason`、`evidence`、
  `approval_basis: dual_model_consensus | human_exception_review`、`review_artifact_sha256`；
- 未解决项：`status: unresolved`、空 `primary`、具体 `missing`；不自动延期；
- `review_artifacts`：`path` 和 `artifact_sha256`，实际 artifact 的 `decisions`
  必须给出对应 Claim、primary 和 approval_basis，不能只贴一个 SHA 充当审核；
- `sources`：每份 source 的 `source_id`、`source_content_sha256`、原件 `path`、
  当前物理 `file_sha256`；需要 SVG 等关联原件时用 `linked_files`（path/file_sha256）。

定位产物需要经真实独立审核后才可编译成此输入；此 job 不会把候选自动转正。
每次启动／续跑以只读数据库读取复核所有3843条当前 Claim／来源／证据／fragment
图与冻结 packet，并重新打开物理原件验证文件 SHA；任何漂移停止。

## 第0、1、2层

- 第0层：圣经书卷是输入边界。使用经审核的唯一 primary 归卷；secondary
  跨卷支持保留，不复制 Claim。整卷输入是正式方案，不要求先按来源拆任务。
- 第1层：模型完整阅读该书卷的成员、证据及来源，识别释经段落并确认成员。
  跨来源一起判断；章号只用于排序，允许太16章末到17章初等跨章论证。
  此层不受20条限制，不能为容量机械切段。独立语义复核通过后冻结单元。
- 第2层：完整段落 <=20 直接一组，不调用分组模型；>20 将该段全部成员送入
  现有 split runner，沿论证边界拆组，每组<=20。各组再独立语义复核。
  太16:19专用上下文仅用于审核范围恰为 `Matt.16.19` 的单元，不固定42条。

## 无损 packet 压缩

实际调用使用紧凑 JSON 和 `wang_exegesis_interned_packet_v1`：长的重复字符串只
存一次，原字段中的 `{"$text": n}` 引用 `texts[n]`，`data` 保存完整原始结构。
提议 transport 逐字段还原比较；独立 reviewer 有自己的 stdlib 编码器，先重新
打开物理原件，再编码。完整 Claim、证据、原文、primary/secondary、revision/SHA
不摘要、不选窗口、不删尾；原始完整 payload 与 wire payload SHA/bytes 同时记录。

压缩并不保证请求一定小于500000字节。必须按压缩后的 prompt+schema+payload+
CLI参数重新测量；仍超限则保存失败并停止，不宣称整书卷不可行，也不自行改为
source-local 流程。准备包的候选经文书卷关联容量仅用于估算，不是经审核归卷。
整卷第一层和第二层 runner 已有执行代码；尚未产出正式 reviewed ownership 输入
是内容审核前提，不能说成“第一层代码不存在”，不能自动转正候选或108未解决项。

`review-exegesis-grouping.py` 是独立 stdlib 路径，不 import backend，不使用
流水线读取器，重新打开原件，并用另一厂商的订阅模型检查全部单元／组的语义。
程序覆盖和语义审核分开记录；后者需要逐组理由及原件逐字证据。

## 运行和续跑

```bash
backend/.venv/bin/python -m backend.pipeline.exegesis_intelligent_grouping_job \
  --role-ledger /absolute/formal-v8.json \
  --role-packet /absolute/role-packet.json \
  --ownership /absolute/reviewed-ownership.json \
  --output-root /absolute/411-independent-output \
  --provider gpt --model gpt-6-sol \
  --reviewer-provider claude --reviewer-model claude-opus-5-5 \
  --max-request-bytes 500000
```

模型和字节上限显式配置；可通过 CODEX_EXECUTABLE／CLAUDE_EXECUTABLE 指定 CLI。
实际请求容量包括 prompt、完整 payload、schema 和命令参数。超限保存请求和原因，
不截断、不切容量窗口、不漏尾。书卷规划本身也执行此限制；若整书卷请求超限，
须另行解决请求容量／审核范围，不将超限当作分组成功。

同一 output root 有排他锁。相同命令续跑只复用 fingerprint 一致的已验证回答，
模型／prompt／schema／代码／输入变更必须换独立 root。未完成或失败的调用保留，
不会自动重启；失败需先审阅 raw／failure，制定有限恢复，再在新 root 执行。
不存在无限重试、API fallback 或语义覆盖率自动修补。

原始 stdout／stderr 和 GPT last-message 在解析前保存；即使无效JSON、超限、
超时、非零退出都保留已获得的原始输出及失败记录。有效 checkpoint 为各目录
`validated.json`；独立审核拒绝时停止，不能跳段。

交付 `config.json`、`preflight.json`、`unresolved.json`、`manifest.json`、
`validation-report.json`、逐段请求／原始回答／审核记录。manifest 原样保留
Claim完整版本／证据包和 ownership.secondary，报告实际3843清账、已分组及未解决数。
存在未解决项时明确标为 partial，不冒充全库完成。模型代码 ready 不等于产物审核通过，
不得据此启动 #357 CVP processing。
