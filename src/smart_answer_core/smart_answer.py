
from smart_answer_core.tool_selector import tool_selector
import smart_answer_core.util as util
from smart_answer_core.expand_acronyms import acconym_expansion
from smart_answer_core.chat_memory import ChatMemory
from pydantic import BaseModel
from typing import List
from smart_answer_core.base_tool import Reference
from langchain.schema.messages import HumanMessage
from smart_answer_core.LLMWrapper import LLMConfig
from smart_answer_core.base_tool import base_tool
from typing import List

class HistoryEntry(BaseModel):
    Role: str
    Message: str

class SmartAnswerResponse(BaseModel):
    answer: str
    references: List[Reference] 
    tool: str 
    new_question: str
    chat_history : List[HistoryEntry] 
    duplicate_question : bool


class SmartAnswer:
    prompt_template = """Answer the question at the end using the following pieces of context. 
    If there is not enough information in the context to answer the question, explain why the question can not be answered with the context. don't try to make up an answer.
    Format response in markdown.
    {context}

    Question: {question}
    Helpful Answer:"""


    def __init__(self, tools : List[base_tool], llm_cfg : LLMConfig = None ) -> None:
        self.selector = tool_selector(tools, llm_cfg)
        self.llm_cfg = llm_cfg

    def __get_answer(self, question:str, sid:str, context, tool, history ):
        prompt_template = tool.get_answer_prompt_template(self.prompt_template, context)
        return util.ask_llm(self.llm_cfg, prompt_template, output_type= None, sid=sid, question = question, context=context, history = history )

    def __get_content_reference(self, result):
        if not result:
            return None, None
        
        if isinstance(result, str) :
            return result, None
        else:
            return result.content, result.references
    
    def __is_duplicate_question(self, chat_history, question:str) -> bool :
        return len( chat_history ) >= 2 and chat_history[-2].content == question

    def __format_chat_history(self, chat_history):
        return [ HistoryEntry(Role= "human" if isinstance(msg, HumanMessage) else "ai", Message= msg.content) for msg in  chat_history ]

    def get_smart_answer(self, question : str, sid :str = None,isFollowUp : bool = False, context_only:bool = False) -> SmartAnswerResponse:         
        if not question:
          return ("", None, None, None )
        
        isFollowUp = sid and len(sid) > 0

        chatMemory = ChatMemory(self.llm_cfg, sid) 
        chat_history = chatMemory.get_chat_history()
        if self.__is_duplicate_question(chat_history,question):
            answer = chat_history[-1].content
            chat_history.pop()
            return SmartAnswerResponse(answer=answer, references=[], tool="DemoTool", new_question=question, duplicate_question=True,
                                    chat_history= self.__format_chat_history(chat_history) )    
        
        ae = acconym_expansion()
        expanded, expanded_question = ae.expand_acronyms(question)
        if expanded:
            question = expanded_question

        question = chatMemory.add_question(question, isFollowUp )      

        tool, args = self.selector.select_tool(question)
        answer = None
        if tool:
            result = tool.retrieve(args, question)
            if not result:
                result = self.selector.get_fallback_tool().retrieve(args, question)

            context_content, reference = self.__get_content_reference(result)

            if isinstance(result, str):
                answer = result
            else:
                question_prefix = ""
                if result:
                    question_prefix = result.prefix 
                answer = self.__get_answer( question_prefix + question, sid, context_content, tool, chat_history)
        if answer:
            chatMemory.add_answer(answer)

        return SmartAnswerResponse(answer=answer, references=reference, tool=tool.name, new_question=question, duplicate_question=False,
                                   chat_history= self.__format_chat_history(chat_history))    

if __name__ == '__main__':


    questions = [
        "How many days are left until ESXi version 5.1 reaches the end of technical guidance?"
]


    sa = SmartAnswer()

    for q in questions:
#        answer = get_smart_answer(q, sid, True)
        answer = sa.get_smart_answer(q)
        print(answer[0])

