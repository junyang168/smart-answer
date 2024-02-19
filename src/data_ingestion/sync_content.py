import pandas as pd
import sqlalchemy
import os
import psycopg2
import json


CONNECTION_STRING =  os.environ.get("CONNECTION_STRING") 

def run_job_create_run(source):
    conn = psycopg2.connect(CONNECTION_STRING)
    cur = conn.cursor()
    sql = """
        select run_id, status from ingestion_run ir  
        where status is null or status <> 'complete'
        order by run_id desc 
        limit 1
    """
    cur.execute(sql)
    run_ids = cur.fetchall()
    if len(run_ids) > 0:
        conn.close()    
        return run_ids[0][0],run_ids[0][1] 
    else:
        sql = "INSERT INTO ingestion_run(source,run_time, status)values( %s , CURRENT_TIMESTAMP,'starting') RETURNING run_id"
        cur.execute(sql, (source,) )
        ids = cur.fetchall()
        conn.commit()
        conn.close()    
        return ids[0][0],'starting'

def run_job_sync_content(run_id):
    conn = psycopg2.connect(CONNECTION_STRING)
    cur = conn.cursor()
    cur.callproc("get_content_sync_delta", (run_id,) )
    ds = cur.fetchall()
    conn.commit()
    conn.close()    
    return ds

def run_job_update_status(run_id, status):
    conn = psycopg2.connect(CONNECTION_STRING)
    cur = conn.cursor()

    sql = "update ingestion_run set status = %s where run_id = %s "
    cur.execute(sql, (status, run_id) )
    conn.commit()
    conn.close()    

def extract_slice(connector, source, slice):
    last_mod_dict =  { ci[0]: ci[1] for ci in slice } 
    content_id_raw = connector.get_content([ id for id, lastmod in slice]) 
    content_all = []
    for cr in content_id_raw:
        id = cr[0]
        content_raw = cr[1]
        last_mod = last_mod_dict[id]
        meta, content = connector.get_content_meta_text(content_raw) 
        content_all.append( (id, last_mod , content_raw, content, meta) ) 
    save_content( source, content_all)


def save_content( source, content_all):
    conn = psycopg2.connect(CONNECTION_STRING)
    cur = conn.cursor()
    sql = """
        insert into ingestion_content(id, source, lastmod, content_raw, metadata, content ) 
        values( %s, %s, %s,%s, %s, %s) 
        on conflict( id ) do update set content = excluded.content, content_raw = excluded.content_raw, metadata = excluded.metadata 
    """

    ds = [ (rec[0], source, rec[1] , get_content_element(rec[2]),json.dumps(rec[4]), rec[3] ) for rec in content_all ]    
    cur.executemany(sql, ds )

    conn.commit()
    cur.close()
    conn.close() 

def get_content_element(content):
    if not content:
        return content
    
    if not isinstance( content, str):
        return json.dumps(content)
    else: 
        return content   

def run_sync_job(connector, test_run=False):
    source = connector.get_source()

    run_id, status = run_job_create_run(source)
    print( f"sync job for {source} starts with run id : {run_id}")

    if status == 'starting':
        ingestion_content = connector.get_content_list()
        if ingestion_content:
            print(f" no of ids {len(ingestion_content)}")
        if ingestion_content is None or len(ingestion_content) == 0:
            run_job_update_status(run_id, "complete")
            return
    
        for row in ingestion_content: 
            row.append(run_id)

        df = pd.DataFrame(ingestion_content, columns=["content_id", "lastmod","run_id"])
        # Create a database engine
        engine = sqlalchemy.create_engine(CONNECTION_STRING)

        # Save the dataframe to a table in the database
        df.to_sql("ingestion_run_content_id", engine, index=False, if_exists="append")

        # Close the engine
        engine.dispose()

        run_job_update_status(run_id,'content_id')
        status = 'content_id'

    if status == 'content_id':
        ids_to_insert = run_job_sync_content(run_id)
        print(f" no of content to load {len(ids_to_insert)}")

        block_size = 100   

        idx = 0
        while idx + block_size < len(ids_to_insert):
            slice = ids_to_insert[idx:idx+block_size]
            extract_slice(connector, source, slice)
            idx = idx + block_size 

        slice = ids_to_insert[idx:]
        extract_slice(connector, source, slice)

        run_job_update_status(run_id, "complete")



if __name__ == '__main__':
    import connectors
    for conn in connectors.connectors:
        run_sync_job(conn)
