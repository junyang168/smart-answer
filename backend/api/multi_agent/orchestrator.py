import asyncio
import json
from typing import List, Optional
from datetime import datetime
from pathlib import Path

from backend.api.config import DATA_BASE_PATH
from backend.api.multi_agent.types import AgentState

from backend.api.multi_agent.agents import segment_notes, expand_unit
from backend.api.sermon_converter_service import (
    get_sermon_source, get_sermon_draft_path, update_sermon_processing_status
)
from backend.api.lecture_manager import get_series, list_series

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

async def process_project_with_mas(project_id: str):
    """
    Main entry point for 2-Phase Sermon/Exposition Generation.
    Phase 1: Segment notes into teaching units.
    Phase 2: Expand each unit into verbatim manuscript.
    Supports Resumability.
    """
    try:
        # 0. Setup & Context Loading
        update_sermon_processing_status(project_id, True, {"stage": "Initializing", "progress": 0})
        
        state = _load_agent_state(project_id)
        
        # If no state, initialize it
        if not state:
            source_content = get_sermon_source(project_id)
            if not source_content:
                raise ValueError("No source content found")
            
            # Resolve Context (Series/Lecture/ProjectType)
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
            
            # Also try to read project_type from project meta.json
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

        # Phase 1: Segmenting
        if not state.units:
            update_sermon_processing_status(project_id, True, {"stage": "教學單元切割", "progress": 5})
            _log_agent_action(project_id, "segmenter", f"開始切割筆記為教學單元（類型：{state.project_type}）...")
            
            units = await asyncio.to_thread(segment_notes, state)
            state.units = units if units else [state.source_notes]
            _save_agent_state(state)
            _log_agent_action(project_id, "segmenter", f"切割完成，共 {len(state.units)} 個教學單元。")
        else:
            _log_agent_action(project_id, "system", f"使用已緩存的 {len(state.units)} 個教學單元。")
        
        units = state.units
        
        # Phase 2: Expanding each unit (TEMPORARILY DISABLED)
        # items_done = len(state.draft_chunks)
        # total_units = len(units)
        # 
        # if items_done < total_units:
        #     full_draft = state.draft_chunks.copy()
        #     
        #     for i in range(items_done, total_units):
        #         unit = units[i]
        #         unit_content = unit["content"] if isinstance(unit, dict) else unit
        #         current_draft_context = "\n\n".join(full_draft)
        #         
        #         # Progress: 20% to 95%
        #         progress = 20 + int((i / total_units) * 75)
        #         update_sermon_processing_status(project_id, True, {
        #             "stage": f"擴展教學單元 {i+1}/{total_units}",
        #             "progress": progress
        #         })
        #         
        #         _log_agent_action(project_id, "expander", f"開始擴展教學單元 {i+1}/{total_units}（{len(unit_content)} 字）...")
        #         
        #         draft_chunk = await asyncio.to_thread(expand_unit, state, unit_content, current_draft_context)
        #         
        #         _log_agent_action(project_id, "expander", f"教學單元 {i+1}/{total_units} 擴展完成（{len(draft_chunk)} 字）。")
        #         
        #         # Append and Save immediately (checkpoint)
        #         full_draft.append(draft_chunk)
        #         state.draft_chunks.append(draft_chunk)
        #         _save_agent_state(state)
        
        # Finalize
        state.full_manuscript = "\n\n".join(state.draft_chunks)
        _save_agent_state(state)
        
        # Save Result to Draft File
        draft_path = get_sermon_draft_path(project_id)
        with open(draft_path, "w", encoding="utf-8") as f:
            f.write(state.full_manuscript)
        
        update_sermon_processing_status(project_id, False, {"stage": "Complete", "progress": 100})
        _log_agent_action(project_id, "system", "生成完成。")
        
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
