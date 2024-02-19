## Loading Environment Variables
import os
import requests
## Loading Environment Variables
import html
from itertools import islice
from bs4 import BeautifulSoup
import os
from content_connector import content_connector
from lxml import etree

import common

class SitemapConnector(content_connector):
  
  HEADERS = {'User-Agent': 'Mozilla/5.0 (iPad; CPU OS 12_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148'}

  def get_source(self):
      return "VMWare Docs"

  def generate_questions(self, meta, txt):
     return []

  def get_collection_name(self):
      return "Knowledge Base"


  def __get_sitemap(self):
    sitemaps =[]
    sitemap_url = "https://docs.vmware.com/sitemap.xml"
    res = requests.get(sitemap_url,headers=self.HEADERS)     
    if (res.status_code == 200):
        bs = BeautifulSoup(res.text, "lxml") 
        sms = bs.find_all('loc')
        for sm in sms:
            res = requests.get(sm.get_text(),headers=self.HEADERS) 
            if res.status_code != 200:
              continue
            sitemaps.append(res.content)
    return sitemaps


                


  def get_content_list(self):
    sitemaps = self.__get_sitemap()
    if len(sitemaps) == 0:
       return None
    
    productPartialUrls = ["Foundation/5", "vSphere/8","vSphere/7", "NSX/4","Horizon/2306","Aria-Automation"]
    ingestion_content = []
    for sm_content in sitemaps:
      tree = etree.fromstring(sm_content)
      sm = {}
      for ch in tree:
          for c in ch:
            tag = c.tag[len("{http://www.sitemaps.org/schemas/sitemap/0.9}"):]
            sm[tag] = ''.join(c.itertext()).strip()      
          url = sm['loc']
          if not url.endswith(".html") or url.find("/en/") < 0:
            continue
          if any(x in url for x in productPartialUrls):      
            lastmod = sm['lastmod']
            ingestion_content.append([url, lastmod])
    return ingestion_content

  def get_content(self,content_ids):
    content = []
    for url in content_ids:
        try:
          res = requests.get(url,headers=self.HEADERS)
          if (res.status_code == 200):
            content.append( (url,res.text) )
        except Exception as e:
          pass
    return content


  def get_content_meta_text(self,html_content):
      if not html_content:
         return None, None
      docs = []
      soup = BeautifulSoup(html.unescape(html_content), "html.parser")
      # parse title and main tags
      # build metadata
      meta = {"source": "VMWare Docs"}
      # get title
      title = None
      if hasattr(soup.head,"title"):
        title = soup.head.title
      
      content = ""

      if title is not None:
        title = title.get_text()
        meta["title"] = title
        content = content + "\n" + title
      # get url
      url = soup.find('meta', attrs={'property': 'og:url'})
      
      if url:
        url = url["content"]
        meta["url"] = url
      # get product
      product = soup.find('meta', attrs={'name': 'product'})
      
      if product:
        product = product["content"]
        meta["product"] = product
        content = content + " for " + product + "\n"
        content += "====================\n"
      #  get guid
      guid = soup.find('meta', attrs={'name': 'guid'})
      
      if guid:
        guid = guid["content"]
        meta["document_id"] = guid
      
      
      # get content
      contentNode = soup.find('main')
      if not contentNode:
        contentNode = soup.find('div', class_='article-wrapper')
      if contentNode:
        content +=  common.convert_html_to_md( str(contentNode) )

      return meta, content 
  

