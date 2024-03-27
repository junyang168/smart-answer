from ..items import CrawlerItem
from scrapy import Spider
from scrapy.http import Request
import psycopg
import re
from datetime import datetime, timezone, timedelta
from typing import Any
import requests
import jsonpath_ng
import json


class Doc2Spider(Spider):
    name = 'Doc'

    start_urls = [
            "https://docs.vmware.com/en/VMware-vSphere/index.html"
        ]
    
    def __init__(self, name: str | None = None, **kwargs: Any):
        super().__init__(name, **kwargs)
        self.__load_sitemap()
#        self.new_method()
        self.start_urls = ['https://docs.vmware.com/en/VMware-vSphere/index.html']



    def parse_product_list(self, response):
        products =  json.loads(response.text)



    def start_requests(self) :
        url = "https://docs.vmware.com/search/get-all-products"

        # The JSON data you want to post
        request_body = json.dumps( {
            "includes": ["product", "url"],
            "language": "en",
            "target": "prod"
        } )
        yield Request(url, method="POST", body=request_body, headers={'Content-Type': 'application/x-www-form-urlencoded'},callback=self.parse_product_list )

 
    def __load_sitemap(self):
        self.CONNECTION_STRING = "postgresql://postgres:airocks$123@192.168.242.24:5432/postgres"
        sql = f"select id, lastmod from ingestion_content where source='{self.name}' "
        conn = psycopg.connect(self.CONNECTION_STRING)
        cur = conn.cursor()
        cur.execute(sql)
        ds = cur.fetchall()
        self.docs = { self.__remove_url_hash( d[0] ) : d[1] for d in ds }
        cur.close()
        conn.close()


    def __get_lastmod(self, response ):
        ent = self.docs.get(response.url)
        if ent:
            return ent[0]
        else:
            dt = datetime.strptime(response.xpath("//meta[@name='last modified']/@content").get(), '%d/%m/%Y %H:%M:%S')
            return dt.strftime("%Y-%m-%d")
    

    def __remove_url_hash(self, url :str) -> str:
        idx = url.find('#')
        if idx >= 0:
            url = url[:idx]
        return url

    def get_urls_from_json(self, json_data):
        jsonpath_expr = jsonpath_ng.parse('$..link_url')
        list_val = [match.value for match in jsonpath_expr.find(json_data)]

        urls = set(list_val)

        return [ url for url in urls if url and ( url[0] == '/' or url.startswith('https://docs.vmware.com')) and url.endswith('.html') ]
    

    def parse_toc(self, response):
        self.docs[response.url] = '2024-03-15'
        item = CrawlerItem()
        item['url'] = response.url
        item['source'] = self.name
        item['meta'] = {'content_type':'toc'}
        json_data = json.loads(response.text)
        item['content_raw'] = json_data
        item['lastmod'] = '2024-03-15'
        item['content_type'] = 'toc'
        links = self.get_urls_from_json(json_data)
        for link in links:
            url = 'https://docs.vmware.com' + link if link[0] =='/' else link
            if not self.docs.get(url):
                yield response.follow(link, self.parse)
#        yield item


    def parse(self, response):
        if not self.docs.get(response.url):
            item = CrawlerItem()
            item['url'] = response.url
            item['source'] = self.name
            item['lastmod'] = self.__get_lastmod(response) 
            self.docs[item['url']] = item['lastmod']

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
                yield item
            else:
                pass     
        
        toc_url = response.url[: response.url.rfind('/')] + '/toc.json'

        toc = self.docs.get(toc_url)
        if not toc:   
            self.docs[toc_url] = '2024-03-15'
            yield Request(toc_url, self.parse_toc)

       



