# Evidence Pipeline, Compiler, Read Model and Invalidation

> **读者**：Developer
> **类型**：规范
> **状态**：当前
> **与代码对齐**：未核对
> **权威范围**：How source material becomes queryable state, and what invalidates it。本文是《文库 Technical Specification》的一部分。

本规范的其余部分：

| 文件 | 内容 |
| --- | --- |
| [Technical Specification: Exegesis and Topic Repository](./README.md) | Architecture, storage layout and identifiers |
| [Data Models](./data-models.md) | Every stored record type in the repository |
| [Evidence Pipeline, Compiler, Read Model and Invalidation](./compiler.md) | How source material becomes queryable state, and what invalidates it |
| [API, Frontend, Source Resolution and Authorization](./api-and-ui.md) | The surfaces this repository exposes |
| [Observability, Testing, Phases, Deployment and Acceptance](./delivery.md) | How the work is verified and shipped |

### Contents

- [6. Evidence and Citation Pipeline](#6-evidence-and-citation-pipeline)
  - [6.1 Extraction generation fingerprint](#61-extraction-generation-fingerprint)
  - [6.2 New evidence schema requirement](#62-new-evidence-schema-requirement)
  - [6.3 Transcript source-map generation](#63-transcript-source-map-generation)
  - [6.4 Notes source-map generation](#64-notes-source-map-generation)
  - [6.5 Citation building](#65-citation-building)
  - [6.6 Cross-lecture lineage](#66-cross-lecture-lineage)
- [7. Repository Compiler](#7-repository-compiler)
- [8. SQLite Read Model](#8-sqlite-read-model)
- [13. State and Invalidation Rules](#13-state-and-invalidation-rules)
  - [13.1 Manuscript changed](#131-manuscript-changed)
  - [13.2 Source changed](#132-source-changed)
  - [13.3 Taxonomy changed](#133-taxonomy-changed)
  - [13.4 Relationship changed](#134-relationship-changed)
  - [13.5 Claim changed](#135-claim-changed)
  - [13.6 Question or answer relation changed](#136-question-or-answer-relation-changed)
  - [13.7 Original-language judgment changed](#137-original-language-judgment-changed)
  - [13.8 Thought map changed](#138-thought-map-changed)
  - [13.9 Publication Profile changed](#139-publication-profile-changed)
  - [13.10 Composition Plan changed](#1310-composition-plan-changed)
  - [13.11 Composition Decision changed](#1311-composition-decision-changed)
  - [13.12 Deliverable review scope changed](#1312-deliverable-review-scope-changed)

## 6. Evidence and Citation Pipeline

### 6.1 Extraction generation fingerprint

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

### 6.2 New evidence schema requirement

The Evidence Inventory schema is extended with an exact source anchor:

```json
{
  "verbatim_source_excerpt": "an exact substring copied from the source",
  "source_ranges": [{"start_line": 3, "end_line": 3}]
}
```

The excerpt is not necessarily displayed as a quotation in the manuscript. It exists to identify the source fragment. Validation rejects an excerpt that is not an exact substring of the declared source range.

For existing Evidence Inventories without this field, migration initially uses the complete mapped paragraph or OCR range and may run an assisted exact-substring proposal. Every assisted proposal remains `candidate` until reviewed.

### 6.3 Transcript source-map generation

When a transcript is imported:

1. resolve the preferred source stage (`published`, `reviewed`, then `raw`);
2. retain every non-comment transcript paragraph and its original metadata;
3. create `unified_source.md` as today;
4. create a deterministic mapping from Unified Input line numbers to transcript paragraph keys;
5. record source and Unified Input hashes; and
6. save the source map beside repository source metadata.

Existing transcript Projects are migrated by aligning normalized Unified Input paragraphs with normalized transcript paragraphs in order. Ambiguous or missing matches are reported and never silently guessed.

### 6.4 Notes source-map generation

When notes pages are assembled:

1. parse each `<!-- Page: ... -->` marker;
2. map subsequent Unified Input lines to that page until the next marker;
3. align the page block with its raw OCR Markdown;
4. store page and OCR checksums; and
5. report edited text that can no longer be mapped exactly.

### 6.5 Citation building

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

### 6.6 Cross-lecture lineage

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


## 13. State and Invalidation Rules

### 13.1 Manuscript changed

If a referenced `final.md` checksum changes:

* the unit's manuscript mapping becomes stale;
* repository publication remains on the last active build;
* editors see a rebuild/review warning; and
* no canonical unit text is overwritten automatically.

### 13.2 Source changed

If a source checksum changes:

* affected citations become stale in the authoring view;
* the active build continues to serve the last valid approved snapshot until a new build is activated;
* new builds reject stale required citations; and
* remapping requires validation and, when ambiguous, editor approval.

### 13.3 Taxonomy changed

Topic assignments whose IDs no longer exist become invalid. Alias changes do not change unit IDs.

### 13.4 Relationship changed

Relationship edits affect only the repository authoring records and compiled navigation; they do not edit manuscript Markdown.

### 13.5 Claim changed

When an approved claim's normalized wording, attribution, visibility, citations, or Scripture evidence changes:

* create a new claim revision rather than overwriting audit history;
* invalidate answer caches and compiled units that reference the prior revision;
* retain saved answer evidence bundles as historical records tied to their original knowledge build; and
* require a new validated build before public QA or publication uses the revision.

The implementation materializes this rule through two authoring records:

* `ProductDependency(dependency_id, consumer_kind, consumer_id, claim_id, pinned_claim_revision, status)` records actual use, not merely a proposed `KnowledgeRoute`;
* `ImpactEvent(impact_event_id, changed_record_id, from_revision, to_revision, affected_targets, required_actions, status)` records invalidation and its disposition.

`GET /admin/canonical-repository/knowledge/claims/{claim_id}/impact` previews reverse impact. Semantic claim updates invalidate matching dependencies and create an event. `POST /admin/canonical-repository/knowledge/impact-events/{id}/withdraw` archives affected published units and activates the resulting build atomically; failure restores their previous state. A knowledge-managed published unit must pin dependency IDs, and compilation rejects missing, invalidated, or revision-mismatched dependencies.

### 13.6 Question or answer relation changed

Changing an `answers`, `qualifies`, `opposes`, or `unanswered` decision recomputes the question's answer status. A question cannot be marked answered merely because topically similar claims exist.

### 13.7 Original-language judgment changed

Representation changes and fact-check changes create independent revisions. A downstream unit or answer is invalidated only when it actually cites the changed judgment or an affected claim.

### 13.8 Thought map changed

Thought-map operations are previewed against the active build. Activation creates a new map revision, recompiles affected indexes, and preserves redirects or lineage for superseded nodes. It never rewrites source claims to fit the new map.

### 13.9 Publication Profile changed

A profile edit creates a new revision. Existing plans and publications retain their pinned revision. Editors may explicitly clone or migrate a plan to the new profile revision and review the resulting impact.

### 13.10 Composition Plan changed

A material plan change creates a new revision, marks its generated draft comparison stale, and requires renewed approval. Published manuscripts retain the previous plan snapshot until a new publication is explicitly activated.

### 13.11 Composition Decision changed

Changing a decision invalidates plan approval when it affects core selection, scope, order, depth, cross-links, appendices, omissions, or coverage gaps. Rejecting an AI proposal leaves an audit record and does not delete the underlying claims.

### 13.12 Deliverable review scope changed

A review scope pins one deliverable revision and its deterministic dependency closure. If the Composition Plan, Answer Evidence Bundle, selected claim revision, material relation, citation, or required maturity changes:

* create a new review-scope revision and preserve the prior snapshot;
* recompute the dependency closure and its blocking work items;
* retain completed reviews when the exact reviewed record revision and required use remain unchanged;
* reopen only newly added, changed, or newly material dependencies; and
* prevent activation until every blocker in the new scope reaches its required maturity.

Completing or rejecting a work item recomputes the scope gate. Deferring an unrelated candidate has no gate effect. Deferring a required dependency is allowed only after revising the deliverable so that no retained conclusion depends on it.
