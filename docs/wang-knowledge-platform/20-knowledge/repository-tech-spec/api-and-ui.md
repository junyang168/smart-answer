# API, Frontend, Source Resolution and Authorization

> **读者**：Developer
> **类型**：规范
> **状态**：当前
> **与代码对齐**：未核对
> **权威范围**：The surfaces this repository exposes。本文是《文库 Technical Specification》的一部分。

本规范的其余部分：

| 文件 | 内容 |
| --- | --- |
| [Technical Specification: Exegesis and Topic Repository](./README.md) | Architecture, storage layout and identifiers |
| [Data Models](./data-models.md) | Every stored record type in the repository |
| [Evidence Pipeline, Compiler, Read Model and Invalidation](./compiler.md) | How source material becomes queryable state, and what invalidates it |
| [API, Frontend, Source Resolution and Authorization](./api-and-ui.md) | The surfaces this repository exposes |
| [Observability, Testing, Phases, Deployment and Acceptance](./delivery.md) | How the work is verified and shipped |

### Contents

- [9. API Design](#9-api-design)
  - [9.1 Public repository endpoints](#91-public-repository-endpoints)
  - [9.2 Admin endpoints](#92-admin-endpoints)
- [10. Frontend Design](#10-frontend-design)
  - [10.1 Routes](#101-routes)
  - [10.2 Reusable components](#102-reusable-components)
  - [10.3 Transcript rendering](#103-transcript-rendering)
  - [10.4 Notes rendering](#104-notes-rendering)
  - [10.5 Relationship visualization](#105-relationship-visualization)
  - [10.6 Knowledge review views](#106-knowledge-review-views)
- [11. Source Resolution and Highlighting](#11-source-resolution-and-highlighting)
- [12. Authentication and Authorization](#12-authentication-and-authorization)

## 9. API Design

All browser-facing calls use the Next.js API proxy. Paths below describe backend FastAPI routes.

### 9.1 Public repository endpoints

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

### 9.2 Admin endpoints

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

### 10.1 Routes

Implemented routes:

```text
/resources/wang-repository
/resources/wang-repository/{unitId}
/resources/sermons/{sermonId}?citation={citationId}

/admin/canonical-repository
/admin/canonical-repository/{unitId}
```

Stable unit and citation IDs, rather than localized titles, identify repository records and deep links.

### 10.2 Reusable components

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

### 10.3 Transcript rendering

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

### 10.4 Notes rendering

The notes source reader displays:

* the source image endpoint for the selected page;
* raw OCR Markdown rendered as text;
* exact `<mark>` highlighting in the OCR text; and
* adjacent-page navigation.

Future `image_regions` are normalized coordinates between 0 and 1 and render as accessible overlays on the source image.

### 10.5 Relationship visualization

The API returns a bounded one-hop graph for the selected unit. The frontend limits node count and groups excess sources by source document. It must not fetch or lay out the complete repository graph by default.

### 10.6 Knowledge review views

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
