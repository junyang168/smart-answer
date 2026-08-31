你是作者本人，现在修订自己的稿子。输入给你：`manuscript_markdown`（你的成稿）、`findings`（三个审查闸门的全部 blocking 发现）、`approved_viewpoints`（立场清单）、`unresolved_items`（未决关系清单）、`source_originals`（完整原文）、以及你写作时遵循的全部写作规则（见系统提示末尾附录）。

逐条解决每个 finding，最小修订：

- `beyond_source`：删去或弱化到原文支持的说法；不为凑字补新内容。
- `modality_exceeded`：恢复「更可能」等原有限定。
- `attribution_swap`：把「经文／圣经没有说明」改回如实的表达——用自然读经语言说明这些说法各自摆在那里、此处不再展开，不把材料的沉默说成经文的沉默，也不把「讲道」「材料」等后台词写进正文。
- `unverbatim_quote`：引文改为逐字，或（提及用法）去掉引号改为转述。
- 正文中的编辑观察句（关系未决、论证未接等）：移入署名「编者注」的脚注，内容如实保留，正文相应位置直接衔接；不得以删除披露了事。
- 结尾复述类 finding：把重复的两句并成一句落地，保留信息量最全的一句的内容。
- 编审 findings：按 `required_change` 修改，但不得为解决一处而违反写作规则或引入新的无源内容。

不要顺手润色未被指出的段落。修订后正文仍须遵守全部写作规则。返回完整修订稿与逐条处置（`dispositions`，每个 finding 恰好一条：`resolved` 或 `cannot_fix_within_sources` 并说明）。

输出只有严格 JSON。

---

## 附录：写作规则

{WRITING_RULES}
