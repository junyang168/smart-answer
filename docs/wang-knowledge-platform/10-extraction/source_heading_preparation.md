# 原文小标题准备

> **读者**：Developer、流程负责人。
> **类型**：流程。
> **状态**：当前。
> **与代码对齐**：2026-10-17，核对本次 #436 工作树中的 `source_heading_batch_runner`、`source_heading_inventory`、`source_editorial_transition` 与字幕保存测试。
> **权威范围**：原文编辑小标题的盘点、保存、恢复及既有资格的读取；不批准 Claim 或 CVP 内容。

## 1. 小标题是 metadata

小标题是编辑提供的结构，不是教授原话或 Evidence。新增小标题不得修改正文、原有记录、段落编号、时间信息或外层 metadata，也不产生 Claim／CVP 修订、重审、批准或新的资格。正文与视觉来源的身份保持不变。

物理文件 SHA 仍记录实际保存版本。对于历史上绑定整个文件的资格证明，读取时验证保存前后的完整 JSON：只允许插入合法的小标题行，所有旧记录及外层字段必须原样保留。该机械证明使旧资格继续依据相同正文生效，不把旧审核升级为新内容批准。没有证明的文件漂移、真实正文变化、holds 和来源疑点仍按原流程处理。

这些记录保存在 `$DATA_BASE_DIR/wang-knowledge-platform/staging/source-editorial-transitions/v1/`。每份证明绑定原路径、保存前后文件 SHA、两份原始字节和正文身份。既有 SQL 来源、Claim、CVP、审核和历史请求保持原样。

## 2. 先盘点，再保存

`source_heading_inventory` 在一个只读 SQL 快照中取得当前 Claim 和相关 Evidence／来源。字面查询仅用于优先排程，不证明专题语义穷尽或独立来源去重。退役记录不进入当前分母。

先处理“人子”相关缺标题来源，再处理其余来源。原文已有小标题但开头没有标题时，只补无标题的开头部分；已具备结构的部分不重新生成。笔记会被盘点，但不进入讲道 JSON 的保存器。正文为空、登记版本漂移或路径不再是 published-first 时明确报告。

默认只生成盘点和冻结清单，零模型调用、零来源写入。`--apply` 才调用订阅模型，并要求有编辑权限的身份：

```bash
backend/.venv/bin/python -m backend.pipeline.source_heading_batch_runner \
  --query 人子 --priority-only \
  --actor-id <editor-id> \
  --model gpt-6.1-sol --effort high \
  --output-dir <staging-directory>
```

确认清单后，在同一命令增加 `--apply`；恢复使用相同目录及参数。`--limit N` 在冻结前限制来源数量。去掉 `--priority-only` 可把其他当前来源排在优先来源后面；不得用新目录重发状态未知的原调用。

本地 CLI 路径可通过 `--codex-executable <path>` 显式指定。模型调用预留之前先验证 CLI 和登录；本地配置失败不算推理调用，也不自动换模型。已保存的合格标题响应可离线重放。

## 3. 保存和恢复

复用讲道编辑器的生成 prompt、ACL、原子 CAS 保存器及 application audit，不调用抽取或 CVP 模型。正文由完整来源生成标题；后续 CVP 的原文取材仍须另行实现小节范围与提案／审核共同材料契约。

生成前记录调用身份与来源、prompt、model、effort、范围。合格标题响应先落盘，再尝试原文保存；写入失败可复用同一响应。模型调用失败或结果未知时停止自动重发，保留未完成状态供核查，不把它标为成功。保存返回丢失时依据已冻结的精确后像恢复。

保存后再次检查正文可读、首段已有标题及物理版本可验证。进度文件只是缓存；来源证明和保存回执才是恢复依据。标题取材改变后，后续 packet／缓存必须绑定当前编辑结构；该变化不使相同正文的 Claim／CVP 批准失效。
