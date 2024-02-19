from abc import ABC, abstractmethod

class content_connector(ABC):

    @abstractmethod
    def get_source(self):
        pass

    @abstractmethod
    def get_collection_name(self):
        pass

    @abstractmethod
    def get_content_list(self):
        pass
    
    @abstractmethod
    def get_content(self,content_ids):
        pass
    
    @abstractmethod
    def get_content_meta_text(self, meta, content):
        pass
    
    
    @abstractmethod
    def generate_questions(self, meta, txt):
        pass
