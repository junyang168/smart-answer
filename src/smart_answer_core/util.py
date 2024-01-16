from dotenv import load_dotenv
load_dotenv()
import os

CONNECTION_STRING =  os.environ.get("CONNECTION_STRING") 

__embedding_model =  os.environ.get("EMBEDDING_MODEL") 

def __init_model():
    if __embedding_model != 'BGE':
        return None
    
    from langchain.embeddings import HuggingFaceBgeEmbeddings
    model_name = "BAAI/bge-large-en-v1.5"
    encode_kwargs = {'normalize_embeddings': True} # set True to compute cosine similarity

    import torch
    use_gpu =  os.environ.get("USE_GPU") 

    print(f"use gpu: {use_gpu}")

    if use_gpu == 'True' and torch.cuda.is_available():
        print(f"cuda available: {torch.cuda.is_available()}")
        model_kwargs = {'device': 'cuda'}
    else:
        model_kwargs = {'device': 'cpu'}
        print("use CPU for embedding")

    model = HuggingFaceBgeEmbeddings(
        model_name=model_name,
        model_kwargs=model_kwargs,
        encode_kwargs=encode_kwargs,
        query_instruction="Represent this sentence for searching relevant passages:"
    )
    model.query_instruction = "Represent this sentence for searching relevant passages:"
    return model

__model = __init_model()


import numpy as np
def __create_bge_embeddings(inputs):
    return [ np.array( __model.embed_query(inp) )for inp in inputs ]


import openai
def __calculate_openai_embedding(inputs):
    output = []
    for inp in inputs:
        response =  openai.Embedding.create( input = inp, engine="ada-embedding")
        output.append( np.array( response['data'][0]['embedding'] ) )
    return output


def calculate_embedding(inputs):
    if __embedding_model == 'BGE':
        return __create_bge_embeddings(inputs)
    else:
        return __calculate_openai_embedding(inputs)


import psycopg2
def execute_sql(sql,params: tuple = None, return_column_names = False, connection_string = None):
    if not connection_string:
        connection_string = CONNECTION_STRING

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


from smart_answer_junyang168.LLM.LLMWrapper import LLMWrapper

def ask_llm( prompt_template : str, output_type = None,  **kwargs ):
    llm = LLMWrapper()
    return llm.askLLM(prompt_template, kwargs, output_type)



