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
        required={'schema_version','source','completion','snapshot','aliases','projection'}
        if (set(value) not in (required,required|{'readback'})
                or value['schema_version'] != SCHEMA):
            raise ValueError('completed predecessor declaration shape drift')
        if 'readback' in value and set(value['readback'])!={'snapshot','projection'}:
            raise ValueError('completed readback declaration shape drift')
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
    if not entries:return None  # no opt-in: preserve the original reader
    if not isinstance(source,dict) or not isinstance(source.get('path'),str):
        raise ValueError('configured completed predecessor requires an exact source binding')
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


def has_predecessor_input(source):
    return _entry(source) is not None


def assert_applied_plan(package,plan,store):
    """Bind the ENTIRE exact plan/package to actual SQL, never a pass flag."""
    from backend.api.canonical_repository.postgres_store import (ChangeOperation,
        PlannedReviewEvent,operation_fingerprint_rows,review_event_fingerprint_rows)
    ops=[ChangeOperation(**row) for row in plan['operations']]
    events=[PlannedReviewEvent(**row) for row in plan.get('review_events',[])]
    source_sha=c.sha256_json(package)
    fp=c.sha256_json({'planner_schema':'wang_postgres_changeset_v2',
        'source_kind':plan['source_kind'],'source_sha256':source_sha,'package_id':package['package_id'],
        'operations':operation_fingerprint_rows(ops),'review_events':review_event_fingerprint_rows(events)})
    if (plan['source_sha256']!=source_sha or plan['fingerprint_sha256']!=fp
            or plan['change_set_id']!='KCS-'+fp[:20] or plan['package_id']!=package['package_id']
            or any(op.after_sha256!=c.record_content_sha(op.payload) for op in ops)):
        raise ValueError('compiled source full package/operation fingerprint drift')
    with store.connect() as conn,conn.cursor() as cursor:
        cursor.execute('SET TRANSACTION READ ONLY')
        cursor.execute('SELECT source_kind,source_sha256,fingerprint_sha256,status FROM wang_knowledge.change_sets WHERE change_set_id=%s',
                       (plan['change_set_id'],))
        if tuple(cursor.fetchone() or ())!=(plan['source_kind'],source_sha,fp,'applied'):
            raise ValueError('compiled source has no exact actual SQL application')


def complete_readset(plan,store,reviewed_rows=None):
    before=plan_readset(plan,store)
    if reviewed_rows is None:return before
    from backend.pipeline.viewpoint_applied_source_epochs import read_pinned_versions
    pins=[(*c.split_key(key),payload.get('revision'),c.record_content_sha(payload))
          for key,payload in reviewed_rows.items()]
    versions={c.key(*key):value for key,value in read_pinned_versions(pins,store).items()}
    if versions!=reviewed_rows:
        raise ValueError('compiled source retained review context SQL version/content drift')
    for key,payload in reviewed_rows.items():
        if key in before and before[key]!=payload:
            raise ValueError('compiled source plan/review predecessor conflict')
        before[key]=payload
    return before


def authenticate_snapshot(snapshot, source, plan, store, *, plan_path=None,
                          reviewed_rows=None, sections=None):
    """Called ONLY after the full package/plan/receipt actual SQL check passes."""
    entry = _entry(source)
    if entry is None:
        c.verify_file(snapshot)
        return
    spec = entry['value']
    if snapshot != spec['snapshot']:
        raise ValueError('completed predecessor snapshot differs from applied package')
    anchor = _small(spec['completion'])
    plan_path=Path(plan_path) if plan_path is not None else Path(source['path']).parent/'dry-run-plan.json'
    plan_pin = {'path':str(plan_path),'file_sha256':c.file_sha256(plan_path)}
    if (anchor.get('kind') != 'completed_source_predecessor_compilation'
            or anchor.get('status') != 'complete'
            or anchor.get('source') != source or anchor.get('plan') != plan_pin
            or anchor.get('bindings') != spec['aliases']+([spec['readback']['snapshot']] if 'readback' in spec else [])
            or anchor.get('semantic_approval_granted') is not False
            or anchor.get('model_calls') != 0 or anchor.get('Registry_writes') != 0):
        raise ValueError('compiled predecessor original source/plan/archive anchor drift')
    before = complete_readset(plan,store,reviewed_rows)
    sections={} if sections is None else sections
    projection = compiled.consume_projection(spec['projection'],sorted(before),
        completion_binding=spec['completion'],snapshot_binding=snapshot,
        field='before_records',sections=sorted(sections))
    if (projection['rows'] != {key:value for key,value in before.items() if value is not None}
            or set(projection['absent_keys']) != {key for key,value in before.items() if value is None}
            or projection['sections']!=sections):
        raise ValueError('compiled predecessor differs from exact applied SQL before versions')
    entry['authenticated'] = True


def authenticate_readback(snapshot,source,plan,post,store):
    """Original complete readback cut; NEVER waive actual current readback."""
    entry=_entry(source)
    if entry is None or 'readback' not in entry['value']:
        c.verify_file(snapshot);return
    spec=entry['value'];copied=spec['readback']
    if not entry['authenticated'] or copied['snapshot']!=snapshot:
        raise ValueError('completed readback lacks authenticated source/predecessors')
    anchor=_small(spec['completion']);headers=anchor.get('readback_headers')
    if (not isinstance(headers,dict) or set(headers)!={'artifact_sha256','change_set_id','post_fingerprint_sha256','source_files_verified'}
            or headers['change_set_id']!=plan['change_set_id'] or headers['source_files_verified'] is not True):
        raise ValueError('completed original readback identity/source proof drift')
    # Historical rows, including unchanged context, remain exact SQL versions.
    # The separate epoch current/child/audit verifier still freshly reads live
    # state; a historical readback must not be compared to a later child image.
    from backend.pipeline.viewpoint_applied_source_epochs import read_pinned_versions
    pins=[(*c.split_key(key),payload.get('revision'),c.record_content_sha(payload)) for key,payload in post.items()]
    versions={c.key(*key):value for key,value in read_pinned_versions(pins,store).items()}
    if versions!=post:raise ValueError('completed readback actual SQL versions differ')
    projection=compiled.consume_projection(copied['projection'],sorted(post),
        completion_binding=spec['completion'],snapshot_binding=snapshot,field='post_records',sections=sorted(headers))
    if projection['rows']!=post or projection['absent_keys'] or projection['sections']!=headers:
        raise ValueError('compiled original readback population/content/seal drift')


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


def compile_completed_epoch(source,output_root,store,*,limits=compiled.CompileLimits()):
    """Same explicit compiler for the generic completed source freeze format."""
    from backend.pipeline.viewpoint_validation_context import require_explicit_history_maintenance
    from backend.pipeline import source_reconciliation_epoch as epoch
    require_explicit_history_maintenance('explicit completed source freeze compilation')
    root=Path(output_root)
    if _scope.get() is not None or not root.is_absolute() or root.exists():
        raise ValueError('new unscoped absolute completed epoch compiler root required')
    package,frozen,plan,complete=epoch.load_completed(source)
    assert_applied_plan(package,plan,store)
    original=Path(source['path']).parent
    snapshots=[pin for pin in complete['bindings'] if pin['path']==str(original/'frozen.json')]
    if len(snapshots)!=1:raise ValueError('completed epoch frozen denominator missing/ambiguous')
    snapshot=snapshots[0];reviewed=c.index(frozen['before_records'])
    before=complete_readset(plan,store,reviewed)
    readback_pins=[pin for pin in complete['bindings'] if pin['path']==str(original/'readback.json')]
    if len(readback_pins)!=1:raise ValueError('completed readback denominator missing/ambiguous')
    readback=readback_pins[0]
    headers={field:epoch._snapshot_header_value(Path(readback['path']),field) for field in (
        'artifact_sha256','change_set_id','post_fingerprint_sha256')}
    headers['source_files_verified']=True
    sections={'artifact_sha256':package['source_repair_application']['frozen_sha256']}
    root.mkdir(parents=True)
    anchor=root/'original-applied-anchor.json'
    write(anchor,dict(kind='completed_source_predecessor_compilation',status='complete',
        source=source,plan=binding(original/'plan.json'),bindings=[snapshot,readback],readback_headers=headers,
        original_completed_frame=binding(original/'complete.json'),semantic_approval_granted=False,
        model_calls=0,Registry_writes=0))
    receipt=compiled.compile_projection(binding(anchor),Path(snapshot['path']),sorted(before),
        root/'predecessors.json',field='before_records',sections=sorted(sections),limits=limits)
    post=c.assert_plan_postimages(package,plan,reviewed)
    post_receipt=compiled.compile_projection(binding(anchor),Path(readback['path']),sorted(post),
        root/'readback.json',field='post_records',sections=sorted(headers),limits=limits)
    declaration=root/'completed-predecessor-input.json'
    write(declaration,dict(schema_version=SCHEMA,source=source,completion=binding(anchor),
        snapshot=snapshot,aliases=[snapshot],projection=receipt['projection'],
        readback={'snapshot':readback,'projection':post_receipt['projection']}))
    with predecessor_inputs([binding(declaration)]):
        authenticate_snapshot(snapshot,source,plan,store,plan_path=original/'plan.json',
                              reviewed_rows=reviewed,sections=sections)
        authenticate_readback(readback,source,plan,post,store)
    write(root/'compilation-result.json',{**receipt,'declaration':binding(declaration),
        'whole_operation_population':len(plan['operations']),'complete_review_records':len(reviewed),
        'readset_population':len(before),'create_absences':sum(v is None for v in before.values()),
        'readback_projection':post_receipt,'exact_SQL_readback_equality':True,
        'exact_SQL_predecessor_equality':True,'production_resume_authorized':False})
    return binding(declaration)
