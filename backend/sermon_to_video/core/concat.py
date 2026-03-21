import os
import subprocess
from pathlib import Path
from moviepy import VideoFileClip

def concatenate_and_cleanup(storyboard: list, output_file: Path, work_dir: Path):
    """
    Concatenates all final scene clips into one output file using FFmpeg complex filtergraphs.
    Supports both hard cuts (concat) and dissolves (xfade) natively while keeping global A/V sync.
    """
    scene_files = []
    
    for item in sorted(storyboard, key=lambda x: x.get("scene_id", 0)):
        scene_mp4 = item.get("final_scene_filepath")
        if scene_mp4 and os.path.exists(scene_mp4):
            scene_files.append((item, scene_mp4))
            
    if not scene_files:
        print("No scenes generated to concatenate.")
        return
        
    if len(scene_files) == 1:
        import shutil
        shutil.copy(scene_files[0][1], str(output_file))
        return

    print(f"Building FFmpeg complex filtergraph for {len(scene_files)} scenes...")
    
    inputs = []
    for _, mp4_path in scene_files:
        inputs.extend(["-i", str(mp4_path)])
        
    norm_filters = []
    for idx in range(len(scene_files)):
        norm_filters.append(f"[{idx}:v]format=yuv420p,setpts=PTS-STARTPTS,fps=24,settb=1/1000000[norm{idx}]")
        
    filter_graph = ";".join(norm_filters) + ";"
    current_v = "[norm0]"
    accumulated_offset = 0.0
    
    for i in range(1, len(scene_files)):
        prev_item, prev_mp4 = scene_files[i-1]
        next_v = f"[norm{i}]"
        out_v = f"[v{i}]"
        
        # Determine if there is a transition from i-1 to i
        trans_dur = float(prev_item.get("transition_duration", 0.0))
        
        # Read actual rendered duration accurately
        clip = VideoFileClip(str(prev_mp4))
        actual_dur = clip.duration
        clip.close()
            
        if trans_dur > 0:
            accumulated_offset += (actual_dur - trans_dur)
            filter_graph += f"{current_v}{next_v}xfade=transition=fade:duration={trans_dur}:offset={accumulated_offset},fps=24,settb=1/1000000[v{i}];"
        else:
            filter_graph += f"{current_v}{next_v}concat=n=2:v=1:a=0,fps=24,settb=1/1000000[v{i}];"
            accumulated_offset += actual_dur
            
        current_v = out_v
        
    filter_graph = filter_graph.rstrip(";")
    
    # Audio mapping
    full_audio_path = work_dir / "full_audio.mp3"
    bgm_path = work_dir / "bgm.mp3"
    if not bgm_path.exists():
        bgm_path = work_dir / "bgm.wav"
        
    audio_filter = ""
    audio_map = ""
    
    if full_audio_path.exists():
        inputs.extend(["-i", str(full_audio_path)])
        audio_idx = len(scene_files)
        
        if bgm_path.exists():
            inputs.extend(["-i", str(bgm_path)])
            bgm_idx = audio_idx + 1
            audio_filter = f"[{bgm_idx}:a]volume=0.12[bgm];[{audio_idx}:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]"
            audio_map = "[aout]"
        else:
            audio_map = f"{audio_idx}:a"
            
    cmd = [
        "ffmpeg", "-y"
    ] + inputs + [
        "-filter_complex", filter_graph + (";" + audio_filter if audio_filter else ""),
        "-map", current_v
    ]
    
    if audio_map:
        cmd.extend(["-map", audio_map])
        
    cmd.extend([
        "-c:v", "libx264",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-r", "24",
        "-preset", "fast",
        str(output_file)
    ])
    
    print(f"🎬 Executing pure FFmpeg Concat & Xfade...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"🛑 FFmpeg Xfade failed:\n{result.stderr}")
        raise RuntimeError("FFmpeg xfade processing failed.")
    
    print("✅ Concatenation and Xfade natively processed in C!")
