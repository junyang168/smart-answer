"""One SQL commit boundary shared by CVP orchestration and maintenance.

This module owns exact material bindings, review resume, approved transaction
submission and commit readback. It contains no campaign CLI or model dispatch.
Historical backup, read-pin, approval and audit requirements are unchanged.
"""
from __future__ import annotations

from pathlib import Path

from backend.api.canonical_repository.postgres_store import (
    ChangeOperation, ChangeSetPlan, PlannedReviewEvent, sha256_json,
)
from backend.api.canonical_repository.transaction_validation import TransactionValidation
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable

PROJECT = Path(__file__).resolve().parents[2]


NORMAL_COLLECTIONS = {"canonical_viewpoints","viewpoint_revisions","viewpoint_claim_links",
                      "viewpoint_relations","viewpoint_identity_candidates","viewpoint_identity_decisions",
                      "viewpoint_structures","viewpoint_structure_revisions","viewpoint_coverage_snapshots",
                      "viewpoint_resolution_ledgers","viewpoint_quality_reports"}


def validate_transaction_audit(audit,claim_ids,viewpoint_ids):
    scope=audit.get('meta',{}).get('registry_read_scope')
    if (not scope or scope.get('kind')!='sql_transaction_group' or scope.get('complete_target_members') is not True
            or set(scope.get('claim_ids',[]))!=set(claim_ids) or set(scope.get('viewpoint_ids',[]))!=set(viewpoint_ids)):
        raise ValueError('SQL completion requires exact complete current group audit scope')
    first=audit['layers']['1'];second=audit['layers']['2']
    if any(value for name,value in first['counts'].items() if name!='pass') or first.get('findings'):
        raise ValueError('affected current source/fragment audit failed')
    if first.get('version_binding',{}).get('stale') or first.get('version_binding',{}).get('missing'):
        raise ValueError('affected current source version is not bound')
    for key in ('findings','dangling','ambiguous_paths','source_document_findings','attestation_findings',
                'derived_scripture_findings','component_locator_findings','retired_evidence_findings',
                'struck_evidence_findings','change_set_readback_findings'):
        if second.get(key):raise ValueError('affected current structural audit failed: '+key)
    anchors=second.get('claim_occurrence_anchors',{})
    if anchors.get('findings') or anchors.get('error_count'):raise ValueError('affected current occurrence audit failed')
    reciprocity=second.get('claim_evidence_reciprocity',{})
    if any(reciprocity.get(k) for k in ('findings','error_count','claim_only','evidence_only','dangling','duplicate_arrays')):
        raise ValueError('affected current Claim/Evidence reciprocity failed')


def _under_relocated_staging(path: Path) -> bool:
    """True for a file under Wang staging after OPS-38 moved it behind a symlink.

    Bindings recorded before the move hold the /opt path, later ones may hold
    the SSD path; both name the same file, and the SHA still proves content.
    """
    from backend.api.config import WANG_STAGING_DIR
    link = Path(WANG_STAGING_DIR)
    if not link.is_symlink():
        return False
    return path.is_relative_to(link) or path.is_relative_to(link.resolve())


def binding(path):
    import os
    from backend.api.canonical_repository.viewpoint_production_safety import file_sha256
    # Authorized SSD archives intentionally retain their original logical
    # path. The archive reader verifies UUID, exact target, size and SHA first.
    from backend.pipeline.viewpoint_backup_archive import logical_binding_path
    given = Path(os.path.normpath(Path(path).absolute()))
    # A staging file keeps the path it was named by, so the staging move does
    # not read as drift; archive symlinks inside staging keep their own check.
    path = given if _under_relocated_staging(given) and not given.is_symlink() else logical_binding_path(path)
    return {"path":str(path),"file_sha256":file_sha256(path)}


def check_binding(value):
    if binding(value["path"]) != value: raise ValueError("exact SQL material/source binding drift")


def verified_group_backup(root, package, ports):
    """Persist fresh backup before commit; recover only its exact SSD receipt.

    A crash after pg_dump and before this small record must not create another
    backup, overwrite an archive, or repeat an already reviewed model request.
    """
    from backend.pipeline.viewpoint_backup_archive import LOCAL, NAME, RECEIPT, catalog
    path = root/'backup.json'
    package_sha = sha256_json(package)
    if path.exists():
        saved = read_artifact(path)
        if saved['package_sha256'] != package_sha:
            raise ValueError('saved backup belongs to a different reviewed package')
        backup = saved['backup']
    else:
        label = 'before-source-357-' + sha256_json(str(root))[:32]
        attempted = list(LOCAL.glob('registry-backup-*-'+label))
        if len(attempted)>1:
            raise ValueError('ambiguous backup attempts for exact SQL execution root')
        if attempted:
            dump = attempted[0]/NAME
            receipt = read_artifact(attempted[0]/RECEIPT)
            if receipt.get('artifact_sha256') != sha256_json({k:v for k,v in receipt.items() if k!='artifact_sha256'}):
                raise ValueError('saved direct SSD backup receipt seal differs')
            dump_binding = binding(dump)
            if not receipt.get('direct_ssd') or dump_binding != {'path':receipt['original_path'],'file_sha256':receipt['file_sha256']}:
                raise ValueError('exact direct SSD backup identity differs')
            backup = {'dump':dump_binding,'restore_catalog_nonempty':True,
                      'catalog_sha256':catalog(dump),'restore_list_exit_code':0}
            if backup['catalog_sha256'] != receipt['restore_catalog_sha256']:
                raise ValueError('current backup restore catalog differs')
        else:
            backup = ports.backup(root)
        write_immutable(path, {'package_sha256':package_sha,'backup':backup})
    check_binding(backup['dump'])
    if not backup.get('restore_catalog_nonempty') or backup.get('restore_list_exit_code') != 0:
        raise ValueError('saved backup lacks successful restore catalog')
    if catalog(Path(backup['dump']['path'])) != backup['catalog_sha256']:
        raise ValueError('saved backup catalog drift')
    return backup


def plan_from_document(value):
    return ChangeSetPlan(**{name:value[name] for name in (
        "change_set_id","fingerprint_sha256","package_id","source_kind","source_sha256","unchanged")},
        ignored_keys=tuple(value["ignored_keys"]),operations=tuple(ChangeOperation(**op) for op in value["operations"]),
        review_events=tuple(PlannedReviewEvent(**event) for event in value.get("review_events",[])))


def resume_reviewed_action(store, run_id):
    """One recovery boundary: consume SQL approval, never rebuild or re-review."""
    saved = store.get_cvp_group(run_id)
    if saved['checkpoint'] != 'reviewed': return None
    evidence = saved['reviewed']['evidence']
    from backend.pipeline.source_editorial_transition import verify_current_source_binding
    for source in evidence['sources']: verify_current_source_binding(source)
    if saved['reviewed']['plan']['source_kind'] not in {'wang_claim_management_v2','wang_cvp_management_v2','wang_cvp_contextual_summary_v1'}:
        check_binding(evidence['backup']['dump'])
    for proof in evidence['approval'].values():
        if isinstance(proof,dict) and {'path','file_sha256'} <= proof.keys(): check_binding(proof)
    plan = plan_from_document(saved['reviewed']['plan'])
    if plan.source_kind == 'cvp_batch_resolution':
        store.retain_reviewed_cvp_relations(run_id,plan=plan,reviewed_sha256=saved['reviewed_sha256'])
        saved = store.get_cvp_group(run_id)
        evidence = saved['reviewed']['evidence']
    return store.apply_plan(plan_from_document(saved['reviewed']['plan']),
        transaction_context=TransactionValidation(run_id,saved['reviewed_sha256'],decoded_pins(evidence['read_pins'])),
        metadata=evidence['execution'])


def pins(records, *, absent=()):
    result = {key:(int(row["revision"]),str(row["content_sha256"]),bool(row.get("retired"))) for key,row in records.items()}
    for key in absent: result.setdefault(key,None)
    return result


def pin_rows(values):
    return [{"collection":k[0],"object_id":k[1],"pin":list(v) if v else None} for k,v in sorted(values.items())]


def decoded_pins(rows):
    return {(row["collection"],row["object_id"]):tuple(row["pin"]) if row["pin"] else None for row in rows}


def commit_package(store, run_id, *, package, approval, authorization, backup, sources,
                   read_pins, reason, actor, source_kind="cvp_batch_resolution", viewpoint_ids=(), disposition=None, action=None,
                   pending_cvp_claim_ids=(), pending_cvp_management_record_keys=None, management_evidence=None):
    """The shared normal/source/link execution boundary after content approval."""
    from backend.pipeline.source_editorial_transition import verify_current_source_binding
    for source in sources:verify_current_source_binding(source)
    if source_kind not in {'wang_claim_management_v2','wang_cvp_management_v2','wang_cvp_contextual_summary_v1'}:
        if not backup.get('restore_catalog_nonempty') or not backup.get('dump'):
            raise ValueError('fresh backup lacks verified restore-list proof')
        check_binding(backup['dump'])
    from backend.api.canonical_repository.reviewed_candidate_contract import validate_store_package_authorization
    validate_store_package_authorization(package)
    plan = store.plan_package(package,source_kind=source_kind,transactional=True)
    if source_kind == "cvp_batch_resolution" and any(op.collection not in NORMAL_COLLECTIONS for op in plan.operations):
        raise ValueError("normal CVP package exceeds authorized content scope")
    complete_pins = dict(read_pins)
    # Complete unchanged dependencies are SQL-approved current facts, not new
    # model judgements. The original material pins (including every current
    # CVP head shown to the model) must remain exact. New revision dependents
    # were included in the Opus packet by the indexed dependents loader.
    context=store.read_package_cvp_context(package,
        retiring=[(op.collection,op.object_id) for op in plan.operations if op.operation=='retire'])
    if source_kind=='wang_cvp_management_v2':
        context.update(store.read_current_dependency_context(complete_pins))
    for key,pin in pins(context).items():
        if key in complete_pins and complete_pins[key]!=pin:
            raise ValueError('model materials changed before SQL approval')
        complete_pins[key]=pin
    for op in plan.operations:
        complete_pins.setdefault((op.collection,op.object_id),None if op.before_revision is None else (op.before_revision,op.before_sha256,False))
    evidence = {"package":{"sha256":sha256_json(package),"payload":package},"approval":approval,
                "authorization":authorization,"backup":backup,"sources":sources,"read_pins":pin_rows(complete_pins),
                "viewpoint_ids":list(viewpoint_ids),"disposition":disposition}
    if source_kind=='wang_cvp_management_v2':
        if not management_evidence:raise ValueError('CVP2 requires complete scoped management approval')
        evidence['cvp_management']=management_evidence
    evidence['execution']={'reason':reason,'actor':actor,'approval':approval,'authorization':authorization,'backup':backup}
    evidence['pending_cvp_claim_ids']=list(pending_cvp_claim_ids)
    application = package.get('source_repair_application') or {}
    if application.get('schema_version') == 'wang_claim_management_v2':
        from backend.pipeline.source_reconciliation_runner import body, read
        material = body(read(Path(application['materials']['path'])))
        evidence['claim_management_scope'] = {'consumer_roots': [list(k) for k in sorted({
            (key.split('/',1)[0], key.split('/',1)[1]) for key in material['unit_request']['target_keys']} |
            {(p['collection'],p['object_id']) for p in material['unit_request']['permissions']})]}
        if 'human_source_decisions' in material:
            evidence['claim_management_scope'].update(source_ids=sorted(material['unit_request']['source_bindings']),
                human_source_decisions=material['human_source_decisions'])
        from backend.pipeline.claim_direct_scope import enabled as direct_scope_enabled
        if direct_scope_enabled(material['unit_request']):
            evidence['claim_management_scope'].update(claim_scope_request=material['unit_request'],
                audit_record_keys=sorted(material['before']))
        from backend.pipeline.claim_joint_update import CONTRACT
        if material['original_goal'].get('joint_update_contract') == CONTRACT:
            evidence['claim_management_scope'].update(joint_update_contract=CONTRACT,
                audit_record_keys=sorted(material['before']))
        evidence['pending_cvp_management_record_keys'] = application['pending_consumers']
    previous_pending=(store.get_cvp_group(run_id) or {}).get('reviewed',{}).get('evidence',{}).get('pending_cvp_management_record_keys',[])
    # Pending is a durable obligation, not a request to mutate every row.
    # Even an explicit empty list must not erase an earlier obligation.
    pending_cvp_management_record_keys=list(dict.fromkeys([*previous_pending,*(pending_cvp_management_record_keys or [])]))
    if pending_cvp_management_record_keys:
        evidence['pending_cvp_management_record_keys']=list(pending_cvp_management_record_keys)
    if action:evidence['action']=action
    reviewed = store.review_cvp_group(run_id,plan=plan,evidence=evidence)
    if source_kind == 'cvp_batch_resolution':
        reviewed = store.retain_reviewed_cvp_relations(run_id,plan=plan,reviewed_sha256=reviewed)
        saved = store.get_cvp_group(run_id)['reviewed']
        plan = plan_from_document(saved['plan'])
        evidence = saved['evidence']
        complete_pins = decoded_pins(evidence['read_pins'])
    context = TransactionValidation(run_id,reviewed,complete_pins)
    result = store.apply_plan(plan,transaction_context=context,metadata=evidence['execution'])
    if result['change_set_id'] is None:return result
    # A separate connection confirms commit visibility. Failure here preserves
    # SQL applied and resumes this read; it never calls apply a second time.
    history = store.get_change_history(plan.change_set_id)
    if not history or history["status"] != "applied": raise ValueError("committed ChangeSet not visible")
    for operation in history['operations']:
        actual=store.get_record_state(operation['collection'],operation['object_id'])
        expected_payload=operation['after']
        # Retirement advances storage/history revision while deliberately
        # preserving the original object payload byte for byte. Do not treat
        # that documented distinction as a missing commit or reapply it.
        if operation['operation']=='retire':
            expected_payload={**expected_payload,'revision':operation['before_revision']}
        if (actual is None or actual['payload']!=expected_payload
                or actual['revision']!=operation['after_revision']
                or actual['content_sha256']!=operation['after_sha256']
                or actual['retired']!=(operation['operation']=='retire')):
            raise ValueError('committed current postimage not visible; do not apply again')
    return result
