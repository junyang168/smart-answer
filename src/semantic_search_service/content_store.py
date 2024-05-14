import os
import sys
from typing import List
import json
from pydantic import  BaseModel
from typing import List, Optional, Any


class HybridScore(BaseModel):
    id:str
    dense_score:float
    bm25_score:float
    colbert_score:float
    hybrid_score: Optional[float] = None
    content_id : Optional[str] = None 
    text: Optional[str] = None


    def model_post_init(self, __context: Any) -> None:
        self.hybrid_score = 0.6*self.colbert_score + 0.4*self.dense_score + 0.2 * self.bm25_score 


    def __str__(self) -> str:
        return self.__repr__() 

    def __repr__(self) -> str:
        return f"Result - id: {self.id} score:{self.hybrid_score} dense:{self.dense_score} bm25:{self.bm25_score} colbert:{self.colbert_score}\n"

class FeedPassage(BaseModel):
    id:str
    content_id: str
    text:str
    last_updated:Optional[str] = None
    language: Optional[str] = None
    status: Optional[str] = None



class ContentStore:
    def __init__(self, loadData = True) -> None:
        self.file_path = os.path.join( os.getenv('base_dir'), 'content_store','content.json')
        self.content = {}
        if loadData:
            self.load_data()
        else:
            self.passages : List[FeedPassage] = []
    
    def save(self, passages : List[FeedPassage] ):
        self.passages.extend(passages)
        self.build_index()

    def load_data(self):
        with open(self.file_path, "r") as file:
            self.passages = [ FeedPassage(**p) for p in json.load(file) ]
        self.build_index()

    def build_index(self):
        self.index = {  p.id: i for i, p in enumerate(self.passages) }

    def load_text(self, score_data : List[HybridScore]):
        for score in score_data:
            passage = self.passages[ self.index[score.id] ]
            score.text = passage.text 
            score.content_id = passage.content_id
        return score_data

    def persist(self):
        with open(self.file_path, "w", encoding='UTF-8') as file:
            data_dicts = [p.dict() for p in self.passages]
            json.dump(data_dicts, file, ensure_ascii=False, indent=4)    


    


