from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel

from google import genai
from google.genai import types
import git
import google.auth
from googleapiclient.discovery import build

from backend.api.config import DATA_BASE_PATH, GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION, GOOGLE_DRIVE_FOLDER_ID

# Define the source directory for notes (hardcoded as per user request for now, or could be config)
# User request: /Volumes/Jun SSD/data/scanned_mat/notes_main/chapter5-7
SOURCE_NOTES_DIR = Path("/Volumes/Jun SSD/data/scanned_mat/notes_main/chapter5-7")

# Define the output directory
NOTES_TO_SERMON_DIR = DATA_BASE_PATH / "notes_to_surmon"

class NoteImage(BaseModel):
    filename: str
    path: str
    processed: bool = False

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

def ensure_dirs():
    if not NOTES_TO_SERMON_DIR.exists():
        NOTES_TO_SERMON_DIR.mkdir(parents=True, exist_ok=True)
    # New flat directory for processed OCR files
    raw_ocr_dir = NOTES_TO_SERMON_DIR / "raw_ocr"
    raw_ocr_dir.mkdir(exist_ok=True)
    return raw_ocr_dir

def get_raw_ocr_path(filename: str) -> Path:
    """
    Get the flat path for a page's markdown file.
    e.g. NOTES_TO_SERMON/raw_ocr/1-01.jpeg.md
    """
    raw_dir = ensure_dirs()
    return raw_dir / f"{filename}.md"

def list_note_images() -> List[NoteImage]:
    """
    List all images in the source directory and check if processed.
    """
    if not SOURCE_NOTES_DIR.exists():
        return []
    
    extensions = {'.jpg', '.jpeg', '.png'}
    images = []
    
    # Sort alphabetically
    for f in sorted(list(SOURCE_NOTES_DIR.iterdir()), key=lambda p: p.name):
        if f.is_file() and f.suffix.lower() in extensions:
            # Check flat path
            is_processed = get_raw_ocr_path(f.name).exists()
            images.append(NoteImage(
                filename=f.name,
                path=str(f),
                processed=is_processed
            ))
    return images

def process_note_image(filename: str) -> str:
    """
    Process a single image using Gemini 3 Pro.
    Returns the process_id (which currently is just the filename stem).
    """
    ensure_dirs()
    
    image_path = SOURCE_NOTES_DIR / filename
    if not image_path.exists():
        raise FileNotFoundError(f"Image {filename} not found in source directory.")

    output_file = get_raw_ocr_path(filename)
    
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

def get_image_path(filename: str) -> Path:
    """
    Return the absolute path to the source image.
    """
    image_path = SOURCE_NOTES_DIR / filename
    if not image_path.exists():
        raise FileNotFoundError(f"Image {filename} not found.")
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

def create_sermon_project(title: str, pages: List[str]) -> SermonProject:
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
        "pages": pages
    }
    with open(sermon_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    # Build source (without auto-process)
    _rebuild_unified_source(sermon_id, pages)
        
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

def update_sermon_processing_status(sermon_id: str, is_processing: bool):
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
    
    # Mark as processing
    update_sermon_processing_status(sermon_id, True)
    
    try:
        newly_processed = []
        for filename in pages:
            # Check if already processed
            is_processed = get_raw_ocr_path(filename).exists()
            
            # If not processed, process it
            if not is_processed:
                print(f"Batch processing: {filename}")
                try:
                    process_note_image(filename)
                    newly_processed.append(filename)
                except Exception as e:
                    print(f"Error processing {filename}: {e}")
            else:
                # OPTIONAL: If it IS processed, but the source still has the placeholder, 
                # we should treat it as 'newly processed' for injection purposes to fix 'stale' source.
                # However, this function is 'ocr-all' so checking strict existence is faster.
                # Let's double check if we need to repair.
                pass

        # Always try to inject any page that might be missing in the source but exists on disk
        # We can just iterate ALL pages and try to inject if placeholder exists.
        # This fixes the user's issue where the file existed but source was stale.
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

def generate_sermon_draft(sermon_id: str) -> str:
    """
    Generate the sermon draft using Gemini.
    """
    try:
        # 1. Mark as processing
        update_sermon_processing_status(sermon_id, True)

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
        
        # Construct Prompt
        system_prompt = """
        你現在是精通聖經原文的釋經講道大師（類似王守仁教授的風格）。
        你的任務是將用戶提供的『原始講義筆記』（Unified Manuscript）改寫成一篇『大師級的釋經講章草稿』。
        
        ### 核心原則：
        1. **語氣**：權威、學術嚴謹，但充滿牧者的慈愛與迫切感。
        2. **原文釋經**：當筆記中出現希臘文/希伯來文時，請務必展開解釋其字義、時態或文法的精妙之處（擴寫 200-300 字）。
        3. **例證擴充**：將筆記中簡略的例子（如 "David"）擴寫成生動的歷史或聖經故事。
        4. **應用導向**：每一段釋經最後必須轉向對現代信徒的應用 (Life Application)。使用「弟兄姊妹...」直接對會眾說話。
        5. **敘事流暢**：不要使用條列式（Bullet points），請將其轉化為連貫的口語敘事段落。
        
        ### 格式要求：
        - 使用 Markdown 格式。
        - 保留大綱標題（# I. ...），但在標題下展現豐富的講道內容。
        - 總字數目標：盡量豐富詳盡，不低於 2000 字。
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
                    parts=[
                        types.Part.from_text(text=f"這是我的講道筆記，請幫我撰寫講章草稿：\n\n{source_content}")
                    ]
                )
            ]
        )
        
        draft_content = response.text
        save_sermon_draft(sermon_id, draft_content)
        return draft_content

    except Exception as e:
        print(f"Error generating draft for {sermon_id}: {e}")
        # In a real app we might want to save the error state somewhere
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
    if meta_file.exists():
        import json
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                title = data.get("title", title)
        except:
            pass

    # 2. Authenticate
    SCOPES = ['https://www.googleapis.com/auth/documents', 'https://www.googleapis.com/auth/drive']
    credentials, _ = google.auth.default(scopes=SCOPES)
    
    # We primarily need Drive API for file creation/import
    drive_service = build('drive', 'v3', credentials=credentials)
    
    # 3. Convert Markdown to HTML
    import markdown
    from io import BytesIO
    from googleapiclient.http import MediaIoBaseUpload
    
    html_body = markdown.markdown(draft_content)
    
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
    
    # 4. Create File Metadata
    file_metadata = {
        'name': title,
        'mimeType': 'application/vnd.google-apps.document'
    }
    
    if GOOGLE_DRIVE_FOLDER_ID:
        file_metadata['parents'] = [GOOGLE_DRIVE_FOLDER_ID]
        
    # 5. Create & Upload
    media = MediaIoBaseUpload(
        BytesIO(html_content.encode('utf-8')),
        mimetype='text/html',
        resumable=True
    )
    
    try:
        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink'
        ).execute()
    except Exception as e:
        if "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in str(e):
             raise RuntimeError(
                 "Authentication Error: Insufficient permissions. "
                 "Please run the following command in your terminal to grant access:\n\n"
                 "gcloud auth application-default login --scopes=\"https://www.googleapis.com/auth/drive,https://www.googleapis.com/auth/documents,https://www.googleapis.com/auth/cloud-platform\" --client-id-file=PATH_TO_YOUR_CLIENT_SECRET.json\n\n"
                 "Note: You must create a custom 'Desktop App' OAuth Client ID in your Google Cloud Project to avoid 'App Blocked' errors."
             )
        raise e

    return f"https://docs.google.com/document/d/{file.get('id')}/edit"


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
