## Loading Environment Variables
import html
from itertools import islice
from bs4 import BeautifulSoup
from langchain.text_splitter import CharacterTextSplitter
import os

from lxml import etree


count = 0
directory = 'html'

docs = []
#for filename in os.listdir(directory):
for root,d_names,files in os.walk('html'): 
  for filename in files:  
    if not filename.endswith(".html"):
      continue
    f = os.path.join(root, filename)
    fo = open(f, 'r')
        
    try:
      print(f)
      soup = BeautifulSoup(html.unescape(fo.read()), "html.parser")
      # parse title and main tags
      # build metadata
      source = {"source": "VMWare Docs"}
      # get title
      title = None
      if hasattr(soup.head,"title"):
        title = soup.head.title
      
      content = ""

      if title is not None:
        title = title.get_text()
        source["title"] = title
        content = content + "\n" + title
      # get url
      url = soup.find('meta', attrs={'property': 'og:url'})
      
      if url:
        url = url["content"]
        source["url"] = url
      # get product
      product = soup.find('meta', attrs={'name': 'product'})
      
      if product:
        product = product["content"]
        source["product"] = product
        content = content + "\nVMWare Product:" + product
      #  get guid
      guid = soup.find('meta', attrs={'name': 'guid'})
      
      if guid:
        guid = guid["content"]
        source["guid"] = guid
      # get content
      contentNode = soup.find('main')
      if not contentNode:
        contentNode = soup.find('div', class_='article-wrapper')
      if contentNode:
        content = content + "\n" + contentNode.get_text()
        # clean the main tag's content
        lines = [line.strip() for line in content.splitlines()]
        clean_text = '\n'.join(line for line in lines if line)
        # chunk the main tag's content
        sp = CharacterTextSplitter(chunk_size = 2000, chunk_overlap=200, separator='\n')
        # add to the document list
        docs.extend(sp.create_documents([clean_text],[source]))
      count = count + 1
    except Exception as e:
      print(repr(e))

print(f"total files:{count}")
from util import save_data
save_data(docs,"Knowledge Base")

