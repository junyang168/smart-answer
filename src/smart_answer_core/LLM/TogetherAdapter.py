from smart_answer_core.LLM.LLMAdapter import LLMAdapter
import together
from dotenv import load_dotenv
load_dotenv()
import os


together.api_key = "95ff1832749b6f85200b78384ac0961081363674d2620c6a62f772298876b89c"


class TogetherAdapter(LLMAdapter):

    def __init__(self, llm = None) -> None:
        self.model = llm


    def supports(self, provider):
        return provider == "Together"

    def set_model(self,model):
         self.model = model
    
    
    def askLLM(self,  user_prompt_template : str, inputs : dict):
        user_prompt = user_prompt_template.format(**inputs)
        output = together.Complete.create(
        prompt = f"<human>:{user_prompt}<bot>:", 
        model = self.model, 
        max_tokens = 512,
        temperature = 0.1,
        top_k = 50,
        top_p = 0.7,
        repetition_penalty = 1,
        stop = ['<human>', '\n\n']
        )
        return output['output']['choices'][0]['text']