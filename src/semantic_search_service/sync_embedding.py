import psycopg
from pgvector.psycopg2 import register_vector
from dotenv import load_dotenv
load_dotenv()

import sys
import os 
import json

from kb_extractor import kb_extractor
from embed_content_extractor import embed_content_extractor
from model import get_model, FeedPassage
from tqdm import tqdm



# Get the current script's directory
current_dir = os.path.dirname(os.path.abspath(__file__))
# Get the parent directory by going one level up
parent_dir = os.path.dirname(current_dir)
# Add the parent directory to sys.path
sys.path.append(parent_dir)


class semantic_search_feeder:

    extractors = [
        kb_extractor()
    ]

    def __init__(self) -> None:
        self.CONNECTION_STRING = os.environ["CONNECTION_STRING"]
        get_model().CONNECTION_STRING = self.CONNECTION_STRING



    def get_content_to_embed(self):
        conn = psycopg.connect(self.CONNECTION_STRING)
        cur = conn.cursor()
        sql = """
            select ic.id, ic.source, ic.metadata , ic."content" 
                from ingestion_content ic 
                where 
                source='KB2' and not exists( select 1 from semantic_search_feed sf where sf.content_id = ic.id )
        """
        cur.execute(sql)
        ds = cur.fetchall()
        conn.commit()
        conn.close()    
        return ds
    
    def get_extractor(self, source:str) -> embed_content_extractor:
        for ext in self.extractors:
            if ext.get_source() == source:
                return ext
        return None


    def process_content(self):
        ds = self.get_content_to_embed()
        print(f'sync {len(ds)} documents to vespa')
        emb_ds = []
        for i in tqdm( range(len(ds)) ):
            r = ds[i]
            id = r[0]
            source = r[1]
            content = json.loads(r[3])
            meta = r[2]
            extractor =  self.get_extractor( source ) 
            if extractor:
                md = extractor.get_metadata(meta, content)
                chunks = extractor.get_content(meta,content)
                for i in range(len(chunks)):
                    rec_id = id
                    if i > 0:
                        rec_id += f'-{i}'
                    emb_ds.append( FeedPassage(
                        id= rec_id,
                        content_id= id,
                        text= chunks[i],
                        language=md.get('language'),
                        last_updated=md.get('last_updated')
                    )
                    )
            if len(emb_ds) > 5:
                feed_result = get_model().feed(emb_ds)
                self.update_feed_status(feed_result)
                emb_ds.clear()

    def update_feed_status(self, feed_passages):
        ds = [ (p.id, p.content_id, p.last_updated, p.status) for p in feed_passages ]
        with psycopg.connect(self.CONNECTION_STRING) as conn:
            with conn.cursor() as cur:
                sql = """
                    insert into semantic_search_feed(chunk_id,content_id, last_updated, status, updated_time) 
                    values( %s, %s, %s, %s, now()) 
                    on conflict( chunk_id ) do update set content_id=excluded.content_id, status = excluded.status, last_updated = excluded.last_updated, updated_time = now()  
                """
                try:
                    cur.executemany(sql, ds )
                    conn.commit()
                except Exception as err:
                    print(err)

if __name__ == '__main__':
    feeder = semantic_search_feeder()
    feeder.process_content()

