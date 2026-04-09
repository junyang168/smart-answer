import asyncio
import json
from typing import Optional
from datetime import datetime
from pathlib import Path

from backend.api.config import DATA_BASE_PATH
from backend.api.multi_agent.types import AgentState

from backend.api.sermon_converter_service import (
    get_sermon_source,
    save_sermon_draft,
    sync_draft_chunks_from_generated_units,
    update_sermon_processing_status,
)
from backend.api.lecture_manager import list_series
from backend.pipeline.stage1 import run_stage1_pipeline


def _render_manuscript_sections(sections: dict) -> str:
    blocks: list[str] = []
    section_map = [
        ("exegesis", "釋經"),
        ("theological_significance", "神學意義"),
        ("application", "生活應用"),
        ("appendix", "附錄"),
    ]
    for key, label in section_map:
        value = sections.get(key) if isinstance(sections, dict) else None
        if isinstance(value, str) and value.strip():
            blocks.append(f"### {label}\n\n{value.strip()}")
    return "\n\n".join(blocks).strip()

def _get_agent_state_path(project_id: str) -> Path:
    return DATA_BASE_PATH / "notes_to_surmon" / project_id / "agent_state.json"

def _save_agent_state(state: AgentState):
    """Persist agent state to disk for resumability."""
    try:
        path = _get_agent_state_path(state.project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(state.model_dump_json(indent=2))
    except Exception as e:
        print(f"Warning: Failed to save agent state: {e}")

def _load_agent_state(project_id: str) -> Optional[AgentState]:
    """Try to load existing agent state."""
    try:
        path = _get_agent_state_path(project_id)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Reject old-format state files (from the 5-agent pipeline)
            old_fields = {"exegetical_notes", "theological_analysis", "illustration_ideas", "beats"}
            if old_fields & set(data.keys()):
                print(f"Warning: Discarding old-format agent state for {project_id}")
                path.unlink()
                return None
            
            return AgentState(**data)
    except Exception as e:
        print(f"Warning: Failed to load previous state: {e}")
    return None

async def process_project_with_mas(project_id: str, force_restart: bool = False):
    """
    Main entry point for the production Stage 1 pipeline.
    Stage 1A: split corrected notes into metadata-only teaching units.
    Stage 1B: extract source slices by line range for local context.
    Stage 1C: generate each unit independently with the strict Stage 1 prompt.
    """
    try:
        update_sermon_processing_status(project_id, True, {"stage": "Initializing", "progress": 0})

        state = _load_agent_state(project_id)

        if not state:
            source_content = get_sermon_source(project_id)
            if not source_content:
                raise ValueError("No source content found")

            series_title = "Unknown Series"
            series_desc = ""
            lecture_title = "Unknown Lecture"
            lecture_desc = ""
            project_type = "sermon_note"
            
            all_series = list_series()
            found = False
            for s in all_series:
                for l in s.lectures:
                    if project_id in l.project_ids:
                        series_title = s.title
                        series_desc = s.description or ""
                        lecture_title = l.title
                        lecture_desc = l.description or ""
                        project_type = s.project_type or "sermon_note"
                        found = True
                        break
                if found:
                    break

            if not found:
                meta_path = DATA_BASE_PATH / "notes_to_surmon" / project_id / "meta.json"
                if meta_path.exists():
                    try:
                        with open(meta_path, "r", encoding="utf-8") as f:
                            meta_data = json.load(f)
                        project_type = meta_data.get("project_type", "sermon_note")
                    except Exception:
                        pass
            
            state = AgentState(
                project_id=project_id,
                project_type=project_type,
                sermon_series_title=series_title,
                sermon_series_description=series_desc,
                lecture_title=lecture_title,
                lecture_description=lecture_desc,
                source_notes=source_content
            )
            _save_agent_state(state)
        else:
            latest_source = get_sermon_source(project_id)
            if latest_source:
                state.source_notes = latest_source

        project_dir = DATA_BASE_PATH / "notes_to_surmon" / project_id
        input_path = project_dir / "unified_source.md"
        log_path = project_dir / "stage1_logs.jsonl"

        def on_log(role: str, message: str) -> None:
            _log_agent_action(project_id, role, message)

        def on_progress(stage: str, progress: int) -> None:
            update_sermon_processing_status(project_id, True, {"stage": stage, "progress": progress})

        summary = await asyncio.to_thread(
            run_stage1_pipeline,
            input_path=input_path,
            output_dir=project_dir,
            model="claude-sonnet-4-6",
            timeout_seconds=90.0,
            max_retries=3,
            force=force_restart,
            log_path=log_path,
            log_callback=on_log,
            progress_callback=on_progress,
        )

        state.units = [unit.__dict__ for unit in summary.units]
        state.generated_units = [generated_unit.__dict__ for generated_unit in summary.generated_units]
        state.draft_chunks = [
            f"## {generated_unit.unit_title}\n\n{_render_manuscript_sections(generated_unit.manuscript_sections)}"
            for generated_unit in summary.generated_units
        ]
        state.failed_units = summary.failed_units
        state.full_manuscript = summary.combined_markdown
        _save_agent_state(state)

        save_sermon_draft(project_id, summary.combined_markdown)
        sync_draft_chunks_from_generated_units(project_id)

        final_stage = "Complete with failures" if summary.failed_units else "Complete"
        update_sermon_processing_status(project_id, False, {"stage": final_stage, "progress": 100})
        _log_agent_action(project_id, "system", "Stage 1 generation completed.")

    except Exception as e:
        print(f"MAS Error: {e}")
        _log_agent_action(project_id, "system", f"CRITICAL ERROR: {e}")
        update_sermon_processing_status(project_id, False, error=str(e))

def _log_agent_action(project_id: str, role: str, message: str):
    """
    Append a log entry to the project's agent log file.
    """
    from backend.api.config import DATA_BASE_PATH
    
    log_file = DATA_BASE_PATH / "notes_to_surmon" / project_id / "agent_logs.json"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "role": role,
        "message": message
    }
    
    current_logs = []
    if log_file.exists():
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                current_logs = json.load(f)
        except Exception:
            pass
    
    if len(current_logs) > 2000:
        current_logs = current_logs[-1000:]
    
    current_logs.append(entry)
    
    with open(log_file, "w", encoding="utf-8") as f:
        json.dump(current_logs, f, ensure_ascii=False, indent=2)
