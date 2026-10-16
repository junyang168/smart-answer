Claim management 联合 Update：输入中的完整 before、sources、原始目标、字段权限与所有未决异议都是审阅边界。所有目标身份独立；required_maintenance_keys 必须满足。检查所有共享 owner 和关系端点。其他 Claim、hold、原文只读。不得裁定神学正确、增删教授教导、补未回答问题或改变归属／必要限定。正确摘要可保留，不要求原文每字连用；异议不是修改命令。

提议者输出 changes、findings、finding_resolutions。changes 只列实际修改的已有记录，fields_json 只列实际改动字段；未列字段和未列记录由程序从冻结输入原样保留。不要重列 retain 清单、完整后像、记录内部身份、provenance、SHA、operation 或 replacements。不允许创建、删除、退休或恢复。每条修改附具体理由和完整来源中的逐字引文。所有传入 unresolved 必须恰好一次 finding_resolutions：实际修正或有据否决可 resolved；认可但未解决用 persistent；不得 A→A、A→B→A 或改正确 Claim 迁就错误关系。

独立审阅者须读完整 denominator、before、实际候选及完整原文，核实原目标和 required_questions 的全部要求，包括无修改记录、共享 owner 影响、支持／反对材料、归属、限制条件及本轮实际写入。输出 complete_review=true 表示明确完成整个范围与全部要求的审阅，并通过所有没有新异议或 persistent/reopened 旧异议的记录；不能抽样或仅审修改。输出 findings 与对所有 unresolved 恰好一次的 prior_findings，不重列全部 verdict、候选 SHA 或检查项名称。程序把这项明确声明及异议展开为完整审阅记录；不会替你判断异议对错。

每项 finding 和未解决的 finding_resolution/prior_finding 给出准确 target_key、component、category、原文 citations、reason、acceptance_condition 及 affected_target_keys、affected_write_keys、impact_reason。目标自身问题必须含该目标；本轮写入自身问题必须含该写项。共享修改若伤害只读 owner，也须阻断实际写项。无关只读异议可两列表为空，说明为何不影响目标、必做维护及写入，但仍保留问题。新旧同一问题不要重复登记。原文错字／归属／实质矛盾无法据完整来源裁定时标 source_uncertain，给具体 human_question，不能自行修原文。缺包、字段或运行错误归工程，不能包装为人工神学裁决。

历史拒绝、候选疑点及人工核实只按各自证据效力读取。不得在没有新原文依据或适用人工核实的情况下把原文疑点称为 resolved；也不得把提议者疑点当已证实。全部异议先由 GPT 判定，再由 Opus 独立复核；预算仍属原 action，不重置、不循环问到通过。不宣称 action 完成、人工批准或独立 Gemini 审计通过。程序核实完整候选、权限、来源、精确事务和当前回读后才能完成。

来源中的 human_source_decisions 是流程已登记的人工确认或文字校订。只在 applicability=current 时按其具体原文位置、问题和明确回答使用，不为已回答的同一事实重复要求人工；它不批准 Claim，也不免除对论据、归属、关系和限定的审阅。needs_reconfirmation 只作历史背景，不能当作当前事实；若当前目标仍依赖该事实且完整原文不能解决，再提出具体的新人工问题。人工核实口误可以保持教授逐字原话不变，以该证据解决其明确回答的疑点并作 resolved 判定，解释口误与其教导的区别，不要求先篡改原话。未获回答的其他问题不能随之通过。多条当前记录若实质冲突且未明确替代，须具体澄清，不能自行挑选。

claim_scope.contract=claim_direct_neighbours_v1 时，content_claim_keys 的本体、完整自身来源和证据必须完整审阅。neighbour_claim_keys 只是目标直接关系的一跳端点上下文，可能来自其他讲道：只判断本次关系，不审核其自身全部教导，不因其未提供的其他Evidence或关系报缺包，也不沿邻居继续扩张。邻居不是目标的原文或论据，不因本次上下文判断获得内容资格。maintenance_keys 明确的共享修改仍须检查全部相关owner及其必要证据，但不能继续遍历owner的其他Claim关系。
