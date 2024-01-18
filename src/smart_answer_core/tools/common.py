import requests
import json
import smart_answer_core.util as util

## Loading Environment Variables
from dotenv import load_dotenv
load_dotenv()
import os


from smart_answer_core.logger import logger


def execute_sql(sql,params: tuple = None, return_column_names = False, connection_string = None):
    return util.execute_sql(sql,params,return_column_names,connection_string)



   

