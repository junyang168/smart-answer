import requests
import json
import smart_answer_junyang168.util as util

## Loading Environment Variables
from dotenv import load_dotenv
load_dotenv()
import os


from smart_answer_junyang168.logger import logger
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


def ask_gpt(question):
    response = openai.ChatCompletion.create(
            engine="gpt35turbo-16k",
            messages=[ {"role":"user","content":question} ]
    )
    return response['choices'][0]['message']['content']


def execute_sql(sql,params: tuple = None, return_column_names = False, connection_string = None):
    return util.execute_sql(sql,params,return_column_names,connection_string)

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


   

