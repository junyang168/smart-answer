from typing import List
from fastapi import FastAPI, Depends
from semantic_search_service import semantic_service, SemanticSearchService
from content_store import HybridScore

app = FastAPI()

@app.get("/semantic_search/{q}", response_model=List[HybridScore] )
def search(q:str ) -> List[HybridScore]:
    return semantic_service.search(q)


if __name__ == '__main__':
#    res = search("基督徒能不能吃祭過偶像的食物？")
#    pass
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

