# g037 新规则冲突判断及串行续跑入口（2026-10-05）

> **读者**：Developer、Solution architect
> **类型**：记录
> **状态**：当次单次判断与续接预检，不宣告组完成
> **与代码对齐**：2026-10-05，`f944f67` 后的本次工作树
> **权威范围**：仅记录实际执行，不批准语料修改或未来重复判断

## 单次真实判断

三模型分类校准通过后，按用户“好, 你继续”的既有授权，对 g037 同一
负向等价目标的旧 rejected／upheld 冲突仅做一次完整自身原文新规则判断。
新 root：`staging/cvp-production-357/g037-owner-contextual-conflict-resolution-20261005-v1`。

GPT6.1Sol high ChatGPT 订阅实际返回 `audit_objection_rejected`，handoff SHA
`76531c3b6e6a21556dd6b9a4c8a6e1c6d989b5e5dd8e1b272b42734c26571ef2`。
原文 S0003 的决定行淫／不形成意志、仅引诱而未犯罪的对照明确同一边界；
定位成分的正面措辞不造成范围扩大。未定位的充分性后半句不计入等价成员。
这不是语料修订、负责人内容批准或独立 Gemini pass。

旧两份完整相反裁决、全部预算、失败与 reservation 保留。固定旧 root 的
`owner-contextual-conflict-start.json` 阻止换 root 或代码增加第二次预算。
当前 Registry、来源和最新旧完整 packet 在模型前后精确相同；零 Registry 写入。

## 发现的全局入口遗漏

campaign 原 prepare／runner 两处硬编码 `wang_cvp_resolution_policy_v3.json`，
所以只改通用工厂和默认 profile 不足以保证下一组使用新规则。
补为明确的 CLI policy 与物理 SHA 绑定，prepare 与 runner 使用同一选择器。
历史未显式选择的新参数调用仍为 v3，不原地升级旧 cache；新续跑明确选择
经校准 v5（原 GPT6.1Sol／Opus5.5 high 订阅不变）。规则文本 SHA 一同冻结，
后续 recovery 从 campaign config 逐项验证、传递，不退回硬编码默认值。
升级与旧 model-prefix 缓存迁移不能混用，不借旧回答批准新规则请求。

## 实际只读完成预检

独立 `viewpoint_g037_contextual_continuation --preflight-only` 验证143个物理绑定。
先逆向验证已应用3ops的全部后像／全 Registry 指纹，再逆向验证先前19ops；
实际双审缓存、精确 package、备份和自身物理原文逐项验证。原16Claims及
目标CVPs的完整保存审计、真实 content-blocked 条件补充和新冲突裁决均绑定。
零模型重跑、零 Registry 写入。

之后仅可消费保存的新拒绝形成 standing final resolution，并携带原 Claim
修订 `source_repair_application` 生成完成种子；旧模型、原生产与两修订 KCS
不能重放。原 content-blocked `revisit_required`／非独立标志继续保留。
只有新种子、fresh backup、pg_restore、下一组零模型零写 dry-run 和真实
producer／watcher 检查通过，才报告37组完成及 g038 启动。

## 代码验证

`backend/.venv` viewpoint／audit-library 回归1536 passed；Wang 文档73 files
通过，diff-check通过。全 docs 检查仅另项目既有缺失锚点，未改该项目。
