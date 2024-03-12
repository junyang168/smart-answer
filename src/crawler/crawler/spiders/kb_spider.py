from scrapy.spiders import SitemapSpider
import scrapy
from scrapy.utils.sitemap import Sitemap, sitemap_urls_from_robots
from urllib.parse import urlparse, parse_qsl
import re
import json
import psycopg
import sys
from ..items import CrawlerItem

class KBSpider(SitemapSpider):

    name = 'KB'

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.CONNECTION_STRING = "postgresql://postgres:airocks$123@192.168.242.24:5432/postgres"
        self.load_sitemap()


    def load_sitemap(self):
        sql = "select id, lastmod from ingestion_content_timestamp "
        conn = psycopg.connect(self.CONNECTION_STRING)
        cur = conn.cursor()
        cur.execute(sql)
        ds = cur.fetchall()
        self.docs = { d[0] : (d[1],None) for d in ds }
        cur.close()
        conn.close()

    def closed(self, reason):
        new_docs = []
        update_docs = []
        del_docs = []
        for loc, tu  in self.docs.items():
            lastmod, status = tu
            if status == 'New':
                new_docs.append((lastmod,loc ) )
            elif status == 'Modified':
                update_docs.append((lastmod,loc))
            elif not status:
                del_docs.append(loc)        
        try:
            conn = psycopg.connect(self.CONNECTION_STRING)
            cur = conn.cursor()
            sql = "insert into ingestion_content_timestamp(lastmod,id) values(%s, %s) on conflict( id ) do update set lastmod = excluded.lastmod"
            cur.executemany(sql, new_docs)        
            sql = "update ingestion_content_timestamp set lastmod = %s where id = %s"
            cur.executemany(sql, update_docs)
            sql = "delete from ingestion_content_timestamp  where id = %s"
            cur.executemany(sql, del_docs)
            conn.commit()
            conn.close()
        except Exception as err:
            print(err)
        

    sitemap_urls = ['https://kb.vmware.com/km_sitemap_index']
    allowed_domains = ["kb.vmware.com"]

    def check_lastmod(self, loc : str, lastmod:str):
        if not lastmod:
            return True
        cur_lastmod = self.docs.get(loc)
        if cur_lastmod:
            if cur_lastmod[0] == lastmod:
                self.docs[loc] = (lastmod, 'No Change')
                return False            
            self.docs[loc] = (lastmod, 'Modified')
        else:
            self.docs[loc] = (lastmod, 'New')
        return True        

    def iterloc(self, it, alt=False):
        for d in it:
            loc = d["loc"]
            lastmod = d.get('lastmod')
            if self.check_lastmod(loc, lastmod) :
                yield loc

            # Also consider alternate URLs (xhtml:link rel="alternate")
            if alt and "alternate" in d:
                yield from d["alternate"]


    def sitemap_filter(self, entries):
        for entry in entries:
            loc = entry['loc']
            yield entry

    def _parse_sitemap(self, response):
        body = self._get_sitemap_body(response)
        if body is None:
            return

        s = Sitemap(body)
        it = self.sitemap_filter(s)

        if s.type == "sitemapindex":
            for loc in self.iterloc(it, self.sitemap_alternate_links):
                if any(x.search(loc) for x in self._follow):
                    yield scrapy.Request(loc, callback=self._parse_sitemap)        
        elif s.type == "urlset":
            for loc in self.iterloc(it, self.sitemap_alternate_links):
                for r, c in self._cbs:
                    if r.search(loc):
                        result = re.search(r'\/s\/article\/(\d+)\?',loc)
                        if not result:
                            result = re.search(r'\/s\/article\/(\d+)$',loc)
                            if not result:
                                continue
                        docid =result.group(1)
                        url = f"https://kb.vmware.com/services/apexrest/v1/article?docid={docid}"
                        parsed_url = urlparse(loc)
                        qs = dict(parse_qsl(parsed_url.query) )
                        lang = qs.get('lang')
                        if lang:
                            url += f"&lang={lang}"
                        yield scrapy.Request(url)
                        break       

#    def parse_en_html(self, response):
    def parse(self, response):
        doc = json.loads( response.body )
        print('parse_article url:', response.url)
        parsed_url = urlparse(response.url)
        qs = dict( parse_qsl(parsed_url.query) )
        meta = doc['meta']['articleInfo']
        meta['product_versions'] = doc['meta']['articleProducts']          
        url = f"https://kb.vmware.com/s/article/{qs.get('docid')}"  
        lang = qs.get('lang') 
        if lang:
            url += f"?lang={lang}"

        item = CrawlerItem()
        item['url'] = url
        item['meta'] = meta
        item['content_raw'] = doc['content']
        item['lastmod'] = doc['meta']['articleInfo']['lastModifiedDate']

        yield item

if __name__ == '__main__':

    from scrapy.crawler import CrawlerProcess
    from pipelines import CrawlerPipeline
    c = CrawlerProcess({
        'USER_AGENT': "Mozilla/5.0 (iPad; CPU OS 12_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148" ,
    })


    c.crawl(KBSpider)
    c.start()

