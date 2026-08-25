# 经文角色与释经覆盖格式 v2：15 篇校准报告

## 一、目的

现有 205 篇第一遍普查已经忠实保留教授实际引用的经文字符串，但字符串本身不能
回答“教授重点解释了哪里”。本试验在不修改 v1 文件的前提下，为 15 篇已发布样本
生成结构化经文伴随记录，验证四项能力：

1. 多卷书、多范围引用可以拆开；
2. 经文可以机械规范化为 OSIS；
3. 可以区分主要释经、平行经文、原文支持、历史背景、神学支持、反例和应用依据；
4. 模型不能新增或遗漏任何经文 occurrence。

## 二、结果

- 样本：15 篇已发布讲道；
- 经文 occurrence：484；
- 成功规范化：472；
- 未解析泛称：12；
- 机械回链验证失败：0；
- 模型漏项、新增项或重复 `ref_key`：0；
- 自动角色均保持 `candidate`，尚未人工批准。

角色分布：

| 角色 | 数量 |
|---|---:|
| theological_support | 163 |
| primary_passage | 126 |
| parallel_passage | 54 |
| lexical_support | 46 |
| application_basis | 31 |
| historical_background | 29 |
| unclassified | 19 |
| counterexample | 16 |

12 个未解析值主要是“约翰福音”“诗篇”“启示录”“保罗书信”“亚伯拉罕之约”
一类没有章节目或并非具体经文的泛称。系统保留原文与角色候选，但不让它们进入
按经卷、章、节排序的覆盖地图。

## 三、关键校准观察

`011WSR01` 的 38 次引用全部通过机械验证。系统能区分：

- 马太福音 26 章连续段落：主要释经对象；
- 约翰福音 12、13 章：平行叙事；
- 马太福音 26:21 的句首 `Amen`：原文／措辞支持；
- 但以理书和旧约背景：按具体 occurrence 判断为历史或神学支持，而不是仅凭讲道标题。

多卷书输入也已验证。例如一个 v1 字符串
`太 17:1–8；路 9:28–36` 会展开为两个稳定记录，各有自己的 OSIS、角色和理由。

## 四、发现的限制

1. `primary_passage` 是 occurrence 级角色，不等同于“整篇讲道唯一主经文”。一篇
   连续释经讲道可以有多个主要段落；专题讲道也可能围绕数处经文展开。
2. 同一经文在 cluster 与 claim 中会重复出现，这是来源层级不同，不是数据重复。
   建覆盖地图时应按 `transcript_id + OSIS` 聚合，同时保留所有 owner 回链。
3. 自动分类只是候选。特别是主要经文与神学支持的边界，需要在形成公开覆盖地图前
   做抽样审核，而不是把 126 条全部逐条审完才允许任何局部成果发布。
4. 只有书名而没有章节的泛称不能排序；后续可回到逐字锚点人工补足，但不得自动猜测。

## 五、结论与扩展门槛

结构本身可以扩展到 205 篇：它保留 v1、能够处理多卷书、能够机械防止增删引用，
也明确区分 AI 候选与人工批准。扩大前建议先人工审核以下小样，而非审核全部 484 条：

- 每种角色随机 5 条；
- 所有 `low` 置信度的 `primary_passage`；
- 12 个未解析泛称；
- 三类高难样本：连续释经、跨经文专题、问答／离题严重的讲道。

审核达到可接受准确率后，再以同一程序为 205 篇生成 v2；公开覆盖地图只显示已批准
或达到既定发布门槛的结果。

## 六、产物

- 试验数据：`$DATA_BASE_DIR/wang-knowledge-platform/staging/corpus-survey/scripture-v2-pilot/`
- 数据格式与验证：`backend/pipeline/corpus_scripture_enrichment.py`
- 分类 runner：`backend/pipeline/corpus_scripture_enrichment_runner.py`
- 分类提示词：`backend/pipeline/prompts/corpus_scripture_role_enrichment.md`
- 自动测试：`backend/tests/test_corpus_scripture_enrichment.py`
