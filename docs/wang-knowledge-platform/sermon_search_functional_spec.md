# Functional Specification: Knowledge-Grounded Sermon Search and QA

## 1. Purpose

The Knowledge-Grounded Sermon Search and QA module lets users ask Chinese natural-language questions about Dr. Wang's biblical interpretation and teaching. It returns a direct answer whose attribution, reasoning path, qualifications, and exact original sources can be inspected.

The module is not merely retrieval over generated articles. It combines raw and reviewed source material, generated manuscripts, reviewed canonical units, and the reviewed claim/argument layer. Manuscript search remains an important recall and fallback mechanism while the corpus is being curated, but a fluent manuscript paragraph is not itself proof that a proposition is the professor's explicit view.

The Exegesis and Topic Repository is the reviewed navigation and provenance layer over the same corpus. Search may retrieve repository canonical units and must return repository citation IDs when exact original-source citations are available. The repository requirements are defined in [Functional Specification: Exegesis and Topic Repository](./exegesis_topic_repository_functional_spec.md).

The common knowledge objects, attribution rules, product projections, and evolution policy are defined in [Knowledge Platform Design](./knowledge_platform_design.md). This specification defines the search and QA projection of that platform.

## 2. Scope

In scope:

- Search and QA over manuscripts plus reviewed questions, claims, relations, Scripture evidence, original-language judgments, canonical units, and exact source citations.
- Normal and deep search modes.
- Streaming answers with sources shown before the final answer is complete.
- Canonical Bible passage retrieval, theological topic retrieval, keyword retrieval, and semantic retrieval.
- Coverage questions, such as which verses are covered by a chapter or document group.
- Source cards, numbered citations, supporting quotes, and related questions.
- Passage explanation, cross-sermon topic synthesis, original-language and translation questions, teaching-development comparison, and source-location questions.
- Public and authenticated internal-research evidence scopes.
- Links from reviewed repository results to exact highlighted sermon or notes fragments.
- Index status and reindexing APIs.
- Series-admin Refresh Index control with background progress status.

Out of scope for the current version:

- Long-lived multi-turn conversational memory.
- User feedback/rating workflows.
- Answer caching.
- Custom reranker service.
- Editing source manuscripts from the search UI.
- Treating search-result snippets as reviewed repository citations when no approved citation record exists.
- Using general model knowledge to fill gaps in what the professor taught.
- Independent historical, linguistic, or theological fact checking; that is a separately labeled stage.

## 3. Users

**Reader**

A church member, student, or researcher who wants to understand Dr. Wang's teaching on a passage or topic without manually opening many manuscript files.

**Pastor or Editor**

A user preparing a sermon, lesson, or manuscript who needs to find where a topic, verse, or theological idea is discussed across the corpus.

**Operator**

A technical user who manages the local index, embedding configuration, backend service, and production deployment.

## 4. Knowledge and Corpus Model

The searchable foundation has four layers. They are related but must not be collapsed:

1. **Original sources**: sermon audio/video, transcripts, lecture notes, page images, and exact fragments.
2. **Editorial documents**: generated or checked-in manuscripts, including passage lectures and topic essays.
3. **Reviewed knowledge**: questions, claims, claim relations, Scripture evidence, original-language judgments, applications, canonical units, and thought-map revisions.
4. **Compiled search projections**: lexical indexes, semantic vectors, canonical indexes, topic indexes, and permission-filtered graph indexes.

The current primary corpus is `馬太福音釋經`, but the design must support the full 200-plus-sermon corpus and later notes collections.

The corpus has two organizing axes:

- **Canonical passage axis**: Matthew passages and cross references, such as `太 16:19` or `以賽亞書 54:5-6`.
- **Theological-topic axis**: topics and themes discussed in Dr. Wang's teaching, such as `耶和華的僕人`, `彌賽亞`, `國度`, `教會`, `靈魂體`, or `捨己`.

Important corpus assumptions:

- Not every chunk has a direct canonical passage.
- Topic/theme is a first-class retrieval dimension, not a secondary display label.
- A document can cover a Matthew passage while also discussing theological themes and cross references from other biblical books.
- Cross references must be indexed and searchable without being treated as the document's primary Matthew scope.
- A Series may contain both notes and transcript Projects; Project type is evaluated per manuscript.
- Transcript passage scope is derived from sustained exegesis in manuscript content when no explicit `bible_verse` hint exists. Project titles are not scripture evidence.
- Bible and topic navigation should increasingly use reviewed canonical units rather than event/Project order.
- A canonical topic unit may aggregate several original sermon or notes citations while retaining one manuscript.
- One claim may appear in many articles or answers without being copied into separate authoritative records.
- A question may be explicit, editor-reconstructed, answered, partially answered, or unanswered.
- `opposed_view` and `editorial_synthesis` are searchable but must never be presented as the professor's explicit claim.
- Original-language judgments have independent faithful-representation and external fact-check states.
- The thought map is versioned and provisional; new sermons may split, merge, qualify, or supersede its nodes.
- The module must scale to at least 400 manuscript documents.

## 5. Primary Workflows

### 5.1. Topic QA

Example: `什麼是耶和華的僕人？`

Expected behavior:

- Retrieve relevant teaching across topical sections and cross references.
- Include sources that may not be tied to a Matthew passage.
- Synthesize a concise theological answer.
- Render citations as numbered references such as `[1]`, not internal source IDs.
- Show supporting quotes below the answer.

### 5.2. Passage Interpretation

Example: `如何解釋太 16:19？`

Expected behavior:

- Detect the canonical reference.
- Prioritize manuscript chunks whose primary passage or document scope overlaps the requested passage.
- Include relevant topical explanation when the passage is discussed theologically.
- Explain the passage from the sermon manuscript evidence, not from general Bible knowledge alone.

### 5.3. Document or Chapter Coverage

Example: `教授對 16 章釋經都覆蓋了那些 verses？`

Expected behavior:

- Interpret the question as a coverage request.
- Find all matching chapter documents, not only the single highest-ranking document.
- Aggregate verse coverage per matching document.
- Separate direct Matthew 16 coverage from cross references.
- Include documents such as:
  - `16 章 - ...`
  - `16 章 - 彌賽亞的身分、性質與捨己的呼召`
  - `16 章 - 靈、魂、體的整體性`

### 5.4. Example Questions

The UI may present suggested question chips. Selecting a chip runs the same search flow as typing the question manually.

### 5.5. Refresh and Share

When a user runs a search, the page URL stores the query and mode. Refreshing the page reloads the same search state and reruns the query.

### 5.6. Refresh a Series Index

After publishing or editing a manuscript, an Editor can select **Refresh Index** on the Series administration page.

Expected behavior:

- The action runs asynchronously and reports progress or failure.
- New or changed manuscripts receive chapter/topic extraction; unchanged cache entries are reused.
- The global topic index is merged before manuscript search is rebuilt.
- Existing semantic-search capability is preserved.
- Only one refresh runs at a time.
- Successful completion makes newly extracted passage topics available to the public `按章節` view and concept topics available to `按主題`.

### 5.7. Original-Language and Translation QA

Example: `王教授为什么认为和合本这里翻译错了？`

Expected behavior:

- Identify the verse, Hebrew or Greek expression, and translation under discussion.
- Distinguish what the professor explicitly observed from an editor's reconstruction.
- Explain his proposed reading and the conclusions that depend on it.
- Show the exact sermon or notes fragment, with media time or notes page.
- Display faithful-representation status and, when available, separate external fact-check status.
- If no reviewed judgment exists, return manuscript excerpts as provisional search results rather than inventing a linguistic conclusion.

### 5.8. Teaching-Development Comparison

Example: `王教授在不同讲道中怎样发展“人子”的论述？`

Expected behavior:

- Retrieve relevant claims in chronological source order.
- Distinguish repetition, extension, qualification, tension, and supersession.
- Avoid manufacturing a change in position merely because wording differs.
- Cite each stage independently and identify unresolved editorial judgments.

### 5.9. Source-Location Question

Example: `教授在哪些讲道中提到宗主国与附庸国之约？`

Expected behavior:

- Prefer a sourced inventory over a synthesized essay.
- Return sermon or notes titles, exact excerpts, and playable time or page links.
- Group repeated mentions without hiding distinct contexts.

### 5.10. Insufficient Evidence

When the reviewed evidence does not answer the question, the system says so. It may list nearby passages, candidate excerpts, or unanswered questions according to the user's access scope, but it must not convert topical similarity into an answer.

## 6. UI Requirements

The search UI is embedded in the notes-to-manuscript series detail page.

Required controls and states:

- Search input for Chinese natural-language questions.
- Normal/deep mode control.
- Example question chips.
- Loading state while retrieval is running.
- Streaming state after sources arrive and before the answer is complete.
- Error state when the backend request fails.
- Empty state before a search is run.

Required result regions:

- **Answer**: synthesized response rendered as readable Chinese prose.
- **Sources/Search Results**: numbered source cards shown as soon as retrieval finishes.
- **Citations**: inline answer citations displayed as `[1]`, `[2]`, etc.
- **Quotes**: supporting quoted excerpts tied to numbered sources.
- **Related Questions**: follow-up questions returned by the backend when available.
- **Attribution**: whether each substantive statement is the professor's explicit claim, his reasoning conclusion, an opposed view, an editorial synthesis, a pending fact check, or insufficient evidence.
- **Reasoning Path**: the reviewed support, answer, qualification, opposition, extension, or tension relations used to assemble the response.
- **Review State**: visible only where appropriate, but mandatory for internal candidate or unpublished evidence.

When a result belongs to a published canonical unit, its source card also provides **查看原始內容**. That action resolves an approved repository citation and opens the exact highlighted original fragment, including media time or notes page. A normal search excerpt without an approved repository citation remains labeled as a manuscript excerpt and must not masquerade as original-source provenance.

Citation display rules:

- Internal chunk IDs must not appear in user-facing answer text.
- Raw model references such as `(source 35480499140f-0028)` or bare IDs must be normalized to numbered citations.
- Citation numbers must map to visible source cards in stable order.
- If a model omits explicit citation markers, the UI may append or infer citations from returned citation metadata and source order.

## 7. Search Modes

### 7.1. Normal Mode

Normal mode is optimized for interactive use.

Behavior:

- Perform one deterministic retrieval pass.
- Use canonical reference extraction, topic extraction, full-text search, semantic vector search when embeddings are available, reviewed canonical-unit lookup, and bounded claim-graph lookup.
- Skip the LLM planner.
- Use the LLM only for answer synthesis after evidence is selected.
- Stream sources before answer generation finishes.

Target use cases:

- Simple topic questions.
- Single-passage interpretation questions.
- Most user-facing searches.

### 7.2. Deep Mode

Deep mode is optimized for harder research questions.

Behavior:

- Use an agentic planner to decide one or more search actions.
- Support search tools such as document lookup, document coverage, original-language lookup, chronology comparison, claim traversal, and multi-index search.
- Iterate when evidence is insufficient.
- Return a search trace explaining tool usage and rounds.

Target use cases:

- Broad theological synthesis.
- Ambiguous or multi-part questions.
- Questions that require comparing multiple documents or axes.

## 8. Retrieval Requirements

Retrieval is a staged hybrid process. Vector similarity improves recall but cannot establish authorship, truth, review state, or a logical relationship.

### 8.1. Candidate recall

The retriever combines:

- Canonical Bible references from the user query.
- Document scope references.
- Primary passage references.
- Cross references.
- Topic tags and theme terms.
- Full-text search over manuscript content.
- Semantic embeddings when enabled.
- Series, project type, topic, canonical reference, and content type filters.
- Reviewed question aliases and claim wording.
- Canonical unit membership.
- Original-language forms, lemmas, translations, and affected claims.

The retriever must not assume every source unit has a passage. Chunks without canonical references remain eligible through topic, keyword, and semantic search.

### 8.2. Claim and argument selection

After recall, the system:

1. resolves the question intent and Scripture scope;
2. selects candidate reviewed questions and claims;
3. traverses only bounded approved relations relevant to the intent;
4. includes necessary support, answer, qualification, opposition, extension, tension, or supersession context;
5. resolves exact citations and canonical units;
6. applies permissions to every node and edge; and
7. creates an `AnswerEvidenceBundle` before prose generation.

The bundle records selected claim, relation, citation, and unit IDs, attribution labels, unresolved items, access scope, and active knowledge build. The prose model cannot add a new claim to the bundle.

### 8.3. Permission filtering

Public retrieval uses approved public records in the active build only. Internal research retrieval may use candidate, unpublished, and editorial records for authenticated editors, but the response visibly labels every non-approved item. Removing a restricted node also removes relations or conclusions that require it; the system must not leave a dangling inference that reveals restricted material.

For chapter/document coverage questions:

- The system must search document groups by document title and chapter number.
- Multiple matching documents must be aggregated.
- Direct coverage must be constrained to the requested canonical prefix when the user asks about a specific chapter.
- Cross references should be reported separately from direct coverage.

## 9. Answer Generation

The answer generator uses the selected evidence as the grounding context.

Requirements:

- The answer must be grounded in the precomputed `AnswerEvidenceBundle`.
- The answer should cite claims with numbered citations when possible.
- The answer must distinguish explicit teaching, reasoning conclusions, opposed views, editorial synthesis, unresolved questions, and external fact-check status.
- The answer must not use general model knowledge as evidence of Dr. Wang's view.
- The answer may use ordinary language to connect approved claims, but it cannot create an unsupported bridge or erase a recorded tension.
- The answer should state the direct conclusion first, then show the reasoning, Scripture evidence, qualifications, and sources appropriate to the question.
- When the evidence is insufficient, the response must say so explicitly rather than completing the professor's argument for him.
- If the LLM provider fails, the system should return a useful extractive fallback instead of a blank response.
- Coverage answers may be generated deterministically from index metadata when possible.

Provider policy:

- The answer synthesis provider is configurable. Model choice does not change the evidence, attribution, permission, or citation gates.
- The LLM API key is server-side only and must never be exposed to the browser.

## 10. Indexing and Storage

The module uses two related local indexes:

- `topic_index.json` for public chapter/topic navigation;
- SQLite for document, reference, full-text, and optional semantic search.

Default index path:

```text
<data_base>/sermon_search/sermon_search.sqlite3
```

Topic-index paths:

```text
<data_base>/sermon_search/topic_index.json
<data_base>/sermon_search/cache/{project_id}.json
```

Indexed entities:

- Documents.
- Source units/chunks.
- Canonical references.
- Topic tags.
- Full-text search rows.
- Optional embedding rows.
- Canonical repository unit IDs and approved citation IDs when available.
- Reviewed questions and aliases.
- Claims, attribution, maturity, review state, and visibility.
- Claim relations and Scripture-evidence roles.
- Original-language judgments and their two independent review states.
- Active thought-map nodes and revision identity.

Indexing requirements:

- Discover Markdown manuscripts from the notes-to-manuscript data tree.
- Include `sermon_note` and `transcript` Projects by default and filter by Project metadata rather than Series type.
- Preserve series, lecture, project, document title, Google Doc ID, source path, content hash, and modification time.
- Parse headings into source unit heading paths.
- Join indexed manuscript sections to reviewed canonical units without deriving identity from editable titles alone.
- Preserve approved repository citation IDs so search results can open exact original-source fragments.
- Extract primary passage references, cross references, document scope references, topics, terms, and content types.
- Support reindexing with or without embeddings.
- Rebuilds should avoid leaving a corrupted partial index as the active index.
- Transcript topic extraction must derive passage scope from manuscript content when explicit scope metadata is absent.
- Topic-cache validity must account for manuscript content, extraction model, and explicit scripture-scope state.
- Knowledge indexes are compiled from reviewed authoring records and include the active knowledge build ID.
- Search reindexing cannot change editorial claim identity, relation type, review state, or thought-map structure.
- Embeddings for claims and judgments are recall aids only; exact sources and approved relations remain authoritative.

Embedding requirements:

- Embeddings are optional but supported.
- Current production configuration uses Google's embedding model.
- If embeddings are disabled or unavailable, keyword, topic, and passage search must still work.

## 11. API Requirements

Backend API routes:

- `GET /sermon_search/status`
  - Returns index path, document count, source unit count, indexed timestamp, and whether embeddings are enabled.
- `POST /sermon_search/reindex`
  - Rebuilds the index. Accepts series, project type, and embedding options.
- `POST /admin/notes-to-sermon/series/{series_id}/index-refresh`
  - Queues cache-aware topic extraction for the Series followed by global search reindexing.
- `GET /admin/notes-to-sermon/series/{series_id}/index-refresh`
  - Returns background refresh status and completion counts.
- `POST /sermon_search/query`
  - Runs a non-streaming search and returns the full response.
- `POST /sermon_search/query_stream`
  - Runs streaming search using request body JSON.
- `GET /sermon_search/query_stream?payload=...`
  - Streaming endpoint compatible with browser `EventSource`.
- `GET /semantic_search/{q}`
  - Compatibility endpoint returning source cards.
- `GET /canonical-repository/units/{unit_id}`
  - Returns the reviewed unit and approved source-citation summaries.
- `GET /canonical-repository/citations/{citation_id}`
  - Resolves exact original text, context, and media/page positioning; never accepts raw client filesystem paths.
- `POST /canonical-repository/qa/evidence`
  - Builds a permission-filtered AnswerEvidenceBundle before answer synthesis.
- `GET /canonical-repository/claims/{claim_id}`
  - Returns an approved claim with visible direct relations, Scripture evidence, units, and citations.
- `GET /canonical-repository/original-language`
  - Searches reviewed Hebrew/Greek judgments by reference, form, lemma, translation, topic, and review state.

Request shape:

```json
{
  "question": "如何解釋太 16:19？",
  "mode": "normal",
  "filters": {
    "series_ids": [],
    "project_types": [],
    "topics": [],
    "canonical_refs": [],
    "content_types": [],
    "source_stages": [],
    "review_states": [],
    "attribution_types": [],
    "access_scope": "public"
  },
  "top_k": null
}
```

Response shape:

```json
{
  "answer": "...",
  "citations": [],
  "sources": [],
  "related_questions": [],
  "answer_evidence": {
    "bundle_id": "AEB-...",
    "knowledge_build_id": "KB-...",
    "claim_ids": [],
    "relation_ids": [],
    "citation_ids": [],
    "unit_ids": [],
    "attribution_labels": [],
    "unresolved_items": [],
    "access_scope": "public"
  },
  "answer_status": "answered | partial | unanswered | insufficient_evidence",
  "search_trace": {
    "mode": "normal",
    "rounds": 1,
    "tools_used": [],
    "notes": [],
    "round_traces": []
  }
}
```

Streaming events:

- `sources`: source cards and search trace are ready.
- `answer_delta`: incremental answer text.
- `evidence`: permission-filtered AnswerEvidenceBundle and answer status are ready.
- `done`: citations, attribution labels, qualifications, and related questions are ready.

The `sources` event must be flushed immediately and must not wait for answer generation to finish.

## 12. Performance Requirements

Interactive targets:

- Retrieval should normally complete in less than 1 second for a 400-document corpus.
- Sources should become visible in the browser within about 1 second when the backend is healthy.
- First answer text should usually appear within 2-5 seconds.
- Full answer generation may take around 8-12 seconds depending on the LLM provider and question complexity.

The UI must make slow answer synthesis tolerable by showing sources and partial answer text as soon as possible.

## 13. Security and Privacy

Requirements:

- LLM and embedding API keys must be stored only in backend environment configuration.
- API keys must never be sent to the browser.
- The frontend talks to the local backend through the Next.js API proxy.
- When embeddings are enabled, source text is sent to the embedding provider during indexing.
- During answer generation, selected evidence is sent to the configured LLM provider.
- The system should avoid logging secrets.
- Public search must use approved public knowledge records and approved public citations only.
- Internal research mode requires authentication and must not be selectable through an untrusted client flag alone.
- Candidate or restricted records must not leak through answer text, source titles, counts, graph neighbors, related questions, or diagnostic traces.
- Saved answer evidence bundles inherit the most restrictive visibility of their contents.

## 14. Observability

Each search response includes a search trace.

The trace should support debugging:

- Selected mode.
- Number of search rounds.
- Tools used.
- Query variants.
- Candidate counts and selected counts.
- Notes about fallbacks or deterministic handling.
- Active knowledge build ID and access scope.
- Candidate counts by manuscript, canonical unit, claim, and original-language index.
- Traversed relation types and selected evidence IDs.
- Counts removed by permission filtering, without revealing restricted values.
- Unanswered or insufficient-evidence decisions.

The trace is primarily for developers and operators; the UI may show it only in diagnostic contexts.

## 15. Acceptance Criteria

### Topic QA

Given the question `什麼是耶和華的僕人？`:

- The answer explains the concept using manuscript evidence.
- The answer includes numbered citations.
- No internal source IDs are visible in the final rendered answer.
- Source cards and quotes are shown.

### Passage QA

Given the question `如何解釋太 16:19？`:

- The retriever prioritizes Matthew 16 evidence.
- The answer explains the passage from the manuscript evidence.
- Sources are visible before the full answer is complete.

### Chapter Coverage

Given the question `教授對 16 章釋經都覆蓋了那些 verses？`:

- The system recognizes the request as coverage-oriented without relying on a hard-coded phrase list alone.
- The answer aggregates all matching Matthew 16 documents.
- The answer includes coverage from the primary chapter document and related topical Matthew 16 documents.
- Cross references are distinguished from direct Matthew 16 coverage.

### Refresh Persistence

Given a completed search:

- Refreshing the page restores the query from the URL.
- The search reruns or restores equivalent visible results.

### Series Index Refresh

Given a newly checked-in transcript with an empty `bible_verse` field:

- Refresh Index derives passage topics from sustained exegesis in the manuscript body.
- It does not infer a chapter from the Project title.
- Supporting cross-references are not promoted to primary passage topics.
- The new manuscript becomes visible in chapter/topic navigation and searchable QA after the job completes.

### Streaming

Given a source result associated with a published canonical unit:

- The source card identifies the canonical unit.
- **查看原始內容** uses an approved citation ID.
- The source reader highlights the exact original fragment rather than only opening the complete source.
- A result without an approved citation is clearly identified as a manuscript search result.

Given a slow LLM response:

- The browser shows source results before the final answer is complete.
- Answer text appears incrementally through streaming events.

### Attribution Safety

Given a source passage in which the professor describes and rejects another interpretation:

- The rejected interpretation is labeled `opposed_view`.
- It is not summarized as the professor's own claim.
- The professor's response and its support are cited separately.

### Unanswered Question

Given a question raised in a sermon without a reviewed answer:

- The answer status is `unanswered` or `insufficient_evidence`.
- Topically similar manuscript text is not promoted into an answer.
- Internal users may see candidate nearby evidence with its review state; public users do not see unpublished material.

### Original-Language Question

Given a question about a translation the professor criticizes:

- The response identifies the original-language observation, translation under discussion, proposed reading, downstream conclusion, and exact source.
- Faithful-representation state and external fact-check state are not conflated.
- A pending fact check is not displayed as a confirmed linguistic fact.

### Cross-Sermon Comparison

Given a question about how a teaching develops over time:

- Results are ordered by source date when known.
- Repetition, extension, qualification, tension, and supersession are distinguished.
- Different wording alone does not create a false doctrinal change.

### Permission Isolation

Given a public question whose strongest evidence is restricted:

- Restricted claims and citations do not appear in the answer, sources, counts, related questions, or trace.
- The answer reports insufficient public evidence if the remaining public bundle cannot support a response.

### Evidence Reproducibility

Given the same question, filters, access scope, and active knowledge build:

- The system can reproduce the selected AnswerEvidenceBundle independent of prose generation.
- Every numbered citation in the answer belongs to the bundle.
- The answer does not introduce a substantive claim absent from the bundle.

## 16. Future Enhancements

Potential future work:

- Server-side answer cache for repeated questions.
- Expanded admin diagnostics for historical refresh runs and embedding health.
- User-visible deep search trace.
- Feedback controls for answer quality and bad citations.
- Optional reranker model.
- Alternative embedding providers, including BGE-M3 hybrid retrieval.
- Multi-turn follow-up questions scoped to prior sources.

## 17. Delivery Status and Migration Policy

The existing implementation primarily searches manuscript documents and can resolve approved canonical citations when available. The reviewed claim graph, original-language workflow, thought-map revisions, AnswerEvidenceBundle endpoint, and graph-grounded answer generation are target capabilities described by this specification; they are introduced incrementally and must not be represented as complete until implemented and evaluated.

During migration:

1. manuscript retrieval remains available and is visibly labeled as provisional document evidence;
2. approved canonical units and citations take precedence where available;
3. reviewed claims and relations are added sermon by sermon without blocking ordinary search;
4. public graph-grounded synthesis is enabled only after permission and attribution tests pass; and
5. the same user question may initially return a sourced document inventory rather than an unsupported synthesized conclusion.
