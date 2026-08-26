# Observability, Testing, Phases, Deployment and Acceptance

> **读者**：Developer
> **类型**：规范
> **状态**：当前
> **与代码对齐**：未核对
> **权威范围**：How the work is verified and shipped。本文是《文库 Technical Specification》的一部分。

本规范的其余部分：

| 文件 | 内容 |
| --- | --- |
| [Technical Specification: Exegesis and Topic Repository](./README.md) | Architecture, storage layout and identifiers |
| [Data Models](./data-models.md) | Every stored record type in the repository |
| [Evidence Pipeline, Compiler, Read Model and Invalidation](./compiler.md) | How source material becomes queryable state, and what invalidates it |
| [API, Frontend, Source Resolution and Authorization](./api-and-ui.md) | The surfaces this repository exposes |
| [Observability, Testing, Phases, Deployment and Acceptance](./delivery.md) | How the work is verified and shipped |

### Contents

- [14. Observability](#14-observability)
- [15. Testing Strategy](#15-testing-strategy)
- [16. Implementation Phases](#16-implementation-phases)
- [17. Deployment and Rollback](#17-deployment-and-rollback)
- [18. Technical Acceptance Criteria](#18-technical-acceptance-criteria)

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
