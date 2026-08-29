# 神学编辑综合文章 Operator Runbook v1

> **读者**：教会编辑、Operator、Developer
> **类型**：操作手册
> **状态**：当前
> **与代码对齐**：2026-08-28

## 一、先决定 scope

新主题只新增一个 `backend/config/editorial_scopes/*.json`。编辑填写读者问题、经文范围、当前已审核 `ViewpointStructure` revision 与 publication profile；问题框架注明为教会编辑判断，不冒充教授原话。不要为新主题改通用 prompt 或 schema。

若人类编辑已经批准本篇的论证顺序、正文部分数量或某项材料只能作为 footnote／inline note，必须把决定写入 scope 的 `editorial_constraints`，并绑定原 feedback artifact SHA。不能只把反馈文件放在 staging 目录里：模型不会自行遍历该目录，下次生成也没有义务记得聊天。Runner 会机械检查 section count、被禁止的 article function 和 embedded material placement；Composition Reviewer 还须逐项审核 approved outline 等需要判断的约束。

母本是优先来源，不是目录权威。目录由审核过的 structure 与 `TheologicalEditorialBrief` 决定。若 structure 尚未覆盖读者问题，先补权威知识记录，不要让 Composition 猜。

本流程直接从 Registry 编译 `TheologicalEvidencePacket`，不使用 `ViewpointKnowledgeProjection`。EvidencePacket 包含当前 scope 选中的 revision、source-local route、Claim、Evidence、来源片段，以及每份入选逐字稿与母本的完整原文，并以 dependency manifest 和 source-original manifest 绑定。片段用于定位，不能代替 Composition、Author 或 Reviewer 阅读完整原稿。

编译时会逐份读取 `source_path` 并验证 `source_sha256`。缺文件、SHA 不符、原稿为空或总字符数超过直接输入上限时，runner 在模型调用前停止；绝不能截短原稿后继续。当前 POC 上限为 120,000 字符，超限状态表示需要实现并验证完整覆盖的批次读取，不表示可以改用摘要或片段。

## 二、编译并审核 brief

```bash
backend/.venv/bin/python -m backend.pipeline.theological_editorial_composition_runner \
  --scope backend/config/editorial_scopes/TES-matthew-16-18-church-foundation-v1.json \
  --output-dir "$DATA_BASE_DIR/wang-knowledge-platform/staging/topic-essays/church-foundation/composition-v1"
```

Runner 依次产生 evidence packet、brief candidate、Independent Composition Review，以及必要时的一次 Composition Revision 和 Final Composition Review。终态必须是 `brief_approved` 才能写正文。`insufficient_material`、`unresolved_structure` 与 `human_editor_required` 都是正常停止状态。

## 三、生成与 grounding

```bash
backend/.venv/bin/python -m backend.pipeline.theological_topic_authoring_runner \
  --composition-dir "$RUN_ROOT/composition-v1" \
  --publication-profile backend/config/publication_profiles/PP-theological-topic-essay-v1.json \
  --quality-profile backend/config/editorial_quality_profiles/WQ-theological-topic-essay-v1.json \
  --output-dir "$RUN_ROOT/authoring-v1"
```

Author 只能按 brief 写作。每个实质段落带 provenance，runner 逐段 grounding。仅 `unsupported_assertion` 可进入受约束 Grounding Revision；transport 或 schema failure 不得当作内容修订。修后重新检查全文，终态必须为 `draft_grounded`。

段落 provenance 的 Claim IDs 负责断言覆盖；凡段落展开或收束论证，还须列出本段实际采用的 `argument_route_revision_ids`。路线必须属于当前 brief section。后台来源对照优先沿该 route 的 source-local attestation 与 step bindings 展示逐字片段，并逐步标明前提、限定和结论；只有不使用路线的简单陈述才从 Claim Evidence 回退。不得从一个 Claim 的全部 Evidence Step 猜测文章采用了哪条论证。

相同 generation fingerprint 会读取现有 envelope，不重复调用模型。输入 SHA、prompt、schema、model 或 generation 参数改变时形成新 generation；旧文件移入 `generations/`，不得手改正文恢复。

## 四、质量审核、审计与发布

```bash
backend/.venv/bin/python -m backend.pipeline.theological_topic_quality_runner \
  --authoring-dir "$RUN_ROOT/authoring-v1" \
  --output-dir "$RUN_ROOT/quality-v1"
```

初稿只做一次 Independent Editorial Review。每轮 Revision 恰好做一次 Final Delta Review；delta 在同一响应返回下一轮 finding。Delta packet 包含 changed paragraphs，并附修订后全文作为位置上下文；Reviewer 只能用全文确认改动段的相邻关系、归属、标题层级和实际结尾，不得借此重做全文初审。只看 paragraph diff 会漏掉位于插入段之后、但文字本身未变化的收束段，因此不得从 diff 顺序推断文章最后一句。评分只按每个 dimension 的 minimum 判断，总分只展示、不决定通过。任何 hard failure 直接失败。

若 finding 要改变 locked heading、section function 或未决关系，停止 Author 修订，回到 Composition：

```bash
backend/.venv/bin/python -m backend.pipeline.theological_editorial_recomposition_runner \
  --composition-dir "$RUN_ROOT/composition-v1" \
  --downstream-review "$RUN_ROOT/quality-v1/independent-editorial-review.json" \
  --output-dir "$RUN_ROOT/composition-v2"
```

然后用 `composition-v2` 建立新的 authoring 与 quality 目录。不要覆盖 v1。

质量通过后，Program Audit 确认 manuscript、grounding、review、scope、evidence、brief、profile、Viewpoint revision 与 ArgumentRoute revision 的绑定，并逐项验证已用 Claim 能走到 Evidence、SourceFragment、绑定相同 source SHA 的 SourceDocument；讲道来源还必须有有效的音频起止时间。Runner 由同一链编译 section-level presentation package，供 reader page 显示对应音频。零 error 才生成 `automated-publication-decision.v1` 并复制到 Wang repository。决定明确写 `human_approval: false`。发布不是部署，本流程不运行 `scripts/deploy.sh`。

## 五、看停止状态

每个目录的 `workflow-status.json` 是 operator 入口：

- `insufficient_material`：补 Claim／Evidence／source-local route；
- `unresolved_structure`：由编辑决定缩小问题或完善 structure；
- `composition_change_required`：把 prose finding 正式带回 Composition；
- `grounding_gate_failed`：看 grounding report，不得直接改 Markdown；
- `editorial_gate_failed`：看逐维 finding 与 hard failure；
- `program_audit_failed`：修 artifact/SHA/ledger，不让模型“润色”技术错误；
- `workflow_published`：Wang repository 已有自动发布 artifact，但生产代码没有因此部署。

## 六、复现检查

清空一个新的临时 run directory，从同一 scope 开始依次运行三条 runner 命令。要求相同的是权威输入、契约、依赖 SHA、审核门槛和合法终态，不要求模型逐字写出同一文章。第二主题必须只替换 scope，不改通用代码和 prompt。
