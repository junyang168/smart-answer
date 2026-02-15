
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from backend.api.gemini_client import gemini_client
from backend.api.config import GENERATION_MODEL

def test_gemini_generation():
    print(f"Testing generation with model: {GENERATION_MODEL}")
    try:
        response = gemini_client.generate("Hello, say hi back.")
        print(f"Success! Response: {response}")
    except Exception as e:
        print(f"Error generating content: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_gemini_generation()
