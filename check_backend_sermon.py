import sys
import os
from pathlib import Path

# Add project root to sys.path
sys.path.append("/Users/junyang/app/smart-answer")

try:
    from backend.api.sermon_converter_service import list_note_images
    
    print("Attempting to list images...")
    images = list_note_images()
    print(f"Found {len(images)} images.")
    
    for img in images[:5]:
        print(f" - {img.filename} (Processed: {img.processed})")
        
    if len(images) > 5:
        print(f" ... and {len(images)-5} more.")
        
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
