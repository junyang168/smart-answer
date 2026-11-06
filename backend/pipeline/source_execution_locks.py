"""Host-wide source/write leases, compatible with legacy exclusive maintenance.

These are execution locks, not content authority. SQL read pins, short
ChangeSet transactions and post-commit verification remain mandatory.
"""
from contextlib import ExitStack, contextmanager
import fcntl
import hashlib
import os
from pathlib import Path


def data_root():
    root = Path(os.environ.get('DATA_BASE_DIR', ''))
    if not root.is_absolute():
        raise ValueError('absolute DATA_BASE_DIR required for shared execution locks')
    return root


def gate_paths():
    staging = data_root() / 'wang-knowledge-platform/staging'
    recovery = Path(os.environ.get('WANG_CVP_RECOVERY_ROOT',
        str(staging / 'cvp-production-357/immediate-recovery-20261002-v1')))
    if not recovery.is_absolute():
        raise ValueError('absolute recovery lock root required')
    return (recovery / 'recovery.lock', staging / 'cvp-production-357/registry-cvp.lock')


def resource_paths(source_ids, record_keys=()):
    if isinstance(source_ids, str) or isinstance(record_keys, str):
        raise ValueError('execution scopes require collections, not a single string')
    sources = set(source_ids)
    records = set(record_keys)
    if not sources or any(not isinstance(s, str) or not s.strip() for s in sources):
        raise ValueError('scoped execution requires explicit source IDs')
    if any(not isinstance(k, str) or '/' not in k or not all(k.split('/', 1)) for k in records):
        raise ValueError('write leases require full collection/object keys')
    root = data_root() / 'wang-knowledge-platform/staging/source-execution-locks'
    names = {'source/' + s for s in sources} | {'record/' + k for k in records}
    return tuple(root / (hashlib.sha256(name.encode()).hexdigest() + '.lock') for name in sorted(names))


@contextmanager
def _hold(paths, mode):
    with ExitStack() as stack:
        handles = []
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = stack.enter_context(path.open('a+'))
            fcntl.flock(handle, mode | fcntl.LOCK_NB)
            handles.append(handle)
        yield tuple(handles)


def shared_maintenance_gates():
    # Legacy CVP/recovery workers still take EX on these exact inodes. A
    # running old worker cannot be bypassed by changing its filename or inode.
    return _hold(gate_paths(), fcntl.LOCK_SH)


def source_write_locks(source_ids, record_keys=()):
    return _hold(resource_paths(source_ids, record_keys), fcntl.LOCK_EX)


@contextmanager
def claim_execution_locks(request):
    with claim_scopes_locks([request]) as handles:
        yield handles


@contextmanager
def claim_scopes_locks(requests):
    requests = tuple(requests)
    # A Claim can read several complete sources. Readers coexist; replacing
    # one of those source versions must wait. Content write targets are EX.
    with ExitStack() as stack:
        source_ids = {sid for request in requests for sid in request.source_bindings}
        sources = stack.enter_context(_hold(resource_paths(source_ids), fcntl.LOCK_SH))
        writes = {key for request in requests for key in request_write_keys(request)}
        paths = resource_paths(source_ids, writes)
        source_paths = set(resource_paths(source_ids))
        records = stack.enter_context(_hold([p for p in paths if p not in source_paths], fcntl.LOCK_EX))
        yield sources + records


def verify_claim_locks(descriptors, request):
    source_paths = resource_paths(request.source_bindings)
    records = [p for p in resource_paths(request.source_bindings, request_write_keys(request)) if p not in set(source_paths)]
    if len(descriptors) != len(source_paths) + len(records):
        raise ValueError('worker inherited an incomplete Claim scope')
    verify_inherited(descriptors[:len(source_paths)], source_paths, exclusive=False)
    verify_inherited(descriptors[len(source_paths):], records, exclusive=True)


def request_write_keys(request):
    writes = set(request.target_keys) | {p.key for p in request.permissions}
    if getattr(request, 'operation', 'Update') == 'Update':
        # Preserve existing same-own-source Update ordering, including scoped
        # creation/qualification checks. This is a set: a cross-sermon request
        # owns all its bound source lanes rather than being split into lectures.
        writes.update('claim_source_execution/' + sid for sid in request.source_bindings)
    return writes


def verify_inherited(descriptors, paths, *, exclusive):
    if len(descriptors) != len(paths):
        raise ValueError('worker inherited an incomplete execution scope')
    for fd, path in zip(descriptors, paths):
        info = os.fstat(fd)
        with path.open('a+') as probe:
            actual = os.fstat(probe.fileno())
            if (actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError('inherited execution lock identity drift')
            # An EX lease must exclude readers too; an SH gate must exclude
            # a maintenance writer. Opening/probing never unlocks the parent.
            mode = fcntl.LOCK_SH if exclusive else fcntl.LOCK_EX
            try:
                fcntl.flock(probe, mode | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            raise ValueError('worker inherited an unlocked execution descriptor')
