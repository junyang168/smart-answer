from dotenv import load_dotenv
load_dotenv()
import os
from embedding_client import Client
from embedding_client.models import EmbeddingRequest, EmbeddingResponse
from  embedding_client.api.default.embed_embed_post import sync




__embedding_model =  os.environ.get("EMBEDDING_MODEL") 

__embedding_api_url = os.environ.get("EMBEDDING_API_URL") 

import numpy as np

def __create_bge_embeddings(text):
    
    client = Client(base_url=__embedding_api_url)

    Req = EmbeddingRequest(text=text)

    resp = sync(client=client,body=Req)

    return  np.array( resp.embeddings ) 


import openai
def __calculate_openai_embedding(text):
    response =  openai.Embedding.create( input = inp, engine="ada-embedding")
    return np.array( response['data'][0]['embedding'] ) 


def calculate_embedding(text):
    if __embedding_model == 'BGE':
        return __create_bge_embeddings(text)
    else:
        return __calculate_openai_embedding(text)


import psycopg2
def execute_sql(sql,params: tuple = None, return_column_names = False, connection_string = None):
    if not connection_string:
        connection_string = os.environ["CONNECTION_STRING"]


    conn = psycopg2.connect(connection_string)
    cur = conn.cursor()
    if params:
        cur.execute(sql,params)
    else:
        cur.execute(sql)
    ds = cur.fetchall()
    if return_column_names:
        columns = [ c.name for c in cur.description]
    cur.close()
    conn.close()

    if return_column_names:
        return ds, columns
    else:
        return ds


def run_dml(sql,params: tuple = None, is_proc=False, connection_string = None):
    if not connection_string:
        connection_string = CONNECTION_STRING

    conn = psycopg2.connect(connection_string)
    cur = conn.cursor()
    if is_proc:
        cur.execute('CALL ' + sql + '();')
    else:
        if params:
            cur.execute(sql,params)
        else:
            cur.execute(sql)
    conn.commit()
    cur.close()
    conn.close()


from smart_answer_core.LLM.LLMWrapper import LLMWrapper

def ask_llm( prompt_template : str, format = None,  **kwargs ):
    llm = LLMWrapper()
    return llm.askLLM(prompt_template, kwargs, format)



import re

def get_product_name_version( product_release:str): 
    z =  re.search("(.*)version(\s\d+.*)$", product_release)
    if not z:
        z = re.search("(.*)(\s\d+.*)$", product_release)

    if z:
        product_name = z.group(1)
        version = z.group(2)
    else:
        product_name = product_release
        version = None

    product_name = strip(product_name)
    if product_name and product_name.upper().startswith('VMWARE '):
        product_name = product_name[len('VMWARE '):]

    return product_name, strip(version)

def strip( name:str):
    return name.strip() if name else None


def print_result(ds, cols, no_data_message = None):
    if len(ds) == 0:
        return  "No Data is available" if no_data_message is None else no_data_message
    else:                
        txt_arr = []
        txt_key = set()
        for r in ds:
            line =  ', '.join( f"{cols[i]} {'is' if len(cols[i]) > 0 else ''} {r[i] if r[i] else ' '}" for i in range(len(cols)) )
            if line in txt_key:
                continue
            txt_arr.append('-' + line)
            txt_key.add(line)
            if len(txt_arr) > 100:
                break        
    return '\n'.join(txt_arr)
