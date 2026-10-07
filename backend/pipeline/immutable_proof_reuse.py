"""Process-local saved-proof authentication only; never cache current SQL states."""
from pathlib import Path
from backend.pipeline.source_reconciliation import verify_file

_verified = {}
MIN_SNAPSHOT_BYTES=8*1024**2


def pack_snapshot(path,value):
    import hashlib,json,os
    from backend.api.canonical_repository.viewpoint_foundation import canonical_json
    path=Path(path)
    if 'source-reconciliation' not in path.parts:return value
    index=path.parts.index('source-reconciliation');base=Path(*path.parts[:index+1])/'immutable-snapshots'
    packed=dict(value)
    for field in ('before_records','post_records','records'):
        if not isinstance(value.get(field),list):continue
        raw=canonical_json(value[field]).encode('utf-8')
        if len(raw)<MIN_SNAPSHOT_BYTES:continue
        sha=hashlib.sha256(raw).hexdigest();base.mkdir(exist_ok=True);target=base/(sha+'.rows.json')
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest()!=sha:raise ValueError('shared records SHA drift')
        else:
            with target.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        packed[field]={'wkp_shared_records_v1':sha,'size':len(raw)}
    return packed


def restore_snapshot(path,value):
    import hashlib,json
    path=Path(path);value=dict(value)
    for field in ('before_records','post_records','records'):
        ref=value.get(field)
        if not isinstance(ref,dict) or 'wkp_shared_records_v1' not in ref:continue
        if 'source-reconciliation' not in path.parts:raise ValueError('shared records outside owning root')
        sha=ref['wkp_shared_records_v1']
        if len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha) or set(ref)!={'wkp_shared_records_v1','size'}:
            raise ValueError('invalid shared records reference')
        index=path.parts.index('source-reconciliation');base=Path(*path.parts[:index+1])/'immutable-snapshots'
        raw=(base/(sha+'.rows.json')).read_bytes()
        if len(raw)!=ref['size'] or hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('shared records size/SHA drift')
        rows=json.loads(raw)
        if not isinstance(rows,list):raise ValueError('shared records not rows')
        value[field]=rows
    return value


def verify_once(identity, bindings, store, verify):
    # Rehash every bound physical file on every reuse. SQL WAL invalidates
    # historical retired-row inputs too; actual current gates remain outside.
    pins=tuple((str(verify_file(b)),b['file_sha256']) for b in bindings)
    with store.connect() as conn,conn.cursor() as cursor:
        cursor.execute('SET TRANSACTION READ ONLY')
        cursor.execute('SELECT pg_current_wal_lsn()::text')
        generation=cursor.fetchone()[0]
    key=(identity,pins,generation)
    if key in _verified:return
    verify()
    # Bounded cache avoids retaining entire snapshots/proofs or stale epochs.
    if len(_verified)>=8:_verified.clear()
    _verified[key]=True


def link_new_snapshot(path, raw):
    """New bytes only; preserve all logical paths/physical SHA and old files."""
    import hashlib,os
    path=Path(path)
    if len(raw)<MIN_SNAPSHOT_BYTES or 'source-reconciliation' not in path.parts:return False
    index=path.parts.index('source-reconciliation')
    base=Path(*path.parts[:index+1]); sha=hashlib.sha256(raw).hexdigest()
    blobs=base/'immutable-snapshots';blobs.mkdir(exist_ok=True)
    target=blobs/(sha+'.json')
    if target.exists():
        with target.open('rb') as f:
            if hashlib.file_digest(f,'sha256').hexdigest()!=sha:raise ValueError('shared snapshot SHA drift')
    else:
        try:
            with target.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        except FileExistsError:
            with target.open('rb') as f:
                if hashlib.file_digest(f,'sha256').hexdigest()!=sha:raise ValueError('shared snapshot race/drift')
    os.link(target,path)
    descriptor=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)
    return True
