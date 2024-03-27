from dotenv import load_dotenv
load_dotenv()
import os
from smart_answer_core.LLMWrapper import LLMConfig

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


from smart_answer_core.LLMWrapper import LLMWrapper

def ask_llm( llm_cfg : LLMConfig,  prompt_template : str, format = None, sid = None,  **kwargs ):
    llm = LLMWrapper(llm_cfg)
    return llm.askLLM(prompt_template, format, sid, kwargs)



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
