import psycopg2
import connectors
from pgvector.psycopg2 import register_vector

import sys
import os 

# Get the current script's directory
current_dir = os.path.dirname(os.path.abspath(__file__))
# Get the parent directory by going one level up
parent_dir = os.path.dirname(current_dir)
# Add the parent directory to sys.path
sys.path.append(parent_dir)
import util

def get_content_to_embed():
    conn = psycopg2.connect(util.CONNECTION_STRING)
    cur = conn.cursor()
    cur.callproc("get_content_to_embed" )
    ds = cur.fetchall()
    conn.commit()
    conn.close()    
    return ds

def get_connector(source):
    for conn in connectors.connectors:
        if conn.get_source() == source :
            return conn
    return None

from langchain.prompts.prompt import PromptTemplate
from langchain.chat_models import AzureChatOpenAI
from langchain import LLMChain

def parse_questions(llm_output):
     arr = llm_output.split('\n')
     return [ s[2:] if len(s) > 2 and s[1] == '.' else s for s in arr]

def save_questions(id_questions):
    conn = psycopg2.connect(util.CONNECTION_STRING)
    conn.autocommit = True

    idx = 0
    block = 200
    while idx + block < len(id_questions):
        slice = id_questions[idx:idx+block]
        cur = conn.cursor()
        args = ','.join(cur.mogrify("(%s,%s)", i).decode('utf-8')
                        for i in slice)
        cur.execute( "INSERT INTO content_question(content_id, question) VALUES " + (args))
        conn.commit()
        cur.close()
        idx = idx + block 

    if idx < len(id_questions) :
        slice = id_questions[idx:]
        cur = conn.cursor()
        args = ','.join(cur.mogrify("(%s,%s)", i).decode('utf-8')
                        for i in slice)
        cur.execute( "INSERT INTO content_question(content_id, question) VALUES " + (args))
        conn.commit()
        cur.close()

    conn.close()

def generate_questions(doc):

    QUESTION_PROMPT =  PromptTemplate.from_template("""
                        Give me three hypothetical queries that the below document can be used to answer. Return hypotetical queries only.
                        Document:
                        {doc}
                        """)

    llm = AzureChatOpenAI(temperature = 0.0, deployment_name= 'gpt35turbo-16k')
    chain = LLMChain(llm=llm, prompt = QUESTION_PROMPT)
    out =  chain.run({"doc": doc})
    return parse_questions(out)

def get_questions():
    conn = psycopg2.connect(util.CONNECTION_STRING)
    cur = conn.cursor()
    sql = """ 
        select content_id, question 
        from content_question cq 
        where not exists( select * from content_embedding ce where ce.content_id = cq.content_id )  
        limit 100
    """
    cur.execute( sql )
    ds = cur.fetchall()
    conn.close()
    return ds


def embed_content(ds_content):
    return  util.calculate_embedding( [ dq[1] for dq in ds_content ] )

def save_embeddings(source, ds_content, embeddings):
    conn = psycopg2.connect(util.CONNECTION_STRING)
    register_vector(conn)
    for i in range(len(ds_content)):
        cur = conn.cursor()    
        sql = f"insert into content_embedding(source,content_id,  content, embedding) values(%s,%s, %s, %s) "
        cur.execute( sql, (source, ds_content[i][0],ds_content[i][1],embeddings[i])  )
        conn.commit()
    conn.close()  


import numpy as np

def search_similar_content( query):
    emb = util.create_bge_embeddings([query])[0]
#    emb = np.array(emb[0])

    conn = psycopg2.connect(util.CONNECTION_STRING)
    cur = conn.cursor()
    register_vector(conn)
    sql = """
            select ic.id, ic.content_raw, ce.content, ce.embedding <=> %s score 
            from content_embedding ce join ingestion_content ic on ic.id = ce.content_id
            order by ce.embedding <=> %s
            limit 10
        """
    cur.execute(sql, (emb,emb) )
    ds = cur.fetchall()
    cur.close()
    conn.close()            
    return ds


def embed_all_quesions():
    ds_questions = get_questions()
    while len(ds_questions) > 0:
        questions_embeddings = embed_content(ds_questions)
        save_embeddings('Questions',ds_questions, questions_embeddings)
        ds_questions = get_questions()

def clear_config_max_embeddings():
    util.run_dml("delete from content_embedding where source='ConfigMax'")
    
def get_config_max_attributes():
    sql = """
        select distinct 'cfgmax-' || x.attribute_name || '-' || x.header as id,   x.attribute_name || ' of ' || x.header 
        from config_max_attribute_value x
        """
    return util.execute_sql(sql)

def embed_config_max_attributes():
    clear_config_max_embeddings()
    ds_attrs = get_config_max_attributes()
    embs = embed_content(ds_attrs)
    save_embeddings('ConfigMax',ds_attrs, embs)


if __name__ == '__main__':
    print('Embed Config Max Attributes')
    embed_config_max_attributes()

    print('Embed Content ')
    util.run_dml('refresh_config_max_content',is_proc=True)

    ds = get_content_to_embed()
    docs = []
    id_questions = []
    idx = 0
    for r in ds:
        id = r[0]
        source = r[1]
        meta =  r[2]
        content = r[3]

        if source != 'ConfigMax':
            continue

        if source == 'ConfigMax':
            questions = [content]
        else:
            connector = get_connector(source)
            questions = connector.generate_questions(meta, content)

        if questions != None and len(questions) == 0 :
            try:
                questions = generate_questions(content)
                exp_questions = []
                for q in questions:
                    expanded, exp_q = util.expand_acronyms(q)
                    if expanded:
                        exp_questions.append(exp_q)
                    else:
                        exp_questions.append(q)
                questions = exp_questions
            except Exception as e:
                pass

        if questions:
            id_questions.extend( [(id, q) for q in questions] )
            idx = idx + 1
            if idx % 1000 == 0:
                save_questions(id_questions)
                id_questions.clear()
            
    print(f" no of documents to index {idx}")
    save_questions(id_questions)
    print("start embedding")
    embed_all_quesions()

