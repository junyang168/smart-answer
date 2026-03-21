"""
subtitle.py - Generate SRT Closed Caption files in Traditional Chinese
Uses opencc to convert Simplified Chinese voiceover text, and Azure TTS scene
bookmarks for timing alignment.
"""
from pathlib import Path


def seconds_to_srt_time(t: float) -> str:
    """Convert float seconds to SRT timestamp format: HH:MM:SS,mmm"""
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def generate_srt(storyboard: list, output_path: Path) -> Path:
    """
    Generate a .srt subtitle file from scene timestamps and voiceover text.
    Converts Simplified Chinese → Traditional Chinese via opencc.
    
    Args:
        storyboard: List of scene dicts, each must have:
                    - duration_sec (float)
                    - voiceover_text (str, Simplified Chinese)
                    - scene_id (int)
        output_path: Where to write the .srt file (e.g., work_dir / "captions.srt")
    Returns:
        Path to the generated .srt file
    """
    try:
        import opencc
        converter = opencc.OpenCC('s2t')  # Simplified → Traditional
    except ImportError:
        print("⚠️  opencc not installed. Skipping CC generation. Run: pip install opencc-python-reimplemented")
        return None

    entries = []
    accumulated_time = 0.0
    
    for item in sorted(storyboard, key=lambda x: x.get("scene_id", 0)):
        duration = item.get("duration_sec", 0.0)
        simplified_text = item.get("voiceover_text", "").strip()
        
        if not simplified_text or duration <= 0:
            accumulated_time += duration
            continue
        
        # Convert to Traditional Chinese
        traditional_text = converter.convert(simplified_text)
        
        start_t = accumulated_time
        end_t = accumulated_time + duration
        
        entries.append({
            "start": start_t,
            "end": end_t,
            "text": traditional_text,
        })
        
        accumulated_time += duration

    # Write SRT format
    srt_lines = []
    for i, entry in enumerate(entries, start=1):
        srt_lines.append(str(i))
        srt_lines.append(f"{seconds_to_srt_time(entry['start'])} --> {seconds_to_srt_time(entry['end'])}")
        srt_lines.append(entry['text'])
        srt_lines.append("")  # blank line between entries

    srt_content = "\n".join(srt_lines)
    output_path.write_text(srt_content, encoding="utf-8-sig")  # utf-8-sig for YouTube CC compatibility
    print(f"✅ Closed Captions (Traditional Chinese) saved → {output_path}")
    return output_path
