"""One bounded validation phase, not a process-global cache or approval receipt.

Historical validators still run their complete existing checks. Within a phase
they share authenticated bytes and immutable results instead of recursively
reloading history. All consumed files are rehashed at the phase boundary. Live
SQL, postimage checks, model execution and writes are never memoized here.
"""
from __future__ import annotations

from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps
import hashlib
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

    def clear(self):
        self.documents.clear()
        self.results.clear()


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
            _current.reset(self.token)

    def pin(self, path):
        path = Path(path).absolute()
        if path not in self.files:
            self.files[path] = (path.resolve(strict=True), physical_sha(path))
            # Historical refusals also check that later call/apply files do NOT
            # exist. Detect additions/deletions, not just changed positive pins.
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
        except BaseException:
            self.pool.clear()
            raise
        finally:
            self.closed = True
            self.metrics['wall_seconds'] = round(time.perf_counter() - self.started, 6)
            self.pool.phase_metrics.append({'phase': self.label, **self.metrics})
            if not self.shared_pool:
                self.pool.clear()


def historical_context():
    ctx = _current.get()
    return ctx if ctx is not None and not ctx.closed and ctx.depth else None


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
        encoded = json.dumps([args, kwargs], sort_keys=True, ensure_ascii=False, default=encode)
        key = (fn, hashlib.sha256(encoded.encode()).hexdigest())
        if key in ctx.results:
            result, files, directories = ctx.results[key]
            for path, expected in files.items():
                ctx.pin(path)
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
