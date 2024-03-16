# Define here the models for your scraped items
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/items.html

import scrapy


class CrawlerItem(scrapy.Item):
    source = scrapy.Field()
    meta = scrapy.Field()
    url = scrapy.Field()
    content = scrapy.Field()
    lastmod = scrapy.Field()
    content_raw = scrapy.Field()
    content_type = scrapy.Field()
