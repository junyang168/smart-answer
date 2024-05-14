from FlagEmbedding import BGEM3FlagModel
import os
import torch

from typing import List, Optional
from pydantic import BaseModel
from vector_store import VectorStore
import numpy as np

class FeedPassage(BaseModel):
    id:str
    content_id: str
    text:str
    last_updated:Optional[str] = None
    language: Optional[str] = None
    status: Optional[str] = None

class QueryResult(BaseModel):
    id:str
    content_id:str 
    relevance_score:float
    text: str

class SemanticSearchService:
    syn_records = []

    def __init__(self, load_data = True):
        use_gpu =  os.environ.get("USE_GPU")         
        use_gpu =  use_gpu == 'True' and torch.cuda.is_available()        
        print(f"use gpu: {use_gpu}")
        self.model =  BGEM3FlagModel('BAAI/bge-m3', use_fp16=use_gpu) 
        self.vector_store = VectorStore(load_data=load_data)         

    def embed(self,passages : List[str]):
        total_len = 0
        i = 0
        idx = 0
        embeddings = {
            "lexical_weights":[],
            "dense_vecs":[],
            "colbert_vecs":[]

        }
        while i < len(passages):
            total_len +=  len( passages[i] )
            if total_len > 10000:
                self.create_embedding(passages[idx: i], embeddings)
                idx = i
                total_len = len( passages[i] )
            i += 1
        self.create_embedding(passages[idx:], embeddings)
        return embeddings

    def create_embedding(self, passages, embeddings):
        if len(passages) == 0:
            return
        res = self.model.encode(passages, return_dense=True, return_sparse=True, return_colbert_vecs=True)
#        res = self.model.encode(passages, return_dense=False, return_sparse=True, return_colbert_vecs=False)

        if res.get("dense_vecs") is not None:
            embeddings["dense_vecs"].extend(res.get("dense_vecs"))
        else:
            embeddings["dense_vecs"].extend([np.empty(1024) for _ in range(len(passages))])

        if res.get("lexical_weights") is not None:
            embeddings["lexical_weights"].extend(res.get("lexical_weights"))
        else:
            embeddings["lexical_weights"].extend([np.empty(1) for _ in range(len(passages))])

        if res.get("colbert_vecs") is not None:            
            embeddings["colbert_vecs"].extend(res.get("colbert_vecs"))
        else:
            embeddings["colbert_vecs"].extend([np.empty((1,1024)) for _ in range(len(passages))])

    def reset_vector_store(self):
        self.vector_store.initialize()
        

    def feed(self, passages : List[FeedPassage]):
        passage_embeddings = self.embed( [ p.text for p in passages ])
        passage_embeddings["ids"] = [ passage.id for passage in passages ]
        
        self.vector_store.save(passage_embeddings)

    def persist(self):
        self.vector_store.persist() 


    def search(self, query:str, topK = 10) -> List[QueryResult]:
        query_embeddings = self.model.encode([query], return_dense=True, return_sparse=True, return_colbert_vecs=True)

        res = self.vector_store.retrieve(query_embeddings, topN=1000)
        return res[:topK]



def get_service() -> SemanticSearchService:
    return SemanticSearchService()



if __name__ == '__main__':
    import timeit
    svc = get_service()

    res = svc.search('基督徒能不能吃祭過偶像的食物？')


    print(res)


