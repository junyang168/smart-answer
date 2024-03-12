from scrapy.spiders import SitemapSpider
import scrapy
from scrapy.utils.sitemap import Sitemap, sitemap_urls_from_robots
from scrapy.settings import Settings

from scrapy.crawler import CrawlerProcess
from crawler.spiders.kb_spider import KBSpider
from crawler import settings as my_settings

crawler_settings = Settings()
crawler_settings.setmodule(my_settings)
process = CrawlerProcess(settings=crawler_settings)
c = CrawlerProcess(settings=crawler_settings)
c.crawl(KBSpider)
c.start()

