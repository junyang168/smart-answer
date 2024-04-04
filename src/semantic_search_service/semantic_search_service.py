from FlagEmbedding import BGEM3FlagModel
import os
import torch

from vespa.application import Vespa
from vespa.io import VespaQueryResponse    
from vespa.exceptions import VespaError
from vespa.deployment import VespaDocker
from vespa.package import Schema, Document, Field, FieldSet
from vespa.package import ApplicationPackage
from vespa.package import RankProfile, Function,  FirstPhaseRanking
from vespa.io import VespaResponse
from vespa.io import VespaQueryResponse
import json
from typing import List, Optional
from pydantic import BaseModel
import psycopg
import pickle
from vector_store import VectorStore
import numpy as np

class FeedPassage(BaseModel):
    id:str
    vespa_id: Optional[ str ] = None
    content_id:str
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
        embeddings["dense_vecs"].extend(res["dense_vecs"])
        embeddings["lexical_weights"].extend(res["lexical_weights"])
        embeddings["colbert_vecs"].extend(res["colbert_vecs"])

    def reset_vector_store(self):
        self.vector_store.initialize()
        

    def feed(self, passages : List[FeedPassage], persit = False):
        passage_embeddings = self.embed( [ p.text for p in passages ])
        passage_embeddings["ids"] = [ passage.id for passage in passages ]
        
        self.vector_store.save(passage_embeddings)
        if persit:
            self.vector_store.persist()


    def search(self, query:str, topK = 10) -> List[QueryResult]:
        query_embeddings = self.model.encode([query], return_dense=True, return_sparse=True, return_colbert_vecs=True)

        res = self.vector_store.retrieve(query_embeddings, topN=1000)
        return res[:topK]



def get_service() -> SemanticSearchService:
    return SemanticSearchService()



if __name__ == '__main__':
    import timeit
    t_0 = timeit.default_timer()
    svc = get_service()

    t_1 = timeit.default_timer()
    elapsed_time = round((t_1 - t_0) , 3)
    print(f"Load Service : {elapsed_time} s")

    res = svc.search('How to deploy vRSLCM to a VMWare Cloud Foundation(VCF) 3.x Workload Domain(WLD) that is using VLAN backed networks?')


    t_2 = timeit.default_timer()
    elapsed_time = round((t_2 - t_1) , 3)
    print(f"Query time : {elapsed_time} s")

    print(res)


