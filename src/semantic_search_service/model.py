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


class FeedPassage(BaseModel):
    id:str
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


class Model:
    CONNECTION_STRING = ""
    syn_records = []

    def __init__(self):
        use_gpu =  os.environ.get("USE_GPU") 
        print(f"use gpu: {use_gpu}")
        use_gpu =  use_gpu == 'True' and torch.cuda.is_available()        
        self.model =  BGEM3FlagModel('BAAI/bge-m3', use_fp16=use_gpu)    
        self.app = Vespa(url="http://localhost:8080")


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
            if total_len > 8000:
                self.create_embedding(passages[idx: i], embeddings)
                idx = i
                total_len = len( passages[i] )
            i += 1
        self.create_embedding(passages[idx:], embeddings)
        return embeddings

    def create_embedding(self, passages, embeddings):
        res = self.model.encode(passages, return_dense=True, return_sparse=True, return_colbert_vecs=True)
        embeddings["dense_vecs"].extend(res["dense_vecs"])
        embeddings["lexical_weights"].extend(res["lexical_weights"])
        embeddings["colbert_vecs"].extend(res["colbert_vecs"])




    def feed_callback(self, response:VespaResponse, id:str):
        for pa in ( p  for p in self.feed_passages if p.id == id):
            if not response.is_successful():
                pa.status = response.get_json()


    def feed(self, passages : List[FeedPassage]):
        self.feed_passages = passages
        passage_embeddings = self.embed( [ p.text for p in passages ])
        feed_data = []
        for i in range(len(passages)):
            passage = passages[i]
            feed_data.append(
                {
                    "id" : passage.id,
                    "fields": {
                        "content_id": passage.content_id,
                        "text": passage.text,
                        "language":passage.language,
                        "last_updated":passage.last_updated,
                        "lexical_rep": {key: float(value) for key, value in passage_embeddings['lexical_weights'][i].items()},
                        "dense_rep":passage_embeddings['dense_vecs'][i].tolist(),
                        "colbert_rep":  {index: passage_embeddings['colbert_vecs'][i][index].tolist() for index in range(passage_embeddings['colbert_vecs'][i].shape[0])}
                    }
                }
            )

        response: VespaResponse = self.app.feed_iterable(feed_data, schema='sa', callback=self.feed_callback)
        return self.feed_passages



    def search(self, query:str) -> List[QueryResult]:
        query_embeddings = self.embed([query])
        query_length = query_embeddings['colbert_vecs'][0].shape[0]
        query_fields = {
            "input.query(q_lexical)": {key: float(value) for key, value in query_embeddings['lexical_weights'][0].items()},
            "input.query(q_dense)": query_embeddings['dense_vecs'][0].tolist(),
            "input.query(q_colbert)":  str({index: query_embeddings['colbert_vecs'][0][index].tolist() for index in range(query_embeddings['colbert_vecs'][0].shape[0])}),
            "input.query(q_len_colbert)": query_length
        }
        response:VespaQueryResponse = self.app.query(
            yql="select id, content_id, text from sa where userQuery() or ({targetHits:10}nearestNeighbor(dense_rep,q_dense))",
            ranking="m3hybrid",
            query=query,
            body={
                **query_fields
            }
        )


        if response.is_successful():
            return [ QueryResult(id=h.get('id'), relevance_score = h.get('relevance'), text= h['fields'].get('text'), content_id= h['fields'].get('content_id'))  for h in response.hits if h.get('relevance') > 0.5]
        else:
            raise Exception(response.get_json())


def create_sa_package():

    vespa_app_name = "sa"
    
    m_schema = Schema(
                name= vespa_app_name,
                document=Document(
                    fields=[
                        Field(name="id", type="string", indexing=["summary"]),
                        Field(name="content_id", type="string", indexing=["summary"]),
                        Field(name="source", type="string", indexing=["summary"]),
                        Field(name="language", type="string", indexing=["summary"]),
                        Field(name="last_updated", type="string", indexing=["summary"]),
                        Field(name="text", type="string", indexing=["summary", "index"], index="enable-bm25"),
                        Field(name="lexical_rep", type="tensor<bfloat16>(t{})", indexing=["summary", "attribute"]),
                        Field(name="dense_rep", type="tensor<bfloat16>(x[1024])", indexing=["summary", "attribute"], attribute=["distance-metric: angular"]),
                        Field(name="colbert_rep", type="tensor<bfloat16>(t{}, x[1024])", indexing=["summary", "attribute"])
                    ],
                ),
                fieldsets=[
                    FieldSet(name = "default", fields = ["text"])
                ]
    )

    
    vespa_application_package = ApplicationPackage(
            name=vespa_app_name,
            schema=[m_schema]
    ) 



    semantic = RankProfile(
        name="m3hybrid", 
        inputs=[
            ("query(q_dense)", "tensor<bfloat16>(x[1024])"), 
            ("query(q_lexical)", "tensor<bfloat16>(t{})"), 
            ("query(q_colbert)", "tensor<bfloat16>(qt{}, x[1024])"),
            ("query(q_len_colbert)", "float"),
        ],
        functions=[
            Function(
                name="dense",
                expression="cosine_similarity(query(q_dense), attribute(dense_rep),x)"
            ),
            Function(
                name="lexical",
                expression="sum(query(q_lexical) * attribute(lexical_rep))"
            ),
            Function(
                name="max_sim",
                expression="sum(reduce(sum(query(q_colbert) * attribute(colbert_rep) , x),max, t),qt)/query(q_len_colbert)"
            )
        ],
        first_phase=FirstPhaseRanking(
            expression="0.4*dense + 0.2*lexical +  0.4*max_sim",
            rank_score_drop_limit=0.0
        ),
        match_features=["dense", "lexical", "max_sim", "bm25(text)"]
    )
    m_schema.add_rank_profile(semantic)


    vespa_docker = VespaDocker()
    app = vespa_docker.deploy(application_package=vespa_application_package)


model = Model()

def get_model():
    return model



if __name__ == '__main__':
    create_sa_package()
#    res = model.search('what is BGE')
#    print(res)