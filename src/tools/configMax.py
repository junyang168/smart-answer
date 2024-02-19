from smart_answer_core.base_tool import base_tool
from smart_answer_core.tool_example import tool_example


## Loading Environment Variables
from dotenv import load_dotenv
load_dotenv()
import os
from smart_answer_core.logger import logger


import pandas as pd
import psycopg2
from psycopg2.sql import Identifier, SQL

from pgvector.psycopg2 import register_vector
import smart_answer_core.util as util




import numpy as np


class ConfigMaxTool(base_tool):
    name = "VMware Product Configuration Limits"
    description = """use this tool to get the recommended maximums or limits for VMware product configurations or settings. 
        The input to this tool should be a comma separated list of string of length three, representing VMWare product release and the metrics(e.g. CPU per VM) for the limit.
        Here are some sample quesions:
        How much RAM can I run on a VM?
        Can I run 40GB RAM on a VM?
        """
        

    def get_few_shots(self):
        return [
            tool_example("what is limit of vCPU per RDS host for Horizon 2306",'Horizon 2306, limit of vCPU per RDS host' )
        ]


    
    threshold = 0.2

    def __init__(self, connection_string = None) -> None:        
        super().__init__()
        if  connection_string:
            self.connection_string = connection_string
        else:
            self.connection_string =  os.environ.get("CONNECTION_STRING") 
     

    def __get_config_max(self, metric, product_release = None):

        attr_embedding = util.calculate_embedding( metric )

        if product_release:    
            product_name, version =  util.get_product_name_version(product_release)
            if product_name:
                product_filter_value = f"%{product_name}%"
            else:
                product_filter_value = "%"

            if version:
                ver_filter_value = f"%{version}%"
            else:
                ver_filter_value = "%"



        conn = psycopg2.connect(self.connection_string)
        register_vector(conn)
        cur = conn.cursor()

        if product_release:    
            sql = f"""
                SELECT  pr2.release_name as release, x.header, x.attribute_name, x.value,  emb.embedding <=> %s score, pr.product_name, x.category_id 
                FROM 
                    content_embedding emb join config_max_attribute_value x on emb.source='ConfigMax' and  ('cfgmax-' || x.attribute_name || '-' || x.header = emb.content_id)
                    join v_product_configmax pr on pr.pid = x.pid 
                    join product_release pr2 on pr2.id = x.rid and pr2.pid = x.pid
                where 
                emb.embedding <=> %s <= {self.threshold}   
                and ( pr.product_name ilike %s or pr2.release_name ilike %s )
                and pr2.release_name ilike %s                 
                order by emb.embedding <=> %s 
                LIMIT 10
            """
            params = (attr_embedding,attr_embedding,product_filter_value,product_filter_value, ver_filter_value, attr_embedding)
        else:
            sql = f"""
                SELECT distinct pr.release_name as release, x.header, x.attribute_name, x.value,  emb.embedding <=> %s score, pr2.release_name as product_name, x.category_id 
                FROM 
                    content_embedding emb join config_max_attribute_value x on emb.source='ConfigMax' and  ('cfgmax-' || x.attribute_name || '-' || x.header = emb.content_id)
                    join product_release pr on pr.pid = x.pid and x.rid = pr.id 
                    join product_release pr2 on pr2.pid = pr.pid and pr2.app='ConfigMax'
                where 
                emb.embedding <=> %s <= {self.threshold}   
                order by emb.embedding <=> %s 
                LIMIT 10
            """
            params = (attr_embedding,attr_embedding,attr_embedding)


        cur.execute(sql, params )
        ds = cur.fetchall()
        conn.close()

        return ds        

    def retrieve(self, params : str, question ):

        logger.info( self.name + " " + params)

        params = params.split(',')
        if len(params) >= 2:
            product_release = params[0] 
            metric  = ' '.join( params[1:] )
        else:
            metric = params[0]
            product_release = None



        ds = self.__get_config_max(metric, product_release)
        if not ds:
            ds = self.__get_config_max(question)

        config_max_params = set()
        maxScore = 0.0
        if ds:
            maxScore = 1 - ds[0][4]
            txt = f"The reletated configuration limits for {product_release} are:\n"            
            for v in ds:
                # release - 0, product_name = 5, category_id = 6
                config_max_params.add((v[5],v[0],v[6]))
                txt += f"- {v[2]} of {v[1]} for {v[0]} is {v[3]} \n"
            ref_array = [ {"title": f"Configuration Maximums for {t[1]}", "link":f"https://configmax.esp.vmware.com/guest?vmwareproduct={t[0]}&release={t[1]}&categories={t[2]})"} for t in config_max_params  ]          
            return { "content":txt, "reference": ref_array[:3] , "maxScore" : maxScore}
        else:
            return None


