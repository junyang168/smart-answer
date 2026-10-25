# g038 完成与 g039 工作树短暂冲突（2026-10-05）

> **读者**：Developer、Solution architect
> **类型**：记录
> **状态**：停止原因已核验，续接仍须真实备份与 dry-run
> **与代码对齐**：2026-10-05，`5bfc912`
> **权威范围**：记录本次机械停止，不放宽干净工作树门槛或语义审核

## 实际停止与根因

campaign `campaign-recovery-2fc29744d5b0204c7b1e472f1f0e0862a914c4ab2e1ee0c4742935aeb699914f-recovery-cfb58a2570d6`
已真实完成 g038 的2Claims，累计38/156组、210/973Claims。
completion 三项物理 SHA 回读匹配；完整独立审计2Claims／3CVPs零异议。
audit SHA `7177973776845a4cc9c07b9462be3c8679b749b2185d7ffdac99a6769acc5e27`。
g035、g037临时非独立复审标记仍保留，不受本组独立通过影响。

g039 `p023:porneia_exception_scope` 历史资格 bridge 已通过，但准备阶段
在2026-10-05T16:33:02Z由 `git status --porcelain` 拒绝非干净工作树。
上次交接文档提交 `5bfc912` 的提交时间为16:33:10Z，与该检查重叠。
这是记录文件提交时序冲突，不是模型拒绝、容量或来源证明失败。
g039尚无模型请求或写入；producer／supervisor均退出，watch-result 为
`dirty_worktree_needs_attention`。共享 state 的旧 g035 失败只属历史。

本次复查工作树已干净、维护无活动、recovery.lock 非阻塞可获。
没有 stash、删除文件、重置预算或修改源码；干净工作树检查保留。

## 受控续接边界

先提交本记录，再于独立 runtime root
`staging/cvp-production-357/g039-clean-worktree-continuation-20261005-v1`
保存原因和一次恢复 receipt。采用既有 `resume_after_repair` 完整验证已完成
38组种子、原成员顺序、物理来源绑定、v5 policy／共享规则和 Claim 修订证明。
fresh backup／pg_restore／g039零模型零写 dry-run／真实进程与 watcher通过
才算已续跑；不重放 g038 模型、ChangeSet或审计，不修改 Claim／Evidence／source。
成功的新唯一 campaign root 只从本次 continuation receipt 读取。

生产恢复后记录写入 runtime 独立产物与 #357，不在活动生产时修改工作树文件，
避免再次以交接记录制造临时 dirty 状态。旧 failure／watch／预算完整保留。
