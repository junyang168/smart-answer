# 逐句详细知识整理与双模型复审流程 v1

> 状态：已完成代码实现，并以 `011WSR01` 的完整逐字稿进行真实试运行。本流程建立可审核的候选知识，不进行神学批评，也不自动批准或出版内容。

## 一、这一步解决什么问题

第一遍全库普查回答“二百多篇讲道大致讲了什么、哪些材料值得优先处理”。它提供广度，却不能直接承担出版、问答或思想研究所需的精确证据。

逐句详细知识整理回答另一组问题：

- 教授正在回答什么问题？
- 哪些话是教授自己的主张，哪些是听众发言或教授准备驳斥的观点？
- 教授用了哪些经文、原文、历史背景和推理步骤？
- 一条结论由哪些证据支持，主张之间又有什么关系？
- 每项内容能否回到逐字稿中的确切原话和时间位置？

它不是把讲道直接改写成文章，而是先建立一份可由释经、专题、问答、搜索和学术思想研究共同使用的候选知识子图。

```mermaid
flowchart LR
    T["完整逐字稿"] --> E["逐句详细知识整理"]
    E --> M["程序机械验证"]
    M --> C["Claude 独立复审"]
    C --> O["OpenAI 独立裁决"]
    O --> K["AI 共识候选知识包"]
    K --> X["释经编排"]
    K --> S["专题编排"]
    K --> Q["问答与搜索"]
    K --> R["学术思想与方法研究"]
```

## 二、模型选择

| 工作 | 默认模型 | 设置 | 原因 |
|---|---|---|---|
| 逐句详细知识整理 | `gpt-5.6-sol` | medium，32,000 output tokens | 需要长上下文、严格 JSON、精确归属和复杂论证关系；medium 是质量与成本的默认平衡点 |
| 独立来源忠实度复审 | `claude-sonnet-5` | 模型默认 adaptive thinking | 使用不同模型家族减少同源盲点；只审来源忠实度，不做神学批评 |
| OpenAI 仲裁 | `gpt-5.6-sol` | medium | 必须重新阅读同一来源，不能盲从 Claude |
| Claude 再审 | `claude-sonnet-5` | 同上 | 仅在 OpenAI 拒绝 Claude 意见时运行 |

只有来源异常复杂、模型持续分歧或结构判断特别困难时，才把单篇或单项升级为更高推理档位；不能把最高成本设置当作全库默认。

## 三、数据对象

一次详细整理产生以下对象，并为模型生成的短 ID 加入讲道命名空间，避免多篇讲道的 `CL001`、`E001` 相互冲突：

- `Question`：教授或听众实际提出的问题；
- `PositionNode`：教授转述并赞同、限定或反驳的立场；
- `Observation`：经文、原文、文体、上下文、历史文化等观察；
- `EvidenceStep`：教授论证中的证据或推理步骤；
- `Claim`：教授明确提出或由其论证直接支持的候选主张；
- `EvidenceRelation`：证据步骤之间的支持、回答、限定和反驳；
- `ClaimRelation`：主张之间的支持、解释、限定、反驳和发展关系；
- `SourceFragment`：绑定来源版本、段落版本和逐字引文的精确锚点。

所有模型输出默认是 `candidate`。AI 共识修正后的结果仍是 `not_human_approved`，不能因此自动公开出版。

## 三点五、第一遍抽取的单位是窗口，不是整篇

### 为什么改

整篇一次读完，模型做的是**取舍**，不是**穷举**。太16:21–23 母本在 #86 修复之后，134 句实质散文只有 66 句进入论证层（49%）；输出用了 32,000 上限中的约 18,000，所以不是被截断，是它自己选的。同一个模型、同一份文字，逐句提问时（ledger second pass）判定第一遍漏掉的句子里有 **45%** 是真材料。

差别只在提问方式：

| | 整篇模式 | 窗口模式 |
|---|---|---|
| 范围 | 整份文件 | 约 5 段 |
| 上下文 | 全文 | 该段前后各 5 段（只读） |
| 指示 | 「整理出论证层」（开放） | 「这几段里每一句承载论证的都要产出」（穷举） |

### 怎么切

两条规则撑起整个设计：

- **看得宽，只答窄**：一个窗口看见 `fetch + 2 × context` 段，但只为中间 `fetch` 段负责，并且在那几段里必须穷举。
- **每段只有一个归属窗口**：任何一段都恰好落在一个窗口的负责范围内，所以**重复不可能产生**——去重是归属问题，不是相似度问题。

重叠买到的不是更多记录，是模型看得懂手上这条记录**为什么**重要。窗口每次前进 `fetch` 段、宽 `fetch + 2 × context` 段，因此两段被同一窗口同时看见的**保证距离是 `2 × context`**。默认 `fetch=5, context=5`：15 段的视野，10 段的保证，对应全库 380 条可定位 observation → evidence_step 关系的 ≤10 段一行——講道 97.1%、母本 98.6%。

### `##` 是硬边界，`###` 不是

「用标题切」这件事里藏着两个不同的东西，只有一个是边界。

**`##` 是生成单元。** 母本不是一次写成的：`stage1_units.json` 记录太16:21–23 母本有四个 unit，而它们**正好就是四个 `##` 段落**，每个 unit 由自己的 `start_line`／`end_line` 独立生成。

```
u001  Wrede彌賽亞秘密理論的內容與批判        = ## 一、
u002  從馬可福音現象回應Wrede的錯誤解經      = ## 二、
u003  彌賽亞身分與性質的兩階段教導           = ## 三、
u004  捨己與背十字架的釋經與應用             = ## 四、
```

实测也印证：窗口模式产出的 264 条窗口内关系，**0 条**跨 `##`；20 条跨窗口远距离关系，只有 1 条跨。

所以**窗口不跨 `##`，上下文也裁到单元边界为止**。隔壁单元的文字不是本段论证的背景，它是另一段独立写成的论证，恰好排在旁边。

**`###` 以下不是边界。** 它是单元**内部**的编辑骨架——釋經 / 神學意義 / 生活應用 / 附錄。那 20 条远距离关系**全部**跨 `###`：编辑把事实放在「釋經」，把由它推出的一步放在「神學意義」。按 `###` 切，切的正是这条工作要保住的边。

| | 窗口内关系 (264) | 远距离关系 (20) |
|---|---:|---:|
| 跨 `##` | **0 (0%)** | **1 (5%)** |
| 跨 `###` | 2 (1%) | 20 (100%) |
| 跨 `####` | 4 (2%) | 20 (100%) |

另一个理由是覆盖面：115 份已发布逐字稿中 25 份做过分段（用 `##` 与 `###`），其余 90 份没有任何标题。没有标题的来源只有一个单元，纯机械切窗——机制对两类来源是同一套。

`###` 仍然有用，只是用在别处：窗口 prompt 带上所在标题链（`二、從馬可福音現象回應Wrede的錯誤解經 > 附錄`），以及边界落在它附近时吸附过去。

### 合并

- **ID**：每个窗口都答 `OBS001`、`E001`，合并前加窗口前缀（`W03-OBS001`），再加来源命名空间。
- **归属**：记录属于**第一个锚点所在段落的归属窗口**；其他窗口对同一段产出的记录是冗余的，丢弃。
- **关系改写**：指向被丢弃记录的关系，改写到覆盖同一段文字的存活记录上。
- **无法改写时保留记录**，不是丢掉关系。近似重复的记录看得见、代价小；被切断的 observation → evidence_step 边会让一条 `load_bearing` 观察变成孤儿，而那正是这条工作线要消灭的失败。

### `load_bearing` 校验移到合并之后

`argument_role=load_bearing` 却没有对应关系即整次失败——这条规则**在单个窗口内无法回答**：它所推出的那一步可能在下一个窗口的负责范围里。在窗口层强制它，不会让模型更努力，只会让最省事的过关方式变成把观察改标 `background`，也就是 #86 刚堵上的那个漏洞。

所以窗口层只校验机械事项（锚点逐字、ID、枚举、窗口内引用完整）；`load_bearing` 与「主张必须有证据」等整体规则在合并后的完整包上执行。

### 跨窗口关系：把窗口看不见的那几条买回来

窗口保证 10 段。太16:21–23 母本的整篇模式产出过 7 条跨度 11–21 段的关系，每一条都是编辑骨架的那道缝：事实在「釋經」，由它推出的一步在「神學意義」，中间隔着「附錄」。

```
span 16  可8:27-33 彼得宣认后耶稣立刻预告受苦  →  门徒缺少的是对弥赛亚性质的认识
span 21  马可反复呈现的四项现象              →  太16:20 保密命令要放在事工处境中解释
```

所以合并之后再跑一次 `cross_window_relation_runner`。它**不重读原文**：此时每条记录都已经是「一句话 + 一个段号」，问题的宽度是 374 条短陈述，不是一份讲稿。两条约束让它不会变成第二次抽取：

- 只能在**清单里已有的对象之间**建立关系。它看不到原文，所以无从引用，也无从新增材料。
- 只能提出**跨度大于窗口保证**的关系。窗口能看见的那些是抽取的职责；在这里重提，等于让一个没有锚点的模型去翻案一个有锚点的判断。

最小跨度**从包里的 window_plan 推出**（`2 × context + 1`），不另外配置：窗口调宽，这一阶段自动收窄，没有第二个地方需要记得改。

实测在太16:21–23 母本上补回 11 条证据关系与 9 条主张关系，整篇模式那 7 条的内容全部涵盖。

### 可复现性

窗口计划（`fetch`、`context`、`snap`、窗口边界）进入抽取指纹。不进指纹的话，换一个窗口大小重跑会匹配到旧指纹而被跳过，staging 里就留下一个回答着没人再问的问题的包。

每个窗口的通过结果按指纹缓存。一份来源现在是几十次调用，没有缓存的话第 20 个窗口失败就等于前 19 个白跑。

### 实测：太16:21–23 母本

```
                        整篇模式(v2)      窗口模式(v3)
实质散文                 66/132  50.0%    130/132  98.5%
经文引用区块              9/19   47.4%     16/19   84.2%
全部句子                 75/208  36.1%    148/208  71.2%

observation                 24                125
evidence_step               29                142
claim                       23                107
evidence relation           32                208   (含跨窗口补回 11)
claim relation              16                 76   (含跨窗口补回  9)
```

markdown 标题 51 句维持 0%，这是设计使然，不是缺口——它们本来就不该进入分母。

代价：24 个窗口、38 次调用、358k tokens（prompt cache 命中 47%），约 25 分钟。整篇模式是 1 次调用。这是本改动的真实成本，不要在别处假装它不存在。

## 四、程序机械闸门

在任何 AI 复审之前，程序先拒绝：

1. 不是逐字稿原文的 anchor；
2. 无法解析的段落定位；
3. 重复、缺失或悬空的对象 ID；
4. 指向不存在节点的关系；
5. 把听众、反方或非断言内容标成可支持教授主张的证据；
6. 没有证据的主张；
7. 来源、prompt、模型、生成设置或 schema 世代不一致的 cache。

抽取指纹包含来源 SHA256、prompt SHA256、模型 ID、reasoning effort、token budget、schema 版本及 response schema SHA256。旧结果在覆盖前归档，不能把不同抽取世代静默混合。

## 五、双模型复审与最小修正规则

Claude 必须重新阅读完整来源，并逐条检查说话者、立场、锚点、限定、遗漏和关系。Claude 的工作不是评论教授的神学是否正确。

Claude 提出的每项问题由 OpenAI 依据同一完整逐字稿独立裁决：

- OpenAI 接受：产生有界、可执行的版本化补丁并自动应用；
- OpenAI 拒绝：Claude 阅读其理由再审；
- Claude 撤回：保留原候选；
- Claude 仍坚持：只把这一项真实分歧交给人工。

补丁必须遵守**最小修正规则**：错误发生在哪一种对象，就修改哪一种对象。例如一条 `ClaimRelation` 错误时，应删除或更正该关系，不能为了保留错误关系而扩大教授的 Claim。补丁语言必须覆盖被审对象；无法用有界补丁表达的拆分、合并或编辑构思不得伪装成自动接受。

整个过程保留三层记录：原始抽取、独立复审与仲裁记录、应用共识补丁后的新候选包。任何一层都不静默覆盖上一层。

## 六、011WSR01 真实试运行

`011WSR01` 的原始逐句详细整理得到：

| 对象 | 数量 |
|---|---:|
| Source Fragment | 66 |
| Question | 8 |
| Position | 4 |
| Observation | 20 |
| Evidence Step | 37 |
| Claim | 17 |
| Evidence Relation | 26 |
| Claim Relation | 11 |

候选内容包括“那人子”的定冠词、但以理七章与神性身份、司提反异象、耶稣受死时间、逾越节、圣餐、新约及宗主国—附庸国之约等论证。

Claude 最终提出三项来源忠实度问题，OpenAI 全部独立接受并自动应用最小补丁：

1. 从一条主张中排除与亚那／该亚法无关的错误 anchor，并补入确切历史背景原话；
2. 删除一条错误的 `qualifies` 主张关系，而不扩大原主张；
3. 为逾越节杯与唱诗的陈述补入实际支持它的逐字来源。

结果为：3 项 `auto_applied`，0 项撤回，0 项持续分歧，0 项转人工。这个结果只说明本样本的来源忠实度分歧已解决，不表示其内容已经获得人工批准、神学认可或出版许可。

应用三项共识补丁后，审核候选包包含 68 个 `SourceFragment`、39 个 `EvidenceStep`、17 条 `Claim`、26 条证据关系和 10 条主张关系。增加的片段和证据来自两项来源修复；减少的一条主张关系是模型一致同意删除的错误关系。原始抽取包、复审记录、仲裁记录、补丁和审核候选包均分别保留。

### 接入共享知识模型

审核候选包已经接入 `shared_knowledge_pilot_v1.json`，不是停留在单篇讲道的孤立输出中。当前共享包包括：

- 2016 NYSC 第三、第四讲的既有详细论证资料；
- `011WSR01` 的双模型共识候选资料；
- 共 3 个来源、47 条候选主张、182 个证据步骤、151 条证据关系和 17 条主张关系。

17 条主张全部进入独立的 `CP-matthew-26-1-30-011` 释经编排：正文按太26:1–30的经文顺序处理受难预告、领袖密谋、伯大尼膏抹、犹大出卖、逾越节筵席、背叛预告和圣餐；约论进入背景附录，司提反异象所引出的救恩论主张明确转介其他专题，不强塞进正文。`DK-f0eac41a4244-CL001` 与 `DK-f0eac41a4244-CL002` 同时连接到“那人子”专题，其中第一条服务 `CD-SON-001` 与 `CD-SON-002`。这验证了同一主张可以同时服务释经和专题，却分别保存编排决定。

审核 UI 会显示这些新增主张、AI 复审结论、精确时间链接、证据关系、主张关系及专题编排去向。AI 已无分歧的项目标记为 `ai_cleared`；这只减少人工来源忠实度复核，不等于自动批准出版。

## 七、运行方式

生成单篇详细知识包：

```bash
PYTHONPATH=. .venv/bin/python -m backend.pipeline.detailed_knowledge_extraction_runner \
  --ids 011WSR01
```

窗口大小可以调，且**必须靠 ledger 分数来调，不靠改措辞**。改动前后各跑一次 `sentence_ledger_runner`，比较 `by_category.prose.represented_pct`：

```bash
PYTHONPATH=. .venv/bin/python -m backend.pipeline.detailed_knowledge_extraction_runner \
  --ids 011WSR01 --window-fetch 5 --window-context 5 --dry-run

PYTHONPATH=. .venv/bin/python -m backend.pipeline.sentence_ledger_runner \
  --source <来源文件> --package <抽取包>
```

合并之后补回跨窗口关系（最小跨度从包里的 window_plan 推出，通常不必指定）：

```bash
PYTHONPATH=. .venv/bin/python -m backend.pipeline.cross_window_relation_runner \
  --package <抽取包> --output <补关系后的包>
```

用 Claude 审阅指定候选包：

```bash
PYTHONPATH=. .venv/bin/python -m backend.pipeline.corpus_ai_review_runner \
  --claim-layer-package "$DATA_BASE_DIR/wang-knowledge-platform/staging/claim-layer/detailed-extractions/011WSR01-f0eac41a4244.detailed-knowledge.json" \
  --claim-layer-output "$DATA_BASE_DIR/wang-knowledge-platform/staging/claim-layer/detailed-extractions/011WSR01-f0eac41a4244.independent-review.json" \
  --spot-check-percent 0
```

随后运行 OpenAI 仲裁，并把共识补丁应用为新候选包：

```bash
PYTHONPATH=. .venv/bin/python -m backend.pipeline.corpus_ai_adjudication_runner \
  --package "$DATA_BASE_DIR/wang-knowledge-platform/staging/claim-layer/detailed-extractions/011WSR01-f0eac41a4244.detailed-knowledge.json" \
  --review "$DATA_BASE_DIR/wang-knowledge-platform/staging/claim-layer/detailed-extractions/011WSR01-f0eac41a4244.independent-review.json" \
  --output "$DATA_BASE_DIR/wang-knowledge-platform/staging/claim-layer/detailed-extractions/011WSR01-f0eac41a4244.adjudication.json" \
  --overrides "$DATA_BASE_DIR/wang-knowledge-platform/staging/claim-layer/detailed-extractions/011WSR01-f0eac41a4244.overrides.json"

PYTHONPATH=. .venv/bin/python -m backend.pipeline.knowledge_consensus_applier \
  --package "$DATA_BASE_DIR/wang-knowledge-platform/staging/claim-layer/detailed-extractions/011WSR01-f0eac41a4244.detailed-knowledge.json" \
  --overrides "$DATA_BASE_DIR/wang-knowledge-platform/staging/claim-layer/detailed-extractions/011WSR01-f0eac41a4244.overrides.json" \
  --output "$DATA_BASE_DIR/wang-knowledge-platform/staging/claim-layer/detailed-extractions/011WSR01-f0eac41a4244.reviewed-candidate.json"
```

## 八、中立 ResearchBatch：批次不是专题

当多篇讲道因为检索词、经文或研究问题而被选中时，系统先把它们登记为 `ResearchBatch`。`ResearchBatch` 只是一次可恢复的处理队列，不是神学分类，也不表示其中材料必然属于同一专题。

例如“约与律法”验证批次目前包括五篇讲道。选择它们只说明这些材料值得一起比较；系统不得预先写入“约”这个 canonical topic。每篇讲道必须先独立完成逐句详细知识整理、Claude 来源忠实度复审、OpenAI 仲裁和共识补丁应用，然后才把审核候选合并为一个**尚未分类**的研究包。

```mermaid
flowchart LR
    B["ResearchBatch：只定义处理范围"] --> E1["讲道 A 独立整理与复审"]
    B --> E2["讲道 B 独立整理与复审"]
    B --> E3["其余讲道独立整理与复审"]
    E1 --> U["未分类的审核候选知识包"]
    E2 --> U
    E3 --> U
    U --> C["跨讲比较：重复、扩展、限定、冲突与无关材料"]
    C --> T["专题、释经、问答等候选去向"]
```

批次 schema 强制：

- `semantic_assumption` 必须为 `none`；
- 不允许预先填写 `topic_id`、`target_topic_id` 或类似语义归属；
- 必须允许材料保持 `unassigned`，也允许一条主张进入多个候选专题；
- 合并结果的 `topic_candidates` 与 `knowledge_routes` 初始为空；
- 只有后续跨讲比较才能提出候选去向，候选去向仍不等于人工批准。

当前中立批次配置位于 `backend/pipeline/research_batches/covenant_law_validation_01.json`。执行计划可先 dry-run，不调用任何模型：

```bash
PYTHONPATH=. .venv/bin/python -m backend.pipeline.research_batch_runner \
  --batch backend/pipeline/research_batches/covenant_law_validation_01.json \
  --dry-run
```

实际执行可用 `--stage extract|review|adjudicate|apply|merge` 分阶段恢复，也可使用默认 `all`。抽取、复审与仲裁都以来源、prompt、模型和 schema 指纹判断是否可以跳过；相同世代不会重复消耗模型调用。`--force` 只应用于明确要求重做的抽取与 Claude 复审。

代码现在已经覆盖到“生成未分类审核候选研究包”以及“跨讲主张比较与双模型归并”。后者只建立 `duplicate / supports / extends / qualifies / contrasts / supersedes / unrelated` 关系，并允许材料保持 `unassigned`；它仍不自动建立专题目录。完整规则与五篇实测见 [跨讲道主张比较与双模型归并流程 v1](./cross_sermon_relation_workflow_v1.md)。专题候选归纳、产品路由、篇章编排与出版审核是更下游的独立流程，不能由批次名称偷渡完成。

## 九、代码位置

- 抽取 schema 与验证：`backend/pipeline/detailed_knowledge_extraction.py`
- 抽取 runner：`backend/pipeline/detailed_knowledge_extraction_runner.py`
- 窗口切分与合并：`backend/pipeline/extraction_windows.py`
- 跨窗口关系：`backend/pipeline/cross_window_relation.py`、`cross_window_relation_runner.py`
- 跨窗口关系 prompt：`backend/pipeline/prompts/cross_window_relation_discovery.md`
- 抽取 prompt：`backend/pipeline/prompts/detailed_knowledge_extraction.md`、`detailed_notes_knowledge_extraction.md`
- 窗口测试：`backend/tests/test_extraction_windows.py`、`test_windowed_extraction_runner.py`、`test_cross_window_relation.py`
- Claude 复审：`backend/pipeline/corpus_ai_review.py`、`corpus_ai_review_runner.py`
- OpenAI 仲裁：`backend/pipeline/corpus_ai_adjudication.py`、`corpus_ai_adjudication_runner.py`
- 共识补丁应用：`backend/pipeline/knowledge_consensus_applier.py`
- 中立批次 schema 与合并：`backend/pipeline/research_batch.py`
- 中立批次 runner：`backend/pipeline/research_batch_runner.py`
- 首个验证批次：`backend/pipeline/research_batches/covenant_law_validation_01.json`
- 跨讲关系 schema 与共识应用：`backend/pipeline/cross_sermon_relation.py`
- 跨讲关系 runner：`backend/pipeline/cross_sermon_relation_runner.py`
- 跨讲关系测试：`backend/tests/test_cross_sermon_relation.py`
- 接入共享模型与产品路由：`backend/pipeline/shared_knowledge_pilot.py`
- 太26释经编排：`$DATA_BASE_DIR/wang-knowledge-platform/staging/claim-layer/composition_plan_matthew_26_1_30_011.json`
- 审核 API：`backend/api/thought_review.py`
- 测试：`backend/tests/test_detailed_knowledge_extraction.py`、`test_corpus_ai_review.py`、`test_corpus_ai_adjudication.py`
