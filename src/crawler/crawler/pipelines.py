# Define your item pipelines here
#
# Don't forget to add your pipeline to the ITEM_PIPELINES setting
# See: https://docs.scrapy.org/en/latest/topics/item-pipeline.html


# useful for handling different item types with a single interface
from itemadapter import ItemAdapter
import psycopg
import json

class SavePipeline:

    def open_spider(self, spider):
        self.CONNECTION_STRING = "postgresql://postgres:airocks$123@192.168.242.24:5432/postgres"
        self.conn = psycopg.connect(self.CONNECTION_STRING)
        

    def process_item(self, item, spider):
        ai = ItemAdapter(item)
        try:
            cur = self.conn.cursor()
        except Exception as err:
            self.conn = psycopg.connect(self.CONNECTION_STRING)
            cur = self.conn.cursor()
 
        sql = """
            insert into ingestion_content(id, source, lastmod, content_raw,content, metadata ) 
            values( %s, %s, %s, %s,%s, %s) 
            on conflict( id ) do update set source=excluded.source, lastmod = excluded.lastmod, content_raw = excluded.content_raw, metadata = excluded.metadata 
        """
        try:
            cur.execute(sql, (ai['url'],ai['source'], ai['lastmod'], ai['content_raw'] , ai['content'], ai['meta']) )
            self.conn.commit()
        except Exception as err:
            print(err)

        cur.close()

        return item
            
