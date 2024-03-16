from ..items import CrawlerItem
from crawler.spiders.enhanced_sitemap_spider import EnhancedSitemapSpider
from scrapy.http import Request
import re
from datetime import datetime, timezone, timedelta


class CommunityPostSpider(EnhancedSitemapSpider):
    name = 'CommunityPost'
    sitemap_urls = ["https://communities.vmware.com/sitemap.xml"]
    allowed_domains = ["communities.vmware.com"]

    def __get_lastmod(self, response ):
        ent = self.docs.get(response.url)
        if ent:
            return ent[0]
        else:
            return ""

    def _map_loc(self, loc:str) -> str:
        result = re.search(r'\/ct-p\/(.+)$',loc)
        if not result:
            return None
        comm_id = result.group(1)
        url = f"https://communities.vmware.com/t5/forums/recentpostspage/post-type/message/category-id/{comm_id}"
        return url
    

    def parse(self, response):                       
        last_page = response.xpath("//div[@id='pager']//li[@class='lia-component-pagesnumbered']/ul[@class='lia-paging-full-pages']/li[contains(@class,'lia-paging-page-last')]/a/text()").get()
        posts = response.xpath("//div[@class='lia-recent-posts']//div[contains(@class,'MessageView') and contains(@class,'lia-accepted-solution') ]//div[@class='MessageSubject']//a[contains(@class,'page-link')]/@href").getall()
        for url in posts:
            item = CrawlerItem()
            item['url'] = "https://communities.vmware.com" + url
            item['source'] = self.name
            item['lastmod'] = '2024-03-15'
            yield item

#            yield Request(url, self.parse_post) 


        post_date = response.xpath("//div[@class='lia-recent-posts']//div[contains(@class,'MessageView')]//div[@title='Posted on']//span[@class='local-date']/text()").get()
        if post_date:
            try:
                dt = datetime.strptime(post_date.strip('\u200e'),'%m-%d-%Y')
                if dt.year < 2021:
                    return
            except Exception as err:
                print(err)

        if not last_page:
            return

        current_page = '1'
        if response.url.find('/page/') >= 0:
            result = re.search(r'\/page\/(\d+)$',response.url)
            if not result:
                return
            current_page = result.group(1)

        current_page_num = int(current_page)

        if current_page_num < int(last_page):
            next_page = response.xpath("//div[@id='pager']//a[contains(@class,'lia-link-navigation') and @rel='next']/@href").get()
            if next_page:
                yield Request(next_page, self.parse) 

        