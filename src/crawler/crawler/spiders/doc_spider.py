from scrapy.spiders import SitemapSpider
import scrapy
from scrapy.utils.sitemap import Sitemap, sitemap_urls_from_robots
from urllib.parse import urlparse, parse_qsl
import re
import json
import psycopg
import sys
from ..items import CrawlerItem
from crawler.spiders.enhanced_sitemap_spider import EnhancedSitemapSpider
from datetime import datetime

class DocSpider(EnhancedSitemapSpider):
    name = 'Doc'
    sitemap_urls = ["https://docs.vmware.com/sitemap.xml"]
    allowed_domains = ["docs.vmware.com"]

    sitemap_rules = [(r'/en/.+\.html', 'parse_en_html')]
    sitemap_index_filters = ['_en_']

    def sitemap_filter2(self, entries):
        if entries.type == "sitemapindex":
            for entry in entries:
                if entry['loc'].find('_en_') >=0 :
                    yield entry
        else:
            super().sitemap_filter(entries)

    def __get_lastmod(self, response ):
        ent = self.docs.get(response.url)
        if ent:
            return ent[0]
        else:
            dt = datetime.strptime(response.xpath("//meta[@name='last modified']/@content").get(), '%d/%m/%Y %H:%M:%S')
            return dt.strftime("%Y-%m-%d")


    def parse_en_html(self, response):
        ent =  self.docs.get(response.url) 
        if ent and (not ent[1] or ent[1] == 'No Change'):
            return
         
        item = CrawlerItem()
        item['url'] = response.url
        item['source'] = self.name
        article = response.xpath("//article")
        parent_id = ''
        if article:
            body = article.xpath('div')
            if body:
                body = body.get()
                parent_link = article.xpath("//div[@class='parentlink']//a/@href").get()
                if parent_link:
                    parent_id = parent_link.replace('.html','') 
            else:
                body = article.get()
        else:
            article = response.xpath("//div[contains(@class,'article-wrapper')]")
            if article:
                body = article.get()
        
        if article:
            item['meta'] = {
                'title' : response.xpath("//meta[@name='title']/@content").get(),
                'document_id' : response.xpath("//meta[@name='guid']/@content").get(),
                'product_versions' : response.xpath("//meta[@name='product']/@content").get(),
                'parent_id' : parent_id
            }
            item['content_raw'] = [{ 'article': body }] 
            item['lastmod'] = self.__get_lastmod(response) 
            yield item
        else:
            pass
