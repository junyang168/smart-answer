本轮 claim_own_evidence_v1 区分两项不可互相替代的判断：完整自身原文是否忠实；Claim 实际绑定的自身 EvidenceStep / SourceFragment 是否足以覆盖 statement 的每个实质成分。own_evidence_bindings 只是实际绑定索引，所有文本仍在 before / candidate.records 中。逐条读实际片段，不能因为完整段落中另有一句、Observation 另有片段、邻居 Claim 有证据，便判本 Claim 绑定充分。片段短但精确覆盖可通过；不要求堆入整篇或复制无关段落。

提议者修正遗漏的具体绑定与必要 Evidence 摘要，保持同一 Claim 判断与原话；不能借片段扩张出教授未讲的解释。若 statement 只是教授引用经文，要写清“教授引述……”以及教授实际给出的上下文说明，不把经文内的应许本身归为教授独立释义；引文保留 quoted / quoted_source 的归属。support_eligibility 按教授实际论证用途审定：不能仅因为引用经文就降为 context_only；教授明确用作论据的经文可以是自身合格证据，但仅列出经文、尚无相应论证用途的引述不能为通过 CVP 而升级。教授自己的解释须有其实际断言的自身合格 Evidence；不能为了 CVP 连接提升经文引述资格。存在真实释义时也不能为省事删去。

独立 Opus 在既有 FindingsReview 中追加 binding_assessments：own_evidence_bindings 中每个 Claim 恰好一次，claim_use 说明所审 statement 是教授判断、引述记录、编辑推论或研究问题；evidence_keys 只选该 Claim 自己实际绑定的 Evidence，reason 简短解释全部实质内容如何受已绑定片段支持、引述与释义如何区分；其中实际影响判断的范围、程度／重心、因果、排他性、归属须说明依据，并核对上下文有无限制或反证。合理概括不要求逐字一致，也不机械弱化原文支持的力度。sufficient=false 必须同时给该 Claim 的具体 finding；不得只说“原段忠实”“锚点正确”或“资格已通过”。sufficient=true 是 Claim 当前表达与绑定充分，不批准任何 CVP 连接；quotation_report 不因此获得独立神学主张的资格。不要增加审核轮次。

圣经证据是教授的立论原则。经文若被教授用来建立或解释主张，本身就是承重证据，不要求教授额外复述一句独立断言才能支持。必须据原文判定实际用途；例如“这节意思就是说……”可能是在解释／采用经文，不能机械当作无论证作用的朗读。经文的 quoted 归属与它的合格支持用途是两个维度，不得混淆。不能为了省事把教授已用经文建立的教导缩成“他念过这节经文”。
