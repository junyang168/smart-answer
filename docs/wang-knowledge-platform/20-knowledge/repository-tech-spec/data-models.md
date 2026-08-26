# Data Models

> **读者**：Developer
> **类型**：规范
> **状态**：当前
> **与代码对齐**：未核对
> **权威范围**：Every stored record type in the repository。本文是《文库 Technical Specification》的一部分。

本规范的其余部分：

| 文件 | 内容 |
| --- | --- |
| [Technical Specification: Exegesis and Topic Repository](./README.md) | Architecture, storage layout and identifiers |
| [Data Models](./data-models.md) | Every stored record type in the repository |
| [Evidence Pipeline, Compiler, Read Model and Invalidation](./compiler.md) | How source material becomes queryable state, and what invalidates it |
| [API, Frontend, Source Resolution and Authorization](./api-and-ui.md) | The surfaces this repository exposes |
| [Observability, Testing, Phases, Deployment and Acceptance](./delivery.md) | How the work is verified and shipped |

### Contents

- [5. Data Models](#5-data-models)
  - [5.1 CanonicalUnit](#51-canonicalunit)
  - [5.2 SourceDocument](#52-sourcedocument)
  - [5.3 SourceMap](#53-sourcemap)
  - [5.4 Citation](#54-citation)
  - [5.5 UnitCitationLink](#55-unitcitationlink)
  - [5.6 UnitRelationship](#56-unitrelationship)
  - [5.7 QuestionRecord](#57-questionrecord)
  - [5.8 ClaimRecord](#58-claimrecord)
  - [5.9 ClaimRelation](#59-claimrelation)
  - [5.10 ScriptureEvidence](#510-scriptureevidence)
  - [5.11 OriginalLanguageJudgment](#511-originallanguagejudgment)
  - [5.12 ApplicationReasoning](#512-applicationreasoning)
  - [5.13 EvidenceStep](#513-evidencestep)
  - [5.14 InferenceBridge](#514-inferencebridge)
  - [5.15 PassageInterpretationChain](#515-passageinterpretationchain)
  - [5.16 ExternalEvidence](#516-externalevidence)
  - [5.17 ThoughtMapRevision](#517-thoughtmaprevision)
  - [5.18 AnswerEvidenceBundle](#518-answerevidencebundle)
  - [5.19 PublicationProfile](#519-publicationprofile)
  - [5.20 CompositionPlan](#520-compositionplan)
  - [5.21 CompositionDecision](#521-compositiondecision)
  - [5.22 DeliverableReviewScope](#522-deliverablereviewscope)
  - [5.23 ReviewWorkItem](#523-reviewworkitem)

### 23 个模型速查

| 模型 | 它保存什么 |
| --- | --- |
| [5.1 CanonicalUnit](#51-canonicalunit) | 一个可发布的释经或专题单元：经文范围、章节结构、状态与它所用的引用。 |
| [5.2 SourceDocument](#52-sourcedocument) | 一份原始来源（讲道逐字稿、笔记、录音）及其类型与版本。 |
| [5.3 SourceMap](#53-sourcemap) | Evidence Inventory 使用的行号范围与原始来源表示之间的对应。 |
| [5.4 Citation](#54-citation) | 一处精确来源引用，定位到逐字稿或笔记中的具体位置。 |
| [5.5 UnitCitationLink](#55-unitcitationlink) | unit 与 citation 的多对多关系表，供编译后的数据库查询。 |
| [5.6 UnitRelationship](#56-unitrelationship) | unit 之间的关系（如释经单元与专题单元的相互引用）。 |
| [5.7 QuestionRecord](#57-questionrecord) | 一个被提出的问题：由教授、听众还是编辑提出，以及回答状态。 |
| [5.8 ClaimRecord](#58-claimrecord) | 教授的一条主张，并区分明确主张、论证结论、释经方法、反对观点与应用。 |
| [5.9 ClaimRelation](#59-claimrelation) | 主张之间的关系：supports、answers、opposes、qualifies、extends、tension 等。 |
| [5.10 ScriptureEvidence](#510-scriptureevidence) | 支持某条主张的经文依据，并标明是教授所用还是编辑补充。 |
| [5.11 OriginalLanguageJudgment](#511-originallanguagejudgment) | 原文（希腊文、希伯来文）判断，以及它是否忠实表述了教授的意思。 |
| [5.12 ApplicationReasoning](#512-applicationreasoning) | 从主张到生活应用的推理，以及该应用的规范强度。 |
| [5.13 EvidenceStep](#513-evidencestep) | 论证中的一步——文本观察、推论或其他类型。 |
| [5.14 InferenceBridge](#514-inferencebridge) | 前提与结论之间被补足的一步，并标明它属于教授明说、教授论证还是编辑推断。 |
| [5.15 PassageInterpretationChain](#515-passageinterpretationchain) | 跨多份来源的经文解释链。它是知识投影，不是文章大纲。 |
| [5.16 ExternalEvidence](#516-externalevidence) | 教授引用的外部证据：历史来源、文化背景、教会传统、学术立场等。 |
| [5.17 ThoughtMapRevision](#517-thoughtmaprevision) | 思想图的一次修订，操作限于 add、extend、promote、demote、split、merge、mark_tension、supersede。 |
| [5.18 AnswerEvidenceBundle](#518-answerevidencebundle) | 生成答案文字之前，由检索与图遍历确定性产出的证据包。 |
| [5.19 PublicationProfile](#519-publicationprofile) | 一种出版体例：章节名、语气、引用政策与编辑规则。作品快照它所用的 profile 版本。 |
| [5.20 CompositionPlan](#520-compositionplan) | 一篇作品的篇章编排计划，是一级记录，不是自由 Markdown。 |
| [5.21 CompositionDecision](#521-compositiondecision) | 编排中的一项决定：作为核心收入、简述、移入专题、移入附录或仅作链接。 |
| [5.22 DeliverableReviewScope](#522-deliverablereviewscope) | 某次交付物的最小依赖闭包，对冻结的交付物版本是确定的。 |
| [5.23 ReviewWorkItem](#523-reviewworkitem) | 一条人工审核任务及其结果：通过、修改后通过、拒绝、延后或阻塞。 |


## 5. Data Models

### 5.1 CanonicalUnit

```json
{
  "schema_version": 1,
  "unit_id": "CU-SEED-c74b23f20a7e",
  "title": "「人子」稱號、但以理書七章與耶穌的神性",
  "unit_type": "concept",
  "status": "published",
  "primary_bible_refs": [
    {"osis": "Matt.16.28-Matt.17.8", "display": "太 16:28–17:8"}
  ],
  "topic_assignments": [
    {
      "topic_ids": ["christology", "deity-of-christ"],
      "path": ["基督論", "耶穌的神性與權柄"],
      "role": "primary"
    }
  ],
  "manuscript": {
    "project_id": "17_章_登山變像_醫治鬼附之子",
    "project_type": "transcript",
    "heading_title": "二、「那個人子」所指的是誰？",
    "heading_anchor": "二-那個人子-所指的是誰",
    "final_sha256": "..."
  },
  "composition": {
    "publication_profile_id": "PP-passage-commentary",
    "publication_profile_revision": 2,
    "composition_plan_id": "CP-matthew-17",
    "composition_plan_revision": 4
  },
  "citation_ids": ["CIT-01J...", "CIT-01K..."],
  "relationship_ids": ["REL-..."],
  "aliases": ["人子", "那個人子"],
  "review": {
    "reviewed_at": "2026-08-04T00:00:00Z",
    "reviewed_by": "editor-id"
  }
}
```

Validation:

* `unit_type` is `passage` or `concept`.
* A passage unit requires at least one primary Bible reference.
* A published unit requires a resolvable manuscript section.
* A published unit requires at least one approved citation or an approved source exception with a reason.

### 5.2 SourceDocument

```json
{
  "schema_version": 1,
  "source_id": "SD-4ef32a...",
  "source_type": "sermon_transcript",
  "origin_id": "2016 NYSC 專題：馬太福音釋經（五）4",
  "title": "馬太福音釋經（五）第四講",
  "source_stage": "published",
  "delivered_at": "2016-07-02T14:00:00-04:00",
  "date_precision": "exact",
  "date_source": "sermon_page_metadata",
  "original_date_text": "2016 NYSC",
  "event_title": "2016 NYSC 靈命進深會",
  "venue": "New York",
  "series_title": "馬太福音釋經（五）",
  "public_url": "/resources/sermons/2016%20NYSC%20...4",
  "media": {
    "kind": "video",
    "url": "/web/video/2016%20NYSC%20...4.mp4"
  },
  "source_sha256": "...",
  "updated_at": "2026-08-04T00:00:00Z",
  "access": "authenticated_reader"
}
```

`source_type` values:

* `sermon_transcript`;
* `scanned_notes`; and
* future types may be added only with an implemented source reader.

Derived manuscripts and Google Docs are not original source types.

`delivered_at` records when the sermon or lecture was delivered, not when the
file was imported or updated. It may be `null` when unknown. `date_precision`
is `exact`, `month`, `year`, or `unknown`; `date_source` and
`original_date_text` preserve how the normalized date was obtained. These
fields are required for a defensible thought-development timeline. Filename
parsing may propose values but may not silently mark them as editor-approved.

### 5.3 SourceMap

The source map connects Project-source line ranges used by Evidence Inventory to the original source representation.

Transcript entry:

```json
{
  "source_line_start": 3,
  "source_line_end": 3,
  "paragraph_key": "31",
  "paragraph_position": 2,
  "paragraph_text_sha256": "...",
  "start_time": 99.0,
  "end_time": 236.0,
  "start_index": 31,
  "end_index": 103
}
```

Notes entry:

```json
{
  "source_line_start": 8,
  "source_line_end": 22,
  "page_file": "notes_main/chapter16/22.jpg",
  "page_position": 1,
  "ocr_line_start": 1,
  "ocr_line_end": 15,
  "page_ocr_sha256": "..."
}
```

Source maps are generated when a source is imported or rebuilt and are versioned by both Unified Input and original source checksums.

### 5.4 Citation

```json
{
  "schema_version": 1,
  "citation_id": "CIT-01J...",
  "source_id": "SD-4ef32a...",
  "source_sha256": "...",
  "locator": {
    "kind": "transcript",
    "paragraph_keys": ["31"],
    "highlight_text": "教授早年接受 Scofield 体系，后来转向专攻释经。",
    "highlight_text_sha256": "...",
    "occurrence": 1,
    "char_start": 120,
    "char_end": 168,
    "start_time": 99.0,
    "end_time": 236.0,
    "time_status": "available"
  },
  "evidence_ids": ["E003", "E004"],
  "role": "historical_background",
  "supports_claim": "教授由 Scofield 体系转向以释经为根基的神学训练。",
  "status": "approved",
  "revision": 2,
  "reviewed_at": "2026-08-04T00:00:00Z",
  "reviewed_by": "editor-id"
}
```

`locator.kind` is `transcript` or `notes`.

For transcript locators, exact text highlighting is required but media time is
not. `start_time` and `end_time` may be `null`; `time_status` is `available`,
`not_mapped`, `no_media`, or `unknown`. Adding a later transcript-to-media time
map revises the locator without changing the citation or claim identity.

Notes locator fields:

* `page_file`;
* `page_position`;
* `highlight_text`;
* `occurrence`;
* `ocr_char_start` and `ocr_char_end`; and
* optional future `image_regions` containing normalized bounding boxes.

Citation status values:

* `candidate`;
* `approved`;
* `rejected`;
* `stale`;
* `unresolved`; and
* `restricted`.

### 5.5 UnitCitationLink

Citation IDs may appear directly on the unit for simple lookup. The compiled database also stores an explicit many-to-many table:

```json
{
  "unit_id": "CU-SEED-c74b23f20a7e",
  "citation_id": "CIT-01J...",
  "display_order": 1,
  "role": "primary_evidence",
  "claim_anchor": null
}
```

`claim_anchor` is optional in the initial release. The required UI is a unit-level Sources panel. Future inline manuscript citation markers may reference the same citation IDs.

### 5.6 UnitRelationship

```json
{
  "relationship_id": "REL-...",
  "from_unit_id": "CU-passage-...",
  "to_unit_id": "CU-topic-...",
  "relationship_type": "related_topic",
  "status": "approved",
  "reason": "The passage contributes the primary narrative example for the topic."
}
```

### 5.7 QuestionRecord

```json
{
  "schema_version": 1,
  "question_id": "Q-01K...",
  "text": "门徒为什么不能赶出那鬼？",
  "questioner": "professor",
  "question_type": "interpretive",
  "bible_refs": ["Matt.17.19-Matt.17.21"],
  "topic_ids": ["faith", "spiritual-authority"],
  "raised_citation_ids": ["CIT-..."],
  "answer_claim_ids": ["CL-...", "CL-..."],
  "answer_status": "answered",
  "previous_question_ids": [],
  "superseded_by_question_id": null,
  "status_history": [
    {
      "status": "answered",
      "claim_ids": ["CL-...", "CL-..."],
      "citation_ids": ["CIT-..."],
      "recorded_at": "2026-08-07T00:00:00Z"
    }
  ],
  "review_status": "approved",
  "visibility": "public"
}
```

`questioner` is `professor`, `audience`, or `editor`. `answer_status` is
`answered`, `partially_answered`, `unanswered`, `deferred`, or `superseded`.
`raised_citation_ids` preserves where the question was actually raised;
`status_history` may point to later-sermon answers without rewriting the
earlier source. An editor-created organizing question is never attributed to
Dr. Wang.

### 5.8 ClaimRecord

```json
{
  "schema_version": 1,
  "claim_id": "CL-01K...",
  "statement": "门徒的小信包括对信心和属灵权柄认识不完整。",
  "claim_type": "reasoning_conclusion",
  "attribution": "professor_reasoning",
  "maturity": "strong_recurring",
  "review_status": "approved",
  "visibility": "public",
  "bible_refs": [
    {"osis": "Matt.17.19-Matt.17.21", "role": "primary_passage"}
  ],
  "topic_ids": ["faith", "spiritual-authority"],
  "scope_qualifiers": {
    "biblical_context": ["Matt.17.19-Matt.17.21"],
    "audience": ["disciples"],
    "temporal_stage": "earthly_ministry",
    "conditions": ["authority exercised in dependent trust"],
    "consequences": [],
    "opposed_view_claim_ids": []
  },
  "citation_ids": ["CIT-..."],
  "source_local_ids": [
    {"project_id": "17_章_登山變像_醫治鬼附之子", "local_claim_id": "C005"}
  ],
  "incoming_relation_ids": ["CR-..."],
  "outgoing_relation_ids": ["CR-..."],
  "revision": 3,
  "supersedes_claim_ids": []
}
```

`claim_type` values include `explicit_claim`, `reasoning_conclusion`, `interpretive_method`, `opposed_view`, `application`, `editorial_synthesis`, `open_question`, and `non_substantive`. `attribution` is independent of review status. Approving an editorial synthesis confirms the synthesis is editorially useful; it does not convert it into the professor's explicit statement.

`scope_qualifiers` is optional but required when a claim would otherwise be
misleading outside its passage, audience, salvation-discussion level, life
stage, or stated conditions. It is especially important for salvation
warnings, assurance, anthropology, church discipline, and situational ethics.

### 5.9 ClaimRelation

```json
{
  "schema_version": 1,
  "claim_relation_id": "CR-...",
  "from_claim_id": "CL-evidence...",
  "to_claim_id": "CL-conclusion...",
  "relation_type": "supports",
  "reason": "The source explicitly uses the first proposition as the reason for the conclusion.",
  "citation_ids": ["CIT-..."],
  "review_status": "approved",
  "confidence": "high",
  "visibility": "public",
  "revision": 1
}
```

Supported relation types are `supports`, `answers`, `opposes`, `qualifies`, `applies`, `repeats`, `extends`, `tension`, `supersedes`, and `editorial_inference`. Cross-sermon `repeats` and `extends` proposals require a reason and human review; lexical similarity alone is insufficient.

### 5.10 ScriptureEvidence

```json
{
  "scripture_evidence_id": "SE-01K...",
  "claim_id": "CL-...",
  "osis": "Dan.7.13-Dan.7.14",
  "display": "但 7:13–14",
  "role": "historical_background",
  "attribution": "professor_used",
  "citation_ids": ["CIT-..."],
  "review_status": "approved"
}
```

`attribution` is `professor_used` or `editor_supplied`. Editor-supplied cross references are excluded when the user asks which biblical evidence Dr. Wang himself used.

### 5.11 OriginalLanguageJudgment

```json
{
  "schema_version": 1,
  "judgment_id": "OLJ-01K...",
  "osis": "Mark.4.12",
  "language": "grc",
  "surface_form": "μήποτε",
  "lemma": "μήποτε",
  "linguistic_issue": ["semantics", "discourse_context"],
  "target_translation": {
    "name": "和合本",
    "rendering": "恐怕"
  },
  "professor_rendering": "或许／也许",
  "semantic_role_in_argument": "opens the possibility of hearing and turning rather than stating a divine purpose to prevent it",
  "scope": {
    "kind": "this_passage_and_parallel_reading",
    "bible_refs": ["Mark.4.12", "Matt.13.15"]
  },
  "reason_claim_ids": ["CL-...", "CL-..."],
  "affected_claim_ids": ["CL-..."],
  "citation_ids": ["CIT-..."],
  "representation_status": "approved",
  "fact_check": {
    "status": "pending",
    "conclusion": null,
    "reviewed_by": null,
    "reviewed_at": null,
    "evidence": []
  },
  "visibility": "public"
}
```

`representation_status` answers whether the record faithfully represents Dr. Wang. `fact_check.status` independently answers whether later language review is pending, confirmed, qualified, disputed, or unresolved. Implementations must never derive one from the other.

`semantic_role_in_argument` and `scope` prevent lexical search from treating every occurrence of the same word as the same judgment. Cross-sermon merging requires compatible passage scope, semantic function, and argument path, not merely a shared lemma or Chinese keyword.

### 5.12 ApplicationReasoning

```json
{
  "application_id": "APP-01K...",
  "source_context": "Acts 15 instructions to Gentile believers in Antioch, Syria, and Cilicia",
  "principle_claim_id": "CL-do-not-cause-stumbling...",
  "target_context": "Christian food practice in a different cultural setting",
  "application_claim_id": "CL-contextual-application...",
  "audience": "believers_in_cross_cultural_fellowship",
  "ecclesial_context": "congregational_fellowship",
  "actor_roles": ["believer", "fellow_member"],
  "governance_goals": ["edification", "protection_from_stumbling"],
  "normative_level": "contextual_application_of_stable_principle",
  "applicability_conditions": ["the practice is morally neutral", "another believer may be harmed"],
  "qualification_claim_ids": [],
  "pastoral_risks": ["turning contextual restraint into a universal food law"],
  "citation_ids": ["CIT-..."],
  "review_status": "approved"
}
```

`normative_level` initially supports `direct_command`, `stable_principle`, `contextual_application_of_stable_principle`, `pastoral_counsel`, and `illustration`. The record must not silently promote a local pastoral application into a universal command.

For church-practice material, `ecclesial_context`, `actor_roles`, and
`governance_goals` distinguish theology of the church from a generic personal
application. Initial actor roles include `congregation`, `leader`, `teacher`,
`minister`, `disciplined_member`, `vulnerable_member`, and `external_audience`;
the vocabulary remains versioned and extensible.

### 5.13 EvidenceStep

```json
{
  "schema_version": 1,
  "evidence_step_id": "ES-01K...",
  "claim_group_ids": ["CG-METHOD-CONTEXT-LINK", "CG-MATTHEW-TRANSFIGURATION"],
  "step_type": "textual_observation",
  "observation": "The passage uses a definite expression before 'Son of Man'.",
  "evidence_refs": [
    {"kind": "scripture_evidence", "id": "SE-..."},
    {"kind": "original_language_judgment", "id": "OLJ-..."}
  ],
  "produced_claim_ids": ["CL-intermediate..."],
  "citation_ids": ["CIT-..."],
  "attribution": "professor_explicit",
  "speaker": "professor",
  "stance": "endorsed",
  "discourse_role": "own_reasoning",
  "anchor_quality": "verified_candidate",
  "support_eligibility": "eligible",
  "local_source_evidence_ids": ["E033"],
  "canonical_evidence_step_ids": ["L3-E033"],
  "review_status": "approved",
  "visibility": "public",
  "revision": 1
}
```

`step_type` values initially include `textual_observation`,
`contextual_observation`, `genre_observation`, `historical_observation`,
`lexical_observation`, `grammatical_observation`, `structural_observation`,
`translation_check`, `comparison`, `logical_exclusion`, `experiential_test`,
and `counterexample`. `produced_claim_ids` is the explicit `used_for` link to
the conclusions served by the step. An EvidenceStep records what Dr. Wang
treats as evidence; it does not by itself assert that the evidence has been
independently fact-checked.

`claim_group_ids` is many-to-many: one EvidenceStep may support more than one
Claim without being duplicated. Export must fail when a referenced local ID
cannot be resolved to a canonical ID, or when a Claim group has no EvidenceStep
membership. The source candidate uses explicit `local_source_evidence_ids`;
repository-facing packages and joins use canonical IDs.

`support_eligibility` is `eligible`, `eligible_with_label`,
`contextual_only`, or a `withheld_*` state. Approval is enforced server-side:
zero eligible steps returns a conflict response, while one eligible step adds a
thin-evidence warning but remains an editorial decision.

### 5.14 InferenceBridge

```json
{
  "schema_version": 1,
  "inference_bridge_id": "IB-01K...",
  "input_refs": [
    {"kind": "evidence_step", "id": "ES-..."},
    {"kind": "claim", "id": "CL-premise..."}
  ],
  "output_claim_id": "CL-conclusion...",
  "reasoning": "The definite title is read in light of Daniel 7, so the speaker identifies the title with the divine heavenly figure rather than generic humanity.",
  "attribution": "professor_reasoning",
  "citation_ids": ["CIT-..."],
  "confidence": "high",
  "review_status": "approved",
  "visibility": "public",
  "revision": 1
}
```

`attribution` is `professor_explicit`, `professor_reasoning`, or `editorial_inference`. Public prose may use an approved editorial bridge, but attribution must remain visible in the evidence inspector and must never be converted into a professor-explicit claim.

### 5.15 PassageInterpretationChain

```json
{
  "schema_version": 1,
  "passage_chain_id": "PIC-Matt-17-1-8",
  "primary_bible_refs": ["Matt.17.1-Matt.17.8"],
  "parallel_bible_refs": ["Mark.9.2-Mark.9.8", "Luke.9.28-Luke.9.36"],
  "question_ids": ["Q-..."],
  "ordered_nodes": [
    {"kind": "evidence_step", "id": "ES-...", "order": 10},
    {"kind": "inference_bridge", "id": "IB-...", "order": 20},
    {"kind": "claim", "id": "CL-...", "order": 30}
  ],
  "cross_sermon_relation_ids": ["CR-..."],
  "coverage_gaps": [
    {"question_id": "Q-...", "status": "unanswered"}
  ],
  "review_status": "candidate",
  "revision": 1
}
```

The chain is a knowledge projection over multiple sources, not a manuscript outline. A CompositionPlan may select a reviewed subchain and still make independent editorial decisions about depth, order, and cross-links.

### 5.16 ExternalEvidence

```json
{
  "schema_version": 1,
  "external_evidence_id": "EE-01K...",
  "evidence_type": "historical_source_claim",
  "statement": "Dr. Wang identifies a particular historical practice as background for the passage.",
  "professor_cited_source": "source as named in the sermon, if any",
  "supports_claim_ids": ["CL-..."],
  "opposes_claim_ids": [],
  "qualification_claim_ids": [],
  "citation_ids": ["CIT-..."],
  "uncertainty": "source_not_yet_verified",
  "representation_status": "approved",
  "fact_check": {
    "status": "pending",
    "conclusion": null,
    "evidence": []
  },
  "visibility": "internal"
}
```

`evidence_type` initially includes `historical_source_claim`, `cultural_background`, `church_tradition`, `scholarly_position`, `medical_or_psychological_claim`, `probability_argument`, and `personal_experience`. Representation review and independent fact checking are orthogonal, as with OriginalLanguageJudgment.

### 5.17 ThoughtMapRevision

```json
{
  "revision_id": "TMR-01K...",
  "thought_map_id": "TM-WANG",
  "operation": "split",
  "before_node_ids": ["TM-old..."],
  "after_node_ids": ["TM-new-a...", "TM-new-b..."],
  "evidence_claim_ids": ["CL-..."],
  "reason": "New sermons show independent definitions and argument fan-out.",
  "previous_revision_id": "TMR-01J...",
  "review_status": "approved",
  "reviewed_by": "editor-id",
  "reviewed_at": "2026-08-07T00:00:00Z"
}
```

Allowed operations are `add`, `extend`, `promote`, `demote`, `split`, `merge`, `mark_tension`, and `supersede`. Activation is append-only: prior records remain addressable for audit and rollback.

### 5.18 AnswerEvidenceBundle

```json
{
  "bundle_id": "AEB-01K...",
  "question": "王教授怎样解释小信？",
  "intent": "topic_explanation",
  "claim_ids": ["CL-..."],
  "traversed_relation_ids": ["CR-..."],
  "citation_ids": ["CIT-..."],
  "unit_ids": ["CU-..."],
  "attribution_labels": ["professor_explicit", "professor_reasoning"],
  "unresolved_items": [],
  "access_scope": "public",
  "knowledge_build_id": "KB-..."
}
```

The bundle is generated deterministically from retrieval and graph traversal before prose generation. It is logged for reproducibility but does not become a permanent theological claim.

### 5.19 PublicationProfile

```json
{
  "schema_version": 1,
  "profile_id": "PP-passage-centered-academic",
  "name": "以经文为中心的学术释经体例",
  "aliases": ["Carson-style structure"],
  "product_types": ["passage_lecture"],
  "rules": [
    {
      "rule_id": "PP-R01",
      "category": "organization",
      "requirement": "按经文和论证顺序组织，不复制课堂顺序",
      "priority": "required"
    },
    {
      "rule_id": "PP-R02",
      "category": "topic_depth",
      "requirement": "神学主题只展开到解释当前经文所需的深度，较完整论述链接主题专论",
      "priority": "required"
    },
    {
      "rule_id": "PP-R03",
      "category": "coverage",
      "requirement": "教授没有讲解的经文显示资料缺口，不假托补写",
      "priority": "required"
    }
  ],
  "default_sections": ["釋經", "神學意義", "生活應用", "附錄"],
  "tone": "平和、清晰、保留主张强度",
  "citation_policy": "substantive claims resolve to approved sources",
  "review_status": "approved",
  "revision": 2,
  "approved_by": "editor-id",
  "approved_at": "2026-08-07T00:00:00Z"
}
```

Section names are stored in Traditional characters, matching `CONTENT_CATEGORIES` in
`backend/pipeline/seed_catalog/generator.py`. This document previously spelled them in Simplified
while the functional specification spelled them in Traditional, so the two normative documents
disagreed on the bytes of a config value; `backend/pipeline/transcript_pipeline.py` still carries a
Traditional-to-Simplified alias table absorbing that mismatch at runtime.

The profile stores explicit editorial rules, not imitation instructions for another author's distinctive prose. A published work snapshots the profile revision it used. Updating a profile never silently changes earlier works.

Micro-sermons use the same record type with a separate approved profile such as `PP-micro-sermon-3-5min` and `product_types: ["micro_sermon"]`. That profile requires one central question, a target duration, a minimum complete claim chain, explicit source mode (`source_excerpt` or `editorial_synthesis`), deeper-reading links, and a prohibition on removing material qualifications merely to meet duration.

### 5.20 CompositionPlan

```json
{
  "schema_version": 1,
  "plan_id": "CP-matthew-17",
  "product_type": "passage_lecture",
  "publication_profile_id": "PP-passage-centered-academic",
  "publication_profile_revision": 2,
  "title": "马太福音第17章释经讲座",
  "scope": {
    "primary_bible_refs": ["Matt.16.28-Matt.17.27"],
    "topic_ids": []
  },
  "brief": {
    "requested_by": "user-id",
    "audience": "church_readers",
    "purpose": "continuous_exposition",
    "target_length": "long_form",
    "special_requirements": ["Carson-style passage-centered structure"]
  },
  "central_question": "登山变像如何显明人子的荣耀，并连接受苦与信心？",
  "thesis": "...",
  "section_ids": ["CPS-01", "CPS-02", "CPS-03"],
  "selected_question_ids": ["Q-..."],
  "selected_claim_ids": ["CL-..."],
  "selected_evidence_step_ids": ["ES-..."],
  "selected_inference_bridge_ids": ["IB-..."],
  "selected_passage_chain_ids": ["PIC-Matt-17-1-8"],
  "selected_external_evidence_ids": [],
  "selected_judgment_ids": ["OLJ-..."],
  "selected_application_ids": ["APP-..."],
  "decision_ids": ["CD-..."],
  "coverage": [
    {
      "osis": "Matt.17.22-Matt.17.27",
      "status": "gap",
      "reason": "No approved Dr. Wang source has been located."
    }
  ],
  "review_status": "approved",
  "revision": 4,
  "reviewed_by": "editor-id",
  "reviewed_at": "2026-08-07T00:00:00Z"
}
```

The plan is a first-class authored-work record. `brief` preserves the user's requirements separately from AI proposals and editor decisions. The plan may select only records visible to its editorial scope; publication later applies the stricter public gate.

### 5.21 CompositionDecision

```json
{
  "schema_version": 1,
  "decision_id": "CD-matt17-human-son-brief",
  "plan_id": "CP-matthew-17",
  "decision_type": "include_briefly",
  "target": {
    "claim_ids": ["CL-son-of-man-..."],
    "unit_ids": ["CU-son-of-man-topic"],
    "bible_refs": ["Matt.16.28-Matt.17.8"]
  },
  "decision": "正文只说明理解本段所需的人子背景，完整论述链接人子主题专论。",
  "reason": "保持当前经文为主线，避免跨经文主题压过登山变像。",
  "governing_input": {
    "kind": "publication_profile_rule",
    "reference": "PP-R02"
  },
  "claim_hierarchy": {
    "paragraph_thesis": "CL-transfiguration-summary",
    "supporting_claims": ["CL-moses-elijah", "CL-cloud-presence"],
    "theological_ground": ["CL-psalm-2-enthronement"],
    "note": "Related title claims remain subordinate to the passage thesis."
  },
  "proposed_by": "ai",
  "review_status": "approved",
  "revision": 2,
  "reviewed_by": "editor-id",
  "reviewed_at": "2026-08-07T00:00:00Z"
}
```

Allowed `decision_type` values initially include `include_as_core`, `include_briefly`, `move_to_topic_article`, `move_to_appendix`, `link_related_unit`, `omit_as_repetition`, `defer_due_to_missing_evidence`, `identify_as_climax`, and `set_order`.

`governing_input.kind` is `user_requirement`, `publication_profile_rule`, `editor_judgment`, or `evidence_constraint`. An AI proposal cannot become approved merely because generation completed.

### 5.22 DeliverableReviewScope

```json
{
  "schema_version": 1,
  "review_scope_id": "DRS-matthew-17-v1",
  "deliverable": {
    "kind": "composition_plan",
    "id": "CP-matthew-17",
    "revision": 4
  },
  "target_release": "matthew-17-pilot",
  "dependency_closure": {
    "claim_ids": ["CL-..."],
    "relation_ids": ["CR-..."],
    "evidence_step_ids": ["ES-..."],
    "inference_bridge_ids": ["IB-..."],
    "passage_chain_ids": ["PIC-Matt-17-1-8"],
    "external_evidence_ids": ["EE-..."],
    "citation_ids": ["CIT-..."],
    "judgment_ids": ["OLJ-..."],
    "application_ids": ["APP-..."],
    "composition_decision_ids": ["CD-..."]
  },
  "required_maturity": {
    "claims": "representation_reviewed",
    "citations": "publication_approved",
    "material_relations": "publication_approved",
    "composition": "publication_approved"
  },
  "blocking_work_item_ids": ["RWI-..."],
  "deferred_record_ids": ["CL-unrelated-..."],
  "coverage_gap_ids": ["GAP-Matt.17.22-Matt.17.27"],
  "status": "in_review",
  "revision": 1,
  "created_by": "editor-id",
  "created_at": "2026-08-07T00:00:00Z"
}
```

The closure is deterministic for the frozen deliverable revision. An editor may remove optional material by revising the deliverable, but cannot remove a supporting or qualifying dependency while retaining a conclusion whose validity or faithful representation requires it.

Maturity values are `candidate`, `source_anchored`, `representation_reviewed`, and `publication_approved`. Publication approval is scoped to the named deliverable and revision rather than treated as proof that a record is sufficient for every future use.

### 5.23 ReviewWorkItem

```json
{
  "schema_version": 1,
  "work_item_id": "RWI-01K...",
  "review_scope_id": "DRS-matthew-17-v1",
  "record_type": "claim_relation",
  "record_id": "CR-...",
  "required_role": "argument_editor",
  "current_maturity": "source_anchored",
  "target_maturity": "publication_approved",
  "priority": "blocking_high",
  "risk": "material_reasoning_bridge",
  "estimated_minutes": 6,
  "actual_minutes": 8,
  "outcome": "changed_then_approved",
  "rework_count": 1,
  "assigned_to": "editor-id",
  "status": "completed",
  "started_at": "2026-08-07T00:00:00Z",
  "completed_at": "2026-08-07T00:08:00Z"
}
```

Allowed outcomes include `approved_unchanged`, `changed_then_approved`, `rejected`, `deferred`, and `blocked`. Capacity reports aggregate time and outcomes; they do not reinterpret theological content.
