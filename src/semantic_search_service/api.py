from typing import Union
from typing import List
from fastapi import APIRouter, Request
from pydantic import BaseModel
from fastapi import FastAPI, Depends
from semantic_search_service.semantic_search_service import get_service, SemanticSearchService, QueryResult

app = FastAPI()

@app.get("/", response_model=List[QueryResult] )
def search(q:str, model:SemanticSearchService=Depends(get_service) ) -> List[QueryResult]:
    return model.search(q)


if __name__ == '__main__':
#    res = search("vPostgres won't start")
#    pass
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

