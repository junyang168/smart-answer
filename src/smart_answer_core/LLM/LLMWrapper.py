from langchain.output_parsers import PydanticOutputParser
from smart_answer_core.LLM.TogetherAdapter import TogetherAdapter
from smart_answer_core.LLM.LangchainAdapter import LangchainAdapter
from dotenv import load_dotenv
load_dotenv()
import os

class LLMWrapper:
    llm_adpaters = [
        TogetherAdapter(),
        LangchainAdapter()
    ]
    
    def __init__(self, provider = None, model = None) -> None:
        if not provider:
            provider  = os.environ.get("LLM_PROVIDER") 
        if not model:
            model  = os.environ.get("LLM_MODEL") 

        self.__llmadapter =  [ a for a in self.llm_adpaters if a.supports(provider) ][0] 
        self.__llmadapter.set_model(model)



    
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
    
        
    def askLLM(self, user_prompt_template, inputs, output_type ):
        inputs = dict(inputs)
        if output_type:
            parser = PydanticOutputParser(pydantic_object=output_type)           
            inputs["format_instructions"]  = parser.get_format_instructions() 
        out = self.__llmadapter.askLLM(user_prompt_template, inputs)
        if output_type:
            out2 = self.check(out)
            return parser.parse(out2)
        else:
            return out

    
