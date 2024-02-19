import html
from bs4 import BeautifulSoup
from langchain.text_splitter import RecursiveCharacterTextSplitter

def extract_text_from_html(encodedHtml):
    decodedHtml = html.unescape(encodedHtml)
    soup = BeautifulSoup(decodedHtml)
    txt = soup.get_text()
    lines = [line.strip() for line in txt.splitlines()]
    return '\n'.join(line for line in lines if line)

def split_content(content, metadata):
    sp = RecursiveCharacterTextSplitter(chunk_size = 2000, chunk_overlap=200)
    docs = sp.create_documents([content],[metadata]  )
#    for d in docs:
#        d.page_content = "passage: " + d.page_content
    return docs



