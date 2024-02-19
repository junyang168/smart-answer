from dotenv import load_dotenv
from util import save_data
import os
load_dotenv()
import psycopg2
import json
from split_html import split_content


CONNECTION_STRING =  os.environ.get("CONNECTION_STRING") 


def get_text(fld_arr, obj ):
    text = ""
    for fld in fld_arr:
        if( fld == "Features"):
            fld_val = " ".join( [ f["features"] for f in obj[fld]]  )
        else:
            fld_val = obj[fld]
        text = f"{text} {fld_val}"
    return text

def load_data(collection_name):
    conn = psycopg2.connect(CONNECTION_STRING)
    cur = conn.cursor()
    sql = "select category, url, content from compatibility_matrix"
    cur.execute(sql )
    data = cur.fetchall()
    conn.close()  

    fields_mappings = { 
         "server" : {"Server":["Partner Name","Model"], "CPU":["CPU Series"], "Supported Release":["Release"],"Server Features":["Features"], "BIOS" : ["BIOS"] },
         "io" : {"Device":["Brand Name","Model"] , "Supported Release":["Release"],"Server Features":["Features"] },
        } 

    documents = []
    for row in data:
        txt = ""
        category = row[0]
        obj = json.loads(row[2])
        fields = fields_mappings[category]
        for k in fields:
            txt += f"{k}: {get_text(fields[k], obj)}"
        documents.extend(split_content(txt, {"url":row[1],"title":collection_name}))
    save_data(documents, collection_name)



load_data("Compatibility")
