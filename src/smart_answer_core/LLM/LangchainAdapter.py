from smart_answer_core.LLM.LLMAdapter import LLMAdapter
from langchain import LLMChain
from langchain.chat_models import AzureChatOpenAI
from langchain.chat_models import ChatOpenAI
import langchain.chains.retrieval_qa.prompt as qa
from langchain.prompts.chat import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
    MessagesPlaceholder
)
import langchain.agents.conversational_chat.prompt as ap
import os

class LangchainAdapter(LLMAdapter):
    system_message = ap.PREFIX

    def __init__(self) -> None:
         super().__init__()


    def support_model(self, name):
        return name == "GPT"
    


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
        
        api_key = os.environ.get("OPENAI_API_KEY") 

        llm = ChatOpenAI(temperature=0,model_name="gpt-4-0613", openai_api_key = api_key)

        chain = LLMChain(llm=llm, prompt = chat_prompt)

        return chain.run(inputs)

