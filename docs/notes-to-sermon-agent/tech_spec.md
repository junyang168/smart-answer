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

## 5. Security & Performance
*   **Concurrency**: Uses FastAPI `BackgroundTasks`. Not scalable horizontally (state is local file-based), but sufficient for single-tenant use.
*   **Rate Limits**: Bound by Vertex AI quotas. Retry logic handles basic transient 429s.
