---
description: Workflow and Architecture of the Notes-to-Sermon Module
---

# Notes to Sermon Workflow

This document serves as a persistent guide to how the `notes-to-sermon` module works, its structure, and its features, so the AI and users do not forget the intricate details of the implementation.


## Exegesis Text Engineering Functional Workflow

⸻

🎯 Project Objective

Build a stable, reusable, and scalable exegesis text engineering system.

The immediate goal is not to produce a devotional reader.

The goal is:

First construct a stable, reviewable, master-level exegesis text from extracted via OCR from images of handwritten or typed notes taken from exegesis class given by a well-known Chinese 基督教福音派神學教授.

In this phase, this is a text-engineering project, not a publishing shortcut.

⸻

Targeted Workflow(Planned)


⸻

Step 0: Notes Preparation & Source Corroboration

Objective

After paper notes are scanned and OCRed, manually review the extracted raw text to ensure accuracy. Corroborate the digital text against another copy of the manual notes if necessary. 

Modify the text to add logical flow and explicit structure so that the AI can more easily understand the theological progression before generating the exegesis manuscript.

⸻

Step 1: Reconstruction & Structural Stabilization Layer

Objective

Reconstruct classroom notes into a complete exegesis manuscript, while simultaneously separating the mixed classroom material into a fixed structural format:
	•	Exegesis
	•	Theological Significance
	•	Application
	•	Appendix (Apologetics / Historical Extensions)

Principles
	•	Do not introduce new theology
	•	Do not systematize prematurely
	•	Preserve the lecturer’s exegetical logic
	•	Retain original-language details and technical observations
	•	Minor sentence smoothing is allowed, but no meaning changes

Output

“Reconstructed Manuscript Draft” (Markdown format, structurally stabilized)

This is the foundational layer.
All later phases depend on this integrity.

⸻

Step 2: Issue Review Layer

Objective

Conduct theological and structural boundary review.

Method

Issue-based auditing only.
	•	Do not rewrite
	•	Do not correct
	•	Do not resolve
	•	Only flag issues

Review Categories
	•	exegesis_error
	•	factual_error
	•	overstatement
	•	imbalance
	•	apologetics_extension
	•	structural

Output

Issue Report (Structured JSON mapped to Interactive UI)

Purpose of This Phase

To protect doctrinal and interpretive boundaries without altering the author’s voice.

⸻

Step 3: Master Text Finalization

Objective

Freeze a stable “Master Text” version.

Characteristics
	•	Structure stabilized
	•	Theological boundaries reviewed
	•	Layer separation complete
	•	Consistent format across chapter
	•	Suitable for long-term reference

Prohibited After Finalization
	•	Structural redesign
	•	Changing editorial standards mid-book
	•	Adjusting theological tone arbitrarily


⸻

Engineering Flow Model

Notes (Raw OCR)
  ↓
Manual Preparation & Structural Flow Fixes (Step 0)
  ↓
Reconstructed & Stabilized Manuscript
  ↓
Issue Review (Interactive Audit)
  ↓
Master Text Freeze


⸻

Design Philosophy
	•	Separate phases strictly
	•	Do not mix objectives
	•	Preserve first, evaluate second
	•	Build master text before reader adaptation



---

## Current Implementation Status
* ✅ Implemented: Step 1 (OCR → unified_source.md; manual Kimi reconstruction + structural formatting → draft_v1.md; interactive JSON audit endpoint)
* ⏳ Not implemented: Step 2–3 (issue review, freeze)

---

## Crrent Technical Design

### Overview
The **Notes to Sermon** module (`/admin/notes-to-sermon/project/`) is designed to implement the functional workflow laid out above. It take raw exegesis notes  (extracted via OCR from images of handwritten or typed notes) and convert them into a structured, exegesis lecture. It follows a multi-step methodical process as laied out in the above functional workflow .

### 2. Core Components

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

### Implementation of Reconstruction & Stabilization Layer.

#### Step 1: Project Creation & Source Input
- A project is created (stored in a JSON metadata file). 
- Images of notes can be uploaded and processed (OCR).
- The raw transcriptions are compiled into a unified markdown file (`unified_source.md`).
- before manual review, user will save the original notes into original_notes.md

#### Step 2: Reconstruct and Stabilize the manuscript draft
- This is done using kimi k2.5 manually outside of the system. It combines creating the manuscript and separating the content into structured categories.
- The current implementation is to use a multi-agent approach to elaborate on the raw notes and build a full sermon draft. But it's not used.
- The prompt manager (`prompt_manager.py`) is often used to structure the generation process.
- The output is saved to the project file structure (e.g., `draft_v1.md`).

#### Step 3: AI Audit to ensure fidelity of reconstruction & structure
- Detect additions not supported by notes
* Detect missing points from notes
* Detect re-ordered logic that changes meaning
* Detect Unsupported additions( Draft claims not supported by notes)

- **Endpoint**: `POST /admin/notes-to-sermon/sermon-project/{project_id}/audit-draft`
- **Function**: `audit_sermon_draft` in `sermon_converter_service.py`
- **Process**:
  - The backend loads both the original `unified_source.md` and the generated `draft_v1.md`.
  - It constructs a prompt calling an OpenAI model (e.g., `gpt-5.2`), acting as a "Reconstruction Fidelity Auditor".
  - The prompt mandates strict adherence to an `AUDIT_SCHEMA` via OpenAI's Structured Outputs (JSON Schema).
  - It ensures all outputs are evaluated and written in **Traditional Chinese**.
  - It outputs JSON evaluating coverage, differences, must-fix issues, and a final grade.
  - The output JSON is returned directly to the frontend.
  - The frontend maps the JSON to construct a custom React component UI (`AiCommandPanel.tsx`).
  - Clicking on "Evidence" texts in the UI triggers a search/highlight callback to pinpoint exact or partial matches within the CodeMirror editor (`MultiPageEditor.tsx`) for immediate revision.

#### Step 4: Exporting
- The final polished draft can be exported to Google Docs.
- Functions like `export_sermon_to_doc` process the Markdown (handling local images, footnotes, and multi-line quotes) to push to the Google Docs API.




### Environment and Setup Notes
- The backend relies heavily on environment variables (loaded via `dotenv`) for external API access, particularly `OPENAI_API_KEY`. Without this, OpenAI SDK requests will crash or hang.
- API requests from the Next.js frontend to the FastAPI backend use Next.js rewrites (`next.config.mjs`) proxying `/api/*` to the local python server port (e.g., `8222` or `8555` depending on dev/prod).