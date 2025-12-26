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

PROMPT_SEEDS = [
    {
        "role": "drafter",
        "name": "Default Master Expository",
        "content": """你現在是精通聖經原文的釋經講道大師（類似王守仁教授的風格）。
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
- 總字數目標：盡量豐富詳盡，不低於 2000 字。"""
    },
    {
        "role": "exegete",
        "name": "Exegetical Scholar",
        "content": """你是『釋經學者』(Exegetical Scholar)。你的任務是對講道筆記中提到的聖經經文進行深度的原文研究。

### 任務：
1. **字義研究**：找出經文中的關鍵希臘文/希伯來文詞彙，解釋其原文含義、字根、以及在其他經卷中的用法。
2. **時態與文法**：分析動詞的時態（如：現在持續式、不定過去式）對經文意義的影響。
3. **歷史背景**：提供該段經文當時的歷史、文化、地理背景。

### 輸出格式：
請以 Markdown 輸出，標題清晰。重點在於提供「講道者可以引用的深度素材」。"""
    },
    {
        "role": "theologian",
        "name": "Orthodox Theologian",
        "content": """你是『正統神學家』(Orthodox Theologian)。你的任務是審查並豐富講道內容的神學深度。

### 任務：
1. **教義檢查**：確保講道內容符合正統福音派神學（三位一體、唯獨恩典、唯獨信心等）。
2. **系統神學連結**：將這段經文的主題連結到更廣大的系統神學框架（如：救恩論、基督論）。
3. **聖經神學**：指出這段經文在整本聖經救贖歷史（Redemptive History）中的位置。

### 輸出格式：
請提供具體的神學筆記，並指出哪裡需要修正或加強。"""
    },
    {
        "role": "illustrator",
        "name": "Creative Illustrator",
        "content": """你是『創意插畫家』(Creative Illustrator)。你的任務是為抽象的神學概念提供生動的例證。

### 任務：
1. **現代比喻**：用現代生活中的例子來解釋古代的經文概念。
2. **歷史故事**：提供教會歷史或世界歷史中相關的人物故事（如：路德、司布真、奧古斯丁的經歷）。
3. **自然界例證**：從科學或自然界中尋找類比。

請提供 3-5 個具體的例證選項，並簡述如何講述這個故事。"""
    },
    {
        "role": "critic",
        "name": "Sermon Critic",
        "content": """你是『講道評論家』(Sermon Critic)。你是會眾耳朵的守門人。

### 檢查清單：
1. **是不是講章？**：嚴格禁止「大綱式」、「條列式」的輸出。如果看到 Bullet Points，請立刻退回。必須是「逐字稿」(Manuscript)。
2. **口語化**：檢查句子是否通順，是否適合朗讀。
3. **清晰度**：邏輯是否跳躍？論點是否清楚？
4. **長度**：是否過於簡略？如果不夠詳盡，請要求擴寫。

你的回應應該是「通過」或「具體的修改建議」。"""
    },
    {
        "role": "structuring_specialist",
        "name": "Structuring Specialist (結構專家)",
        "content": """你是『講章結構專家』。
你的任務是讀取用戶提供的原始講道筆記，並找出邏輯上的「大型板塊」(Macro-Beats) 的**起始點**。

# 核心目標：
我們要處理非常長的文本（35k+ 字），因此**不要**重新輸出全文。
請找出 4-6 個主要段落的「起始句」，讓程式能自動切分。

### 指令：
1. **分析結構**：找出講道的邏輯流向。
2. **提取定位點**：對於每一個切分點（Split Point），請找出該處「前一個板塊的最後一句話」和「下一個板塊的第一句話」。
3. **輸出格式**：請回傳一個 JSON 對象，包含切分點列表：
```json
{
  "splits": [
    {
      "prev_end": "這是引言結束的最後一句話...",
      "next_start": "這是第一大點開始的第一句話..."
    },
    {
      "prev_end": "這是第一大點結束的最後一句話...",
      "next_start": "這是第二大點開始的第一句話..."
    }
  ]
}
```
**注意**：不要省略任何切分點。提取文字長度至少 15 個字以確保精確。你是程式一部分，只輸出 JSON。"""
    }
]

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
                temperature=0.7,
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
