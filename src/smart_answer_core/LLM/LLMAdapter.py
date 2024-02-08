from abc import ABC, abstractmethod

class LLMAdapter(ABC):
    @abstractmethod
    def supports(self,provider):
        pass

    @abstractmethod
    def askLLM(self,  user_prompt_template : str, inputs : dict):
        pass

    @abstractmethod
    def set_model(self, model):
        pass
