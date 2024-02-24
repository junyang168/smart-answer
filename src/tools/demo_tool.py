from smart_answer_core.base_tool import base_tool

from smart_answer_core.base_tool import RetrievalResult
from smart_answer_core.base_tool import Reference


## Loading Environment Variables
from dotenv import load_dotenv
load_dotenv()
import os
from smart_answer_core.logger import logger


import pandas as pd
import psycopg2
from psycopg2.sql import Identifier, SQL

class DemoTool(base_tool):
    name = "China Telecom Knowledge Base"
    description = """
      """

    def is_fallback_tool(self):
        return True
            
    def retrieve(self, args :str, question : str) -> RetrievalResult:
        print(args)
        print(question)

        # Open the file in read mode ('r')
        with open('/Users/junyang/app/smart-answer/web/public/china_telecom_kb.txt', 'r') as file:
            # Read the entire file
            content = file.read()
            ref = Reference(Title='China Telecom Tech Support', Link='https://smart-answer.ai/china_telecom_kb.txt')
            ret = RetrievalResult(content=content,references=[ref])
            return ret

    def get_answer_prompt_template(self,default_prompt, context):
        return  """ 
Answer the question in Chinese at the end using the following pieces of context. 
    If there is not enough information in the context to answer the question, explain why the question can not be answered with the context. don't try to make up an answer.
    Format response in markdown.
    {context}
Question: {question}
    你的回答:"""

