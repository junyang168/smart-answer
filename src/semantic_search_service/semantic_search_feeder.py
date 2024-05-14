import psycopg
from pgvector.psycopg2 import register_vector
from dotenv import load_dotenv
load_dotenv()

import sys
import os 
import json

#from kb_extractor import kb_extractor
from embed_content_extractor import embed_content_extractor
import semantic_search_service
from content_store import FeedPassage, HybridScore

from tqdm import tqdm



# Get the current script's directory
current_dir = os.path.dirname(os.path.abspath(__file__))
# Get the parent directory by going one level up
parent_dir = os.path.dirname(current_dir)
# Add the parent directory to sys.path
sys.path.append(parent_dir)


class semantic_search_feeder:

    def __init__(self, extractors) -> None:
        self.extractors = extractors
        self.semanticSearch = semantic_search_service.SemanticSearchService(load_data = False)


    def load_jsonl(self, input_path) -> list:
        """
        Read list of objects from a JSON lines file.
        """
        data = []
        with open(input_path, 'r', encoding='utf-8') as f:
            for line in f:
                data.append(json.loads(line.rstrip('\n|\r')))
        return data

    def get_content_to_embed(self):
        conn = psycopg.connect(self.CONNECTION_STRING)
        cur = conn.cursor()
        sql = """
            select ic.id, ic.source, ic.metadata , ic."content" 
                from ingestion_content ic 
                where source='KB2'
--                and ic.metadata->>'lastModifiedDate' like '2023-05-2%'
--                and not exists(select 1 from semantic_search_feed ssf where ssf.content_id = ic.id)
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
    
    def reset(self):
        self.semanticSearch.reset_vector_store()

    def process_content(self, content_providers ):
#        ds = self.get_content_to_embed()
#        print(f'sync {len(ds)} documents to vespa')
        emb_ds = []
        for cp in content_providers:
            ds = cp.get_content()
            source = cp.get_source()
            for doc in ds:
                meta = doc.get('metadata')
                item_id = meta.get('item')
                content = doc.get('script')
                extractor =  self.get_extractor( source ) 
                if extractor:
                    md = extractor.get_metadata(meta, content)
                    chunks = extractor.get_content(meta,content)
                    for i, chunk in enumerate(chunks) :
                        rec_id = f"{item_id}-{i}"
                        if len(emb_ds) < 20:
                            emb_ds.append( FeedPassage(
                                id = rec_id,
                                content_id= item_id,
                                text= chunk
                            ) )
                        else:
                            self.semanticSearch.feed(emb_ds)
                            emb_ds.clear()
            if emb_ds:
                self.semanticSearch.feed(emb_ds)
        self.semanticSearch.persist()



    def update_feed_status(self, feed_passages):
        ds = [ (p.id, p.content_id, p.vespa_id, p.last_updated, p.status) for p in feed_passages ]
        with psycopg.connect(self.CONNECTION_STRING) as conn:
            with conn.cursor() as cur:
                sql = """
                    insert into semantic_search_feed(chunk_id,content_id, vespa_id, last_updated, status, updated_time) 
                    values( %s, %s, %s, %s, %s, now()) 
                    on conflict( chunk_id ) do update set content_id=excluded.content_id, status = excluded.status, last_updated = excluded.last_updated, updated_time = now()  
                """
                try:
                    cur.executemany(sql, ds )
                    conn.commit()
                except Exception as err:
                    print(err)

if __name__ == '__main__':
    from sermon_extractor import SermonExtractor
    from sermon_content import SermonContent

    content_providers = [SermonContent()]

    feeder = semantic_search_feeder([SermonExtractor()])
    
    feeder.reset()
    feeder.process_content(content_providers)

#    go = input('are you sure to delete all records[Y/N]')
#    if go == 'Y':
