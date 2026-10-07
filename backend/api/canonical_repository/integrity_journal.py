"""Bounded global-generation and exact transactional write-set observations.

No installer, connection, model, semantic approval or full-snapshot fallback.
An unchanged generation proves continuity ONLY from a separately certified
complete baseline captured in the same stable SQL snapshot. Reading a token
today cannot attach it to an old fingerprint or retroactively approve history.
Existing full Registry fingerprint/apply gates remain until that admission
contract and actual scalability acceptance are implemented.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import re
import uuid

from backend.api.canonical_repository.viewpoint_foundation import sha256_json

INSTALL_PATH=Path(__file__).with_name('integrity_journal_install.sql')
TABLES=('objects','integrity_head','integrity_changes')
FUNCTIONS={
    'object_integrity_signature':('sql','s',False,['search_path=pg_catalog','TimeZone=UTC']),
    'capture_object_integrity_change':('plpgsql','v',True,['search_path=pg_catalog']),
    'guard_integrity_metadata_write':('plpgsql','v',False,['search_path=pg_catalog']),
    'deny_integrity_truncate':('plpgsql','v',False,['search_path=pg_catalog']),
}
TRIGGERS={
    ('objects','integrity_capture'):('capture_object_integrity_change',29),
    ('objects','integrity_objects_no_truncate'):('deny_integrity_truncate',34),
    ('integrity_head','integrity_head_write_guard'):('guard_integrity_metadata_write',31),
    ('integrity_changes','integrity_changes_write_guard'):('guard_integrity_metadata_write',31),
    ('integrity_head','integrity_head_no_truncate'):('deny_integrity_truncate',34),
    ('integrity_changes','integrity_changes_no_truncate'):('deny_integrity_truncate',34),
}
MAX_DELTA_ROWS=10000


def _declared_bodies():
    text=INSTALL_PATH.read_text(encoding='utf-8')
    result=dict(re.findall(r'CREATE FUNCTION wang_knowledge\.(\w+)\([^;]*?AS \$wkp\$(.*?)\$wkp\$;',text,re.S))
    if set(result)!=set(FUNCTIONS):raise ValueError('integrity installation code denominator drift')
    return result


def observe_generation(cursor, *, lock=False):
    """Constant-sized catalog/head read. Caller owns RR/read-only/time budget.

    Physical catalog generations catch disable/re-enable or replace/restore,
    not just the final text of a trigger. Engine/filenode identity catches a
    restore/restart. DDL invalidates the old baseline; no automatic rebaseline.
    `lock=True` is only for the short owning write transaction, never a model
    call or long shared read lock.
    """
    declared=_declared_bodies()
    cursor.execute('''SELECT p.proname,l.lanname,p.provolatile,p.prosecdef,p.proconfig,
        p.prosrc,p.xmin::text,p.ctid::text,p.oid
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        JOIN pg_language l ON l.oid=p.prolang
        WHERE n.nspname='wang_knowledge' AND p.proname=ANY(%s) ORDER BY p.proname''',
        (list(FUNCTIONS),))
    functions=list(cursor.fetchall())
    if len(functions)!=len(FUNCTIONS) or {r[0] for r in functions}!=set(FUNCTIONS):
        raise ValueError('integrity functions absent/ambiguous')
    for name,language,volatility,definer,config,source,*_ in functions:
        if ((language,volatility,definer,config)!=FUNCTIONS.get(name)
                or source!=declared.get(name)):
            raise ValueError('integrity function definition/config drift')
    function_ids={row[0]:row[-1] for row in functions}
    cursor.execute('''SELECT c.relname,t.tgname,p.proname,t.tgtype,t.tgenabled,p.oid,
        t.tgqual IS NULL,t.tgnargs,t.tgconstraint,t.tgdeferrable,t.tginitdeferred,
        t.xmin::text,t.ctid::text,t.oid,pg_get_triggerdef(t.oid)
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE n.nspname='wang_knowledge' AND c.relname=ANY(%s) AND NOT t.tgisinternal
        ORDER BY c.relname,t.tgname''',(list(TABLES),))
    triggers=list(cursor.fetchall())
    if len(triggers)!=len(TRIGGERS):raise ValueError('integrity trigger denominator drift')
    for table,name,function,kind,enabled,function_id,no_condition,nargs,constraint,deferred,initially_deferred,*_ in triggers:
        if (TRIGGERS.get((table,name))!=(function,kind) or enabled!='A'
                or function_id!=function_ids.get(function)
                or (no_condition,nargs,constraint,deferred,initially_deferred)!=(True,0,0,False,False)):
            raise ValueError('integrity trigger absent/disabled/changed')
    cursor.execute('''SELECT c.relname,i.indkey::text,i.indisprimary,i.indisvalid,
        i.indisready,a.amname,pg_get_indexdef(i.indexrelid),i.xmin::text,i.ctid::text
        FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_class index_table ON index_table.oid=i.indexrelid
        JOIN pg_am a ON a.oid=index_table.relam
        WHERE n.nspname='wang_knowledge' AND c.relname=ANY(%s) ORDER BY c.relname''',
        (['integrity_head','integrity_changes'],))
    indexes=list(cursor.fetchall())
    if (len(indexes)!=2 or [(r[0],r[1],r[2],r[3],r[4],r[5]) for r in indexes]!=[
            ('integrity_changes','1 2',True,True,True,'btree'),
            ('integrity_head','1',True,True,True,'btree')]):
        raise ValueError('integrity primary-key index missing/invalid; no H scan fallback')
    # Schema-only metadata stays bounded independently of object/history rows.
    # Attribute/default/constraint/policy changes must not leave an old global
    # token valid merely because no row-level trigger fired for that DDL.
    cursor.execute('''WITH tables AS (SELECT c.* FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='wang_knowledge' AND c.relname=ANY(%s))
        SELECT relname,'column',a.attnum::text,
          row(a.attname,a.atttypid,a.atttypmod,a.attcollation,a.attnotnull,a.attgenerated,a.attidentity)::text,
          a.xmin::text,a.ctid::text FROM tables c JOIN pg_attribute a ON a.attrelid=c.oid WHERE a.attnum>0
        UNION ALL SELECT relname,'default',d.adnum::text,pg_get_expr(d.adbin,d.adrelid),d.xmin::text,d.ctid::text
          FROM tables c JOIN pg_attrdef d ON d.adrelid=c.oid
        UNION ALL SELECT relname,'constraint',k.conname,pg_get_constraintdef(k.oid),k.xmin::text,k.ctid::text
          FROM tables c JOIN pg_constraint k ON k.conrelid=c.oid
        UNION ALL SELECT relname,'policy',p.polname,row(p.polcmd,p.polroles,p.polqual::text,p.polwithcheck::text)::text,
          p.xmin::text,p.ctid::text FROM tables c JOIN pg_policy p ON p.polrelid=c.oid
        UNION ALL SELECT relname,'table','flags',row(c.relowner,c.relrowsecurity,c.relforcerowsecurity)::text,'','' FROM tables c
        UNION ALL SELECT relname,'index',i.indexrelid::text,row(i.indisvalid,i.indisready,pg_get_indexdef(i.indexrelid))::text,
          i.xmin::text,i.ctid::text FROM tables c JOIN pg_index i ON i.indrelid=c.oid
        ORDER BY 1,2,3''',(list(TABLES),))
    schema=list(cursor.fetchall())
    cursor.execute('''SELECT current_database(),extract(epoch FROM pg_postmaster_start_time())::text,
        floor(pg_snapshot_xmax(pg_current_snapshot())::text::numeric/4294967296)::text,
        ARRAY(SELECT c.relname||':'||c.oid::text||':'||pg_relation_filenode(c.oid)::text
          FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
          WHERE n.nspname='wang_knowledge' AND c.relname=ANY(%s) ORDER BY c.relname)''',
        (list(TABLES),))
    storage=cursor.fetchone()
    if not storage or len(storage[-1])!=len(TABLES):raise ValueError('integrity storage identity missing')
    cursor.execute('SELECT journal_id::text,sequence FROM wang_knowledge.integrity_head '
                   'WHERE singleton=1'+(' FOR UPDATE' if lock else ''))
    head=cursor.fetchone()
    if not head or type(head[1]) is not int or head[1]<0:raise ValueError('integrity head missing/invalid')
    return dict(schema_version='wang_registry_integrity_generation_v1',
        journal_id=head[0],sequence=head[1],storage=list(storage),
        catalog_sha256=sha256_json(dict(functions=functions,triggers=triggers,indexes=indexes,schema=schema)),
        installation_sha256=hashlib.sha256(INSTALL_PATH.read_bytes()).hexdigest())


def assert_unchanged_generation(cursor, expected):
    _generation(expected)
    observed=observe_generation(cursor)
    if observed!=expected:raise ValueError('global Registry generation changed; no full-table fallback')
    return observed


def read_object_signatures(cursor, keys):
    """Fresh exact-ID positive/negative observation, including retired rows."""
    if (not isinstance(keys,list) or not keys
            or any(not isinstance(k,tuple) or len(k)!=2 or any(not isinstance(s,str) or not s for s in k) for k in keys)
            or len(set(keys))!=len(keys)):
        raise ValueError('nonempty unique exact object keys required')
    keys=sorted(keys)
    cursor.execute('''SELECT wanted.collection,wanted.object_id,
        CASE WHEN o.object_id IS NOT NULL THEN wang_knowledge.object_integrity_signature(o) END
        FROM unnest(%s::text[],%s::text[]) AS wanted(collection,object_id)
        LEFT JOIN wang_knowledge.objects o
        ON o.collection=wanted.collection AND o.object_id=wanted.object_id
        ORDER BY wanted.collection,wanted.object_id''',
        ([k[0] for k in keys],[k[1] for k in keys]))
    rows=list(cursor.fetchall())
    if [(r[0],r[1]) for r in rows]!=keys:raise ValueError('object signature population drift')
    for row in rows:_signature(row[2])
    return {(collection,oid):signature for collection,oid,signature in rows}


def _generation(value):
    if (not isinstance(value,dict) or set(value)!={'schema_version','journal_id','sequence','storage','catalog_sha256','installation_sha256'}
            or value['schema_version']!='wang_registry_integrity_generation_v1'
            or type(value['sequence']) is not int or value['sequence']<0
            or not isinstance(value['storage'],list) or len(value['storage'])!=4
            or not isinstance(value['storage'][-1],list) or len(value['storage'][-1])!=3
            or any(not isinstance(value[k],str) or not re.fullmatch('[0-9a-f]{64}',value[k])
                   for k in ('catalog_sha256','installation_sha256'))):
        raise ValueError('exact integrity generation shape required')
    try:
        if str(uuid.UUID(value['journal_id']))!=value['journal_id']:raise ValueError()
    except (ValueError,TypeError,AttributeError):
        raise ValueError('canonical integrity journal identity required') from None


def _signature(value):
    if value is None:return
    if (not isinstance(value,dict) or set(value)!={'revision','content_sha256','retired','row_sha256'}
            or type(value['revision']) is not int or value['revision']<=0
            or type(value['retired']) is not bool
            or any(not isinstance(value[k],str) or not re.fullmatch('[0-9a-f]{64}',value[k])
                   for k in ('content_sha256','row_sha256'))):
        raise ValueError('exact physical object signature required')


def verify_transaction_delta(cursor, before, after, expected_before, expected_after):
    """Prove ALL object writes were in this declared exact set, not approval.

    Expected signatures come from fresh exact SQL observations and original
    reviewed plan/postimage guards. All modifications, including direct SQL,
    retirement and deletion, increment the same transactional head. Rollback
    rolls back both the object mutation and its journal; there is no sequence
    allocation outside the owning transaction. Extra/gapped/multi-tx writes
    fail before any admission, and oversized intervals fail before reading H.
    """
    _generation(before);_generation(after)
    if ({k:v for k,v in before.items() if k!='sequence'}
                !={k:v for k,v in after.items() if k!='sequence'}):
        raise ValueError('integrity lineage/catalog/installation changed')
    if (type(before['sequence']) is not int or type(after['sequence']) is not int
            or not 0<=before['sequence']<after['sequence']
            or after['sequence']-before['sequence']>MAX_DELTA_ROWS):
        raise ValueError('bounded positive transaction interval required; no H fallback')
    if (not isinstance(expected_before,dict) or not expected_before
            or not isinstance(expected_after,dict) or set(expected_before)!=set(expected_after)
            or any(not isinstance(k,tuple) or len(k)!=2 or any(not isinstance(s,str) or not s for s in k)
                   for k in expected_before)):
        raise ValueError('exact nonempty written-key denominator required')
    for value in (*expected_before.values(),*expected_after.values()):_signature(value)
    cursor.execute('''SELECT sequence,transaction_id,operation,before_collection,before_object_id,
        after_collection,after_object_id,before_signature,after_signature
        FROM wang_knowledge.integrity_changes
        WHERE journal_id=%s::uuid AND sequence>%s AND sequence<=%s ORDER BY sequence''',
        (before['journal_id'],before['sequence'],after['sequence']))
    rows=list(cursor.fetchall())
    if len(rows)!=after['sequence']-before['sequence']:raise ValueError('integrity interval gap')
    states=deepcopy(expected_before); touched=set(); tx=None
    for number,row in enumerate(rows,before['sequence']+1):
        sequence,transaction,operation,bc,bi,ac,ai,old,new=row
        if sequence!=number or not transaction or tx not in (None,transaction):
            raise ValueError('integrity order/multiple transaction drift')
        tx=transaction;_signature(old);_signature(new)
        old_key=(bc,bi) if old is not None else None
        new_key=(ac,ai) if new is not None else None
        if ((operation=='INSERT' and (old_key is not None or new_key is None or bc is not None or bi is not None))
                or (operation=='DELETE' and (old_key is None or new_key is not None or ac is not None or ai is not None))
                or (operation=='UPDATE' and (old_key is None or old_key!=new_key))
                or operation not in {'INSERT','UPDATE','DELETE'}):
            raise ValueError('integrity operation/identity population drift')
        key=new_key or old_key
        if key not in states or states[key]!=old:raise ValueError('unplanned/non-target write or predecessor signature drift')
        states[key]=new;touched.add(key)
    if touched!=set(expected_before) or states!=expected_after:
        raise ValueError('actual written population/final signature drift')
    return dict(status='exact_transaction_write_set_observed',rows=len(rows),written_keys=len(touched),
                semantic_approval_granted=False)
