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

    # Extract ground-truth original script from storyboard
    original_script = " ".join([item.get("voiceover_text", "") for item in storyboard if item.get("voiceover_text")])

    sys_prompt = (
        "你是一位專業的 YouTube 影片字幕編輯專家。\n"
        "請將以下收到的 JSON 字典中的語音辨識字幕（包含錯字），根據我提供的【原始正確講稿】進行全面修正與「繁體中文」轉換。\n"
        "你的目標是：讓字幕 100% 符合原始講稿的用詞，絕不能有辨識錯誤或同音字錯誤（例如：將『實實』修正回『時時』）。\n\n"
        "【排版規則】\n"
        "1. 每行字幕不可超過 15 個中文字。\n"
        "2. 如果句子過長，請在同一個區塊內自然換行（使用 \\n）。\n"
        "3. 你必須返回一個合法的 JSON 格式，Key 保持與輸入的 JSON 完全一樣，Value 是精準備份原始講稿的處理後文字。\n\n"
        f"【原始正確講稿】\n{original_script}"
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
        
        # Time parsing helpers
        def parse_srt_time(time_str):
            h, m, s_ms = time_str.split(':')
            s, ms = s_ms.split(',')
            return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(ms)

        def format_srt_time(ms_time):
            ms_time = int(ms_time)
            h = ms_time // 3600000
            m = (ms_time % 3600000) // 60000
            s = (ms_time % 60000) // 1000
            ms = ms_time % 1000
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

        # Step 4: Reconstruct SRT with mathematical multi-line chunking
        final_srt_lines = []
        current_idx = 1
        
        for item in srt_data:
            idx = item["id"]
            if idx in refined_dict:
                text_content = refined_dict[idx].strip()
                lines = text_content.split('\n')
                
                # We want exactly 1 line per SRT chunk for modern fast-paced reading
                chunks = []
                for line in lines:
                    line = line.strip()
                    if line:
                        chunks.append(line)
                
                time_str = item["time"]
                start_str, end_str = time_str.split(' --> ')
                start_ms = parse_srt_time(start_str.strip())
                end_ms = parse_srt_time(end_str.strip())
                
                chunk_duration = (end_ms - start_ms) / len(chunks)
                
                for i, chunk in enumerate(chunks):
                    c_start_ms = start_ms + i * chunk_duration
                    c_end_ms = start_ms + (i + 1) * chunk_duration
                    c_time_str = f"{format_srt_time(c_start_ms)} --> {format_srt_time(c_end_ms)}"
                    
                    final_srt_lines.append(str(current_idx))
                    final_srt_lines.append(c_time_str)
                    final_srt_lines.append(chunk)
                    final_srt_lines.append("")
                    current_idx += 1
            else: # Fallback to original Whisper text if GPT-5.4 didn't process it
                final_srt_lines.append(str(current_idx))
                final_srt_lines.append(item["time"])
                final_srt_lines.append(item["text"])
                final_srt_lines.append("")
                current_idx += 1
            
        final_srt_str = "\n".join(final_srt_lines).strip()
        
        output_path.write_text(final_srt_str, encoding="utf-8")
        print(f"✅ Closed Captions (Structured Traditional Chinese via OpenAI) saved → {output_path}")
        return output_path
        
    except Exception as e:
        print(f"⚠️ Error during LLM restructuring: {e}")
        output_path.write_text(raw_srt, encoding="utf-8")
        print(f"✅ Fallback to raw Whisper SRT saved → {output_path}")
        return output_path
