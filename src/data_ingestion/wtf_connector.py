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

import urllib.parse
import requests

import json



class WTFConnector(content_connector):
  def get_source(self):
      return "WTF"
  
  def generate_questions(self):
     return False
  

  def get_collection_name(self):
      return "Knowledge Base"

  def get_content_list(self):
    res = requests.get('https://wtf.eng.vmware.com/api/def/find/')
    if res.status_code == 200:
       all_words = json.loads(res.text)
       if all_words.get( "status" ) == 'worked':
          return  [ [ 'https://wtf.eng.vmware.com/api/def/get/?word=' + urllib.parse.quote_plus(w) , '2023-09-20' ]  for w in all_words.get("data") ]


  def extract_word_definition(self, defStr):
    import re
    pattern = r"__([^_]*)__"
    match = re.search(pattern, defStr)
    if match:
      return match.group(1)  
    pattern = r"__([^_]*_[^_]*)__"
    match = re.search(pattern, defStr)
    if match:
       return match.group(1)
    return defStr

  def get_content(self,urls):
    content_raw = []
    for url in urls:
      res = requests.get(url)
      if (res.status_code == 200):
          word_def = json.loads(res.text)
          word = word_def.get("word")
          defs =  word_def.get("defs")
          if defs:
              content_raw.append( [ { "word": word, "definition": self.extract_word_definition(d["def"]), "content":d["def"], "lastmod": d["last_touch"] }  for d in defs ] )
    return content_raw


  def get_content_meta_text(self,meta, content):
      meta['title'] = content[0].get('word').upper() 
      meta['url'] =  "https://wtf.eng.vmware.com/#" + content[0].get('word')
      return meta['title']
  
  def get_splitter(self):
     from langchain.text_splitter import MarkdownTextSplitter
     return MarkdownTextSplitter()

  def generate_questions(self, meta, txt):
     return ["What is " +  txt]



