from FlagEmbedding import BGEM3FlagModel
import os
import torch

from typing import List, Optional
from pydantic import BaseModel
from vector_store import VectorStore
from content_store import ContentStore, HybridScore, FeedPassage    
from keypoints_search import keypointSearch
import numpy as np


class SemanticSearchService:
    syn_records = []

    def __init__(self, load_data = True):
        use_gpu =  os.environ.get("USE_GPU") 
        if use_gpu == 'True':        
            if torch.cuda.is_available():
                use_gpu = True
            elif torch.backends.mps.is_available():
                use_gpu = True      
        print(f"use gpu: {use_gpu}")
        self.model =  BGEM3FlagModel('BAAI/bge-m3', use_fp16=use_gpu) 
        self.vector_store = VectorStore(load_data=load_data)  
        self.content_store = ContentStore()       

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
        self.content_store.save(passages)

    def persist(self):
        self.vector_store.persist() 
        self.content_store.persist()


    def search(self, query:str, topK = 10) -> List[HybridScore]:
        query_embeddings = self.model.encode([query], return_dense=True, return_sparse=True, return_colbert_vecs=True)

        res = self.vector_store.retrieve(query_embeddings, topN=1000)
        res_text =  self.content_store.load_text( res[:topK] )
        content_items = [ d for d in res_text if d.hybrid_score >= 0.65 ]
        if len(content_items) == 0:
            content_items = keypointSearch.search(query)

        return content_items


semantic_service = SemanticSearchService()

if __name__ == '__main__':
    import timeit
    svc = semantic_service

    res = svc.search('基督徒能过万圣节吗？')


    print(res)


