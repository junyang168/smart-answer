import sys
import os

# Add project root to path (smart-answer/)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

try:
    from backend.api.config import GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION
    import vertexai
    from vertexai.preview import rag
    
    print(f"Listing corpora in project {GOOGLE_CLOUD_PROJECT}, location {GOOGLE_CLOUD_LOCATION}...")
    
    vertexai.init(project=GOOGLE_CLOUD_PROJECT, location=GOOGLE_CLOUD_LOCATION)
    
    corpora = rag.list_corpora()
    
    count = 0
    for c in corpora:
        count += 1
        # Corpus name format: projects/{project}/locations/{location}/ragCorpora/{rag_corpus_id}
        corpus_id = c.name.split('/')[-1]
        print(f"Display Name: {c.display_name}")
        print(f"Expected RAG_CORPUS_ID: {corpus_id}")
        print(f"Full Name: {c.name}")
        print("-" * 20)
        
    if count == 0:
        print("No corpora found.")
    else:
        print(f"Found {count} corpora.")
            
except Exception as e:
    print(f"Error listing corpora: {e}")
    import traceback
    traceback.print_exc()
