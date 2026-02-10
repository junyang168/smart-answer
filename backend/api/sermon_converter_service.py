from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import List, Optional, Dict, Any
from pydantic import BaseModel

from google import genai
from google.genai import types
import git
import google.auth
from googleapiclient.discovery import build

from backend.api.config import DATA_BASE_PATH, GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION, GOOGLE_DRIVE_FOLDER_ID, FULL_ARTICLE_ROOT

# Define the source directory for notes
IMAGES_ROOT = FULL_ARTICLE_ROOT / "images" / "scanned_mat"

# Define the output directory
NOTES_TO_SERMON_DIR = DATA_BASE_PATH / "notes_to_surmon"

class NoteImage(BaseModel):
    filename: str
    path: str
    processed: bool = False
    folder: Optional[str] = None

class Segment(BaseModel):
    id: str
    raw_text: str
    refined_text: str
    status: str = "PENDING"

class SermonProject(BaseModel):
    id: str
    title: str
    pages: List[str] # List of filenames like ["1-01.jpeg", "1-02.jpeg"]
    processing: bool = False
    google_doc_id: Optional[str] = None
    progress: Optional[Dict[str, Any]] = None # e.g. {"current": 1, "total": 10} or {"stage": "...", "progress": ...}
    processing_status: Optional[str] = None
    processing_progress: Optional[int] = None
    processing_error: Optional[str] = None
    bible_verse: Optional[str] = None
    prompt_id: Optional[str] = None
    series_id: Optional[str] = None
    lecture_id: Optional[str] = None

def ensure_dirs():
    if not NOTES_TO_SERMON_DIR.exists():
        NOTES_TO_SERMON_DIR.mkdir(parents=True, exist_ok=True)
    # New flat directory for processed OCR files
    raw_ocr_dir = NOTES_TO_SERMON_DIR / "raw_ocr"
    raw_ocr_dir.mkdir(exist_ok=True)
    return raw_ocr_dir

def get_raw_ocr_path(filename: str, folder: str = "") -> Path:
    """
    Get the flat path for a page's markdown file.
    Flattens the path (folder + filename) to avoid subdirectories in raw_ocr.
    """
    raw_dir = ensure_dirs()
    full_path_str = filename
    if folder:
        full_path_str = f"{folder}/{filename}"
    
    # Sanitize: replace / and \ with _
    safe_name = full_path_str.replace("/", "_").replace("\\", "_")
    return raw_dir / f"{safe_name}.md"

def list_note_images(folder: str = "") -> List[NoteImage]:
    """
    List all images in the source directory and check if processed.
    If folder is provided, list images in that subdirectory of IMAGES_ROOT.
    """
    target_dir = IMAGES_ROOT
    if folder:
        target_dir = target_dir / folder
    
    if not target_dir.exists():
        return []
    
    extensions = {'.jpg', '.jpeg', '.png'}
    images = []
    
    # Sort alphabetically
    for f in sorted(list(target_dir.iterdir()), key=lambda p: p.name):
        if f.is_file() and f.suffix.lower() in extensions:
            # Construct relative filename
            rel_filename = f.name
            if folder:
                rel_filename = f"{folder}/{f.name}"
                
            # Check flat path using relative filename (folder="" because it's in filename)
            is_processed = get_raw_ocr_path(rel_filename).exists()
            images.append(NoteImage(
                filename=rel_filename,
                path=str(f),
                processed=is_processed,
                folder=folder
            ))
    return images

def process_note_image(filename: str, folder: str = "") -> str:
    """
    Process a single image using Gemini 3 Pro.
    Returns the process_id (which currently is just the filename stem).
    """
    ensure_dirs()
    
    image_path = get_image_path(filename, folder)
    output_file = get_raw_ocr_path(filename, folder)
    
    # Init Gemini Client
    client = genai.Client(
        vertexai=True,
        project=GOOGLE_CLOUD_PROJECT,
        location="global" 
    )

    model_id = "gemini-3-pro-preview"

    # 3. Read Image
    with open(image_path, "rb") as img_f:
        image_data = img_f.read()
    
    # Determine mime_type
    ext = image_path.suffix.lower()
    mime_type = "image/png" if ext == ".png" else "image/jpeg"

    # 4. Construct Prompt (Traditional Chinese)
    prompt = """
    請將此手寫/掃描的講義頁面轉錄為結構化的 Markdown。
    請務必保留邏輯層級、希臘文註釋以及任何經文對照表的結構化大綱，完全按照原樣呈現。
    識別箭頭/線條為邏輯連接詞（例如『導致』、『對比』）。
    將頁邊筆記視為對正文的權威修訂或補充，直接整合進去。
    直接輸出 Markdown 內容即可，不要包含任何開場白或結尾語。
    """

    # 5. Call API
    response = client.models.generate_content(
        model=model_id,
        contents=[
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=prompt),
                    types.Part.from_bytes(data=image_data, mime_type=mime_type)
                ]
            )
        ]
    )
    
    raw_markdown = response.text

    # Save Raw OCR to flat path
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(raw_markdown)
        
    return filename

def get_image_path(filename: str, folder: str = "") -> Path:
    """
    Return the absolute path to the source image.
    """
    target_dir = IMAGES_ROOT
    if folder:
        target_dir = target_dir / folder
        
    image_path = target_dir / filename
    if not image_path.exists():
        raise FileNotFoundError(f"Image {filename} not found in {target_dir}.")
    return image_path

def get_page_segments(filename: str) -> List[Segment]:
    """
    Retrieve segments for a page from the flat raw OCR file.
    """
    ocr_file = get_raw_ocr_path(filename)
    
    if not ocr_file.exists():
        return []
        
    with open(ocr_file, "r", encoding="utf-8") as f:
        content = f.read()
        
    # TODO: Implement actual segmentation logic here or read from a saved JSON
    # For V1, return the whole thing as one big segment to refine
    return [
        Segment(
            id="1",
            raw_text=content,
            refined_text=content.strip(),
            status="PENDING_REVIEW"
        )
    ]

def create_sermon_project(title: str, pages: List[str], series_id: Optional[str] = None, lecture_id: Optional[str] = None) -> SermonProject:
    """
    Create a new sermon project.
    1. Create a folder for the sermon.
    2. Read logical order of pages.
    3. Concatenate their 'raw_ocr.md' into one 'unified_source.md'.
    """
    ensure_dirs()
    
    # Simple ID generation from title
    sermon_id = title.lower().replace(" ", "_").replace(":", "")
    sermon_dir = NOTES_TO_SERMON_DIR / sermon_id
    sermon_dir.mkdir(exist_ok=True)
    
    
    unified_content = f"# {title}\n\n"
    
    # Save metadata
    import json
    metadata = {
        "id": sermon_id,
        "title": title,
        "pages": pages,
        "series_id": series_id,
        "lecture_id": lecture_id
    }
    with open(sermon_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    # Build source (without auto-process)
    _rebuild_unified_source(sermon_id, pages)
    
    # Sync with Lecture Manager if linked
    if series_id and lecture_id:
        try:
            from backend.api.lecture_manager import assign_project_to_lecture
            # Note: assign_project_to_lecture might call back to update_sermon_project_linking, 
            # but that just updates meta which we just wrote. It handles redundancy.
            # But wait, assign_project_to_lecture calls update_sermon_project_linking.
            # update_sermon_project_linking reads meta, updates it, writes it.
            # We just wrote meta. So we are fine.
            # However, to avoid double writing, maybe we rely on assign_project_to_lecture to update the meta link?
            # assign_project_to_lecture ADDS the project to the lecture list.
            # And it calls update_sermon_project_linking(project_id, series_id, lecture_id) to update project meta.
            # So actually, we don't need to write series_id/lecture_id in the initial dump if we call assign.
            # BUT, if assign fails, we want it recorded? 
            # safely: write it, then call assign. Assign will overwrite meta with same values.
            assign_project_to_lecture(series_id, lecture_id, sermon_id)
        except Exception as e:
            print(f"Failed to auto-link project to lecture: {e}")
        
    return SermonProject(**metadata)

def _rebuild_unified_source(sermon_id: str, pages: List[str]):
    """
    Helper to reconstruct the unified markdown file from current pages.
    """
    sermon_dir = NOTES_TO_SERMON_DIR / sermon_id
    # Get title from meta if possible, or parsing not needed if we just overwrite content
    # But we want to keep the Title header.
    # Let's read the current title from meta
    meta_file = sermon_dir / "meta.json"
    import json
    title = "Sermon Project"
    if meta_file.exists():
        with open(meta_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            title = data.get("title", title)

    unified_content = f"# {title}\n\n"

    for filename in pages:
        raw_ocr_file = get_raw_ocr_path(filename)
        
        if raw_ocr_file.exists():
            with open(raw_ocr_file, "r", encoding="utf-8") as f:
                page_text = f.read()
            unified_content += f"\n\n<!-- Page: {filename} -->\n\n"
            unified_content += page_text
        else:
            unified_content += f"\n\n<!-- Page: {filename} (Not Processed) -->\n\n"

    unified_file = sermon_dir / "unified_source.md"
    with open(unified_file, "w", encoding="utf-8") as f:
        f.write(unified_content)

def _inject_newly_processed_pages(sermon_id: str, processed_files: List[str]):
    """
    Safely injects content for newly processed files into existing unified_source.md
    by replacing the (Not Processed) placeholder.
    Preserves user edits in other parts of the file.
    """
    sermon_dir = NOTES_TO_SERMON_DIR / sermon_id
    unified_file = sermon_dir / "unified_source.md"
    
    if not unified_file.exists():
        return

    with open(unified_file, "r", encoding="utf-8") as f:
        content = f.read()
        
    updated = False
    for filename in processed_files:
        placeholder = f"<!-- Page: {filename} (Not Processed) -->"
        if placeholder in content:
            # Read new content
            raw_ocr_file = get_raw_ocr_path(filename)
            if raw_ocr_file.exists():
                with open(raw_ocr_file, "r", encoding="utf-8") as rf:
                    page_text = rf.read()
                
                new_block = f"<!-- Page: {filename} -->\n\n{page_text}"
                content = content.replace(placeholder, new_block)
                updated = True
                
    if updated:
        with open(unified_file, "w", encoding="utf-8") as f:
            f.write(content)

def update_sermon_pages(sermon_id: str, action: str, filename: str) -> SermonProject:
    """
    Add or Remove a page from the project.
    """
    sermon_dir = NOTES_TO_SERMON_DIR / sermon_id
    meta_file = sermon_dir / "meta.json"
    if not meta_file.exists():
        raise FileNotFoundError("Project not found")
        
    import json
    with open(meta_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    pages = data.get("pages", [])
    
    if action == "add":
        if filename not in pages:
            pages.append(filename)
            pages.sort() # Keep sorted or append? User might want custom order. 
            # For now, let's just append. User requested "Add/Remove". 
            # If we want to maintain file order, simple sort might be better for "Chapters"
            pages.sort() 
    elif action == "remove":
        if filename in pages:
            pages.remove(filename)
            
    data["pages"] = pages
    
    # Save Meta
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        
    # Rebuild Source
    _rebuild_unified_source(sermon_id, pages)
    
    return SermonProject(**data)

def trigger_project_page_ocr(sermon_id: str, filename: str):
    """
    Run OCR for a specific page in a project and update the unified source.
    """
    # 1. Process
    process_note_image(filename)
    
    # 2. Rebuild Source (to include the new text)
    # We need to get the current pages list
    sermon_dir = NOTES_TO_SERMON_DIR / sermon_id
    meta_file = sermon_dir / "meta.json"
    if meta_file.exists():
        import json
        with open(meta_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        _rebuild_unified_source(sermon_id, data.get("pages", []))

def update_sermon_processing_status(sermon_id: str, is_processing: bool, progress: Optional[Dict[str, Any]] = None, error: Optional[str] = None):
    """
    Update the processing status in meta.json.
    """
    sermon_dir = NOTES_TO_SERMON_DIR / sermon_id
    meta_file = sermon_dir / "meta.json"
    if meta_file.exists():
        import json
        with open(meta_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        data["processing"] = is_processing
        
        # 1. Update fields from progress if provided (regardless of processing state)
        if progress:
             data["progress"] = progress
             if "stage" in progress:
                 data["processing_status"] = str(progress["stage"])
             if "progress" in progress and isinstance(progress["progress"], (int, float)):
                 data["processing_progress"] = int(progress["progress"])
             
             # Support "current"/"total" style
             if "current" in progress and "total" in progress:
                 try:
                     pct = int((progress["current"] / progress["total"]) * 100)
                     data["processing_progress"] = pct
                     data["processing_status"] = f"Processing {progress['current']}/{progress['total']}"
                 except:
                     pass
        
        # 2. Handle Completion/Stopping
        if not is_processing:
            # Only clear fields if NO final progress was provided (i.e. cancelled or silent stop)
            # If we passed {"stage": "Complete", "progress": 100}, we WANT to keep it.
            if not progress:
                keys_to_remove = ["progress", "processing_status", "processing_progress"]
                for k in keys_to_remove:
                    if k in data: del data[k]

            # Save error if provided
            if error:
                data["processing_error"] = error
            elif "processing_error" in data:
                # Clear previous error on success (if no new error)
                del data["processing_error"]
        
        # 3. If processing is True, definitely clear OLD errors
        elif is_processing:
            if "processing_error" in data:
                 del data["processing_error"]
              
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

def trigger_project_batch_ocr(sermon_id: str):
    """
    Run OCR for ALL unprocessed pages in a project.
    """
    sermon_dir = NOTES_TO_SERMON_DIR / sermon_id
    meta_file = sermon_dir / "meta.json"
    if not meta_file.exists():
        return
        
    import json
    with open(meta_file, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    pages = data.get("pages", [])
    
    # Calculate total work first
    unprocessed_pages = []
    for filename in pages:
        if not get_raw_ocr_path(filename).exists():
             unprocessed_pages.append(filename)
             
    total_files = len(unprocessed_pages)
    current_count = 0

    # Mark as processing with init progress
    update_sermon_processing_status(sermon_id, True, {"current": 0, "total": total_files})
    
    try:
        newly_processed = []
        for filename in pages:
            # Check if updated progress is needed
            # We iterate all pages but only count the ones we actually process
            is_processed = get_raw_ocr_path(filename).exists()
            
            # If not processed (or it was just in our unprocessed list), process it
            if filename in unprocessed_pages: 
                # Double check existence to be safe or just rely on list
                if not get_raw_ocr_path(filename).exists():
                    print(f"Batch processing: {filename}")
                    try:
                        current_count += 1
                        # Update progress before starting heavy work? Or after?
                        # Usually user wants to see "Processing 1/5".
                        update_sermon_processing_status(sermon_id, True, {"current": current_count, "total": total_files})
                        
                        process_note_image(filename)
                        newly_processed.append(filename)
                    except Exception as e:
                        print(f"Error processing {filename}: {e}")
            else:
                pass

        _inject_newly_processed_pages(sermon_id, pages)

    finally:
        # Mark as done
        update_sermon_processing_status(sermon_id, False)

def get_sermon_source(sermon_id: str) -> str:
    sermon_dir = NOTES_TO_SERMON_DIR / sermon_id
    unified_file = sermon_dir / "unified_source.md"
    if not unified_file.exists():
        return ""
    with open(unified_file, "r", encoding="utf-8") as f:
        return f.read()

def save_sermon_source(sermon_id: str, content: str) -> bool:
    """
    Overwrite the unified source file with new content.
    """
    sermon_dir = NOTES_TO_SERMON_DIR / sermon_id
    if not sermon_dir.exists():
        raise FileNotFoundError(f"Sermon project {sermon_id} not found")
        
    unified_file = sermon_dir / "unified_source.md"
    with open(unified_file, "w", encoding="utf-8") as f:
        f.write(content)
    return True

def get_sermon_draft_path(sermon_id: str) -> Path:
    sermon_dir = NOTES_TO_SERMON_DIR / sermon_id
    return sermon_dir / "draft_v1.md"

def reset_agent_state(sermon_id: str):
    """
    Reset the multi-agent system state for a project, allowing a fresh restart.
    Deletes agent_state.json and agent_logs.json, and resets metadata status.
    """
    sermon_dir = NOTES_TO_SERMON_DIR / sermon_id
    
    # 1. Delete State File
    state_file = sermon_dir / "agent_state.json"
    if state_file.exists():
        state_file.unlink()
        
    # 2. Delete Logs (New Path)
    new_logs = sermon_dir / "agent_logs.json"
    if new_logs.exists():
        new_logs.unlink()
        
    # 3. Delete Logs (Legacy Path) - cleanup
    legacy_logs = DATA_BASE_PATH / "sermon_projects" / sermon_id / "agent_logs.json"
    if legacy_logs.exists():
        legacy_logs.unlink()
        
    # 4. Reset Metadata
    meta_file = sermon_dir / "meta.json"
    if meta_file.exists():
        import json
        with open(meta_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        # Clear processing flags
        keys_to_clear = [
            "processing", 
            "processing_status", 
            "processing_progress", 
            "processing_error",
            "progress" # Clear detailed progress dict
        ]
        
        for k in keys_to_clear:
            if k in data:
                del data[k]
                
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

def get_agent_state_data(sermon_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve the full agent state JSON for inspection.
    """
    sermon_dir = NOTES_TO_SERMON_DIR / sermon_id
    state_file = sermon_dir / "agent_state.json"
    if not state_file.exists():
        return None
    import json
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return None

def get_sermon_draft(sermon_id: str) -> str:
    draft_file = get_sermon_draft_path(sermon_id)
    if not draft_file.exists():
        return ""
    with open(draft_file, "r", encoding="utf-8") as f:
        return f.read()

def save_sermon_draft(sermon_id: str, content: str) -> bool:
    """
    Overwrite the draft file with new content.
    """
    sermon_dir = NOTES_TO_SERMON_DIR / sermon_id
    if not sermon_dir.exists():
        raise FileNotFoundError(f"Sermon project {sermon_id} not found")
        
    draft_file = get_sermon_draft_path(sermon_id)
    with open(draft_file, "w", encoding="utf-8") as f:
        f.write(content)
    return True

    return True

def split_markdown_by_headings(content: str) -> List[str]:
    """
    Split markdown content into sections based on headers (H1, H2).
    Simple heuristic: Split by lines starting with '# ' or '## '.
    Returns a list of section strings.
    """
    import re
    # Split by looking ahead for a header at the start of a line
    # We want to keep the delimiter (the header) with the section.
    # Pattern: (?=^#+ ) - matches position before a header
    # But python re.split with lookahead might trigger empty strings.
    
    # A safer manual approach for reliability:
    lines = content.split('\n')
    sections = []
    current_section = []
    
    for line in lines:
        # Check if line identifies a new major section (H1 or H2)
        # We assume H1 is title, but notes often have # I. Introduction
        if (line.startswith("# ") or line.startswith("## ")) and current_section:
            # If we have accumulated content, push it and start new
            sections.append("\n".join(current_section))
            current_section = []
            
        current_section.append(line)
        
    if current_section:
        sections.append("\n".join(current_section))
        
    # Filter empty sections
    return [s for s in sections if s.strip()]

def generate_sermon_draft(sermon_id: str, prompt_id: Optional[str] = None) -> str:
    """
    Generate the sermon draft using Gemini.
    Uses iterative "Split & Merge" strategy for deep generation.
    """
    try:
        # 1. Mark as processing
        update_sermon_processing_status(sermon_id, True)

        # Save used prompt ID to metadata
        if prompt_id:
            sermon_dir = NOTES_TO_SERMON_DIR / sermon_id
            meta_file = sermon_dir / "meta.json"
            if meta_file.exists():
                import json
                with open(meta_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data["prompt_id"] = prompt_id
                with open(meta_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)

        source_content = get_sermon_source(sermon_id)
        if not source_content:
            update_sermon_processing_status(sermon_id, False)
            raise ValueError("Unified source is empty. Please process images first.")
            
        # Init Gemini Client
        client = genai.Client(
            vertexai=True,
            project=GOOGLE_CLOUD_PROJECT,
            location="global" 
        )
        model_id = "gemini-3-pro-preview"
        
        
        # Resolve Prompt
        from backend.api.prompt_manager import get_prompt, DEFAULT_SYSTEM_PROMPT
        
        system_prompt = DEFAULT_SYSTEM_PROMPT
        generation_temperature = 0.7
        
        if prompt_id:
            p = get_prompt(prompt_id)
            if p:
               system_prompt = p.content
               generation_temperature = p.temperature

        # --- Iterative Generation Strategy ---
        sections = split_markdown_by_headings(source_content)
        
        # If too few sections or short content, just do one pass (fallback)
        if len(sections) <= 1:
            total_sections = 1
            update_sermon_processing_status(sermon_id, True, {"current": 0, "total": 1})
            
            response = client.models.generate_content(
                model=model_id,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=generation_temperature
                ),
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(text=f"這是我的講道筆記，請幫我撰寫講章草稿：\n\n{source_content}")
                        ]
                    )
                ]
            )
            draft_content = response.text
        else:
            # Multi-pass generation
            total_sections = len(sections)
            generated_parts = []
            
            print(f"Generating draft in {total_sections} sections for {sermon_id}")
            
            for i, section in enumerate(sections):
                # Update progress
                update_sermon_processing_status(sermon_id, True, {"current": i + 1, "total": total_sections})
                
                # Context aware prompt
                # To maintain context, we provide the FULL notes as reference, 
                # AND the previously generated content to ensure flow.
                
                previous_context = ""
                if generated_parts:
                    # Provide the last 1000 chars or full previous content? 
                    # Gemini has huge window, let's provide everything generated so far to ensure perfect flow.
                    previous_text = "\n\n".join(generated_parts)
                    previous_context = f"""
                    === 前面已撰寫的內容（Previously Generated） ===
                    {previous_text}
                    === 前面內容結束 ===
                    """
                
                iterative_prompt = f"""
                你是正在撰寫一篇長講章的大師。
                
                這是整份『原始講義筆記』（Unified Manuscript）供你參考上下文（Global Context）：
                === 完整筆記開始 ===
                {source_content}
                === 完整筆記結束 ===
                
                {previous_context}
                
                現在，請專注於撰寫第 {i+1}/{total_sections} 部分。
                
                === 當前需撰寫的段落（Current Section） ===
                {section}
                
                **任務要求**：
                1. 請參考「完整筆記」與「前面已撰寫的內容」。
                2. 確保語氣、用詞與前文連貫，不要與前文重複，而是順暢地接續下去。
                3. **只輸出** 「當前需撰寫的段落」 的擴寫內容。
                4. 必須極度詳盡，保留所有希臘文/希伯來文的釋經細節。
                """
                
                response = client.models.generate_content(
                    model=model_id,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=generation_temperature
                    ),
                    contents=[
                        types.Content(
                            role="user",
                            parts=[
                                types.Part.from_text(text=iterative_prompt)
                            ]
                        )
                    ]
                )
                generated_parts.append(response.text)
                
            draft_content = "\n\n".join(generated_parts)

        save_sermon_draft(sermon_id, draft_content)
        return draft_content

    except Exception as e:
        print(f"Error generating draft for {sermon_id}: {e}")
        raise e
    finally:
        # 2. Mark as done
        update_sermon_processing_status(sermon_id, False)

def get_sermon_images(sermon_id: str) -> List[str]:
    """
    Return ordered list of image filenames for this sermon.
    """
    sermon_dir = NOTES_TO_SERMON_DIR / sermon_id
    meta_file = sermon_dir / "meta.json"
    if not meta_file.exists():
        return []
    import json
    with open(meta_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("pages", [])

def get_sermon_project_metadata(sermon_id: str) -> Optional[SermonProject]:
    """
    Get full metadata for a sermon project.
    """
    sermon_dir = NOTES_TO_SERMON_DIR / sermon_id
    meta_file = sermon_dir / "meta.json"
    if not meta_file.exists():
        return None
        
    import json
    with open(meta_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return SermonProject(**data)

def list_sermon_projects() -> List[SermonProject]:
    """
    List all sermon projects.
    """
    ensure_dirs()
    projects = []
    
    for d in NOTES_TO_SERMON_DIR.iterdir():
        if d.is_dir() and d.name != "raw_ocr":
            meta_file = d / "meta.json"
            if meta_file.exists():
                try:
                    import json
                    with open(meta_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    projects.append(SermonProject(**data))
                except Exception as e:
                    print(f"Error reading project {d.name}: {e}")
                    
    return sorted(projects, key=lambda p: p.title)

def commit_sermon_project(sermon_id: str) -> str:
    """
    Commit the sermon project to the local git repository.
    Files committed: meta.json, unified_source.md, draft_v1.md (if exists)
    """
    sermon_dir = NOTES_TO_SERMON_DIR / sermon_id
    if not sermon_dir.exists():
        raise FileNotFoundError(f"Sermon project {sermon_id} not found")

    meta_file = sermon_dir / "meta.json"
    unified_file = sermon_dir / "unified_source.md"
    draft_file = sermon_dir / "draft_v1.md"
    
    # Get title for commit message
    title = sermon_id
    if meta_file.exists():
        import json
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                title = data.get("title", sermon_id)
        except:
            pass

    repo = git.Repo(DATA_BASE_PATH)
    
    paths_to_add = []
    if meta_file.exists():
        paths_to_add.append(str(meta_file))
    if unified_file.exists():
        paths_to_add.append(str(unified_file))
    if draft_file.exists():
        paths_to_add.append(str(draft_file))
        
    if not paths_to_add:
        raise ValueError("No files found to commit for this project")

    repo.index.add(paths_to_add)
    
    if not repo.index.diff("HEAD"):
            return "Nothing to commit"

    commit = repo.index.commit(f"Update sermon project: {title}")
    return str(commit.hexsha)


def update_sermon_project_metadata(sermon_id: str, title: str, bible_verse: Optional[str] = None) -> SermonProject:
    """
    Update the project metadata (title, bible_verse).
    """
    sermon_dir = NOTES_TO_SERMON_DIR / sermon_id
    if not sermon_dir.exists():
        raise FileNotFoundError(f"Sermon project {sermon_id} not found")
        
    meta_file = sermon_dir / "meta.json"
    import json
    data = {}
    if meta_file.exists():
        with open(meta_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            
    data["title"] = title
    if bible_verse:
        data["bible_verse"] = bible_verse
    elif "bible_verse" in data:
        # If explicitly passed as None (or empty string handled by caller), should we delete?
        # Let's assume input reflects desired state. 
        # But python Optional default is None. 
        # Let's trust that if the key is updated, we keep it. 
        # Actually simplest to just set it if provided.
        # If user wants to clear it, they might send empty string.
        pass
        
    # Standardize: Always write what we have
    data["bible_verse"] = bible_verse
    
    # Ensure ID is present (it might be missing in old meta files)
    if "id" not in data:
        data["id"] = sermon_id
    
    # Ensure pages is present
    if "pages" not in data:
        data["pages"] = []

    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        
    try:
        return SermonProject(**data)
    except Exception as e:
        # If validation fails, we shouldn't just crash with 500 without details
        raise ValueError(f"Metadata Validation Error: {e}")


def update_sermon_project_linking(sermon_id: str, series_id: Optional[str], lecture_id: Optional[str]) -> bool:
    """
    Update the linking of a project to a Series/Lecture.
    """
    sermon_dir = NOTES_TO_SERMON_DIR / sermon_id
    if not sermon_dir.exists():
        return False
        
    meta_file = sermon_dir / "meta.json"
    import json
    data = {}
    if meta_file.exists():
        with open(meta_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            
    data["series_id"] = series_id
    data["lecture_id"] = lecture_id
    
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return True


def export_sermon_to_doc(sermon_id: str) -> str:
    """
    Export the sermon draft to a Google Doc in the configured Drive folder.
    Returns the URL of the created document.
    """
    # 1. Read Draft Content + Metadata
    sermon_dir = NOTES_TO_SERMON_DIR / sermon_id
    if not sermon_dir.exists():
         raise FileNotFoundError(f"Sermon project {sermon_id} not found")
    
    draft_file = sermon_dir / "draft_v1.md"
    if not draft_file.exists():
        raise ValueError("Draft not found. Please generate a draft first.")
        
    with open(draft_file, "r", encoding="utf-8") as f:
        draft_content = f.read()

    meta_file = sermon_dir / "meta.json"
    title = f"Sermon Draft: {sermon_id}"
    
    import json
    meta_data = {}
    if meta_file.exists():
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                meta_data = json.load(f)
                title = meta_data.get("title", title)
        except:
            pass
            
    existing_doc_id = meta_data.get("google_doc_id")

    # 2. Authenticate

    SCOPES = ['https://www.googleapis.com/auth/documents', 'https://www.googleapis.com/auth/drive']
    
    # Try using OAuth Token First (Desktop App)
    import os
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    
    # Locate token.json relative to project root or config
    # We assume it's in DATA_BASE_PATH's parent or specific location. 
    # For now, mimic config assumption: base_dir
    # Best to use consistent path derived from config.
    token_path = DATA_BASE_PATH.parent / "token.json" 
    # Or just assume standard working dir if not robust? 
    # Let's use absolute path logic similar to service_account.json
    # Users/junyang/app/smart-answer/token.json
    # DATA_BASE_PATH is /opt/homebrew/var/www/church/web/data ... wait.
    # User's project root is /Users/junyang/app/smart-answer
    # We should look in the project root.
    # But DATA_BASE_PATH is configured to /opt/...
    # Let's rely on finding it near config.py or CWD?
    # Safer: Check CWD and Env Var.
    # Or hardcode for this user's fix first? No, let's use CWD as fallback.
    
    creds = None
    possible_token = Path("token.json").resolve()
    if possible_token.exists():
         try:
             creds = Credentials.from_authorized_user_file(str(possible_token), SCOPES)
         except Exception as e:
             print(f"Failed to load token.json: {e}")
             
    if not creds:
        # Fallback to Service Account / ADC
        credentials, _ = google.auth.default(scopes=SCOPES)
        creds = credentials
    
    # We primarily need Drive API for file creation/import
    drive_service = build('drive', 'v3', credentials=creds)
    
    # 3. Convert Markdown to HTML
    import markdown
    from io import BytesIO
    from googleapiclient.http import MediaIoBaseUpload
    
    html_body = markdown.markdown(draft_content, extensions=['tables', 'footnotes'])
    
    # Wrap in full HTML with styles to enforce spacing
    html_content = f"""
    <html>
    <head>
    <style>
        body {{ font-family: 'Arial'; font-size: 11pt; line-height: 1.5; }}
        p {{ margin-bottom: 12pt; }}
        h1 {{ font-size: 24pt; margin-top: 24pt; margin-bottom: 12pt; }}
        h2 {{ font-size: 18pt; margin-top: 18pt; margin-bottom: 8pt; }}
        h3 {{ font-size: 14pt; margin-top: 14pt; margin-bottom: 6pt; }}
        ul, ol {{ margin-bottom: 12pt; }}
        li {{ margin-bottom: 4pt; }}
    </style>
    </head>
    <body>
    {html_body}
    </body>
    </html>
    """
    
    # 5. Create Or Update
    media = MediaIoBaseUpload(
        BytesIO(html_content.encode('utf-8')),
        mimetype='text/html',
        resumable=True
    )
    
    file_id = None
    
    try:
        # A. Try to Update if ID exists
        if existing_doc_id:
            try:
                # We use update to overwrite content. 
                # Note: For Google Docs, updating 'media_body' requires using the underlying file update 
                # but depending on if it's a proprietary Doc or just a file. 
                # Actually, standard 'update' with uploadType=media works for many types, 
                # but for Native Google Docs, simple full overwrite via Drive API might be tricky without 'convert'.
                # However, since we originally created it with import/convert, let's see.
                # A safer way to "Overwrite" a Google Doc with HTML is tricky. 
                # The Drive API 'update' method CAN update content of standard files, but for Docs it might just append or be complex.
                # Actually, the simplest way to "replace" content of a Google Doc is to use the Docs API to delete everything and insert.
                # BUT, given we are converting HTML -> Doc, using Drive API 'update' with 'newRevision' might work if we treat it as an import?
                # No, Drive API update with conversion is not standard.
                
                # ALTERNATE STRATEGY for UPDATE: Use Docs API to clear and replace?
                # Or just check if valid, if so, maybe just creating new one is actually better for "Draft v2"?
                # User wants "Only ONE google doc".
                # Let's try to just update the file metadata/content using Drive API. 
                # If we send HTML, Drive helper usually converts it on Create. On Update? 
                
                # Let's try to update the content.
                drive_service.files().update(
                    fileId=existing_doc_id,
                    media_body=media
                ).execute()
                file_id = existing_doc_id
                print(f"Updated existing doc: {file_id}")
            except Exception as update_err:
                print(f"Failed to update existing doc (maybe deleted?): {update_err}")
                # Fallback to create new
                existing_doc_id = None
        
        # B. Create New if needed
        if not existing_doc_id:
            file_metadata = {
                'name': title,
                'mimeType': 'application/vnd.google-apps.document'
            }
            if GOOGLE_DRIVE_FOLDER_ID:
                file_metadata['parents'] = [GOOGLE_DRIVE_FOLDER_ID]
                
            file = drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, webViewLink'
            ).execute()
            file_id = file.get('id')
            
            # Save the new ID to meta
            meta_data["google_doc_id"] = file_id
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(meta_data, f, indent=2)

    except Exception as e:
        if "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in str(e):
             raise RuntimeError(
                 "Authentication Error: Insufficient permissions."
             )
        raise e

    return f"https://docs.google.com/document/d/{file_id}/edit"


def refine_sermon_draft(sermon_id: str, selection: str, instruction: str) -> str:
    """
    Refine a specific selection of the sermon draft using Gemini.
    Returns the refined text (only the replacement for the selection).
    """
    try:
        # Init Gemini Client
        client = genai.Client(
            vertexai=True,
            project=GOOGLE_CLOUD_PROJECT,
            location="global" 
        )
        model_id = "gemini-3-pro-preview"
        
        # Construct Prompt
        # We need to give context if possible, but for now let's treat the selection as the primary context
        # to ensure the focus is on rewriting just that part.
        system_prompt = """
        你是專業的寫作編輯與講道學導師。你的任務是根據用戶的指示，重寫或修改提供的文本片段。
        
        原則：
        1. 直接輸出修改後的文本，不要有任何開場白或解釋。
        2. 保持講道的語氣（權威、慈愛、口語化敘事）。
        3. 如果用戶提供外部筆記或資料，請將其整併入選取的段落中，使其流暢連貫。
        """
        
        user_prompt = f"""
        指示 (Instruction): {instruction}
        
        選取的文本 (Selected Text):
        {selection}
        
        請根據指示重寫上述選取的文本：
        """
        
        response = client.models.generate_content(
            model=model_id,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.7 
            ),
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=user_prompt)]
                )
            ]
        )
        
        return response.text.strip()

    except Exception as e:
        print(f"Error refining draft for {sermon_id}: {e}")
        raise e
