## Loading Environment Variables
import os
from content_connector import content_connector
import json
import psycopg2
import pandas as pd
import sqlalchemy
from collections.abc import Iterable  



CONNECTION_STRING =  os.environ.get("CONNECTION_STRING") 


class CompatibilityConnector(content_connector):  
   category = "server"

   def __init__(self, category):
      self.category = category
  
   def get_source(self):
      return "Compatibility"

   def get_collection_name(self):
      return "Compatibility"
  
   site_content = {}  

   def get_content_list(self):
    conn = psycopg2.connect(CONNECTION_STRING)
    cur = conn.cursor()
    sql = f"select url, content, last_mod from ingestion_site_content where action ='Done' and category= '{self.category}' "
    cur.execute(sql )
    self.site_content = cur.fetchall()
    conn.close()   
    return [ [r[0], r[2]] for r in self.site_content]

   def get_content(self,content_ids):
    return [ json.loads(r[1]) for r in self.site_content] 
         
   def __add_item(self, dest, key, val ):
      if not val or len(val) == 0:
         return
      if not key in dest:
         dest[key] = [val]
      else:
         vals = val.split(",")
         for v in vals:
            if not any([ x == v for x in dest[key] ]  ):
               dest[key].append(v)

   def __compress_obj(self, dest, key, src ):      
      if isinstance(src, list):
         for item in src:
            self.__compress_obj(dest, key, item)
      elif isinstance(src,dict):         
         for k in src:
            ck = f"{key}-{k}" if key else k
            self.__compress_obj(dest, ck, src[k])
      else:
         self.__add_item(dest, key, src)   
   
   def __convert_to_text(self, obj):
      txt = ""
      for k in obj:
         v = obj[k]
         if isinstance( v, list):
            v =  ",".join(v) 
         txt = f"{txt} {k}:{v}\n" 
      return txt

   fields_mappings = { 
         "server" : {"Server":["Partner Name","Model"] },
         "io" : {"Manufacturer":["Brand Name","Model"]  },
        } 

   def get_content_meta_text(self,content): 
      txt_obj = {}           
      flds = self.fields_mappings[self.category]
      index_flds = {}
      for key in flds :
         index_flds[key] = " ".join( [ content[k] for k in flds[key]])

      index_flds["Supported Releases"] = content["Supported Releases"]
      index_flds["Features"] = [ rel["Features"] for rel in content["Compatible Releases"] ]


      self.__compress_obj( txt_obj , None, index_flds )

      txt = self.__convert_to_text(txt_obj) 

      return {"url": content["Model_link"] , "title" : "Compatibility " + self.category}, txt
  
