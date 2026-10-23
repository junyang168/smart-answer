# CVP 语境规则真实分类校准（2026-10-05）

> **读者**：Developer、Solution architect
> **类型**：记录
> **状态**：当次只读分类校准及冲突入口预检；不是生产通过
> **与代码对齐**：2026-10-05，基于 `6464348` 后的本次工作树
> **权威范围**：仅记录本次结果，不批准任何 Claim、连接、CVP 或组完成

## 实际结果

用户“好, 你继续”允许先做双边校准，再显式处理 g037 相反旧裁决。
GPT6.1Sol high ChatGPT 订阅、Opus5.5 high 订阅、Gemini3.8Flash API
各真实调用一次，均 21/21，误放、误拒和未决出口错误均为零。
没有重复诊断模型，没有 Registry 写入，没有重跑 g037 或已完成组。

21 例均为明确标记的虚构材料，不是教授引文。13 例来自既有离线案例，
其中部分概念已出现在规则示例；8 例为新增样例。编号改为不透露意思的
C001–C021 并打乱顺序，期望标签和原案例名称不进入模型输入。
每个回答都核对逐字来源引句和具体理由；人工读过三模型的完整理由。
有效局部论据被接受，纯相关被拒绝；可消解指代和语态同义被接受，
必要／充分条件、范围、模态、归属的真实变化被拒绝，缺指代进入未决。

这只是同一版本规则的合成分类测试，**不是所有生产角色的端到端试跑，
也不能估计真实讲道误判率**。样例的语境边界较明确；仍须以真实完整自身
原文检查 g037。方法参照 OpenAI 的
[evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)，
使用任务分类、边界案例、冻结输入和独立期望；没有把21例分数作为生产批准。

## 冻结产物

canonical runtime root 为
`$DATA_BASE_DIR/wang-knowledge-platform/staging/cvp-production-357/cvp-semantic-calibration-20261005-v1`。
最初误放在已忽略的 worktree `output/` 的副本已逐文件无差异复制到该路径；
原副本保留，不重新调用模型。原执行器物理 SHA 和各调用 reservation 保留。

- dataset SHA：`628a1ef509ad6e0220eccff3cdd48dcb62af3c33f3ed2394d42facba81822a23`。
- GPT raw artifact SHA：`4a9d946a9eaea91451a7f4676f30d4b50f354f44d8c7da78bfad7da3dd7d32bc`。
- Opus raw artifact SHA：`17377a6dfe25fc2305793628354924453707ff561b0c8b08c82f2cd5952fa448`。
- `result.json` 明确 `production_approval=false`、`g037_completion=false`、`registry_writes=0`。

## 真实旧裁决核验与后续边界

历史验证器漏了真实 Codex client 默认 `max_output_tokens=64000`，导致
配置 SHA 不同，并误选择新 prompt。现改为只构造实际订阅 client 的配置，
不执行模型；没有从 raw 猜配置或放宽 SHA。旧两份完整 packet/raw/handoff、
audit 和物理原文已验证，确认为同一目标先 rejected 后 upheld，只有 finding
文字改变。测试夹具改用真实 client profile，并保留全部漂移拒绝反例。

新增仅此 g037 矛盾的一次显式新规则判断入口，冻结两份旧完整裁决，
验证 current Registry 与旧最新完整 packet 相等、校准结果及规则物理 SHA，
新请求真实预检 54944 bytes，小于原500000门槛。预检零模型、零写。
固定旧 root 单次 reservation 不因换代码或 root 重置，普通防重复仲裁 guard
保持；新判断不是负责人批准，也不清除旧拒绝。任何 upheld／未决仍停止。

当前真实完成仍 36/156 组、192/973 Claims；g037 未 completion，g038 未启动，
定时检查保持 PAUSED。既有19 ops与3 ops都不重放，content-blocked 待复审标记保留。

## 代码检查

`backend/.venv` 的 viewpoint 与 audit-library 回归共1529 passed；
Wang 文档72 files检查通过，`git diff --check`通过。
全 docs 检查仅有 CONVENTIONS 已记录的另项目旧锚点错误，未改该项目。
