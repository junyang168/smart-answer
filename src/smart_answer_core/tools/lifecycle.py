from smart_answer_junyang168.base_tool import base_tool
from smart_answer_junyang168.tool_example import tool_example

import requests
import json

## Loading Environment Variables
from dotenv import load_dotenv
load_dotenv()
import os

import pandas as pd
import sqlalchemy
from smart_answer_junyang168.logger import logger


import smart_answer_junyang168.tools.common as util
from datetime import date


class LifeCycleTool(base_tool):
    name = "VMWare production version and life cycle dates"
    description = """ use this tool to understand support dates, general availability date and end of technical guidance date of VMware product versions
        The input to this tool should be  the VMWare product release. Use comma delimited string if question is about multiple releases.
    """
    
    question_context = """
        The question below is about support dates, general availability date and end of technical guidance date of VMware product versions
     """
    
    def __init__(self, connection_string) -> None:        
        super().__init__()
        self.connection_string = connection_string

    def get_few_shots(self):
        return [
            tool_example("When will vSphere 7 go out of support",'vSphere 7' ),
            tool_example("When will vSphere 7 be released",'vSphere 7' ),
            tool_example("What versions of vShpere are still supported",'vSphere'),
            tool_example("What versions of vShpere are released",'vSphere'),           
        ]
                        
    def get_answer_prompt_template(self,default_prompt, context):
        return  """ General availability Date determines when a specific version of VMWare product is released. The later the General Availability Date, the newer the release. """ + default_prompt 
               
    def retrieve(self, product_release, question):

        logger.info( self.name + " " + product_release)

        if not product_release:
            return "unable to get product"
        
        releases = product_release.split(',')

        ds_all = []

        first_product_name = None
        for product_release in releases:

            product_name, version = util.get_product_name_version(product_release)

            if not first_product_name:
                first_product_name = product_name

            if not product_name:
                product_name = first_product_name

            sql = f"""select product_release, version, "end of support","general availability","end of technical guidance" 
                from v_product_lifecycle lc where lc.product_name ilike \'{product_name}\' 
                """
            if version:
                sql += f" AND lc.version like '{version}%'"
            
            sql += " order by version desc"

            ds = util.execute_sql(sql, connection_string=self.connection_string) 
            if len(ds)  ==  0:
                self.return_direct = True
                return "unable to find life cycle date for " + product_release
            ds_all.extend(ds)
        
        response = util.print_result(ds_all, ["","version","End of Support Date","General Availability Date ","End of Technical Guidance Date"])


        return { "content": response, "prefix": f"Today is {date.today().strftime('%Y-%m-%d')}.\n", 
                "reference": [ {"title":"Product Life Cycle Matrix", "link":"https://lifecycle.vmware.com/"}] }
    
