from ..items import CrawlerItem
from crawler.spiders.enhanced_sitemap_spider import EnhancedSitemapSpider

class CoreSpider(EnhancedSitemapSpider):
    name = 'Core'
    sitemap_urls = ["https://core.vmware.com/sitemap.xml"]
    allowed_domains = ["core.vmware.com"]

    def __get_lastmod(self, response ):
        ent = self.docs.get(response.url)
        if ent:
            return ent[0]
        else:
            return ""


    def parse(self, response):
        item = CrawlerItem()
        item['url'] = response.url
        item['source'] = self.name
        article = response.xpath("//div[@class='article-body']")
        
        if article:
            item['meta'] = {
                'title' : response.xpath("/head/title").get(),
            }
            item['content_raw'] = [{ 'article': article.get() }] 
            item['lastmod'] = self.__get_lastmod(response) 
            yield item
        else:
            pass
