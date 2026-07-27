import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

try:
    response = client.chat.completions.create(
        model=os.getenv("OPENAI_GENERATION_MODEL", "gpt-5.6-sol"),
        messages=[{"role": "user", "content": "Hello"}],
        timeout=10
    )
    print("Success")
except Exception as e:
    print(f"Error: {e}")
