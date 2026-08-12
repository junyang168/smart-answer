# 王守仁教授讲道语料第一遍普查格式 v1

## 一、设计原则

第一遍普查的目的，是为全部讲道建立一张可以搜索、比较和复核的“内容地图”，不是立即写文章，也不是立即宣布教授的完整思想体系。

因此每篇讲道只保留五类信息：

1. **讲了哪些内容群；**
2. **提出了哪些候选主张；**
3. **主张使用了哪些圣经依据；**
4. **教授采用了什么释经或推理方法；**
5. **每项判断如何回到原始逐字稿核查。**

这一格式比 manuscript 阶段的 `evidence_inventory.json` 更轻。它允许暂时遗漏细枝末节，但不允许虚构主张、经文或主张之间的关系。

## 二、两层结构

### 1. Content Cluster

`content_clusters` 回答“这篇讲道谈了哪些相对完整的内容”。它不等于最终文章单元，也不依赖讲道标题。

每个 cluster 包含：

- `cluster_id`
- `title`
- `function`：`exegesis`、`theology`、`application`、`method`、`background`、`interaction`、`non_substantive`
- `summary`
- `segment_indexes`
- `scripture_refs`
- `topic_terms`

### 2. Candidate Claim

`candidate_claims` 回答“教授在这里主张什么”。每项均是候选材料，尚未自动成为教授思想体系中的正式命题。

每项包含：

- `claim_id`
- `statement`
- `claim_kind`：
  - `explicit_claim`：教授直接断言；
  - `reasoning_conclusion`：教授由若干理由得出的结论；
  - `interpretive_method`：教授反复使用或明确说明的释经方法；
  - `opposed_view`：教授明确反对的解释或立场；
  - `question`：教授提出、需要回答的实质问题；
  - `application`：教授对信徒观念或行动的指向；
- `attribution`：`explicit`、`close_paraphrase` 或 `editorial_inference`
- `cluster_ids`
- `scripture_refs`
- `relations`
- `anchors`
- `review_status`：第一遍固定为 `candidate`
- `confidence`：仅表示抽取把握，不表示神学正确性。

## 三、最小关系集

第一遍只记录明显关系：

- `supports`
- `answers`
- `opposes`
- `qualifies`
- `applies`

跨讲道的 `repeats`、`extends`、`changes` 和 `tension_with` 在汇总阶段产生，不能只因关键词相同就自动建立。

## 四、来源锚点

每个候选主张至少保留一个 `anchor`：

- `segment_locator`：普查内部唯一的 `S0001...`；
- `source_segment_id`：来源原有 ID，可重复或缺失；
- `source_segment_index`：来源数组顺序；
- `start_time`
- `end_time`
- `verbatim_excerpt`

`verbatim_excerpt` 必须是该 segment 的精确连续子字符串。程序可以据此计算字符位置并发现逐字稿更新造成的失效。

不得假设来源中的 segment ID 唯一。若同一篇逐字稿重复使用 `SEG-001` 一类 ID，验证和回链必须以该次普查分配的唯一 `segment_locator` 为准，同时保留原 ID、顺序和时间供审计。若模型返回旧 ID，只有在该 ID 或精确 excerpt 能唯一解析时才允许机械修复；否则该项必须重跑或人工处理。

第一遍不要求为同一句话建立大量重叠锚点。若一个结论依赖多个相隔较远的理由，则保留多个 anchors。

### 抽取世代

每份当前 survey 必须带 `extraction`：

- `generation_fingerprint_sha256` 识别共同的 prompt、model、generation settings 和 schema；
- `fingerprint_sha256` 再绑定本篇逐字稿 SHA256；
- 每条 `candidate_claim` 带同一个 `extraction_fingerprint`。

没有这些字段的历史文件仍可单独阅读，但属于 legacy，不能进入新的跨讲综合。不同 generation 的 surveys 也不得在同一次综合中混用。被重抽结果替换的旧文件保存在 `generations/`，不静默删除。

## 五、经文依据与来源证据

两者必须分开：

- `anchors` 证明“教授确实这样讲过”；
- `scripture_refs` 记录“教授用什么圣经支持或解释这项主张”。

不得因为编辑者知道某节经文相关，就把教授没有使用的经文补入 `scripture_refs`。

### v1 限制与 v2 经文伴随记录

v1 的 `scripture_refs` 是原样字符串，足以防止凭空补经文，却不能可靠生成
释经覆盖地图。为避免重写已经完成的 205 篇普查，v1 文件保持不变；每篇另有一份
`wang_corpus_scripture_roles_v2` 伴随记录，把每一次引用展开为可审核对象：

```json
{
  "ref_key": "claim:C017:0:0",
  "owner_kind": "claim",
  "owner_id": "C017",
  "source_raw_text": "太 17:1–8；路 9:28–36",
  "raw_text": "太 17:1–8",
  "osis": "Matt.17.1-Matt.17.8",
  "display": "馬太福音 17:1–8",
  "role": "primary_passage",
  "role_reason": "该处持续解释登山变像叙事。",
  "confidence": "high",
  "attribution": "professor_used",
  "review_status": "candidate"
}
```

同一个原始字符串若含多卷书或多个明确范围，必须拆成不同 `ref_key`，使马太福音
可以是 `primary_passage`，路加福音可以是 `parallel_passage`。若只有“诗篇”或
“保罗书信”等泛称而没有章节目，保留 `osis: null` 和
`normalization_status: unresolved`，不得伪造章节目。

伴随记录保存原 v1 文件的 SHA256；机械验证必须保证：

1. 每个 v1 引用 occurrence 恰好有一个或多个可解释的展开记录；
2. 模型只能为既有 `ref_key` 分类，不可增删经文；
3. OSIS 由程序解析，不由模型填写；
4. 所有角色初始都是 `candidate`，未经人工审核不得作为公开的重点覆盖结论。

角色至少包括 `primary_passage`、`parallel_passage`、`lexical_support`、
`historical_background`、`theological_support`、`counterexample` 和
`application_basis`。若暂时无法判断角色，应保存 `unclassified`，不得默认为
`primary_passage`。v1 数据在完成角色回填前只能称为“经文提及”，不能称为
“重点释经覆盖”。

实现与运行入口：

- `backend/pipeline/corpus_scripture_enrichment.py`：拆分、OSIS 规范化和机械验证；
- `backend/pipeline/corpus_scripture_enrichment_runner.py`：使用 Terra Medium 判断论证角色；
- `backend/pipeline/prompts/corpus_scripture_role_enrichment.md`：角色边界与禁止事项。

## 六、人工审核

审核者至少判断：

1. 这是不是教授实际表达的主张；
2. 表述有没有比原话更强；
3. 是否误把听众问题、引用对象或被反对观点当作教授立场；
4. 经文是否真的承担所写的论证作用；
5. cluster 是否应拆分或与其他讲道内容合并。

只有审核通过后，candidate claim 才可进入持续修订的论证层。

### 忠实整理与事实核查的边界

现阶段的首要责任是完整、准确地呈现教授实际提出的主张及其论证强度，而不是在抽取时暗中修正教授的语言学、历史或神学判断。事实核查属于后续独立阶段，其结果必须与“教授主张什么”分别保存和展示。

第一遍普查只对以下两类明显问题作标记：

- 明显的圣经书卷、章节目或引文归属错误；
- 从上下文能够明确判断的口误、转写错误或人名误写。

即使发现这类问题，也要保留原始逐字稿，不可静默覆盖；候选主张中记录教授意图表达的内容，并附上 `review_warning`，供人工确认。对于尚有解释空间的事实或学术争议，只记录教授原主张，不在本阶段裁决。

### 保留教授主张的力度

若教授明确说“代表神”“我就是神”或使用同等强度的结论，整理稿不可自行弱化为“可能具有神圣意味”。反之，教授只说“属神”“指向”或提出问题时，也不可擅自升级为更强结论。正式文稿可以改善顺序与可读性，但必须保存主张的实际力度。

### 暂定结构必须可调整

跨讲道主题的章节归属可以暂定，但不能永久锁死。例如句首 `Amen` 目前可以作为“人子与耶稣神性”专论中的相关小节；若后续普查发现它在更多场景中形成独立、稳定且材料丰富的论证链，则可以提升为独立主题单元，并由原专论链接过去。

## 七、轻量普查与完整论证层的关系

```mermaid
flowchart LR
    A["已发布逐字稿"] --> B["第一遍普查：内容群与候选主张"]
    B --> C["人工抽样审核"]
    C --> D["全语料跨讲道聚合"]
    D --> E["重要主张进入完整论证层"]
    E --> F["释经讲座、主题专论与研究工具"]
```

不是每项普查结果都需要升级为完整论证图。只有反复出现、跨经文、具有争议、或将用于正式写作的主张，才需要展开完整的理由、反对意见、限定和证据网络。

## 八、跨讲道综合与覆盖计算

全语料不能一次塞给模型。综合采用两级结构：

1. 将每篇讲道压缩为保留全部 claim refs 的 sermon card；
2. 分批建立 candidate batch themes；
3. 全局综合只能选择真实存在的 `batch_theme_ref`；
4. 程序将所选 batch themes 展开为完整 claim refs，并机械计算覆盖讲道数和主张数；
5. 模型可以选择代表性 claims 供阅读，但不得把代表性 claims 冒充完整覆盖范围。

跨讲道综合结果仍是 candidate。`repeats`、`extends`、`qualifies`、`tension` 与 `supersedes` 必须保留其选中主题、展开后的 claim refs、理由和人工审核状态。
