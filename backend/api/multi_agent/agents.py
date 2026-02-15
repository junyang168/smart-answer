from typing import List, Dict
import json

from backend.api.multi_agent import llm_client
from backend.api.multi_agent.types import AgentState, AgentRole

# =============================================================================
# Project-Type-Specific Prompts
# =============================================================================

# --- Segmenter Prompts ---

SEGMENTER_SERMON_NOTE_PROMPT = """

你是一位聖經釋經學的資深編輯。


# 核心目標
請分析以下釋經課課堂筆記的結構，將其切割為適合轉換逐字稿的教學單元。

# 切割原則
- 每個單元應是一個完整的教學段落（如：一個主題、一段經文解釋、一個案例分析）
- 每個教學單元 500–1500 字。
- 保持邏輯連貫性，不要在論證中途切割

# 輸出格式
請用 JSON 格式回傳單元列表：
```json
{
  "units": [
    {
      "id": "1",
      "title": "單元標題（從筆記提取或生成）",
      "type": "heading|exegesis|application|contrast|summary",
      "keypoints": "單元主題（從筆記提取或生成）",
      "start_marker": "單元第一句話（至少15字）",
      "end_marker": "單元最後一句話（至少15字）",
      "estimated_words": 800,
      "context_needed": "前一單元的主題（用於銜接）"
    }
  ],
  "total_units": 5
}

"""

SEGMENTER_TRANSCRIPT_PROMPT = """你是『讀經講稿教學單元切割專家』。

# 核心目標
請分析以下牧師講道的逐字稿或筆記的結構，將其切割為適合轉換講稿的教學單元。

# 切割原則
- 每個單元應是一個完整的討論段落（如：一個討論主題、一段經文探討、一個案例分析）
- 每個教學單元 500–1500 字。
- 保持邏輯連貫性，不要在論證中途切割

# 必须遵守
- 最后一个单元的结束位置**必须是文章结尾**

# 輸出格式
請用 JSON 格式回傳單元列表：
```json
{
  "units": [
    {
      "id": "1",
      "title": "單元標題（從牧師講道的逐字稿提取或生成）",
      "type": "heading|discussion|application|contrast|summary",
      "keypoints": "本單元的主要觀點（從牧師講道的逐字稿提取或生成）",
      "start_marker": "單元第一句話（至少15字）",
      "end_marker": "單元最後一句話（至少15字）",
      "estimated_words": 800,
      "context_needed": "前一單元的主要觀點（用於銜接）"
    }
  ],
  "total_units": 5
}

"""

# --- Expander Prompts ---

EXPANDER_SERMON_NOTE_PROMPT = """你是『大師級釋經逐字稿撰寫專家』。

# 核心目標
將提供的「教學單元」（筆記片段）擴展為一段完整的釋經逐字稿。

# 核心原則
1. **忠實吸收**：逐字對應，盡量吸收筆記中的所有細節——經文引用、原文解析、歷史背景、神學觀點、比較分析、列表數據，一個都不可遺漏。
2. **不引入新觀點**：絕對不要引入任何筆記中沒有的新神學觀點、新例證、或新的經文解讀。你的任務是「擴展」而非「創作」。
3. **充分擴展**：詳細解釋每一個神學要點。不要直接呈現筆記原文，而是用流暢的釋經講座語言重新表達，讓聽眾能夠理解深層含義。
4. **行文簡潔流暢**：保持句子之間的邏輯連接和流暢度。語言簡潔有力，不冗贅，但每個要點都要充分展開。
5. **銜接上下文**：如果提供了「前文」，請確保語氣、用詞和邏輯與前文自然銜接，不要重複前文已經講過的內容。

# 格式要求
- 使用 Markdown 格式。
- 使用 `##` 作為大段落標題，`###` 作為子標題。
- **希臘文/希伯來文**：如筆記中出現，必須保留原文（如 ἐπιθυμέω），不可用英文拼音替代。
- 禁止使用 Bullet points 列表。將列表轉化為排比句或分析段落。
- 輸出為適合朗讀的講座逐字稿。"""

EXPANDER_TRANSCRIPT_PROMPT = """你是『讀經講稿撰寫專家』。

# 核心目標
將提供的「教學單元」（團契討論片段）擴展為一段完整的讀經講稿。

# 核心原則
1. **忠實吸收**：盡量吸收討論稿中的所有細節——經文引用、討論觀點、提問與回應、實際應用，一個都不可遺漏。
2. **不引入新觀點**：絕對不要引入任何討論稿中沒有的新神學觀點、新例證、或新的經文解讀。你的任務是「擴展」而非「創作」。
3. **充分擴展**：詳細解釋每一個要點。不要直接呈現原文，而是用流暢的講稿語言重新表達，讓讀者能夠理解討論的深層含義。
4. **行文簡潔流暢**：保持句子之間的邏輯連接和流暢度。語言簡潔有力，不冗贅，但每個要點都要充分展開。
5. **銜接上下文**：如果提供了「前文」，請確保語氣、用詞和邏輯與前文自然銜接，不要重複前文已經講過的內容。

# 格式要求
- 使用 Markdown 格式。
- 使用 `##` 作為大段落標題，`###` 作為子標題。
- 禁止使用 Bullet points 列表。將列表轉化為排比句或分析段落。
- 輸出為適合朗讀的講稿。"""


# =============================================================================
# Agent Functions
# =============================================================================

def segment_notes(state: AgentState) -> List[Dict]:
    """Phase 1: Split notes into teaching units using project-type-specific prompt."""
    
    if state.project_type == "transcript":
        system_prompt = SEGMENTER_TRANSCRIPT_PROMPT
    else:
        system_prompt = SEGMENTER_SERMON_NOTE_PROMPT
    
    user_prompt = f"""請將以下筆記切割為教學單元。

=== 原始筆記 ===
{state.source_notes}
=== 筆記結束 ===
"""
    
    try:
        text = llm_client.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.1,
            json_mode=True,
        )
        try:
            data = json.loads(text)
        except Exception as e1:
            print(f"Segmenter: JSON parse failed ({e1}), trying cleanup...")
            text_clean = text.replace("```json", "").replace("```", "").strip()
            try:
                data = json.loads(text_clean)
            except Exception as e2:
                print(f"Segmenter: Cleanup parse also failed ({e2}). Raw response (first 500 chars): {text[:500]}")
                data = {}
        
        unit_defs = data.get("units", [])
        if not unit_defs:
            return [_fallback_unit(state.source_notes)]
        
        # Extract text segments using start_marker / end_marker
        full_text = state.source_notes
        units = []
        
        for u in unit_defs:
            start_marker = u.get("start_marker", "").strip()
            end_marker = u.get("end_marker", "").strip()
            
            if not start_marker:
                continue
            
            # Find start position
            start_idx = full_text.find(start_marker)
            if start_idx == -1:
                start_idx = full_text.find(start_marker[-10:])
            
            if start_idx == -1:
                continue
            
            # Find end position
            if end_marker:
                end_idx = full_text.find(end_marker, start_idx)
                if end_idx != -1:
                    end_idx += len(end_marker)
                else:
                    end_idx = full_text.find(end_marker[-10:], start_idx)
                    if end_idx != -1:
                        end_idx += len(end_marker[-10:])
            
            if not end_marker or end_idx == -1:
                end_idx = len(full_text)
            
            segment = full_text[start_idx:end_idx].strip()
            if segment:
                units.append({
                    "title": u.get("title", f"教學單元 {len(units)+1}"),
                    "keypoints": u.get("keypoints", u.get("topic", "")),
                    "type": u.get("type", ""),
                    "content": segment,
                })
        
        return units if units else [_fallback_unit(state.source_notes)]
        
    except Exception as e:
        print(f"Segmenter Error: {e}")
        return [_fallback_unit(state.source_notes)]


def _fallback_unit(text: str) -> Dict:
    """Create a single fallback unit wrapping the entire text."""
    return {"title": "完整筆記", "keypoints": "", "type": "full", "content": text}


def expand_unit(state: AgentState, unit_content: str, previous_text: str) -> str:
    """Phase 2: Expand a single teaching unit into verbatim manuscript."""
    
    if state.project_type == "transcript":
        system_prompt = EXPANDER_TRANSCRIPT_PROMPT
    else:
        system_prompt = EXPANDER_SERMON_NOTE_PROMPT
    
    user_prompt = f"""請將以下教學單元擴展為逐字稿。

=== 全局上下文 ===
系列：{state.sermon_series_title}
主題：{state.sermon_series_description}

=== 前文（保持銜接） ===
{previous_text[-2000:] if previous_text else "這是講稿的開頭。"}

=== 當前需擴展的教學單元 ===
{unit_content}

請充分擴展此單元，詳細解釋每一個要點。不要引入新的神學觀點。避免直接呈現筆記原文。
"""
    
    return llm_client.generate(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.7,
    )
