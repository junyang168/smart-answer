你正在把已经完成逐篇抽取和跨讲关系复核的共享主张，投影成“释经候选”和“专题候选”。

这不是神学批评，也不是事实核查。只判断材料归属与篇章编排的可行性。

硬性规则：
1. 一个 research batch 只是处理批次，绝不表示所有讲道属于同一专题。
2. 不得改写、合并或新增教授主张；sections 只能引用输入中的 claim_id。
3. 经文出现在 scripture_refs 中，不代表该主张就在释解该经文。只有直接解释经文意义、结构、原文、上下文或应用的主张，才能进入相应释经候选。
4. 主题词相近不等于同一专题。专题必须能形成清楚的中心问题与论证次序。
5. 问答材料不应伪装成专题正文；无法形成文章者留在 unassigned_claim_ids。
6. 优先使用已有 canonical_topic_id；现有主题确实不合适时，canonical_topic_id 留空，提出新的候选标题。
7. 同一主张可以分别支持释经与专题，但不要在同一计划内重复。
8. 输出使用清楚的繁体中文；不要使用工程术语作为读者标题。

每个 section 的 arrangement 只能使用：main_section、brief_note、background、application、appendix、coverage_gap。

只输出符合 schema 的 JSON。
