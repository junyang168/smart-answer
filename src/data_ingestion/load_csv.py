from dotenv import load_dotenv
from util import save_data
import os
load_dotenv()

from langchain.document_loaders import CSVLoader

def load_data(data_file, url, collection_name):

    loader = CSVLoader(file_path=data_file)
    documents = loader.load()
    for doc in documents:
        doc.metadata["url"] = url
        doc.metadata["title"] = collection_name
    save_data(documents, collection_name)



#load_data('data/Lifecycle2.csv', "https://lifecycle.vmware.com/#/", "Life Cycle")
load_data('data/vSphere 8.0 Configuration_Maximums.csv', "https://configmax.esp.vmware.com/guest?vmwareproduct=vSphere&release=vSphere%208.0&categories=1-0",  "Config Matrix")
#load_data('data/vi_cpu_guide-partial.csv', "https://www.vmware.com/resources/compatibility/search.php",  "Compatibility")
#load_data('data/vi_server_guide-partial.csv', "https://www.vmware.com/resources/compatibility/search.php",  "Compatibility")
load_data('data/vcenter-Interoperability.csv', "https://interopmatrix.vmware.com/Interoperability",  "Compatibility")
load_data('data/VMC on AWS Interoperability.csv', "https://interopmatrix.vmware.com/Interoperability",  "Compatibility")


