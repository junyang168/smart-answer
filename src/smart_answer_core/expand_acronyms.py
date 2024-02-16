import spacy
import smart_answer_core.util as util

    
class acconym_expansion(object):
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(acconym_expansion, cls).__new__(cls)
            # Put any initialization here.

        return cls._instance
    

    def load_acronyms(self):
        ds = util.execute_sql("""
            select a."Acronyms" acronyms, a."Definition" definition 
            from acronyms a 
            where coalesce(a."Remove" ,'N') <> 'Y'                           
            """)
        return { r[0] :r[1] for r in ds }

    def __init__(self) -> None:
        try:
            self.nlp = spacy.load("en_core_web_lg")
            # Define a dictionary of acronyms and their expansions
            self.acronym_dict = self.load_acronyms()
        except:
            self.nlp = None

    # Function to expand acronyms in text
    def expand_acronyms(self,text):
        if not self.nlp:
            return False, text
        
        tokens = self.nlp(text)
        expanded_text = []
        expanded = False
        
        for token in tokens:
            key = token.text.upper()
            if token.is_oov and not token.is_stop and key in self.acronym_dict:
                # If the token is an acronym, replace it with its expansion
                expanded = True
                expanded_text.append(f"{self.acronym_dict[token.text.upper()]}({token.text})")
            else:
                # If it's not an acronym, keep the original token
                expanded_text.append(token.text)
        
        # Join the expanded tokens back into a single string
        return expanded, ' '.join(expanded_text)



