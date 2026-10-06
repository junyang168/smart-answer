"""One bounded validation phase, not a process-global cache or approval receipt.

Historical validators still run their complete existing checks. Within a phase
they share authenticated bytes and immutable results instead of recursively
reloading history. All consumed files are rehashed at the phase boundary. Live
SQL row generations are authenticated anew for each read-only phase. Immutable
data may be reused only when that PostgreSQL generation matches; current check results
never cross a phase, model call or write.
"""
from __future__ import annotations

from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps
import hashlib
import inspect
import json
from pathlib import Path
import time

_current = ContextVar('cvp_validation_context', default=None)
_pool = ContextVar('cvp_historical_proof_pool', default=None)


class HistoricalProofPool:
    """Owned by one campaign runtime; never persisted or shared with a writer."""
    def __init__(self):
        self.documents = {}
        self.results = {}
        self.phase_metrics = []
        self.registry_data = {}
        self.registry_cuts = {}

    def clear(self):
        self.documents.clear()
        self.results.clear()
        self.registry_data.clear()
        self.registry_cuts.clear()


@contextmanager
def validation_runtime():
    if _pool.get() is not None:
        raise ValueError('one owner per validation runtime')
    pool = HistoricalProofPool()
    token = _pool.set(pool)
    try:
        yield pool
    finally:
        pool.clear()
        _pool.reset(token)


class ReadOnlyDict(dict):
    def _deny(self, *args, **kwargs):
        raise TypeError('validated history is read-only')
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _deny

    def __deepcopy__(self, memo):
        from copy import deepcopy
        return {deepcopy(k, memo): deepcopy(v, memo) for k, v in self.items()}


class ReadOnlyList(list):
    def _deny(self, *args, **kwargs):
        raise TypeError('validated history is read-only')
    __setitem__ = __delitem__ = append = extend = insert = pop = remove = clear = reverse = sort = __iadd__ = __imul__ = _deny

    def __deepcopy__(self, memo):
        from copy import deepcopy
        return [deepcopy(v, memo) for v in self]


class RecordSnapshot(ReadOnlyDict):
    """Copy the index for simulations; immutable unmodified rows are shared."""
    def __deepcopy__(self, memo):
        return dict(self)


def readonly(value, memo=None):
    if isinstance(value, (ReadOnlyDict, ReadOnlyList)):
        return value
    memo = {} if memo is None else memo
    if id(value) in memo:
        return memo[id(value)]
    if isinstance(value, dict):
        cls = RecordSnapshot if value and all(isinstance(k, tuple) and len(k) == 2 for k in value) else ReadOnlyDict
        result = cls()
        memo[id(value)] = result
        for k, v in value.items():
            dict.__setitem__(result, k, readonly(v, memo))
        return result
    if isinstance(value, list):
        result = ReadOnlyList()
        memo[id(value)] = result
        for v in value:
            list.append(result, readonly(v, memo))
        return result
    if isinstance(value, tuple):
        return tuple(readonly(v, memo) for v in value)
    return value


def physical_sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


class ValidationContext:
    def __init__(self, label):
        self.label = label
        self.depth = 0
        self.closed = False
        self.files = {}
        self.pool = _pool.get() or HistoricalProofPool()
        self.shared_pool = _pool.get() is not None
        self.documents = self.pool.documents
        self.results = self.pool.results
        self.dependency_stack = []
        self.current_results = {}
        self.registry_views = {}
        self.directories = {}
        self.started = time.perf_counter()
        self.metrics = dict(json_parses=0, parsed_bytes=0, proof_runs=0, proof_reuses=0)

    def __enter__(self):
        current = _current.get()
        if current is not None and not current.closed:
            raise ValueError('nested validation context must share its owner')
        self.token = _current.set(self)
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            if exc_type is None:
                self.finish()
            else:
                self.closed = True
                self.pool.clear()
        finally:
            for view in self.registry_views.values():
                view.close()
            self.current_results.clear()
            _current.reset(self.token)

    def pin(self, path, *, history=False):
        path = Path(path).absolute()
        if path not in self.files:
            self.files[path] = (path.resolve(strict=True), physical_sha(path))
        if history or self.depth:
            # Negative historical receipts, not mutable runtime output folders.
            self.directories.setdefault(path.parent, frozenset(p.name for p in path.parent.iterdir()))
        for dependencies in self.dependency_stack:
            dependencies.add(path)
        return self.files[path][1]

    def read(self, path):
        path = Path(path).absolute()
        pin = self.pin(path)
        if path in self.documents and self.documents[path][0] != self.files[path]:
            raise ValueError('historical document no longer matches its authenticated bytes: ' + str(path))
        if path not in self.documents:
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != pin:
                raise ValueError('historical artifact changed while reading: ' + str(path))
            self.documents[path] = (self.files[path], readonly(json.loads(data)))
            self.metrics['json_parses'] += 1
            self.metrics['parsed_bytes'] += len(data)
        return self.documents[path][1]

    def finish(self):
        if self.closed:
            return
        try:
            for path, (resolved, pin) in self.files.items():
                if path.resolve(strict=True) != resolved or physical_sha(path) != pin:
                    raise ValueError('validation phase historical input drift: ' + str(path))
            for path, names in self.directories.items():
                if frozenset(p.name for p in path.iterdir()) != names:
                    raise ValueError('validation phase historical receipt set drift: ' + str(path))
            for view in self.registry_views.values():
                view.verify_unchanged()
        except BaseException:
            self.pool.clear()
            raise
        finally:
            for view in self.registry_views.values():
                view.close()
            self.current_results.clear()
            self.closed = True
            self.metrics['wall_seconds'] = round(time.perf_counter() - self.started, 6)
            self.pool.phase_metrics.append({'phase': self.label, **self.metrics})
            if not self.shared_pool:
                self.pool.clear()


def historical_context():
    ctx = _current.get()
    return ctx if ctx is not None and not ctx.closed and ctx.depth else None


def active_context():
    ctx = _current.get()
    return ctx if ctx is not None and not ctx.closed else None


def runtime_active():
    return _pool.get() is not None


def runtime_metrics():
    pool = _pool.get()
    return list(pool.phase_metrics) if pool is not None else []


def read_current_objects(store, keys):
    """Fresh SQL for a complete declared readset. Never enters the proof pool.

    This replaces whole-library reads ONLY for guards that already compare
    these exact objects. Global freeze/non-target drift gates remain unchanged.
    Stored revision/SHA are returned, not fabricated from payload defaults.
    """
    wanted = set(keys)
    ctx = active_context()
    if ctx is not None and hasattr(store, 'database_url'):
        return current_registry_view(store).read_keys(wanted)
    records = {}
    with store.connect() as connection:
        connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        rows = connection.execute(
            'SELECT collection,object_id,revision,content_sha256,payload,retired_at '
            'FROM wang_knowledge.objects WHERE object_id=ANY(%s)',
            (sorted({oid for _, oid in wanted}),),
        ).fetchall()
        for collection, oid, revision, sha, payload, retired_at in rows:
            key = collection, oid
            if key in wanted:
                if key in records:
                    raise ValueError('duplicate current object: ' + str(key))
                records[key] = {'payload': payload, 'revision': revision,
                                'content_sha256': sha, 'retired': retired_at is not None}
    if records.keys() != wanted:
        raise ValueError('current proof readset incomplete: ' + str(sorted(wanted - records.keys())))
    return records


def finish_validation_phase():
    """Mandatory before a model call or a write; no state crosses that boundary."""
    ctx = _current.get()
    if ctx is not None:
        ctx.finish()


def validation_phase(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        existing = _current.get()
        if existing is not None and not existing.closed:
            return fn(*args, **kwargs)
        with ValidationContext(fn.__module__ + '.' + fn.__name__):
            return fn(*args, **kwargs)
    return run


def validated_history(fn):
    signature = inspect.signature(fn)
    @wraps(fn)
    def run(*args, **kwargs):
        ctx = _current.get()
        if ctx is None or ctx.closed:
            return fn(*args, **kwargs)
        # Package identity is content-bound, never object-id or filename-only.
        def encode(v):
            if isinstance(v, Path):
                return str(v)
            raise TypeError('historical proof argument is not content-addressable')
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        encoded = json.dumps(bound.arguments, sort_keys=True, ensure_ascii=False, default=encode)
        key = (fn, hashlib.sha256(encoded.encode()).hexdigest())
        if key in ctx.results:
            result, files, directories = ctx.results[key]
            for path, expected in files.items():
                ctx.pin(path, history=True)
                if ctx.files[path] != expected:
                    raise ValueError('historical proof dependency drift: ' + str(path))
            for path, names in directories.items():
                if ctx.directories.get(path) != names:
                    raise ValueError('historical proof receipt population drift: ' + str(path))
            ctx.metrics['proof_reuses'] += 1
            return result
        ctx.depth += 1
        dependencies = set()
        ctx.dependency_stack.append(dependencies)
        try:
            source_file = Path(fn.__code__.co_filename)
            if source_file.is_file():
                ctx.pin(source_file)
            result = readonly(fn(*args, **kwargs))
            ctx.results[key] = (result, {p: ctx.files[p] for p in dependencies},
                                {p.parent: ctx.directories[p.parent] for p in dependencies})
            ctx.metrics['proof_runs'] += 1
            return result
        finally:
            ctx.dependency_stack.pop()
            ctx.depth -= 1
    return run


def registry_signature(connection):
    """Authenticate physical MVCC generations, not application-supplied SHAs.

    Any UPDATE (including payload-only or same stored SHA), INSERT or DELETE
    changes this cut. Relation rewrite, restart and 32-bit XID wrap invalidate
    every cached row. Payloads are read and checked once per such generation;
    merely rehashing the entire unchanged JSON library adds no new evidence.
    """
    rows = connection.execute(
        "SELECT collection,encode(sha256(convert_to(string_agg("
        "length(object_id)::text||':'||object_id||':'||xmin::text||':'||ctid::text,"
        "E'\\n' ORDER BY object_id),'UTF8')),'hex') "
        "FROM wang_knowledge.objects GROUP BY collection ORDER BY collection"
    )
    result = dict(rows)
    result['__change_sets__'] = connection.execute(
        "SELECT encode(sha256(convert_to(coalesce(string_agg("
        "length(change_set_id)::text||':'||change_set_id||':'||xmin::text||':'||ctid::text,"
        "E'\\n' ORDER BY change_set_id),''),'UTF8')),'hex') FROM wang_knowledge.change_sets c"
    ).fetchone()[0]
    epoch = connection.execute(
        "SELECT pg_postmaster_start_time()::text,"
        "floor(pg_snapshot_xmax(pg_current_snapshot())::text::numeric/4294967296)::text,"
        "pg_relation_filenode('wang_knowledge.objects'::regclass),"
        "pg_relation_filenode('wang_knowledge.change_sets'::regclass)"
    ).fetchone()
    # Each collection's reuse identity includes the database storage epoch.
    return {k:(tuple(epoch),v) for k,v in result.items()}


class CurrentRegistryView:
    """One consistent read-only view, valid only until the next model/write.

    Uses the store's existing writer advisory lock in shared mode. An actual
    physical row-generation cut on a fresh connection at exit detects even writers
    that ignored that lock. The runtime may retain immutable data, not current
    verdicts, keyed by the freshly checked PostgreSQL generation.
    """
    def __init__(self, store):
        from backend.api.canonical_repository.postgres_store import POSTGRES_APPLY_ADVISORY_LOCK_KEY
        self.store = store
        self.connection = store.connect()
        self.collections = {}
        self.records = {}
        self.closed = False
        self.pool = active_context().pool
        try:
            # Acquire before the MVCC snapshot, so a preceding writer's commit
            # is visible. Closing the connection releases this session lock.
            self.connection.autocommit = True
            self.connection.execute("SELECT pg_advisory_lock_shared(hashtextextended(%s,0))",
                                    (POSTGRES_APPLY_ADVISORY_LOCK_KEY,))
            self.connection.autocommit = False
            self.connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            self.signature = registry_signature(self.connection)
        except BaseException:
            self.close()
            raise

    def list_records(self, collection):
        if collection not in self.collections:
            identity = self.store.database_url, collection
            pin = self.signature.get(collection)
            cached = self.pool.registry_data.get(identity)
            if cached is None or cached[0] != pin:
                rows = self.connection.execute(
                    'SELECT object_id,revision,content_sha256,payload,retired_at FROM wang_knowledge.objects '
                    'WHERE collection=%s AND retired_at IS NULL ORDER BY object_id', (collection,)
                ).fetchall()
                data = tuple((oid,readonly({'payload':payload,'revision':revision,
                    'content_sha256':sha,'retired':retired_at is not None}))
                    for oid,revision,sha,payload,retired_at in rows)
                self.pool.registry_data[identity] = pin, data
            else:
                data = cached[1]
            self.records.update({(collection,oid):row for oid,row in data})
            self.collections[collection] = tuple(row['payload'] for _,row in data)
        return list(self.collections[collection])

    def canonical_cut(self, collection):
        from backend.api.canonical_repository.viewpoint_foundation import sha256_json
        identity = self.store.database_url, collection
        pin = self.signature.get(collection)
        cached = self.pool.registry_cuts.get(identity)
        if cached is None or cached[0] != pin:
            parts = tuple(sorted(sha256_json(p) for p in self.list_records(collection)))
            self.pool.registry_cuts[identity] = pin, parts
        else:
            parts = cached[1]
        return list(parts)

    def read_keys(self, wanted):
        missing = wanted - self.records.keys()
        if missing:
            rows = self.connection.execute(
                'SELECT collection,object_id,revision,content_sha256,payload,retired_at '
                'FROM wang_knowledge.objects WHERE object_id=ANY(%s)',
                (sorted({oid for _,oid in missing}),)
            ).fetchall()
            for col,oid,revision,sha,payload,retired_at in rows:
                if (col,oid) in missing:
                    self.records[col,oid] = readonly({'payload':payload,'revision':revision,
                        'content_sha256':sha,'retired':retired_at is not None})
        if wanted - self.records.keys():
            raise ValueError('current proof readset incomplete')
        return {key:self.records[key] for key in wanted}

    def verify_unchanged(self):
        with self.store.connect() as connection:
            connection.execute('SET TRANSACTION READ ONLY')
            if registry_signature(connection) != self.signature:
                raise ValueError('Registry changed during mechanical validation')

    def close(self):
        if not self.closed:
            self.closed = True
            self.connection.rollback()
            self.connection.close()
            self.records.clear()
            self.collections.clear()


def current_registry_view(store):
    ctx = active_context()
    if ctx is None:
        raise ValueError('Registry view requires a bounded validation phase')
    identity = store.database_url
    if identity not in ctx.registry_views:
        ctx.registry_views[identity] = CurrentRegistryView(store)
    return ctx.registry_views[identity]


def current_check(fn):
    """Reuse one check in the SAME guarded SQL view, never across phases."""
    signature = inspect.signature(fn)
    @wraps(fn)
    def run(*args, **kwargs):
        ctx = active_context()
        if ctx is None:
            return fn(*args, **kwargs)
        values = signature.bind(*args, **kwargs)
        values.apply_defaults()
        if any((hasattr(v, 'connect') or hasattr(v, 'list_records')) and not hasattr(v, 'database_url')
               for v in values.arguments.values()):
            return fn(*args, **kwargs)
        def encode(value):
            if hasattr(value, 'database_url'):
                view = current_registry_view(value)
                return {'guarded_registry_view':id(view)}
            if isinstance(value, Path):return str(value)
            raise TypeError('current check argument not content-addressable')
        encoded = json.dumps(values.arguments, sort_keys=True, ensure_ascii=False, default=encode)
        key = fn, hashlib.sha256(encoded.encode()).hexdigest()
        if key not in ctx.current_results:
            ctx.current_results[key] = readonly(fn(*args, **kwargs))
        result = ctx.current_results[key]
        # Findings consumers append their own independent findings. Preserve
        # that list API without exposing the retained immutable result.
        return list(result) if isinstance(result, ReadOnlyList) else result
    return run
