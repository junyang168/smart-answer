from ..items import CrawlerItem
from scrapy import Spider
from scrapy.http import Request
import psycopg
import re
from datetime import datetime, timezone, timedelta

class CommunitySpider(Spider):
    name = 'Community'
    sitemap_urls = ["https://communities.vmware.com/sitemap.xml"]
    allowed_domains = ["communities.vmware.com"]

    def __get_lastmod(self, response ):
        ent = self.docs.get(response.url)
        if ent:
            return ent[0]
        else:
            return ""

    def start_requests(self):
        self.CONNECTION_STRING = "postgresql://postgres:airocks$123@192.168.242.24:5432/postgres"

        sql = f"select id, lastmod from ingestion_content where source='CommunityPost' "
        conn = psycopg.connect(self.CONNECTION_STRING)
        cur = conn.cursor()
        cur.execute(sql)
        ds = cur.fetchall()
        cur.close()
        conn.close()        
        for r in ds:
            yield Request(r[0], self.parse_post)


    def __get_lastmod(self, response):
        date_time_str = response.xpath("//meta[@itemprop='dateModified']/@content").get()
        dt = datetime.fromisoformat(date_time_str)
        return dt.strftime("%Y-%m-%d")


    def parse_post(self, response):                       
        item = CrawlerItem()
        item['url'] = response.url
        item['source'] = self.name
        item['lastmod'] =  self.__get_lastmod(response)
        item['meta'] =  {
                'title' : response.xpath("//meta[@property='og:title']/@content").get(),
                'community' :  response.xpath("//meta[@property='article:section']/@content").get()
            }        
        item['content_raw'] = [{'article': response.xpath("//div[contains(@class,'lia-quilt-column-main-content')]").get()}]

        yield item

