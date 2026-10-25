"""Independent bounded SQL closure for saved Claim/CVP audit verification.

Standard library only: no backend reader/models. This is a readset, not content
approval. Every selected CVP's ALL active links and all linked Claims are kept;
retired bound Evidence/Fragments remain material for the independent reader.
No whole collection/history fallback or query that hides a truncated population.
"""
from copy import deepcopy
import json
import subprocess
import time

COLLECTIONS = ('claims','evidence_steps','source_fragments','source_documents',
               'canonical_viewpoints','viewpoint_revisions','viewpoint_claim_links')
MAX_ROWS = 10000
MAX_BYTES = 16*1024**2


def identifiers(values):
    values = list(values)
    if len(values)>MAX_ROWS or any(type(v) is not str or not v or len(v)>512 for v in values):
        raise ValueError('bounded exact nonempty audit identities required')
    return sorted(set(values))


def literal(value):
    return "'"+value.replace("'","''")+"'"


def indexed_plan(value,target='object_id'):
    """Full table OR full index walks are not a bounded target query."""
    stack=[value[0]['Plan']]
    while stack:
        node=stack.pop();stack.extend(node.get('Plans',[]))
        if node.get('Relation Name')=='objects':
            kind=node.get('Node Type','')
            if (kind in ('Seq Scan','Parallel Seq Scan')
                    or kind in ('Index Scan','Index Only Scan','Bitmap Heap Scan')
                    and not (node.get('Index Cond') or node.get('Recheck Cond'))):
                raise ValueError('independent audit requires an actual indexed readset; no fallback')
            if (kind in ('Index Scan','Index Only Scan','Bitmap Heap Scan')
                    and target not in (node.get('Index Cond','')+node.get('Recheck Cond',''))):
                raise ValueError('collection-only index walk is not an indexed target query')


class AuditReadset:
    def __init__(self,database_url,claim_ids,viewpoint_ids,*,seconds=5):
        self.database_url=database_url
        self.claim_ids,self.viewpoint_ids=identifiers(claim_ids),identifiers(viewpoint_ids)
        if not self.claim_ids or not self.viewpoint_ids or not 0<seconds<=5:
            raise ValueError('complete selected Claim/CVP denominator and hard budget required')
        self.started=time.perf_counter();self.seconds=seconds
        self.metrics={'sql_calls':0,'sql_result_rows':0,'sql_result_bytes':0,'query_plans':0}
        self.rows={name:{} for name in COLLECTIONS}
        self.epoch=self._plain("select json_build_object('server_start',pg_postmaster_start_time(),"
            "'database_oid',(select oid from pg_database where datname=current_database()),"
            "'filenode',pg_relation_filenode('wang_knowledge.objects'::regclass))::text")[0]
        self._load()
        if self._plain("select json_build_object('server_start',pg_postmaster_start_time(),"
            "'database_oid',(select oid from pg_database where datname=current_database()),"
            "'filenode',pg_relation_filenode('wang_knowledge.objects'::regclass))::text")[0]!=self.epoch:
            raise ValueError('audit SQL storage epoch changed')

    def _raw(self,sql,*,bitmap_only=False):
        remaining=self.seconds-(time.perf_counter()-self.started)
        if remaining<=0:raise ValueError('independent audit readset hard time budget exceeded')
        milliseconds=max(1,int(remaining*1000))
        wrapped=(f'BEGIN READ ONLY; SET LOCAL statement_timeout={milliseconds}; '
                 'SET LOCAL standard_conforming_strings=on; SET LOCAL enable_seqscan=off; '
                 'SET LOCAL enable_indexscan='+('off' if bitmap_only else 'on')+'; '
                 'SET LOCAL enable_bitmapscan=on; SET LOCAL row_security=off; '+sql+'; COMMIT;')
        # psql -c sends the whole string as one simple-query message and only
        # exposes its final COMMIT result. stdin sends each statement in the
        # same owned session, retaining the SELECT/EXPLAIN output before COMMIT.
        result=subprocess.run(['psql',self.database_url,'-XqAt','-v','ON_ERROR_STOP=1'],input=wrapped,
                              capture_output=True,text=True,timeout=remaining)
        self.metrics['sql_calls']+=1
        self.metrics['sql_result_bytes']+=len(result.stdout.encode())
        if result.returncode:raise ValueError('independent indexed readset SQL failed; no fallback')
        if self.metrics['sql_result_bytes']>MAX_BYTES:raise ValueError('audit readset transfer budget exceeded')
        if time.perf_counter()-self.started>self.seconds:raise ValueError('audit readset time budget exceeded')
        return result.stdout

    def _plain(self,sql,*,bitmap_only=False):
        result=[json.loads(line) for line in self._raw(sql,bitmap_only=bitmap_only).splitlines() if line]
        self.metrics['sql_result_rows']+=len(result)
        return result

    def _select(self,collection,where,*,target='object_id',ids=None):
        if collection not in COLLECTIONS:raise ValueError('outside independent audit collection cut')
        query=("SELECT object_id,review_status,revision,content_sha256,retired_at,payload,"
               "xmin::text AS xmin,ctid::text AS ctid FROM wang_knowledge.objects "
               f"WHERE collection={literal(collection)} AND ({where}) ORDER BY object_id LIMIT {MAX_ROWS+1}")
        if ids is not None:
            # One full-key equality probe per requested key. Scalar-ARRAY index
            # scans may choose collection-only walks even on the PRIMARY KEY.
            key_condition=('object_id=wanted.id' if target=='object_id' else
                           "retired_at IS NULL AND payload->>'viewpoint_id'=wanted.id")
            query=("SELECT o.* FROM unnest(ARRAY["+','.join(literal(i) for i in ids)+"]::text[]) wanted(id) "
                "CROSS JOIN LATERAL (SELECT object_id,review_status,revision,content_sha256,retired_at,payload,"
                "xmin::text AS xmin,ctid::text AS ctid FROM wang_knowledge.objects "
                f"WHERE collection={literal(collection)} AND {key_condition} OFFSET 0) o "
                f"ORDER BY o.object_id LIMIT {MAX_ROWS+1}")
        # The stats row refuses excess rows/bytes BEFORE transferring payloads.
        statement=("WITH chosen AS MATERIALIZED ("+query+"), stats AS (SELECT count(*) n,"
            "coalesce(sum(octet_length(payload::text)),0) bytes FROM chosen) "
            "SELECT json_build_object('stats',json_build_object('rows',n,'bytes',bytes))::text FROM stats UNION ALL "
            "SELECT json_build_object('object_id',object_id,'review_status',review_status,'revision',revision,"
            "'content_sha256',content_sha256,'retired',retired_at IS NOT NULL,'payload',payload,"
            "'xmin',xmin,'ctid',ctid)::text FROM chosen WHERE (SELECT n<="+str(MAX_ROWS)+
            ' AND bytes<='+str(MAX_BYTES//2)+' FROM stats)')
        indexed_plan(json.loads(self._raw('EXPLAIN (FORMAT JSON) '+statement)),target)
        self.metrics['query_plans']+=1
        rows=self._plain(statement)
        stats=rows[0]['stats']
        if stats['rows']>MAX_ROWS or stats['bytes']>MAX_BYTES//2 or len(rows)-1!=stats['rows']:
            raise ValueError('audit dependency population exceeds bounded readset; never truncate')
        # UNION transfer order is not an input semantic; match old object order.
        return sorted(rows[1:],key=lambda row:row['object_id'])

    def _ids(self,collection,ids):
        ids=identifiers(ids)
        if not ids:return
        where='object_id = ANY(ARRAY['+','.join(literal(i) for i in ids)+']::text[])'
        rows=self._select(collection,where,ids=ids)
        if {row['object_id'] for row in rows}!=set(ids):
            raise ValueError('selected independent audit reference is missing; no partial frame')
        for row in rows:self.rows[collection][row['object_id']]=row

    def _load(self):
        indexes=self._plain("SELECT json_build_object('valid',i.indisvalid,'ready',i.indisready,"
            "'method',a.amname,'keys',i.indkey::text,'expression',pg_get_expr(i.indexprs,i.indrelid),"
            "'predicate',pg_get_expr(i.indpred,i.indrelid),'xmin',i.xmin::text,'ctid',i.ctid::text)::text "
            "FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_am a ON a.oid=c.relam "
            "WHERE i.indexrelid=to_regclass('wang_knowledge.objects_audit_viewpoint_members_idx') "
            "AND i.indrelid='wang_knowledge.objects'::regclass")
        if (len(indexes)!=1 or indexes[0]['valid'] is not True or indexes[0]['ready'] is not True
                or indexes[0]['method']!='btree' or indexes[0]['keys']!='0 2'
                or indexes[0]['expression']!="(payload ->> 'viewpoint_id'::text)"
                or indexes[0]['predicate']!="((collection = 'viewpoint_claim_links'::text) AND (retired_at IS NULL))"):
            raise ValueError('exact partial viewpoint-member index absent/changed; explicit maintenance required; no fallback')
        self.index_catalog=indexes[0]
        self._ids('canonical_viewpoints',self.viewpoint_ids)
        where="retired_at IS NULL AND payload->>'viewpoint_id' = ANY(ARRAY["+','.join(literal(i) for i in self.viewpoint_ids)+']::text[])'
        links=self._select('viewpoint_claim_links',where,target='viewpoint_id',ids=self.viewpoint_ids)
        for row in links:
            if row['payload'].get('effective_state') in (None,'active'):
                self.rows['viewpoint_claim_links'][row['object_id']]=row
        self._ids('viewpoint_revisions',[r['payload']['current_revision_id']
            for r in self.rows['canonical_viewpoints'].values() if r['payload'].get('current_revision_id')])
        self._ids('claims',self.claim_ids+[r['payload']['claim_id'] for r in self.rows['viewpoint_claim_links'].values()
                                        if r['payload'].get('claim_id')])
        eids=set()
        for row in self.rows['claims'].values():
            value=row['payload'];eids.update(value.get('evidence_step_ids') or [])
            eids.update(value.get('eligible_evidence_step_ids') or [])
        self._ids('evidence_steps',eids)
        fids=set()
        for row in self.rows['evidence_steps'].values():
            value=row['payload'];fids.update(value.get('source_fragment_ids') or [])
            if value.get('source_fragment_id'):fids.add(value['source_fragment_id'])
        for row in self.rows['viewpoint_claim_links'].values():
            fids.update(b['source_fragment_id'] for b in row['payload'].get('evidence_bindings') or [] if b.get('source_fragment_id'))
        self._ids('source_fragments',fids)
        self._ids('source_documents',{r['payload']['source_id'] for r in self.rows['source_fragments'].values()
                                      if r['payload'].get('source_id')})
        if sum(len(v) for v in self.rows.values())>MAX_ROWS:
            raise ValueError('complete audit closure exceeds hard object budget')

    def collection(self,name,*,include_retired=False):
        if name not in self.rows:raise ValueError('outside declared independent audit closure')
        # The full reader's row shape/order is preserved; current MVCC tokens
        # are separate proof data, not a different semantic input to the model.
        return [{k:deepcopy(v) for k,v in row.items() if k not in {'xmin','ctid','content_sha256'}}
                for _,row in sorted(self.rows[name].items()) if include_retired or not row['retired']]

    def state(self):
        return {'epoch':self.epoch,'index_catalog':self.index_catalog,'claim_ids':self.claim_ids,'viewpoint_ids':self.viewpoint_ids,'rows':self.rows}

    def assert_unchanged(self):
        remaining=self.seconds-(time.perf_counter()-self.started)
        if remaining<=0:raise ValueError('audit closing readset budget exceeded')
        current=AuditReadset(self.database_url,self.claim_ids,self.viewpoint_ids,seconds=remaining)
        if current.state()!=self.state():raise ValueError('current audit members/dependencies/version/retired/MVCC drift')
        return current.metrics
