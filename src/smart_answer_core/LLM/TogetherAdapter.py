from smart_answer_junyang168.LLM.LLMAdapter import LLMAdapter
import together
from dotenv import load_dotenv
load_dotenv()
import os


together.api_key = "95ff1832749b6f85200b78384ac0961081363674d2620c6a62f772298876b89c"


class TogetherAdapter(LLMAdapter):

    def __init__(self, llm = None) -> None:
        self.model = llm


    def support_model(self, name):
        self.model = name
        return name in ['mistralai/Mixtral-8x7B-Instruct-v0.1','teknium/OpenHermes-2p5-Mistral-7B']
    
    
    def askLLM(self,  user_prompt_template : str, inputs : dict):
        user_prompt = user_prompt_template.format(**inputs)
        output = together.Complete.create(
        prompt = f"<human>:{user_prompt}<bot>:", 
        model = self.model, 
        max_tokens = 256,
        temperature = 0.1,
        top_k = 60,
        top_p = 0.6,
        repetition_penalty = 1.1,
        stop = ['<human>', '\n\n']
        )
        return output['output']['choices'][0]['text']