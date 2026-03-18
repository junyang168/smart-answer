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
    audit_passed: Optional[bool] = None
    project_type: str = "sermon_note" 

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
    
    # Recursively find all images
    all_files = []
    for ext in extensions:
        all_files.extend(target_dir.rglob(f"*{ext}"))
        all_files.extend(target_dir.rglob(f"*{ext.upper()}"))
        
    # Remove duplicates and sort by relative path
    all_files = sorted(list(set(all_files)), key=lambda p: str(p))
    
    for f in all_files:
        if f.is_file():
            # Construct relative filename from IMAGES_ROOT
            try:
                rel_filename = str(f.relative_to(IMAGES_ROOT))
            except ValueError:
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

def create_sermon_project(title: str, pages: List[str], series_id: Optional[str] = None, lecture_id: Optional[str] = None, project_type: str = "sermon_note") -> SermonProject:
    """
    Create a new sermon project.
    1. Create a folder for the sermon.
    2. Read logical order of pages.
    3. Concatenate their 'raw_ocr.md' into one 'unified_source.md'.
    """
    ensure_dirs()
    
    # Simple ID generation from title
    project_id = title.lower().replace(" ", "_").replace(":", "")
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
    sermon_dir.mkdir(exist_ok=True)
    
    
    unified_content = f"# {title}\n\n"
    
    # Save metadata
    import json
    metadata = {
        "id": project_id,
        "title": title,
        "pages": pages,
        "series_id": series_id,
        "lecture_id": lecture_id,
        "project_type": project_type
    }
    with open(sermon_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    # Build source (without auto-process)
    _rebuild_unified_source(project_id, pages)
    
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
            assign_project_to_lecture(series_id, lecture_id, project_id)
        except Exception as e:
            print(f"Failed to auto-link project to lecture: {e}")
        
    return SermonProject(**metadata)

def _rebuild_unified_source(project_id: str, pages: List[str]):
    """
    Helper to reconstruct the unified markdown file from current pages.
    """
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
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

def _inject_newly_processed_pages(project_id: str, processed_files: List[str]):
    """
    Safely injects content for newly processed files into existing unified_source.md
    by replacing the (Not Processed) placeholder.
    Preserves user edits in other parts of the file.
    """
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
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

def update_sermon_pages(project_id: str, action: str, filename: str) -> SermonProject:
    """
    Add or Remove a page from the project.
    """
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
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
    _rebuild_unified_source(project_id, pages)
    
    return SermonProject(**data)

def trigger_project_page_ocr(project_id: str, filename: str):
    """
    Run OCR for a specific page in a project and update the unified source.
    """
    # 1. Process
    process_note_image(filename)
    
    # 2. Inject the newly processed text into the existing source
    _inject_newly_processed_pages(project_id, [filename])

def update_sermon_processing_status(project_id: str, is_processing: bool, progress: Optional[Dict[str, Any]] = None, error: Optional[str] = None):
    """
    Update the processing status in meta.json.
    """
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
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

def trigger_project_batch_ocr(project_id: str):
    """
    Run OCR for ALL unprocessed pages in a project.
    """
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
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
    update_sermon_processing_status(project_id, True, {"current": 0, "total": total_files})
    
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
                        update_sermon_processing_status(project_id, True, {"current": current_count, "total": total_files})
                        
                        process_note_image(filename)
                        newly_processed.append(filename)
                    except Exception as e:
                        print(f"Error processing {filename}: {e}")
            else:
                pass

        _inject_newly_processed_pages(project_id, pages)

    finally:
        # Mark as done
        update_sermon_processing_status(project_id, False)

def get_sermon_source(project_id: str) -> str:
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
    unified_file = sermon_dir / "unified_source.md"
    if not unified_file.exists():
        return ""
    with open(unified_file, "r", encoding="utf-8") as f:
        return f.read()

def extract_processable_content(content: str) -> str:
    """
    Extracts content up to the `<!-- Ignore Below-->` marker.
    If the marker is not found, returns the entire content.
    """
    import re
    match = re.search(r'<!--\s*Ignore Below\s*-->', content, re.IGNORECASE)
    if match:
        return content[:match.start()].strip()
    return content.strip()

def save_sermon_source(project_id: str, content: str) -> bool:
    """
    Overwrite the unified source file with new content.
    """
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
    if not sermon_dir.exists():
        raise FileNotFoundError(f"Sermon project {project_id} not found")
        
    unified_file = sermon_dir / "unified_source.md"
    with open(unified_file, "w", encoding="utf-8") as f:
        f.write(content)
    return True

def get_sermon_draft_path(project_id: str) -> Path:
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
    return sermon_dir / "draft_v1.md"

def reset_agent_state(project_id: str):
    """
    Reset the multi-agent system state for a project, allowing a fresh restart.
    Deletes agent_state.json and agent_logs.json, and resets metadata status.
    """
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
    
    # 1. Delete State File
    state_file = sermon_dir / "agent_state.json"
    if state_file.exists():
        state_file.unlink()
        
    # 2. Delete Logs (New Path)
    new_logs = sermon_dir / "agent_logs.json"
    if new_logs.exists():
        new_logs.unlink()
        
    # 3. Delete Logs (Legacy Path) - cleanup
    legacy_logs = DATA_BASE_PATH / "sermon_projects" / project_id / "agent_logs.json"
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

def get_agent_state_data(project_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve the full agent state JSON for inspection.
    """
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
    state_file = sermon_dir / "agent_state.json"
    if not state_file.exists():
        return None
    import json
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return None

def get_sermon_draft(project_id: str) -> str:
    draft_file = get_sermon_draft_path(project_id)
    if not draft_file.exists():
        return ""
    with open(draft_file, "r", encoding="utf-8") as f:
        return f.read()

def save_sermon_draft(project_id: str, content: str) -> bool:
    """
    Overwrite the draft file with new content, and simultaneously split into chunks.
    """
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
    if not sermon_dir.exists():
        raise FileNotFoundError(f"Sermon project {project_id} not found")
        
    draft_file = get_sermon_draft_path(project_id)
    with open(draft_file, "w", encoding="utf-8") as f:
        f.write(content)
        
    # Also chunk the draft immediately
    chunks = split_markdown_for_review(content)
    meta_json = chunks_to_jsonable(chunks)
    
    meta_file = get_draft_chunks_meta_path(project_id)
    import json
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(meta_json, f, indent=2, ensure_ascii=False)
        
    chunks_dir = get_sermon_draft_chunks_dir(project_id)
    for c in chunks:
        c_file = chunks_dir / f"{c.id}.md"
        with open(c_file, "w", encoding="utf-8") as f:
            f.write(c.text)
            
    return True

def get_sermon_draft_chunks_dir(project_id: str) -> Path:
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
    chunks_dir = sermon_dir / "draft_chunks"
    chunks_dir.mkdir(exist_ok=True)
    return chunks_dir

def get_draft_chunks_meta_path(project_id: str) -> Path:
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
    return sermon_dir / "draft_chunks_meta.json"

def get_draft_chunks(project_id: str) -> list[dict]:
    meta_file = get_draft_chunks_meta_path(project_id)
    if not meta_file.exists():
        # Fallback if draft exists but no chunks (e.g., legacy drafts)
        draft_content = get_sermon_draft(project_id)
        if draft_content:
            save_sermon_draft(project_id, draft_content)
        else:
            return []
            
    import json
    chunks_dir = get_sermon_draft_chunks_dir(project_id)
    # read meta
    with open(meta_file, "r", encoding="utf-8") as f:
        meta_data = json.load(f)
        
    for item in meta_data:
        c_file = chunks_dir / f"{item['id']}.md"
        if c_file.exists():
            with open(c_file, "r", encoding="utf-8") as f:
                item["content"] = f.read()
        else:
            item["content"] = ""
    return meta_data

def update_draft_chunk(project_id: str, chunk_id: str, new_text: str) -> bool:
    chunks_dir = get_sermon_draft_chunks_dir(project_id)
    c_file = chunks_dir / f"{chunk_id}.md"
    if not c_file.exists():
        raise FileNotFoundError(f"Draft chunk {chunk_id} not found")
        
    with open(c_file, "w", encoding="utf-8") as f:
        f.write(new_text)
        
    # Rebuild draft
    rebuild_draft_from_chunks(project_id)
    return True

def rebuild_draft_from_chunks(project_id: str):
    meta_file = get_draft_chunks_meta_path(project_id)
    chunks_dir = get_sermon_draft_chunks_dir(project_id)
    if not meta_file.exists():
        return
        
    import json
    with open(meta_file, "r", encoding="utf-8") as f:
        meta_data = json.load(f)
        
    full_text = []
    for item in meta_data:
        c_file = chunks_dir / f"{item['id']}.md"
        if c_file.exists():
            with open(c_file, "r", encoding="utf-8") as f:
                full_text.append(f.read())
                
    content = "\n\n".join(full_text)
    draft_file = get_sermon_draft_path(project_id)
    with open(draft_file, "w", encoding="utf-8") as f:
        f.write(content)


def get_sermon_final_path(project_id: str) -> Path:
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
    return sermon_dir / "final.md"

def get_sermon_chunks_dir(project_id: str) -> Path:
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
    chunks_dir = sermon_dir / "chunks"
    chunks_dir.mkdir(exist_ok=True)
    return chunks_dir

def get_chunks_meta_path(project_id: str) -> Path:
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
    return sermon_dir / "chunks_meta.json"

import re
from dataclasses import dataclass
from typing import Tuple

@dataclass
class Chunk:
    id: str
    title: str
    level: int                 # heading level (2 for ##, 3 for ###)
    start_line: int            # 1-based
    end_line: int              # 1-based, inclusive
    text: str

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)\s*$")

def _find_heading_level(line: str) -> Optional[Tuple[int, str]]:
    m = _HEADING_RE.match(line)
    if not m:
        return None
    level = len(m.group(1))
    title = m.group(2).strip()
    return level, title

def split_markdown_for_review(
    md: str,
    primary_level: int = 2,            # "##"
    secondary_level: int = 3,          # "###" used if chunk too large
    max_chars: int = 9000,             # rough size guard for one review request
    keep_preamble_as_chunk: bool = True
) -> List[Chunk]:
    lines = md.splitlines()
    n = len(lines)
    primary_idxs: List[int] = []
    for i, line in enumerate(lines):
        info = _find_heading_level(line)
        if info and info[0] == primary_level:
            primary_idxs.append(i)

    chunks: List[Chunk] = []
    def emit_chunk(start_i: int, end_i: int, title: str, level: int, chunk_id: str):
        text = "\n".join(lines[start_i:end_i+1]).strip("\n")
        if not text.strip():
            return
        chunks.append(
            Chunk(id=chunk_id, title=title, level=level, start_line=start_i + 1, end_line=end_i + 1, text=text)
        )

    if keep_preamble_as_chunk:
        if primary_idxs and primary_idxs[0] > 0:
            pre_text = "\n".join(lines[:primary_idxs[0]]).strip()
            if pre_text:
                emit_chunk(0, primary_idxs[0] - 1, "(Preamble / Front Matter)", 1, "chunk_000")
        elif not primary_idxs:
            emit_chunk(0, n - 1, "(Whole Document)", 1, "chunk_000")
            return chunks

    if not primary_idxs:
        emit_chunk(0, n - 1, "(Whole Document)", 1, "chunk_000")
        return chunks

    primary_ranges: List[Tuple[int, int]] = []
    for idx, start_i in enumerate(primary_idxs):
        end_i = (primary_idxs[idx + 1] - 1) if idx + 1 < len(primary_idxs) else (n - 1)
        primary_ranges.append((start_i, end_i))

    chunk_counter = 1
    for start_i, end_i in primary_ranges:
        info = _find_heading_level(lines[start_i])
        primary_title = info[1] if info else "(Untitled)"
        primary_text = "\n".join(lines[start_i:end_i+1])
        if len(primary_text) <= max_chars:
            emit_chunk(start_i, end_i, primary_title, primary_level, f"chunk_{chunk_counter:03d}")
            chunk_counter += 1
            continue
        
        secondary_idxs: List[int] = []
        for i in range(start_i + 1, end_i + 1):
            info2 = _find_heading_level(lines[i])
            if info2 and info2[0] == secondary_level:
                secondary_idxs.append(i)

        if not secondary_idxs:
            emit_chunk(start_i, end_i, f"{primary_title} (oversized)", primary_level, f"chunk_{chunk_counter:03d}")
            chunk_counter += 1
            continue

        header_end = secondary_idxs[0] - 1
        emit_chunk(start_i, header_end, f"{primary_title} — (intro)", primary_level, f"chunk_{chunk_counter:03d}")
        chunk_counter += 1

        for s_idx, s_start in enumerate(secondary_idxs):
            s_end = (secondary_idxs[s_idx + 1] - 1) if s_idx + 1 < len(secondary_idxs) else end_i
            s_info = _find_heading_level(lines[s_start])
            s_title = s_info[1] if s_info else "(Untitled Subsection)"
            emit_chunk(s_start, s_end, f"{primary_title} — {s_title}", secondary_level, f"chunk_{chunk_counter:03d}")
            chunk_counter += 1
    return chunks

def chunks_to_jsonable(chunks: List[Chunk]) -> List[Dict]:
    return [
        {
            "id": c.id, "title": c.title, "level": c.level, 
            "start_line": c.start_line, "end_line": c.end_line, "char_len": len(c.text)
        }
        for c in chunks
    ]

def start_theological_review(project_id: str) -> bool:
    """
    Copy draft_v1.md to final.md, split into chunks, and save them.
    """
    draft_file = get_sermon_draft_path(project_id)
    final_file = get_sermon_final_path(project_id)
    meta_file = get_chunks_meta_path(project_id)
    chunks_dir = get_sermon_chunks_dir(project_id)
    
    if not draft_file.exists():
        raise FileNotFoundError(f"Draft not found for project {project_id}")
        
    if not final_file.exists():
        import shutil
        shutil.copy2(draft_file, final_file)
        
    # Always regenerate chunks if meta_file doesn't exist or we just created final.md
    if not meta_file.exists() or not final_file.exists():
        with open(final_file, "r", encoding="utf-8") as f:
            md_content = f.read()
            
        chunks = split_markdown_for_review(md_content)
        meta_json = chunks_to_jsonable(chunks)
        
        import json
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(meta_json, f, indent=2, ensure_ascii=False)
            
        for c in chunks:
            chunk_path = chunks_dir / f"{c.id}.md"
            with open(chunk_path, "w", encoding="utf-8") as f:
                f.write(c.text.strip() + "\n")
                
    return True

def rebuild_final_from_chunks(project_id: str):
    """
    Reads all chunks from chunks_dir according to chunks_meta.json 
    and concatenates them to rebuild final.md.
    """
    final_file = get_sermon_final_path(project_id)
    meta_file = get_chunks_meta_path(project_id)
    chunks_dir = get_sermon_chunks_dir(project_id)
    
    if not meta_file.exists():
        return
        
    import json
    with open(meta_file, "r", encoding="utf-8") as f:
        meta_json = json.load(f)
        
    rebuilt_content = []
    for chunk_meta in meta_json:
        chunk_id = chunk_meta["id"]
        chunk_path = chunks_dir / f"{chunk_id}.md"
        if chunk_path.exists():
            with open(chunk_path, "r", encoding="utf-8") as f:
                rebuilt_content.append(f.read().strip())
                
    with open(final_file, "w", encoding="utf-8") as f:
        f.write("\n\n".join(rebuilt_content) + "\n")

def get_final_chunks(project_id: str) -> List[Dict]:
    meta_file = get_chunks_meta_path(project_id)
    chunks_dir = get_sermon_chunks_dir(project_id)
    
    # Auto-generate chunks if not present but final.md is
    if not meta_file.exists():
        final_file = get_sermon_final_path(project_id)
        if final_file.exists():
            with open(final_file, "r", encoding="utf-8") as f:
                md_content = f.read()
                
            chunks = split_markdown_for_review(md_content)
            meta_json = chunks_to_jsonable(chunks)
            
            import json
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(meta_json, f, indent=2, ensure_ascii=False)
                
            for c in chunks:
                chunk_path = chunks_dir / f"{c.id}.md"
                with open(chunk_path, "w", encoding="utf-8") as f:
                    f.write(c.text.strip() + "\n")
        else:
            return []
    
    import json
    with open(meta_file, "r", encoding="utf-8") as f:
        meta_json = json.load(f)
        
    results = []
    for m in meta_json:
        chunk_path = chunks_dir / f"{m['id']}.md"
        content = ""
        if chunk_path.exists():
             with open(chunk_path, "r", encoding="utf-8") as f:
                 content = f.read()
        results.append({
            **m,
            "content": content
        })
    return results

def update_final_chunk(project_id: str, chunk_id: str, content: str) -> bool:
    chunks_dir = get_sermon_chunks_dir(project_id)
    chunk_path = chunks_dir / f"{chunk_id}.md"
    
    if not chunk_path.exists():
        raise FileNotFoundError(f"Chunk {chunk_id} not found")
        
    with open(chunk_path, "w", encoding="utf-8") as f:
         f.write(content.strip() + "\n")
    return True

def get_sermon_final(project_id: str) -> str:
    final_file = get_sermon_final_path(project_id)
    if not final_file.exists():
        return ""
    with open(final_file, "r", encoding="utf-8") as f:
        return f.read()

def save_sermon_final(project_id: str, content: str) -> bool:
    """
    Overwrite the final file with new content.
    """
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
    if not sermon_dir.exists():
        raise FileNotFoundError(f"Sermon project {project_id} not found")
        
    final_file = get_sermon_final_path(project_id)
    with open(final_file, "w", encoding="utf-8") as f:
        f.write(content)
    return True
def get_sermon_audit_result(project_id: str, chunk_id: str) -> Optional[dict]:
    """
    Load the persisted fidelity audit result (fidelity_audit.json) and return it as a dictionary for a specific chunk.
    Returns the dict if it exists, otherwise None.
    """
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
    audit_file = sermon_dir / "fidelity_audit.json"
    
    if not audit_file.exists():
        return None
        
    try:
        import json
        with open(audit_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get(chunk_id)
    except Exception as e:
        print(f"Error reading fidelity_audit.json: {e}")
        return None



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

def generate_sermon_draft(project_id: str, prompt_id: Optional[str] = None) -> str:
    """
    Generate the sermon draft using Gemini.
    Uses iterative "Split & Merge" strategy for deep generation.
    """
    try:
        # 1. Mark as processing
        update_sermon_processing_status(project_id, True)

        # Save used prompt ID to metadata
        if prompt_id:
            sermon_dir = NOTES_TO_SERMON_DIR / project_id
            meta_file = sermon_dir / "meta.json"
            if meta_file.exists():
                import json
                with open(meta_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data["prompt_id"] = prompt_id
                with open(meta_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)

        source_content = get_sermon_source(project_id)
        if not source_content:
            update_sermon_processing_status(project_id, False)
            raise ValueError("Unified source is empty. Please process images first.")
            
        source_content = extract_processable_content(source_content)
            
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
            update_sermon_processing_status(project_id, True, {"current": 0, "total": 1})
            
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
            
            print(f"Generating draft in {total_sections} sections for {project_id}")
            
            for i, section in enumerate(sections):
                # Update progress
                update_sermon_processing_status(project_id, True, {"current": i + 1, "total": total_sections})
                
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

        save_sermon_draft(project_id, draft_content)
        return draft_content

    except Exception as e:
        print(f"Error generating draft for {project_id}: {e}")
        raise e
    finally:
        # 2. Mark as done
        update_sermon_processing_status(project_id, False)

def get_sermon_images(project_id: str) -> List[str]:
    """
    Return ordered list of image filenames for this sermon.
    """
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
    meta_file = sermon_dir / "meta.json"
    if not meta_file.exists():
        return []
    import json
    with open(meta_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("pages", [])

def get_sermon_project_metadata(project_id: str) -> Optional[SermonProject]:
    """
    Get full metadata for a sermon project.
    """
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
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

def commit_sermon_project(project_id: str) -> str:
    """
    Commit the sermon project to the local git repository.
    Rebuilds final.md from chunks first.
    """
    rebuild_final_from_chunks(project_id)
    
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
    if not sermon_dir.exists():
        raise FileNotFoundError(f"Sermon project {project_id} not found")

    meta_file = sermon_dir / "meta.json"
    unified_file = sermon_dir / "unified_source.md"
    draft_file = sermon_dir / "draft_v1.md"
    
    # Get title for commit message
    title = project_id
    if meta_file.exists():
        import json
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                title = data.get("title", project_id)
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
        
    final_file = sermon_dir / "final.md"
    chunks_meta = sermon_dir / "chunks_meta.json"
    audit_file = sermon_dir / "theological_audit.json"
    chunks_dir = sermon_dir / "chunks"
    
    if final_file.exists():
        paths_to_add.append(str(final_file))
    if chunks_meta.exists():
        paths_to_add.append(str(chunks_meta))
    if audit_file.exists():
        paths_to_add.append(str(audit_file))
    if chunks_dir.exists():
        paths_to_add.append(str(chunks_dir))
        
    if not paths_to_add:
        raise ValueError("No files found to commit for this project")

    repo.index.add(paths_to_add)
    
    if not repo.index.diff("HEAD"):
            return "Nothing to commit"

    commit = repo.index.commit(f"Update sermon project: {title}")
    return str(commit.hexsha)


def update_sermon_project_metadata(project_id: str, title: str, bible_verse: Optional[str] = None, google_doc_link: Optional[str] = None) -> SermonProject:
    """
    Update the project metadata (title, bible_verse, google_doc_id).
    """
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
    if not sermon_dir.exists():
        raise FileNotFoundError(f"Sermon project {project_id} not found")
        
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
    
    # Handle google doc link updates
    if google_doc_link is not None:
        if google_doc_link.strip() == "":
            if "google_doc_id" in data:
                del data["google_doc_id"]
        else:
            import re
            match = re.search(r'/document/d/([a-zA-Z0-9-_]+)', google_doc_link)
            if match:
                data["google_doc_id"] = match.group(1)
            else:
                raise ValueError("Invalid Google Doc Link format")
    
    # Ensure ID is present (it might be missing in old meta files)
    if "id" not in data:
        data["id"] = project_id
    
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


def update_sermon_project_type(project_id: str, project_type: str) -> bool:
    """
    Update the project type in metadata.
    """
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
    if not sermon_dir.exists():
        return False
        
    meta_file = sermon_dir / "meta.json"
    import json
    data = {}
    if meta_file.exists():
        with open(meta_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            
    data["project_type"] = project_type
    
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return True


def update_sermon_project_linking(project_id: str, series_id: Optional[str], lecture_id: Optional[str]) -> bool:
    """
    Update the linking of a project to a Series/Lecture.
    """
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
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


def export_sermon_to_doc(project_id: str) -> str:
    """
    Export the sermon draft to a Google Doc in the configured Drive folder.
    Returns the URL of the created document.
    """
    rebuild_final_from_chunks(project_id)
    
    # 1. Read Final Content + Metadata
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
    if not sermon_dir.exists():
         raise FileNotFoundError(f"Sermon project {project_id} not found")
    
    draft_file = sermon_dir / "final.md"
    if not draft_file.exists():
        raise ValueError("Final Master Text not found. Please Start Theological Review first.")
        
    with open(draft_file, "r", encoding="utf-8") as f:
        draft_content = f.read()

    meta_file = sermon_dir / "meta.json"
    title = f"Master Text: {project_id}"
    
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
             
    # Refresh token if expired
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            # Save refreshed token back to token.json
            with open(possible_token, "w") as token_file:
                token_file.write(creds.to_json())
            print("Refreshed OAuth token successfully.")
        except Exception as e:
            print(f"Failed to refresh token: {e}")
            creds = None

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
    
    # Pre-process: Strip leading whitespace from heading lines (AI drafts sometimes indent them)
    import re
    draft_content = re.sub(r'^[ \t]+(#{1,6}\s)', r'\1', draft_content, flags=re.MULTILINE)

    # Pre-process: Force line breaks in blockquotes
    # Find lines starting with > and ensure they end with 2 spaces
    def preserve_blockquote_lines(match):
        line = match.group(0)
        # If line is just ">" or "> " without text, maybe don't add spaces? 
        # But for safety, let's just add spaces to any quote line that has content.
        # Actually, if we just want line breaks, 2 spaces is standard markdown "hard wrapping".
        return line.rstrip() + "  "

    # Regex: Start of line, optional whitespace, >, then content.
    draft_content = re.sub(r'^\s*>.*$', preserve_blockquote_lines, draft_content, flags=re.MULTILINE)
    
    html_body = markdown.markdown(draft_content, extensions=['tables', 'footnotes', 'fenced_code'])
    
    # Process Images (SVG -> PNG, Local -> Base64)
    html_body = _process_images_for_export(html_body)

    # Strip <thead>/<tbody> tags — Google Docs renders them as extra empty rows
    html_body = re.sub(r'</?thead>', '', html_body)
    html_body = re.sub(r'</?tbody>', '', html_body)
    
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
        blockquote {{ margin-left: 20pt; padding-left: 10pt; border-left: 2pt solid #cccccc; color: #555555; background-color: #f9f9f9; padding: 10pt; font-style: italic; }}
        table {{ border-collapse: collapse; width: 100%; margin-bottom: 12pt; }}
        th, td {{ border: 1px solid #000000; padding: 6pt 8pt; text-align: left; vertical-align: top; }}
        th {{ background-color: #f0f0f0; font-weight: bold; }}
        pre {{ background-color: #f4f4f4; padding: 10pt; font-family: 'Courier New', Courier, monospace; white-space: pre-wrap; margin-bottom: 12pt; border-radius: 4pt; }}
        code {{ font-family: 'Courier New', Courier, monospace; background-color: #f4f4f4; padding: 2pt 4pt; border-radius: 2pt; }}
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
            
            target_folder_id = GOOGLE_DRIVE_FOLDER_ID
            if meta_data.get("project_type") == "Fellowship Transcript":
                target_folder_id = "1tizJv2BEql_v0zXehGPSjY2TPnkyGq7t"
                
            if target_folder_id:
                file_metadata['parents'] = [target_folder_id]
                
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

    # Post-process: Shift heading levels down (H1→Title, H2→H1, H3→H2, etc.)
    try:
        heading_remap = {
            'HEADING_1': 'TITLE',
            'HEADING_2': 'HEADING_1',
            'HEADING_3': 'HEADING_2',
            'HEADING_4': 'HEADING_3',
            'HEADING_5': 'HEADING_4',
            'HEADING_6': 'HEADING_5',
        }
        docs_service = build('docs', 'v1', credentials=creds)
        doc = docs_service.documents().get(documentId=file_id).execute()
        requests = []
        for element in doc.get('body', {}).get('content', []):
            paragraph = element.get('paragraph')
            if paragraph:
                named_style = paragraph.get('paragraphStyle', {}).get('namedStyleType')
                new_style = heading_remap.get(named_style)
                if new_style:
                    requests.append({
                        'updateParagraphStyle': {
                            'range': {
                                'startIndex': element.get('startIndex'),
                                'endIndex': element.get('endIndex')
                            },
                            'paragraphStyle': {
                                'namedStyleType': new_style
                            },
                            'fields': 'namedStyleType'
                        }
                    })
        if requests:
            docs_service.documents().batchUpdate(
                documentId=file_id,
                body={'requests': requests}
            ).execute()
    except Exception as e:
        print(f"Warning: Failed to remap heading styles: {e}")

    return f"https://docs.google.com/document/d/{file_id}/edit"


def refine_sermon_draft(project_id: str, selection: str, instruction: str) -> str:
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
        print(f"Error refining draft for {project_id}: {e}")
        raise e

def run_structured_chunk_audit(
    project_id: str,
    chunk_id: str,
    input_text: str,
    system_prompt: str,
    json_schema: dict,
    output_filename: str,
    post_process_hook=None
) -> dict:
    """
    Generic helper to run structured audits (Fidelity or Theological) on chunks.
    Handles the common logic: structured JSON generation, file saving, and error handling.
    """
    import json
    from backend.api.openai_client import generate_structured_json
    
    try:
        sermon_dir = NOTES_TO_SERMON_DIR / project_id
        audit_file = sermon_dir / output_filename
        sermon_dir.mkdir(parents=True, exist_ok=True)
        
        audit_data = generate_structured_json(
            system_prompt=system_prompt,
            user_prompt=input_text,
            json_schema=json_schema.get("json_schema", json_schema),
            model="gpt-5.2",
            temperature=0.0
        )
        
        if post_process_hook:
            audit_data = post_process_hook(audit_data)
            
        try:
            existing_audits = {}
            if audit_file.exists():
                 with open(audit_file, "r", encoding="utf-8") as f:
                      existing_audits = json.load(f)
                      
            existing_audits[chunk_id] = audit_data
            
            with open(audit_file, "w", encoding="utf-8") as f:
                json.dump(existing_audits, f, indent=2, ensure_ascii=False)
                
        except Exception as e:
            print(f"Failed to process audit result for saving ({output_filename}): {e}")
            return {"error": "Failed to process JSON", "raw": str(e)}

        return audit_data

    except Exception as e:
        print(f"Error executing audit for chunk {chunk_id} in {project_id}: {e}")
        return {"error": f"Error executing audit: {str(e)}"}

def fidelity_audit_chunk(project_id: str, chunk_id: str) -> dict:
    """
    Review a specific final chunk against the original notes using OpenAI Structured Outputs.
    Returns the fidelity analysis text as a python dictionary (JSON).
    """
    try:
        source_content = get_sermon_source(project_id)
        
        chunk_file = get_sermon_draft_chunks_dir(project_id) / f"{chunk_id}.md"
        if not chunk_file.exists():
             return {"error": f"Chunk file {chunk_id}.md not found."}
             
        with open(chunk_file, "r", encoding="utf-8") as f:
             draft_content = f.read()

        if not source_content:
            return "Error: Unified source notes are empty."
            
        source_content = extract_processable_content(source_content)

        if not draft_content.strip():
            return "Error: Chunk content is empty. Please edit the chunk before auditing."

        import os
        import json
        from backend.api.openai_client import generate_structured_json
        SYSTEM_PROMPT = """
        你是「释经逐字稿语义忠实度审核器（Diff Auditor）」。

        你的职责只有一个：
        检测【逐字稿】是否忠实于【笔记】的语义内容。

        你不是讲道者，不是改写者，不进行神学评论。

        你只做：
        语义层面的检测与证据列举。

        ────────────────

        【审核背景】

        【逐字稿】是根据【笔记】进行语义扩展生成的。

        允许的扩展：

        - 将碎片笔记扩展为完整句子
        - 添加最小必要的逻辑连接词
        - 引用笔记中列出的经文内容
        - 使用系统固定结构标题：
        「释经」「神学意义」「生活应用」「附录」

        ────────────────

        【不得视为差异】

        以下内容不得列入差异：

        - 固定结构标题
        - Markdown 排版
        - 分段变化
        - 编号变化
        - 经文编号引用
        - 最小必要逻辑连接词

        这些属于格式或表达层面。

        你只审语义。

        ────────────────

        【只允许标记三类语义差异】

        1️⃣ addition  
        逐字稿新增笔记未包含的神学命题或事实内容。

        2️⃣ deletion  
        笔记中的实质要点在逐字稿中消失。

        3️⃣ stance_upgrade  
        笔记中的推测、可能、提示，
        在逐字稿中被强化为确定断言。

        ────────────────

        【审核步骤（必须按顺序执行）】

        请依序执行以下步骤：

        STEP 1  
        逐条检查【笔记要点】，确认逐字稿是否覆盖。

        STEP 2  
        检查逐字稿是否新增笔记未出现的神学命题。

        STEP 3  
        检查逐字稿是否删除笔记的实质要点。

        STEP 4  
        检查逐字稿是否将推测语气强化为断言。

        每一步都必须重新扫描全文。

        ────────────────

        【证据要求】

        每个问题必须提供：

        - 笔记证据
        - 逐字稿证据

        若无法提供明确文本证据，
        请不要标记。

        ────────────────

        【风险分级】

        P0  
        新增或删除核心神学内容

        P1  
        立场升级或明显语气强化

        P2  
        轻微语义偏移

        P3  
        可忽略表达差异

        ────────────────

        【通过标准】

        若存在 P0 或 P1  
        → Revise

        若仅存在 P2 或 P3  
        → Pass

        风险等级优先于评分。

        ────────────────

        你只审语义。
        不审格式。
        不进行神学评价。

        ────────────────

        【定位要求（用于 UI 高亮）】

        每个问题必须提供：

        location  
        使用文本中已有的段落标题或经文范围。

        excerpt  
        逐字稿中的原文片段（20–120 字），
        必须可以通过字符串搜索定位。

        ────────────────

        【修改建议】

        每个问题必须提供 proposed_fix。

        修改建议必须遵循：

        - 最小修改原则
        - 不新增神学内容
        - 不改变原意
        - 只修复语义差异

        修改建议可以是：

        - 删除新增内容
        - 恢复被删除要点
        - 调整语气强度
        
        """
        AUDIT_SCHEMA = {
            "type": "json_schema",
            "json_schema": {
                "name": "matthew_audit_report_v1",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "coverage": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "note_id": {"type": "string"},
                                    "note_excerpt": {"type": "string"},
                                    "matched": {"type": "boolean"},
                                    "transcript_evidence": {"type": "string"}
                                },
                                "required": ["note_id", "note_excerpt", "matched", "transcript_evidence"]
                            }
                        },
                        "diffs": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {

                                    "type": {
                                        "type": "string",
                                        "enum": ["addition","deletion","stance_upgrade"]
                                    },

                                    "risk": {
                                        "type": "string",
                                        "enum": ["P0","P1","P2","P3"]
                                    },

                                    "location": {
                                        "type": "string"
                                    },

                                    "excerpt": {
                                        "type": "string"
                                    },

                                    "note_evidence": {
                                        "type": "string"
                                    },

                                    "transcript_evidence": {
                                        "type": "string"
                                    },

                                    "reason": {
                                        "type": "string"
                                    },

                                    "proposed_fix": {
                                        "type": "string"
                                    }

                                },

                                "required": [
                                    "type",
                                    "risk",
                                    "location",
                                    "excerpt",
                                    "note_evidence",
                                    "transcript_evidence",
                                    "reason",
                                    "proposed_fix"
                                ]
                            }
                        },                        
                        "scores": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "faithfulness": {"type": "number"},
                                "theology_safety": {"type": "number"}
                            },
                            "required": ["faithfulness","theology_safety"]
                        },
                        "pass": {"type": "boolean"},
                        "must_fix": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": ["coverage","diffs","scores","pass","must_fix"]
                }
            }
        }

        user_prompt = f"【笔记】\n{source_content}\n\n【逐字稿】\n{draft_content}"
        
        result = run_structured_chunk_audit(
            project_id=project_id,
            chunk_id=chunk_id,
            input_text=user_prompt,
            system_prompt=SYSTEM_PROMPT,
            json_schema=AUDIT_SCHEMA,
            output_filename="fidelity_audit.json"
        )
        
        # After completing the audit, check if we need to update the global project flag
        check_and_update_project_audit_status(project_id)
        
        return result

    except Exception as e:
        print(f"Error loading source data for fidelity audit {chunk_id} in {project_id}: {e}")
        return {"error": f"Error executing audit: {str(e)}"}

def force_audit_pass(project_id: str, chunk_id: str) -> bool:
    """
    Manually override the fidelity audit status to Passed for a specific chunk.
    Updates fidelity_audit.json.
    """
    import json
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
    if not sermon_dir.exists():
        raise FileNotFoundError(f"Project not found: {project_id}")

    # Update fidelity_audit.json if it exists so the UI reflects it
    audit_file = sermon_dir / "fidelity_audit.json"
    if audit_file.exists():
        with open(audit_file, "r", encoding="utf-8") as f:
            existing_audits = json.load(f)
            
        if chunk_id in existing_audits:
            audit_data = existing_audits[chunk_id]
            audit_data["pass"] = True
            
            # Optionally overwrite must_fix to show why it passed
            if audit_data.get("must_fix") and len(audit_data["must_fix"]) > 0:
                audit_data["must_fix"] = ["(User Overridden) " + x for x in audit_data["must_fix"]]
                
            existing_audits[chunk_id] = audit_data
            
        with open(audit_file, "w", encoding="utf-8") as f:
            json.dump(existing_audits, f, indent=2, ensure_ascii=False)
            
    # Re-evaluate global project audit status
    check_and_update_project_audit_status(project_id)
            
    return True

def check_and_update_project_audit_status(project_id: str) -> bool:
    """
    Checks if all draft chunks have a passed fidelity audit.
    If so, sets the project's meta.json `audit_passed` flag to True.
    """
    import json
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
    if not sermon_dir.exists():
        return False
        
    meta_file = sermon_dir / "meta.json"
    audit_file = sermon_dir / "fidelity_audit.json"
    chunks_meta_file = sermon_dir / "draft_chunks_meta.json"
    
    if not (meta_file.exists() and audit_file.exists() and chunks_meta_file.exists()):
        return False
        
    try:
        with open(chunks_meta_file, "r", encoding="utf-8") as f:
            chunks = json.load(f)
            
        with open(audit_file, "r", encoding="utf-8") as f:
            audits = json.load(f)
            
        all_passed = True
        for chunk in chunks:
            chunk_id = chunk.get("id")
            if not chunk_id:
                continue
            audit = audits.get(chunk_id)
            if not audit or not audit.get("pass"):
                all_passed = False
                break
                
        with open(meta_file, "r", encoding="utf-8") as f:
            meta_data = json.load(f)
            
        if meta_data.get("audit_passed") != all_passed:
            meta_data["audit_passed"] = all_passed
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(meta_data, f, indent=2)
                
        return all_passed
    except Exception as e:
        print(f"Error checking project audit status for {project_id}: {e}")
        return False


# ======================
# Step 3 Review Prompt (繁體中文・最新穩定版)
# ======================
STEP3_REVIEW_PROMPT_ZH_TW = """
你是「福音派釋經母本重大邊界審閱器」。

你不是改寫者，不是風格編輯，不是神學辯論者。
你只負責偵測「明顯、客觀、可直接核對」的重大邊界問題。

總原則（最高優先級）：
- 不確定 → 不列為問題
- 只有在“你能指出明確矛盾或明確錯誤”時才出手
- 你要以“降低誤報率”為首要目標（寧可漏掉，也不要亂報）

————————————————————
【硬性閘門：confidence】
你輸出每一個 issue 時，必須給 0~1 的 confidence。
只有 confidence >= 0.88 才允許輸出該 issue。
若達不到 0.88，請不要輸出（直接略過）。

————————————————————
【禁止列為問題的項目】（極重要，違反即為失敗）
以下全部禁止標記為 issue：

- 正常的福音派神學解釋
- 合理的神學推論（與經文方向一致）
- “可更精確/可補充/可更完整”的學術性建議（這不是錯誤）
- 平行經文範圍的完整度爭議：
  只要作者引用的是「常見主要平行段落」，即使還有更廣對應，也不得標記
- 用語不夠嚴謹但大意正確（例如術語更精確寫法、可改寫更漂亮）：
  一律不得標記 factual_error
- 結構模板（釋經／神學意義／生活應用／附錄）
- 合理的邏輯銜接句（因此/所以/這顯示…）只要沒有捏造新事實
- 已放在 Appendix 的護教/歷史/教會觀察：
  一律視為結構正確；除非“直接違反聖經文本本身”才可標記

————————————————————
【只允許標記以下四類問題】（高門檻）

1) exegesis_error（明顯釋經錯誤）
僅限：
- 明確違反該段經文內容（或該段逐字稿直接引用的經文）
- 將因果關係講反（文本有清楚因果）
- 誤認人物或事件（可直接核對）
- 文本內部自相矛盾（釋經層面）

2) factual_error（客觀事實錯誤，超高門檻）
僅限「客觀且普遍公認，可直接核對」的錯誤：
- 經文引用錯誤：章/節/內容對不上
- 明確的人物識別錯誤：把 A 說成 B（可核對）
- 明確的原文詞義錯誤：把字義講反/杜撰不存在的語義
注意：
- “更精確”≠“錯誤”；不得因措辭不夠學術就標記 factual_error。

3) overstatement（明顯過度推論，高門檻）
僅限：
- 結論無法從本段文本合理推出，且屬明顯跳躍
- 把外段/外系統教義硬套為本段直接結論
注意：
- 概括性神學總結只要不與經文明顯矛盾，不得標記。

4) structural_issue（重大結構錯位）
僅限：
- 嚴重放錯層級，導致讀者必然把“附錄延伸”誤當“經文直接結論”
注意：Appendix 本身不列 structural_issue（除非直接違反經文）。

————————————————————
【定位要求（給 UI 高亮用）】
每個 issue 必須提供：
- location：使用輸入文本中可見的段落標題/小節名/經文範圍（不得編造）
- excerpt：逐字稿中可搜尋定位的原句片段（20–120 字）
- reason：只能基於輸入文本本身（不可引用外部資料/你自己的知識作為判定依據）
- suggested_fix：用一句話描述“如何改到不越界”（不需要重寫全文，只要方向）
- confidence：0~1（低於 0.88 禁止輸出）

————————————————————
【輸出格式】
只輸出 JSON（禁止多餘文字、markdown）：

{
  "issues": [
    {
      "type": "exegesis_error | factual_error | overstatement | structural_issue",
      "location": "...",
      "excerpt": "...",
      "reason": "...",
      "suggested_fix": "...",
      "confidence": 0.0
    }
  ],
  "summary": "總結一句話"
}

若沒有重大問題（或不夠確定），請輸出：

{
  "issues": [],
  "summary": "未發現重大邊界問題。"
}
"""

CONFIDENCE_THRESHOLD = 0.88

REVIEW_SCHEMA = {
    "name": "step3_major_boundary_review",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "issues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": [
                                "exegesis_error",
                                "factual_error",
                                "overstatement",
                                "structural_issue",
                            ],
                        },
                        "location": {"type": "string"},
                        "excerpt": {"type": "string"},
                        "reason": {"type": "string"},
                        "suggested_fix": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["type", "location", "excerpt", "reason", "suggested_fix", "confidence"],
                    "additionalProperties": False,
                },
            },
            "summary": {"type": "string"},
        },
        "required": ["issues", "summary"],
        "additionalProperties": False,
    }
}

def audit_theological_boundary(project_id: str, chunk_id: str) -> dict:
    """
    Step 3 Review (Major Boundary Review)
    Input: a markdown chunk text representing a section of the final text.
    Output: JSON issues list with highlight-friendly location/excerpt.
    """
    try:
        from backend.api.sermon_converter_service import get_sermon_chunks_dir
        chunk_file = get_sermon_chunks_dir(project_id) / f"{chunk_id}.md"
        if not chunk_file.exists():
             return {"error": f"Chunk file {chunk_id}.md not found."}
             
        with open(chunk_file, "r", encoding="utf-8") as f:
             exegesis_markdown = f.read()

        if not exegesis_markdown.strip():
            return {"summary": "No content to review.", "issues": []}

        def _filter_confidence(data: dict) -> dict:
            issues = data.get("issues", [])
            if not isinstance(issues, list):
                issues = []
            
            filtered = []
            for it in issues:
                try:
                    conf = float(it.get("confidence", 0.0))
                except Exception:
                    conf = 0.0
                if conf >= CONFIDENCE_THRESHOLD:
                    filtered.append(it)

            data["issues"] = filtered
            if not filtered:
                 if issues:
                     data["summary"] = "AI 發現了潛在問題，但信心指數未及門檻 (0.88)，因此未列出重大邊界問題。"
                 else:
                     data["summary"] = "未發現重大邊界問題。"
            return data

        return run_structured_chunk_audit(
            project_id=project_id,
            chunk_id=chunk_id,
            input_text=exegesis_markdown,
            system_prompt=STEP3_REVIEW_PROMPT_ZH_TW,
            json_schema=REVIEW_SCHEMA,
            output_filename="theological_audit.json",
            post_process_hook=_filter_confidence
        )

    except Exception as e:
        print(f"Error loading final text chunk for theological audit {project_id}: {e}")
        return {"error": f"Error executing theological audit: {str(e)}"}

def get_theological_audit_result(project_id: str, chunk_id: str) -> Optional[dict]:
    sermon_dir = NOTES_TO_SERMON_DIR / project_id
    audit_file = sermon_dir / "theological_audit.json"
    
    if not audit_file.exists():
        return None
        
    try:
        import json
        with open(audit_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get(chunk_id)
    except Exception as e:
        print(f"Error reading theological_audit.json: {e}")
        return None

def _process_images_for_export(html_content: str) -> str:
    """
    Process HTML to make images compatible with Google Docs:
    1. Resolve local paths (/web/data -> DATA_BASE_PATH)
    2. Convert SVGs to PNGs (using cairosvg)
    3. Embed all images as Base64 Data URIs
    """
    from bs4 import BeautifulSoup
    import base64
    import mimetypes
    import os
    
    # --- MacOS Apple Silicon Cairo Fix ---
    # Attempt to help ctypes find the library on Apple Silicon for cairosvg
    extra_lib_path = "/opt/homebrew/lib"
    if os.path.exists(extra_lib_path):
        os.environ['DYLD_FALLBACK_LIBRARY_PATH'] = extra_lib_path + ":" + os.environ.get('DYLD_FALLBACK_LIBRARY_PATH', '')
        import ctypes
        try:
            ctypes.CDLL("/opt/homebrew/lib/libcairo.2.dylib")
        except:
            try:
                ctypes.CDLL("/opt/homebrew/lib/libcairo.dylib")
            except:
                pass
    
    try:
        import cairosvg
    except ImportError:
        print("Warning: cairosvg not found. SVG conversion will fail.")
        cairosvg = None

    soup = BeautifulSoup(html_content, 'html.parser')
    
    for img in soup.find_all('img'):
        src = img.get('src')
        if not src:
            continue
            
        # Resolve local path
        local_path = None
        if src.startswith('/web/data/'):
            # Map /web/data/ -> DATA_BASE_PATH/
            # DATA_BASE_PATH is /.../web/data
            rel_path = src.replace('/web/data/', '')
            local_path = DATA_BASE_PATH / rel_path
        elif src.startswith('http'):
            # Skip remote images for now (Google Docs handles them if public, but we could embed them too)
            pass
            
        if local_path and local_path.exists():
            try:
                mime_type, _ = mimetypes.guess_type(local_path)
                
                # Special handling for SVG
                if mime_type == 'image/svg+xml' or local_path.suffix.lower() == '.svg':
                    if cairosvg:
                        try:
                            with open(local_path, 'rb') as f:
                                svg_data = f.read()
                            # Convert to PNG
                            png_data = cairosvg.svg2png(bytestring=svg_data)
                            b64_data = base64.b64encode(png_data).decode('utf-8')
                            img['src'] = f"data:image/png;base64,{b64_data}"
                            # Remove height/width attributes if they cause issues, or let Docs handle it?
                            # Usually Docs handles standard images fine.
                        except Exception as e:
                            print(f"Failed to convert SVG {local_path}: {e}")
                    else:
                        print(f"Skipping SVG {local_path} - cairosvg missing")
                else:
                    # Standard Image (JPG, PNG)
                    with open(local_path, "rb") as f:
                        data = f.read()
                        b64_data = base64.b64encode(data).decode('utf-8')
                        if not mime_type: mime_type = 'image/png'
                        img['src'] = f"data:{mime_type};base64,{b64_data}"
                        
            except Exception as e:
                print(f"Error processing image {src}: {e}")
                
    return str(soup)
