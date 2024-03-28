from typing import Union
from typing import List
from fastapi import APIRouter, Request
from pydantic import BaseModel
from fastapi import FastAPI, Depends
from model import get_model, Model, QueryResult
app = FastAPI()


@app.get("/", response_model=List[QueryResult] )
def search(q:str, model:Model=Depends(get_model) ) -> List[QueryResult]:
    return model.search(q)


if __name__ == '__main__':
#    res = search("vPostgres won't start")
#    pass
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

