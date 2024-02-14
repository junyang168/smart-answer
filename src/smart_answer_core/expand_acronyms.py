import spacy
import smart_answer_core.util as util



# Load the spaCy model
nlp = spacy.load("en_core_web_lg")

def load_acronyms():
    ds = util.execute_sql("""
        select a."Acronyms" acronyms, a."Definition" definition 
        from acronyms a 
        where coalesce(a."Remove" ,'N') <> 'Y'                           
        """)
    return { r[0] :r[1] for r in ds }

# Define a dictionary of acronyms and their expansions
acronym_dict = load_acronyms()

# Function to expand acronyms in text
def expand_acronyms(text):
    tokens = nlp(text)
    expanded_text = []
    expanded = False
    
    for token in tokens:
        key = token.text.upper()
        if token.is_oov and not token.is_stop and key in acronym_dict:
            # If the token is an acronym, replace it with its expansion
            expanded = True
            expanded_text.append(f"{acronym_dict[token.text.upper()]}({token.text})")
        else:
            # If it's not an acronym, keep the original token
            expanded_text.append(token.text)
    
    # Join the expanded tokens back into a single string
    return expanded, ' '.join(expanded_text)



