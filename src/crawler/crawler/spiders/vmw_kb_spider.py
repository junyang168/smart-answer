from scrapy.spiders import SitemapSpider
from urllib.parse import urlparse, parse_qsl
import re
import json
from ..items import CrawlerItem
from crawler.spiders.enhanced_sitemap_spider import EnhancedSitemapSpider

class VmwKBSpider(EnhancedSitemapSpider):

    name = 'KB2'

    sitemap_urls = ['https://kb.vmware.com/km_sitemap_index']
    allowed_domains = ["kb.vmware.com"]


    api_loc_map = {}


    def _map_loc(self, loc:str) -> str:
        result = re.search(r'\/s\/article\/(\d+)\?',loc)
        if not result:
            result = re.search(r'\/s\/article\/(\d+)$',loc)
            if not result:
                return None
        docid = result.group(1)
        url = f"https://kb.vmware.com/services/apexrest/v1/article?docid={docid}"
        parsed_url = urlparse(loc)
        qs = dict(parse_qsl(parsed_url.query) )
        lang = qs.get('lang')
        if lang:
            url += f"&lang={lang}"
        self.api_loc_map[url] = loc
        return url
    
    def __get_lastmod(self, api_url, doc ):
        loc = self.api_loc_map.get(api_url)
        if loc:
            ent = self.docs.get(loc)
            if ent:
                return loc, ent[0]
        return loc, doc['meta']['articleInfo']['lastModifiedDate']


    def parse(self, response):
        doc = json.loads( response.body )
        print('parse_article url:', response.url)
        parsed_url = urlparse(response.url)
        qs = dict( parse_qsl(parsed_url.query) )
        meta = doc['meta']['articleInfo']
        meta['product_versions'] = doc['meta']['articleProducts']          

        item = CrawlerItem()
        item['source'] = self.name
        item['meta'] = meta
        item['content_raw'] = doc['content']
        item['url'], item['lastmod'] = self.__get_lastmod( response.url, doc)

        yield item

