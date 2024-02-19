## Loading Environment Variables
import os
from content_connector import content_connector
import json
import psycopg2
import pandas as pd
import sqlalchemy
from collections.abc import Iterable  
import requests



CONNECTION_STRING =  os.environ.get("CONNECTION_STRING") 


class ProductConnector(content_connector):  
   
   def get_products(self):
        url = 'https://apigw.vmware.com/v1/m4/api/SIM-Internal/products?interoptype=product'
        custom_header = {
            "Content-Type": "application/json",
            "X-Auth-Key":"N31mVcQkL?Q]GSe[Tve0Wl8b[i2_vU:ClohDvU7Ex;GCu4=hxa=q>3B<aMEZRwmT"
        }

        r = requests.get(url,headers = custom_header)
        return  json.loads(r.text)


   def get_source(self):
      return "Product Release"


   def get_collection_name(self):
      return "Product Release"
  
   site_content = [] 

   def get_content_list(self):
    products = self.get_products()
    product_releases = []
    for product in products:
       name = product["name"]
       if name.lower().startswith("vmware"):
          name = name[len("VMware"):]
       product_releases.append( {"id": product["id"], "name": name} )
#       for release in product["releases"]:
#          product_releases.append({"id": release["id"], "name": f"{product['name']} {release['versionBossd']}"})
    self.site_content = product_releases

    return [ [r["id"], '2023-08-09'] for r in self.site_content ]

   def get_content(self,content_ids):
    return self.site_content 
         


   def get_content_meta_text(self,content): 
      return {"url": content["id"]}, content["name"]
  

if __name__ == "__main__":
    from run_job import run_sync_job
    doc_conn = ProductConnector()
    run_sync_job(doc_conn, True)    