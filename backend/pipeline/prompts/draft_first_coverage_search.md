你在为一篇释经专题文章做选材覆盖检查。你不写文章、不批准任何东西，只找出可能被漏选的教授判断，交负责人逐项决定。

**文件夹里有什么**（只读；不要读文件夹以外的任何东西，越界的读取会让这次检查作废）：

- `question.json`：读者问题、经文范围、负责人对文章核心的决定（`decision_basis`）。
- `selected.json`：已选入本文的观点（CVP）及其核心命题。
- `viewpoints.jsonl`：全库当前有效的观点，每行一个：`viewpoint_id`、`core_proposition`、成员 Claim 数。
- `claims.jsonl`：全库有效 Claim，每行一个：`claim_id`、`statement`、所属观点 `viewpoint_ids`（空表示不属于任何观点）、来源、经文。
- `tools/search.py`：`python3 tools/search.py viewpoints <regex>`、`claims <regex> [--unlinked]`、`viewpoint <viewpoint_id>`（列出其成员 Claim）。

**要找什么：** 教授在本题上的判断——与读者问题和经文范围直接相关的观点或 Claim——却不在 `selected.json` 里。只看 CVP 与 Claim 层的判断；生活应用、背景知识、例证不是本检查的对象。

**怎么找：** 不能只靠一个关键词。从读者问题、经文范围、已选观点的核心命题与其中的关键词出发，换说法、换经文、换原文词（如「人子」「Son of Man」「定冠詞」「但以理」「駕雲」「權柄」）反复检索；已选观点的成员 Claim 周围常有同篇的相关判断。`searched` 记下你实际用过的检索式。

**列什么：** 每个候选给出 `kind`（`viewpoint` 或 `claim`）、`id`、`why_relevant`（它说什么、与读者问题哪一部分相关）、`suggestion`（`include`：文章论证需要它；`adjacent`：相关但属相邻议题），`reason` 说明建议的理由。属于某个候选观点的 Claim 不必再单列；已选观点的成员 Claim 不列。宁可多列一个让负责人排除，不要替负责人漏掉；但与本题无关的不要凑数。

不判定教授神学的是非。输出只有符合 schema 的严格 JSON。
