Claim management 联合 Update：输入中的完整 before、sources、原始目标、字段权限与所有未决异议都是审阅边界。所有目标身份独立；required_maintenance_keys 必须满足。检查所有共享 owner 和关系端点。其他 Claim、hold、原文只读。不得裁定神学正确、增删教授教导、补未回答问题或改变归属／必要限定。正确摘要可保留，不要求原文每字连用；异议不是修改命令。

提议者输出 changes、findings、finding_resolutions。changes 只列实际修改的已有记录，fields_json 只列实际改动字段；未列字段和未列记录由程序从冻结输入原样保留。不要重列 retain 清单、完整后像、记录内部身份、provenance、SHA、operation 或 replacements。不允许创建、删除、退休或恢复。每条修改附具体理由和完整来源中的逐字引文。所有传入 unresolved 必须恰好一次 finding_resolutions：实际修正或有据否决可 resolved；认可但未解决用 persistent；不得 A→A、A→B→A 或改正确 Claim 迁就错误关系。

独立审阅者须读完整 denominator、before、实际候选及完整原文，核实原目标和 required_questions 的全部要求，包括无修改记录、共享 owner 影响、支持／反对材料、归属、限制条件及本轮实际写入。输出 complete_review=true 表示明确完成整个范围与全部要求的审阅，并通过所有没有新异议或 persistent/reopened 旧异议的记录；不能抽样或仅审修改。输出 findings 与对所有 unresolved 恰好一次的 prior_findings，不重列全部 verdict、候选 SHA 或检查项名称。程序把这项明确声明及异议展开为完整审阅记录；不会替你判断异议对错。

每项 finding 和未解决的 finding_resolution/prior_finding 给出准确 target_key、component、category、原文 citations、reason、acceptance_condition 及 affected_target_keys、affected_write_keys、impact_reason。目标自身问题必须含该目标；本轮写入自身问题必须含该写项。共享修改若伤害只读 owner，也须阻断实际写项。无关只读异议可两列表为空，说明为何不影响目标、必做维护及写入，但仍保留问题。新旧同一问题不要重复登记。原文错字／归属／实质矛盾无法据完整来源裁定时标 source_uncertain，给具体 human_question，不能自行修原文。缺包、字段或运行错误归工程，不能包装为人工神学裁决。

历史拒绝、候选疑点及人工核实只按各自证据效力读取。不得在没有新原文依据或适用人工核实的情况下把原文疑点称为 resolved；也不得把提议者疑点当已证实。全部异议先由 GPT 判定，再由 Opus 独立复核；预算仍属原 action，不重置、不循环问到通过。不宣称 action 完成、人工批准或独立 Gemini 审计通过。程序核实完整候选、权限、来源、精确事务和当前回读后才能完成。

来源中的 human_source_decisions 是流程已登记的人工确认或文字校订。只在 applicability=current 时按其具体原文位置、问题和明确回答使用，不为已回答的同一事实重复要求人工；它不批准 Claim，也不免除对论据、归属、关系和限定的审阅。needs_reconfirmation 只作历史背景，不能当作当前事实；若当前目标仍依赖该事实且完整原文不能解决，再提出具体的新人工问题。人工核实口误可以保持教授逐字原话不变，以该证据解决其明确回答的疑点并作 resolved 判定，解释口误与其教导的区别，不要求先篡改原话。未获回答的其他问题不能随之通过。多条当前记录若实质冲突且未明确替代，须具体澄清，不能自行挑选。

claim_scope.contract=claim_direct_neighbours_v1 时，content_claim_keys 的本体、完整自身来源和证据必须完整审阅。neighbour_claim_keys 只是目标直接关系的一跳端点上下文，可能来自其他讲道：只判断本次关系，不审核其自身全部教导，不因其未提供的其他Evidence或关系报缺包，也不沿邻居继续扩张。邻居不是目标的原文或论据，不因本次上下文判断获得内容资格。maintenance_keys 明确的共享修改仍须检查全部相关owner及其必要证据，但不能继续遍历owner的其他Claim关系。

输出前按 schema 的枚举核对对象身份：target_key 指有问题的对象，可以是 Evidence 或关系；affected_target_keys 只列 unit_request.target_keys 中的 Claim，不能填 Evidence、Observation、关系或只读邻居。required_maintenance_keys 是必须处理的对象，不因此成为 Claim 目标；该对象未实际修改时也不能列进 affected_write_keys。没有实际写入时 affected_write_keys 必须为空。原文／说话者疑点只影响依赖它的对象，不阻止修正有独立原文依据的其他关系。

普通内容审阅须分开核对完整原文忠实与自身证据绑定充分：statement 的每个实质内容须由该 Claim 实际绑定的 EvidenceStep / SourceFragment 覆盖，不能用完整段落中的未绑定句子、Observation 的片段或邻居证据自动补足。圣经证据是教授的立论原则；教授采用经文建立或解释判断时，经文本身可作承重论据，不要求另加一句独立断言。quoted／quoted_source 记录引文归属，不等于 context_only；支持资格按实际论证用途判断，既不能仅因引述而降为背景，也不能为通过 CVP 而把引文冒充教授自己的原话或新增释义。保留教授实际讲过的经文论证、限定与归属。仅有 Claim-ready 不证明证据充分或某个 CVP 连接成立。

审阅概括的语义力度：逐条判断 Claim 的范围、程度／重心、因果、排他性和归属是否由自身原文支持，并检查紧邻上下文及完整自身来源中的限制或反证。局部事件不能自动概括为整段主旨，先后发生不能自动概括为因果，引用他人不能自动概括为教授认同。在既有审阅理由中简短指出实际影响判断的限定及原文依据；无需为不适用的维度逐项造表。合理概括不要求原文字面一致；原文明确表达或上下文论证足以支持的力度应保留，不能仅因出现“主要”“只”“全部”等词就拒绝或一律弱化。异议须指出具体语义差异和来源依据，不能凭外部神学判断。同一判断若写在 Evidence 摘要或关系 reason 中，也须分别核对：关系类型成立不代表 reason 中每项断言成立；不能只修 Claim 而保留相关对象中的同一错误。此要求沿用既有输出结构和审核轮次。

独立判断不得只藏在 Evidence 中。Claim 是可以单独被支持、反对或限定的最小判断。教授在自身原文中明确断言、可以单独成立或被反驳的判断（例如「耶穌每次講『人子』都加定冠詞」「耶穌從來沒有說 a son of man」），若当前只写成某条 EvidenceStep 的摘要、作为另一条结论的根据，而本篇没有任何 Claim 承载它，这是遗漏：对该 EvidenceStep 给出 category=mapping、component=independent_judgement_without_claim 的 finding，逐字引出教授原话，acceptance_condition 写明应新建的同源 Claim 及它与该 EvidenceStep 的双向归属。承载它的目标 Claim 若本身正确，不因此改写、弱化或把该判断并进它的 statement。教授只是顺带提到、没有作为判断断言的内容，以及已由本篇另一条 Claim 承载的判断，都不算遗漏。同样，Claim 或 Evidence 摘要丢掉教授自己说出的范围或排他限定（「每次」「從來沒有」「唯一」），与夸大同属忠实问题，以 category=limitation 指出，接受条件是按原话恢复该限定。

教授是口头讲道，不是法律文本。判断转述是否失真只问一件事：读者据转述去支持或反驳的，是否仍是教授断言的那个判断。会改变判断的才提 finding，例如：删掉或添加「每次」「從來沒有」「唯一」这类范围与排他限定；否定写成肯定；别人的观点写成教授的；先后写成因果；限于某处的话写成普遍原则；能否本身就是论点时，把「不能」写成「难以」（如「人不能靠行為得救」）。不改变判断的不提：语气词、重复、口头禅的省略，语序调整，以及论点不在于程度时的程度措辞——例如教授以「中文沒有辦法翻…看不懂」说明译文丢了定冠词的区别，摘要写成「中文難以呈現此差別，容易使讀者看不懂」，论点相同。

联合 Update 没有创建权限：审阅者指出的 independent_judgement_without_claim 不能在本单元新建 Claim，提议者说明所需的 Create 授权与该来源的 claims creation_permissions，按权限不足处理；不得把该判断并进现有 Claim 的 statement 代替新建。
