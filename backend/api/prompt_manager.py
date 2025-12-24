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

DEFAULT_SYSTEM_PROMPT = """你現在是精通聖經原文的釋經講道大師（類似王守仁教授的風格）。
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
    if not prompts:
        # Create default prompt
        default_prompt = Prompt(
            id=str(uuid.uuid4()),
            name="Default Master Expository",
            content=DEFAULT_SYSTEM_PROMPT,
            created_at=datetime.utcnow().isoformat(),
            updated_at=datetime.utcnow().isoformat(),
            is_default=True,
            temperature=0.7
        )
        _save_prompts([default_prompt.dict()])

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

def create_prompt(name: str, content: str, temperature: float = 0.7) -> Prompt:
    prompts = _load_prompts()
    new_prompt = Prompt(
        id=str(uuid.uuid4()),
        name=name,
        content=content,
        temperature=temperature,
        created_at=datetime.utcnow().isoformat(),
        updated_at=datetime.utcnow().isoformat()
    )
    prompts.append(new_prompt.dict())
    _save_prompts(prompts)
    return new_prompt

def update_prompt(prompt_id: str, name: str, content: str, temperature: float) -> Optional[Prompt]:
    data = _load_prompts()
    updated_item = None
    for item in data:
        if item["id"] == prompt_id:
            item["name"] = name
            item["content"] = content
            item["temperature"] = temperature
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
