import markdown
from bs4 import BeautifulSoup
import tools.common as common
import itertools 
import re
import sqlalchemy
import pandas as pd


def markdown_to_plain_text(markdown_text):
    # Initialize a Markdown instance
    md = markdown.Markdown()
    
    # Convert the Markdown text to HTML
    html_text = md.convert(markdown_text)
    
    # Remove HTML tags to get plain text
    plain_text = ''.join(BeautifulSoup(html_text, "html.parser").findAll(text=True))
    
    return plain_text

def clean_up_text(input_text):
    input_text = input_text.split('\n')[0]
    input_text =  markdown_to_plain_text(input_text)
    input_text = re.sub(r'\(.*\)','',input_text)
    input_text = input_text.replace('\n','')
    return input_text

def load_acronyms():
    return common.execute_sql("select * from v_acronym")

def get_acronym_definition():
    ds = load_acronyms()
    acronyms =  [(r[0], clean_up_text(r[1]) ) for r in ds ]
    an_iterator = itertools.groupby(acronyms, lambda x : x[0])
    acronyms_definition = [] 
    for word, group in an_iterator: 
        defs = list(group)
        for defi in defs:
            if len(defi[1]) > 0: 
                acronyms_definition.append(defi)
    return acronyms_definition



def save_acronym_definition(acronyms_definition):
    df = pd.DataFrame(acronyms_definition, columns=["acronyms", "definition"])
    # Create a database engine
    engine = sqlalchemy.create_engine(common.CONNECTION_STRING)

    # Save the dataframe to a table in the database
    df.to_sql("acronyms_definition", engine, index=False, if_exists="replace")

    # Close the engine
    engine.dispose()

acronyms_definition = get_acronym_definition()
save_acronym_definition(acronyms_definition)
