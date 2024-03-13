from scrapy.settings import Settings

from scrapy.crawler import CrawlerProcess
from crawler.spiders.kb_spider import KBSpider
from crawler.spiders.doc_spider import DocSpider
from crawler.spiders.core_spider import CoreSpider
from crawler.spiders.blogs_spider import BlogSpider
from crawler import settings as my_settings

from datetime import datetime

crawler_settings = Settings()
crawler_settings.setmodule(my_settings)
process = CrawlerProcess(settings=crawler_settings)
c = CrawlerProcess(settings=crawler_settings)
#c.crawl(KBSpider)
c.crawl(DocSpider)
#c.crawl(CoreSpider)
#c.crawl(BlogSpider)
c.start()

