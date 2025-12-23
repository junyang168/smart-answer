from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional

from ..rag_client import rag_client
from ..config import GOOGLE_DRIVE_FOLDER_ID

router = APIRouter(prefix="/rag", tags=["rag"])

class InitRequest(BaseModel):
    display_name: str = "Smart Answer Corpus"

class ChatRequest(BaseModel):
    query: str

class ChatResponse(BaseModel):
    answer: str
    citations: List[dict]

@router.post("/init")
def initialize_corpus(request: InitRequest):
    """Creates a new RAG Corpus (one-time setup)."""
    try:
        corpus_name = rag_client.create_corpus(display_name=request.display_name)
        # Extract ID for user to save
        corpus_id = corpus_name.split("/")[-1]
        return {"status": "success", "corpus_name": corpus_name, "corpus_id": corpus_id, "message": "Please add RAG_CORPUS_ID to your .env file."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/sync")
def sync_drive():
    """Triggers import of files from the configured Google Drive folder."""
    try:
        rag_client.import_drive_files(folder_id=GOOGLE_DRIVE_FOLDER_ID)
        return {"status": "success", "message": "Import started/completed."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/file_count")
def get_file_count():
    """Returns the number of files in the corpus."""
    try:
        files = rag_client.list_files()
        return {"status": "success", "count": len(files)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    """Retrieves context and generates an answer."""
    try:
        result = rag_client.generate_answer(request.query)
        return ChatResponse(answer=result["answer"], citations=result["citations"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
