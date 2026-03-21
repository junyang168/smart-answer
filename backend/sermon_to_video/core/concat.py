import os
from pathlib import Path
from moviepy import VideoFileClip, concatenate_videoclips


def concatenate_and_cleanup(storyboard: list, output_file: Path, work_dir: Path):
    """
    Concatenates all final scene clips into one output file and cleans up temporary files.
    """
    # 1. Gather scene clips
    clips = []
    scene_files = []
    
    for item in sorted(storyboard, key=lambda x: x.get("scene_id", 0)):
        scene_mp4 = item.get("final_scene_filepath")
        if scene_mp4 and os.path.exists(scene_mp4):
            clips.append(VideoFileClip(scene_mp4))
            scene_files.append(scene_mp4)
            
    if not clips:
        print("No scenes generated to concatenate.")
        return
        
    print(f"Concatenating {len(clips)} scenes with hard cuts (audio-safe)...")
    final_video = concatenate_videoclips(clips, method="compose")
    
    # Apply the global audio track to the entirely concatenated video
    full_audio_path = work_dir / "full_audio.mp3"
    if full_audio_path.exists():
        from moviepy import AudioFileClip
        full_audio_clip = AudioFileClip(str(full_audio_path))
        
        # Check for BGM manually placed in the project directory
        bgm_path = work_dir / "bgm.mp3"
        if not bgm_path.exists():
            bgm_path = work_dir / "bgm.wav"
            
        if bgm_path.exists():
            from moviepy import CompositeAudioClip
            from moviepy.audio.fx import MultiplyVolume, AudioLoop
            print(f"🎵 Found BGM at {bgm_path.name}, mixing with voiceover...")
            
            bgm_clip = AudioFileClip(str(bgm_path))
            bgm_clip = bgm_clip.with_effects([
                AudioLoop(duration=full_audio_clip.duration),
                MultiplyVolume(0.12)  # 12% max master volume
            ])
            
            final_audio = CompositeAudioClip([full_audio_clip, bgm_clip])
            final_video = final_video.with_audio(final_audio)
        else:
            final_video = final_video.with_audio(full_audio_clip)
        
    # Write final
    output_path = str(output_file)
    print(f"MoviePy - Writing video {output_path}")
    final_video.write_videofile(
        output_path,
        fps=24,
        codec="libx264",
        audio_codec="aac",
        ffmpeg_params=["-pix_fmt", "yuv420p"]
    )
    
    # Close clips
    final_video.close()
    if 'full_audio_clip' in locals():
        full_audio_clip.close()
    for c in clips:
        c.close()
        
    print("Concatenation complete. Temporary files are preserved in the working directory.")
    # Cleanup disabled as per user request to preserve all data files
