## Loading Environment Variables
import os
from simple_salesforce import Salesforce, format_soql
## Loading Environment Variables
from content_connector import content_connector
from dotenv import load_dotenv
load_dotenv()
import common

class SFConnector(content_connector):

  def get_source(self):
      return "KB"

  def generate_questions(self, meta, txt):
    if meta.get("language") == 'en_US':
        return []
    else:
       return None

  def get_collection_name(self):
      return "Knowledge Base"
    
  def getSF(self):
    # return Salesforce(domain='test', username='mzigarelli@vmware.com.gs.gsluat', password='3VqmHpJ5&iiD', security_token='9YyXVTaupZsWQETSM9jVoxR0')
    return Salesforce(instance_url = os.getenv('SF_INSTANCE_URL'), username = os.getenv('SF_USERNAME'), password = os.getenv('SF_PASSWORD'), security_token='')

  def get_content_list(self):
    sf = self.getSF()
    articles = sf.query_all("SELECT Id, LastModifiedDate FROM Knowledge__kav WHERE PublishStatus = 'Online' AND IsVisibleInPkb = true ORDER BY CreatedDate DESC")
    
    ingestion_content = [ [article["Id"], article["LastModifiedDate"]] for article in articles['records'] ]
      
    return ingestion_content

  def get_content(self,content_ids):
    if len(content_ids) == 0:
       return []
    
    sf = self.getSF()

    query = sf.query_all(format_soql("SELECT Id, Document_Id__c, Cause__c, External_Article_URL__c, Heading__c, Details__c, Impact_Risks__c, Purpose__c, Products__c, Keywords__c, Resolution__c, Solution__c, Summary, Symptoms__c, Title, Workaround__c, LastModifiedDate, Language FROM Knowledge__kav WHERE Id IN {}", content_ids))

    content = [] 
    for r in query['records']:
       d = dict(r)
       d.pop('attributes')
       content.append((r['Id'],d))
    return content
  
  def get_content_meta_text(self,content):
      # build metadata
      meta =  {
         "source":"KB", 
         "document_id": content["Document_Id__c"], 
         "title": content["Title"], 
         "url": content["External_Article_URL__c"], 
         "lastmod": content["LastModifiedDate"],  
         "language":  content["Language"],
         "product" : content.get("Products__c")
        } 
      
      section_names = [ "Summary", "Cause","Details","Symptoms", "Solution", "Workaround", "Resolution"]      
      section_values = [ content["Summary"], content["Cause__c"], content["Details__c"],content["Symptoms__c"], content["Solution__c"], content["Workaround__c"], content["Resolution__c"]]

      md_txt = "# " + content["Title"] + " \n"
      md_txt += f' Products: {content.get("Products__c")}\n'

      for i, sec_name in enumerate(section_names):
          if( not section_values[i]):
              continue    
          md_txt  +=  f"## {sec_name}\n" + common.convert_html_to_md(section_values[i]) + '\n'
      
      return meta, md_txt

     
      
