from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline
from smart_answer_junyang168.LLM.LLMAdapter import LLMAdapter
import re

class GPTQAdapter(LLMAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.model_name_or_path = "TheBloke/OpenHermes-2.5-Mistral-7B-GPTQ"

        
    def askLLM(self,  user_prompt_template : str, inputs : dict):

        user_prompt = user_prompt_template.format(**inputs)

        system_message = "You are Support Assistant"
        prompt_template=f"""<|im_start|>system
        {system_message}<|im_end|>
        <|im_start|>user
        {user_prompt}<|im_end|>
        <|im_start|>assistant
        """

        tokenizer = AutoTokenizer.from_pretrained(self.model_name_or_path, use_fast=True)
        input_ids = tokenizer(prompt_template, return_tensors='pt').input_ids.cuda()

        model = AutoModelForCausalLM.from_pretrained(self.model_name_or_path,
                                                    device_map="auto",
                                                    trust_remote_code=False,
                                                    revision="main")

        output = model.generate(inputs=input_ids, temperature=0.1, do_sample=True, top_p=0.95, top_k=40, max_new_tokens=512)
        message =  tokenizer.decode(output[0])
#        print(message)

        reex = re.compile(r"\<\|im_start\|\>\s*assistant(.+)\<\|im_end\|\>", flags=re.DOTALL)
        mo =  reex.search(message)
        return mo.group(1)

    def support_model(self, name):
        return name == "TheBloke/OpenHermes-2.5-Mistral-7B-GPTQ"


