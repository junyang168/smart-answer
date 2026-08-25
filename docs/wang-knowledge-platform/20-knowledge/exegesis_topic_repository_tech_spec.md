# Technical Specification: Exegesis and Topic Repository

> This specification implements the goals described in the [Project Mission Statement](../00-overview/project_mission_statement.md). The Mission Statement is authoritative for why the repository exists and how original teaching, claims, arguments, recurring thought, exegesis, and topic articles relate.
>
> The cross-product claim graph, original-language model, QA contract, permissions, and evolution policy are defined in [王守仁教授释经与思想知识平台设计](../00-overview/knowledge_platform_design.md).

## 1. Architecture Overview

The Exegesis and Topic Repository is a new read and editorial layer over the existing Notes to Sermon and Transcript to Manuscript workflows. It does not replace Series, Lecture, Project, `final.md`, Evidence Inventory, theological review, or sermon transcript storage.

The implemented repository currently separates four responsibilities:

1. **Production artifacts**: existing Project files used to create and review manuscripts.
2. **Repository authoring records**: reviewed canonical units, source documents, citations, and relationships.
3. **Compiled repository indexes**: atomically generated Bible, topic, lookup, and search projections.
4. **Source readers**: public or authenticated pages that resolve a citation and highlight original content.

The target knowledge platform adds two responsibilities without replacing the four above:

5. **Knowledge authoring records**: reviewed questions, claims, evidence steps, inference bridges, claim relations, Scripture and external evidence, original-language judgments, applications, passage interpretation chains, and revisions.
6. **Knowledge services**: permission-aware hybrid retrieval, bounded graph traversal, answer evidence bundles, and product projections for manuscripts, QA, search, comparison, and study tools.

```mermaid
flowchart TD
    P["Project final.md"] --> U["Canonical Unit Builder"]
    E["Evidence Inventory and Plan"] --> U
    T["Published Transcript JSON"] --> SM["Transcript Source Map"]
    N["Notes Pages and OCR"] --> NM["Notes Source Map"]
    SM --> C["Citation Builder"]
    NM --> C
    U --> R["Repository Authoring Store"]
    C --> R
    R --> I["Atomic Index Compiler"]
    I --> API["Repository API"]
    API --> UI["Bible, Topic, Unit, Relationship UI"]
    API --> SR["Sermon and Notes Source Readers"]
```

Target extension:

```mermaid
flowchart TD
    SD["Source Documents and Citations"] --> CG["Question, Claim, and Relation Store"]
    CG --> EX["Passage Projection"]
    CG --> TP["Topic Projection"]
    CG --> OL["Original-Language Projection"]
    CG --> EV["Revision and Development Projection"]
    EX --> CU["Canonical Units"]
    TP --> CU
    CG --> HR["Hybrid Retriever"]
    HR --> GT["Permission-Aware Graph Traversal"]
    GT --> EB["Answer Evidence Bundle"]
    EB --> QA["Evidence-Backed QA"]
```

## 2. Existing Components to Reuse

### Manuscript production

* `data/notes_to_surmon/{project_id}/final.md`
* `data/transcripts_to_manuscript/{project_id}/final.md`
* Project `meta.json`
* `evidence_inventory.json`
* `manuscript_plan.json`
* draft/final chunk metadata
* cross-lecture continuity proposals and integration applications

### Topic and Bible seed catalog

The existing `backend/pipeline/seed_catalog` output is the migration seed, not the final public store:

* `canonical_units.json`
* `bible_index.json`
* `topic_taxonomy.json`
* `topic_aliases.json`
* `duplicate_candidates.json`
* `review_needed.json`

Seed records remain `candidate_requires_review` until approved.

### Transcript source metadata

Published transcript JSON already contains:

* paragraph `index`;
* `start_index` and `end_index`;
* `start_time` and `end_time`;
* `start_timeline`; and
* paragraph text and type.

### Notes source metadata

Notes Projects already contain:

* ordered source pages in `meta.json.pages`;
* page markers in `unified_source.md` using `<!-- Page: ... -->`;
* per-page OCR Markdown under the shared raw OCR store; and
* source images served by the notes image endpoint.

### Public navigation and search

The existing `TopicNavigator`, topic index, `ScriptureMarkdown`, and sermon search are reused or adapted. The existing topic index currently links to manuscript heading anchors; repository citations add original-source links without removing manuscript links.

## 3. Storage Layout

The repository uses filesystem JSON as the reviewable source of truth and compiles an atomic read model for public requests.

```text
DATA_BASE_DIR/
  canonical_repository/
    repository_manifest.json
    units/
      {unit_id}.json
    sources/
      {source_id}.json
    source_maps/
      {source_id}.json
    citations/
      {citation_id}.json
    relationships/
      relationships.json
    questions/
      {question_id}.json
    claims/
      {claim_id}.json
    claim_relations/
      relations.json
    scripture_evidence/
      {scripture_evidence_id}.json
    original_language/
      {judgment_id}.json
    applications/
      {application_id}.json
    evidence_steps/
      {evidence_step_id}.json
    inference_bridges/
      {inference_bridge_id}.json
    passage_chains/
      {passage_chain_id}.json
    external_evidence/
      {external_evidence_id}.json
    publication_profiles/
      {profile_id}.json
    composition_plans/
      {plan_id}.json
    composition_decisions/
      {decision_id}.json
    review_scopes/
      {review_scope_id}.json
    review_work_items/
      {work_item_id}.json
    thought_maps/
      {thought_map_id}.json
    revisions/
      {revision_id}.json
    reviews/
      unit_reviews.json
      citation_reviews.json
      claim_reviews.json
      relation_reviews.json
      original_language_reviews.json
      publication_profile_reviews.json
      composition_plan_reviews.json
      composition_decision_reviews.json
      capacity_events.jsonl
    builds/
      {build_id}/
        repository.sqlite3
        bible_index.json
        topic_index.json
        original_language_index.json
        thought_map.json
        build_manifest.json
    active.json
```

`active.json` points to the last fully validated build. A compiler writes to a new build directory, validates it, and atomically replaces `active.json`. Failed builds remain diagnostic artifacts and never become public.

The initial implementation may serve small index responses directly from compiled JSON. Point lookups and filtered queries use `repository.sqlite3` so the design scales beyond the Matthew pilot.

## 4. Identifiers

IDs must not depend on editable titles.

### Canonical unit ID

* Existing reviewed seed IDs such as `CU-SEED-c6138b9864f3` are retained.
* New units use `CU-{stable-hash}` derived from the approved unit lineage, not the title alone.
* A title change does not change the ID.

### Source document ID

`SD-{stable-hash(source_type, origin_id)}`

Examples of `origin_id`:

* sermon transcript filename without `.json`;
* notes Project ID plus source page collection identity.

### Citation ID

`CIT-{random-or-content-independent-id}`

Citation IDs remain stable when an editor adjusts a locator. The citation record stores revision and source hashes.

Argument-layer `SourceFragment` and `EvidenceStep` do not define a second citation identity. A fragment stores `citation_id`, source/paragraph/excerpt hashes and an `anchor_state`; an evidence step stores `citation_ids`. Eligibility and approval require those IDs to resolve through the canonical citation service.

### Topic ID

`TopicNode.topic_id` is the only authoritative thematic identity. `CanonicalUnit.topic_assignments` and `KnowledgeRoute.canonical_topic_ids` are foreign keys to it. Legacy `topic_###` search IDs and analysis-time `TOPIC-*` route targets are retained only in reconciliation metadata or aliases; they are not valid substitutes for a `TopicNode` ID.

### Relationship ID

`REL-{stable-hash(from_unit_id, to_unit_id, relationship_type)}`

### Question, claim, and claim-relation IDs

* `Q-{content-independent-stable-id}` identifies a question across wording edits.
* `CL-{content-independent-stable-id}` identifies a repository-wide claim. Per-Project IDs such as `C001` or `E003` remain source-local lineage and never become global identity.
* `CR-{stable-hash(from_claim_id, to_claim_id, relation_type, relation_lineage)}` identifies a reviewed claim relation.

### Original-language and revision IDs

* `OLJ-{content-independent-stable-id}` identifies one original-language or translation judgment.
* `ES-{content-independent-stable-id}` identifies one evidence observation across wording edits.
* `IB-{content-independent-stable-id}` identifies one attributed inference bridge.
* `PIC-{passage-lineage-id}` identifies one passage interpretation chain across revisions.
* `EE-{content-independent-stable-id}` identifies one external-evidence assertion.
* `TMR-{monotonic-or-content-independent-id}` identifies a thought-map revision.

### Publication and composition IDs

* `PP-{content-independent-stable-id}` identifies a reusable Publication Profile across wording and rule revisions.
* `CP-{content-independent-stable-id}` identifies the editorial plan for one authored work.
* `CD-{content-independent-stable-id}` identifies one material composition decision within a plan.
* `DRS-{content-independent-stable-id}` identifies one versioned deliverable review scope.
* `RWI-{content-independent-stable-id}` identifies one review work item across assignment changes.

Split and merge operations create new IDs and preserve redirects/lineage from earlier records. A title, Chinese wording, topic path, or model-generated summary must never be the sole identity input.

## 5. Data Models

### 5.1. CanonicalUnit

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

### 5.2. SourceDocument

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

### 5.3. SourceMap

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

### 5.4. Citation

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

### 5.5. UnitCitationLink

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

### 5.6. UnitRelationship

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

### 5.7. QuestionRecord

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

### 5.8. ClaimRecord

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

### 5.9. ClaimRelation

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

### 5.10. ScriptureEvidence

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

### 5.11. OriginalLanguageJudgment

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

### 5.12. ApplicationReasoning

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

### 5.13. EvidenceStep

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

### 5.14. InferenceBridge

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

### 5.15. PassageInterpretationChain

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

### 5.16. ExternalEvidence

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

### 5.17. ThoughtMapRevision

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

### 5.18. AnswerEvidenceBundle

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

### 5.19. PublicationProfile

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
  "default_sections": ["释经", "神学意义", "生活应用", "附录"],
  "tone": "平和、清晰、保留主张强度",
  "citation_policy": "substantive claims resolve to approved sources",
  "review_status": "approved",
  "revision": 2,
  "approved_by": "editor-id",
  "approved_at": "2026-08-07T00:00:00Z"
}
```

The profile stores explicit editorial rules, not imitation instructions for another author's distinctive prose. A published work snapshots the profile revision it used. Updating a profile never silently changes earlier works.

Micro-sermons use the same record type with a separate approved profile such as `PP-micro-sermon-3-5min` and `product_types: ["micro_sermon"]`. That profile requires one central question, a target duration, a minimum complete claim chain, explicit source mode (`source_excerpt` or `editorial_synthesis`), deeper-reading links, and a prohibition on removing material qualifications merely to meet duration.

### 5.20. CompositionPlan

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

### 5.21. CompositionDecision

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

### 5.22. DeliverableReviewScope

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

### 5.23. ReviewWorkItem

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

## 6. Evidence and Citation Pipeline

### 6.0. Extraction generation fingerprint

AI extraction cache identity is not the source checksum alone. Two hashes are stored. `generation_fingerprint_sha256` identifies the shared extraction generation:

```json
{
  "prompt_sha256": "...",
  "model_id": "gpt-5.6-terra",
  "reasoning_effort": "medium",
  "max_output_tokens": 6000,
  "schema_version": "wang_corpus_first_pass_content_v1",
  "response_schema_sha256": "..."
}
```

The per-transcript `fingerprint_sha256` is SHA256 over the same deterministic identity plus the source checksum:

```json
{
  "source_sha256": "...",
  "prompt_sha256": "...",
  "model_id": "gpt-5.6-terra",
  "reasoning_effort": "medium",
  "max_output_tokens": 6000,
  "schema_version": "wang_corpus_first_pass_content_v1",
  "response_schema_sha256": "...",
  "generation_fingerprint_sha256": "..."
}
```

The survey stores both fingerprints and `generated_at`; every candidate claim stores the per-transcript fingerprint. Resume/skip requires exact per-transcript fingerprint equality. Prompt, model, schema, reasoning setting, token budget, or source changes therefore force re-extraction. Before replacement, the previous canonical survey is copied to `$DATA_BASE_DIR/wang-knowledge-platform/staging/corpus-survey/generations/` under its old fingerprint.

This describes the behavior of the reusable extraction runner for a new, explicitly scoped survey output. It does not authorize mutation of `CORPUS-SURVEY-205-V1`, which is a one-time closed historical survey. Later sermons and later transcript revisions must enter the normal PostgreSQL knowledge-authoring or ResearchBatch workflow; they must not be appended to, or used to regenerate, the 205-card V1 corpus.

Corpus synthesis requires one and only one `generation_fingerprint_sha256` across all selected surveys. It fails closed on legacy or mixed-generation input. Its own batch/final cache identities include source cards, source extraction generation, synthesis prompt, model, reasoning setting, token budget, and response schema.

### 6.1. New evidence schema requirement

The Evidence Inventory schema is extended with an exact source anchor:

```json
{
  "verbatim_source_excerpt": "an exact substring copied from the source",
  "source_ranges": [{"start_line": 3, "end_line": 3}]
}
```

The excerpt is not necessarily displayed as a quotation in the manuscript. It exists to identify the source fragment. Validation rejects an excerpt that is not an exact substring of the declared source range.

For existing Evidence Inventories without this field, migration initially uses the complete mapped paragraph or OCR range and may run an assisted exact-substring proposal. Every assisted proposal remains `candidate` until reviewed.

### 6.2. Transcript source-map generation

When a transcript is imported:

1. resolve the preferred source stage (`published`, `reviewed`, then `raw`);
2. retain every non-comment transcript paragraph and its original metadata;
3. create `unified_source.md` as today;
4. create a deterministic mapping from Unified Input line numbers to transcript paragraph keys;
5. record source and Unified Input hashes; and
6. save the source map beside repository source metadata.

Existing transcript Projects are migrated by aligning normalized Unified Input paragraphs with normalized transcript paragraphs in order. Ambiguous or missing matches are reported and never silently guessed.

### 6.3. Notes source-map generation

When notes pages are assembled:

1. parse each `<!-- Page: ... -->` marker;
2. map subsequent Unified Input lines to that page until the next marker;
3. align the page block with its raw OCR Markdown;
4. store page and OCR checksums; and
5. report edited text that can no longer be mapped exactly.

### 6.4. Citation building

For each canonical unit:

1. load the unit's evidence IDs;
2. collect their source ranges and exact source excerpts;
3. resolve ranges through the source map;
4. merge adjacent fragments only when they belong to the same source paragraph or page and support the same claim;
5. create candidate citations;
6. validate exact text and timing/page identity; and
7. require editor approval before public publication.

Shared-knowledge imports run the same resolver before records are written. The importer resolves logical transcript sources to `SourceDocument`, verifies source-map paragraph SHA256, requires the proposed excerpt to be an exact substring, creates or reuses a Canonical Citation, and attaches it to both fragment and evidence step. Non-verbatim or missing anchors are downgraded to `withheld_*`; they are not discarded, but cannot approve a claim.

The builder rejects an excerpt when every non-empty line is only a Markdown heading (`#` through `######`). A range containing a heading and substantive prose remains valid. This prevents navigation headings from producing source cards with no transcript content or meaningful media timestamp.

Existing heading-only links are repaired non-destructively: `detach_heading_only_citations()` removes their IDs from affected canonical units but preserves the citation JSON records for audit and recovery. The maintenance result reports affected units, removed links, and any unit left without a substantive source.

### 6.5. Cross-lecture lineage

Continuity decisions, Series Draft operations, and Integration Application patches must copy:

* source document ID;
* evidence IDs;
* source ranges;
* exact source excerpts; and
* any existing citation IDs.

An operation that updates manuscript prose but loses source lineage fails validation.

## 7. Repository Compiler

The compiler consumes reviewed authoring records and produces:

* canonical unit rows;
* Bible reference rows;
* topic assignment rows;
* unit relationship rows;
* source document rows;
* citation rows;
* unit-citation link rows;
* Bible index JSON;
* topic index JSON; and
* approved question and claim rows;
* claim relation, EvidenceStep, InferenceBridge, Scripture-evidence, external-evidence, and passage-chain rows;
* original-language index JSON;
* active thought-map and revision metadata;
* approved Publication Profile revisions;
* approved Composition Plans and Decisions for published works;
* frozen Deliverable Review Scopes for published works;
* a build manifest.

### Build validation

Before activation the compiler verifies:

* unique IDs;
* referenced units, sources, and citations exist;
* manuscript project and heading anchors resolve;
* source hashes match for approved citations;
* highlighted text resolves exactly;
* passage units have primary references;
* topic paths exist in the reviewed taxonomy;
* published units satisfy source requirements; and
* no public record points to a restricted source.
* every public claim has at least one approved citation, except an approved editorial synthesis whose complete component claims are cited;
* every public relation references visible approved claims and does not expose a restricted citation;
* original-language representation and fact-check states are independently valid;
* question answer status agrees with its visible approved answering claims; and
* split, merge, and supersession lineage is acyclic and resolves to active nodes.
* every published authored work references an approved immutable Publication Profile revision and approved Composition Plan revision;
* every material Composition Decision belongs to the referenced plan and has an approved or explicitly waived state;
* every selected claim, evidence step, inference bridge, passage chain, external evidence record, judgment, application, unit, and citation exists and is permitted for the plan and publication scope;
* every public inference bridge has an explicit attribution and a complete visible input/output path;
* every external-evidence record preserves representation review separately from fact-check status;
* every passage-chain node exists, follows an acyclic ordered chain, and retains cross-sermon source lineage;
* every coverage gap has an explicit disposition and cannot be silently replaced by generated content; and
* generated manuscript structure and selected knowledge do not materially diverge from the approved plan without a newer plan revision.
* the Deliverable Review Scope is computed from the exact published plan or answer-bundle revision;
* every blocking work item reaches its target maturity;
* deferred records are outside the retained material dependency closure; and
* no publication gate depends on unrelated candidate records from the same source or Project.

The build manifest includes counts for units, passage units, topic units, relationships, citations, stale citations, unresolved citations, and source documents, plus every input checksum.

## 8. SQLite Read Model

Recommended tables:

```text
repository_builds
canonical_units
unit_manuscripts
bible_references
unit_bible_references
topics
topic_aliases
unit_topics
unit_relationships
source_documents
source_fragments
citations
unit_citations
questions
claims
claim_relations
claim_scripture_evidence
original_language_judgments
application_reasoning
evidence_steps
inference_bridges
passage_interpretation_chains
passage_chain_nodes
external_evidence
publication_profiles
publication_profile_rules
composition_plans
composition_plan_sections
composition_decisions
composition_plan_claims
composition_plan_judgments
composition_plan_applications
composition_plan_evidence_steps
composition_plan_inference_bridges
composition_plan_passage_chains
composition_plan_external_evidence
deliverable_review_scopes
review_scope_dependencies
review_work_items
review_capacity_events
thought_map_nodes
thought_map_revisions
unit_claims
```

Required indexes:

* `canonical_units(status, unit_type)`;
* `unit_bible_references(book_order, chapter_start, verse_start)`;
* `unit_topics(topic_id, role)`;
* `unit_relationships(from_unit_id)` and `unit_relationships(to_unit_id)`;
* `citations(source_id, status)`;
* `unit_citations(unit_id, display_order)`; and
* `questions(answer_status, visibility, review_status)`;
* `claims(review_status, visibility, claim_type, maturity)`;
* `claim_relations(from_claim_id, relation_type)` and `claim_relations(to_claim_id, relation_type)`;
* `claim_scripture_evidence(book_order, chapter_start, verse_start, role)`;
* `original_language_judgments(osis_start, language, representation_status, fact_check_status)`;
* `evidence_steps(step_type, review_status, visibility)`;
* `inference_bridges(output_claim_id, attribution, review_status, visibility)`;
* `passage_interpretation_chains(book_order, chapter_start, verse_start, review_status)`;
* `external_evidence(evidence_type, representation_status, fact_check_status, visibility)`;
* `publication_profiles(review_status, revision)`;
* `composition_plans(product_type, review_status, publication_profile_id)`;
* `composition_decisions(plan_id, decision_type, review_status)`;
* `composition_plan_claims(plan_id, display_order, role)`;
* `deliverable_review_scopes(target_release, status, revision)`;
* `review_scope_dependencies(review_scope_id, record_type, blocking)`;
* `review_work_items(review_scope_id, required_role, status, priority)`;
* `review_capacity_events(completed_at, required_role, record_type, outcome)`;
* `unit_claims(unit_id, display_order, role)`;
* full-text search over unit title, aliases, manuscript text, arguments, and source title.

Claims and original-language judgments also receive full-text rows and optional embeddings. Embeddings are recall aids and never establish attribution, relation type, review status, or public visibility.

The repository compiler may reuse parsing and OSIS normalization from sermon search, but the authoring records remain independent of the search index so search reindexing cannot alter editorial decisions.

## 9. API Design

All browser-facing calls use the Next.js API proxy. Paths below describe backend FastAPI routes.

### 9.1. Public repository endpoints

#### `GET /canonical-repository/status`

Returns active build ID, generated time, unit counts, source counts, and availability.

#### `GET /canonical-repository/bible-index`

Returns books, chapters, passage units, empty-state metadata, and canonical ordering.

Optional filters:

* `book`;
* `chapter`;
* `series_id`; and
* `status` for authenticated editors.

#### `GET /canonical-repository/topic-index`

Returns the reviewed topic tree, aliases, and unit summaries.

#### `GET /canonical-repository/units/{unit_id}`

Returns:

* unit metadata;
* manuscript Markdown;
* Bible and topic assignments;
* approved citation summaries;
* direct relationships; and
* source-access state.

#### `GET /canonical-repository/citations/{citation_id}`

Resolves the citation against the active source version and returns:

* source metadata;
* locator;
* exact highlight;
* bounded context;
* media timing or notes page;
* deep-link URL; and
* `valid`, `stale`, `restricted`, or `unresolved` state.

The endpoint never silently substitutes a different fragment.

#### `GET /canonical-repository/units/{unit_id}/relationships`

Returns only the selected unit and direct related units/sources for local graph rendering.

#### `GET /canonical-repository/claims/{claim_id}`

Returns a public approved claim, its direct approved relations, Scripture evidence, original-language links, canonical units, and citation summaries. It never returns restricted relation endpoints.

#### `GET /canonical-repository/original-language`

Lists approved original-language judgments with filters for Bible reference, language, lemma, target translation, source, affected topic, and fact-check state.

#### `POST /canonical-repository/qa/evidence`

Builds a permission-filtered `AnswerEvidenceBundle`. Public callers receive approved and public records only. The endpoint may be internal to the answer service in the first implementation, but its input/output contract remains separately testable from prose generation.

### 9.2. Admin endpoints

#### `GET /admin/canonical-repository/units`

Returns authoring summaries, including unpublished units. Supported filters are:

* `status`;
* `unit_type`; and
* `source_origin_id`, which matches the original sermon transcript ID through citation → source lineage.

The sermon detail right rail uses `source_origin_id` to show all passage and concept units supported by the current sermon without relying on unit titles or manuscript Project names.

#### `POST /admin/canonical-repository/source-maps/rebuild`

Payload:

```json
{
  "project_ids": [],
  "source_ids": [],
  "force": false
}
```

Creates or refreshes source maps and reports exact, ambiguous, missing, and stale mappings.

#### `POST /admin/canonical-repository/units/import-candidates`

Imports candidate canonical units from a seed catalog or checked-in Project lineage. It never publishes them.

#### `PUT /admin/canonical-repository/units/{unit_id}`

Updates reviewed title, type, references, topic assignments, status, relationships, and manuscript mapping. When the submitted status is `published`, the endpoint validates every citation and refreshes the public indexes synchronously.

#### `POST /admin/canonical-repository/units/{unit_id}/citations`

Creates a candidate citation from evidence IDs and a selected source range.

#### `PATCH /admin/canonical-repository/citations/{citation_id}`

Adjusts locator, exact highlight, role, supported claim, review status, and revision.

#### `POST /admin/canonical-repository/citations/{citation_id}/remap`

Attempts deterministic remapping after a source version change. Ambiguous results remain unapproved.

#### `POST /admin/canonical-repository/build`

Runs a complete validated build and atomically activates it. The current local implementation is synchronous.

Publication currently uses `PUT /admin/canonical-repository/units/{unit_id}` with `status: "published"`. It validates the unit and citations and refreshes the public indexes without changing Project `final.md`.

#### `GET /admin/canonical-repository/claims`

Lists claims across all review states with passage, topic, source, type, maturity, attribution, visibility, relation-count, and fact-check filters.

#### `PUT /admin/canonical-repository/claims/{claim_id}`

Reviews claim wording, type, attribution, references, topics, maturity, visibility, citations, and revision. Optimistic concurrency requires the current revision.

#### `POST /admin/canonical-repository/claim-relations`

Creates a candidate relation. `repeats`, `extends`, `tension`, and `supersedes` require a written reason and cross-source evidence when used across sermons.

#### `PATCH /admin/canonical-repository/claim-relations/{relation_id}`

Reviews, changes, or rejects a relation without rewriting either endpoint claim.

#### `PUT /admin/canonical-repository/original-language/{judgment_id}`

Updates faithful-representation review. Independent fact-check fields require the language-review permission and preserve earlier conclusions as revisions.

#### `POST /admin/canonical-repository/thought-map/revisions`

Creates and previews add, extend, promote, demote, split, merge, tension, or supersede operations. Activation requires an approved revision and a successful impact build.

#### `GET /admin/canonical-repository/publication-profiles`

Lists reusable profiles and revisions. Filters include product type, review status, and active revision.

#### `POST /admin/canonical-repository/publication-profiles`

Creates a candidate profile from explicit user requirements. The API stores structured rules and provenance; a prose label such as `Carson style` is not sufficient by itself.

#### `PUT /admin/canonical-repository/publication-profiles/{profile_id}`

Creates a reviewed revision. Existing published plans continue to reference their original profile revision.

#### `POST /admin/canonical-repository/composition-plans`

Creates a plan from a profile revision, user brief, selected scope, and available knowledge records. AI-generated outlines and decisions remain candidate until reviewed.

#### `GET /admin/canonical-repository/composition-plans/{plan_id}`

Returns the plan, profile snapshot, user brief, outline, selected knowledge, coverage matrix, decisions, review history, and generated-work links.

#### `PUT /admin/canonical-repository/composition-plans/{plan_id}`

Creates a new optimistic-concurrency revision. It never overwrites the plan revision used by an existing publication.

#### `POST /admin/canonical-repository/composition-plans/{plan_id}/decisions`

Creates a candidate material decision with its governing input and reason.

#### `PATCH /admin/canonical-repository/composition-decisions/{decision_id}`

Reviews, changes, rejects, or explicitly waives one decision. Major changes invalidate plan approval and downstream manuscript generation.

#### `POST /admin/canonical-repository/composition-plans/{plan_id}/validate-manuscript`

Compares a generated or edited manuscript with the approved plan. It reports missing core sections, unplanned claims, changed order, unresolved gaps, missing cross-links, and material divergence. Findings never rewrite the manuscript automatically.

#### `POST /admin/canonical-repository/review-scopes`

Freezes a deliverable revision and computes its minimum dependency closure. The response classifies dependencies as blocking, deferrable, already satisfied, restricted, stale, or inconsistent.

#### `GET /admin/canonical-repository/review-scopes/{review_scope_id}`

Returns scope revision, dependency graph, maturity targets, work items, progress, estimated/actual review time, gaps, deferrals, and publication-gate failures.

#### `PUT /admin/canonical-repository/review-scopes/{review_scope_id}`

Creates a new scope revision after an editor changes the deliverable or explicitly removes optional material. It rejects removal of a necessary dependency unless the dependent conclusion is also removed.

#### `GET /admin/canonical-repository/review-queue`

Lists work by deliverable, role, risk, maturity, status, passage/topic, and assignee. Default ordering prioritizes blocking items for the nearest editor-approved deliverable.

#### `PATCH /admin/canonical-repository/review-work-items/{work_item_id}`

Records assignment, timing, outcome, rework, notes, and resulting maturity. Completing a work item triggers review-scope gate recomputation.

#### `GET /admin/canonical-repository/review-capacity`

Reports median and percentile review time by record type and role, acceptance/change/rejection/deferral rates, weekly available hours, completed throughput, current backlog, and projected backlog growth. Candidate generation volume is reported separately from reviewed throughput.

## 10. Frontend Design

### 10.1. Routes

Implemented routes:

```text
/resources/wang-repository
/resources/wang-repository/{unitId}
/resources/sermons/{sermonId}?citation={citationId}

/admin/canonical-repository
/admin/canonical-repository/{unitId}
```

Stable unit and citation IDs, rather than localized titles, identify repository records and deep links.

### 10.2. Reusable components

Recommended components:

* `RepositoryNavigator`
* `BibleIndexView`
* `TopicIndexView`
* `CanonicalUnitReader`
* `UnitSourcesPanel`
* `SourceCitationCard`
* `SourceCitationDrawer`
* `UnitRelationshipView`
* `TranscriptSourceReader`
* `NotesSourceReader`
* `CitationReviewEditor`
* `CitationMediaPlayer`
* `SermonRepositoryUnits`
* `ClaimReviewEditor`
* `ClaimRelationEditor`
* `QuestionAnswerChainReview`
* `OriginalLanguageJudgmentEditor`
* `EvidenceStepEditor`
* `InferenceBridgeEditor`
* `PassageInterpretationChainView`
* `ExternalEvidenceEditor`
* `ThoughtMapRevisionPreview`
* `AnswerEvidenceInspector`
* `PublicationProfileLibrary`
* `PublicationProfileEditor`
* `CompositionPlanEditor`
* `CompositionCoverageMatrix`
* `CompositionDecisionList`
* `CompositionManuscriptDiff`
* `DeliverableReviewScopeView`
* `ReviewDependencyGraph`
* `CapacityAwareReviewQueue`
* `ReviewCapacityDashboard`
* `KnowledgeRoutePanel`
* `ClaimHierarchyPanel`
* `EditorialChecksPanel`
* `InterpretiveTensionsPanel`

`SermonRepositoryUnits` is editor-only and calls the admin unit-list endpoint with the current sermon transcript ID as `source_origin_id`. It renders passage and concept lists separately and includes every review status.

`ClaimReviewEditor` receives a server-computed `review_gate`. The client disables
approval when `can_approve` is false, but the PATCH review endpoint repeats the
same check so direct requests cannot bypass the gate. `CompositionPlanEditor`
renders `claim_hierarchy`, knowledge routes, editorial checks, and tensions from
the API. A control that expands a distant review section assigns a stable DOM
target and calls `scrollIntoView`; keyboard focus behavior must remain
accessible.

### 10.3. Transcript rendering

`SermonDetailView` retains transcript segment metadata so a citation can address a stable paragraph rather than searching an undifferentiated manuscript string. A conceptual segment container is:

```html
<section id="sermon-segment-31" data-paragraph-key="31">...</section>
```

On citation load:

1. request the citation resolver;
2. find the target segment;
3. verify citation state;
4. render the exact range with `<mark>`;
5. scroll and focus the segment; and
6. seek the media player to `start_time`.

Highlighting is performed from resolved citation data, not directly from untrusted query-string offsets.

The canonical-unit source card embeds `CitationMediaPlayer` before the excerpt. The player receives the source media metadata and citation `start_time`. Source links use a new tab so an editor can keep the unit review open while inspecting the complete sermon.

### 10.4. Notes rendering

The notes source reader displays:

* the source image endpoint for the selected page;
* raw OCR Markdown rendered as text;
* exact `<mark>` highlighting in the OCR text; and
* adjacent-page navigation.

Future `image_regions` are normalized coordinates between 0 and 1 and render as accessible overlays on the source image.

### 10.5. Relationship visualization

The API returns a bounded one-hop graph for the selected unit. The frontend limits node count and groups excess sources by source document. It must not fetch or lay out the complete repository graph by default.

### 10.6. Knowledge review views

The knowledge authoring UI is deliberately separate from the public article reader. Editors need to inspect the structure that can later support several products, not only the prose rendered in one manuscript.

Required editor views are:

* **Question and answer chain**: shows a question, explicit answers, supporting and opposing claims, unanswered state, and every exact source.
* **Claim review**: edits normalized wording without overwriting the professor's quoted wording; attribution, maturity, visibility, Scripture references, topics, and citations are reviewed independently.
* **Relation review**: displays both endpoint claims and the source context before approving `supports`, `answers`, `qualifies`, `opposes`, `repeats`, `extends`, `tension`, or `supersedes`.
* **Original-language review**: shows the Hebrew/Greek form, grammatical or semantic observation, translation under criticism, the professor's proposed reading, downstream claims, and exact source. Faithful-representation review and external fact-checking have separate controls.
* **Thought-map revision preview**: shows the before/after graph and affected public units, questions, and saved answers before a split, merge, promotion, demotion, or supersession is activated.
* **Answer evidence inspection**: shows the exact claims, relations, citations, permissions, and knowledge build used to construct an answer before prose generation.
* **Publication Profile library**: translates a named editorial request into versioned, concrete rules and allows comparison between revisions.
* **Composition Plan editor**: shows the user brief, governing profile, central question, thesis, outline, selected knowledge, target depth, coverage gaps, and approval state.
* **Composition Decision review**: explains why content is core, brief, linked, moved, omitted, deferred, treated as a climax, or placed in a particular order.
* **Plan/manuscript comparison**: detects material divergence while allowing ordinary prose editing that does not change the approved structure or knowledge selection.

The UI must label `professor_explicit`, `professor_reasoning`, `opposed_view`, `editorial_synthesis`, `pending_fact_check`, and `insufficient_evidence` distinctly. Styling alone is insufficient; the label remains available to screen readers and exports.

## 11. Source Resolution and Highlighting

### Exact resolution

When `source_sha256` matches:

1. locate the recorded paragraph or notes page;
2. confirm `highlight_text` at the stored occurrence or offsets;
3. return bounded context; and
4. mark the result `valid`.

### Source changed

When the source hash differs:

1. attempt the same stable paragraph key or page file;
2. search for the exact highlight string;
3. if exactly one match exists, return a remap proposal to editors;
4. if no or multiple matches exist, mark `stale` or `unresolved`; and
5. do not change the approved citation automatically.

### Context limits

Public citation responses return the complete cited paragraph plus at most one adjacent paragraph on each side by default. The full source remains available through its normal authenticated page.

## 12. Authentication and Authorization

* Published canonical units may be public or follow the site's existing manuscript access policy.
* Public citation resolution requires an approved citation and an allowed source stage.
* Raw and reviewed-only sermon transcripts are editor-only.
* Notes images follow the same access policy as their associated Project or repository unit.
* Admin mutations require `editor` or `admin` role.
* The API rejects path traversal and never accepts filesystem paths from public requests.
* Public QA can retrieve only approved public claims, relations, canonical units, and citations in the active build.
* Internal research QA may retrieve candidate and unpublished records only for authenticated editors and must display their review state in the answer evidence bundle.
* Claim approval, relation approval, source approval, faithful-representation review, and original-language fact checking are separate permissions even when one person initially holds several roles.
* A language reviewer may update `fact_check` without silently changing the faithful-representation record; an editor may faithfully record the professor's view without declaring that view externally verified.
* Saved answer evidence bundles inherit the most restrictive visibility of their component records.
* Approving a Publication Profile, Composition Plan, or Composition Decision requires editorial-planning permission; it does not grant claim, citation, language-review, or publication permission.
* The user who supplies a brief may approve its requirements while an editor separately approves the resulting plan.
* Public readers cannot access internal rejected alternatives or private user briefs unless deliberately included in public editorial notes.
* Capacity reports expose reviewer identities only to authorized administrators; aggregate throughput may be visible to editors.

## 13. State and Invalidation Rules

### Manuscript changed

If a referenced `final.md` checksum changes:

* the unit's manuscript mapping becomes stale;
* repository publication remains on the last active build;
* editors see a rebuild/review warning; and
* no canonical unit text is overwritten automatically.

### Source changed

If a source checksum changes:

* affected citations become stale in the authoring view;
* the active build continues to serve the last valid approved snapshot until a new build is activated;
* new builds reject stale required citations; and
* remapping requires validation and, when ambiguous, editor approval.

### Taxonomy changed

Topic assignments whose IDs no longer exist become invalid. Alias changes do not change unit IDs.

### Relationship changed

Relationship edits affect only the repository authoring records and compiled navigation; they do not edit manuscript Markdown.

### Claim changed

When an approved claim's normalized wording, attribution, visibility, citations, or Scripture evidence changes:

* create a new claim revision rather than overwriting audit history;
* invalidate answer caches and compiled units that reference the prior revision;
* retain saved answer evidence bundles as historical records tied to their original knowledge build; and
* require a new validated build before public QA or publication uses the revision.

The implementation materializes this rule through two authoring records:

* `ProductDependency(dependency_id, consumer_kind, consumer_id, claim_id, pinned_claim_revision, status)` records actual use, not merely a proposed `KnowledgeRoute`;
* `ImpactEvent(impact_event_id, changed_record_id, from_revision, to_revision, affected_targets, required_actions, status)` records invalidation and its disposition.

`GET /admin/canonical-repository/knowledge/claims/{claim_id}/impact` previews reverse impact. Semantic claim updates invalidate matching dependencies and create an event. `POST /admin/canonical-repository/knowledge/impact-events/{id}/withdraw` archives affected published units and activates the resulting build atomically; failure restores their previous state. A knowledge-managed published unit must pin dependency IDs, and compilation rejects missing, invalidated, or revision-mismatched dependencies.

### Question or answer relation changed

Changing an `answers`, `qualifies`, `opposes`, or `unanswered` decision recomputes the question's answer status. A question cannot be marked answered merely because topically similar claims exist.

### Original-language judgment changed

Representation changes and fact-check changes create independent revisions. A downstream unit or answer is invalidated only when it actually cites the changed judgment or an affected claim.

### Thought map changed

Thought-map operations are previewed against the active build. Activation creates a new map revision, recompiles affected indexes, and preserves redirects or lineage for superseded nodes. It never rewrites source claims to fit the new map.

### Publication Profile changed

A profile edit creates a new revision. Existing plans and publications retain their pinned revision. Editors may explicitly clone or migrate a plan to the new profile revision and review the resulting impact.

### Composition Plan changed

A material plan change creates a new revision, marks its generated draft comparison stale, and requires renewed approval. Published manuscripts retain the previous plan snapshot until a new publication is explicitly activated.

### Composition Decision changed

Changing a decision invalidates plan approval when it affects core selection, scope, order, depth, cross-links, appendices, omissions, or coverage gaps. Rejecting an AI proposal leaves an audit record and does not delete the underlying claims.

### Deliverable review scope changed

A review scope pins one deliverable revision and its deterministic dependency closure. If the Composition Plan, Answer Evidence Bundle, selected claim revision, material relation, citation, or required maturity changes:

* create a new review-scope revision and preserve the prior snapshot;
* recompute the dependency closure and its blocking work items;
* retain completed reviews when the exact reviewed record revision and required use remain unchanged;
* reopen only newly added, changed, or newly material dependencies; and
* prevent activation until every blocker in the new scope reaches its required maturity.

Completing or rejecting a work item recomputes the scope gate. Deferring an unrelated candidate has no gate effect. Deferring a required dependency is allowed only after revising the deliverable so that no retained conclusion depends on it.

## 14. Observability

Structured build and source-map logs include:

* job ID and stage;
* source documents scanned;
* exact, ambiguous, missing, and stale source mappings;
* candidate and approved citation counts;
* unit validation failures;
* compiled row counts;
* input and output hashes; and
* activation result.

Knowledge and QA logs additionally include:

* knowledge build ID and access scope;
* parsed question intent and Scripture references;
* lexical, semantic, canonical-unit, and claim-graph candidate counts;
* selected claim, relation, citation, and unit IDs;
* permission-filtered record counts without leaking restricted content;
* unanswered or insufficient-evidence decisions;
* answer evidence bundle ID and prose-generation model/version; and
* thought-map revision impact and activation result.

Review-capacity events additionally record deliverable ID, frozen scope revision, record type, required role, estimated minutes, actual minutes, outcome, rework count, and gate effect. They must not record hidden reviewer activity outside an explicit work-item session as inferred labor time.

Logs store IDs, scores, decisions, and hashes by default. Exact restricted transcript text is not duplicated into general application logs.

Admin UI summaries must link every failure to the affected unit, citation, Project, or source.

## 15. Testing Strategy

### Unit tests

* stable ID generation;
* transcript line-to-paragraph mapping;
* notes line-to-page mapping;
* exact highlight and occurrence resolution;
* citation stale detection;
* canonical Bible sorting;
* topic alias and multi-path assignment;
* publication gate validation; and
* atomic build activation.
* claim revision and stable identity;
* relation direction and allowed endpoint validation;
* question answer-status derivation;
* public/internal visibility propagation;
* original-language representation/fact-check independence;
* thought-map split, merge, tension, and supersession lineage; and
* deterministic AnswerEvidenceBundle construction.
* Publication Profile revision pinning;
* Composition Plan optimistic concurrency and immutable publication snapshots;
* Composition Decision type, target, governing-input, and review validation;
* coverage-gap disposition; and
* material versus non-material plan/manuscript divergence classification.
* deterministic minimum dependency closure from a frozen deliverable;
* prevention of orphaned conclusions when dependencies are removed;
* deliverable-scoped maturity evaluation; and
* review-time and outcome aggregation.

### API tests

* public unit and index responses;
* citation access by source stage and role;
* stale and unresolved citation responses;
* optimistic concurrency on editorial updates;
* concurrent build rejection; and
* path traversal rejection.
* public claim graph permission filtering;
* restricted endpoints omitted from otherwise public relations;
* independent original-language review permissions;
* thought-map optimistic concurrency and impact preview; and
* QA evidence endpoint reproducibility for the same active build.
* profile revision isolation across older and newer plans;
* composition-plan permission separation;
* decision review invalidation of plan approval; and
* plan/manuscript validation findings without automatic mutation.
* review-scope revision and optimistic concurrency;
* publication success with unrelated candidates still pending;
* publication rejection when one blocking dependency is incomplete; and
* capacity report authorization and aggregation.

### Frontend tests

* Bible and topic views link to the same unit;
* citation drawer displays exact excerpt and context;
* sermon page scrolls, highlights, and seeks correctly;
* notes page opens the correct page and highlights OCR text;
* keyboard focus reaches highlighted content;
* mobile source sheet works without horizontal overflow; and
* stale citations display a warning rather than a misleading highlight.
* claim and relation editors expose both normalized claims and exact sources;
* attribution and review state remain visible without relying only on color;
* original-language review separates faithful representation from fact checking;
* thought-map revision preview lists affected units and questions; and
* answer evidence inspection matches the citations rendered in the answer.
* the profile editor exposes concrete rules rather than only a style label;
* the plan editor distinguishes user requirements, editor judgments, AI proposals, and evidence constraints;
* Matthew 17 coverage gaps remain visible and cannot be silently drafted; and
* the manuscript comparison explains material divergence from the approved plan.
* the queue groups work by deliverable and explains why each item blocks or can be deferred;
* internal candidate maturity is visible while public pages remain free of candidate leakage; and
* “not yet organized” is visually distinct from “no teaching found.”

### Migration tests

Use the three-unit pilot:

* Transfiguration passage unit;
* `小信` cross-passage topic unit; and
* dispensationalism/Scofield multi-source topic unit.

The Scofield test must confirm that one manuscript unit retains separate third- and fourth-lecture citations, each opening its own highlighted transcript and time range.

### Knowledge and QA evaluation

Use the frozen 205-sermon survey and candidate baseline v3 as the discovery and structural baseline, while the first two deliverable-scoped review sets supply the human-reviewed evaluation cases. The evaluation must include:

* an explicit claim with a direct answer;
* a question the professor raises but does not answer;
* an opposed view that must not be attributed to him;
* a conclusion inferred from his reasoning but not stated verbatim;
* a repeated and an extended claim across sermons;
* a genuine tension that must not be auto-merged;
* a Hebrew or Greek translation criticism;
* the recurring `δικαιόω` judgment in which Dr. Wang explicitly prefers “成义／成为义” over “称义”, including the linked opposed view and separate fact-check state;
* a passage question requiring verse order and context;
* a topic question requiring cross-sermon synthesis; and
* a public query whose best internal evidence is unpublished or restricted;
* a conclusion whose textual observation and inference bridge must both be visible;
* a passage interpretation distributed across multiple sermons; and
* an external historical or scientific premise whose fact-check state differs from its representation state.

For each case, reviewers score attribution, completeness, relation accuracy, source precision, qualification, and permission safety separately. Fluent prose cannot compensate for a failed evidence bundle.

The first Matthew 17 passage deliverable and the first cross-sermon “Son of Man” topic deliverable measure operational feasibility. They record actual active review minutes for source verification, representation, argument paths, composition, and optional fact checking; proposed/accepted/changed/rejected/deferred counts; rework causes; weekly editor availability; completed throughput; and projected backlog. The pilot report must state which measurements are observed and which remain estimates.

## 16. Implementation Phases

### Phase 1: Provenance foundation

* Implement repository models and filesystem store.
* Generate transcript and notes source maps.
* Extend Evidence Inventory with exact source excerpts.
* Implement citation builder and validator.

### Phase 2: Source readers

* Preserve paragraph metadata in sermon rendering.
* Add citation resolver and transcript highlighting.
* Connect media seeking.
* Add notes source reader and OCR highlighting.

### Phase 3: Repository authoring and public UI

* Import candidate seed units.
* Implement unit and citation review.
* Implement Bible, topic, unit, and local relationship pages.
* Implement validated atomic builds.

### Phase 4: Workflow integration

* Carry citation lineage through generation and cross-lecture integration.
* Connect Check In and repository refresh without coupling their writes.
* Add repository source links to sermon search results.

### Phase 5: Migration

* Complete the three-unit pilot.
* Migrate reviewed Matthew units.
* Resolve duplicate and low-confidence candidates.
* Expand incrementally to the full sermon corpus.

### Phase 6: Knowledge authoring foundation

* Freeze the mechanically validated 205-sermon survey and candidate baseline v3 with source hashes, model, prompt, synthesis lineage, and the 17-group structural review decisions.
* Import only records selected by a deliverable scope; retain the remainder as survey candidates rather than flooding the review queue.
* Import selected questions, claims, EvidenceSteps, InferenceBridges, Scripture and external evidence, original-language judgments, applications, passage chains, and relations as candidates.
* Implement claim, argument-path, question-chain, passage-chain, original-language, application, and external-evidence review views.
* Preserve project-local evidence IDs while assigning stable repository IDs.
* Publish only reviewed records into the active knowledge build.

### Phase 7: Thought-map evolution

* Seed the provisional theological and exegetical maps from reviewed claims, not manuscript titles.
* Implement add, extend, promote, demote, split, merge, tension, and supersede previews.
* Record reasons and evidence for every activated structural change.
* Keep the map extensible as new published sermons and notes are added.

### Phase 8: Knowledge-grounded QA and research

* Upgrade sermon search to hybrid manuscript, canonical-unit, and claim-graph retrieval.
* Build and permission-filter an AnswerEvidenceBundle before prose generation.
* Support passage explanation, topic synthesis, original-language, comparison, and source-location questions.
* Run blind evaluation before enabling public synthesized answers.

### Phase 9: Publication profiles and composition planning

* Implement Publication Profile, Composition Plan, and Composition Decision authoring records and review UI.
* Convert the user's passage-centered academic commentary requirements into the first approved profile.
* Migrate `matthew_17_exposition_blueprint.md` into the first versioned plan and decision set.
* Validate plan/manuscript conformance without preventing ordinary line editing.

### Phase 10: Deliverable-scoped review and capacity measurement

* Compute a minimum publishable subgraph from the Matthew 17 plan.
* Build the capacity-aware review queue and publication gate for that scope.
* Leave unrelated candidates pending without blocking the pilot.
* Measure real review time and rework across the first passage and topic deliverables.
* Set later batch sizes from observed weekly throughput and backlog growth.

### Phase 11: Additional authored works

* Generate passage-centered academic lectures, cross-sermon topic essays, and three-to-five-minute micro-sermons from selected reviewed knowledge under approved profiles and plans.
* Link the existing micro-sermon delivery record to CompositionPlan, active knowledge build, ClaimRelation/Citation dependencies, source mode, duration, and review state without making the delivery JSON a parallel knowledge store.
* Add research comparison, teaching outlines, study guides, and other projections without creating parallel knowledge stores.

## 17. Deployment and Rollback

* Repository schema version is recorded in every authoring record and build manifest.
* New builds are created beside the active build.
* Activation is a single atomic pointer update.
* Rollback changes `active.json` to the previous validated build.
* Project manuscripts, transcripts, notes images, and earlier builds are not deleted during activation or rollback.

## 18. Technical Acceptance Criteria

* Every published unit resolves to exactly one authoritative manuscript section.
* Every required approved citation resolves to exact original text at build time.
* Passage and topic units use the same citation resolver.
* Transcript citations expose valid segment identity and timing when available.
* Notes citations expose valid page identity and highlighted OCR text.
* A source hash mismatch cannot silently produce a valid response.
* A topic unit can retain citations from multiple lectures without duplicating manuscript prose.
* Cross-lecture integration preserves evidence and citation lineage.
* Repository builds are atomic and rollback-capable.
* Existing Project generation, audit, Check In, and `final.md` behavior remain unchanged unless explicitly extended by this specification.
* Every public synthesized answer can expose the exact approved claims, relations, and citations used to generate it.
* The system does not attribute an opposed view, editor synthesis, or unresolved inference to the professor as an explicit claim.
* An unanswered question remains unanswered until an approved `answers` relation exists.
* Original-language judgments retain separate faithful-representation and external fact-check states.
* Public QA cannot reveal candidate, unpublished, or restricted records through answer text, citations, counts, or graph neighbors.
* Thought-map revisions are append-only, evidence-backed, previewable, and rollback-capable.
* The same reviewed claim can support a passage lecture, topic essay, QA answer, search result, and study tool without duplicating its identity or provenance.
* Every published authored work pins an approved Publication Profile and Composition Plan revision.
* Important editorial choices have stable IDs, reasons, governing inputs, review states, and revision history.
* User requirements, editor judgments, AI proposals, and evidence constraints remain distinguishable.
* A plan can keep `Amen` and “人子” brief in Matthew 17 while linking deeper topic works, without deleting their claims or sources.
* Missing Matthew 17:22–27 evidence remains an explicit coverage gap; generation cannot fill it as Dr. Wang's exposition.
* Updating a Publication Profile does not silently alter earlier publications.
* A publication is gated by its frozen minimum dependency closure, not by the review state of the entire Project or corpus.
* A required support or qualification cannot be deferred while its dependent conclusion remains in scope.
* Unrelated Candidate records remain auditable and do not prevent a completed deliverable from publishing.
* Public readers never see Candidate or Source-anchored records as approved teaching.
* The pilot produces observed throughput, review-time, outcome, rework, capacity, and backlog metrics before wider extraction is scheduled.

## 19. Implementation Status

This section describes implemented repository functionality only. The formal shared-knowledge authoring store and package importer are now implemented, but the complete original-language review workflow, evolving thought map, Publication Profile workflow, AnswerEvidenceBundle service, compiled knowledge build, and knowledge-grounded QA described above remain target architecture unless explicitly listed below.

Phase 1 foundation is now represented in code under `backend/api/canonical_repository`:

* typed canonical unit, source, source-map, citation, relationship, and resolution records;
* atomic JSON authoring storage;
* deterministic transcript paragraph/line/time mapping and notes page/OCR mapping;
* exact-substring citation creation with stale-source detection;
* public lookup and admin rebuild/edit/build endpoints;
* atomic compiled Bible/topic JSON and SQLite read models; and
* automatic best-effort source-map refresh when a transcript is imported or notes Unified Input is rebuilt;
* an admin preview list at `/admin/canonical-repository`, with candidate-only seed import, passage/topic views, status badges, filtering, source counts, and manuscript Project links;
* a unit review page for title/type/reference/topic editing, citation approval, deterministic source-map citation creation, publish validation, and preview-before-apply merges;
* a public Bible/topic repository shell that reads only an activated build and never exposes candidates;
* a notes source reader showing the exact scanned page beside highlighted OCR, with stale-source warnings and authenticated-reader gating;
* exact `verbatim_source_excerpt` generation and validation in new Transcript Evidence Inventories, with safe full-range compatibility for legacy inventories;
* cross-lecture integration patches that retain evidence IDs, source ranges, verbatim excerpts, source document IDs, and citation IDs;
* Markdown rendering of the linked manuscript section in the admin unit review page;
* embedded audio/video players above sermon excerpts, with citation-time seeking and new-tab links to the complete sermon;
* source-title resolution from the original sermon record rather than the derived manuscript Project title;
* editor-only sermon right-rail lists of all citing passage and topic units, including unpublished statuses, using the `source_origin_id` lineage filter;
* non-destructive heading-only citation cleanup and prevention of future heading-only source cards; and
* Bible index grouping by canonical book and chapter, followed by verse-order sorting and per-unit deduplication across multiple Bible references.

The shared-knowledge authoring foundation is now represented by `knowledge_models.py`, `knowledge_importer.py`, and the `RepositoryStore.knowledge_*` methods:

* versioned atomic records for Source Fragment, Question, Observation, Claim, authoritative Topic Node, Evidence Step, evidence and claim relations, external Position, Knowledge Route, Editorial Synthesis, Composition Plan/Decision, Editorial Check, and Tension;
* full-package validation before writes, including duplicate IDs and unresolved source, evidence, claim, position, route, synthesis, and composition references;
* Canonical Citation binding before import, with exact-text/hash validation and approval gates for claims/evidence lacking a valid citation;
* explicit topic reconciliation: taxonomy nodes are authoritative, candidate aliases may migrate, analysis routes gain canonical foreign keys, and old search IDs remain projections mapped to CanonicalUnit;
* incremental package imports that may reference records already present in the canonical repository;
* idempotent imports with SHA256 package manifests and protection for existing human review fields;
* revision-guarded record updates for concurrent editorial work; and
* admin endpoints for package import, collection status, collection reads, individual reads, and revision-guarded updates.

Authoring records are stored under `canonical_repository/knowledge/<collection>/<record-id>.json`; package manifests are stored under `canonical_repository/knowledge/packages/`. These are internal authoring records. They are not included in the public active build until a later compiler and publication gate explicitly approves them.

Topic reconciliation is written to `canonical_repository/knowledge/reconciliation/topic_identity.json` and can be rerun through `POST /admin/canonical-repository/knowledge/topics/reconcile`. The report must remain empty for unknown unit topics and unresolved `topic_research` routes before a topic build is activated.

The Matthew pilot migration is implemented by `backend/pipeline/canonical_repository_pilot.py`. It attaches multi-lecture citations to the Amen, dispensationalism, and Transfiguration units, and creates a separate cross-passage `小信` concept unit related to—rather than replacing—the individual passage units.

The sermon reader accepts a citation deep link, highlights the exact original excerpt, scrolls it into view, and seeks authenticated audio/video to the citation start time. The public unit page currently hides manuscript text intentionally and presents approved original sources first; the admin unit page still renders the manuscript Markdown for editorial comparison. The remaining release work is primarily editorial: additional candidate citations must be reviewed, units moved to `published`, and the repository expanded beyond the Matthew pilot. Wider corpus migration remains incremental because notes without Evidence Inventory ranges require human source selection rather than guessed provenance.
