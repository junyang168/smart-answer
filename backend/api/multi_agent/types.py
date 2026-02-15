from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from enum import Enum

class AgentRole(str, Enum):
    SEGMENTER = "segmenter"
    EXPANDER = "expander"
    SYSTEM = "system"

class AgentState(BaseModel):
    # --- Context (Inputs) ---
    project_id: str
    project_type: str = "sermon_note"  # "sermon_note" or "transcript"
    sermon_series_title: str
    sermon_series_description: str
    lecture_title: str
    lecture_description: str
    
    # The core material
    source_notes: str  # The unified markdown from OCR or transcript
    
    # --- Phase 1: Teaching Units ---
    units: Optional[List[Dict]] = None  # [{title, keypoints, type, content}, ...]
    
    # --- Phase 2: Expansion ---
    draft_chunks: List[str] = Field(default_factory=list)
    full_manuscript: Optional[str] = None
    
    # --- Logging ---
    processing_logs: List[str] = Field(default_factory=list)
    
    def add_log(self, message: str):
        self.processing_logs.append(message)
