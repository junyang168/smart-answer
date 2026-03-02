---
description: Workflow and Architecture of the Notes-to-Sermon Module
---

# Notes to Sermon Workflow

This document serves as a persistent guide to how the `notes-to-sermon` module works, its structure, and its features, so the AI and users do not forget the intricate details of the implementation.

## 1. Overview
The **Notes to Sermon** module (`/admin/notes-to-sermon/project/`) is designed to take raw sermon notes (often extracted via OCR from images of handwritten or typed notes) and convert them into a structured, unified sermon draft. It features multi-agent generation and an AI Audit step.

## 2. Core Components

### Frontend (Next.js)
- **Location**: `web/src/app/admin/notes-to-sermon/`
- **Main Editor**: `MultiPageEditor.tsx` acts as the primary UI. It manages a split-pane view containing:
  - **Source/Draft View**: A CodeMirror markdown editor (via `ReactSimpleMDE`) to edit either the `Unified Input` (Source) or the `Generated Draft`.
  - **AI Panel**: Depending on the view, it shows metadata or the `AiCommandPanel`.
- **AI Audit Panel**: `AiCommandPanel.tsx` is located in the right pane when the user is in "Generated Draft" mode. It triggers the backend audit endpoint and renders the Markdown review. The state is hoisted or preserved via CSS `display: hidden` when switching tabs.

### Backend (FastAPI Python)
- **Router**: `backend/api/sermon_converter_router.py` (prefix: `/admin/notes-to-sermon/`)
- **Service**: `backend/api/sermon_converter_service.py`. Contains the core logic.

## 3. The Step-by-Step Workflow

### Step 1: Project Creation & Source Input
- A project is created (stored in a JSON metadata file). 
- Images of notes can be uploaded and processed (OCR).
- The raw transcriptions are compiled into a unified markdown file (`unified_source.md`).

### Step 2: Generating the Sermon Draft
- This is done using kimi k2.5 manually outside of the system 
- The current implementation is to use a multi-agent approach to elaborate on the raw notes and build a full sermon draft. But it's not used
- The prompt manager (`prompt_manager.py`) is often used to structure the generation process.
- The output is saved to the project file structure (e.g., `draft_v1.md`).

### Step 3: AI Audit (Reviewing the Draft)
- **Endpoint**: `POST /admin/notes-to-sermon/sermon-project/{sermon_id}/audit-draft`
- **Function**: `audit_sermon_draft` in `sermon_converter_service.py`
- **Process**:
  - The backend loads both the original `unified_source.md` and the generated `draft_v1.md`.
  - It constructs a prompt calling an OpenAI model (e.g., `gpt-5.2`), acting as a "Diff Auditor" (释经逐字稿外置审核器).
  - The prompt mandates strict adherence to an `AUDIT_SCHEMA` via OpenAI's Structured Outputs (JSON Schema).
  - It outputs JSON evaluating coverage, differences, must-fix issues, and a final grade.
  - The JSON is reformatted into a human-readable Markdown string and returned to the frontend.

### Step 4: Exporting
- The final polished draft can be exported to Google Docs.
- Functions like `export_sermon_to_doc` process the Markdown (handling local images, footnotes, and multi-line quotes) to push to the Google Docs API.

## 4. Environment and Setup Notes
- The backend relies heavily on environment variables (loaded via `dotenv`) for external API access, particularly `OPENAI_API_KEY`. Without this, OpenAI SDK requests will crash or hang.
- API requests from the Next.js frontend to the FastAPI backend use Next.js rewrites (`next.config.mjs`) proxying `/api/*` to the local python server port (e.g., `8222` or `8555` depending on dev/prod).