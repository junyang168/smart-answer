from typing import List

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from model import Model, get_model


app = FastAPI()

class EmbeddingResponse(BaseModel):
    embeddings: List[float]

class EmbeddingRequest(BaseModel):
    text: str


@app.post("/embed", response_model=EmbeddingResponse)
def embed(request: EmbeddingRequest, model: Model = Depends(get_model)):
    emb = model.embed(request.text)
    return EmbeddingResponse(
        embeddings=emb
    )