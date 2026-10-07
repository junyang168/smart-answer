"""Compose separately authenticated source transitions; never ID/status waivers."""
from pathlib import Path
from backend.pipeline import viewpoint_g041_source_epoch as historical
from backend.pipeline import viewpoint_s201129_source_epoch as local

GROUPING_EPOCH_KEY = historical.GROUPING_EPOCH_KEY
VERSION = 'wang_exact_applied_source_epochs_v1'


def has_applied_source_frame(path, package):
    """Recognize the saved physical format, never a source-ID admission rule."""
    app=package.get('source_repair_application') or {}
    # Classify by the package's original declared format, NOT whether its
    # receipts happen to be present/pass. Missing or damaged receipts must
    # reach the generic hard stop, not an expensive legacy-writer fallback.
    return bool(app.get('before_binding')) and 'bindings' in app


def consume_applied_source_package(bound, store):
    """Authenticate an original applied package, not rebuild its old writer.

    The actual SQL KCS binds the ENTIRE package and exact operation/review-event
    fingerprint. This grants no new semantic approval, current postimage waiver
    or group completion. The original writer, source/hold/current and completed
    group audit gates remain separate. No sermon IDs or source exceptions here.
    Only saved applications with an explicit complete physical denominator may
    use this adapter; missing older proof shapes stop, never fall back to a full
    historical snapshot reconstruction.
    """
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact as read
    from backend.pipeline.source_reconciliation import verify_file
    from backend.api.canonical_repository.postgres_store import (
        ChangeOperation,PlannedReviewEvent,operation_fingerprint_rows,
        review_event_fingerprint_rows,record_content_sha)
    from backend.api.canonical_repository.viewpoint_foundation import sha256_json
    path=verify_file(bound);root=path.parent;package=read(path)
    app=package.get('source_repair_application') or {}
    if path.name!='effective-package.json' or not app.get('schema_version'):
        raise ValueError('completed source application package identity missing')
    plan=read(root/'dry-run-plan.json');receipt=read(root/'apply-receipt.json')
    verified=read(root/'verified-result.json')
    pins=verified.get('bindings',[])
    required={str(root/name) for name in ('before.json','effective-package.json',
        'dry-run-plan.json','backup.json','pg-restore-proof.json','apply-intent.json','apply-receipt.json')}
    if (len(pins)!=len({pin['path'] for pin in pins}) or not required<={pin['path'] for pin in pins}
            or bound not in pins or app.get('before_binding') not in pins):
        raise ValueError('applied source physical proof denominator missing/changed')
    for pin in pins:verify_file(pin)
    if any(pin not in pins for pin in app.get('bindings',[])):
        raise ValueError('original reviewed source bindings omitted from application proof')
    ops=[ChangeOperation(**row) for row in plan['operations']]
    events=[PlannedReviewEvent(**row) for row in plan.get('review_events',[])]
    if (len(ops)!=len({(op.collection,op.object_id) for op in ops})
            or any(op.after_sha256!=record_content_sha(op.payload) for op in ops)):
        raise ValueError('applied source operation population/payload SHA drift')
    source_sha=sha256_json(package)
    fp=sha256_json({'planner_schema':'wang_postgres_changeset_v2',
        'source_kind':app['schema_version'],'source_sha256':source_sha,
        'package_id':str(package.get('package_id') or ''),
        'operations':operation_fingerprint_rows(ops),'review_events':review_event_fingerprint_rows(events)})
    if (plan['source_kind']!=app['schema_version'] or plan['source_sha256']!=source_sha
            or plan['fingerprint_sha256']!=fp or plan['change_set_id']!='KCS-'+fp[:20]
            or plan['package_id']!=package['package_id'] or receipt.get('status')!='applied'
            or receipt['change_set_id']!=plan['change_set_id']
            or verified.get('status')!='applied_readback_verified'
            or verified['change_set_id']!=plan['change_set_id']
            or verified['master_data_mutations']!=len(ops)):
        raise ValueError('original package/application/readback identity drift')
    intent=read(root/'apply-intent.json')
    if (intent.get('package_sha256')!=source_sha or intent.get('plan_sha256')!=sha256_json(plan)
            or intent.get('single_apply_required') is not True
            or intent.get('schema_version')!=app['schema_version']
            or intent.get('pre_fingerprint_sha256')!=verified['pre_fingerprint_sha256']):
        raise ValueError('original applied source intent/package drift')
    with store.connect() as conn,conn.cursor() as cursor:
        cursor.execute('SET TRANSACTION READ ONLY')
        cursor.execute('SELECT source_kind,source_sha256,fingerprint_sha256,status,summary FROM wang_knowledge.change_sets WHERE change_set_id=%s',
            (plan['change_set_id'],))
        row=cursor.fetchone()
        if (not row or tuple(row[:4])!=(app['schema_version'],source_sha,fp,'applied')
                or row[4]!=receipt['summary']):
            raise ValueError('original source has no matching actual applied SQL proof')
    return package


def applied_source_preimages(bound, store):
    """Read exact old SQL versions declared by an authenticated applied plan.

    This is a scoped historical read, not current approval or a whole snapshot.
    Missing versions, unknown operation shapes or a content mismatch stop; no
    archived Registry reconstruction is a fallback. Unchanged context is NOT
    inferred here and must come from the caller's original bound review input.
    """
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    from backend.api.canonical_repository.postgres_store import record_content_sha
    package = consume_applied_source_package(bound, store)
    plan = read_artifact(Path(bound['path']).parent/'dry-run-plan.json')
    wanted, result = [], {}
    for op in plan['operations']:
        key = op['collection'], op['object_id']
        if op['operation'] == 'create':
            if op['before_revision'] is not None or op['before_sha256'] is not None:
                raise ValueError('created object has a declared predecessor')
            result[key] = None
        elif (op['operation'] == 'update' and type(op['before_revision']) is int
                and op['before_revision'] > 0 and isinstance(op['before_sha256'], str)
                and len(op['before_sha256']) == 64):
            wanted.append((*key, op['before_revision'], op['before_sha256']))
        else:
            raise ValueError('unsupported or unpinned applied predecessor operation')
    if wanted:
        placeholders = ','.join(['(%s,%s,%s::integer,%s)']*len(wanted))
        with store.connect() as conn, conn.cursor() as cursor:
            cursor.execute('SET TRANSACTION READ ONLY')
            cursor.execute('''SELECT ov.collection,ov.object_id,ov.revision,ov.content_sha256,ov.payload
                FROM wang_knowledge.object_versions ov JOIN (VALUES '''+placeholders+''')
                AS wanted(collection,object_id,revision,content_sha256)
                ON ov.collection=wanted.collection AND ov.object_id=wanted.object_id
                AND ov.revision=wanted.revision AND ov.content_sha256=wanted.content_sha256''',
                tuple(value for pin in wanted for value in pin))
            rows = cursor.fetchall()
        expected = {pin[:2]: pin[2:] for pin in wanted}
        if len(rows) != len(expected):
            raise ValueError('applied predecessor SQL version population missing/duplicated')
        for collection, oid, revision, digest, payload in rows:
            key = collection, oid
            if (key not in expected or key in result or (revision,digest) != expected[key]
                    or not isinstance(payload, dict) or payload.get('revision') != revision
                    or record_content_sha(payload) != digest):
                raise ValueError('applied predecessor SQL identity/version/content drift')
            result[key] = payload
    return package, result


def implementation(bound):
    path = Path(bound.get('path',''))
    if path == historical.PACKAGE:
        return historical
    if path == local.repair.PACKAGE:
        return local
    if path.is_file():
        from backend.pipeline import source_reconciliation_epoch as generic
        # Route only a recognized generic artifact. Its validator then proves
        # the actual review/apply/completion, not merely this schema label.
        import json
        raw = json.loads(path.read_text(encoding='utf-8'))
        if (raw.get('source_repair_application') or {}).get('schema_version') == 'wang_owner_claim_statement_revision_v1':
            from backend.pipeline import claim_statement_revision as statement
            if statement.binding(path) != bound:
                raise ValueError('statement revision physical package drift')
            statement.validate_package(raw)
            return statement
        if (raw.get('source_repair_application') or {}).get('schema_version') == 'wang_source_reconciliation_v1':
            if generic.file_binding(path) != bound:
                raise ValueError('generic epoch physical package drift')
            generic.load(bound)
            return generic
    # Preserve the legacy entry's own exact physical/path validator; an
    # unknown single binding is not accepted by this routing operation.
    return historical


def members(bound):
    if bound.get('schema_version') != VERSION:
        implementation(bound)
        return [bound]
    if set(bound) != {'schema_version','bindings'} or not bound['bindings']:
        raise ValueError('invalid composite source epoch shape')
    rows = bound['bindings']
    if rows != sorted(rows,key=lambda b:b['path']) or len(rows) != len({r['path'] for r in rows}):
        raise ValueError('duplicate or unordered source epoch proofs')
    for row in rows:
        if set(row) != {'path','file_sha256'}:
            raise ValueError('nested or unbound source epoch')
        routed = implementation(row)
        if Path(row['path']) not in {historical.PACKAGE, local.repair.PACKAGE} and routed is historical:
            raise ValueError('unrecognized composite source epoch package')
    return rows


def compose(paths):
    known = {historical.PACKAGE:historical,local.repair.PACKAGE:local}
    selected = []
    generic_rows = {}
    for raw_path in paths:
        path = Path(raw_path)
        if path in known:
            selected.append(path)
        elif path.is_file():
            from backend.pipeline.source_reconciliation_runner import binding
            bound = binding(path)
            routed = implementation(bound)
            if routed is not historical:
                selected.append(path)
                generic_rows[path] = bound
    if len(selected) != len(set(selected)):
        raise ValueError('duplicate applied source epoch path')
    rows = [known[p].binding() if p in known else generic_rows[p] for p in sorted(selected)]
    if not rows:
        return None
    return rows[0] if len(rows) == 1 else {'schema_version':VERSION,'bindings':rows}


def verified_applied(bound, store):
    return [implementation(row).verified_applied(row,store) for row in members(bound)]


def grouping_physical_transition(manifest, bound):
    transitions = {}
    for row in members(bound):
        current = implementation(row).grouping_physical_transition(manifest,row)
        if transitions.keys() & current.keys():
            raise ValueError('conflicting source physical transitions')
        transitions.update(current)
    return transitions


def bridge(**kwargs):
    return implementation(kwargs['source_repair_binding']).bridge(**kwargs)


def grouping_current_claim_packets(manifest, bound, claim_ids):
    """Only a fully proved transition may replace old content comparison pins."""
    result = {}
    for row in members(bound):
        projector = getattr(implementation(row), 'grouping_current_claim_packets', None)
        if projector is None:
            continue  # No projection authority: the original strict check stays.
        projected = projector(manifest, row, claim_ids)
        if result.keys() & projected.keys() or set(projected) - set(claim_ids):
            raise ValueError('conflicting or foreign source epoch grouping projection')
        result.update(projected)
    return result
