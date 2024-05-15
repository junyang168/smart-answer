from langchain.chains import LLMChain
from langchain.chains.conversation.memory import ConversationBufferWindowMemory
from smart_answer_core.util import ask_llm
from langchain.schema.messages import HumanMessage
from smart_answer_core.LLMWrapper import LLMConfig
import os
from langchain_community.chat_message_histories import SQLChatMessageHistory
#from langchain.memory import PostgresChatMessageHistory



class ChatMemory: 
    CONDENSE_QUESTION_PROMPT = """Given the following conversation and a follow up question, rephrase the follow up question to be a standalone question.
Chat History:
{chat_history}
Follow Up Input: {question}
Standalone question:"""


    human_role = "human"
    ai_role = "ai"        

    def __init__(self, llm_cfg : LLMConfig, sid = None, message_window = 3, connection_string = None) -> None:  
        self.memory = None    
        self.llm_cfg = llm_cfg  
        if  connection_string:
            self.connection_string = connection_string
        else:
            self.connection_string =  os.getenv("CONNECTION_STRING") 
        if sid:
            chat_message_history = SQLChatMessageHistory(
                session_id=sid, connection_string= self.connection_string
            )            
            self.memory = ConversationBufferWindowMemory(
                    memory_key='chat_history',
                    k= message_window,
                    chat_memory = chat_message_history,  #PostgresChatMessageHistory(sid, self.connection_string),
                    return_messages=True

        )

    def _create_standalone_question(self,chat_history, question):
        out = ask_llm(self.llm_cfg, self.CONDENSE_QUESTION_PROMPT,output_type=None,chat_history = chat_history, question = question)
        return out
    
    def add_question(self, question, isFollowUp = False):
        if self.memory:
            ch_list =  self.memory.load_memory_variables({}).get('chat_history')
            self.memory.chat_memory.add_user_message(question)
            if isFollowUp and ch_list:        
                chat_history = '\n'.join([ ( self.human_role if isinstance(msg, HumanMessage) else self.ai_role) + ": " + msg.content for msg in  ch_list ] )
                question = self._create_standalone_question(chat_history, question)
        return question

    
    def add_answer(self, answer):
        if self.memory:
            self.memory.chat_memory.add_ai_message(answer)


    def set_roles(self, human_role, ai_role):
        self.human_role = human_role
        self.ai_role = ai_role

    def add_human_message(self, message):
        self.memory.chat_memory.add_user_message(message)

    def add_ai_message(self, message):
        self.memory.chat_memory.add_ai_message(message)

    def get_chat_history(self):
        if( self.memory):
            return self.memory.load_memory_variables({}).get('chat_history')
        return []
