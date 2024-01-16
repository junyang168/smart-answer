from abc import ABC, abstractmethod

class LLMAdapter(ABC):
    @abstractmethod
    def support_model(self,name):
        pass

    @abstractmethod
    def askLLM(self,  user_prompt_template : str, inputs : dict):
        pass
