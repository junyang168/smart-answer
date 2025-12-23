import sys
import os

# Add project root to path (smart-answer/)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

try:
    from backend.api.config import GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION
    import vertexai
    from vertexai.preview import rag
    
    print(f"Testing RAG connection...")
    print(f"Project: {GOOGLE_CLOUD_PROJECT}")
    print(f"Location: {GOOGLE_CLOUD_LOCATION}")
    
    vertexai.init(project=GOOGLE_CLOUD_PROJECT, location=GOOGLE_CLOUD_LOCATION)
    
    print("Listing existing corpora...")
    corpora = rag.list_corpora()
    for c in corpora:
        print(f"Found corpus: {c.name}")
        
    print("Attempting to create a test corpus...")
    # generate a unique name to avoid conflicts if needed, but RAG engine handles this.
    test_corpus_name = "test_corpus_debug"
    try:
        corpus = rag.create_corpus(display_name=test_corpus_name)
        print(f"Successfully created corpus: {corpus.name}")
        # Clean up
        # print("Deleting test corpus...")
        # rag.delete_corpus(name=corpus.name)
    except Exception as e:
        print(f"FAILED to create corpus: {e}")
        
except Exception as e:
    print(f"Test Script Crashed: {e}")
    import traceback
    traceback.print_exc()
