# Functional Specification: Exegesis and Topic Repository

> **讀者**：Solution architect
> **類型**：規範
> **狀態**：當前
> **與代碼對齊**：未核對
> **權威範圍**：面向讀者的文庫功能：canonical unit、聖經與主題索引、來源可追溯。受總體設計約束。

> Project mission and plain-language explanation: [王守仁教授釋經與專題講論文庫 Mission Statement](../00-overview/project_mission_statement.md)
>
> Cross-product knowledge architecture, argument graph, original-language records, QA contract, permissions, and evolution rules: [王守仁教授释经与思想知识平台设计](../00-overview/knowledge_platform_design.md)

### Contents

- [1. Purpose](#1-purpose)
- [2. Problem Statement](#2-problem-statement)
- [3. Product Principles](#3-product-principles)
- [4. Users and Permissions](#4-users-and-permissions)
- [5. Scope](#5-scope)
- [6. Domain Concepts](#6-domain-concepts)
- [7. Information Architecture](#7-information-architecture)
- [8. Reader Workflows](#8-reader-workflows)
- [9. Editorial Workflows](#9-editorial-workflows)
- [10. UI Requirements](#10-ui-requirements)
- [11. Citation Requirements](#11-citation-requirements)
- [12. Integration with Existing Workflows](#12-integration-with-existing-workflows)
- [13. Migration Plan](#13-migration-plan)
- [14. Non-Functional Requirements](#14-non-functional-requirements)
- [15. Acceptance Criteria](#15-acceptance-criteria)
- [16. Rollout Sequence](#16-rollout-sequence)


## 1. Purpose

The Exegesis and Topic Repository reorganizes Dr. Wang's notes, lectures, sermons, and generated manuscripts into a durable reader-facing knowledge collection. Its larger mission is not merely to reformat more than 200 sermons, but to preserve what Dr. Wang said, reconstruct how his Scripture evidence and reasoning support his claims, identify recurring interpretive patterns, and use that reviewed foundation to create coherent exegesis, topic studies, and evidence-backed answers. It is not organized primarily by teaching event, conference, Lecture, or Project. Instead, it presents the same reviewed canonical units through two complementary indexes:

1. a Bible index from Genesis through Revelation; and
2. a theological topic index containing concepts such as dispensationalism, the deity of Christ, justification, faith, and hermeneutics.

Every published unit contains a readable manuscript and one or more traceable links to the exact source fragments from which the manuscript was derived. A source link must open the original sermon transcript or notes source, position the reader at the relevant fragment, and highlight that fragment. Opening only the top of a complete source document does not satisfy this requirement.

The repository is also the reviewed knowledge foundation for evidence-backed QA, three-to-five-minute micro-sermons, original-language search, thought-development comparison, and future study tools. Canonical manuscripts are new editorial works built from reviewed claims and evidence under an approved publication profile and composition plan; they are not direct graph projections or the only machine-readable representation of Dr. Wang's teaching.

## 2. Problem Statement

Dr. Wang's teaching frequently combines verse-by-verse exegesis, theological synthesis, applications, historical comments, questions, and personal examples in a non-linear order. The same argument may be repeated, extended, corrected, or illustrated in several lectures. Preserving lecture order therefore produces unnecessary repetition and weakens the logic of a reader-facing manuscript.

The repository solves three related problems:

* **Editorial structure**: preserve Dr. Wang's thought without preserving the accidental sequence of a live lecture.
* **Dual discovery**: let readers begin with either a biblical passage or a theological topic.
* **Source traceability**: let readers inspect the exact sermon or notes fragment supporting a unit, including its original context and media time when available.

## 3. Product Principles

### 3.1 Canonical unit, not lecture, is the reading unit

A Project remains the production, review, and audit boundary. A canonical unit is the long-term editorial and reader-facing unit. One Project may create several canonical units, and one canonical unit may combine evidence from several Projects, lectures, dates, or venues.

### 3.2 Bible and topic views are indexes over the same repository

The Bible index and topic index must not create duplicate manuscripts. A unit has one authoritative manuscript and can appear in multiple index locations.

### 3.3 Exegesis may contain theology

Passage units may include theology necessary to explain the passage. Topic units synthesize arguments that cross passages or recur across multiple sources. The distinction is organizational, not a rule that theology must be removed from exegesis.

### 3.4 Source links are fragment-level citations

Both passage units and topic units use the same citation capability:

* original sermon transcript with highlighted text;
* audio or video positioned at the relevant start time;
* original notes page with the relevant OCR text highlighted; and
* enough surrounding context to evaluate the quotation or argument.

### 3.5 Repetition is consolidated, not erased

When a later lecture repeats an existing idea without adding substance, the existing canonical unit remains unchanged and the repeated source may be recorded as an additional occurrence. When the later lecture adds Scripture, reasoning, qualification, or correction, the canonical unit is extended and both sources remain visible.

### 3.6 Editorial decisions are reviewable

AI may propose unit boundaries, topic assignments, duplicate relationships, and source fragments. Publication requires human review. Low-confidence classification or unresolved source mapping must remain visible rather than being silently accepted.

### 3.7 Knowledge graph before product projection

Questions, claims, opposed views, Scripture evidence, original-language judgments, qualifications, applications, and cross-sermon relations are stored independently of any one article. Passage manuscripts, topic manuscripts, QA answers, search results, timelines, and study materials are projections over that shared reviewed knowledge.

### 3.8 QA is claim-grounded, not transcript-only RAG

Keyword, passage, and vector retrieval may discover candidate evidence. A public answer must use approved claim relationships to distinguish Dr. Wang's position from a quoted opposing view, connect questions to answers, include later qualifications, and resolve citations to exact sources. When approved evidence is insufficient, the system reports that limitation rather than supplying an answer from general model knowledge.

### 3.9 The thought map remains open

The current candidate thought trunks do not constrain later sermons. Editors may add, extend, promote, demote, split, merge, mark tension, or supersede nodes. Every change preserves evidence, reason, prior revision, and review state.

### 3.10 Review follows deliverables, not total extraction volume

AI may create many candidate records, but a publication does not wait for the entire corpus or Project to become approved. Each article, answer set, or report defines a minimum publishable subgraph containing only the knowledge, sources, relations, and composition decisions it materially uses. Records outside that dependency closure remain candidate and do not block the deliverable.

“Important content requires human review” means important content used in the current public deliverable requires review. It does not mean every extracted record across 200-plus sermons must be reviewed before any result can ship.

## 4. Users and Permissions

### 4.1 Reader

* Browses by Bible passage or topic.
* Reads the canonical manuscript.
* Opens related passage and topic units.
* Opens exact source fragments and their surrounding context.
* Seeks sermon media to the cited time.
* Asks passage, topic, source-location, original-language, and comparison questions.
* Sees whether an answer is Dr. Wang's explicit claim, a reasoning conclusion, an editorial synthesis, fact-check pending, or evidence insufficient.

### 4.2 Editor

* Reviews candidate canonical units.
* Confirms whether a unit is passage-led or topic-led.
* Confirms Bible references and topic taxonomy assignments.
* Reviews, adjusts, adds, or removes source citations.
* Resolves stale or ambiguous citations.
* Publishes units and refreshes repository indexes.
* Reviews candidate questions, claims, relations, original-language judgments, and thought-map revisions.
* Separates faithful representation of Dr. Wang's claim from later language, history, or theological fact checking.

### 4.3 Administrator

* Manages repository builds and source-map jobs.
* Reviews failed or stale source mappings.
* Controls access to unpublished transcripts and notes.
* Can rebuild derived indexes without changing manuscripts or editorial decisions.

## 5. Scope

### 5.1 Included in the initial release

* Global repository independent of an individual Series page.
* Bible and topic indexes over shared canonical units.
* Passage and topic unit detail pages.
* Manuscript, Sources, and Related Units views.
* Multiple source citations per unit.
* Transcript paragraph highlighting and media time positioning.
* Notes page selection and highlighted OCR text.
* Local relationship visualization centered on the selected unit.
* Editorial source review and publication gating.
* Migration of the existing Matthew seed catalog and transcript Projects.

### 5.2 Included in the knowledge-platform target

* A reviewed question, claim, relation, Scripture-evidence, original-language, application, and revision store.
* Claim-aware passage and topic projections.
* Evidence-backed public QA and a clearly marked internal research mode.
* Original-language and translation-criticism browsing.
* Cross-sermon repetition, extension, qualification, tension, and development views.
* Reusable knowledge projections for study guides and future course tools.

### 5.3 Explicitly out of scope for the initial release

* Replacing the existing Series, Lecture, or Project production workflow.
* Automatically publishing AI-proposed units without editor review.
* Displaying a single global graph containing every unit and source.
* Requiring image-coordinate highlighting on handwritten notes in the first release. The first release shows the correct source image beside highlighted OCR text. Image overlay coordinates may be added later.
* Treating a generated manuscript or Google Doc as an original source. These are derived editorial artifacts and do not satisfy the source requirement.

## 6. Domain Concepts

### 6.1 Canonical unit

A reviewed editorial article with a stable ID, title, manuscript, classifications, and sources.

Required properties:

* stable unit ID;
* title;
* unit type: `passage` or `concept`;
* publication status;
* authoritative manuscript location;
* primary Bible references, if applicable;
* zero or more topic taxonomy paths;
* related unit relationships; and
* at least one approved source citation or an explicit approved exception.

### 6.2 Passage unit

A unit whose primary organizing question is the interpretation of a sustained biblical passage. It may contain necessary theological significance and application.

### 6.3 Topic unit

A unit whose primary organizing question crosses passages, lectures, or occasions. A topic unit may cite many sermon and notes fragments and must have the same highlight and media-positioning behavior as a passage unit.

### 6.4 Source document

An original sermon transcript, sermon recording, or notes source. Source documents have stable IDs and version hashes.

### 6.5 Source fragment

A precise region within a source document. For transcripts this includes paragraph identity, exact highlighted text, and time information. For notes it includes the source page, exact OCR text, and text range.

### 6.6 Citation

A stable, shareable record connecting a source fragment to one or more canonical units. A citation records what the fragment supports, not merely where the source document is stored.

### 6.7 Unit relationship

A reviewed connection between two canonical units. Initial relationship types are:

* `related_topic`;
* `related_passage`;
* `explains`;
* `supported_by`;
* `background_for`;
* `contrasts_with`; and
* `supersedes`.

### 6.8 Question

A source-grounded interpretive, theological, audience, or editorial question. It records who asked it, the source anchor, applicable passages/topics, answer status, and the claim IDs that answer it. Important unanswered questions remain visible.

### 6.9 Claim

The smallest reviewable proposition that can be supported, opposed, qualified, applied, repeated, extended, or placed in tension. Claim types distinguish explicit claims, reasoning conclusions, interpretive methods, opposed views, applications, editorial syntheses, and open questions. Claims have stable repository-wide IDs and do not inherit identity from a manuscript heading.

### 6.10 Claim relationship

A reviewed directed relation between claims. Supported types include `supports`, `answers`, `opposes`, `qualifies`, `applies`, `repeats`, `extends`, `tension`, `supersedes`, and `editorial_inference`. The relation records its reason, sources, review state, and revision.

### 6.11 Original-language judgment

A structured record of Dr. Wang's Hebrew, Aramaic, or Greek argument and any translation criticism. It preserves the biblical reference, source-language form, grammatical or semantic issue, target translation, Dr. Wang's proposed rendering, reasons, interpretive effect, exact source, representation review, and separate fact-check state.

### 6.12 Evidence step

A source-grounded observation Dr. Wang uses in an argument, such as wording, grammar, genre, context, history, comparison, or counterexample. It identifies the evidence without collapsing it into the final conclusion.

### 6.13 Inference bridge

A reviewable explanation of how one or more evidence steps or premise claims lead to a conclusion. It distinguishes Dr. Wang's explicit reasoning, reasoning closely reconstructed from his discourse, and an editor-supplied bridge.

### 6.14 Passage interpretation chain

A cross-sermon, passage-keyed sequence of questions, observations, inference bridges, conclusions, later extensions, qualifications, and unresolved gaps. It is reusable knowledge and does not determine the outline of a specific manuscript.

### 6.15 External evidence

A historical, cultural, scholarly, medical, psychological, probabilistic, traditional, or experiential premise used in an argument. It preserves Dr. Wang's use of that material while keeping independent fact checking separate.

### 6.16 Application reasoning

A structured transition from the biblical source context through a stable principle to a target context. It records audience, normative level, applicability conditions, qualifications, and pastoral risks so a local exhortation is not silently universalized.

### 6.17 Thought-map revision

An auditable change that adds, extends, promotes, demotes, splits, merges, marks tension, or supersedes a thought node. Superseded records remain available for history and rollback.

### 6.18 Answer evidence bundle

The bounded, permission-filtered collection used to answer one question. It contains selected question/claim IDs, traversed relationships, approved citations, attribution labels, unresolved issues, and related units. The generated prose is not itself the evidence bundle.

### 6.19 Publication profile

A reusable, user-approved editorial specification for one product family. It converts a request such as an academically structured, passage-centered commentary into explicit rules for passage order, theological depth, original-language treatment, alternative interpretations, application, tone, citations, appendices, cross-links, and evidence gaps. It describes how editors intend to publish; it is not attributed to Dr. Wang.

### 6.20 Composition plan

The versioned plan for one specific passage lecture, topic essay, course, or other authored work. It records the publication profile, audience, purpose, scope, central question, thesis, outline, selected knowledge records, desired depth, expected length, coverage gaps, and approval state.

### 6.21 Composition decision

A reviewable editorial choice whose alternative could materially change the work. Examples include making material a core section, treating it briefly, moving it to a topic article or appendix, linking a related unit, omitting repetition, deferring for missing evidence, identifying a narrative climax, or setting section order. Each decision records its reason, affected knowledge records, governing user requirement or profile rule, version, and reviewer. It is never represented as the professor's claim.

## 7. Information Architecture

```mermaid
flowchart LR
    B["Bible Index"] --> P["Passage Unit"]
    T["Topic Index"] --> C["Topic Unit"]
    P <-->|"reviewed relationships"| C
    P --> PM["Passage Manuscript"]
    C --> CM["Topic Manuscript"]
    P --> PC["Source Citations"]
    C --> TC1["Source Citation A"]
    C --> TC2["Source Citation B"]
    PC --> PS["Original Sermon or Notes<br/>highlight plus time or page"]
    TC1 --> TS1["Original Sermon A<br/>highlight plus media time"]
    TC2 --> TS2["Original Notes<br/>page plus highlighted OCR"]
```

## 8. Reader Workflows

### 8.1 Browse by Bible

1. The reader opens **按聖經**.
2. The reader selects a book and chapter.
3. The UI lists passage units in canonical verse order.
4. Each result displays passage range, title, related-topic count, source count, and publication state.
5. Selecting a result opens the canonical unit page.

Books or chapters with no published material remain visible and display an empty state rather than disappearing from the biblical canon.

### 8.2 Browse by topic

1. The reader opens **按主題**.
2. The reader expands the reviewed two-level taxonomy.
3. The UI lists units assigned to the selected topic.
4. A unit may appear under more than one topic path without creating a duplicate manuscript.
5. Selecting a result opens the same canonical unit page used by Bible browsing.

### 8.3 Read a canonical unit

The unit page provides three primary views:

* **Manuscript**: the readable article, retaining `釋經`, `神學意義`, `生活應用`, and `附錄` only when they contain substantive content.
* **來源與證據**: approved source fragments grouped by source document and ordered by editorial role or teaching date.
* **關聯單元**: related passage and topic units.

The page header displays the unit type, Bible references, topic paths, source count, and publication state.

The publication metadata identifies the governing publication profile and composition-plan revision. Public presentation may summarize the editorial approach; editor views expose the complete plan and decision history.

### 8.4 Inspect a sermon source

1. The reader selects a sermon citation.
2. The sermon page opens or a source drawer appears.
3. The UI scrolls to the cited transcript paragraph.
4. The exact cited text is highlighted.
5. One preceding and one following paragraph are available as context.
6. If media timing exists, the player seeks to the citation start time but does not autoplay without user action.
7. The reader may expand to the complete sermon.

### 8.5 Inspect a notes source

1. The reader selects a notes citation.
2. The notes reader opens the correct scanned page.
3. The source image is displayed beside or above its OCR text.
4. The cited OCR text is highlighted and scrolled into view.
5. The reader may view adjacent pages.

### 8.6 Explore relationships

The relationship view is centered on the selected unit. It shows only direct passage, topic, and source relationships. It must not render the complete repository as an unreadable graph.

Selecting a related-unit node opens that unit. Selecting a source node opens the citation preview.

### 8.7 Ask an evidence-backed question

1. The reader asks a passage, topic, original-language, comparison, application, or source-location question.
2. The system identifies question intent, Bible references, topic terms, time comparison, and requested answer depth.
3. Retrieval finds candidate units, claims, original-language judgments, and source occurrences.
4. The system traverses approved relationships to collect answers, supporting reasons, opposed views, qualifications, tensions, and exact citations.
5. Access control removes restricted material before answer generation.
6. Sources and direct claims appear before or while answer prose streams.
7. The response distinguishes Dr. Wang's explicit claim, reasoning conclusion, editorial synthesis, pending fact check, different expression, and insufficient evidence.
8. The reader may open the exact highlighted source, related passage unit, or topic study.

The answer must not infer Dr. Wang's position from a topically similar transcript fragment alone. If a candidate fragment is an opposed view, unanswered question, or non-substantive classroom exchange, it cannot become the answer without a reviewed relationship establishing its role.

### 8.8 Browse original-language and translation judgments

The reader or authorized researcher can browse by Bible reference, source-language term, target translation, sermon, theological effect, and fact-check state. Each record displays:

* the source-language form and transliteration when available;
* the Chinese translation under discussion;
* Dr. Wang's proposed rendering;
* his lexical, grammatical, contextual, and cross-reference reasons;
* the interpretive or theological conclusions affected;
* exact source fragments and media time; and
* a separately labeled independent fact-check result, when one exists.

Faithful representation approval does not imply independent linguistic correctness.

### 8.9 Compare teaching across time

The reader or researcher selects a claim or topic and sees occurrences ordered by date. The comparison distinguishes stable repetition, added evidence, extension, qualification, application, unresolved tension, and supersession. Frequency is displayed as evidence, not as the sole measure of importance.

## 9. Editorial Workflows

### 9.1 Create or update a canonical unit

1. A checked-in Project or approved cross-lecture integration produces candidate units.
2. The system retains the evidence IDs assigned to each unit.
3. The repository builder maps those evidence IDs to original source fragments.
4. The editor reviews title, type, references, topic paths, relationships, manuscript location, and citations.
5. The editor approves or revises the candidate.
6. Publication writes the approved repository record and rebuilds derived indexes.

### 9.2 Review source citations

For every proposed citation, the editor sees:

* source title, date, venue, and source type;
* the exact highlighted text;
* surrounding context;
* paragraph and media time, or notes page and OCR range;
* evidence IDs and the claim or role supported;
* source version status; and
* actions to adjust, approve, reject, or replace the fragment.

A citation must contain substantive source prose. A Markdown heading by itself is navigation metadata, not evidence, and must not appear as a transcript or notes citation. Existing heading-only citations may be detached from units without deleting their stored citation records.

### 9.3 Review repository units from a sermon

When an editor opens an original sermon page, the right rail displays every canonical unit citing that sermon, including `candidate`, `reviewed`, `published`, and `archived` units. The list is divided into **釋經單元** and **主題單元**. Each item shows its current review status and links directly to the canonical-unit review page.

This view answers two editorial questions without requiring a search through the repository:

* which passage units have already been extracted from this sermon; and
* which cross-passage topic units currently use this sermon as a source.

The list is available only to users with repository editing permission. Public sermon readers do not see unpublished repository units.

### 9.4 Consolidate repeated teaching

When a later source overlaps an existing unit, the editor chooses among:

* **additional occurrence**: manuscript remains unchanged; source is added;
* **extension**: manuscript and source list are expanded;
* **correction**: manuscript is updated while both the earlier and corrective source remain visible;
* **exact duplicate**: no repeated prose is added, but the occurrence may remain in provenance; or
* **new related unit**: the material has a distinct organizing question and becomes a separate unit.

### 9.5 Publish

A unit may be published only when:

* its manuscript points to a checked-in `final.md` section or an approved repository manuscript;
* its unit type and index assignments are reviewed;
* it references an approved publication profile and composition plan;
* every material composition decision is approved or explicitly waived with a reason;
* coverage gaps and deferred passages are disclosed rather than silently filled;
* every attached citation resolves against its recorded source version;
* at least one source citation is approved, unless an editor-approved exception includes a reason; and
* no required citation is stale or unresolved.

The publication gate evaluates the deliverable's frozen review scope, not whether every candidate record in the source Project, sermon, chapter, or repository is approved. Excluded candidates remain visible to authorized editors and retain their lineage.

Publishing a unit does not overwrite Project manuscripts. Refreshing repository indexes does not rerun manuscript generation.

### 9.6 Review questions, claims, and relations

For each candidate claim, the editor reviews:

* exact wording and attribution;
* whether the professor states it, argues to it, quotes it as an opposed view, or leaves it unanswered;
* Scripture evidence and the role of each reference;
* source citations and surrounding context;
* incoming and outgoing argument relations;
* relation to passage and topic units;
* maturity, visibility, and fact-check state; and
* duplicate, extension, qualification, tension, or supersession candidates.

Approval of a claim does not automatically publish an article. Publication of an article does not automatically approve every editor-generated synthesis in its prose.

### 9.7 Review original-language judgments

The editor first approves whether the record faithfully states Dr. Wang's argument. Independent language review is a separate action with a separate reviewer, status, notes, and evidence. A fact-check result may confirm, qualify, dispute, or leave the claim unresolved but cannot overwrite the professor's recorded position.

### 9.8 Evolve the thought map

When new sermons alter the current map, the editor previews the affected nodes and chooses add, extend, promote, demote, split, merge, mark tension, or supersede. The UI requires a change reason and shows the resulting effects on passage projections, topic projections, QA answers, and related units before activation.

### 9.9 Plan and review an authored work

1. The user selects a versioned Publication Profile or creates a candidate profile from explicit requirements.
2. The user supplies the work's passage/topic scope, audience, purpose, desired depth, length, and special requirements.
3. The system proposes a Composition Plan from available reviewed knowledge and reports missing coverage.
4. The editor reviews the central question, thesis, selected knowledge records, outline, depth, cross-links, appendices, omissions, and deferred material.
5. Every material choice becomes a Composition Decision with a reason and provenance to the user requirement or profile rule.
6. The editor approves the plan before manuscript generation.
7. Generation remains within the approved plan. A new claim, major reordering, new appendix, or changed scope returns the plan to review rather than entering prose silently.
8. Publication snapshots the exact profile and plan revisions used.

For the Matthew 17 pilot, the plan must explain why `Amen` and “人子” receive only passage-relevant treatment, why their full discussion links to topic studies, which units form the principal exegetical sections, and how Matthew 17:22–27 coverage gaps are handled.

### 9.10 Create a deliverable review scope

1. The editor freezes the Composition Plan or AnswerEvidenceBundle revision for the intended deliverable.
2. The system computes the material dependency closure: selected claims, required relations, exact citations, language/application records, composition records, permissions, gaps, and unresolved items.
3. The editor may remove optional material from the deliverable, but cannot waive a dependency while retaining the conclusion that requires it.
4. Each dependency receives a target maturity and responsible review role.
5. The UI shows blocking, deferrable, completed, and failed items plus estimated and actual review time.
6. Publication becomes available when every blocking dependency reaches its target state and no access or stale-source gate fails.
7. Later expansion creates a new review-scope revision; it does not retroactively change the published snapshot.

### 9.11 Operate a capacity-aware review queue

Editors can group work by deliverable, reviewer role, risk, passage/topic, or source. The default queue prioritizes records that unblock the nearest approved deliverable. It does not prioritize merely because AI generated the record earlier.

The pilot records estimated and actual review minutes by record type and role; proposed, accepted, changed, rejected, and deferred counts; rework caused by bad source anchors or wrong attribution; weekly available editor hours; and projected backlog under the current extraction rate. These measurements determine batch size and future automation. The system must not hide editorial debt behind a large candidate count.

### 9.12 Plan and review a micro-sermon

1. The editor chooses one reader question that can be answered in three to five minutes without removing a material premise or qualification.
2. The system builds a minimum claim-and-source subgraph and proposes either `source_excerpt` or `editorial_synthesis` mode.
3. For a source excerpt, the editor verifies exact media start/end time, transcript highlight, speaker, stance, and surrounding context.
4. For editorial synthesis, the editor reviews the short script, claim order, attribution, limitations, and links to deeper passage/topic products.
5. Independent AI review checks source fidelity, argument completeness, attribution, and harmful omissions; it does not perform theological criticism.
6. Publication uses the existing micro-sermon administration and public routes and records ProductDependencies for every material claim and citation.
7. A stale source or invalidated upstream dependency returns the micro-sermon to review and blocks it from a new public build.

## 10. UI Requirements

### 10.1 Repository home

Required navigation:

* **按聖經**;
* **按主題**;
* **關係圖**; and
* full-text search when the existing sermon search integration is enabled.

The existing Series page remains available as **按講次／場合** browsing and may link into the repository with Series filters.

### 10.2 Bible index

* Preserve canonical book order.
* Sort units by OSIS start reference, not title.
* Distinguish primary passage from supporting cross-references.
* Show empty books and chapters without implying missing data is an error.

### 10.3 Topic index

* Use the reviewed taxonomy and alias groups.
* Support a unit assigned to multiple topic paths.
* Display source count and related passage count.
* Allow filtering by unit title, alias, argument, passage, or source title.

### 10.4 Unit page

* Use a single URL for the unit regardless of discovery path.
* Preserve manuscript heading anchors.
* Show sources in a dedicated tab or panel; source access must not require finding links inside prose.
* Optional inline source markers may be added later, but they do not replace the Sources panel.

Current release behavior:

* the admin review page renders the linked manuscript section as Markdown so editors can compare the edited article with its original evidence;
* the public unit page is temporarily source-first and hides the manuscript behind a feature flag until the editorial team approves public manuscript presentation; and
* this temporary hiding affects presentation only—the manuscript locator and manuscript Markdown remain part of the canonical unit.

### 10.5 Source citation component

Each citation displays:

* source label and teaching date or notes page;
* short exact excerpt;
* supported role or claim;
* start time for timed media;
* source-version warning when stale; and
* **查看原始內容** action.

For sermon sources, the audio or video player appears immediately above the highlighted excerpt and seeks to the citation start time. Both the source title and **打开完整讲道与逐字稿** open the complete sermon in a new browser tab.

Citation excerpts are rendered as Markdown in the admin review page. A pure Markdown heading is suppressed at citation-generation time because it provides no evidentiary text and otherwise creates an empty-looking player card at `0:00`.

### 10.6 Responsive and accessible behavior

* Desktop may use manuscript plus source drawer or split view.
* Mobile uses stacked tabs and a full-width source sheet.
* Highlighting must use semantic `<mark>` behavior and must not rely on color alone.
* Opening a citation moves keyboard focus to the highlighted fragment.
* All source links remain shareable URLs.

### 10.7 Knowledge and QA views

Required knowledge-platform views are:

* a claim review view with direct incoming/outgoing relations;
* a question view that shows answer status and answering claims;
* an original-language judgment view;
* a source occurrence timeline;
* a thought-map revision preview and history; and
* a QA result view with direct answer, reasoning, Scripture evidence, qualifications, source cards, and related units.
* a Publication Profile library with version comparison;
* a Composition Plan editor showing outline, selected knowledge, coverage, and approval state; and
* a Composition Decision list filterable by core, brief, cross-link, appendix, omission, deferral, climax, and order.

The default UI renders a bounded local neighborhood, not the complete graph. Internal research mode must be visually distinct from public approved-content mode.

### 10.8 Non-technical review safeguards

The review UI is complete only when an editor can see and act on the judgments required for publication. Data that exists only in JSON or an API response does not satisfy this requirement.

* A claim separates eligible professor evidence, audience/opposed-view context, and withheld evidence.
* Every claim shows its intended knowledge route and any thin-evidence warning.
* A claim with zero eligible evidence cannot be approved through either the UI or the API. One eligible item remains reviewable but is visibly marked as weak support.
* Composition review displays claim hierarchy—paragraph thesis, supporting claims, theological ground, and editorial note—rather than a flat claim list.
* Composition lists show `main_section`, `brief_note`, topic-link, and coverage-gap actions alongside review status.
* Editorial checks and unresolved interpretive tensions appear in the publication review surface.
* Controls that reveal content lower on the page must move focus or scroll to the revealed region and expose a clear accessible label; silent expansion is not acceptable.

## 11. Citation Requirements

### 11.1 Transcript citation

A valid transcript citation contains:

* stable citation and source IDs;
* transcript workflow stage;
* source checksum;
* paragraph identity;
* exact highlighted substring;
* occurrence or character offsets when the substring is not unique;
* start and end time when present;
* evidence IDs; and
* supported claim or argument role.

### 11.2 Notes citation

A valid notes citation contains:

* stable citation and source IDs;
* Project and source-page identity;
* source checksum;
* exact highlighted OCR substring;
* page-relative OCR line or character range;
* evidence IDs; and
* supported claim or argument role.

### 11.3 Version behavior

If the current source checksum differs from the citation checksum, the citation becomes stale. The UI must not silently highlight a different passage. The system may propose a remap using stable paragraph identity and exact quotation, but an ambiguous remap requires editor review.

## 12. Integration with Existing Workflows

### 12.1 Notes to Manuscript

Existing Project generation, theological review, Check In, and `final.md` remain authoritative. The repository consumes checked-in manuscript sections and source lineage; it does not replace Project editing.

### 12.2 Transcript to Manuscript

Evidence Inventory and Manuscript Plan already retain source ranges and evidence assignments. New generation must additionally retain exact highlight anchors so repository citations can be built deterministically.

### 12.3 Cross-Lecture Integration

Merge proposals and integration applications must carry source lineage with every evidence disposition and patch. Merging prose without merging source lineage is invalid.

### 12.4 Topic and Search Index

The repository's reviewed Bible and topic indexes become the preferred navigation source. Sermon search continues to index manuscript text and uses repository unit IDs and citation IDs when returning source results.

## 13. Migration Plan

> Not normative. This section records an intended sequence at a point in time; the rules in the rest of this specification are what implementations must satisfy.

### 13.1 Pilot

Use three representative Matthew units:

1. a passage unit: the Transfiguration;
2. a cross-passage topic unit: the meaning of `小信`; and
3. a repeated multi-source topic: dispensationalism and the Scofield tradition.

The pilot must exercise transcript timing, multiple lecture sources, notes pages, passage relationships, topic relationships, and stale-source handling.

### 13.2 Matthew migration

After the pilot passes:

1. import reviewed units from the Matthew seed catalog;
2. resolve candidate and duplicate-review items;
3. build source maps for published notes and transcript Projects;
4. approve citations in batches;
5. publish Matthew repository indexes; and
6. compare repository coverage with all checked-in Matthew Projects.

### 13.3 Remaining sermons

Process the wider corpus incrementally. New sermons enter the same evidence, continuity, canonical-unit, citation-review, and publication workflow. The repository must not wait for all 200-plus sermons to be processed before publishing reviewed units.

## 14. Non-Functional Requirements

### 14.1 Traceability

Every published source link resolves to exact original content or clearly reports why it is unavailable.

### 14.2 Stability

Unit and citation URLs remain stable when titles change. Titles must never be used as the sole identifier.

### 14.3 Integrity

Derived indexes are rebuilt atomically. A failed build must not replace the active repository.

### 14.4 Performance

Repository index pages should respond within one second for the expected corpus. Citation resolution and source preview should normally respond within one second on local infrastructure.

### 14.5 Security

Public readers may access only source stages and notes assets authorized for publication. Draft, reviewed-only, or raw sources require editor access.

### 14.6 Observability

Repository builds report unit, relationship, citation, stale-citation, and unresolved-citation counts. Every published snapshot records input hashes and generation time. Editorial reporting also shows the active deliverables, blocking review items, estimated and actual review time, approval/rejection/deferral outcomes, rework, weekly capacity, and projected backlog. It must distinguish “records extracted” from “records required by a deliverable” so a large candidate corpus does not create a misleading completion percentage.

## 15. Acceptance Criteria

### 15.1 Shared unit behavior

* The Bible and topic indexes can point to the same unit URL.
* A unit has one authoritative manuscript regardless of how the reader discovered it.
* Both passage and topic units display source citations.

### 15.2 Transcript source behavior

* Selecting a transcript citation opens the correct sermon.
* The cited paragraph scrolls into view and the exact text is highlighted.
* The media player seeks to the citation start time when timing exists.
* The reader can inspect surrounding context and the complete sermon.
* A heading-only transcript segment is never offered as a source citation.

### 15.3 Sermon editorial rail behavior

* Editors can see all passage and topic units citing the current sermon.
* Candidate and other unpublished units remain visible to editors and hidden from public readers.
* Selecting a unit opens its repository review page.

### 15.4 Notes source behavior

* Selecting a notes citation opens the correct page.
* The relevant OCR text scrolls into view and is highlighted.
* The original scanned page remains visible.

### 15.5 Multi-source topic behavior

* A topic unit can list sources from multiple lectures and notes Projects.
* Each source opens its own exact highlighted fragment.
* Repeated teaching does not create repeated manuscript prose.

### 15.6 Editorial behavior

* An editor can adjust and approve a citation.
* A changed source checksum marks affected citations stale.
* A unit with unresolved required citations cannot be published.
* Repository refresh does not overwrite Project manuscripts.
* The editor can choose an approved Publication Profile and create a plan for one work.
* The plan distinguishes user requirements, editor decisions, and AI proposals.
* Important decisions have IDs, reasons, revisions, and review states.
* Regeneration cannot silently change an approved plan.

### 15.7 Composition behavior

* The same reviewed claims can support different approved works without duplicating claim identity.
* A passage-centered academic profile keeps the current passage primary while linking deeper cross-passage topics.
* A missing passage is recorded as a coverage gap and deferred decision; AI does not invent Dr. Wang's exposition.
* A published article resolves to the exact Publication Profile and Composition Plan revisions used to create it.
* Changing a profile does not retroactively change existing publications.
* A micro-sermon plan contains one central question and a minimum complete argument rather than a mechanically shortened manuscript.
* A source-excerpt micro-sermon resolves to exact media bounds and highlighted transcript context; an editorial-synthesis micro-sermon is visibly attributed to the editor and resolves to every material claim used.
* Upstream claim, relation, or citation invalidation identifies and blocks every affected micro-sermon.

### 15.8 Pilot examples

* The Transfiguration passage unit links to its relevant lecture fragment.
* The `小信` topic unit links to each relevant Matthew passage and source occurrence.
* The dispensationalism/Scofield topic unit consolidates repeated prose while preserving separately highlighted third- and fourth-lecture sources.

### 15.9 Knowledge-platform behavior

* The same approved claim can support a passage unit, topic unit, search result, and QA answer without duplicating identity or source citations.
* A question whose only matching fragment is an opposed view does not return that view as Dr. Wang's position.
* A significant question with no approved answering claim is returned as unanswered or evidence insufficient.
* Original-language results distinguish faithful representation from independent fact-check status.
* The “因信成义，而非因信称义” result preserves Dr. Wang's wording, links the `δικαιόω` judgment to its theological claim and opposed view, and does not normalize it back to conventional terminology.
* Public QA cannot retrieve candidate claims or restricted source text.
* Internal research QA clearly labels candidates, editorial synthesis, tension, and fact-check-pending material.
* A thought node can be split or superseded without deleting its prior revision or source lineage.
* One new out-of-sample sermon can create a new thought trunk rather than being forced into the current seven candidates.

### 15.10 Editorial-capacity behavior

* A deliverable can publish when its minimum subgraph passes even if unrelated records from the same sermons remain candidate.
* A required supporting or qualifying relation cannot be deferred while its dependent conclusion remains in the deliverable.
* The review queue explains which deliverable each blocking item affects.
* Candidate and source-anchored records remain internal and visibly labeled.
* Public pages distinguish “not yet organized” from “Dr. Wang did not teach this.”
* The first passage and topic deliverables report actual review minutes, acceptance, rejection, deferral, rework, weekly capacity, and projected backlog.

## 16. Rollout Sequence

> Not normative. A rollout order, not a requirement. The repository technical specification carries its own phase list; neither overrides the rules above.

1. Build source registry and source maps.
2. Build citation records and validation.
3. Implement transcript and notes source readers.
4. Implement canonical unit APIs and editorial citation review.
5. Implement repository Bible, topic, unit, and local relationship views.
6. Migrate and approve the three-unit pilot.
7. Migrate Matthew 1–17 and validate coverage.
8. Extend the workflow incrementally to the full sermon corpus.
9. Add repository-wide question, claim, EvidenceStep, InferenceBridge, relation, Scripture/external-evidence, original-language, application, passage-chain, and revision records.
10. Add Publication Profile, Composition Plan, and Composition Decision records and migrate the Matthew 17 blueprint as the first plan.
11. Freeze the 205-sermon candidate survey, the reviewed 17-group structural decisions, and candidate baseline v3; create review scopes for the first passage and topic deliverables, and import/review only each complete dependency closure.
12. Generate and review the Matthew 17 passage work from the approved plan; record review time, outcomes, rework, blockers, and deferred candidates.
13. Use the measured pilot capacity to set the next deliverable queue and a realistic release cadence before expanding review volume.
14. Upgrade search/QA to hybrid retrieval plus reviewed graph traversal, using a separate review scope for every public answer class or saved answer bundle.
15. Validate one unseen published sermon against passage, topic, composition, original-language, QA, and editorial-capacity scenarios before wider migration.
