from typing import List
from fastapi import FastAPI, Depends
import os
from dotenv import load_dotenv
env_file = os.getenv("ENV_FILE")
print(f'env_file: {env_file}')
if env_file:
    load_dotenv(env_file)
else:
    load_dotenv()  # Fallback to default .env file in the current directory


from semantic_search_service import semantic_service, SemanticSearchService
from content_store import HybridScore


app = FastAPI()

@app.get("/semantic_search/{q}", response_model=List[HybridScore] )
def search(q:str ) -> List[HybridScore]:
    return semantic_service.search(q)


if __name__ == '__main__':
#    res = search("基督徒能不能吃祭過偶像的食物？")
    pass
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9009)

