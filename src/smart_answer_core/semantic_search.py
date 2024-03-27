
from dotenv import load_dotenv
load_dotenv()

import sys
import os 
from dotenv import load_dotenv

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)

import smart_answer_core.openapi_client as openapi_client

from smart_answer_core.openapi_client.models.query_result import QueryResult


def search(query:str) ->list[QueryResult]:
    configuration = openapi_client.Configuration(
        host = "http://localhost:8000"
    )

    # Enter a context with an instance of the API client
    with openapi_client.ApiClient(configuration) as api_client:
        # Create an instance of the API class
        api_instance = openapi_client.DefaultApi(api_client)
        return api_instance.search_get(query)


if __name__ == '__main__':
    res = search('what is BGE?')
    print(res)

