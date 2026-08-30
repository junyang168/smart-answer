# 神学编辑综合文章流程 v1

> **读者**：神学编辑、Solution architect、Developer
> **类型**：流程
> **状态**：当前
> **与代码对齐**：2026-08-30
> **权威范围**：从审核过的教授材料形成神学编辑综合文章时，编辑判断、运行阶段、artifact 边界、停止状态与验收条件。

## 目录

1. [目标](#一目标)
2. [产品边界](#二产品边界)
3. [两种忠实](#三两种忠实)
4. [角色](#四角色)
5. [Artifact chain](#五artifact-chain)
6. [状态机](#六状态机)
7. [TheologicalEditorialBrief](#七theologicaleditorialbrief)
8. [材料充分性](#八材料充分性)
9. [写作与审核](#九写作与审核)
10. [可重复性](#十可重复性)
11. [“教会的根基”golden case](#十一教会的根基golden-case)
12. [第二主题 smoke run](#十二第二主题-smoke-run)
13. [非目标](#十三非目标)

## 一、目标

这条流程的产品不是逐字稿摘要，也不是把同一主题的来源依次堆在一起。它要让教会编辑在不增加、删减或裁决教授教导的前提下，按读者真正要理解的问题，全面而忠实地阐明教授的释经与神学思想。

POC 的交付物是一条可重复执行的流程。“教会的根基”只是第一项 golden case。流程换到另一个经文或主题时，不得修改通用代码、添加主题专用 prompt 条款，或在流程外手改正文才能完成。

## 二、产品边界

神学编辑综合文章由教会编辑部编纂。教授是思想与讲授材料的来源，但文章的选题、顺序、标题、段落关系和详略是编辑判断，不得伪装成教授逐字说过的目录或句子。

文章可以综合教授在多篇讲道和母本中实际表达的观点，也可以并列多条实际出现过的 `ArgumentRoute`。它不得把不同来源的零散论据拼成教授从未采用过的一条“超级路线”，不得把“反对 A”自动翻转成编辑选择的 B，也不得替教授回答他没有回答的问题。

母本是优先且重要的来源，不自动成为综合文章的目录权威。文章骨架由当前主题的审核过 `ViewpointStructure` 与编辑说明书共同决定；母本中的承重论证、限定和实际路线仍受来源忠实 gate 保护。

## 三、两种忠实

流程分别检查两件事：

1. **来源忠实**：每项实质主张能否回到教授的 Claim、Evidence、来源片段或实际 `ArgumentRoute`；
2. **结构忠实**：标题、导言、小标题、篇幅、顺序和结论是否让教授的正面主张成为读者的理解中心，并保留重要限定、张力和未决关系。

两者都必须通过。来源齐全不能补偿结构失真；结构清楚也不能补偿无来源的编辑推论。

## 四、角色

| 角色 | 负责判断 | 不得做的事 |
| --- | --- | --- |
| Scope Editor | 定义读者问题、经文／主题边界和候选结构 | 先写答案，再寻找支持材料 |
| Evidence Compiler | 从审核过的记录机械编译最小证据包 | 判断神学正误、选择文章立场 |
| Composition Agent | 形成 `TheologicalEditorialBrief` 候选，决定观点角色、顺序、详略和 route-out | 写最终散文、创造新观点或新路线 |
| Composition Reviewer | 判断 brief 是否忠实、完整、可写，材料是否足够 | 用外部神学补洞、代写正文 |
| Author Agent | 严格按通过审核的 brief 和证据包写文章 | 改变中心、静默调和、补无源桥梁 |
| Editorial Reviewer | 判断正面中心、论证清晰度、普通读者可读性与写作质量 | 重做抽取、直接修改稿件 |
| Revision Agent | 只处理已接受 finding，并报告 disposition | 借修订改变 composition 意图 |
| Program Audit | 验证 SHA、来源、段落归属、路线与程序不变量 | 给文笔打分或作神学裁判 |

## 五、Artifact chain

每次运行依次产生以下 artifact：

1. `EditorialScope`：读者问题、产品类型、主题／经文范围、所选 structure revision 和明确排除项；
2. `TheologicalEvidencePacket`：所选 structure、CVP revision、source-local `ArgumentRoute`、Claim、Evidence、来源片段，以及 scope 内每份逐字稿与母本的完整原文；它是本产品自己的输入契约，不依赖 `ViewpointKnowledgeProjection`；
3. `TheologicalEditorialBriefCandidate`：Composition Agent 的结构化编辑方案；
4. `TheologicalEditorialBriefReview`：独立审核结果及正式材料充分性判断；
5. `TheologicalEditorialBrief`：只含已通过或经共识修订的有效说明书；
6. `TopicAuthoringPacket`：brief、证据和产品 profile 的写作切片；
7. manuscript、author ledger、Independent Editorial Review、Revision 与 Final Delta Review；
8. Program Audit 与 automated publication decision。

每项 artifact 都记录 schema version、上游 SHA、生成指纹、prompt、model 和 generation。输入改变产生新 generation，不能覆盖旧判断；相同指纹已经完成时跳过模型调用。

## 六、状态机

```mermaid
flowchart LR
    S["EditorialScope"] --> E["Evidence compile"]
    E -->|"结构或记录无效"| X["invalid_authority_input"]
    E --> C["Composition"]
    C --> R["Composition review"]
    R -->|"材料不足"| I["insufficient_material"]
    R -->|"关系未决且影响主问题"| U["unresolved_structure"]
    R -->|"可写"| B["Approved brief"]
    B --> A["Author"]
    A -->|"需改变 brief"| H["composition_change_required"]
    A --> D["Independent Editorial Review"]
    D --> V["Revision"]
    V -->|"finding 属于 brief 而非 prose"| C
    V --> F["Final Delta Review"]
    F -->|"仍有 finding 且轮次允许"| V
    F -->|"无可执行修复"| M["human_editor_required"]
    F -->|"通过"| P["Program Audit"]
    P -->|"错误"| G["program_audit_failed"]
    P -->|"零错误"| Q["Automated publication decision"]
```

停止状态是正式结果，不是异常文字。每项状态必须指出失败阶段、绑定 artifact SHA、reason code、受影响的 viewpoint／route／claim ID，以及下一步需要补材料、改结构还是人工判断。

## 七、TheologicalEditorialBrief

`TheologicalEditorialBrief` 是编辑判断的权威，不是文章草稿。它至少包含：

- `reader_question`：文章要回答的一个主要问题；
- `reader_takeaway`：编辑预计读者读完能复述的正面中心，明确标记为编辑综合；
- `opening_contract`：开场先介绍什么解释或经文问题、为什么需要检验、唯一统摄问题、进入哪一节及首先展开什么经文证据；它规定读者路径，不代写开场白；
- `reader_argument_contract`：一个中心答案、三至五步 proof chain、每步的依赖 section／Claim／ArgumentRoute，以及每项正面表述与中心答案的关系；关系只能是来源明确、编辑重述、带限定推论或未决，不得用相近措辞代替关系判断；
- `conclusion_contract`：确定回答及其 Claim、正面材料的作用次序、未决关系唯一披露位置、应用边界位置、结尾功能与支撑最后一句的 Claim；它规定读者最后得到什么答案，不代写结尾；
- 所选 `structure_revision_id` 及其内容 SHA；
- 每个 focal viewpoint 的 revision、结构角色、归属类别、是否进入正文，以及进入正文时承担的 section；
- 每条选中 `ArgumentRoute` 的 revision、source-local attestations 和文章功能；
- `unresolved_items`、不可升级的模态、不可静默调和的张力；
- ordered sections，每节的统摄问题、阶段结论、所依赖的前节、读者功能、观点、路线、必要限定和禁止承担的功能；
- 每条路线在该节承担的是主证、旁证、限定、异议回应还是应用；
- `route_out`，列出所有不进入正文的相关材料及理由；
- 导言、标题和结论如何共同承载正面中心。

人类编辑已经批准的写作提纲、材料位置和文章形态必须进入 SHA-bound `EditorialScope`，不能只留在聊天记录、一次 review finding 或 staging 旁边的反馈文件。Brief 逐项声明这些约束如何落实，Composition Reviewer 逐项独立判断；任何一项未满足都不能批准。能机械判断的约束由程序先判，例如正文 H2 数量、禁止独立 introduction section，以及指定 viewpoint／route 必须作为 footnote 或 inline note；模型不得用“整体方向一致”替代这些检查。

Structure 中的每个 focal viewpoint 必须恰好落入正文 section 或 `route_out`。任何静默遗漏使 brief 无效。`central_claim` 与至少一项适用的 `positive_identification` 必须进入正文；`negative_boundary` 不得成为 reader takeaway；`application` 不得先于其依赖的正面释经；`tension_side` 和 `qualification` 不得被改写为无条件断言。

各节不能只是材料容器。第一节没有前置依赖；此后每节必须声明它承接的前节，使整篇形成可检查的递进。每个使用路线的 section 至少有一条主证；旁证、限定、异议回应和应用不得在 heading 中被摊成与统摄问题同等级的并列题目。Composition Reviewer 必须逐节明确判断 heading 是否与统摄问题、阶段结论一致，路线功能是否保持主次，并单独判断整篇递进是否成立；这些判断有一项不成立，就不能批准 brief。

指定为 footnote 或 inline note 的材料仍完整进入所在 section 的 viewpoint／route ledger，并保留来源、模态和限定；它同时记录在该节的 embedded material ledger，route 在文章层只能承担旁证、限定或异议回应，不得成为主证，也不得另建 H2。这样“放进注释”改变的是呈现分量，不是把教授讲过的内容删掉。

## 八、材料充分性

Composition Review 在写作前回答“现有材料能不能忠实回答 reader question”。它分别检查：

- structure 是否为 active/current，revision 是否经过允许级别的审核；
- 每个进入正文的 CVP 是否仍是 current revision；
- 每项实质正面识别是否至少有来源 Claim 和合格 Evidence；
- brief 要展开的每条论证是否对应一条真实、source-local 的 `ArgumentRoute`；
- 若无完整路线，brief 是否诚实降级为有限陈述，而不是由编辑补桥；
- 未决关系是否被保留，且不妨碍 reader question 获得诚实的部分回答；
- route-out 是否覆盖已知但不适合本篇的批驳、应用、重复材料和旁支。

Composition 必须先给 scope 内每项重要正面表述分类，再决定文章形态。若两项表述都可能承担中心答案，而原稿没有说明它们是同义、层级、并列还是选择关系，不能把它们放进一篇文章后交给 Author 自行解释；`reader_argument_contract.shape_decision` 必须停止、拆篇或缩窄问题。缩窄后，被 scope 明确排除的记录继续保留在 route-out 与审计元数据中，但不能仅因全题存在未决关系就强迫 Author 在正文重新列出这些记录。

材料不足时不调用 Author。`insufficient_material` 可以是成功的研究终态：它证明流程没有把缺口变成散文。

Claim、Evidence、`ArgumentRoute` 与来源片段是查找材料的索引，不代替神学编辑阅读原稿。Composition Agent、Composition Reviewer、Author Agent 与 Editorial Reviewer 都必须收到 scope 内每份完整逐字稿和完整母本。Evidence Compiler 逐份校验原文件 SHA，记录来源类型、字符数、正文 SHA 与完整覆盖 manifest；缺少、改变或静默截断任何一份原稿时，必须在模型调用前停止。

POC 的直接输入上限为全部原稿合计 120,000 字符；“教会的根基”实际六份原稿共 83,084 字符，可以完整送入。超过上限不得自动退化为只看片段，而应停止在 `batched source reading required`。后续批次读取实现必须证明每个来源从头到尾都被覆盖，并把批次边界与原稿 SHA 绑定，全部覆盖后才可进入 Composition 或 Author。

## 九、写作与审核

Author 必须让正面中心成为全文主线与读者最后的记忆中心。通常先写正面中心，再使用反方材料限定误读；若 SHA-bound 人类 approved outline 明确用一项争议或否定边界发动经文问题，则以该约束为序，入口须迅速进入经文检验，后文仍须完整建立正面答案。标题、导言、主要小节和结论应让普通读者能够回答“教授主张什么、为什么”。教授花在批驳上的讲授时长不自动决定文章篇幅。

Author 写作前必须读完整逐字稿和母本；来源片段只帮助定位具体证据。Composition 与 Editorial Review 也使用同一份完整原稿包，不能只根据 CVP、route summary 或 brief 判断忠实度。

开场是独立的质量对象。Brief 必须用 `opening_contract` 建立“受检验的解释或经文问题—为什么需要检验—一个统摄问题—首项经文证据”的路径；Author 必须逐字使用获批的统摄问题，不得用多个连续问句或候选答案清单替代这条路径。导言中的“然而、但是、因此、所以”等连接词必须表达真实的转折或因果，不能只承担换题作用。

结尾也是独立的质量对象。Brief 必须用 `conclusion_contract` 先锁定确定回答，再按直接回答、补充经文和带限定的推论安排正面材料的作用层级；它不能把几项材料压成平面 inventory。未决关系只在正文最相关的一处披露，结尾不重复；应用边界放在最终答案之前或注释中。Author 的最后一句必须由契约列出的 Claim 支持，并直接回答开头的问题，不能落在 section 复盘、编辑过程或否定边界。

接地按句子做的事分两条路。下结论的句子——释经判断、推论、神学主张——必须回到该段声明的 Claim，一如既往。还原教授怎么教的句子——讲法框架、比喻、字词解释、事件时间、地点背景——可以改由 provenance 的 `texture_anchors` 支持：每项锚定 scoped source original 的一个逐字片段（至少 10 字），程序在 Author 交稿与每次 Grounding 时都逐字核对。材料里有教授自己的具体讲法时优先采用并锚定，不得因无对应 Claim 而改写成抽象转述。texture 只承载教学血肉：锚文之外的结论、因果与动机仍须 Claim 支持；纯叙事段落可以没有 claim_ids，但不得声明 Evidence Step 或 ArgumentRoute。

段落 provenance 分开记录 Claim 与 `ArgumentRoute`：Claim 回答本段声明了哪些教授主张，route revision 回答这些主张在本段组成哪一条实际论证。后台来源预览对论证段落必须沿 section 批准的 source-local route attestation 和 step bindings 取片段，按前提、限定、异议回应与结论显示；不得把 Claim 背后的全部 Evidence Step 按来源合并后冒充本段论证。没有使用路线的经文引述或简单陈述才回退到 Claim Evidence。

文章必须采用第一层的释经论证视角：以经文问题、观察、推理和结论推进，让读者跟着教授的论证走；不得以“教授有几种看法”“现有材料如何分类”作为全文骨架。导言可以一次交代文章整理的是王教授的讲论，未决关系也必须在真正影响结论处披露；但反复使用“教授认为／指出／判断”“一种／另一种识别”“现有材料”来组织标题和段落，会把文章写成思想分析或审核报告，应作为 hard failure 退回 Composition，而不是只在 Revision 中替换措辞。

初稿仍只调用一次 Independent Editorial Review。每轮 Revision 后恰好调用一次 Final Delta Review；同一 delta 响应返回下一轮 finding。不得增加 Score-Gap Review，也不得把修订稿重新送去全文初审。

Final Delta Review 以 changed paragraphs 为审核范围，但 packet 同时携带修订后全文作为位置上下文。全文只用于确认改动段落的真实相邻关系、provenance 边界、标题层级和文章实际结尾，不授权重审未改段落。Paragraph diff 会省略文字未变但位置仍在插入段之后的收束段；Reviewer 不得从 diff 的 insert／delete 顺序推断最后一句。

若 Reader-prose Review 发现问题被 brief 锁定（例如 approved heading 本身静默统一未决关系），Author 不得绕过 brief 修改。流程产生 `composition_change_required`，将 finding 转为 Composition Review finding，正式修订 brief，经过一次 Final Composition Review 后重新生成文章。实跑证明这不是异常边角：golden case 的第一版 brief 就在下游被发现把“信仰告白”与“所领受并传下的真理”合成一个小标题。

Composition finding 必须明确列出允许修改的 candidate 字段。Revision 后由程序计算 baseline 与 revised candidate 的真实 JSON diff：每个变动字段必须属于某项 finding 的授权范围，或被逐项申报为有理由且关联到具体 finding 的连带修改；申报但没有真实变化、真实变化却未申报，都会在 Final Composition Review 前失败。Final Composition Reviewer 还要看到 baseline、真实 diff、授权范围与连带修改，并复查 heading、统摄问题、阶段结论、路线主次和整篇递进，不能只确认旧 finding 表面上已消失。

Writing quality profile 必须把以下情况列为 hard failure：稿件虽有来源，但负面批驳、争论对象或错误观点取代了 brief 所声明的正面中心；或者真实的 source-local 路线虽然都出现了，却被写成没有统摄问题、没有证据主次和阶段结论的平面清单。Reviewer 应检查标题、导言、小标题和结论是否共同回答 reader question，并区分“跨来源拼接路线”与“路线真实但文章层级被压平”这两种失败；不能只在一般 `argument_organization` 说明中顺带提及。

Independent Editorial Review packet 另行提取 H1 与第一个 H2 之间的 reader prose，并附 Brief 的 `opening_contract`。Reviewer 必须先审核这段文字，且 `general_reader_readability` 的 evidence 至少逐字引用其中一句；只引用正文中段不能为导言背书。无根据的转折、没有被前句发动的问题、两个竞争问题或先于论证的答案清单，构成 `opening_reader_path_broken` hard failure，并须产生锚定在导言的 blocking finding。初审一旦漏掉未改动的导言，Delta Review 按范围继承便无法补救，因此这项责任不能下放到修订轮次。

同一 packet 另行提取最后一个 H2 下的 reader prose，并附 `conclusion_contract`。Reviewer 必须逐字引用结尾、用一句普通话复述读者最终得到的答案，并分别判断答案是否直接、编辑过程是否挤走答案、正面主张是否按契约推进、未决披露是否重复。结构化判断与 `conclusion_reader_answer_broken` hard failure 不一致时，review 无效；任一结尾失败都必须有锚定在结尾的 blocking finding。Final Delta Review 每轮重新读取完整结尾，防止 Revision 在修正 attribution、route 或其他 metadata 时把内部指令写进 reader prose，或新引入重复、平面清单和负面落点。

Reviewer 还须只按稿件本身重建“一句话问题—一句话答案—三至五步证明链”，至少逐字引用三个不同位置，再与完整原稿核对。缺推论桥梁、竞争答案或读者无法复述时，不得因为末句清楚或每段有来源而放行。`positive_thesis_and_structural_fidelity`、`argument_route_integrity`、`general_reader_readability` 与 `reader_memory_center` 中的任何 finding 都是 blocking；核心论证缺口不能标成 minor／nonblocking 后留待人工发现。

## 十、可重复性

可重复性不要求模型逐字生成同一篇文章，而要求：

- 相同权威输入产生相同 deterministic artifact SHA；
- 相同 generation fingerprint 不重复调用模型；
- 任一 artifact 缺失时可从它的直接上游恢复；
- 输入改变后建立新 generation，旧 artifact 仍可审计；
- 所有停止状态可由机器读取和解释；
- reader-visible prose 只能由正式 Author／Revision 阶段产生，流程外不得手改；
- 换主题时只更换 `EditorialScope` 与权威内容，不改通用代码或 prompt。

同一主题经过人类编辑决定后再生成，也不能只依赖模型“记得上次谈过什么”。决定一旦批准，就加入该 scope 的版本化 constraints，并绑定原 feedback artifact SHA；scope SHA 因此改变，后续每次生成都会收到并审核同一要求。聊天上下文可以帮助讨论，不能充当运行时契约。

Operator runbook 必须说明如何选择 scope、运行、恢复、查看停止状态、处理 composition change，以及如何确认没有使用主题专用 patch。

## 十一、“教会的根基”golden case

Golden case 的 reader question 是：

> 教授认为教会建立在什么根基上，他怎样从太 16:18 及相关经文形成这个判断？

当前审核结构要求 brief 至少保留：磐石不是彼得本人；彼得的基督论认信／所传真理；使徒和先知；“更可能是基督本人”的模态；三项正面识别之间尚未统一的关系；`Petros / petra` 与正典希腊文本的方法边界。

教皇论批驳只能在正面论证完成后承担应用或反方边界。删去这部分，文章仍须能够清楚回答 reader question。若做不到，结构忠实 gate 必须失败，即使所有句子都有来源。

## 十二、第二主题 smoke run

Golden case 通过后，从现有审核结构中选择一个较小、内容不同的主题运行同一 artifact chain。Smoke run 的验收不是必须出版；它可以完成文章，也可以合法停在 `insufficient_material` 或 `unresolved_structure`。

验收重点是：没有修改通用代码、schema 或 prompt，没有增加主题专用条件，所有阶段仍产生同类型 artifact，停止理由能够指向具体权威记录。

## 十三、非目标

- 不判断教授的神学结论是否正确；
- 不建立编辑预设的王教授神学体系；
- 不要求所有相关来源都进入一篇文章；
- 不以出现次数或讲授篇幅计算观点重要性；
- 不用一般神学知识补足教授没有回答的问题；
- 不把 POC 发布等同于部署生产环境。
