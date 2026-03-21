"""
subtitle.py - Generate natural, organic SRT Closed Captions
Uses OpenAI Whisper on the actual audio file to get phrase-level timestamps, 
and OpenAI GPT-4o to dynamically restructure line breaks and output Traditional Chinese.
"""
from pathlib import Path
from openai import OpenAI
from backend.api.config import OPENAI_API_KEY

def generate_srt(storyboard: list, output_path: Path) -> Path:
    """
    Extract perfectly timed, organically structured traditional Chinese SRT.
    
    Args:
        storyboard: The storyboard (used simply to locate the audio files directory)
        output_path: Where to save the final SRT
    Returns:
        Path to the generated .srt file
    """
    if not OPENAI_API_KEY:
        print("⚠️ OPENAI_API_KEY is missing from .env. Skipping advanced CC generation.")
        return None
        
    client = OpenAI(api_key=OPENAI_API_KEY, timeout=45.0)
    
    # Locate the combined audio file produced in Phase 2
    work_dir = output_path.parent
    audio_file_path = work_dir / "full_audio.mp3"
    
    if not audio_file_path.exists():
        print("⚠️ full_audio.mp3 not found! Cannot generate subtitles.")
        return None
        
    # Step 1: Extract timing via Whisper API
    print("🎙️ Sending audio to OpenAI Whisper for precision timing...")
    with open(audio_file_path, "rb") as audio_file:
        raw_srt = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="srt"
        )
        
    if not raw_srt:
        print("⚠️ Whisper returned empty SRT.")
        return None
        
    # Step 2: Parse raw SRT to isolate text
    import re
    import json
    
    # Simple SRT parser
    blocks = raw_srt.strip().split('\n\n')
    srt_data = []
    text_dict = {}
    
    for block in blocks:
        lines = block.split('\n')
        if len(lines) >= 3:
            idx = lines[0]
            timestamp = lines[1]
            text = " ".join(lines[2:])
            srt_data.append({"id": idx, "time": timestamp, "text": text})
            text_dict[idx] = text
            
    # Step 3: Use OpenAI's GPT-5.4-Mini to format the text only (JSON mode)
    print("🧠 Using OpenAI GPT-5.4-Mini to organically format and translate SRT text...")

    sys_prompt = (
        "你是一位專業的 YouTube 影片字幕編輯專家。\n"
        "請將以下收到的 JSON 字典中的所有文字，進行地道的「繁體中文」轉換，並遵循以下排版規則：\n"
        "1. 每行字幕不可超過 15 個中文字。\n"
        "2. 如果句子過長，請在同一個區塊內自然換行（使用 \\n）。\n"
        "3. 根據上下文修復同音字或辨識錯誤。\n"
        "4. 你必須返回一個合法的 JSON 格式，Key 保持完全一樣，Value 是處理後的文字。"
    )

    try:
        response = client.responses.create(
            model="gpt-5.4-mini",
            input=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": json.dumps(text_dict, ensure_ascii=False)}
            ]
        )
        
        raw_output = response.output[0].content[0].text.strip()
        
        # Strip potential markdown wrapping from GPT-5.4
        if raw_output.startswith("```json"):
            raw_output = raw_output[7:]
        elif raw_output.startswith("```"):
            raw_output = raw_output[3:]
        if raw_output.endswith("```"):
            raw_output = raw_output[:-3]
            
        refined_dict = json.loads(raw_output.strip())
        
        # Step 4: Reconstruct SRT
        final_srt_lines = []
        for item in srt_data:
            idx = item["id"]
            final_srt_lines.append(idx)
            final_srt_lines.append(item["time"])
            
            # Use refined text if available, fallback to original
            final_text = refined_dict.get(idx, item["text"])
            final_srt_lines.append(final_text)
            final_srt_lines.append("") # Blank line separator
            
        final_srt_str = "\n".join(final_srt_lines).strip()
        
        output_path.write_text(final_srt_str, encoding="utf-8")
        print(f"✅ Closed Captions (Structured Traditional Chinese via OpenAI) saved → {output_path}")
        return output_path
        
    except Exception as e:
        print(f"⚠️ Error during LLM restructuring: {e}")
        output_path.write_text(raw_srt, encoding="utf-8")
        print(f"✅ Fallback to raw Whisper SRT saved → {output_path}")
        return output_path
