## Loading Environment Variables
from typing import List, Tuple
from dotenv import load_dotenv
import os
load_dotenv()



from split_html import split_content
from split_html import extract_text_from_html



from lxml import etree

tree = etree.parse(r'data/all-english-public-kbs.xml')

documents = []

for article in tree.getroot():
    ar = {}
    for ch in article:
        tag = ch.tag[len("{http://www.force.com/2009/06/asyncapi/dataload}"):]
        ar[tag] = ''.join(ch.itertext()).strip()

    Document_Id__c = ar.get("Document_Id__c")
    PublishStatus = ar.get("PublishStatus")
    if PublishStatus == 'Draft':
        continue
    Id = ar.get("Id")
    Cause__c = ar.get("Cause__c")
    External_Article_URL__c = ar.get("External_Article_URL__c") 
    Heading__c = ar.get("Heading__c")
    Details__c = ar.get("Details__c")
    Impact_Risks__c = ar.get("Impact_Risks__c") 
    Purpose__c = ar.get("Purpose__c")
    Products__c = ar.get("Products__c")
    Keywords__c = ar.get("Keywords__c")
    Resolution__c = ar.get("Resolution__c")
    Solution__c = ar.get("Solution__c")
    Summary = ar.get("Summary")
    Symptoms__c = ar.get("Symptoms__c")
    Title = ar.get("Title")
    Workaround__c  = ar.get("Workaround__c")
    last_updated = ar.get("LastModifiedDate")
    if  last_updated:
        last_updated = last_updated.replace("T"," ").replace("Z","")

    section_names = ["Title","Summary", "Cause", "Detail","Symptoms", "Solution", "Workaround"]
    section_values = [Title,Summary, Cause__c, Details__c,Symptoms__c, Solution__c, Workaround__c]

    content = ""
    for i in range( len(section_names) ):
        if( not section_values[i]):
            continue    
        val = extract_text_from_html(section_values[i])
        content += f"{section_names[i]}:\n{val}\n" 

    if len(content.strip()) == 0:
        continue    

    documents.extend(split_content(content, {"document_id": Document_Id__c, "title": Title, "url": External_Article_URL__c, "last_updated": last_updated, "product": Products__c }))         


from util import save_data
save_data(documents,"Knowledge Base")
