# Functional Specification: Sermon Manuscript Search and QA Module

## 1. Purpose

The Sermon Manuscript Search and QA module provides a Perplexity-like question answering experience over generated sermon manuscript documents. It lets users ask Chinese natural-language questions about Dr. Wang's `馬太福音釋經` manuscripts and receive a synthesized answer with visible sources, citations, and supporting excerpts.

The module is part of the `notes_to_manuscript_series` resource experience. It is not a replacement for browsing sermon manuscripts; it is a research and discovery layer that helps users locate relevant teaching across canonical passages and theological topics.

## 2. Scope

In scope:

- Search and QA over local Markdown sermon manuscript documents.
- Normal and deep search modes.
- Streaming answers with sources shown before the final answer is complete.
- Canonical Bible passage retrieval, theological topic retrieval, keyword retrieval, and semantic retrieval.
- Coverage questions, such as which verses are covered by a chapter or document group.
- Source cards, numbered citations, supporting quotes, and related questions.
- Index status and reindexing APIs.

Out of scope for the current version:

- Multi-turn conversational memory.
- User feedback/rating workflows.
- Admin UI for index management.
- Answer caching.
- Custom reranker service.
- Editing source manuscripts from the search UI.

## 3. Users

**Reader**

A church member, student, or researcher who wants to understand Dr. Wang's teaching on a passage or topic without manually opening many manuscript files.

**Pastor or Editor**

A user preparing a sermon, lesson, or manuscript who needs to find where a topic, verse, or theological idea is discussed across the corpus.

**Operator**

A technical user who manages the local index, embedding configuration, backend service, and production deployment.

## 4. Corpus Model

The corpus consists of generated sermon manuscript Markdown files. The current primary corpus is `馬太福音釋經`, but the design must support additional series later.

The corpus has two organizing axes:

- **Canonical passage axis**: Matthew passages and cross references, such as `太 16:19` or `以賽亞書 54:5-6`.
- **Theological-topic axis**: topics and themes discussed in Dr. Wang's teaching, such as `耶和華的僕人`, `彌賽亞`, `國度`, `教會`, `靈魂體`, or `捨己`.

Important corpus assumptions:

- Not every chunk has a direct canonical passage.
- Topic/theme is a first-class retrieval dimension, not a secondary display label.
- A document can cover a Matthew passage while also discussing theological themes and cross references from other biblical books.
- Cross references must be indexed and searchable without being treated as the document's primary Matthew scope.
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
- Use canonical reference extraction, topic extraction, full-text search, and semantic vector search when embeddings are available.
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
- Support search tools such as document lookup, document coverage, and multi-index search.
- Iterate when evidence is insufficient.
- Return a search trace explaining tool usage and rounds.

Target use cases:

- Broad theological synthesis.
- Ambiguous or multi-part questions.
- Questions that require comparing multiple documents or axes.

## 8. Retrieval Requirements

The retriever must combine multiple retrieval signals:

- Canonical Bible references from the user query.
- Document scope references.
- Primary passage references.
- Cross references.
- Topic tags and theme terms.
- Full-text search over manuscript content.
- Semantic embeddings when enabled.
- Series, project type, topic, canonical reference, and content type filters.

The retriever must not assume every source unit has a passage. Chunks without canonical references remain eligible through topic, keyword, and semantic search.

For chapter/document coverage questions:

- The system must search document groups by document title and chapter number.
- Multiple matching documents must be aggregated.
- Direct coverage must be constrained to the requested canonical prefix when the user asks about a specific chapter.
- Cross references should be reported separately from direct coverage.

## 9. Answer Generation

The answer generator uses the selected evidence as the grounding context.

Requirements:

- The answer must be grounded in returned source units.
- The answer should cite claims with numbered citations when possible.
- The answer should prefer Dr. Wang's manuscript content over general model knowledge.
- If the LLM provider fails, the system should return a useful extractive fallback instead of a blank response.
- Coverage answers may be generated deterministically from index metadata when possible.

Initial model choice:

- DeepSeek API is the default answer synthesis provider.
- The LLM API key is server-side only and must never be exposed to the browser.

## 10. Indexing and Storage

The index is a local SQLite database.

Default index path:

```text
<data_base>/sermon_search/sermon_search.sqlite3
```

Indexed entities:

- Documents.
- Source units/chunks.
- Canonical references.
- Topic tags.
- Full-text search rows.
- Optional embedding rows.

Indexing requirements:

- Discover Markdown manuscripts from the notes-to-manuscript data tree.
- Preserve series, lecture, project, document title, Google Doc ID, source path, content hash, and modification time.
- Parse headings into source unit heading paths.
- Extract primary passage references, cross references, document scope references, topics, terms, and content types.
- Support reindexing with or without embeddings.
- Rebuilds should avoid leaving a corrupted partial index as the active index.

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
- `POST /sermon_search/query`
  - Runs a non-streaming search and returns the full response.
- `POST /sermon_search/query_stream`
  - Runs streaming search using request body JSON.
- `GET /sermon_search/query_stream?payload=...`
  - Streaming endpoint compatible with browser `EventSource`.
- `GET /semantic_search/{q}`
  - Compatibility endpoint returning source cards.

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
    "content_types": []
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
- `done`: citations and related questions are ready.

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

## 14. Observability

Each search response includes a search trace.

The trace should support debugging:

- Selected mode.
- Number of search rounds.
- Tools used.
- Query variants.
- Candidate counts and selected counts.
- Notes about fallbacks or deterministic handling.

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

### Streaming

Given a slow LLM response:

- The browser shows source results before the final answer is complete.
- Answer text appears incrementally through streaming events.

## 16. Future Enhancements

Potential future work:

- Server-side answer cache for repeated questions.
- Admin UI for status, reindex, and embedding health.
- User-visible deep search trace.
- Feedback controls for answer quality and bad citations.
- Optional reranker model.
- Alternative embedding providers, including BGE-M3 hybrid retrieval.
- Multi-turn follow-up questions scoped to prior sources.
