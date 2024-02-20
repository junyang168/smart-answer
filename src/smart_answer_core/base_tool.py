from pydantic import BaseModel
from typing import List


class Reference(BaseModel):
    Title: str
    Link : str

class RetrievalResult(BaseModel):
    prefix: str  = ""
    content: str
    references: List[Reference]


class base_tool:
    name = ""
    description = ""


    def get_few_shots(self):
        return []

    def retrieve(self, args, question:str) -> RetrievalResult:
        return None

    def get_answer_prompt_template(self, prompt_template, context):
        return prompt_template
    
    def is_fallback_tool(self):
        return False

