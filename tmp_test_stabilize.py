import sys
import os
from pathlib import Path

# Add project root to sys path to simulate running from backend
sys.path.append("/Users/junyang/app/smart-answer")

from backend.api.sermon_converter_service import stabilize_sermon_structure

def test_stabilize():
    project_id = "_12章_-_安息日的爭議，褻瀆聖靈的罪"
    print(f"Running stabilization for project: {project_id}")
    
    try:
        # Check if the environment requires dotenv loading
        from dotenv import load_dotenv
        load_dotenv("/Users/junyang/app/smart-answer/.env")
        
        result = stabilize_sermon_structure(project_id)
        
        print(f"Success! Generated {len(result)} characters of stabilized text.")
        
        # Write to tmp buffer to inspect
        out_path = "/tmp/test_stabilized_output.md"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(result)
            
        print(f"Wrote stabilized markdown output to: {out_path}")
        
    except Exception as e:
        print(f"Error during stabilization test: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_stabilize()
