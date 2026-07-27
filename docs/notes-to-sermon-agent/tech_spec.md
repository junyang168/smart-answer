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
*   `coverage_audit_stale`: whether the draft changed after the last Coverage Audit
*   `audit_passed`: whether the current Coverage Audit passed
*   `theological_audit_completed`: whether every final chunk has an executable audit result
*   `theological_audit_passed`: whether every final chunk completed with zero findings; informational and not the Check In gate

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

### 4.5. Model Configuration

Transcript generation and both transcript review paths currently resolve to:

```text
OPENAI_GENERATION_MODEL=gpt-5.6-sol
```

Theological review does not currently have a separate model setting. It calls `generate_structured_json()` without a model override and therefore uses the shared `OPENAI_GENERATION_MODEL` value.

## 5. Security & Performance
*   **Concurrency**: Uses FastAPI `BackgroundTasks`. Not scalable horizontally (state is local file-based), but sufficient for single-tenant use.
*   **Rate Limits**: Bound by Vertex AI quotas. Retry logic handles basic transient 429s.
