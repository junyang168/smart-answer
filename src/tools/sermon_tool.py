import sys
import os
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)




from smart_answer_core.base_tool import base_tool

from smart_answer_core.base_tool import RetrievalResult
from smart_answer_core.base_tool import Reference
from typing import List 


## Loading Environment Variables
from dotenv import load_dotenv
load_dotenv()
import os
from smart_answer_core.logger import logger
import glob
import os
import requests
import xml.etree.ElementTree as ET



class SermonTool(base_tool):
    name = "Sermon"
    description = """
      """
    

    def get_sitemap_urls(self):
        sitemap_url = f"{self.base_url}/public/sitemap"
        response = requests.get(sitemap_url)
        if response.status_code == 200:
            sitemap_data = response.text
            urls = self.parse_sitemap(sitemap_data)
            return urls
        else:
            return []

    def parse_sitemap(self, sitemap_data):
        root = ET.fromstring(sitemap_data)
        urls = []
        for url in root.findall('.//{http://www.sitemaps.org/schemas/sitemap/0.9}url'):
            loc = url.find('{http://www.sitemaps.org/schemas/sitemap/0.9}loc').text
            urls.append(loc)
        return urls

    def __init__(self):
        self.base_url = os.getenv('SERMON_BASE_URL')

        item_urls = self.get_sitemap_urls()
        self.sermons = [  self.get_item(url) for url in item_urls ] 
        for s in self.sermons:
            s['text'] = self.get_script(s)


    def is_fallback_tool(self):
        return True

    def get_item(self, url, is_published:bool = False):
        item_name = url.split('/')[-1]
        response = requests.get(f"{self.base_url}/api/final_sermon/junyang168@gmail.com/{item_name}/published")
        if response.status_code == 200:
            sermon_data = response.json()
            sermon_data['metadata']['item'] = item_name
            return sermon_data
        else:
            return None

    def get_script(self, sermon):
        return '\n\n'.join( [ p['text'] for p in sermon['script'] ] )


    def retrieve(self, args :str, question : str) -> RetrievalResult:

        docs =  [f"""<document index="{s.get('metadata').get('item')}">
    <source>{self.base_url}/public/{s.get('metadata').get('item')}</source>
    <document_content>{s['text']}</document_content></document>""" for s in self.sermons]
        doc_str = '\n'.join(docs)
        context = f"<documents>{doc_str}</documents>"

        refs = [Reference(Id=s.get('metadata').get('item'), Title=s.get('metadata').get('title'), Link=f"{self.base_url}/public/{s.get('metadata').get('item')}") for s in self.sermons]

        return RetrievalResult(content=context,references=refs)
    

    def get_answer_prompt_template(self,default_prompt, context):

        return """

Here are some documents for you to reference for your task:
{context}
First, find the quotes from the document that are most relevant to answering the question, and then print them in numbered order in <quotes></quotes> tags. Quotes should be relatively short. If there are no relevant quotes, write "No relevant quotes" instead.
Then, answer the question in <answer></answer> tags. Do not include or reference quoted content verbatim in the answer. Don't say "According to Quote [1]" when answering. Instead make references to quotes relevant to each section of the answer solely by adding their bracketed numbers at the end of relevant sentences.

If the question cannot be answered by the document, say so.

"""

    def get_quote_text(self, text):
        return text[ :text.find('[') ].strip()



    def parse_quotes(self, quotes_text) -> List[Reference]:
        quotes = []
        for q_text in quotes_text.split('\n'):
            q_text = q_text.strip()
            if not q_text:
                continue
            secs = q_text.split('.',1)
            if len(secs) < 2:
                continue
            quote = Reference(Id=secs[0], Title=self.get_quote_text(secs[1]))
            quotes.append(quote)
        return quotes
    

    def get_quotes_refs(self, quotes : List[Reference], sermon : dict):
        url = f"{self.base_url}/api/search"
        item = sermon['metadata']['item']
        req = {'item': item, 
               'text_list': [q.Title for q in quotes] }
        response = requests.post(url, json=req)
        if response.status_code == 200:
            data = response.json()
            for q in quotes:
                if not q.Link:
                    idx = data.get(q.Title)
                    if idx:
                        q.Link = f"{self.base_url}/public/{item}#{idx}" 

    def set_quote_refs(self, quotes : List[Reference]):      
        for s in self.sermons:
            qs = [ q for q in quotes if not q.Link]
            if qs:
                self.get_quotes_refs(qs, s)
              

    def parse_answer(self, answer):
        root = ET.fromstring("<root>" + answer + "</root>")
        quotes = root.findall('.//quotes')
        answer_node = root.find('.//answer')

        quotes = self.parse_quotes(quotes[0].text if quotes else '')

        self.set_quote_refs(quotes)
        answer_text = answer_node.text if answer_node is not None else ''
#        answer_text = answer_text + '\n[1]: <http://localhost:8000/public/2019-2-18  良心#1_210> "Hobbit lifestyles"'

        return answer_text, quotes




if __name__ == "__main__":


    st = SermonTool()

    quotes_text = '\n1. 論到祭偶像之物,我們曉得偶像在世上算不得甚麼,也知道神只有一位,再沒有別的神。[1]\n2. 但人不都有這等知識。有人到如今因拜慣了偶像,就以為所吃的是祭偶像之物。他們的良心既然軟弱,也就污穢了。[2]\n3. 其實食物不能叫神看中我們,因為我們不吃也無損,吃也無益。[3]\n4. 只是你們要謹慎,恐怕你們這自由竟成了那軟弱人的絆腳石。[4]\n5. 若有人見你這有知識的,在偶像的廟裡坐席,這人的良心若是軟弱,豈不放膽去吃那祭偶像之物嗎?[5]\n6. 因此,基督為他死的那軟弱弟兄,也就因你的知識沉淪了。[6]\n7. 你們這樣得罪弟兄們,傷了他們軟弱的良心,就是得罪基督。[7]\n'

    quotes = st.parse_quotes(quotes_text)
    st.set_quote_refs(quotes)
    print(quotes)



