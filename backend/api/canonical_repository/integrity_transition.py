"""Committed exact SQL-plan observations from a certified complete baseline.

No writes, installer, model, semantic approval, legacy-fingerprint replacement
or full-history fallback. This indexed local primitive is NOT yet production
admission: original source/raw/review/hold/application gates remain mandatory.
It does not claim the predecessor's legacy fingerprint is the NEW fingerprint.
"""
from dataclasses import asdict
from pathlib import Path
import hashlib
import re
import time

from backend.api.canonical_repository import integrity_baseline as baseline, integrity_journal as journal
from backend.api.canonical_repository.postgres_store import (
    PostgresKnowledgeStore, ChangeSetPlan, validate_change_set_plan_integrity,
    stored_operation_payload, canonical_json,
)
from backend.api.canonical_repository.viewpoint_foundation import sha256_json

INDEX_INSTALL_PATH=Path(__file__).with_name('integrity_ledger_indexes.sql')
CODE=(*baseline.CODE,Path(__file__),INDEX_INSTALL_PATH)
MAX_OPERATIONS=1000
MAX_INPUT_BYTES=8*1024**2
SCHEMA='wang_committed_registry_transition_observation_v1'


def _indexed_snapshot(cursor,seconds):
    baseline._snapshot(cursor,seconds)
    # Only this short owned read transaction: no ALTER SYSTEM/session-global
    # planner mutation. Costs alone may choose a full H scan for a tiny table.
    # Explicit EXPLAIN veto below still stops if no legal indexed plan exists.
    cursor.execute('SET LOCAL enable_seqscan=off')
    cursor.execute('SET LOCAL enable_indexscan=on')
    cursor.execute('SET LOCAL enable_bitmapscan=on')


class _IndexedCursor(baseline._DeadlineCursor):
    def execute(self,query,parameters=None):
        tables=set(re.findall(r'\b(?:FROM|JOIN)\s+wang_knowledge\.(objects|object_versions|change_sets|change_operations|integrity_changes)\b',query,re.I))
        if tables:
            super().execute('EXPLAIN (FORMAT JSON) '+query,parameters)
            result=self.cursor.fetchone()[0]
            def indexed(node):
                if (node.get('Relation Name') in tables and node.get('Node Type') in {'Seq Scan','Parallel Seq Scan'}):
                    raise ValueError('actual ledger/object plan would full-scan N/H; no fallback')
                if (node.get('Relation Name') in tables
                        and node.get('Node Type') in {'Index Scan','Index Only Scan','Bitmap Heap Scan'}
                        and not (node.get('Index Cond') or node.get('Recheck Cond'))):
                    raise ValueError('actual ledger/object plan would full-scan an index over N/H; no fallback')
                for child in node.get('Plans',[]):indexed(child)
            for root in result:indexed(root['Plan'])
        return super().execute(query,parameters)


def _pins():return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in CODE}


def _ledger_indexes(cursor):
    wanted=[('change_sets','change_sets_pkey','1',True),
            ('change_operations','change_operations_pkey','1 2',True),
            ('object_versions','integrity_object_versions_change_set_idx','6 1 2 3',False),
            ('objects','objects_pkey','1 2',True)]
    cursor.execute('''SELECT c.relname,idx.relname,i.indkey::text,i.indisprimary,
        i.indisvalid,i.indisready,a.amname,i.indpred IS NULL,i.indexprs IS NULL,
        i.xmin::text,i.ctid::text,pg_get_indexdef(i.indexrelid)
        FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_class idx ON idx.oid=i.indexrelid JOIN pg_am a ON a.oid=idx.relam
        WHERE n.nspname='wang_knowledge' AND idx.relname=ANY(%s) ORDER BY c.relname''',
        ([r[1] for r in wanted],))
    rows=list(cursor.fetchall())
    expected=sorted(wanted)
    if (len(rows)!=len(expected) or [tuple(r[:4]) for r in rows]!=expected
            or any(tuple(r[4:9])!=(True,True,'btree',True,True) for r in rows)):
        raise ValueError('required exact ledger index absent/invalid; no history SeqScan fallback')
    return sha256_json(rows)


def _ledger_rows(cursor,kcs):
    cursor.execute('''SELECT 'kcs',change_set_id,xmin::text,ctid::text FROM wang_knowledge.change_sets WHERE change_set_id=%s
        UNION ALL SELECT 'operation',operation_index::text,xmin::text,ctid::text
          FROM wang_knowledge.change_operations WHERE change_set_id=%s
        UNION ALL SELECT 'version',collection||'/'||object_id||'/'||revision::text,xmin::text,ctid::text
          FROM wang_knowledge.object_versions WHERE change_set_id=%s ORDER BY 1,2,3,4''',(kcs,kcs,kcs))
    return list(cursor.fetchall())


def _bounded_ledger(cursor,plan):
    # Required indexes have already been authenticated. Refuse corrupt/extra
    # rows and oversized actual SQL payloads BEFORE the existing exact helper
    # can fetch them, not after an unbounded transfer has happened.
    for table in ('change_operations','object_versions'):
        cursor.execute('SELECT count(*) FROM (SELECT 1 FROM wang_knowledge.'+table+
                       ' WHERE change_set_id=%s LIMIT %s) limited',(plan.change_set_id,MAX_OPERATIONS+1))
        if cursor.fetchone()[0]!=len(plan.operations):raise ValueError('bounded exact ledger denominator drift')
    cursor.execute('''SELECT octet_length(metadata::text)+octet_length(summary::text)
        FROM wang_knowledge.change_sets WHERE change_set_id=%s''',(plan.change_set_id,))
    size=cursor.fetchone()
    if not size or size[0]>MAX_INPUT_BYTES:raise ValueError('missing/oversized actual ChangeSet metadata')
    cursor.execute('SELECT sum(octet_length(payload::text)) FROM wang_knowledge.object_versions WHERE change_set_id=%s',
                   (plan.change_set_id,))
    if cursor.fetchone()[0]>MAX_INPUT_BYTES:raise ValueError('oversized actual version payloads')
    cursor.execute('''SELECT count(*),sum(octet_length(o.payload::text))
        FROM unnest(%s::text[],%s::text[]) wanted(collection,object_id)
        JOIN wang_knowledge.objects o ON o.collection=wanted.collection AND o.object_id=wanted.object_id''',
        ([o.collection for o in plan.operations],[o.object_id for o in plan.operations]))
    count,size=cursor.fetchone()
    if count!=len(plan.operations) or size>MAX_INPUT_BYTES:
        raise ValueError('missing/oversized actual object postimage')


def observe_committed_plan(connection,baseline_binding,*,baseline_sha256,collections,
                           plan,plan_sha256,metadata,expected_before,expected_after,output):
    """Observe a committed plan using O(D) indexed rows, never apply/replay it.

    All actual objects writes since the certified baseline must be exactly the
    declared single transaction. Actual KCS/operation/version rows and current
    postimages must match the pinned plan. Ledger xmin must be the actual
    journal transaction, not metadata retrofitted in a later transaction.
    A fresh second SQL snapshot rejects commits hidden by the first RR read.
    Existing source/model/hold/backup/exact-once approval is NOT supplied here.
    """
    baseline._idle(connection);baseline._sha(plan_sha256)
    if (not isinstance(plan,ChangeSetPlan) or not 0<len(plan.operations)<=MAX_OPERATIONS
            or plan.review_events):
        # A separate bounded exact event-ledger authority is still required.
        # Never silently omit an event merely because it is not an object row.
        raise ValueError('bounded event-free plan required; unsupported ledger authority stops')
    frozen=asdict(plan)
    if sha256_json(frozen)!=plan_sha256:raise ValueError('exact plan content SHA drift')
    if not isinstance(metadata,dict) or len(canonical_json([frozen,metadata]).encode())>MAX_INPUT_BYTES:
        raise ValueError('bounded complete plan/metadata required')
    validate_change_set_plan_integrity(plan)
    keys=[(o.collection,o.object_id) for o in plan.operations]
    if (len(set(keys))!=len(keys) or any(o.operation not in {'create','update','retire'} for o in plan.operations)
            or not isinstance(expected_before,dict) or not isinstance(expected_after,dict)
            or set(expected_before)!=set(keys) or set(expected_after)!=set(keys)):
        raise ValueError('exact unique supported plan write denominator required')
    for op in plan.operations:
        old=expected_before[(op.collection,op.object_id)];new=expected_after[(op.collection,op.object_id)]
        journal._signature(old);journal._signature(new)
        if (new is None or new['revision']!=op.after_revision or new['content_sha256']!=op.after_sha256
                or new['retired']!=(op.operation=='retire')
                or (op.operation=='create' and (old is not None or op.before_revision is not None or op.before_sha256 is not None))
                or (op.operation!='create' and (old is None or old['revision']!=op.before_revision
                                               or old['content_sha256']!=op.before_sha256))):
            raise ValueError('plan and exact physical write signatures disagree')
    output=Path(output)
    if not output.is_absolute() or output.exists():raise ValueError('fresh absolute observation output required')
    started=time.perf_counter();pins=_pins()
    def remaining():
        left=5-(time.perf_counter()-started)
        if left<=0:raise ValueError('bounded committed-plan observation ceiling exceeded; no fallback')
        return left
    saved=baseline.read_baseline(baseline_binding,expected_sha256=baseline_sha256,collections=collections)
    summary=plan.as_dict()['summary']
    with connection.transaction():
        with connection.cursor() as raw:
            _indexed_snapshot(raw,remaining());cursor=_IndexedCursor(raw,remaining)
            index_sha=_ledger_indexes(cursor)
            after=journal.observe_generation(cursor)
            delta=journal.verify_transaction_delta(cursor,saved['generation'],after,expected_before,expected_after)
            if delta['rows']!=len(keys):
                raise ValueError('objects write count differs from the single-operation exact plan')
            tx32=str(int(delta['transaction_id'])%4294967296)
            _bounded_ledger(cursor,plan)
            PostgresKnowledgeStore._assert_change_set_write_ledger(cursor,plan,metadata,summary)
            PostgresKnowledgeStore._assert_change_set_object_readback(cursor,plan)
            if journal.read_object_signatures(cursor,keys)!=expected_after:
                raise ValueError('fresh physical postimage differs from declared readback')
            generations=_ledger_rows(cursor,plan.change_set_id)
            if (len(generations)!=1+2*len(keys)
                    or any(row[2]!=tx32 for row in generations)
                    or sum(r[0]=='kcs' for r in generations)!=1
                    or sum(r[0]=='operation' for r in generations)!=len(keys)
                    or sum(r[0]=='version' for r in generations)!=len(keys)):
                raise ValueError('KCS/operation/version was not committed in exact objects transaction')
    with connection.transaction():
        with connection.cursor() as raw:
            _indexed_snapshot(raw,remaining());cursor=_IndexedCursor(raw,remaining)
            journal.assert_unchanged_generation(cursor,after)
            if _ledger_indexes(cursor)!=index_sha:raise ValueError('ledger access-path generation drift')
            _bounded_ledger(cursor,plan)
            if _ledger_rows(cursor,plan.change_set_id)!=generations:
                raise ValueError('committed SQL ledger changed during observation')
    if pins!=_pins() or sha256_json(asdict(plan))!=plan_sha256:
        raise ValueError('observation code/plan changed during SQL certification')
    remaining()
    value=dict(schema_version=SCHEMA,status='committed_plan_objects_continuity_only_not_admission',
        baseline_binding=dict(baseline_binding),baseline_registry_fingerprint_sha256=baseline_sha256,
        before_generation=saved['generation'],after_generation=after,change_set_id=plan.change_set_id,
        plan_sha256=plan_sha256,metadata_sha256=sha256_json(metadata),ledger_index_sha256=index_sha,
        actual_transaction=delta,implementation_bindings=pins,semantic_approval_granted=False,
        legacy_post_registry_fingerprint_sha256=None)
    value['artifact_sha256']=sha256_json(value);data=canonical_json(value).encode()
    if len(data)>baseline.MAX_CERTIFICATE_BYTES:raise ValueError('bounded observation certificate size exceeded')
    with output.open('xb') as handle:handle.write(data)
    return dict(path=str(output),file_sha256=hashlib.sha256(data).hexdigest())
