import asyncio
import re
import json
from typing import List, Optional
from datetime import datetime
from pathlib import Path

from backend.api.config import DATA_BASE_PATH
from backend.api.multi_agent.types import AgentState

from backend.api.multi_agent.agents import (
    run_exegete, run_theologian, run_illustrator, 
    run_homiletician_beat, run_critic_check, identify_beats
)
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
            return AgentState(**data)
    except Exception as e:
        print(f"Warning: Failed to load previous state: {e}")
    return None

async def process_project_with_mas(project_id: str):
    """
    Main entry point for Multi-Agent Sermon Generation.
    Supports Resumability.
    """
    try:
        # 0. Setup & Context Loading
        update_sermon_processing_status(project_id, True, {"stage": "Initializing", "progress": 0})
        
        # Determine Context (Series/Lecture)
        # We do this every time to ensure context is fresh if it changed, 
        # unless we loaded a valid state.
        
        state = _load_agent_state(project_id)
        
        # If no state, initialize it
        if not state:
            source_content = get_sermon_source(project_id)
            if not source_content:
                 raise ValueError("No source content found")
                 
            # Resolve Context
            series_title = "Unknown Series"
            series_desc = ""
            lecture_title = "Unknown Lecture"
            lecture_desc = ""
            
            all_series = list_series() 
            found = False
            for s in all_series:
                for l in s.lectures:
                    if project_id in l.project_ids:
                        series_title = s.title
                        series_desc = s.description
                        lecture_title = l.title
                        lecture_desc = l.description
                        found = True
                        break
                if found: break
                
            state = AgentState(
                project_id=project_id,
                sermon_series_title=series_title,
                sermon_series_description=series_desc,
                lecture_title=lecture_title,
                lecture_description=lecture_desc,
                source_notes=source_content
            )
            _save_agent_state(state) # Save initial state

        # Phase 1: Research
        if not state.exegetical_notes:
            update_sermon_processing_status(project_id, True, {"stage": "Exegetical Research", "progress": 10})
            _log_agent_action(project_id, "exegete", "Starting deep research on original text...")
            
            state.exegetical_notes = await asyncio.to_thread(run_exegete, state)
            _save_agent_state(state)
            _log_agent_action(project_id, "exegete", "Research complete. Notes generated.")
        else:
             _log_agent_action(project_id, "system", "Resuming using cached Exegetical Notes.")
        
        # Phase 2: Enrichment
        if not state.theological_analysis:
            update_sermon_processing_status(project_id, True, {"stage": "Theological Analysis", "progress": 30})
            _log_agent_action(project_id, "theologian", "Reviewing theological consistency...")
            state.theological_analysis = await asyncio.to_thread(run_theologian, state)
            _save_agent_state(state)
        
        if not state.illustration_ideas:
            update_sermon_processing_status(project_id, True, {"stage": "Illustration Brainstorming", "progress": 50})
            _log_agent_action(project_id, "illustrator", "Brainstorming modern examples...")
            state.illustration_ideas = await asyncio.to_thread(run_illustrator, state)
            _save_agent_state(state)
        
        # Phase 3: Drafting (The Anti-Outline Loop)
        if not state.beats:
            update_sermon_processing_status(project_id, True, {"stage": "Structuring (Intelligent Splitting)", "progress": 60})
            _log_agent_action(project_id, "structuring_specialist", "Analyzing structure...")
            
            # Use LLM to split
            beats = await asyncio.to_thread(identify_beats, state)
            state.beats = beats if beats else [state.source_notes]
            _save_agent_state(state)
            _log_agent_action(project_id, "structuring_specialist", f"Identified {len(state.beats)} macro-beats.")
            
        beats = state.beats
        
        # Resume Beat Loop
        # state.draft_chunks tracks completed chunks
        # If we have 2 chunks but 5 beats, we start at index 2
        
        items_done = len(state.draft_chunks)
        total_beats = len(beats)
        
        if items_done < total_beats:
            full_draft = state.draft_chunks.copy() # Start with existing
            
            for i in range(items_done, total_beats):
                beat = beats[i]
                current_draft_context = "\n\n".join(full_draft)
                
                # Progress update per beat
                progress = 70 + int((i / total_beats) * 25)
                update_sermon_processing_status(project_id, True, {"stage": f"Drafting Part {i+1}/{total_beats}", "progress": progress})
                
                _log_agent_action(project_id, "drafter", f"Drafting section {i+1}/{total_beats}...")
                
                # Retry loop for Critic
                max_retries = 2
                passed = False
                best_attempt = ""
                
                for attempt in range(max_retries + 1):
                    draft_chunk = await asyncio.to_thread(run_homiletician_beat, state, beat, current_draft_context)
                    best_attempt = draft_chunk # Keep latest
                    
                    check_result = await asyncio.to_thread(run_critic_check, draft_chunk)
                    if check_result:
                        _log_agent_action(project_id, "critic", f"Beat {i+1} PASSED review.")
                        passed = True
                        break
                    else:
                        _log_agent_action(project_id, "critic", f"Beat {i+1} FAILED review. Retrying...")
                        pass
                
                # Append and Save immediately
                full_draft.append(best_attempt)
                state.draft_chunks.append(best_attempt)
                _save_agent_state(state) # Checkpoint!
        
        # Finalize
        state.full_manuscript = "\n\n".join(state.draft_chunks)
        _save_agent_state(state)
        
        # Save Result to Draft File
        draft_path = get_sermon_draft_path(project_id)
        with open(draft_path, "w", encoding="utf-8") as f:
            f.write(state.full_manuscript)
            
        update_sermon_processing_status(project_id, False, {"stage": "Complete", "progress": 100})
        _log_agent_action(project_id, "chief_editor", "Workflow complete.")
        
    except Exception as e:
        print(f"MAS Error: {e}")
        # Log error to file too so it's visible in UI logs? 
        _log_agent_action(project_id, "system", f"CRITICAL ERROR: {e}")
        update_sermon_processing_status(project_id, False, error=str(e))

def _log_agent_action(project_id: str, role: str, message: str):
    """
    Append a log entry to the project's agent log file.
    """
    import json
    from backend.api.config import DATA_BASE_PATH
    
    # Consolidate into the same folder as meta.json and agent_state.json
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
            # Limit logs size? No, user wants debugging.
        except:
            pass
            
    # Maybe limit if > 1000? 
    if len(current_logs) > 2000:
        current_logs = current_logs[-1000:]
        
    current_logs.append(entry)
    
    with open(log_file, "w", encoding="utf-8") as f:
        json.dump(current_logs, f, ensure_ascii=False, indent=2)
