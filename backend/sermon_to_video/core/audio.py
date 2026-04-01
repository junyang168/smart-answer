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

# --- SSML PROMPTS ---

PROMPT_SSML_SHORT_SERMON = """
# Role: 资深音频工程师与 Azure TTS 专家
你现在负责为一个严肃的查经/神学小品（Short Sermon）视频制作 Azure TTS 的 SSML。

# Audio Profile (声音人设)
* **情绪基调**：深沉、稳重、充满长者智慧、带有沧桑感和安抚人心的力量。
* **发音人**：固定使用 `<voice name="zh-CN-YunzeNeural">`。

# Formatting Rules (严格约束)
1. **全局语速与音调**：`<prosody rate="-10%" pitch="-2%">` 包裹全文。
2. **书签注入**：每个 scene 前插入 `<mark name="scene_N" />`。
3. **微操停顿**：
   * 分镜头之间：`<break time="1s"/>` 到 `1.5s`。
   * 句号/问号：`800ms` - `1s`。
   * 逗号/转折：`400ms` - `600ms`。
"""

PROMPT_SSML_EXEGESIS = """
# **角色：资深音频工程师与 Azure TTS 专家**

你负责为一个严肃的查经 / 神学讲解视频生成 Azure TTS 的 SSML（语音合成标记语言）。

---

# **任务**

我将提供给你一个包含多个分镜（scene）的 JSON 数组。

每个元素包含：
- scene_id
- voiceover_text（可能包含内嵌 cue 标记，如：[s10_1]）

你的任务是：
1. 按 scene_id 顺序，将所有 voiceover_text 串联起来
2. 在每个 scene 开始前插入 scene 起始 <mark> 标签
3. 将文本中的内嵌 cue 标记（如 [s10_1]）转换为对应位置的 <mark> 标签
4. 使用 <break> 标签建立清晰的讲解节奏
5. 输出一个完整的、可直接提交给 Azure TTS API 的 SSML 字符串

⚠️ 不要改写、总结、润色或删减原文
⚠️ 只能做断句、停顿、强调和书签插入

---

# **声音设置（固定）**

必须严格使用：
<voice name="zh-CN-YunzeNeural">
  <prosody rate="0%" pitch="0%">

要求：
- rate 固定为 0%
- pitch 固定为 0%
- 不允许整体放慢语速
- 节奏必须通过断句和停顿实现

---

# **核心原则（最重要）**

👉 声音必须像老师在一步一步解释逻辑
👉 必须让“逻辑结构被听出来”

---

# **节奏系统（核心规则）**

## **微停顿（Micro Pause）**
<break time="100ms"/> 到 <break time="150ms"/>
用于短语之间、自然呼吸

## **逻辑停顿（Logical Pause）**
<break time="200ms"/> 到 <break time="300ms"/>
用于从句之间、解释推进

## **结构停顿（Structural Pause）**
<break time="500ms"/> 到 <break time="700ms"/>

每个 scene 结束后，必须插入：

<break time="900ms"/> 到 <break time="1200ms"/>

用于：
- 场景切换
- 给 Python 后端留出更清晰的时间边界
- 让观众在听感上感受到段落切换

---

# 讲解节奏规则（Teaching Cadence）

你必须让“老师式讲解”的节奏清楚可听。

---

## 提问句
提问前后都要留出空间，让听众有思考时间。

例如：
- “那问题就来了……”
- “这到底是什么意思？”

---

## 定义句
定义句必须切分清楚，不能一口气读完。

特别是：
- “不是……而是……”
- “不是起点，而是结果”

这种句型必须读出层次。

---

## 对比句
对比双方都要有清楚的断句。

例如：
- 不是一种感觉
- 而是一个结果

---

## 结论句
结论句后面要有更明显的停顿，让意思“落地”。

---

# 强调规则（Emphasis）

只在少量关键概念上使用：

<emphasis level="moderate">关键词</emphasis>

适用于：
- 神学关键词
- 关键对比词
- 重要转折词

例如：
- 安息
- 聪明通达
- 轻省

⚠️ 禁止滥用 emphasis  
⚠️ 一段里不要连续强调太多词

---

# 书签标签规则（极其重要）

你必须插入两类 `<mark>` 标签，它们用途不同：

1. **scene 起始标签**
   - 用于 Python 后端捕捉每个 scene 的起始时间戳
   - 格式必须严格为：
     <mark name="scene_1" />

2. **cue 标签**
   - 用于 Python 后端捕捉 scene 内部语义触发点（overlay timing）
   - cue 标签来自输入文本中的内嵌 cue 标记
   - 例如：
     [s10_1]
   - 必须转换为：
     <mark name="s10_1" />

---

## Scene 起始标签规则（必须严格遵守）

1. 每个 scene 必须且只能有一个 scene 起始标签
2. scene 起始标签必须放在该 scene 的第一句语音之前
3. scene_id 必须严格对应，例如：
   - scene_1
   - scene_2
   - scene_3
4. 不允许漏掉任何一个 scene
5. 不允许把多个 scene 放在同一个 scene 起始标签下
6. 不允许把 scene 起始标签插在句子中间
7. 不允许把 scene 起始标签放在 scene 结束后

---

## Cue 标签规则（必须严格遵守）

1. cue 标签只能来自输入文本中的内嵌 cue 标记
2. 输入中若出现：
   [s10_1]
   则必须转换为：
   <mark name="s10_1" />
3. cue 标签本身不能被朗读
4. cue 标签必须插在对应 cue 标记所在位置
5. 不允许省略 cue 标签
6. 不允许改写 cue id
7. 不允许把 cue 标签移动到别的位置

---

## 带 cue 的文本处理规则（非常重要）

输入中的 `voiceover_text` 可能包含类似：

那接下来这句话就更关键了：“我就使你们得安息。”[s10_c1]安息不是简单的休息，[s0_c2]而是……[s10_c3]在……

处理规则：

1. 方括号中的 cue 标记本身不能出现在最终朗读文本中
2. 必须将它们转换为对应位置的 `<mark />`
3. cue 后面的文字仍然正常朗读
4. 不允许删除 cue 后面的文字
5. 不允许把 cue 标记原样输出

---

## 每个 scene 的标准结构

每个 scene 必须严格符合下面的结构：

<mark name="scene_X" />
[该 scene 的 voiceover_text 转换后的朗读内容，包含内部 break、必要 emphasis、以及可能出现的 cue mark]
<break time="900ms"/>

---

## 一致性检查（必须满足）

最终生成的 SSML 中：

### Scene 层面
- scene 起始标签的数量必须等于输入 scene 的数量
- scene 起始标签的编号必须与输入 scene_id 一一对应
- 每个 scene 都必须有自己的起始标记

### Cue 层面
- 输入文本中所有内嵌 cue 标记都必须被转换成对应的 `<mark>`
- cue 的 name 必须与输入中的 cue id 完全一致
- cue 标签的位置必须与原文中的 cue 标记位置一致

如果做不到，输出就是无效的。

---

# 禁止事项

- 不要整体放慢语速
- 不要加情绪化表演
- 不要读成“讲道煽情风格”
- 不要机械按标点停顿
- 不要改写文本
- 不要省略 scene
- 不要漏掉 `<mark>`

---

# 输出格式

只输出纯 SSML XML 字符串：

<speak>
  <voice name="zh-CN-YunzeNeural">
    <prosody rate="0%" pitch="0%">
      ...
    </prosody>
  </voice>
</speak>
"""

def generate_ssml(storyboard: List[Dict[str, Any]], mode: str = "Short Sermon") -> str:
    """
    Calls Gemini 3 to create the proper SSML with expert micro-pauses and narrative constraints.
    """
    input_data = []
    for item in storyboard:
        input_data.append({
            "scene_id": item.get("scene_id"),
            "voiceover_text": item.get("voiceover_text", "")
        })
        
    base_prompt = PROMPT_SSML_EXEGESIS if "exegesis" in mode.lower() else PROMPT_SSML_SHORT_SERMON
    
    prompt = base_prompt + f"""
# Task Details
我将提供一个分镜头 JSON。请将所有的 `voiceover_text` 串联生成一个完整的 SSML。
不要输出 Markdown 代码块，直接返回 XML。

# Input Data
{json.dumps(input_data, ensure_ascii=False, indent=2)}
"""
    
    print(f"🤖 Calling AI Model for Expert {mode} SSML Generation...")
    
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
        
        response_text = response_text.strip()
        
        # Ensure <speak> tag has required Azure TTS attributes
        if response_text.startswith("<speak>"):
            response_text = response_text.replace(
                "<speak>",
                '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="zh-CN">',
                1
            )
            
        return response_text
    except Exception as e:
        print(f"Error calling AI for SSML: {e}")
        return fallback_generate_ssml(storyboard)

def process_audio_for_scenes(storyboard_data: Any, work_dir: Path, project_dir: Path | None = None) -> Any:
    """
    Synthesizes the entire storyboard as a single SSML file to preserve prosody.
    Handles both dict (new format) and list (legacy) storyboard data.
    """
    # Extract metadata and scenes
    if isinstance(storyboard_data, dict):
        metadata = storyboard_data.get("metadata", {})
        scenes = storyboard_data.get("scenes", [])
        mode = metadata.get("mode", "Short Sermon")
    else:
        scenes = storyboard_data
        mode = "Short Sermon"

    ssml_filepath = work_dir / "full_sermon.ssml"
    audio_filepath = work_dir / "full_audio.mp3"
    
    # Look for a custom user-provided SSML first
    custom_ssml = (project_dir or work_dir) / f"{(project_dir or work_dir).name}.ssml"
    if custom_ssml.exists():
        print(f"🎵 [AzureTTS] Found Custom SSML: {custom_ssml}! Using this instead of auto-generation.")
        with open(custom_ssml, "r", encoding="utf-8") as f:
            ssml_string = f.read()
    else:
        print(f"🎵 [AzureTTS] Generating built-in {mode} SSML from storyboard...")
        ssml_string = generate_ssml(scenes, mode=mode)
        
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
        # Update scene durations — the only field that stays in storyboard.json
        total_audio_file = MP3(str(audio_filepath))
        total_duration = total_audio_file.info.length
        
        scene_offsets = {}
        for i, item in enumerate(scenes):
            scene_id = str(item.get("scene_id"))
            mark_key = f"scene_{scene_id}"
            
            start_sec = bookmarks.get(mark_key, 0.0)
            
            if i + 1 < len(scenes):
                next_scene_id = str(scenes[i+1].get("scene_id"))
                next_mark_key = f"scene_{next_scene_id}"
                next_start_sec = bookmarks.get(next_mark_key, total_duration)
                duration = next_start_sec - start_sec
            else:
                duration = total_duration - start_sec
                
            item["duration_sec"] = max(duration, 0.1)
            scene_offsets[scene_id] = start_sec
        
        # Return storyboard (only duration_sec updated) and cue data separately
        cue_data = {
            "cue_points": bookmarks,
            "scene_offsets": scene_offsets
        }
        return storyboard_data, cue_data
    elif result.reason == speechsdk.ResultReason.Canceled:
        raise RuntimeError(f"Speech synthesis canceled. Details: {result.cancellation_details.error_details}")
    else:
        raise RuntimeError(f"Speech synthesis failed: {result.reason}")
