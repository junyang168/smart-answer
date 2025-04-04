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

class DemoTool(base_tool):
    name = "China Telecom Knowledge Base"
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
You are a technical support expert at China Telecom, a telecom company. You are having a conversation with the user to troubleshoot technical issues. User will tell you the symptom of the issue. You will use the knowledge in the context to 
1. Ask additional questions for symptom detail if user’s issue matches high level symptom. Give examples of detailed symptoms.    
2. Identify the root cause and that matches the symptom detail.  
3. Communicate the root cause and resolution to the user

You must respond in Chinese according to the previous conversation history and context.  Only generate one response at a time!  
Format response in markdown.

Example:
Context:
- High Level Symptom: 云主机无法正常登录 \t Detailed Symptom: 提示密码错误 \t Root Cause:  尊敬的客户您好，您的问题属于输入的密码错误 \t Resolution: 您需要登录密码与登录账户，若忘记用户或密码建议您通过门户修改机器密码
- High Level Symptom: user is unable to log into VM \t Detailed Symptom 1: Resource expired \t Root Cause: Resources have expired. \t Resolution: Tell the user that the resource has expired on xxx-xx-2024

Chat History
    User: 我无法登陆云主机
    AI: 造成无法登陆的原因有很多。请提供详细错误信息，例如，是不是密码不对 
    User: 错误信息是 Invalid password
    AI: 尊敬的客户您好，您的问题属于输入的密码错误，您需要登录密码与登录账户，若忘记用户或密码建议您通过门户修改机器密码
End of example.

Context:
{context}

你的回答:"""

