from langchain_openai import ChatOpenAI
import langchain.chains.retrieval_qa.prompt as qa
from langchain_core.prompts import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
    MessagesPlaceholder
)
from langchain_core.runnables.history import RunnableWithMessageHistory

from langchain_core.output_parsers import JsonOutputParser
import langchain.agents.conversational_chat.prompt as ap
from dotenv import load_dotenv
load_dotenv()
import os
from langchain_core.chat_history import BaseChatMessageHistory
from langchain.memory import PostgresChatMessageHistory

store = {}

def get_session_history(session_id:str) -> BaseChatMessageHistory:
    if session_id not in store:
        store[session_id] = PostgresChatMessageHistory(session_id,  os.environ.get("CONNECTION_STRING") )
    return store[session_id]


class LLMWrapper:

    def __init__(self, model = None) -> None:
        self.api_url  = os.environ.get("LLM_API_URL") 
        self.api_key = os.environ.get("LLM_API_KEY") 
        if not self.api_key:
             self.api_key = "na"
        if not model:
            self.model  = os.environ.get("LLM_MODEL") 


    def _create_prompt(self,user_prompt_template :str, sid :str):   
        messages = [SystemMessagePromptTemplate.from_template(user_prompt_template)]
        if sid:
            messages.append(MessagesPlaceholder(variable_name="history"))
        messages.append(HumanMessagePromptTemplate.from_template('{question}'))        
        return ChatPromptTemplate.from_messages(messages)

    # Function to check parentheses
    def check(self, myStr):
        stack = []
        post = -1
        for i,ch in enumerate(myStr):
            if ch == '{':
                stack.append(ch)
            elif ch == '}':
                if len(stack) > 0:
                    stack.pop()
                    if len(stack) == 0:
                        return myStr[:(i+1)]
                else: 
                    return None
        return None

    def askLLM(self, user_prompt_template : str, format, sid:str , inputs):
        inputs = dict(inputs)
        if format == 'Json':
            parser = JsonOutputParser()          
            inputs["format_instructions"]  = parser.get_format_instructions() 

        chat_prompt = self._create_prompt(user_prompt_template, sid )
        
        if self.model.startswith('openai/'):  
            model_name = self.model[len('openai/'):]          
            llm = ChatOpenAI(temperature=0,model_name= model_name)
        else:
            llm = ChatOpenAI(temperature=0,model_name= self.model, openai_api_key = self.api_key, openai_api_base= self.api_url, streaming=False, max_tokens=1000)

        runnable = chat_prompt | llm

#        if sid : 
#            with_message_history = RunnableWithMessageHistory(
#                runnable,
#                get_session_history,
#                input_messages_key="question",
#                history_messages_key='history'
#            )
#            out =  with_message_history.invoke(inputs, config={"configurable": {"session_id": sid}})
#        else:
        out = runnable.invoke(inputs)

        if format == 'Json':
            out2 = self.check(out.content)
            return parser.parse(out2)
        else:
            return out.content
        