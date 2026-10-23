联合 Update 专用契约 wang_claim_joint_update_v1：这是一个 action 的一个单元，全部目标、required_maintenance_keys 和完整 before/after 必须共同处理，不能局部通过、另起任务或改变分组。仅既有对象 retain/update；没有创建、退役、恢复、合并或拆分权限。目标 Claim 身份独立；另一条保留原文也须完整审阅。原字段权限只是备用许可，不表示必须修改。original_goal.required_maintenance_keys 的每项每轮都必须处置并审阅，retain 可接受，未决仍阻断，不能归为无关 context。

每个 finding 及未解决的 finding_resolution/prior_finding 都必须显式给出 affected_target_keys、affected_write_keys、impact_reason。affected_target_keys 只列实际受影响的本单元目标。affected_write_keys 只列候选 disposition 不为 retain 的实际写项，不能列全部备用许可。共享 E 修改若使只读 C 失去支持，即使没有目标受影响，也必须 affected_write_keys=["evidence_steps/E"] 并拒绝本次写入；必须审阅共享修改对全部旧、新 owners 和关系端点的影响，不能仅把 C 降为不可用就放行 E。对 C 的独立章号疑点，若不影响目标、实际写入或必做维护，可两列表为空，说明无关原因；仍保留并拒绝有问题的 C，不谎称解决。

GPT 需逐项判断所有传入异议，可以据完整原文修正或否决。Opus 独立核对修正或有据否决；未解决问题的目标/写入影响若与 GPT 判断不同，不能宣布通过。不要推测程序会从自然语言自行补齐影响范围。实际写项自身的问题必须包含该写项，目标自身的问题必须包含该目标。一次 pass 只代表当前完整候选通过，不等于 action 已完成；最终由共同事务和精确当前回读核实。
