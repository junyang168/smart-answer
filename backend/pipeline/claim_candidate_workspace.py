"""Private socket-only PostgreSQL for candidate verification, never canonical ingest.

The full unreviewed package is retained, not relabelled as reviewed. This loader
cannot accept a database URL: its only destination is the cluster it starts.
Normal Flash transactions/readback are reused inside that disposable workspace.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore, normalize_package, record_content_sha
from backend.pipeline import evidence_refresh as e
from backend.pipeline.knowledge_package_merge import validate_merged_package


def command(args):
    result=subprocess.run(args,capture_output=True,text=True,timeout=60)
    if result.returncode:raise RuntimeError('candidate PostgreSQL failed: '+(result.stderr or result.stdout)[-2000:])


@contextmanager
def open_candidate(package, root):
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    validate_merged_package(package)
    records=normalize_package(package)
    if len(records.get('source_documents',{}))!=1:raise ValueError('candidate workspace requires one source')
    if any(p.get('review_status') not in (None,'candidate') for values in records.values() for p in values.values()):
        raise ValueError('creation candidate cannot carry review approval or holds')
    for doc in records['source_documents'].values():
        if hashlib.sha256(Path(doc['source_path']).read_bytes()).hexdigest()!=doc['source_file_sha256']:
            raise ValueError('candidate source file drift')
    initdb=shutil.which('initdb');pgctl=shutil.which('pg_ctl')
    if not initdb or not pgctl:raise RuntimeError('candidate isolation requires local PostgreSQL binaries')
    with (root/'workspace.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if (root/'data').exists() and not (root/'isolation.json').exists():
            raise ValueError('refuse pre-existing PostgreSQL directory without candidate workspace binding')
        e.save(root/'candidate-input.json',package)
        e.save(root/'isolation.json',{'version':'claim_flash_private_candidate_sql_v1','candidate_sha256':e.digest(package),
            'data_directory':str(root/'data'),'canonical_authority':False})
        data=root/'data';sock=Path(tempfile.mkdtemp(prefix='flash-pg-',dir='/tmp'));started=False
        try:
            if not (data/'PG_VERSION').exists():
                command([initdb,'-D',str(data),'-A','trust','--no-locale','--encoding=UTF8','-U','postgres'])
            command([pgctl,'-D',str(data),'-l',str(root/'postgres.log'),'-o',f"-h '' -k {sock} -p 5432",'-w','start'])
            started=True
            store=PostgresKnowledgeStore(make_conninfo(host=str(sock),port=5432,dbname='postgres',user='postgres'))
            store.migrate()
            with store.connect() as conn:
                actual=Path(conn.execute('SHOW data_directory').fetchone()[0]).resolve()
                if actual!=data.resolve() or conn.execute('SHOW listen_addresses').fetchone()[0]:
                    raise ValueError('candidate workspace is not its private socket-only cluster')
                receipt=conn.execute("SELECT metadata FROM wang_knowledge.change_sets WHERE change_set_id='candidate-bootstrap'").fetchone()
                if receipt:
                    if receipt[0]['candidate_sha256']!=e.digest(package):raise ValueError('workspace belongs to another candidate')
                else:
                    if conn.execute('SELECT count(*) FROM wang_knowledge.objects').fetchone()[0]:
                        raise ValueError('candidate bootstrap requires empty private workspace')
                    # Explicit isolated bootstrap. No call to canonical ingest, no
                    # discarded extraction metadata, no forged review/consensus seal.
                    conn.execute('''INSERT INTO wang_knowledge.change_sets
                        (change_set_id,fingerprint_sha256,package_id,source_kind,source_sha256,status,metadata,applied_at)
                        VALUES ('candidate-bootstrap',%s,%s,'isolated_unreviewed_candidate',%s,'applied',%s,now())''',
                        (e.digest(package),package['package_id'],e.digest(package),Jsonb({'candidate_sha256':e.digest(package),
                            'candidate_path':str(root/'candidate-input.json'),'canonical_authority':False})))
                    for col,values in records.items():
                        for oid,payload in values.items():
                            sha=record_content_sha(payload)
                            conn.execute('''INSERT INTO wang_knowledge.objects
                                (collection,object_id,revision,review_status,visibility,content_sha256,payload)
                                VALUES (%s,%s,1,'candidate','internal',%s,%s)''',(col,oid,sha,Jsonb(payload)))
                            conn.execute('''INSERT INTO wang_knowledge.object_versions
                                (collection,object_id,revision,content_sha256,payload,change_set_id)
                                VALUES (%s,%s,1,%s,%s,'candidate-bootstrap')''',(col,oid,sha,Jsonb(payload)))
            store._flash_candidate_workspace=str(root)
            yield store
        finally:
            if started:command([pgctl,'-D',str(data),'-m','fast','-w','stop'])
            shutil.rmtree(sock)
            # Keep stopped SQL state for exact continuation; never leave a daemon.
            e.publish(root,{'stopped':True,'data_retained_for_resume':True,'production_writes':0})
