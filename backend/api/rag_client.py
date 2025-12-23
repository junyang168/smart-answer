from __future__ import annotations
import time
from typing import Optional, List, Dict, Any
import vertexai
from vertexai.preview import rag
from google.api_core.exceptions import NotFound

from .config import (
    GOOGLE_CLOUD_PROJECT,
    GOOGLE_CLOUD_LOCATION,
    RAG_CORPUS_ID,
    GENERATION_MODEL
)
from google import genai
from google.genai import types

class RagClient:
    def __init__(self):
        vertexai.init(project=GOOGLE_CLOUD_PROJECT, location=GOOGLE_CLOUD_LOCATION)
        self.corpus_name = None
        
        if RAG_CORPUS_ID:
            self.corpus_name = f"projects/{GOOGLE_CLOUD_PROJECT}/locations/{GOOGLE_CLOUD_LOCATION}/ragCorpora/{RAG_CORPUS_ID}"
            print(f"Initialized RAG Client with existing Corpus: {self.corpus_name}")

    def create_corpus(self, display_name: str = "Smart Answer Corpus") -> str:
        """Creates a new RAG Corpus and returns its name."""
        corpus = rag.create_corpus(display_name=display_name)
        self.corpus_name = corpus.name
        print(f"Created new RAG Corpus: {self.corpus_name}")
        return self.corpus_name

    def _get_drive_service(self):
        """Initializes and returns the Google Drive API service."""
        from googleapiclient.discovery import build
        import google.auth
        
        # Explicitly request Drive metadata scope
        SCOPES = ['https://www.googleapis.com/auth/drive.metadata.readonly']
        credentials, _ = google.auth.default(scopes=SCOPES)
        return build('drive', 'v3', credentials=credentials)

    def _get_all_subfolders(self, folder_id: str) -> List[str]:
        """Recursively finds all subfolder IDs starting from the given folder_id."""
        service = self._get_drive_service()
        folders = [folder_id]
        
        # Queue for BFS
        queue = [folder_id]
        
        print(f"Scanning for subfolders in {folder_id}...")
        while queue:
            current_id = queue.pop(0)
            
            page_token = None
            while True:
                response = service.files().list(
                    q=f"'{current_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
                    spaces='drive',
                    fields='nextPageToken, files(id, name)',
                    pageToken=page_token
                ).execute()
                
                for file in response.get('files', []):
                    # print(f"Found subfolder: {file.get('name')} ({file.get('id')})")
                    folders.append(file.get('id'))
                    queue.append(file.get('id'))
                    
                page_token = response.get('nextPageToken', None)
                if page_token is None:
                    break
                    
        print(f"Found {len(folders)} total folders (including root).")
        return folders

    def import_drive_files(self, folder_id: str):
        """Recursively imports files from a Google Drive folder and its subfolders into the Corpus."""
        if not self.corpus_name:
            raise ValueError("RAG Corpus not initialized. Call create_corpus first or set RAG_CORPUS_ID.")

        # Find all subfolders recursively
        all_folder_ids = self._get_all_subfolders(folder_id)
        
        # Prepare paths for all folders
        paths = [f"https://drive.google.com/drive/folders/{fid}" for fid in all_folder_ids]
        
        print(f"Starting import for {len(paths)} folders...")
        
        # Batch import might be limited, but let's try passing all paths first.
        # If too many paths, we might need to chunk the folders.
        # RAG Engine limit is usually high for paths, but let's be safe.
        chunk_size_folders = 50 
        total_imported = 0
        
        for i in range(0, len(paths), chunk_size_folders):
            chunk_paths = paths[i:i + chunk_size_folders]
            print(f"Importing batch {i//chunk_size_folders + 1} of {(len(paths)-1)//chunk_size_folders + 1}...")
            
            response = rag.import_files(
                corpus_name=self.corpus_name,
                paths=chunk_paths,
                chunk_size=512,
                chunk_overlap=100
            )
            count = response.imported_rag_files_count
            total_imported += count
            print(f"Batch imported {count} files.")
            
        print(f"Total import completed. Imported {total_imported} files across {len(paths)} folders.")
        return {"imported_rag_files_count": total_imported}

    def retrieve(self, query: str, similarity_top_k: int = 5) -> List[Any]:
        """Retrieves relevant context from the Corpus."""
        if not self.corpus_name:
             raise ValueError("RAG Corpus not initialized.")

        # Perform retrieval
        response = rag.retrieval_query(
            rag_resources=[
                rag.RagResource(
                    rag_corpus=self.corpus_name,
                )
            ],
            text=query,
            similarity_top_k=similarity_top_k,
        )
        return response.contexts

    def list_files(self) -> List[rag.RagFile]:
        """Lists all files in the corpus."""
        if not self.corpus_name:
            raise ValueError("RAG Corpus not initialized.")
            
        return list(rag.list_files(corpus_name=self.corpus_name))

    def generate_answer(self, query: str) -> Dict[str, Any]:
        """Generates an answer using Gemini grounded with retrieved context."""
        contexts = self.retrieve(query)
        
        # Manually construct context string (or use looking tools if available in SDK)
        # Using the standard context injection for now
        context_text = "\n\n".join([c.text for c in contexts.contexts])
        
        prompt = f"""You are a helpful assistant. Use the following context to answer the user's question.
        
Context:
{context_text}

Question:
{query}

Answer:"""
        
        # Using standard genai client for generation (as initialized in gemini_client, but simpler here)
        client = genai.Client()
        response = client.models.generate_content(
            model=GENERATION_MODEL,
            contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]
        )
        
        return {
            "answer": response.text,
            "citations": [
                {"title": c.source_display_name, "uri": c.source_uri} for c in contexts.contexts
            ]
        }

rag_client = RagClient()
