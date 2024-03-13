from scrapy.spiders import SitemapSpider
import scrapy
from scrapy.utils.sitemap import Sitemap
import psycopg

class EnhancedSitemapSpider(SitemapSpider):

    sitemap_index_filters = []


    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.CONNECTION_STRING = "postgresql://postgres:airocks$123@192.168.242.24:5432/postgres"
        self.__load_sitemap()


    def sitemap_filter(self, entries):
        if entries.type == "urlset":
            for entry in entries:
                loc = self._map_loc(entry['loc'])
                lastmod = entry.get('lastmod')
                if loc and self.__check_lastmod(loc, lastmod):
                    yield ( {'loc':loc, 'lastmod': lastmod })
        else:
            for entry in entries:
                if not self.sitemap_index_filters :
                    yield entry
                else:
                    for f in self.sitemap_index_filters:
                        if entry['loc'].find(f) >=0 :
                            yield entry

            

    def __load_sitemap(self):
        sql = f"select id, lastmod from ingestion_content where source='{self.name}' "
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
#            sql = f"insert into ingestion_content(lastmod,id, source) values(%s, %s, '{self.name}') on conflict( id ) do update set lastmod = excluded.lastmod"
#            cur.executemany(sql, new_docs)        
#            sql = "update ingestion_content_timestamp set lastmod = %s where id = %s"
#           cur.executemany(sql, update_docs)
            sql = "delete from ingestion_content  where id = %s"
            cur.executemany(sql, del_docs)
            conn.commit()
            conn.close()
        except Exception as err:
            print(err)
        
    def __check_lastmod(self, loc : str, lastmod:str):
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


    def _map_loc(self, loc:str) -> str:
        return loc

 
