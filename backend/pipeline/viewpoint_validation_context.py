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
import os
from pathlib import Path
import time
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import math

_current = ContextVar('cvp_validation_context', default=None)
_pool = ContextVar('cvp_historical_proof_pool', default=None)


@dataclass(frozen=True)
class ValidationLimits:
    """Fail-closed resource guard, not evidence that an algorithm is scalable.

    Explicit maintenance may use an unbounded context under its external job
    budget. Routine phases may not silently downgrade or retry without limits.
    File IO includes authentication, parsing and the closing reauthentication.
    """
    file_io_bytes: int
    wall_seconds: float

    def __post_init__(self):
        if (type(self.file_io_bytes) is not int or self.file_io_bytes <= 0
                or not isinstance(self.wall_seconds, (int, float))
                or isinstance(self.wall_seconds, bool) or not math.isfinite(self.wall_seconds)
                or self.wall_seconds <= 0):
            raise ValueError('positive finite validation limits required')


class ValidationBudgetExceeded(ValueError):
    """Mechanical design/scale failure; never a semantic rejection or retry."""
    def __init__(self, phase, metric, observed, limit, operation):
        self.detail = dict(status='scalability_budget_exceeded', phase=phase,
                           metric=metric, observed=observed, limit=limit,
                           operation=operation, automatic_fallback=False,
                           model_calls=0, registry_writes=0)
        super().__init__(json.dumps(self.detail, ensure_ascii=False, sort_keys=True))


# Safety ceiling for the existing authenticated-history runtime. This does not
# certify its cold path: the known whole-history path MUST stop at this ceiling
# until replaced by compiled bounded proofs. No production release from a warm
# cache or from merely staying under these ceilings; growth acceptance is extra.
ROUTINE_VALIDATION_LIMITS = ValidationLimits(64*1024**2, 5.0)


class HistoricalProofPool:
    """Owned by one campaign runtime; never persisted or shared with a writer."""
    def __init__(self, limits=None):
        self.limits = limits
        self.documents = {}
        self.results = {}
        self.phase_metrics = []
        self.registry_data = {}
        self.registry_cuts = {}
        self.typed_data = {}
        self.registry_generations = {}

    def clear(self):
        self.documents.clear()
        self.results.clear()
        self.registry_data.clear()
        self.registry_cuts.clear()
        self.typed_data.clear()
        self.registry_generations.clear()


@contextmanager
def validation_runtime(*, limits=None):
    if _pool.get() is not None:
        raise ValueError('one owner per validation runtime')
    pool = HistoricalProofPool(limits)
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
    def __init__(self, label, *, limits=None):
        self.label = label
        self.depth = 0
        self.closed = False
        self.files = {}
        self.absent_files = set()
        self.pool = _pool.get() or HistoricalProofPool()
        # A caller cannot loosen its owning runtime's hard guard. Standalone
        # maintenance can choose its own explicit bounds without changing it.
        self.limits = self.pool.limits if self.pool.limits is not None else limits
        if limits is not None and self.pool.limits is not None and limits != self.pool.limits:
            raise ValueError('cannot override owning validation runtime limits')
        self.shared_pool = _pool.get() is not None
        self.documents = self.pool.documents
        self.results = self.pool.results
        self.dependency_stack = []
        self.receipt_policy_stack = []
        self.current_results = {}
        self.registry_views = {}
        self.directories = {}
        self.started = time.perf_counter()
        self.metrics = dict(json_parses=0, parsed_bytes=0, proof_runs=0, proof_reuses=0,
                            file_io_bytes=0, hashed_bytes=0)

    def check_budget(self, operation):
        if self.limits is not None:
            elapsed = time.perf_counter()-self.started
            if elapsed > self.limits.wall_seconds:
                raise ValidationBudgetExceeded(self.label, 'wall_seconds', elapsed,
                                               self.limits.wall_seconds, operation)

    def charge_io(self, size, operation):
        self.check_budget(operation)
        total = self.metrics['file_io_bytes']+size
        if self.limits is not None and total > self.limits.file_io_bytes:
            raise ValidationBudgetExceeded(self.label, 'file_io_bytes', total,
                                           self.limits.file_io_bytes, operation)
        self.metrics['file_io_bytes'] = total

    def hash_file(self, path):
        # Refuse an oversized historical snapshot BEFORE opening/reading it.
        # Stream and charge actual chunks too, so concurrent growth cannot
        # defeat a stat-only precheck. No timestamp is treated as a content SHA.
        size = path.stat().st_size
        self.check_budget('authenticate '+str(path))
        if self.limits is not None and self.metrics['file_io_bytes']+size > self.limits.file_io_bytes:
            raise ValidationBudgetExceeded(self.label, 'file_io_bytes', self.metrics['file_io_bytes']+size,
                                           self.limits.file_io_bytes, 'authenticate '+str(path))
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            while chunk := stream.read(1024*1024):
                self.charge_io(len(chunk), 'authenticate '+str(path))
                self.metrics['hashed_bytes'] += len(chunk)
                digest.update(chunk)
        self.check_budget('authenticated '+str(path))
        return digest.hexdigest()

    def read_bytes(self, path):
        size = path.stat().st_size
        self.check_budget('read '+str(path))
        if self.limits is not None and self.metrics['file_io_bytes']+size > self.limits.file_io_bytes:
            raise ValidationBudgetExceeded(self.label, 'file_io_bytes', self.metrics['file_io_bytes']+size,
                                           self.limits.file_io_bytes, 'read '+str(path))
        chunks = []
        with path.open('rb') as stream:
            while chunk := stream.read(1024*1024):
                self.charge_io(len(chunk), 'read '+str(path))
                chunks.append(chunk)
        return b''.join(chunks)

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
                self.metrics['wall_seconds'] = round(time.perf_counter()-self.started, 6)
                self.pool.phase_metrics.append({'phase': self.label, 'status': 'failed', **self.metrics})
        finally:
            for view in self.registry_views.values():
                view.close()
            self.current_results.clear()
            _current.reset(self.token)

    def pin(self, path, *, history=False):
        self.check_budget('pin historical input')
        path = Path(path).absolute()
        if path not in self.files:
            self.files[path] = (path.resolve(strict=True), self.hash_file(path))
        if (history or self.depth) and (not self.receipt_policy_stack or any(self.receipt_policy_stack)):
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
            data = self.read_bytes(path)
            if hashlib.sha256(data).hexdigest() != pin:
                raise ValueError('historical artifact changed while reading: ' + str(path))
            self.documents[path] = (self.files[path], readonly(json.loads(data)))
            self.check_budget('parsed '+str(path))
            self.metrics['json_parses'] += 1
            self.metrics['parsed_bytes'] += len(data)
        return self.documents[path][1]

    def optional_file_exists(self, path):
        """Track a named optional/negative input, not its entire log directory."""
        path = Path(path).absolute()
        exists = os.path.lexists(path)
        if exists:
            if path in self.absent_files:
                raise ValueError('validation phase optional receipt appeared: ' + str(path))
            self.pin(path)
        else:
            if path in self.files:
                raise ValueError('validation phase optional receipt disappeared: ' + str(path))
            self.absent_files.add(path)
            for dependencies in self.dependency_stack:
                dependencies.add(path)
        return exists

    def finish(self):
        if self.closed:
            return
        try:
            self.check_budget('closing validation boundary')
            for path, (resolved, pin) in self.files.items():
                if path.resolve(strict=True) != resolved or self.hash_file(path) != pin:
                    raise ValueError('validation phase historical input drift: ' + str(path))
            for path in self.absent_files:
                if os.path.lexists(path):
                    raise ValueError('validation phase optional receipt appeared: ' + str(path))
            for path, names in self.directories.items():
                if frozenset(p.name for p in path.iterdir()) != names:
                    raise ValueError('validation phase historical receipt set drift: ' + str(path))
            for view in self.registry_views.values():
                view.verify_unchanged()
            self.check_budget('closed validation boundary')
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


def optional_file_exists(path):
    ctx = active_context()
    return ctx.optional_file_exists(path) if ctx else os.path.lexists(path)


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


def validated_history(fn=None, *, bound_files_only=False):
    """Memoize a complete historical validator, never a current approval.

    Legacy validators retain conservative directory-population guards. A
    validator which uses only explicit, content-bound files (no discovery or
    absence-based decisions) can opt into bound_files_only. This separates its
    proof inputs from unrelated progress/log files, without ignoring a changed,
    missing or replaced input. Nested legacy validators keep their own guards.
    """
    if fn is None:
        return lambda wrapped: validated_history(wrapped, bound_files_only=bound_files_only)
    signature = inspect.signature(fn)
    @wraps(fn)
    def run(*args, **kwargs):
        ctx = _current.get()
        if ctx is None or ctx.closed:
            return fn(*args, **kwargs)
        ctx.check_budget('historical validator '+fn.__qualname__)
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
            ctx.receipt_policy_stack.append(not bound_files_only)
            try:
                for path, expected in files.items():
                    if expected is None:
                        if ctx.optional_file_exists(path):
                            raise ValueError('historical proof optional receipt appeared: ' + str(path))
                    else:
                        ctx.pin(path, history=True)
                    if expected is not None and ctx.files[path] != expected:
                        raise ValueError('historical proof dependency drift: ' + str(path))
            finally:
                ctx.receipt_policy_stack.pop()
            for path, names in directories.items():
                # The previous phase may have inherited this guard from a
                # legacy validator. A bound-only caller does not populate it
                # by pinning files in the new phase; absence from this context
                # is not physical drift. Reauthenticate the actual directory,
                # then retain the guard through the closing boundary.
                if (frozenset(p.name for p in path.iterdir()) != names
                        or (path in ctx.directories and ctx.directories[path] != names)):
                    raise ValueError('historical proof receipt population drift: ' + str(path))
                ctx.directories[path] = names
            ctx.metrics['proof_reuses'] += 1
            return result
        ctx.depth += 1
        ctx.receipt_policy_stack.append(not bound_files_only)
        dependencies = set()
        ctx.dependency_stack.append(dependencies)
        try:
            source_file = Path(fn.__code__.co_filename)
            if source_file.is_file():
                ctx.pin(source_file)
            result = readonly(fn(*args, **kwargs))
            ctx.check_budget('historical validator finished '+fn.__qualname__)
            ctx.results[key] = (result, {p: ctx.files.get(p) for p in dependencies},
                                {p.parent: ctx.directories[p.parent] for p in dependencies
                                 if p.parent in ctx.directories})
            ctx.metrics['proof_runs'] += 1
            return result
        finally:
            ctx.receipt_policy_stack.pop()
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


def database_clock(connection):
    """Cluster WAL is monotonic: unchanged means no logged data mutation.

    Read BEFORE starting the MVCC snapshot. A changed clock triggers the full
    physical-row cut, never an assumption that a writer behaved correctly.
    Restart is part of identity; canonical tables must actually be WAL logged.
    """
    row=connection.execute(
        "SELECT pg_current_wal_insert_lsn()::text,pg_postmaster_start_time()::text,"
        "bool_and(relpersistence='p') FROM pg_class "
        "WHERE oid IN ('wang_knowledge.objects'::regclass,'wang_knowledge.change_sets'::regclass)"
    ).fetchone()
    if row[2] is not True:raise ValueError('canonical tables must be WAL logged')
    return tuple(row[:2])


class CurrentRegistryView:
    """One consistent read-only view, valid only until the next model/write.

    Uses a read-only MVCC snapshot, not a multi-writer coordination protocol.
    A fresh WAL clock and, when changed, physical row-generation cut at exit
    detects data drift. The runtime may retain immutable data, not current
    verdicts, keyed by the freshly checked PostgreSQL generation.
    """
    def __init__(self, store):
        self.store = store
        self.connection = store.connect()
        self.collections = {}
        self.records = {}
        self.closed = False
        self.pool = active_context().pool
        try:
            # Single-writer campaign: no database-wide read lock. Sample the
            # engine clock BEFORE the MVCC snapshot, never attach a newer
            # clock to an older snapshot. Original apply locking is unchanged.
            self.connection.autocommit = True
            self.clock=database_clock(self.connection)
            self.connection.autocommit = False
            self.connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            prior=self.pool.registry_generations.get(store.database_url)
            self.signature = prior[1] if prior is not None and prior[0]==self.clock else registry_signature(self.connection)
            self.pool.registry_generations[store.database_url]=self.clock,self.signature
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
            connection.autocommit=True
            clock=database_clock(connection)
            if clock!=self.clock:
                connection.autocommit=False
                connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                if registry_signature(connection) != self.signature:
                    raise ValueError('Registry changed during mechanical validation')
                # Keep the PRE-snapshot clock, so a later mutation never rides
                # an older snapshot under a newer token.
                self.pool.registry_generations[self.store.database_url]=clock,self.signature

    def close(self):
        if not self.closed:
            self.closed = True
            try:self.connection.rollback()
            finally:self.connection.close()
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


class TypedRecords(Mapping):
    """Parsed data, not approval: callers receive isolated selected models.

    All active rows are schema-validated when the authenticated generation is
    loaded. Never expose the retained mutable Pydantic objects to consumers.
    """
    def __init__(self, rows, model, id_field):
        parsed = [model.model_validate(row) for row in rows]
        self.__rows = {getattr(row,id_field):row for row in parsed}
        if len(parsed)!=len(self.__rows):raise ValueError('duplicate typed record identity')
    def __len__(self):return len(self.__rows)
    def __iter__(self):return iter(self.__rows)
    def __getitem__(self,key):
        row=self.__rows[key]
        return row.model_copy(deep=True) if hasattr(row,'model_copy') else deepcopy(row)
    def reciprocal_subset(self, selected_claims, forward_ids):
        """Include BOTH forward and reverse edges, even malformed orphan edges."""
        wanted=set(forward_ids)
        wanted.update(k for k,row in self.__rows.items() if set(row.produced_claim_ids)&selected_claims)
        return {k:self[k] for k in wanted if k in self.__rows}
    def project_last(self, key_field, value_field):
        # Preserve the old full-collection stable-order last-value projection.
        return {getattr(row,key_field):deepcopy(getattr(row,value_field)) for row in self.__rows.values()}


def typed_records(store, collection, model, id_field):
    ctx=active_context()
    if ctx is None or not hasattr(store,'database_url'):
        return TypedRecords(store.list_records(collection),model,id_field)
    view=current_registry_view(store)
    ctx.pin(Path(inspect.getfile(model)))
    key=store.database_url,collection,model,id_field
    pin=view.signature.get(collection)
    cached=ctx.pool.typed_data.get(key)
    if cached is None or cached[0]!=pin:
        data=TypedRecords(view.list_records(collection),model,id_field)
        ctx.pool.typed_data[key]=pin,data
    else:data=cached[1]
    return data


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
