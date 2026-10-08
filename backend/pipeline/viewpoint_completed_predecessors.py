"""Explicit compact predecessor inputs for ALREADY applied source packages.

This is not approval, an archive hash cache, or a writer authorization override.
An offline compiler must first authenticate the entire original snapshot. The
routine consumer checks the small copied readset against EVERY pinned SQL before
version in the actual applied plan. Source/model/current/hold gates stay separate.
No projection discovery, compilation, or whole-history fallback is automatic.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from backend.pipeline import source_reconciliation as c
from backend.pipeline import viewpoint_compiled_readset as compiled
from backend.pipeline.source_reconciliation_runner import body, read, write, binding

SCHEMA = 'wang_completed_source_predecessor_input_v1'
_scope = ContextVar('completed_source_predecessor_inputs', default=None)


def _small(bound):
    return body(compiled._small(bound))


@contextmanager
def predecessor_inputs(bindings):
    """Caller pins declarations explicitly in the new campaign configuration."""
    if _scope.get() is not None:
        raise ValueError('nested completed predecessor declarations')
    entries = {}
    for bound in bindings:
        value = _small(bound)
        if (set(value) != {'schema_version','source','completion','snapshot','aliases','projection'}
                or value['schema_version'] != SCHEMA):
            raise ValueError('completed predecessor declaration shape drift')
        source = value['source']['path']
        aliases = value['aliases']
        if (source in entries or not aliases or value['snapshot'] not in aliases
                or len(aliases) != len({pin['path'] for pin in aliases})
                or any(pin['file_sha256'] != value['snapshot']['file_sha256'] for pin in aliases)):
            raise ValueError('completed predecessor source/alias ambiguity')
        entries[source] = {'binding':bound,'value':value,'authenticated':False}
    token = _scope.set(entries)
    try:
        yield
        for entry in entries.values():c.verify_file(entry['binding'])
    finally:
        _scope.reset(token)


def _entry(source):
    entries = _scope.get() or {}
    entry = entries.get(source['path'])
    if entry is not None:
        c.verify_file(entry['binding'])
        if entry['value']['source'] != source:
            raise ValueError('completed predecessor package binding drift')
    return entry


def plan_readset(plan, store):
    """Whole operation population, not selected Claims or latest SQL rows."""
    from backend.pipeline.viewpoint_applied_source_epochs import read_pinned_versions
    pins, before = [], {}
    for op in plan['operations']:
        key = c.key(op['collection'],op['object_id'])
        if key in before:raise ValueError('duplicate completed predecessor operation')
        if op['operation'] == 'create':
            if op['before_revision'] is not None or op['before_sha256'] is not None:
                raise ValueError('created object has a predecessor')
            before[key] = None
        elif op['operation'] == 'update':
            pins.append((op['collection'],op['object_id'],op['before_revision'],op['before_sha256']))
            before[key] = None  # reserve identity; SQL below must supply it
        else:raise ValueError('unsupported completed predecessor operation')
    if not before:raise ValueError('empty completed predecessor operation population')
    versions=read_pinned_versions(pins,store)
    if set(versions)!={(p[0],p[1]) for p in pins} or any(v is None for v in versions.values()):
        raise ValueError('completed predecessor SQL population missing/changed')
    before.update({c.key(*key):value for key,value in versions.items()})
    return before


def authenticate_snapshot(snapshot, source, plan, store):
    """Called ONLY after the full package/plan/receipt actual SQL check passes."""
    entry = _entry(source)
    if entry is None:
        c.verify_file(snapshot)
        return
    spec = entry['value']
    if snapshot != spec['snapshot']:
        raise ValueError('completed predecessor snapshot differs from applied package')
    anchor = _small(spec['completion'])
    plan_pin = {'path':str(Path(source['path']).parent/'dry-run-plan.json'),
                'file_sha256':c.file_sha256(Path(source['path']).parent/'dry-run-plan.json')}
    if (anchor.get('kind') != 'completed_source_predecessor_compilation'
            or anchor.get('status') != 'complete'
            or anchor.get('source') != source or anchor.get('plan') != plan_pin
            or anchor.get('bindings') != spec['aliases']
            or anchor.get('semantic_approval_granted') is not False
            or anchor.get('model_calls') != 0 or anchor.get('Registry_writes') != 0):
        raise ValueError('compiled predecessor original source/plan/archive anchor drift')
    before = plan_readset(plan,store)
    projection = compiled.consume_projection(spec['projection'],sorted(before),
        completion_binding=spec['completion'],snapshot_binding=snapshot,
        field='before_records')
    if (projection['rows'] != {key:value for key,value in before.items() if value is not None}
            or set(projection['absent_keys']) != {key for key,value in before.items() if value is None}):
        raise ValueError('compiled predecessor differs from exact applied SQL before versions')
    entry['authenticated'] = True


def verify_archived_refusal(snapshot, source):
    """A saved refusal remains intact; only its redundant whole snapshot moves.

    A copied alias is eligible only after this package's entire SQL predecessor
    population was authenticated above. Other refusal/raw/source files always
    retain ordinary physical SHA verification. Strict new-write readers do not
    call this function and never inherit this completed-only scope.
    """
    entry = _entry(source)
    if entry is None or snapshot not in entry['value']['aliases']:
        c.verify_file(snapshot)
        return
    if not entry['authenticated']:
        raise ValueError('archived refusal predecessor lacks actual SQL authentication')


def compile_source_predecessor(source, output_root, store, *, completed_frame=None,
                               limits=compiled.CompileLimits()):
    """Explicit offline maintenance, never called by a routine reader.

    First retain the original full physical/SQL authentication. Then copy only
    all operation predecessors (including create absences) and prove exact
    equality against every applied SQL before pin. A failed/missing projection
    leaves its artifacts intact and cannot trigger an automatic recompilation.
    """
    from backend.pipeline.viewpoint_validation_context import require_explicit_history_maintenance
    from backend.pipeline.viewpoint_applied_source_epochs import consume_applied_source_package
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    require_explicit_history_maintenance('explicit completed source predecessor compilation')
    if _scope.get() is not None:
        raise ValueError('compiler cannot inherit a compact predecessor scope')
    root=Path(output_root)
    if not root.is_absolute() or root.exists():
        raise ValueError('new absolute compiled predecessor root required')
    package=consume_applied_source_package(source,store,completed_frame=completed_frame)
    app=package['source_repair_application']
    snapshot=app.get('before_binding')
    if not snapshot:raise ValueError('source application has no original snapshot predecessor')
    plan_path=Path(source['path']).parent/'dry-run-plan.json'
    # Historical executor plans use their original RAW format. The applied
    # package consumer has already reconstructed its SQL-bound fingerprint;
    # do not require a later unrelated source-runner seal on that raw file.
    plan=read_artifact(plan_path);before=plan_readset(plan,store)
    aliases=[snapshot]
    for pin in app.get('review_packet',{}).get('prior_refusal_bindings',[]):
        if pin.get('file_sha256')==snapshot['file_sha256'] and pin not in aliases:
            c.verify_file(pin);aliases.append(pin)
    root.mkdir(parents=True)
    anchor=root/'original-applied-anchor.json'
    write(anchor,dict(kind='completed_source_predecessor_compilation',status='complete',
        source=source,plan=binding(plan_path),bindings=aliases,
        original_completed_frame=completed_frame,semantic_approval_granted=False,
        model_calls=0,Registry_writes=0))
    receipt=compiled.compile_projection(binding(anchor),Path(snapshot['path']),sorted(before),
        root/'predecessors.json',field='before_records',limits=limits)
    declaration=root/'completed-predecessor-input.json'
    write(declaration,dict(schema_version=SCHEMA,source=source,completion=binding(anchor),
        snapshot=snapshot,aliases=aliases,projection=receipt['projection']))
    with predecessor_inputs([binding(declaration)]):
        authenticate_snapshot(snapshot,source,plan,store)
    write(root/'compilation-result.json',{**receipt,'declaration':binding(declaration),
        'whole_operation_population':len(before),'retained_before_versions':sum(v is not None for v in before.values()),
        'create_absences':sum(v is None for v in before.values()),
        'exact_SQL_predecessor_equality':True,'old_archive_aliases':aliases,
        'production_resume_authorized':False})
    return binding(declaration)
