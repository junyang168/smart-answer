import os
import argparse
from pathlib import Path
from typing import List
from PIL import Image
from google import genai
from google.genai import types

print(f"google-genai version: {genai.__version__}")

# Import config to credentials
# Assumes this script is run from the backend directory or PYTHONPATH includes it
try:
    from api.config import GEMINI_API_KEY
except ImportError:
    # Fallback if run directly and not able to find the module
    import sys
    sys.path.append(str(Path(__file__).parent))
    from api.config import GEMINI_API_KEY

def list_image_files(directory: Path) -> List[Path]:
    """
    List all image files in the directory, sorted alphabetically.
    Supports .jpg, .jpeg, .png.
    """
    extensions = {'.jpg', '.jpeg', '.png'}
    files = [
        f for f in directory.iterdir()
        if f.is_file() and f.suffix.lower() in extensions
    ]
    return sorted(files, key=lambda p: p.name)

def process_images_to_markdown(image_paths: List[Path], output_file: Path):
    """
    Process a list of images using Gemini and append transcripts to an output file.
    """
    
    # Initialize client. explicit api_key or ADC will be used.
    # Consider setting 'vertexai=True' explicitly if the model is only on Vertex, 
    # but strictly speaking strict 'google-genai' usage usually infers from environment.
    # We will initialize with whatever we have.
    client = genai.Client(
        vertexai=True,
        project="gen-lang-client-0011233318", # Replace with your actual GCP Project ID
        location="global"     # Gemini 3 is strictly locked to this region
    )

    model_id = "gemini-3-pro-preview"
    
    print(f"Starting processing of {len(image_paths)} images using {model_id}...")

    # Open the output file in write mode to clear it first, then append
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("# Lecture Notes Reconstruction\n\n")

    for i, image_path in enumerate(image_paths):
        print(f"[{i+1}/{len(image_paths)}] Processing {image_path.name}...")
        
        try:
            # Prepare image for the API
            # Ideally we read binary or use a helper if the client supports path directly
            # The google-genai client typically supports PIL images or bytes
            # Determine mime_type
            ext = image_path.suffix.lower()
            mime_type = "image/png" if ext == ".png" else "image/jpeg"
            
            with open(image_path, "rb") as img_f:
                image_data = img_f.read()
            
            prompt = """
            將此手寫/掃描的講義頁面轉錄為 Markdown。
            請務必保留邏輯層級、希臘文註釋以及任何經文對照表的結構化大綱，完全按照原樣呈現。
            直接輸出 Markdown 內容即可，不要包含任何開場白（如「這裡是根據您提供的...」）或結尾語。
            """

            response = client.models.generate_content(
                model=model_id,
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(text=prompt),
                            types.Part.from_bytes(data=image_data, mime_type=mime_type)
                        ]
                    )
                ]
            )
            
            markdown_content = response.text
            
            # Append to file
            with open(output_file, 'a', encoding='utf-8') as f:
                f.write(f"\n\n<!-- Page: {image_path.name} -->\n\n")
                f.write(markdown_content)
                f.write("\n\n---\n")
                
            print(f"Finished {image_path.name}")

        except Exception as e:
            print(f"Error processing {image_path.name}: {e}")

    print(f"\nProcessing complete. Output saved to {output_file}")

def main():
    parser = argparse.ArgumentParser(description="Reconstruct lecture notes from scanned images.")
    parser.add_argument("--image_dir", type=str, default="/Volumes/Jun SSD/data/scanned_mat/notes_main", help="Directory containing scanned note images")
    parser.add_argument("--output", type=str, default="lecture_notes_1.md", help="Output Markdown file name")
    
    args = parser.parse_args()
    
    image_dir = Path(args.image_dir)
    if not image_dir.exists() or not image_dir.is_dir():
        print(f"Error: Directory '{image_dir}' does not exist.")
        return

    image_files = list_image_files(image_dir)
    if not image_files:
        print(f"No image files found in '{image_dir}'.")
        return

    output_path = Path(args.output)
    process_images_to_markdown(image_files, output_path)

if __name__ == "__main__":
    main()
