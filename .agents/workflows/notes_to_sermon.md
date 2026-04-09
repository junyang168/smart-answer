---
description: Workflow and Architecture of the Notes-to-Sermon Module
---

# Notes to Sermon Workflow

This document is the current source-of-truth for the `notes-to-sermon` module.
It describes the workflow that is actually implemented now, not the older planned flow.


## Objective

The module is an exegesis text-engineering pipeline.

Its purpose is to transform corrected exegesis lecture notes into:
- a stable Stage 1 exegesis manuscript draft
- chunk-level review artifacts
- a reviewed Master Text
- export-ready editorial metadata

This is not a devotional shortcut and not a sermonizer.
The system is designed to preserve exegetical logic, maintain auditability, and keep every phase explicit.


## Functional Flow

### Step 0: Source Preparation

Goal:
- scan notes
- OCR pages
- manually correct OCR output
- save corrected notes into `unified_source.md`

Important:
- `unified_source.md` is the Stage 1 source of truth
- with project-id CLI mode, Stage 1 always reads `unified_source.md`
- if it does not exist, the pipeline throws immediately


### Step 1: Stage 1 Reconstruction Pipeline

Goal:
- split corrected notes into logical teaching units
- extract all detailed points per unit
- generate one structured manuscript per unit

Important constraints:
- chunked generation only
- each unit is generated independently
- every API call is stateless
- split output contains boundaries and metadata only, never raw note duplication
- generated units are the source of truth for later draft chunking

Current provider:
- Anthropic Claude
- default model: `claude-sonnet-4-6`

Current implementation:
- `backend/pipeline/stage1.py`
- CLI entry: `backend/pipeline/sermon_generation.py`

Current operator workflow:
- click `Generate Draft`
- enter the Stage 1 console
- run split first if needed
- inspect the unit boundaries
- generate one unit or all units
- monitor progress and logs from persisted backend state


### Step 2: Fidelity Audit

Goal:
- compare each Stage 1 draft chunk against the original corrected note slice
- detect omissions, additions, unsupported claims, and stance upgrades

Important:
- fidelity audit is chunk-level
- it uses source line boundaries inherited from Stage 1 units
- it slices `unified_source.md` first, then preprocesses the slice
- it no longer silently falls back to the entire notes file


### Step 3: Theological Boundary Audit

Goal:
- review the final chunk text for theological / exegesis / structural issues

Important:
- this audit does not need the original notes for its core judgment
- but it still requires stable chunk lineage
- final chunks now inherit lineage from draft chunks instead of being re-split from markdown headings alone


### Step 4: Master Text Finalization

Goal:
- create `final.md` from the reviewed draft chunk structure
- preserve stable chunk identities for final review and export


### Step 5: Editorial Metadata + Export

Goal:
- generate and edit whole-document metadata for Master Text
- export to Google Docs with clean top-level formatting

Metadata fields:
- title
- subtitle
- summary
- key_bible_verse
- key_exegetical_points
- key_theological_points

Important:
- `key_exegetical_points` and `key_theological_points` are stored as Markdown bullet lists
- export uses title/subtitle from Master Text metadata


## Current Architecture

### Frontend

Location:
- `web/src/app/admin/notes-to-sermon/project/[id]/`

Main editor:
- `MultiPageEditor.tsx`

Views:
- `Unified Input`
- `Generated Draft`
- `Master Text`

Review panels:
- `TheologicalAuditPanel.tsx`

Current UI behavior:
- `Generate Draft` opens the Stage 1 console instead of silently launching a full run
- Stage 1 console supports:
  - split-only execution
  - per-unit manuscript generation
  - generate-all execution
  - live polling of progress, unit states, and logs
- draft and final are edited chunk-by-chunk
- `FULL_DOC` can still be selected for whole-document viewing
- Master Text metadata panel only appears in `Master Text` mode when `FULL_DOC` is selected

Stage 1 console:
- `web/src/app/admin/notes-to-sermon/project/[id]/generation/page.tsx`


### Backend

Router:
- `backend/api/sermon_converter_router.py`

Core service:
- `backend/api/sermon_converter_service.py`

Stage 1 pipeline:
- `backend/pipeline/stage1.py`

Detached Stage 1 worker:
- `backend/pipeline/stage1_worker.py`


## Current File Layout Per Project

Project directory:
- `DATA_BASE_DIR/notes_to_surmon/<project_id>/`

Primary files:
- `unified_source.md`
- `original_notes.md`
- `stage1_manifest.json`
- `stage1_job.json`
- `stage1_units.json`
- `draft_v1.md`
- `final.md`
- `master_text_meta.json`

Stage 1 artifacts:
- `generated_units/`
  - one JSON file per unit
  - one `.points.json` cache per unit for point extraction

Draft review artifacts:
- `draft_chunks/`
- `draft_chunks_meta.json`

Final review artifacts:
- `chunks/`
- `chunks_meta.json`

Audit artifacts:
- `fidelity_audit.json`
- `theological_audit.json`

Logs:
- `stage1_logs.jsonl`
- `stage1_worker.log`
- `agent_logs.json`


## Stage 1 Technical Design

### 1. Unit Splitting

Input:
- `unified_source.md`

Output:
- `stage1_units.json`

Each unit contains:
- `unit_id`
- `chapter_title`
- `section_title`
- `unit_title`
- `scripture_range`
- `start_line`
- `end_line`
- `split_reason`
- `prev_unit_id`
- `next_unit_id`

Rules:
- split by exegetical / logical boundaries
- prioritize scripture-range transitions and discourse transitions
- do not split just by length
- do not merge distinct arguments merely to reduce unit count


### 2. Point Extraction

Per unit:
- extract all detailed points from the current unit note slice
- classify each point into:
  - `釋經`
  - `神學`
  - `應用`
  - `附錄`

Prompt:
- `backend/pipeline/prompts/point_extractor.md`


### 3. Manuscript Generation

Per unit:
- second call consumes extracted points plus source slices
- outputs structured JSON, then markdown is derived from it
- generation can run for:
  - one selected unit
  - all units in one pass
- reruns reuse existing artifacts unless forced

Prompt:
- `backend/pipeline/prompts/unit_generator.md`

Generated fields per unit:
- `points`
- `manuscript_sections`
- `coverage_checks`
- `coverage_summary`
- `generated_markdown`


### 4. Prompt Files

Current prompt files:
- `backend/pipeline/prompts/unit_splitter.md`
- `backend/pipeline/prompts/point_extractor.md`
- `backend/pipeline/prompts/unit_generator.md`
- `backend/pipeline/prompts/master_text_metadata.md`

Shared prompt component:
- `backend/pipeline/prompts/shared/category_definitions.md`

Important:
- category definitions are maintained once and injected into prompt templates


### 5. Stage 1 Output Rendering

Rendered user-facing draft:
- uses numbered headings such as `一、二、三、...`
- does not render provenance lines like:
  - `衝突-11章 | ... | 太 11:1-6 | lines 9-39`

Important:
- provenance and line boundaries are still preserved in JSON metadata
- they are hidden from the rendered manuscript body


### 6. Stage 1 Job Model

Stage 1 execution is now backend-detached.

Behavior:
- UI starts a backend worker process
- the worker continues after the HTTP request returns
- the browser can refresh and recover progress from persisted state
- status is derived from:
  - `stage1_manifest.json`
  - `stage1_job.json`
  - `stage1_logs.jsonl`

Supported modes:
- split only
- generate one unit
- generate all units

Important:
- the Stage 1 console does not depend on in-memory browser state
- the pipeline is resumable because unit outputs are persisted per artifact file


## Chunk Lineage Design

### Source of Truth

For draft chunking:
- `generated_units/*.json` is the source of truth
- `draft_v1.md` is a rendered artifact, not the source of truth

For final chunking:
- final chunks inherit from draft chunks when possible
- they are not supposed to drift from Stage 1 unit lineage


### Draft Chunk Generation

Implemented in:
- `backend/api/sermon_converter_service.py`

Behavior:
- build draft chunks directly from `generated_units`
- preserve:
  - `unit_id`
  - `chapter_title`
  - `section_title`
  - `scripture_range`
  - `source_start_line`
  - `source_end_line`


### Final Chunk Generation

Behavior:
- initialize final chunks from current draft chunk lineage
- rebuild `final.md` from chunk files
- keep final chunk identities stable across review/edit cycles


## Audit Design

### Fidelity Audit

Endpoint:
- `POST /admin/notes-to-sermon/sermon-project/{project_id}/audit-draft`

Behavior:
- reads the selected draft chunk
- resolves its source line boundaries from `draft_chunks_meta.json`
- slices the raw `unified_source.md`
- preprocesses the slice
- runs the fidelity audit against that local source slice

Stored result:
- `fidelity_audit.json`

Project-level summary:
- `GET /admin/notes-to-sermon/sermon-project/{project_id}/fidelity-audit-summary`


### Theological Audit

Endpoint:
- `POST /admin/notes-to-sermon/sermon-project/{project_id}/theological-audit`

Behavior:
- audits final chunk text
- uses stable final chunk lineage inherited from draft chunks
- does not depend on reparsing `final.md` headings for chunk identity

Stored result:
- `theological_audit.json`


## Stage 1 Control Endpoints

Status:
- `GET /admin/notes-to-sermon/sermon-project/{project_id}/stage1/status`

Actions:
- `POST /admin/notes-to-sermon/sermon-project/{project_id}/stage1/split`
- `POST /admin/notes-to-sermon/sermon-project/{project_id}/stage1/generate-all`
- `POST /admin/notes-to-sermon/sermon-project/{project_id}/stage1/unit/{unit_id}/generate`

Returned status payload includes:
- current job mode
- whether the detached worker is still running
- manifest status
- per-unit status
- current progress
- recent Stage 1 logs


## Master Text Metadata

Stored in:
- `master_text_meta.json`

Endpoints:
- `GET /admin/notes-to-sermon/sermon-project/{project_id}/master-text-metadata`
- `POST /admin/notes-to-sermon/sermon-project/{project_id}/master-text-metadata`
- `POST /admin/notes-to-sermon/sermon-project/{project_id}/generate-master-text-metadata`

Generation behavior:
- reads full `final.md`
- uses Claude
- generates:
  - title
  - subtitle
  - summary
  - key_bible_verse
  - markdown bullet list of key exegetical points
  - markdown bullet list of key theological points


## Google Doc Export

Implemented in:
- `export_sermon_to_doc` in `backend/api/sermon_converter_service.py`

Behavior:
- exports `final.md`
- prepends title and subtitle from `master_text_meta.json`
- title and subtitle are left-aligned
- title is larger/bold
- subtitle is smaller/muted
- Drive file name is updated to match the export title


## CLI

Stage 1 CLI:

```bash
python3 -m backend.pipeline.sermon_generation --project-id '衝突與安息-12,13章'
```

Split only:

```bash
python3 -m backend.pipeline.sermon_generation --project-id '衝突與安息-12,13章' --split-only
```

Important:
- all Stage 1 Python code now lives under `backend/`


## Current Implementation Status

Implemented:
- OCR page processing into `unified_source.md`
- Stage 1 split / point extraction / manuscript generation
- Claude-based Stage 1 pipeline
- detached Stage 1 backend worker with persisted progress
- Stage 1 console UI for split / per-unit / generate-all control
- generated-unit-based draft chunking
- fidelity audit with note-slice boundaries
- lineage-preserving theological audit chunks
- Master Text metadata generation/editing
- Google Doc export with title/subtitle

Partially implemented / evolving:
- prompt and chunk rendering refinements
- UI ergonomics in Master Text mode

Not the current source of truth anymore:
- old “manual Kimi reconstruction” description
- old prompt-manager-based reconstruction flow
- old markdown-reparsing chunk logic


## Environment Notes

Important environment variables:
- `ANTHROPIC_API_KEY`
- Google auth credentials for Docs / Drive export

The frontend talks to the FastAPI backend through the existing Next.js API proxy setup.
