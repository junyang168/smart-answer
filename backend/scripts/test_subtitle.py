import sys
import json
from pathlib import Path

# Provide explicit path resolution for the module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.sermon_to_video.core.subtitle import generate_srt

def main():
    work_dir = Path("/opt/homebrew/var/www/church/web/data/sermon_to_video/主恩的滋味")
    storyboard_file = work_dir / "storyboard.json"
    
    # We use a dummy test output so we don't overwrite master_1080p_final.srt
    srt_output = work_dir / "test_gpt_output.srt"
    
    with open(storyboard_file, "r", encoding="utf-8") as f:
        storyboard = json.load(f)

    print("Running generate_srt test...")
    generate_srt(storyboard, srt_output)
    
    # Verify if '時時' is in the generated SRT
    if srt_output.exists():
        with open(srt_output, "r", encoding="utf-8") as f:
            content = f.read()
            if "時時" in content:
                print("✅ Validation PASSED! The Ground Truth text correctly aligned and replaced Whisper's '實實' with '時時'.")
            else:
                print("❌ Validation FAILED! The word '時時' was not found in the output SRT.")
                print(f"Here is the content snippet:\n{content[:500]}...")

if __name__ == "__main__":
    main()
