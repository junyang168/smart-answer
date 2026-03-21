import os
import json
import xml.sax.saxutils as saxutils
from pathlib import Path
from typing import List, Dict, Any

import azure.cognitiveservices.speech as speechsdk
from mutagen.mp3 import MP3

from backend.api.config import AZURE_SPEECH_KEY, AZURE_SPEECH_REGION, GENERATION_MODEL
from backend.api.gemini_client import gemini_client

def fallback_generate_ssml(storyboard: List[Dict[str, Any]], voice_name: str = "zh-CN-YunzeNeural") -> str:
    """
    Generates a single monolithic SSML string combining all scenes, separated by <mark> bookmarks.
    """
    ssml_parts = [
        f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="zh-CN">',
        f'  <voice name="{voice_name}">',
        f'      <prosody rate="-10%" pitch="-2%">'
    ]
    
    for item in storyboard:
        scene_id = item.get("scene_id")
        text = str(item.get("voiceover_text", "")).strip()
        if not text:
            text = '<break time="100ms"/>'
        else:
            text = saxutils.escape(text)
            
        ssml_parts.append(f'          <mark name="scene_{scene_id}" />')
        ssml_parts.append(f'          {text}')
        ssml_parts.append(f'          <break time="1s"/>')
        
    ssml_parts.append('      </prosody>')
    ssml_parts.append('  </voice>')
    ssml_parts.append('</speak>')
    
    return "\n".join(ssml_parts)

def generate_ssml(storyboard: List[Dict[str, Any]]) -> str:
    """
    Calls Gemini 3 to create the proper SSML with expert micro-pauses and narrative constraints.
    """
    input_data = []
    for item in storyboard:
        input_data.append({
            "scene_id": item.get("scene_id"),
            "voiceover_text": item.get("voiceover_text", "")
        })
        
    prompt = """
# Role: 资深音频工程师与 Azure TTS 专家
你现在负责为一个严肃的查经/神学宣讲视频制作 Azure TTS 的 SSML（语音合成标记语言）。

# Task
我将提供给你一个包含多个分镜头（scene）的 JSON 脚本。你的任务是将所有的 `voiceover_text` 串联起来，生成一个**完整的、全局的 SSML 字符串**。

# Audio Profile (声音人设)
* **目标受众**：渴望真理、可能正在经历人生低谷的信徒。
* **情绪基调**：深沉、稳重、充满长者智慧、带有沧桑感和安抚人心的力量。
* **发音人**：必须固定使用 `<voice name="zh-CN-YunzeNeural">`。

# Formatting Rules (严格约束)
请严格按照以下规则生成 XML：

1. **全局语速与音调控制**：
   在 `<voice>` 标签内部，必须用 `<prosody rate="-10%" pitch="-2%">` 包裹全文，以营造缓慢、娓娓道来的讲述感。

2. **书签注入 (Bookmark Injection)**：
   在拼接每一个分镜头之前，**必须**插入对应的 `<mark>` 标签用于 Python 后端捕捉时间戳。
   格式为：`<mark name="scene_1" />`，数字随场景 ID 递增。

3. **微操停顿法 (Micro-Pauses)**：
   不要让 AI 一口气把话说完，必须根据语意手动插入 `<break time="..." />` 标签：
   * **分镜头之间**：必须插入 `<break time="1s"/>` 到 `<break time="1.5s"/>`。
   * **句号/问号/感叹号**：插入 `<break time="800ms"/>` 或 `<break time="1s"/>`。
   * **逗号或语意转折**：插入 `<break time="400ms"/>` 到 `<break time="600ms"/>`。
   * **强调或反问前（如“他疯了吗？”）**：插入 `<break time="800ms"/>`，留出思考空间。

# Output Format (输出格式)
不要输出任何 Markdown 代码块前缀（如 ```xml），不要解释，直接输出纯 XML 字符串格式的 SSML，确保它是完全合法的 XML，可以直接发送给 Azure API。

# Input Data Example
[
  {"scene_id": 1, "voiceover_text": "神为什么不听我的祷告？"},
  {"scene_id": 2, "voiceover_text": "其实，这种绝望感既真实又古老。"}
]

# Real Input Data
"""
    prompt += json.dumps(input_data, ensure_ascii=False, indent=2)
    
    print("🤖 Calling AI Model for Expert SSML Generation...")
    
    try:
        response_text = gemini_client.generate(prompt=prompt, model="gemini-3.1-pro-preview")
        
        # Clean up potential markdown formatting from LLM
        response_text = response_text.strip()
        if response_text.startswith("```xml"):
            response_text = response_text[6:]
        elif response_text.startswith("```"):
            response_text = response_text[3:]
            
        if response_text.endswith("```"):
            response_text = response_text[:-3]
            
        return response_text.strip()
    except Exception as e:
        print(f"Error calling AI for SSML: {e}")
        return fallback_generate_ssml(storyboard)

def process_audio_for_scenes(storyboard: List[Dict[str, Any]], work_dir: Path) -> List[Dict[str, Any]]:
    """
    Synthesizes the entire storyboard as a single SSML file to preserve prosody.
    Uses Azure TTS bookmark events to calculate precise durations for each scene.
    """
    ssml_filepath = work_dir / "full_sermon.ssml"
    audio_filepath = work_dir / "full_audio.mp3"
    
    # Look for a custom user-provided SSML first
    custom_ssml = work_dir / f"{work_dir.name}.ssml"
    if custom_ssml.exists():
        print(f"🎵 [AzureTTS] Found Custom SSML: {custom_ssml}! Using this instead of auto-generation.")
        with open(custom_ssml, "r", encoding="utf-8") as f:
            ssml_string = f.read()
    else:
        print("🎵 [AzureTTS] Generating built-in SSML from storyboard...")
        ssml_string = generate_ssml(storyboard)
        
    with open(ssml_filepath, "w", encoding="utf-8") as f:
        f.write(ssml_string)
        
    if not AZURE_SPEECH_KEY or not AZURE_SPEECH_REGION:
        print("[WARNING] AZURE_SPEECH_KEY or REGION not set! Falling back to 5s mock duration per scene.")
        for i, item in enumerate(storyboard):
            item["duration_sec"] = 5.0
            item["audio_start_sec"] = i * 5.0
            item["full_audio_filepath"] = None
        return storyboard

    speech_config = speechsdk.SpeechConfig(subscription=AZURE_SPEECH_KEY, region=AZURE_SPEECH_REGION)
    speech_config.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Audio16Khz32KBitRateMonoMp3)
    audio_config = speechsdk.audio.AudioOutputConfig(filename=str(audio_filepath))
    
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)
    
    bookmarks = {}
    
    def bookmark_listener(evt):
        scene_name = evt.text
        audio_offset_seconds = evt.audio_offset / 10000000.0
        bookmarks[scene_name] = audio_offset_seconds
        print(f"[AzureTTS] Bookmark '{scene_name}' reached at {audio_offset_seconds:.3f}s")
        
    synthesizer.bookmark_reached.connect(bookmark_listener)
    
    print("Synthesizing full continuous SSML audio...")
    result = synthesizer.speak_ssml_async(ssml_string).get()
    
    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        total_audio_file = MP3(str(audio_filepath))
        total_duration = total_audio_file.info.length
        
        for i, item in enumerate(storyboard):
            scene_id = str(item.get("scene_id"))
            mark_key = f"scene_{scene_id}"
            
            start_sec = bookmarks.get(mark_key, 0.0)
            # Remove redundant assignments
            # item["audio_start_sec"] = start_sec
            # item["full_audio_filepath"] = str(audio_filepath)
            
            if i + 1 < len(storyboard):
                next_scene_id = str(storyboard[i+1].get("scene_id"))
                next_mark_key = f"scene_{next_scene_id}"
                next_start_sec = bookmarks.get(next_mark_key, total_duration)
                duration = next_start_sec - start_sec
            else:
                duration = total_duration - start_sec + 1.0
                
            item["duration_sec"] = max(duration, 0.1)
            
        return storyboard
    elif result.reason == speechsdk.ResultReason.Canceled:
        raise RuntimeError(f"Speech synthesis canceled. Details: {result.cancellation_details.error_details}")
    else:
        raise RuntimeError(f"Speech synthesis failed: {result.reason}")
