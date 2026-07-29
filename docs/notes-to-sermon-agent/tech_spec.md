# Technical Specification: Notes to Sermon Transformation System

## 1. System Architecture
The system follows a monolithic architecture with a clear separation between the frontend (Next.js) and backend (FastAPI/Python). The core logic resides in the backend, specifically in the `multi_agent` package.

### 1.1. High-Level Components
*   **Web Client**: Next.js 13+ App Router. Handles UI state polling and rendering.
*   **API Layer**: FastAPI routers (`sermon_converter_router.py`). Exposes endpoints for triggering generation and retrieving status.
*   **Orchestrator**: `backend/api/multi_agent/orchestrator.py`. The "brain" that manages the state machine and invokes agents appropriately.
*   **Agents Module**: `backend/api/multi_agent/agents.py`. Contains the specific prompt logic and LLM calls for each persona.
*   **LLM Gateway**: `GeminiClient` wrapping `google-genai` SDK (V1). Connects to Vertex AI (Gemini 3 Pro).
*   **Persistence**: Local filesystem storage (JSON artifacts).
*   **Transcript Pipeline**: `backend/pipeline/transcript_pipeline.py`. Implements full-transcript evidence extraction, planning, generation, and coverage auditing.
*   **OpenAI Gateway**: Transcript generation, Coverage Audit, and Theological Boundary Review use structured OpenAI responses. The current shared model is `gpt-5.6-sol`, configured by `OPENAI_GENERATION_MODEL`.
*   **Series Index Refresh**: `backend/api/series_index_refresh.py`. Runs cache-aware topic extraction for one Series and then rebuilds the global manuscript search index.

## 2. Data Models & Schemas

### 2.1. Agent State
Persisted in `notes_to_surmon/{project_id}/agent_state.json`.

```python
class AgentState(BaseModel):
    project_id: str
    
    # Context (Immutable after init)
    sermon_series_title: str
    sermon_series_description: str
    lecture_title: str
    lecture_description: str
    source_notes: str # The unified markdown of raw notes
    
    # Artifacts (Mutable)
    exegetical_notes: Optional[str] = None
    theological_analysis: Optional[str] = None
    illustration_ideas: Optional[str] = None
    beats: Optional[List[str]] = None      # The structure/plan
    draft_chunks: List[str] = Field(default_factory=list) # Progress so far
    full_manuscript: Optional[str] = None  # Final result
```

### 2.2. Agent Logs
Persisted in `notes_to_surmon/{project_id}/agent_logs.json`.
Structure: `List[Dict[str, str]]`
```json
[
  {
    "timestamp": "ISO-8601 String",
    "role": "exegete | theologian | illustrator ...",
    "message": "Human-readable log message"
  }
]
```

### 2.3. Project Metadata (`meta.json`)
Tracks the overall status of the project, including non-agent metadata.
*   `processing`: boolean (Is the system running?)
*   `processing_status`: string (e.g., "Drafting Part 2/5")
*   `processing_progress`: integer (0-100)
*   `processing_error`: optional string

Transcript projects additionally use:

*   `project_type`: `"transcript"`
*   `storage_root`: `"transcripts_to_manuscript"`
*   `series_id` / `lecture_id`: bidirectional Series and Lecture association
*   `bible_verse`: optional editor-supplied scripture-scope hint; transcript passage indexing can derive scope from manuscript content when this is empty
*   `sermon_transcript_id`: exact source transcript identifier, stored without `.json`
*   `sermon_transcript_source_stage`: stage used by the last import (`published`, `reviewed`, or `raw`)
*   `sermon_transcript_imported_at`: UTC timestamp of the last Unified Input import
*   `sermon_transcript_source_sha256`: hash of the imported transcript text for provenance
*   `coverage_audit_stale`: whether the draft changed after the last Coverage Audit
*   `audit_passed`: whether the current Coverage Audit passed
*   `theological_audit_completed`: whether every final chunk has an executable audit result
*   `theological_audit_passed`: whether every final chunk completed with zero findings; informational and not the Check In gate
*   `theological_review_stale`: an integration patch changed the Draft after an older final review copy was created; the UI must offer **Restart Theological Review** and Check In remains disabled

### 2.4. Transcript Project Storage

Canonical transcript project files live at:

```text
data/transcripts_to_manuscript/{project_id}/
```

A compatibility symlink exists at:

```text
data/notes_to_surmon/{project_id}
  -> data/transcripts_to_manuscript/{project_id}
```

This preserves existing notes-to-sermon routes while keeping transcript manuscripts in their dedicated root. The project ID must also appear in the assigned Lecture's `project_ids` array in `notes_to_surmon/series_db.json`; project metadata and Series metadata form a bidirectional association.

### 2.5. Transcript Artifacts

* `unified_source.md`: reviewed sermon transcript
* `evidence_inventory.json`: complete evidence inventory with source ranges
* `manuscript_plan.json`: ordered logical units and evidence assignments
* `transcript_generated_units/Uxxx.json`: resumable generation artifacts
* `draft_v1.md`: editor-authoritative manuscript draft
* `draft_chunks_meta.json` / `draft_chunks/*.md`: editable logical units with evidence lineage
* `coverage_audit.json`: whole-document Coverage Audit result
* `final.md`: final review copy
* `chunks_meta.json` / `chunks/*.md`: final theological-review chunks
* `theological_audit.json`: per-final-chunk theological results

Each evidence item includes `scripture_refs` and a structured `scripture_presentations` array. Every presentation records:

* `reference`: the reader-facing compact citation;
* `mode`: `direct_quote`, `paraphrase`, or `reference_only`;
* `quoted_text`: an exact source-transcript substring for `direct_quote`, otherwise `null`; and
* `role`: what the cited passage proves or explains in Dr. Wang's argument.

Evidence validation rejects a direct quotation not found verbatim in its declared transcript source range. Unit generation requires the reference and requires every direct quotation to appear inside a Markdown `>` blockquote. The deterministic whole-document check contributes `tone_or_format` findings to `coverage_audit.json`; the Stage 1 UI displays those findings as targeted, read-only correction proposals.

## 3. API Design

### 3.1. Trigger Generation
**POST** `/api/admin/notes-to-sermon/sermon-project/{id}/generate-draft`
*   **Payload**: `{ "use_mas": true, "restart": boolean }`
*   **Behavior**:
    *   If `restart=true`: Calls `reset_agent_state` (deletes JSONs).
    *   Starts `process_project_with_mas` as a Background Task.
    *   Returns 202 Accepted immediately.

### 3.2. Status Polling
**GET** `/api/admin/notes-to-sermon/sermon-project/{id}/agent-logs`
*   **Returns**: Consolidated list of logs from both legacy and new paths.

**GET** `/api/admin/notes-to-sermon/sermon-project/{id}/agent-state`
*   **Returns**: The full `AgentState` JSON object. Used by frontend to render output artifacts.

### 3.3. Transcript Pipeline Endpoints

* **GET** `/admin/notes-to-sermon/sermon-transcript?transcript_id={id}`
  * Validates an exact ID and reports the selected workflow stage and character count.
  * Resolution order is `script_published`, `script_review`, then `script_patched`.
* **POST** `/admin/notes-to-sermon/sermon-project/{id}/import-sermon-transcript`
  * Payload: `{ "transcript_id": "...", "overwrite": false }`.
  * Imports non-comment transcript paragraphs into `unified_source.md`, preserving reviewed subtitle Markdown.
  * Returns `409 Conflict` when meaningful Unified Input exists and `overwrite` is false.
  * Records source provenance and invalidates prior Coverage/theological-review status.
* **POST** `/admin/notes-to-sermon/sermon-project`
  * Transcript creation additionally accepts `sermon_transcript_id` and `import_sermon_transcript`.
* **POST** `/admin/notes-to-sermon/sermon-project/{id}/metadata`
  * Accepts `sermon_transcript_id`; updating this field links the transcript but does not import or overwrite Unified Input.

* **POST** `/admin/notes-to-sermon/sermon-project/{id}/stage1/analyze`
  * Builds or refreshes the evidence inventory and logical manuscript plan.
* **POST** `/admin/notes-to-sermon/sermon-project/{id}/stage1/generate-all`
  * Generates all planned units and combines the initial draft.
* **POST** `/admin/notes-to-sermon/sermon-project/{id}/stage1/audit`
  * Runs Coverage Audit against the existing human-edited `draft_v1.md`.
  * This mode must not save `summary.combined_markdown` back to the draft.
* **POST** `/admin/notes-to-sermon/sermon-project/{id}/start-review`
  * Creates/synchronizes `final.md` and final review chunks; resets theological review state.
* **POST** `/admin/notes-to-sermon/sermon-project/{id}/theological-audit`
  * Runs structured theological boundary review for one final chunk.
* **POST** `/admin/notes-to-sermon/sermon-project/{id}/check-in`
  * For transcript projects, rejects the request unless `theological_audit_completed == true`.

### 3.4. Series Index Refresh Endpoints

* **POST** `/admin/notes-to-sermon/series/{series_id}/index-refresh`
  * Returns `202 Accepted` and queues a background refresh.
  * Rejects concurrent refreshes with `409 Conflict` because topic-index output and search-index storage are global.
* **GET** `/admin/notes-to-sermon/series/{series_id}/index-refresh`
  * Returns the in-process state: `idle`, `queued`, `running`, `completed`, or `failed`.
  * Completed results include topic count, indexed document count, and source-unit count.

### 3.5. Series Continuity Endpoints

* **POST** `/admin/notes-to-sermon/series/{series_id}/continuity`
  * Payload: `{ "project_id": "..." }`.
  * Requires a transcript Project assigned to the selected Series.
  * Returns `202 Accepted` and runs content-based comparison as a background task.
  * The first release creates a review proposal only; it does not edit Project or Series manuscript text.
* **GET** `/admin/notes-to-sermon/series/{series_id}/continuity/{project_id}`
  * Returns `idle`, `queued`, `running`, `completed`, or `failed`.
  * A completed response includes the latest persisted Merge Proposal.

### 3.6. Series Draft Endpoints

* **POST** `/admin/notes-to-sermon/series/{series_id}/series-draft`
  * Payload: `{ "project_id": "...", "proposal_id": "..." }`.
  * Approves the exact persisted proposal and queues the Series Draft build.
  * Rejects stale proposals when either the current evidence inventory or a referenced earlier `final.md` changed after proposal review.
* **GET** `/admin/notes-to-sermon/series/{series_id}/series-draft/{project_id}`
  * Returns background build state and completed unit/evidence counts.
* **GET** `/admin/notes-to-sermon/series/{series_id}/series-draft`
  * Returns the current review-only Series Draft Markdown.

### 3.7. Integrated Manuscript Application Endpoints

* **POST** `/admin/notes-to-sermon/series/{series_id}/integrated-manuscript`
  * Payload: `{ "project_id": "...", "proposal_id": "..." }`.
  * Materializes only new main/appendix units into the current transcript Project draft.
  * Saves changed earlier units as review-only patches and rejects stale target manuscripts or a human-edited current draft.
* **GET** `/admin/notes-to-sermon/series/{series_id}/integrated-manuscript/{project_id}`
  * Returns current application status, local-unit and evidence counts, and each earlier-unit patch's `safe`, `applied`, or `conflict` state.
* **POST** `/admin/notes-to-sermon/series/{series_id}/integrated-manuscript/apply-patches`
  * Payload: `{ "project_id": "...", "application_id": "..." }`.
  * Applies every currently safe replacement to the target Project's `draft_v1.md`, never to `final.md`.
  * Leaves patches with human Draft edits or a changed/missing reviewed baseline untouched and reports them as conflicts requiring manual merge.
  * For a transcript target, writes a deterministic patch Coverage pass only when reversing all applied patches reconstructs the complete reviewed `final.md`; otherwise leaves Coverage stale.
  * Marks a successfully certified transcript target's final review copy stale so the editor must explicitly restart theological review from the updated Draft.
  * Notes targets and uncertified transcript targets follow their normal audit and check-in workflow.

## 4. Implementation Details

### 4.1. Orchestration Logic (`process_project_with_mas`)
The orchestrator uses a **State Machine** pattern with **Checkpointing**:
1.  **Load State**: Tries to read `agent_state.json`. If missing, initializes new state from project source.
2.  **Phase 1 (Research)**: Checks if `exegetical_notes` is null. If so, runs Exegete and saves state.
3.  **Phase 2 (Enrichment)**: Sequentially runs Theologian and Illustrator if their fields are null.
4.  **Phase 3 (Structure)**: Runs Structuring Specialist to populate `state.beats`.
5.  **Phase 4 (Drafting Loop)**:
    *   Iterates through `state.beats`.
    *   Skips beats already present in `state.draft_chunks` (Resume logic).
    *   For each new beat:
        *    Calls `Drafter` with context (previous text + current beat).
        *   Calls `Critic` to valid.
        *   If Critic fails, retry loop (up to 3 times).
        *   **Save State** after each successful chunk.

### 4.2. Beat Visualization
*   **Backend**: `identify_beats` uses a specialized LLM prompt (JSON mode) to find split points in the source markdown. It employs a "Dual-Anchor" strategy (finding text before/after the split) to be robust against minor OCR errors.
*   **Frontend**: `ScriptureMarkdown` component parses the markdown string. It detects `> [!NOTE]` syntax to render collapsible cards for each beat.

### 4.3. Transcript State Transitions

```text
Transcript saved
  -> Analyze transcript
  -> Evidence inventory + manuscript plan
  -> Generate manuscript
  -> Draft editing
  -> Coverage Audit passes
  -> Start Theological Review
  -> Audit every final chunk
  -> Check In
```

State invalidation rules:

* Saving a transcript draft chunk sets `coverage_audit_stale = true` and `audit_passed = false`.
* Coverage Audit reads the current `draft_v1.md`; it never reconstructs the draft from generated units.
* Generated units may bootstrap draft chunks only when no draft chunk bundle exists. File modification times must not be used to overwrite later human edits.
* Starting theological review resets both theological status fields.
* Saving a final chunk removes that chunk's previous theological result and sets both theological status fields to false.
* After each theological audit, the backend recomputes:
  * `theological_audit_completed`: all final chunks have valid results;
  * `theological_audit_passed`: all valid results contain zero issues.
* Check In uses `theological_audit_completed`, not `theological_audit_passed`, because findings are advisory and require human judgment.

### 4.4. Audit Responsibilities

| Audit | Input scope | Purpose | Workflow effect |
|---|---|---|---|
| Coverage Audit | Complete transcript, evidence inventory, plan, and current draft | Fidelity, completeness, logic, classification, and format | Must pass before final review |
| Theological Boundary Review | One final Review Chunk | High-confidence major exegetical/theological boundary findings | Every chunk must be reviewed; findings remain advisory |
| Fidelity Audit | Notes project source and draft chunks | Legacy notes-to-sermon source fidelity | Hidden for transcript projects |

For Scripture formatting, Coverage Audit supplements semantic model review with deterministic checks. It verifies that each structured reference appears and that every `direct_quote` appears in a Markdown blockquote. A failure identifies the logical unit and Evidence ID; it does not rewrite `draft_v1.md`.

A presentation-only migration of a previously reviewed transcript manuscript follows a narrower state transition:

```text
verify quoted text against unified_source.md
  -> rewrite citation presentation in draft_v1.md
  -> rebuild draft chunks
  -> synchronize final.md and final review chunks
  -> preserve audit_passed=true and coverage_audit_stale=false
  -> reset theological_audit_completed/theological_audit_passed
```

This transition is valid only for formatting-preserving migrations. Ordinary Draft editing continues to invalidate Coverage because the system cannot assume that a general edit is non-substantive.

### 4.5. Model Configuration

Transcript generation and both transcript review paths currently resolve to:

```text
OPENAI_GENERATION_MODEL=gpt-5.6-sol
```

Theological review does not currently have a separate model setting. It calls `generate_structured_json()` without a model override and therefore uses the shared `OPENAI_GENERATION_MODEL` value.

### 4.6. Topic and Search Index Refresh

The refresh pipeline has two stages:

```text
Selected Series manuscripts
  -> cache-aware topic extraction
  -> global topic_index.json merge
  -> global SQLite manuscript search rebuild
```

Implementation rules:

* `discover_manuscripts()` selects the requested Series and filters each Project using its own `project_type`. A `sermon_note` Series may legitimately contain both `sermon_note` and `transcript` Projects.
* Default indexing includes both supported Project types.
* Notes projects without `bible_verse` retain the legacy overview/structural behavior and do not produce passage topics.
* Transcript projects without `bible_verse` instruct the extractor to determine sustained Matthew exegesis from `final.md`. The Project title is never used to infer a chapter.
* An explicit transcript `bible_verse` remains a scope hint/override for the extractor.
* Passage-topic recovery uses references in the corresponding manuscript section when the model returns a thematic passage name without a Matthew reference prefix.
* Per-project topic cache records `content_hash`, model, and `bible_verse`. A legacy transcript cache or a cache created under a different scope is not reusable.
* Existing topic caches for unchanged Projects are reused, so a Series refresh normally calls the topic-extraction model only for new, changed, or scope-invalidated manuscripts.
* Search reindexing preserves embeddings when the current production index has embeddings enabled.

Authoritative index paths:

```text
data/sermon_search/topic_index.json
data/sermon_search/cache/{project_id}.json
data/sermon_search/sermon_search.sqlite3
```

The Series admin page polls the status endpoint every two seconds while the job is queued or running.

### 4.7. Series Continuity Analysis

Implementation lives in `backend/api/series_manuscript_service.py` and uses `gpt-5.6-sol` through the existing structured-output client.

Processing order:

```text
Current Project evidence_inventory.json
  -> resolve earlier Projects from Series/Lecture ordering
  -> read only earlier Projects that have final.md
  -> split prior manuscripts into stable heading-based sections
  -> content and Scripture candidate retrieval
  -> structured semantic relationship classification
  -> deterministic evidence-assignment validation
  -> persisted Merge Proposal
```

The Series and Lecture order limits which Projects are earlier; it does not determine whether content is duplicated. Duplicate decisions are made from actual evidence and candidate manuscript text.

The structured proposal requires:

* every current evidence ID exactly once;
* no unknown prior section references;
* a relationship, recommended action, new contribution, reason, and confidence for every decision; and
* an empty `unassigned_evidence_ids` list.

Read-only proposal artifacts are stored at:

```text
data/series_manuscripts/{series_id}/manifest.json
data/series_manuscripts/{series_id}/merge_runs/{proposal_id}/proposal.json
```

Prior section IDs are stable hashes of Project ID, heading path, and section ordinal. Proposal source snapshots record the current evidence hash and prior manuscript hashes so later apply/merge work can reject stale proposals.

### 4.8. Approved Proposal to Series Draft

Implementation lives in `backend/api/series_manuscript_builder.py`. The builder parses earlier checked-in manuscripts into canonical units at `##` headings, resolves every approved decision to an operation, and regenerates only affected units.

```text
Approved Merge Proposal
  -> validate evidence and prior-manuscript snapshots
  -> parse prior final.md files into canonical units
  -> group extensions/corrections by existing unit
  -> generate changed and new units with gpt-5.6-sol
  -> verify every new evidence ID is covered by its operation
  -> verify every evidence ID has exactly one final disposition
  -> save review-only Series Draft and registries
```

Artifacts:

```text
data/series_manuscripts/{series_id}/canonical_plan.json
data/series_manuscripts/{series_id}/evidence_registry.json
data/series_manuscripts/{series_id}/draft.md
data/series_manuscripts/{series_id}/merge_runs/{proposal_id}/build.json
```

`canonical_plan.json` preserves stable canonical unit IDs, source Project provenance, hashes, and change summaries. `evidence_registry.json` records whether each evidence item was merged, newly represented, represented by an existing unit, or omitted as non-substantive. The builder does not write to `data/notes_to_sermon/{project_id}/final.md` and does not invoke index refresh or publication.

New main-text decisions that share Scripture references are grouped into one logical operation before generation. A reference-free related question is attached to the nearest Scripture-grounded new unit. This prevents separate proposal decisions about narrative, theology, and application from becoming repetitive reader-facing units. Operation results are content-addressed by the approved decision, evidence, existing-unit hash, prompt, and model, so an interrupted or editorially regrouped rebuild can reuse unaffected operations safely.

### 4.9. Integration Application and Project Draft

Implementation lives in `backend/api/series_manuscript_application.py`.

```text
Reviewed integration changes
  -> revalidate every earlier target final.md hash
  -> compose current Project draft from new + appendix units
  -> persist earlier-unit replacements as pending patches
  -> persist all evidence dispositions in integration_application.json
  -> create editable draft chunks with integrated evidence lineage
  -> write Integration Coverage Check (pass)
  -> reset Theological Review
```

Applying reviewed earlier-unit patches is a separate, recoverable step:

```text
Pending replacement patch
  -> verify target final.md still has the reviewed file and unit hashes
  -> verify the same unit in draft_v1.md has no human divergence
  -> safe: replace only that H2 unit in draft_v1.md
  -> conflict: preserve the Draft unchanged and require manual merge
  -> transcript target with exact reconstructed baseline: write patch Coverage pass
  -> other target or failed reconstruction: invalidate target audit
  -> mark any certified transcript review copy stale; never write final.md
```

Artifacts:

```text
data/transcripts_to_manuscript/{project_id}/draft_v1.md
data/transcripts_to_manuscript/{project_id}/integration_application.json
data/series_manuscripts/{series_id}/applications/{application_id}/application.json
data/series_manuscripts/{series_id}/applications/{application_id}/patches/{canonical_unit_id}.md
```

For an integrated transcript, Coverage Audit reads both `draft_v1.md` and `integration_application.json`. Evidence assigned to a verified pending patch or an earlier canonical unit is not treated as missing from the current Project draft, but the auditor still checks whether the recorded destination actually carries the evidence. Standalone transcript generation is rejected while an approved Integration Application is active.

Materialization also writes `coverage_audit.json` with `audit_kind: integration_coverage_check` and `overall_status: pass`, then clears `coverage_audit_stale`. This lets the editor proceed directly to Theological Review. Any later Draft Chunk edit uses the normal invalidation rule, marks coverage stale again, and requires a fresh audit before review can continue.

Patch application records per-unit results in `integration_application.json`. Applied results are idempotent: later status reads retain `applied` rather than attempting the replacement again. Safe patches are grouped by target Project and saved once, so multiple changes to one earlier manuscript do not repeatedly rebuild its chunks. The target's published `final.md` remains the immutable reviewed baseline throughout the operation.

For transcript patch targets, `series_manuscript_application.py` reverses the applied units in memory and requires the reconstructed full Draft to equal the reviewed `final.md`. On success it writes `coverage_audit.json` with `audit_kind: integration_patch_coverage_check`, marks Coverage passed, and sets `theological_review_stale: true`. The editor UI then offers **Restart Theological Review**. `start_theological_review()` replaces `final.md` and its chunk bundle from the updated Draft only under this explicit stale-review state, clears the flag, and resets the theological audit. Check In remains disabled until every new review chunk has been completed.

## 5. Security & Performance
*   **Concurrency**: Uses FastAPI `BackgroundTasks`. Not scalable horizontally (state is local file-based), but sufficient for single-tenant use.
*   **Rate Limits**: Bound by Vertex AI quotas. Retry logic handles basic transient 429s.
*   **Index Refresh State**: Refresh status and the global concurrency guard are in process. A backend restart clears visible job status; index files and completed cache artifacts remain on disk.
