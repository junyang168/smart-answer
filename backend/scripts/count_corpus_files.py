import sys
import os

# Add project root to path (smart-answer/)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

try:
    from backend.api.config import GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION, RAG_CORPUS_ID
    import vertexai
    from vertexai.preview import rag
    
    print(f"Initializing Vertex AI...")
    vertexai.init(project=GOOGLE_CLOUD_PROJECT, location=GOOGLE_CLOUD_LOCATION)
    
    if not RAG_CORPUS_ID:
        print("Error: RAG_CORPUS_ID is not set in .env")
        sys.exit(1)
        
    corpus_name = f"projects/{GOOGLE_CLOUD_PROJECT}/locations/{GOOGLE_CLOUD_LOCATION}/ragCorpora/{RAG_CORPUS_ID}"
    print(f"Checking corpus: {corpus_name}")
    
    # Try to list files
    try:
        files = rag.list_files(corpus_name=corpus_name)
        count = 0
        for f in files:
            count += 1
            # print(f"File: {f.display_name}") # Optional: list filenames
            
        print(f"Total files in corpus: {count}")
        
    except Exception as e:
        print(f"Error listing files: {e}")

except Exception as e:
    print(f"Script Error: {e}")
    import traceback
    traceback.print_exc()
