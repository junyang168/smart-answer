# 神学编辑综合文章 POC 运行报告

> **读者**：教会编辑、Product、Developer
> **类型**：运行报告
> **状态**：完成
> **运行日期**：2026-08-28

## 一、Golden case：教会的根基

- Scope：`TES-matthew-16-18-church-foundation-v1`
- Evidence：7 个 viewpoint、9 条 source-local ArgumentRoute、17 个 Claim、6 个来源；compiler finding 为零。
- 第一版 brief 在 Composition Review 后通过；Author 生成稿 grounding 发现 3 条越界，经正式 Grounding Revision 后 13 段零 finding。
- Independent Editorial Review 随后发现 brief 自身把“彼得的信仰告白”与“所领受并传下的真理”合成一个小标题，也发现一项跨 route 要素进入错误 section。Author 无权改 locked heading，因此流程正式回到 Composition。
- amended brief 经 Final Composition Review 通过，brief SHA 为 `c094d5f64e80d5b74c06459ba416b5fe5fa643f0c365c4ad1e1d79bb32729186`。
- 新 brief 重新生成全文；13 个实质段落 grounding 零 finding。一次 Independent Editorial Review 后发生两轮正式 Revision，每轮各有且仅有一次 Final Delta Review。第二次 delta 返回零 finding。
- Program Audit：0 errors；最终 manuscript SHA 为 `0d17a88daaaa0bf972cbc533d4b40dc5875e136b15450831afbe48e88917dfe4`。
- Program Audit 同时验证已用 Claim 到 Evidence、SourceFragment、SourceDocument、source SHA 与 sermon 时间锚的完整链；发布包为 6 个小节产生 19 个可播放来源片段。
- `automated-publication-decision.v1` 已生成，`approval_authority=automated_quality_gates`、`human_approval=false`，并发布到 Wang repository。没有部署生产环境。

最终文章先呈现三种正面识别，再处理排除彼得本人、正典希腊文本方法与教皇论应用；保留“更可能”的模态和三种识别关系未决。删除最后的应用节，前文仍能回答 reader question。

## 二、第二主题 smoke：阴间权柄不能胜过教会

- Scope：`TES-matthew-16-18-gates-not-prevail-v1`
- Evidence：2 个 viewpoint、2 条 route、2 个 Claim、1 个来源；compiler finding 为零。
- 未修改任何通用代码、schema 或 prompt 的主题条款。
- Author 生成 4 个实质段落，grounding 零 finding且无需 Grounding Revision。
- 一次 Independent Editorial Review、一轮 Revision、一次 Final Delta Review后通过。
- Program Audit：0 errors；最终 manuscript SHA 为 `b599e72f8e7b9d06f84315a5108cdeeee4192b1e7ee0ec089d3edddfd8bcc4c8`；自动发布到 Wang repository，未部署。
- 3 个小节各有可播放来源片段，共 3 个 player。

## 三、POC 校准所得

1. 结构忠实不能只在 Author prompt 中提醒，必须先成为审核过的 brief，再成为独立 dimension 与 hard failure。
2. 下游 review 能发现上游 brief 的问题；流程必须允许正式回到 Composition，不能强迫 Author 违反 locked contract。
3. Grounding 必须看到经过审核、且明确标为 editor instruction 的结构限定；否则会把合法编辑判断误判为教授材料越界。
4. Final Delta Review 只重评 affected dimensions，但必须允许在同一响应提出其他 configured dimension 的下一轮 finding。
5. 总分不能决定通过；两次实跑均以每维 minimum、hard failure 和 Program Audit 分别判断。

## 四、2026-08-29 golden-case recalibration（append-only）

以上保留为原始 POC 运行记录。后续教会编辑审读发现，同一主题的新 generation 即使通过旧门槛仍可退化：Brief Reviewer 会把限定与未决关系强塞进 H2；已经批准的人类 outline 决定只放在 run 目录旁边而没有进入 SHA-bound 输入；Author 在一处保留「或者」后，又在小节结论或全文结尾把两个答案揉合；只含 paragraph diff 的 Delta packet 不能证明哪一段实际位于全文末尾。

本轮没有流程外修改 reader-visible prose，而是增加可重复的通用 contract：

- scope-bound `editorial_constraints` 承载 approved outline、准确 H2 数量、禁止 introduction H2 和亚兰文 footnote placement；
- brief section 明确 governing question、section conclusion、依赖关系和 route role；heading 只需自然框定问题并与结论相容，不得被迫复述所有限定；
- brief revision 使用 finding 授权 JSON path、程序计算的 baseline/revised diff 与逐项 collateral-change disclosure；
- Author／Reviewer 在导言、阶段结论和全文结尾逐次检查来源的「或者」，分开 professor assertion 与 editorial unresolved disclosure，并要求 embedded objection 写出实际 route node 而非只报异议名称；
- Final Delta Review packet 附修订后全文，仅用于确认 diff 段落的真实位置、相邻关系、归属和实际结尾。Reviewer 不得从 diff 的 insert/delete 顺序推断最后一句。

当前 approved brief SHA 为 `1773fa3394f5d50bb0bc8b18253804de35d0e298f2f0154043202ebaae23c564`。最终 manuscript SHA 为 `ba317e75aa05364a70c6351150da88e7ba6169d2255cccd3781b602d80ac7aff`；Program Audit 为 0 errors、0 warnings，`automated-publication-decision.v1` 仍明确 `human_approval=false`。内部预览 `church-foundation-poc-v14` 已注册到 `/admin/wang/operations/articles/reviews/`；浏览器验收确认来源默认关闭，开启后可见逐字稿、Audio 与母本链接，再关闭可恢复纯文章视图。没有部署生产环境。
