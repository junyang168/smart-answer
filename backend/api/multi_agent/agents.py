from google.genai import types
from typing import Optional, List
import json

from backend.api.gemini_client import gemini_client
from backend.api.prompt_manager import list_prompts, init_default_prompt
from backend.api.multi_agent.types import AgentState, AgentRole

# Shared Gemini Config
MODEL_ID = "gemini-3-pro-preview" 

def _get_system_prompt(role: str) -> str:
    # Ensure defaults exist
    try:
        init_default_prompt()
        prompts = list_prompts()
        for p in prompts:
            if p.role == role:
                return p.content
    except Exception as e:
        print(f"Error fetching prompts: {e}")
            
    return "You are a helpful assistant."

def run_exegete(state: AgentState) -> str:
    system_prompt = _get_system_prompt(AgentRole.EXEGETE.value)
    
    user_prompt = f"""
    Here is the section of sermon notes we effectively need to research.
    
    === Source Notes ===
    {state.source_notes}
    === End Notes ===
    
    Please assume the role of the Exegetical Scholar and provide your research notes.
    """
    
    response = gemini_client.generate_raw(
        model=MODEL_ID,
        contents=[types.Content(role="user", parts=[types.Part.from_text(text=user_prompt)])],
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.7
        )
    )
    return response.text

def run_theologian(state: AgentState) -> str:
    system_prompt = _get_system_prompt(AgentRole.THEOLOGIAN.value)
    
    user_prompt = f"""
    Context:
    Series: {state.sermon_series_title} - {state.sermon_series_description}
    Lecture: {state.lecture_title}
    
    === Source Notes ===
    {state.source_notes}
    
    === Exegetical Insights (From previous step) ===
    {state.exegetical_notes}
    
    Please provide your Theological Analysis.
    """
    
    response = gemini_client.generate_raw(
        model=MODEL_ID,
        contents=[types.Content(role="user", parts=[types.Part.from_text(text=user_prompt)])],
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.7
        )
    )
    return response.text

def run_illustrator(state: AgentState) -> str:
    system_prompt = _get_system_prompt(AgentRole.ILLUSTRATOR.value)
    
    user_prompt = f"""
    Help us find illustrations for this sermon section.
    
    === Source Notes ===
    {state.source_notes}
    
    === Theological Core ===
    {state.theological_analysis}
    
    Suggest 3-5 vivid illustrations.
    """
    
    response = gemini_client.generate_raw(
        model=MODEL_ID,
        contents=[types.Content(role="user", parts=[types.Part.from_text(text=user_prompt)])],
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.9
        )
    )
    return response.text

def run_homiletician_beat(state: AgentState, beat_content: str, previous_text: str) -> str:
    system_prompt = _get_system_prompt(AgentRole.HOMILETICIAN.value)
    
    user_prompt = f"""
    write the manuscript for the following section (Beat).
    
    === Global Context ===
    Series: {state.sermon_series_title}
    Theme: {state.sermon_series_description}
    
    === Background Research ===
    [Exegetical Notes]: {state.exegetical_notes[:500]}... (truncated for focus)
    [Theological Notes]: {state.theological_analysis[:500]}...
    [Illustrations Available]: {state.illustration_ideas}
    
    === Previous Spoken Context (Maintain Flow) ===
    {previous_text[-1000:] if previous_text else "This is the beginning."}
    
    === Section to Write Now ===
    {beat_content}
    
    REMINDER: Write a FULL MANUSCRIPT. No outlines. Speak to the people.
    """
    
    response = gemini_client.generate_raw(
        model=MODEL_ID,
        contents=[types.Content(role="user", parts=[types.Part.from_text(text=user_prompt)])],
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.7
        )
    )
    return response.text

def run_critic_check(text: str) -> bool:
    system_prompt = _get_system_prompt(AgentRole.CRITIC.value)
    
    user_prompt = f"""
    Review this text. Does it meet the criteria of being a "Spoken Manuscript"?
    
    CRITERIA:
    1. No bullet points.
    2. No "In summary" or "Outline:" headers.
    3. Proper paragraph structure.
    4. Sufficient length/detail.
    
    TEXT TO REVIEW:
    {text}
    
    Respond with exactly "PASS" or "FAIL". If FAIL, add a reason after a newline.
    """
    
    response = gemini_client.generate_raw(
        model=MODEL_ID,
        contents=[types.Content(role="user", parts=[types.Part.from_text(text=user_prompt)])],
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.0
        )
    )
    
    output = response.text.strip().upper()
    return output.startswith("PASS")

def identify_beats(state: AgentState) -> List[str]:
    system_prompt = _get_system_prompt(AgentRole.STRUCTURING_SPECIALIST.value)
    
    user_prompt = f"""
    Please structure the following raw notes into beats.
    
    === Raw Notes ===
    {state.source_notes}
    === End Notes ===
    """
    
    try:
        response = gemini_client.generate_raw(
            model=MODEL_ID,
            contents=[types.Content(role="user", parts=[types.Part.from_text(text=user_prompt)])],
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.1,
                response_mime_type="application/json"
            )
        )
        
        # Parse JSON
        text = response.text
        try:
            data = json.loads(text)
            splits = data.get("splits", [])
        except:
             text_clean = text.replace("```json", "").replace("```", "").strip()
             try:
                data = json.loads(text_clean)
                splits = data.get("splits", [])
             except:
                splits = []
             
        if not splits:
            return [state.source_notes]
            
        full_text = state.source_notes
        split_indices = [0]
        
        current_search_idx = 0
        
        for split in splits:
            prev_end = split.get("prev_end", "").strip()
            next_start = split.get("next_start", "").strip()
            
            if not prev_end or not next_start:
                continue
                
            # Find prev_end first
            end_idx = full_text.find(prev_end, current_search_idx)
            if end_idx == -1:
                end_idx = full_text.find(prev_end[-10:], current_search_idx)
            
            if end_idx != -1:
                split_point_candidate = end_idx + len(prev_end)
                next_val_idx = full_text.find(next_start, split_point_candidate)
                
                if next_val_idx != -1 and (next_val_idx - split_point_candidate) < 500:
                    split_indices.append(next_val_idx)
                    current_search_idx = next_val_idx + 1
                else:
                    split_indices.append(split_point_candidate)
                    current_search_idx = split_point_candidate
            else:
                 next_val_idx = full_text.find(next_start, current_search_idx)
                 if next_val_idx != -1:
                     split_indices.append(next_val_idx)
                     current_search_idx = next_val_idx + 1

        beats = []
        split_indices.sort()
        split_indices.append(None)
        
        for i in range(len(split_indices) - 1):
            start = split_indices[i]
            end = split_indices[i+1]
            segment = full_text[start:end].strip()
            if segment:
                beats.append(segment)
                
        return beats
        
    except Exception as e:
        print(f"Structuring Error: {e}")
        return [state.source_notes]
