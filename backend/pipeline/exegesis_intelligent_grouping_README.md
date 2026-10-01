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
  不得带 `original_text`（含 linked_files 内），否则拒绝；带 `paragraphs` 时只记录
  其数量与 SHA 后丢弃，preparation 段落永不进入模型。

定位产物需要经真实独立审核后才可编译成此输入；此 job 不会把候选自动转正。
每次启动／续跑以只读数据库读取复核所有3843条当前 Claim／来源／证据／fragment
图与冻结 packet，并重新打开物理原件验证文件 SHA；任何漂移停止。

## 来源 packet 与模型投影

`exegesis_grouping_source_packet.py`（纯 stdlib）定义两层：

- **审计 payload**（`wang_exegesis_canonical_source_v2`）：冻结 Claim 全部字段
  （revision、content SHA、ownership 的 review_artifact_sha256 等）＋每份来源从物理
  原件读出的正文。逐字稿 JSON 的每个 `script` 行连同全部物理字段保留，加
  `physical_row`／`body_row`／`editorial_row` 定位；`~~…~~` 划掉内容、字幕行、标题行
  原样保留，不用会改文本的 reader。母本 Markdown 按空行切块，每块＝内容行＋其后
  的空行，文件开头的空行自成一块（`block_ordinal` 为空）；块文本逐字相接即原文件，
  程序断言该等式。冻结 fragment 的 `paragraph_key` 不改写、不冒充已修正，
  `locator_note` 说明它与物理定位的关系。审计 payload 完整写入
  `audit-payloads/<phase>/<key>.json`，续跑逐字段比对。
- **模型投影**（`wang_exegesis_model_projection_v2`）：只在已知位置删去明确列出的
  provenance 键（`omitted_provenance_fields` 按 claim／evidence_step／fragment／
  ownership／source／linked_file 列出），不是全局按键名删除，所以 `document` 元数据、
  嵌套的 `visual_locator`、`secondary` 关系对象里即使出现 `path`／`revision` 也原样保留。
  陈述、证据步骤、逐字证据、定位、primary/secondary、reviewed_unit 等上下文全部逐字保留。

**SVG／XML 视觉原件按用户决定不进入任何模型输入**（L1 提议、L2 拆组、两次独立复核）。
审计层保留 linked_files 的 `path`／`file_sha256`／`byte_length`／`char_length` 引用和冻结
fragment 的完整 `verbatim_excerpt`（即使它是整份 SVG）；模型层 linked_files 只剩
`file_name`、大小和 `svg_excluded_by_user: true`，含 SVG 标记的 `verbatim_excerpt` 由
`verbatim_excerpt_excluded`（sha256、char_length）代替，并附 `visual_evidence_policy`：
不推测、不总结被排除的视觉内容；若没有它就不能判断某边界或归属，须明确写出缺少
视觉证据的限制（复核给 needs_resolution），不得伪称完整核对。投影前后都对所有字符串
扫描 SVG/XML 标记，出现在任何其他字段即 fail closed，不会被偷偷塞入。
来源记录的 linked file 不是 SVG/XML 时同样拒绝（schema 不确定）。

`assert_projection_complete` 的证明形状是“非 SVG 语义全部保留＋全部 SVG 排除明确清账”，
不再声称全部视觉原文都交给了模型：检测 Claim 尾部缺失、关系被删、原文被改、SVG 被
放回、以及投影新增了审计里没有的字段；返回 claims／evidence_steps／fragments／
source_texts／source_text_chars／svg_excluded_linked_files／svg_excluded_fragment_excerpts／
svg_excluded_chars 计数，写入审计记录与容量报告。每份来源正文在 wire 中只出现一次。

独立 reviewer（`scripts/review-exegesis-grouping.py`）用自己的 stdlib 实现重读物理原件、
按 path/SHA 校验 linked SVG（含 byte/char 长度比对）但不把 SVG 文本放进任何 payload；
审计对象里若出现内联 `original_text` 一律拒绝（拒绝的是未核验的内联数据，不是合法
审计对象）。经审核 ownership 的 evidence 引文允许来自视觉原件，按磁盘原件核对；
提议者与复核模型的引文只按模型可见文本核对，因为它们从未收到 SVG。

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
打开物理原件，再编码。完整 Claim、证据、原文、primary/secondary 不摘要、不选窗口、
不删尾；revision/SHA 等 provenance 留在审计 payload 与 request.json，不进模型 data；
审计 payload、模型投影与 wire payload 的 SHA/bytes 同时记录。

压缩并不保证请求一定小于500000字节。必须按压缩后的 prompt+schema+payload+
CLI参数重新测量；仍超限则保存失败并停止，不宣称整书卷不可行，也不自行改为
source-local 流程。准备包的候选经文书卷关联容量仅用于估算，不是经审核归卷。
整卷第一层和第二层 runner 已有执行代码；尚未产出正式 reviewed ownership 输入
是内容审核前提，不能说成“第一层代码不存在”，不能自动转正候选或108未解决项。

## 容量测量（只读，不调用模型）

`exegesis_grouping_capacity.py` 用 runtime 同一套 serializer 测四种请求：L1 整卷提议
（规划 prompt＋unit schema）、L1 独立复核（reviewer 自己的 serializer，含 proposal）、
>20 候选单元的 L2 拆组（分组 prompt、仅 `Matt.16.19` 附回归上下文、严格分组 schema）、
L2 独立复核。每项报告 pretty／compact-uninterned／interned 三种 payload 字节、prompt／
schema／argv 字节、`prompt_carried_in`／`schema_carried_in`（GPT 的 schema 是文件参数，
Claude 的 prompt 与 schema 在 argv 内）、完整请求字节与是否超限，并附投影完整性计数。
byte fit 不等于 token fit。

```bash
backend/.venv/bin/python -m backend.pipeline.exegesis_grouping_capacity \
  --preparation /absolute/grouping-preparation.json \
  --sources /absolute/sources.json \
  --output-root /absolute/411-capacity \
  --executable "$(command -v codex)" --max-request-bytes 500000
```

正式 L1 单元与正式 reviewed ownership 尚不存在，所以报告里一切单元和 proposal 都带
`diagnostic_candidate_not_reviewed_not_model_output` 标签：书卷关联来自准备包的候选键；
>20 候选单元按经文键合并该键的全部候选成员（已审核 primary 与未审核引用键成员合并、
按 Claim 去重、每个成员保留 `candidate_membership_basis` 与 `preparation_status`），
624 等历史审核分类只是成员标签，不是分组边界；preparation 行若非 Claim exact-once
即拒绝。proposal 是明确的 placeholder，只给字节下界，不代表语义通过。不固定 42 条，
不把候选归属转正，不编造模型结果。

## 测试

```bash
backend/.venv/bin/python -m pytest backend/tests/test_exegesis_grouping_source_packet.py \
  backend/tests/test_exegesis_intelligent_grouping.py backend/tests/test_exegesis_grouping_packet.py -q
```

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
