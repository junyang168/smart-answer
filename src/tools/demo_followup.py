from smart_answer_core.base_tool import base_tool

from smart_answer_core.base_tool import RetrievalResult
from smart_answer_core.base_tool import Reference


## Loading Environment Variables
from dotenv import load_dotenv
load_dotenv()
import os
from smart_answer_core.logger import logger


import pandas as pd
import psycopg
from psycopg.sql import Identifier, SQL
import pandas as pd

class DemoFollowupTool(base_tool):
    name = "Followup Knowledge Base"
    description = """
      """

    def is_fallback_tool(self):
        return True
            
    def retrieve(self, args :str, question : str) -> RetrievalResult:
        kb_file = os.environ["DEMO_KB_FILE"]

        df = pd.read_excel(kb_file)
        content = ""
        for index, row in df.iterrows():
            content += "-"
            for column in df.columns:
                content += f"{column}: {row[column]} \t"
            content += "\n"
        ref = Reference(Title='China Telecom Tech Support', Link='https://smart-answer.ai/China-Telecom.xlsx')
        ret = RetrievalResult(content=content,references=[ref])
        return ret
    


    def get_answer_prompt_template(self,default_prompt, context):
        return  """ 
You are a technical support expert at VMWare. You are having a conversation with the user to troubleshoot technical issues. 
User will tell you the symptom of the issue. Your task is to use the knowledge in the context to 
1. If user’s issue matches multiple symptom detail, Generate a followup question to get additional symptom detail.  Give a bullet list of detailed symptoms.    
2. If the user's input matches the symptom detail, respond with the root cause and resolution in the following format
Your issue is cause by  root cause in the context
This issue can be resolved by resolution in the context

You must respond according to the chat history and the context.  Only generate one response at a time!  
Format response in markdown.

Example 1:
Context:
- High Level Symptom: user is unable to log into VM \t Detailed Symptom: Invalid password \t Root Cause:  user entered the wrong password \t Resolution: Enter the correct password
- High Level Symptom: user is unable to log into VM \t Detailed Symptom: Resource expired \t Root Cause: Resources have expired. \t Resolution: Tell the user that the resource has expired on xxx-xx-2024

Chat
    User: I can’t log into VM
    AI: There can be multiple causes to the issue. I need to detail about the error message. For example, Invalid password, resources expired 
    User: Error  is Invalid password 
    AI: Your issue is caused by user entered the wrong password.  Enter the correct password
End of example.


Context:
{context}

Your Response:"""

