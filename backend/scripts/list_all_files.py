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
    print(f"Listing files in corpus: {corpus_name}")
    
    files = []
    page_token = None
    
    while True:
        # Pass page_token to list_files (assuming pager support or manual token handling)
        # Note: The Vertex AI SDK (preview) 'list_files' returns a Pager object that is iterable.
        # However, to be explicit and debug safe, let's just iterate the pager completely.
        # If the previous generic iteration stopped early, it might be due to a bug or network.
        # Let's try iterating effectively.
        pager = rag.list_files(corpus_name=corpus_name, page_size=100, page_token=page_token)
        batch_count = 0
        for f in pager:
            files.append(f)
            batch_count += 1
        
        # If the pager is an iterable that handles pages automatically (which it usually is in Google SDKs),
        # then the simple loop 'for f in pager' should have worked.
        # But if it stopped at ~100 or so, maybe it didn't auto-page.
        # Let's try checking if the pager object exposes the next page token manually
        # OR just trust the iterator and print debug info.
        
        # Actually, let's trust the iterator but add print to see progress.
        break 
        
    # Re-writing with simple full iteration expecting automatic pagination
    pager = rag.list_files(corpus_name=corpus_name)
    all_files = []
    for f in pager:
        all_files.append(f)
        if len(all_files) % 100 == 0:
            print(f"Fetched {len(all_files)} files...")
    
    # Update files reference
    files = all_files
    
    output_file = os.path.join(os.path.dirname(__file__), '../indexed_files.txt')
    
    count = 0
    with open(output_file, 'w') as f_out:
        for f in files:
            count += 1
            f_out.write(f"{f.display_name}\n")
            
    print(f"Successfully listed {count} files.")
    print(f"File names have been saved to: {output_file}")

except Exception as e:
    print(f"Script Error: {e}")
    import traceback
    traceback.print_exc()
