import os
#os.environ["LLM"]  = "mistralai/Mixtral-8x7B-Instruct-v0.1"
#os.environ["LLM"]  = 'teknium/OpenHermes-2p5-Mistral-7B'
#os.environ["OPENAI_API_KEY"] = "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"

from smart_answer_core.chat_memory import ChatMemory
from smart_answer_core.util import ask_llm
import json

CONNECTION_STRING="postgresql://postgres:airocks$123@192.168.242.24:5432/postgres"
os.environ["CONNECTION_STRING"] = CONNECTION_STRING


class GhostwriterService:

    ghost_writer_template = """
    You are a technical writer helping you support agent to write knowledge base articles to troubleshoot a VMWare product issue. Your task is to collect related information from support agent. 

    Support Agent should first provide the type of KB articles to the technical write.
    There are two types of Knowledge base(KB) articles. 

    1. How to: is the KB to document a problem with a fix or workaround
    2. Informational: provides facts/announcements

    After identifying type of knowledge article, technical writer should also ask support agent for production and version of the VMware product

    For How to Knowledge base articles. Technical writer will
    1. ask for the SME is the KB related to. The SME can be Storage or networking
    2. ask support agent to provide description of the issue. The description should contain information of how issue is presented. Examples are: User sees an error message, log bundle comment, host PSOD.
    and the impact of the error. Examples are: VM availability impacted, host access is lost


    Create a question for support agent base on conversation history. Create One question at a time.

    Conversation History:
    {chat_history}

    Question:

    Let’s go
    """

    def __init__(self, sid) -> None:
        self.sid = sid
        self.chatMemory = ChatMemory(sid= sid, message_window=20, connection_string= CONNECTION_STRING) 
        self.chatMemory.set_roles(human_role='Support Agent',ai_role='Technical Writer')

        current_dir = os.path.dirname(os.path.abspath(__file__))

        self.prompte_path = os.path.join(current_dir + '/../app', 'prompt_template.json')

        with open(self.prompte_path, 'r') as f:
            temp = json.load(f)
            self.ghost_writer_template = temp["template"]


    def get_question(self,answer):
        if answer:
            self.chatMemory.add_human_message(answer)
        chat_history = self.chatMemory.get_chat_history()    
        question = ask_llm(self.ghost_writer_template,output_type=None, chat_history= chat_history)
        self.chatMemory.add_ai_message(question)
        return "Technical Writer: " + question
    
    def get_chat_history(self):
        return self.chatMemory.get_chat_history()   

    def get_prompt_template(self):
        return self.ghost_writer_template 

    def set_prompt_template(self, prompt_template):
        self.ghost_writer_template = prompt_template
        with open(self.prompte_path, 'w') as f:
            json.dump({"template":self.ghost_writer_template}, f)        


