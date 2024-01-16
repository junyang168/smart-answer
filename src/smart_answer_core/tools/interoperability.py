from smart_answer_junyang168.base_tool import base_tool
from smart_answer_junyang168.tool_example import tool_example

import requests
import json

## Loading Environment Variables
from dotenv import load_dotenv
load_dotenv()
import os

from smart_answer_junyang168.logger import logger


import pandas as pd
import smart_answer_junyang168.tools.common as util
#import product_embedding as util


class InterOperabilityTool(base_tool):
    name = "VMWare Product Compatibility"
    description = """
        use this tool to understand compatibiilty or interoperability between VMWare products.  
        The input to this tool should be a comma separated list of string of length two, representing the two product releases you wanto understand compatibility with.
        For example, 
            1. `Aria 1.0,ESXi 5.0` would be the input if you wanted to know whether VMware Aria 1.0  can run on VMware ESXi 5.0. 
            2. `Aria,ESXi 5.0` would be the input if you wanted to know the versions of Aria that support VMware ESXi 5.0. 
    """
  
    def __init__(self, connection_string) -> None:        
        super().__init__()
        self.connection_string = connection_string

    def get_few_shots(self,):
        return [
            tool_example("Is vSAN  compatible with vCenter?",'vSAN, vCenter' )
        ]


    def _get_product_id(self, product_release):
        product_name, version =  util.get_product_name_version(product_release)

        sql = """select product_name, pid 
        from v_product_interoperability 
        where product_name iLike %s or Levenshtein(product_name, %s ) < 3
        order by Levenshtein(product_name, %s ) asc
        """
        ds = util.execute_sql(sql,(product_name, product_name, product_name), connection_string=self.connection_string)

        if len(ds) >0:
            r = ds[0]
            return (r[1],r[0], version )
        else:
            return (None,product_name,version)

    def _get_similar_products(self, product_release):
        product_name, version =  util.get_product_name_version(product_release)

        sql = f"select product_name from v_product_interoperability where product_name iLike '%{product_name}%'"

        ds = util.execute_sql(sql, connection_string=self.connection_string)

        if len(ds) > 0 :
            return 'There are multiple products that match your input, please choose: '  + '\n'.join([ r[0] for r in ds ] )
        else:
            return None
                
    def _call_interoperability_api(self, url, json_body):
        custom_header = {
            "Content-Type": "application/json",
            "X-Auth-Key":"N31mVcQkL?Q]GSe[Tve0Wl8b[i2_vU:ClohDvU7Ex;GCu4=hxa=q>3B<aMEZRwmT"
        }
        r = requests.post(url,headers = custom_header, json=json_body)
        if r.text and r.text.lower().startswith('no result'):
            return None
        else:
            return  json.loads(r.text)
            
    def __get_interoperability(self, p1, p2):
        json_body = {"columns":[{"product":p1,"releases":[]}],
                     "rows":[{"product":p2,"releases":[]}],
                     "isCollection":"false","isHidePatch":"false","isHideGenSupported":"false","isHideTechSupported":"false",
                     "isHideCompatible":"false","isHideIncompatible":"false","isHideNTCompatible":"false","isHideNotSupported":"true","col":"8,","row":"0,"
                     }
        url = 'https://apigw.vmware.com/v1/m4/api/SIM-Internal/products/interoperabilityMatrix'
        return self._call_interoperability_api(url, json_body)

    def _get_support_status(self, status):
        return  status == 1 or status == 3 
    

    def _match_version(self, v_data :str, v_user : str):
        if not v_user:
            return True
        v_data = v_data.replace(' ','').lower()
        v_user = v_user.replace(' ','').lower()
        return v_data.startswith(v_user)
    


    def _get_link(self,pid1, pid2) :
        return [ { "title":"VMWare Interoperability Matrix", "link":f"https://interopmatrix.vmware.com/Interoperability?col={pid1}&row={pid2}&isHidePatch=false&isHideGenSupported=false&isHideTechSupported=false&isHideCompatible=false&isHideNTCompatible=false&isHideIncompatible=false&isHideNotSupported=true&isCollection=false" } ]

    def _get_not_compatible_message(self, p1, p2, pid1, pid2):
        return {"content": f"{p1} and {p2} are not compatible. please refer to the VMWare interoperability matrix for more detail ", "reference": self._get_link(pid1,pid2) }
    
    def _get_product_and_version(self, products):
        arr_product = products.split(',')
        pid2, p2, r2 = self._get_product_id( arr_product[0] )
        pid1, p1, r1 = self._get_product_id(arr_product[1])
        response = None
        txt = ''
        if not pid1 or not pid2:
            if not pid2:
                sim_products = self._get_similar_products( p2 )
                if not sim_products:
                    response =  { "content": "Product Not Found" }
                else:
                    txt +=  sim_products + '\n'
            if not pid1:
                sim_products = self._get_similar_products( p1 )
                if not sim_products:
                    response =  { "content": "Product Not Found" }
                else:
                    txt += sim_products + '\n'
            if txt:
                response = { "content": txt }
        return { "p1_info": (pid1,p1, r1, arr_product[0]), "p2_info":(pid2,p2, r2, arr_product[1]), "response": response}


    def retrieve(self, products : str,question : str):

        logger.info( self.name + " " + products)

        result = self._get_product_and_version(products)

        if result.get('response'):
            return result.get('response')
        
        
        p1_info = result.get("p1_info")
        p2_info = result.get("p2_info")
        pid1 = p1_info[0]
        pid2 = p2_info[0]
        p1 = p1_info[1]
        p2 = p2_info[1]
        r1 = p1_info[2]
        r2 = p2_info[2]

        comp_data = self.__get_interoperability(pid1,pid2)

        if not comp_data:
            return self._get_not_compatible_message(p1_info[3],p2_info[3],pid1, pid2)

        
        data = None
        for k in comp_data:
            data = comp_data[k]
        column_names = []
        arr = {}
        column_names = ["row name"]
        for col_idx in range( len(data) ):
            col = data[col_idx]
            col_name = col['version']
            column_names.append( col_name )
            if col_idx == 0: 
                arr["row name"] = [ r['version'] for r in col["rowProdReleaseMap"]['0']] 
            arr[col_name] =  [ r["status"] for r in col["rowProdReleaseMap"]['0']] 
        hasData = False
        if len(arr) > 0:            
            df = pd.DataFrame(arr,columns=column_names)
            for idx in reversed( range( len(column_names)) ):
                if idx == 0:
                    break
                col_name = column_names[idx]
                if not self._match_version(col_name, r1 ):
                    column_names.pop(idx)
                    df.drop(col_name, axis=1,inplace=True)
            if len(column_names) <= 1:
                return self._get_not_compatible_message(p1_info[3],p2_info[3],pid1,pid2)            
            
            txt_arr_comp = []
            txt_arr_not_comp = []

            com_p1_ver = set() 
            com_p2_ver = set() 


            for r in df.itertuples():
                ver_row = r[1]
                if self._match_version(ver_row, r2 ):
                    hasData = True
                    rel_supported = [ column_names[ i-1 ] for i in range(2, len(r)) if self._get_support_status(r[i])]
                    if len(rel_supported) > 0:
                        com_p2_ver.add(ver_row)
                        com_p1_ver.update(rel_supported)
                        txt_arr_comp.append( f"{p2} version {ver_row} supports {p1} version {','.join(rel_supported) }" )
                    rel_not_supported = [ column_names[ i-1 ] for i in range(2, len(r)) if not self._get_support_status(r[i])]
                    if len(rel_not_supported) > 0:
                        txt_arr_not_comp.append( f"{p2} version {ver_row} does not support {p1} version {','.join(rel_not_supported) } " )
            if not hasData:
                self.return_direct = True
                return self._get_not_compatible_message(p1_info[3],p2_info[3],pid1,pid2)
            
            if len(txt_arr_comp) > 0:
                txt =  f"{p1} {','.join(com_p1_ver)} are compatible with {p2} {','.join(com_p2_ver)} "  
                return  { "content":txt, "reference": self._get_link(pid1,pid2) } 
            else: 
                txt = self._get_not_compatible_message(p1_info[3],p2_info[3],pid1,pid2)
        else:
            txt = self._get_not_compatible_message(p1_info[3],p2_info[3],pid1,pid2)

        return txt

            



if __name__ == '__main__':
#    refresh()
    pass
