import json
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel
import uuid
from datetime import datetime

from backend.api.config import DATA_BASE_PATH

PROMPTS_FILE = DATA_BASE_PATH / "prompts.json"

class Prompt(BaseModel):
    id: str
    name: str # e.g. "Default Style", "Expository", "Casual"
    content: str # The system prompt text
    created_at: str
    updated_at: str
    is_default: bool = False
    temperature: float = 0.7
    role: str = "drafter" # exegete, theologian, illustrator, drafter, critic

# --- DEFAULT PROMPTS (Traditional Chinese) ---

PROMPT_SEEDS = []

# Legacy fallback for single-agent path in sermon_converter_service.py
DEFAULT_SYSTEM_PROMPT = "你是一位專業的講稿撰寫者。請將提供的筆記轉化為完整的講稿。"

def _load_prompts() -> List[dict]:
    if not PROMPTS_FILE.exists():
        return []
    with open(PROMPTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def _save_prompts(prompts: List[dict]):
    with open(PROMPTS_FILE, "w", encoding="utf-8") as f:
        json.dump(prompts, f, indent=2, ensure_ascii=False)

def init_default_prompt():
    prompts = _load_prompts()
    
    # Check if we need to seed any missing roles
    existing_roles = set()
    for p in prompts:
        # Backwards compatibility: if no role, assume drafter
        if "role" not in p:
             p["role"] = "drafter"
        existing_roles.add(p.get("role", "drafter"))
    
    # Also check generic default existence for safety
    has_default = any(p.get("is_default") for p in prompts)
    
    updated = False
    
    for seed in PROMPT_SEEDS:
        # Check if a prompt with this role exists
        existing = next((p for p in prompts if p.get("role") == seed["role"]), None)
        
        if not existing:
            # Create new
            new_prompt = Prompt(
                id=str(uuid.uuid4()),
                name=seed["name"],
                content=seed["content"],
                created_at=datetime.utcnow().isoformat(),
                updated_at=datetime.utcnow().isoformat(),
                is_default=(seed["role"] == "drafter" and not has_default),
                temperature=1.0 if seed["role"] in ["drafter", "illustrator"] else 0.7,
                role=seed["role"]
            )
            prompts.append(new_prompt.dict())
            updated = True
        else:
            # Auto-update seeded prompts if they are "defaults" or just generic roles we manage?
            # Let's say if the name matches the seed name, we update the content. 
            # This allows us to push updates to prompts without user manual intervention, 
            # BUT users might have edited them.
            # Compromise: Update if the content is drastically different? 
            # Or just rely on user deleting it.
            # User specifically asked for this change now. I should force it for the 'structuring_specialist' role at least.
            if existing.get("role") == "structuring_specialist" and existing.get("content") != seed["content"]:
                 existing["content"] = seed["content"]
                 existing["name"] = seed["name"] # Sync name too
                 existing["updated_at"] = datetime.utcnow().isoformat()
                 updated = True

    if updated:
        _save_prompts(prompts)


def list_prompts() -> List[Prompt]:
    init_default_prompt()
    data = _load_prompts()
    return [Prompt(**d) for d in data]

def get_prompt(prompt_id: str) -> Optional[Prompt]:
    prompts = list_prompts()
    for p in prompts:
        if p.id == prompt_id:
            return p
    return None

def create_prompt(name: str, content: str, temperature: float = 0.7, role: str = "drafter") -> Prompt:
    prompts = _load_prompts()
    new_prompt = Prompt(
        id=str(uuid.uuid4()),
        name=name,
        content=content,
        temperature=temperature,
        role=role,
        created_at=datetime.utcnow().isoformat(),
        updated_at=datetime.utcnow().isoformat()
    )
    prompts.append(new_prompt.dict())
    _save_prompts(prompts)
    return new_prompt

def update_prompt(prompt_id: str, name: str, content: str, temperature: float, role: str) -> Optional[Prompt]:
    data = _load_prompts()
    updated_item = None
    for item in data:
        if item["id"] == prompt_id:
            item["name"] = name
            item["content"] = content
            item["temperature"] = temperature
            item["role"] = role
            item["updated_at"] = datetime.utcnow().isoformat()
            updated_item = Prompt(**item)
            break
    
    if updated_item:
        _save_prompts(data)
        
    return updated_item

def delete_prompt(prompt_id: str) -> bool:
    data = _load_prompts()
    # Prevent deleting default? (optional constraint, let's allow for flexibility but maybe warn in UI)
    # Actually let's keep it simple.
    
    new_data = [d for d in data if d["id"] != prompt_id]
    if len(new_data) < len(data):
        _save_prompts(new_data)
        return True
    return False
