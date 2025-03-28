import requests
import xml.etree.ElementTree as ET
from embed_content_provider import ContentProvider
import os

class SermonContent(ContentProvider):

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

    def get_item(self, url, is_published:bool = False):
        item_name = url.split('/')[-1]
        response = requests.get(f"{self.base_url}/sc_api/final_sermon/junyang168@gmail.com/{item_name}")
        if response.status_code == 200:
            sermon_data = response.json()
            sermon_data['metadata']['item'] = item_name
            return sermon_data
        else:
            return None   

    def __init__(self):         
        self.base_url = os.getenv('SERMON_BASE_URL')

    def get_source(self) -> str:
        return "holylogos"
    
    def get_content(self) -> list[dict]:
        item_urls = self.get_sitemap_urls()
        return [  self.get_item(url) for url in item_urls ] 

if __name__ == '__main__':
    sc = SermonContent()
    content = sc.get_content()

    print(content)

