from langchain.tools import BaseTool
import requests
import json

## Loading Environment Variables
from dotenv import load_dotenv
load_dotenv()
import os


import pandas as pd
import psycopg2
from psycopg2.sql import Identifier, SQL

from pgvector.psycopg2 import register_vector
from operator import itemgetter


CONNECTION_STRING =  os.environ.get("CONNECTION_STRING") 

import numpy as np
from numpy.linalg import norm


from openai.embeddings_utils import cosine_similarity
from itertools import groupby

import util

def __call_api(url, json_body = None):     
    if json_body is None:   
        r = requests.get(url)
    else:
        r = requests.post(url,json=json_body)
    if len(r.text) > 0:
        return json.loads(r.text)


def __save_releses(data):
    conn = psycopg2.connect(CONNECTION_STRING)

    delquery = "delete from product_release where app='Config Max'"
    cur = conn.cursor()
    cur.execute(delquery)
    conn.commit()
    cur.close()

    query = SQL("insert into {table}( {pid},{rid}, {release_name},{app})values(%s,%s,%s,%s)").format(
        table = Identifier('product_release'),
        pid = Identifier('pid'),
        rid = Identifier('id'),
        release_name = Identifier('release_name'),
        app = Identifier('app')
    )

    for r in data:
        cur = conn.cursor()
        cur.execute( query, (str(r[0]), str(r[1]), r[2].strip(),'Config Max')  )
        conn.commit()
        cur.close()
    conn.close()      

def __get_releases(pid):
    releases = __call_api(f"https://configmax-service.esp.vmware.com/limits/menutree/v1/vmwareproducts/{pid}/releases?hasconfigmaxset=true&ispublished=true")

    data = []
    if releases:
        for rel in releases:
            data.append( (pid, rel["id"], rel["rmVmwPrdRelVersion"]) )
    return data

def __get_product_ids():
    conn = psycopg2.connect(CONNECTION_STRING)
    query = "select distinct pid from v_product_configmax"
    cur = conn.cursor()
    cur.execute(query )
    ds = cur.fetchall()
    return [ r[0] for r in ds ]


def __get_release_category(pid, rid):
    categories = __call_api(f"https://configmax-service.esp.vmware.com/limits/menutree/v1/vmwareproducts/{pid}/releases/{rid}/categories?ispublished=true")
    cat_reqs = {"prodId":pid, "relId": rid}
    cat_reqs["categories"] = [ {"categoryId": c["id"], "subCategoryId": 0 } for c in categories]
    return cat_reqs

def __get_config_limits( pid, rid, cat_req):
    limits = __call_api(f"https://configmax-service.esp.vmware.com/limits/managelimits/v1/vmwareproducts/{pid}/releases/{rid}/categories/attributes?showall=true&isTotalCount=false",json_body=cat_req)
    limit_matrix = []        
    for l in limits:
        categoryName = l['name'][ :l['name'].find(':') ] 
        categoryId  = l['categoryId']
        limit_matrix.extend( [ {"header":attr["headername"], "attr":attr["keyname"], "val":attr["attrValue"], "categoryName":categoryName,"categoryId": categoryId}  for attr in l["configs"] ] )
#    limit_matrix = sorted(limit_matrix, key=itemgetter("attr"))
#    cat_limit_matrix = sorted(limit_matrix, key=itemgetter("cat"))
#    result = {}

#    for attr_name, group in groupby(limit_matrix, key=lambda r: r["attr"]):
#        result[attr_name] = list(group) 

#    for cat, group in groupby(cat_limit_matrix, key=lambda r: r["cat"]):
#        result[cat] = list(group) 

    return limit_matrix

def __save_config_limit( pid, rid, config_limit):

    conn = psycopg2.connect(CONNECTION_STRING)


    delquery = "delete from config_max_attribute_value where pid =%s and rid = %s"
    cur = conn.cursor()
    cur.execute(delquery,(str(pid), str(rid)))
    conn.commit()
    cur.close()

    query = SQL("insert into {table}( {pid},{rid}, {category_id}, {category_name}, {attribute_name},{header},{value})values(%s,%s,%s,%s,%s,%s,%s)").format(
        table = Identifier('config_max_attribute_value'),
        pid = Identifier('pid'),
        rid = Identifier('rid'),
        category_name = Identifier('category_name'),
        category_id = Identifier('category_id'),
        attribute_name = Identifier('attribute_name'),
        header = Identifier('header'),
        value = Identifier('value')
    )

    for cfg in config_limit:
        cur = conn.cursor()
        cur.execute( query, (str(pid), str(rid), str(cfg['categoryId']),  cfg['categoryName'].strip(),cfg['attr'], cfg['header'], cfg['val'])  )
        conn.commit()
        cur.close()
    conn.close()  

def __get_all_releases():
    conn = psycopg2.connect(CONNECTION_STRING)
    query = """
        select pid, id
        from product_release where app='Config Max'
    """

    cur = conn.cursor()
    cur.execute(query )
    ds = cur.fetchall()
    return ds

def __save_attribute_embedding(attr_name, attr_type, embedding):
    conn = psycopg2.connect(CONNECTION_STRING)
    register_vector(conn)
    cur = conn.cursor()
    embedding = np.array(embedding)
    sql = "insert into product_attribute_embedding(attribute_name, attribute_type, embedding) values(%s, %s, %s) "
    cur.execute( sql, ( attr_name,attr_type, embedding,)  )
    conn.commit()
    conn.close()  


def __create_embeddings():
    conn = psycopg2.connect(CONNECTION_STRING)
    query = """
        select *
        from (
            select distinct attribute_name, 'attribute' as type
            from config_max_attribute_value cl join product_release pr on cl.pid  = pr.pid and pr.app='ConfigMax'
            where value is not null
            union 
            select distinct header, 'header' as type
            from config_max_attribute_value cl join product_release pr on cl.pid  = pr.pid and pr.app='ConfigMax'
            where value is not null
        ) a
        where not exists(
        select * from product_attribute_embedding pae where pae.attribute_name  = a.attribute_name
        )
    """

    query = """
        select *
        from (
            select distinct attribute_name || ' of ' || header, 'attribute_header' 
            from config_max_attribute_value cl 
        ) a
    """

    cur = conn.cursor()
    cur.execute(query )
    ds = cur.fetchall()
    for r in ds:
        attr_name = r[0]
        attr_type = r[1]
        embedding = util.calculate_embedding(attr_name)
        __save_attribute_embedding(attr_name,attr_type, embedding )



def load_config_max(step ):

    if step <= 1:
        pids = __get_product_ids()
        releases = []
        for pid in pids:
            releases += __get_releases(pid)
        __save_releses(releases)

    if step <= 2:
        releases = __get_all_releases()

        for pid, rid in releases:    
            cat_reqs = __get_release_category(pid, rid)
            cfg_limits = __get_config_limits(pid, rid, cat_reqs)
            __save_config_limit(pid,rid, cfg_limits)

    if step <= 3:
        __create_embeddings()

load_config_max(3)