"""Explicit full-cut certification, then bounded continuity observation.

This is NOT review/source/hold/SQL-application approval. No installer, model,
connection factory, normal-path rebuild fallback or production launcher exists.
The existing full Registry apply guard remains unchanged. Certificates bind an
actually recomputed complete cut to a journal observed in that same snapshot;
an old fingerprint plus a newly read token is never a certificate.
"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import time

from psycopg.pq import TransactionStatus

from backend.api.canonical_repository import integrity_journal as journal
from backend.api.canonical_repository.knowledge_models import KNOWLEDGE_COLLECTIONS
from backend.api.canonical_repository.postgres_store import canonical_json, record_content_sha
from backend.api.canonical_repository.viewpoint_foundation import sha256_json

SCHEMA='wang_complete_registry_integrity_baseline_v1'
MAX_CERTIFICATE_BYTES=128*1024
CODE=(Path(__file__),Path(journal.__file__),journal.INSTALL_PATH,
      Path(__file__).with_name('knowledge_models.py'),
      Path(__file__).with_name('postgres_store.py'),
      Path(__file__).with_name('viewpoint_foundation.py'))


@dataclass(frozen=True)
class BaselineLimits:
    seconds: float=10
    payload_bytes: int=512*1024**2
    rows: int=1000000
    value_bytes: int=8*1024**2

    def __post_init__(self):
        if (type(self.seconds) not in (float,int) or not 0<self.seconds<=10
                or any(type(v) is not int or v<=0 for v in
                       (self.payload_bytes,self.rows,self.value_bytes))
                or self.value_bytes>8*1024**2):
            raise ValueError('explicit positive baseline budgets with hard ten-second ceiling required')


def _pins():
    return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in CODE}


def _idle(connection):
    if connection.info.transaction_status!=TransactionStatus.IDLE:
        raise ValueError('fresh idle connection required; no inherited SQL snapshot')


def _sha(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value):
        raise ValueError('exact SHA256 required')


def _collections(value):
    if (not isinstance(value,list) or not value
            or any(not isinstance(c,str) or not c for c in value)
            or len(value)!=len(set(value))
            or not set(KNOWLEDGE_COLLECTIONS)<=set(value)):
        raise ValueError('complete explicit collection cut including all empty known collections required')
    return sorted(value)


def _snapshot(cursor,seconds):
    cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    cursor.execute("SELECT set_config('statement_timeout',%s,true),set_config('row_security','off',true)",
                   (str(max(1,int(seconds*1000))),))


class _DeadlineCursor:
    """Use the remaining TOTAL budget, not a fresh timeout for each query."""
    def __init__(self,cursor,remaining):self.cursor=cursor;self.remaining=remaining
    def execute(self,query,parameters=None):
        self.cursor.execute("SELECT set_config('statement_timeout',%s,true)",
                            (str(max(1,int(self.remaining()*1000))),))
        return self.cursor.execute(query,parameters)
    def fetchall(self):return self.cursor.fetchall()
    def fetchone(self):return self.cursor.fetchone()


def compile_baseline(connection,expected_sha256,collections,output,*,limits=BaselineLimits()):
    """Explicit maintenance only. Stream SQL once; never load N payloads.

    Both generation and complete payload cut use ONE RR read-only snapshot.
    A second fresh transaction rejects concurrent commits hidden by the first
    snapshot. No global/head lock is held through the scan. Output is exclusive
    and contains only a small certificate, not the full historical library.
    """
    from backend.pipeline.viewpoint_validation_context import require_explicit_history_maintenance
    require_explicit_history_maintenance('complete Registry integrity baseline certification')
    _sha(expected_sha256);collections=_collections(collections);_idle(connection)
    output=Path(output)
    if not output.is_absolute() or output.exists():
        raise ValueError('fresh absolute baseline output required; no overwrite/rebaseline fallback')
    started=time.perf_counter();pins=_pins()
    hashes={c:[] for c in collections};counts={c:0 for c in collections};active_collections=set()
    metrics=dict(sql_rows=0,active_payload_rows=0,canonical_payload_bytes=0)
    def budget():
        remaining=limits.seconds-(time.perf_counter()-started)
        if remaining<=0:
            raise ValueError('explicit baseline time ceiling exceeded; no fallback')
        return remaining
    budget()
    with connection.transaction():
        with connection.cursor() as cursor:
            _snapshot(cursor,budget());generation=journal.observe_generation(_DeadlineCursor(cursor,budget))
        # Server cursor bounds client memory. This full read is never a normal
        # consumer or a fallback, and includes retired rows to establish the
        # collection denominator without trusting a separate partial query.
        with connection.cursor(name='wkp_integrity_baseline') as cursor:
            cursor.execute('SELECT collection,object_id,revision,content_sha256, '
                           'CASE WHEN retired_at IS NULL AND octet_length(payload::text)<=%s THEN payload END, '
                           'retired_at IS NOT NULL FROM wang_knowledge.objects ORDER BY collection,object_id',
                           (limits.value_bytes,))
            while True:
                with connection.cursor() as control:
                    control.execute("SELECT set_config('statement_timeout',%s,true)",(str(max(1,int(budget()*1000))),))
                chunk=cursor.fetchmany(16)
                if not chunk:break
                for collection,oid,revision,content_sha,payload,retired in chunk:
                    metrics['sql_rows']+=1
                    if metrics['sql_rows']>limits.rows:raise ValueError('baseline row ceiling exceeded')
                    if retired:continue
                    if collection not in hashes:raise ValueError('unknown collection omitted from full cut')
                    active_collections.add(collection)
                    if not isinstance(payload,dict):raise ValueError('baseline payload missing/oversized')
                    raw=canonical_json(payload).encode('utf-8')
                    metrics['canonical_payload_bytes']+=len(raw)
                    if len(raw)>limits.value_bytes or metrics['canonical_payload_bytes']>limits.payload_bytes:
                        raise ValueError('baseline payload byte ceiling exceeded')
                    if (type(revision) is not int or revision<=0
                            or type(payload.get('revision')) is not int or payload['revision']!=revision
                            or record_content_sha(payload)!=content_sha.strip()):
                        raise ValueError('baseline payload/revision/content SHA drift')
                    if collection in KNOWLEDGE_COLLECTIONS:
                        if payload.get(KNOWLEDGE_COLLECTIONS[collection][1])!=oid:
                            raise ValueError('baseline payload identity drift')
                    hashes[collection].append(hashlib.sha256(raw).hexdigest())
                    counts[collection]+=1;metrics['active_payload_rows']+=1
                # Do not retain the previous decoded batch while psycopg
                # materializes the next one. Individual SQL values are capped.
                del chunk
        if set(collections)!=set(KNOWLEDGE_COLLECTIONS)|active_collections:
            raise ValueError('complete collection denominator drift; no undeclared extra empty cut')
        with connection.cursor() as cursor:
            journal.assert_unchanged_generation(_DeadlineCursor(cursor,budget),generation)
        observed=sha256_json({c:sorted(hashes[c]) for c in collections})
        if observed!=expected_sha256:raise ValueError('complete SQL cut differs from expected Registry fingerprint')
    budget()
    # It must be a NEW snapshot: RR in the first transaction alone would miss
    # an unrelated writer committed while the server cursor was being read.
    with connection.transaction():
        with connection.cursor() as cursor:
            _snapshot(cursor,budget())
            journal.assert_unchanged_generation(_DeadlineCursor(cursor,budget),generation)
    if _pins()!=pins:raise ValueError('baseline implementation changed during certification')
    budget()
    value=dict(schema_version=SCHEMA,status='complete_cut_continuity_only_not_admission',
        registry_fingerprint_sha256=observed,collections=collections,active_counts=counts,
        generation=generation,implementation_bindings=pins,metrics=metrics,
        semantic_approval_granted=False)
    value['artifact_sha256']=sha256_json(value)
    raw=canonical_json(value).encode('utf-8')
    if len(raw)>MAX_CERTIFICATE_BYTES:raise ValueError('baseline certificate size ceiling exceeded')
    with output.open('xb') as handle:handle.write(raw)
    return dict(path=str(output),file_sha256=hashlib.sha256(raw).hexdigest())


def read_baseline(binding,*,expected_sha256,collections):
    """Authenticate a bounded saved baseline; makes NO current SQL assertion."""
    _sha(expected_sha256);collections=_collections(collections)
    if not isinstance(binding,dict) or set(binding)!={'path','file_sha256'}:
        raise ValueError('exact physical baseline binding required')
    _sha(binding['file_sha256']);path=Path(binding['path'])
    if not path.is_absolute() or path.stat().st_size>MAX_CERTIFICATE_BYTES:
        raise ValueError('bounded absolute baseline certificate required')
    # Bound the read itself, not just a pre-read stat that a replacement/growth
    # could invalidate. Physical SHA and size are checked on these exact bytes.
    with path.open('rb') as handle:raw=handle.read(MAX_CERTIFICATE_BYTES+1)
    if len(raw)>MAX_CERTIFICATE_BYTES or hashlib.sha256(raw).hexdigest()!=binding['file_sha256']:
        raise ValueError('baseline physical SHA/size drift')
    value=json.loads(raw);seal=value.pop('artifact_sha256',None)
    if (set(value)!={'schema_version','status','registry_fingerprint_sha256','collections','active_counts',
                    'generation','implementation_bindings','metrics','semantic_approval_granted'}
            or seal!=sha256_json(value) or value['schema_version']!=SCHEMA
            or value['status']!='complete_cut_continuity_only_not_admission'
            or value['semantic_approval_granted'] is not False
            or value['registry_fingerprint_sha256']!=expected_sha256
            or value['collections']!=collections or set(value['active_counts'])!=set(collections)
            or any(type(n) is not int or n<0 for n in value['active_counts'].values())
            or not isinstance(value['metrics'],dict)
            or set(value['metrics'])!={'sql_rows','active_payload_rows','canonical_payload_bytes'}
            or any(type(n) is not int or n<0 for n in value['metrics'].values())
            or value['metrics']['active_payload_rows']!=sum(value['active_counts'].values())
            or value['metrics']['sql_rows']<value['metrics']['active_payload_rows']
            or value['implementation_bindings']!=_pins()):
        raise ValueError('baseline seal/cut/implementation drift')
    return value


def consume_baseline(connection,binding,*,expected_sha256,collections):
    """Small certificate plus fresh bounded catalog/head reads, not admission.

    Does not query object payloads, journal history, archived snapshots or any
    reviewer. A miss/change is a stop, NEVER an implicit rebaseline. The later
    owning write transaction still needs its exact reviewed plan and fresh
    original source/hold/current/SQL guards at its linearization boundary.
    """
    started=time.perf_counter()
    def remaining():
        value=5-(time.perf_counter()-started)
        if value<=0:raise ValueError('bounded baseline consumption time ceiling exceeded; no fallback')
        return value
    _idle(connection)
    value=read_baseline(binding,expected_sha256=expected_sha256,collections=collections)
    with connection.transaction():
        with connection.cursor() as cursor:
            _snapshot(cursor,remaining())
            journal.assert_unchanged_generation(_DeadlineCursor(cursor,remaining),value['generation'])
    remaining()
    return dict(status='unchanged_complete_cut_observed',registry_fingerprint_sha256=expected_sha256,
                generation=value['generation'],semantic_approval_granted=False)
