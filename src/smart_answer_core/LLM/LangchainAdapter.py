from smart_answer_core.LLM.LLMAdapter import LLMAdapter
from langchain import LLMChain
from langchain_openai import ChatOpenAI
import langchain.chains.retrieval_qa.prompt as qa
from langchain.prompts.chat import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
    MessagesPlaceholder
)
import langchain.agents.conversational_chat.prompt as ap
from dotenv import load_dotenv
load_dotenv()
import os

class LangchainAdapter(LLMAdapter):
    system_message = ap.PREFIX

    def __init__(self) -> None:
        super().__init__()
        self.api_url  = os.environ.get("LLM_API_URL") 
        self.api_key = os.environ.get("LLM_API_KEY") 
        if not self.api_key:
             self.api_key = "na"

         


    def supports(self, provider):
        return provider == "Langchain"
    
    def set_model(self,model):
         self.model = model
    


    def _create_prompt(self,user_prompt_template, inputs):            
            input_variables = list(inputs.keys())
            messages = [
                SystemMessagePromptTemplate.from_template(self.system_message),
                HumanMessagePromptTemplate.from_template(user_prompt_template)
            ]
            return ChatPromptTemplate(input_variables=input_variables, messages=messages)

    def askLLM(self,  user_prompt_template : str, inputs : dict):

        chat_prompt = self._create_prompt(user_prompt_template, inputs)

#        llm = AzureChatOpenAI(temperature = 0.0, deployment_name= 'gpt35turbo-16k')
        
        if self.api_url:
            llm = ChatOpenAI(temperature=0,model_name= self.model , openai_api_key = self.api_key, openai_api_base= self.api_url, streaming=False, max_tokens=1000)
        else:
            llm = ChatOpenAI(temperature=0,model_name= self.model , openai_api_key = self.api_key)

        chain = LLMChain(llm=llm, prompt = chat_prompt)

        return chain.run(inputs)
