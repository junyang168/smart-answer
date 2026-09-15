# Claim–Evidence 互反修复流程

> **读者**：Wang Knowledge Platform 运维人员与 Developer
> **类型**：流程
> **状态**：当前
> **与代码对齐**：未核对
> **权威范围**：历史 `Claim.evidence_step_ids` 与
> `EvidenceStep.produced_claim_ids` 互反事故的冻结、来源重放/重跑、原子修复与验收顺序。

## 1. 不可省略的前提

1. 确认所有已登记的 source writer、batch 和 supersession 都已结束。不能只看 issue
   状态；还要核对进程、run manifest 和最终 applied `ChangeSet`。
2. 把这些已登记任务的最终 `ChangeSet ID + fingerprint_sha256` 写入已封存的
   `wang_claim_evidence_reciprocity_prerequisites_v1` manifest。漏列 writer 等于没有完成停写确认。
3. 把可定位的历史 reviewed package 写入已封存的
   `wang_claim_evidence_reciprocity_authority_manifest_v1`。没有合格 package 时，显式封存
   `packages: []`；不用模型或人的猜测伪造 package authority。
4. 设定 `KNOWLEDGE_DATABASE_URL`，使用本 worktree 的 `backend/.venv`。本流程不发布、
   不部署，也不改原文、标题或 editorial structure。

## 2. 冻结与审计

```bash
export WKP364_RUN_DIR=/absolute/path/to/a/new-incident-directory
backend/.venv/bin/python -m backend.pipeline.claim_evidence_reciprocity_repair freeze \
  --prerequisites "$WKP364_RUN_DIR/prerequisites.v1.json" \
  --output "$WKP364_RUN_DIR/freeze-01.json"
backend/.venv/bin/python -m backend.pipeline.claim_evidence_reciprocity_repair audit \
  --input "$WKP364_RUN_DIR/freeze-01.json" \
  --output "$WKP364_RUN_DIR/audit-01.json"
backend/.venv/bin/python -m backend.pipeline.claim_evidence_reciprocity_repair bind-authority \
  --input "$WKP364_RUN_DIR/freeze-01.json" \
  --manifest "$WKP364_RUN_DIR/authority-manifest.v1.json" \
  --output "$WKP364_RUN_DIR/authority-bridge-01.json"
backend/.venv/bin/python -m backend.pipeline.claim_evidence_reciprocity_repair plan \
  --input "$WKP364_RUN_DIR/freeze-01.json" \
  --authority-bridge "$WKP364_RUN_DIR/authority-bridge-01.json" \
  --output "$WKP364_RUN_DIR/repair-plan-01.json"
```

`freeze` 先取 canonical apply advisory lock，再建立只读 snapshot。它同时冻结全部
Claim/Evidence 对、review ledger、ProductDependency 以及被引用的
SourceFragment/SourceDocument identity。

`plan` 退出码为 `2` 或 `apply_allowed=false` 时严禁 apply。不得手改 JSON 后重封；
验证器会从冻结记录重编 action、operation 和 queue。

## 3. 先处理 source work 与人工裁定

存在 exact replay、source rerun 或 manual queue 时，当前 repair plan 只是证据，永不能之后
被“解锁”。先编译 source execution plan：

```bash
backend/.venv/bin/python -m backend.pipeline.claim_evidence_reciprocity_source_queue_runner plan \
  --authority-bridge "$WKP364_RUN_DIR/authority-bridge-01.json" \
  --frozen-input "$WKP364_RUN_DIR/freeze-01.json" \
  --rerun-bindings "$WKP364_RUN_DIR/rerun-bindings.v1.json" \
  --output "$WKP364_RUN_DIR/source-execution-01.json"
```

- exact replay 只能重放 authority validation 绑定的原 package bytes。
- source rerun 的 `dispatch` 命令不带 `--apply`，只生成 reviewed candidate；随后由
  `preview` 和 `apply` 经专用 PostgreSQL guard 写入。
- manual queue 只能交给有权的人员对当前 revision 做裁定。Fable、Codex 或其他模型的
  review 不是这种人工权威。

source runner 会保护当前与历史已证明的 human-settled Claim/Evidence，核对全局
review ledger 和 SourceDocument generation，并禁止通用 `apply_plan` 绕过专用 guard。

每完成一个 source work 或人工 `ChangeSet`，都废弃当前 freeze/audit/plan，从第 1 节
重新确认停写并冻结。只有新 plan 的所有 queue 为空时才进入下一步。

## 4. 备份与原子 apply

备份必须在最终 freeze 之后生成，而且是可由 `pg_restore --list` 解析的 custom archive：

```bash
pg_dump "$KNOWLEDGE_DATABASE_URL" --format=custom \
  --file "$WKP364_RUN_DIR/pre-apply.dump"
backend/.venv/bin/python -m backend.pipeline.claim_evidence_reciprocity_repair apply \
  --plan "$WKP364_RUN_DIR/repair-plan-final.json" \
  --backup-dump "$WKP364_RUN_DIR/pre-apply.dump" \
  --committed-receipt "$WKP364_RUN_DIR/committed-receipt-final.json" \
  --output "$WKP364_RUN_DIR/apply-result-final.json"
```

apply 在同一事务内重验冻结分母、人工权威、review ledger、source lineage、
dependencies、操作、ObjectVersion 和最终互反图；任一不同即 rollback。备份的数据库名、
生成时间和 `wang_knowledge` schema 也必须与 freeze 绑定。

如果进程在 commit 后、result 完成前中断，保留 `committed-receipt` 并停止操作；不得用新备份
或重封后的 plan 盲目重试。

## 5. 验收与留存

apply result 必须同时证明：

- claim-only、evidence-only、dangling 和 duplicate 都是 `0`；
- Claim→Evidence 与 Evidence→Claim pair set 完全相等；
- preview/apply 的 ChangeSet、operation、revision 和 SHA 相等；
- fresh freeze/audit/plan 是 `0 operations`；
- 重试不增加 revision、ObjectVersion 或 review event。

把 prerequisites、每次 freeze/audit/authority bridge/plan、source dispatch/candidate/receipt、人工裁定引用、
backup SHA、committed receipt 和最终 result 作为同一事故包留存。本流程不部署任何东西。
