from typing import Union
from typing import List
from fastapi import APIRouter, Request
from pydantic import BaseModel
from fastapi import FastAPI, Depends
from semantic_search_service.vespa_service import get_service, VespaService, QueryResult

app = FastAPI()

@app.get("/", response_model=List[QueryResult] )
def search(q:str, model:VespaService=Depends(get_service) ) -> List[QueryResult]:
    return model.search(q)


if __name__ == '__main__':
#    res = search("vPostgres won't start")
#    pass
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

