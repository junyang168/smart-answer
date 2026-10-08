"""SQL CVP flow. One store boundary for normal CVP and authorized revisions.

Routine startup reads SQL checkpoints and current dependencies only. Historical
admission is a separate explicit command. No legacy runner or snapshot fallback
is reachable from this entry point.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import (
    ChangeOperation, ChangeSetPlan, PlannedReviewEvent, PostgresKnowledgeStore,
    canonical_json, normalize_package, record_content_sha, sha256_json,
)
from backend.api.canonical_repository.transaction_validation import TransactionValidation
from backend.api.canonical_repository.knowledge_models import (
    ClaimRecord, EvidenceStepRecord, SourceFragmentRecord,
)
from backend.api.canonical_repository.viewpoint_resolution import compile_review_claim
from backend.pipeline.viewpoint_resolution_runtime import (
    read_artifact, read_saved_model_response, stable_decided_at, write_immutable,
)

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


def binding(path):
    from backend.api.canonical_repository.viewpoint_production_safety import file_sha256
    # Authorized SSD archives intentionally retain their original logical
    # path. The archive reader verifies UUID, exact target, size and SHA first.
    from backend.pipeline.viewpoint_backup_archive import logical_binding_path
    path = logical_binding_path(path)
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
    for source in evidence['sources']: check_binding(source)
    check_binding(evidence['backup']['dump'])
    for proof in evidence['approval'].values():
        if isinstance(proof,dict) and {'path','file_sha256'} <= proof.keys(): check_binding(proof)
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


def current_claim_authority(store, cid, row):
    """Consume an actual revision-bound SQL review, not an inherited pass bit."""
    claim = row["payload"]
    if row.get("retired") or claim.get("review_status") in {"human_hold","human_review_required","withheld_unreviewed","rejected","superseded"}:
        raise ValueError("current Claim held or not reviewed: " + cid)
    with store.connect() as conn:
        events = conn.execute("""SELECT review_event_id,reviewer_kind,decision,artifact
                                 FROM wang_knowledge.review_events
                                 WHERE collection='claims' AND object_id=%s AND object_revision=%s
                                   AND decision=%s ORDER BY review_event_id""",
                              (cid,row["revision"],claim.get("review_status"))).fetchall()
    if len(events) != 1 or events[0][1] not in {"ai","human","system"} or not events[0][3]:
        raise ValueError("current Claim has missing/ambiguous exact SQL review: " + cid)
    return {"claim_id":cid,"revision":row["revision"],"content_sha256":row["content_sha256"],
            "review_event_id":events[0][0],"reviewer_kind":events[0][1],"decision":events[0][2],
            "review_artifact_sha256":sha256_json(events[0][3])}


def prepare_group(store, definition, group):
    from backend.api.canonical_repository.models import Citation
    from backend.pipeline.viewpoint_evidence_source_context import attach_source_context,load_context_source
    from backend.pipeline.viewpoint_scope_packet_runner import registry_context
    started = time.perf_counter()
    records = store.read_cvp_context(group["claim_ids"])
    authority = []; claims = []; sources = {}
    for cid in group["claim_ids"]:
        row = records.get(("claims",cid))
        if not row: raise ValueError("original Claim absent from SQL")
        admission = current_claim_authority(store,cid,row); authority.append(admission)
        claim = ClaimRecord.model_validate(row["payload"])
        evidence = {oid:EvidenceStepRecord.model_validate(value["payload"]) for (col,oid),value in records.items() if col=="evidence_steps" and not value.get("retired")}
        fragments = {oid:SourceFragmentRecord.model_validate(value["payload"]) for (col,oid),value in records.items() if col=="source_fragments" and not value.get("retired")}
        source_ids = {fragments[fid].source_id for eid in claim.evidence_step_ids
                      for fid in set(evidence[eid].source_fragment_ids or []) | ({evidence[eid].source_fragment_id} if evidence[eid].source_fragment_id else set())}
        if len(source_ids) != 1: raise ValueError("Claim has no unique bound own source")
        sid = next(iter(source_ids)); doc = records["source_documents",sid]["payload"]
        loaded = load_context_source(doc)
        sources[sid] = {"path":str(loaded[0]),"file_sha256":loaded[1]}
        compiled, findings = compile_review_claim(claim=claim,evidence_index=evidence,fragment_index=fragments,
            citation_index={oid:Citation.model_validate(value["payload"]) for (col,oid),value in records.items() if col=="citations"},
            coverage_source_sha256={sid:doc["source_body_sha256"]},attestation_sha=sha256_json(admission))
        if findings or compiled is None: raise ValueError("current Claim/Evidence qualification failed: " + str(findings))
        claims.append(attach_source_context(compiled,doc).model_dump(mode="json"))
    # Current heads only. Historical CVR chains are never loaded as context.
    with store.connect() as conn:
        rows = conn.execute("""SELECT v.payload,r.payload,v.revision,v.content_sha256,r.revision,r.content_sha256
                               FROM wang_knowledge.objects v
                               LEFT JOIN wang_knowledge.objects r ON r.collection='viewpoint_revisions'
                                 AND r.object_id=v.payload->>'current_revision_id' AND r.retired_at IS NULL
                               WHERE v.collection='canonical_viewpoints' AND v.retired_at IS NULL""").fetchall()
    for view,revision,vrev,vsha,rrev,rsha in rows:
        if (revision is None or revision['viewpoint_id']!=view['viewpoint_id']
                or record_content_sha(view)!=vsha or record_content_sha(revision)!=rsha):
            raise ValueError('current CVP head is missing, foreign or physically inconsistent')
        records['canonical_viewpoints',view['viewpoint_id']]={'payload':view,'revision':vrev,'content_sha256':vsha,'retired':False}
        records['viewpoint_revisions',revision['viewpoint_revision_id']]={'payload':revision,'revision':rrev,'content_sha256':rsha,'retired':False}
    views = registry_context([row[0] for row in rows],[row[1] for row in rows])
    links = [row["payload"] for (col,_),row in records.items() if col=="viewpoint_claim_links" and not row.get("retired") and row["payload"]["claim_id"] in group["claim_ids"]]
    packet = {"batch_id":"CVPB-"+sha256_json({"campaign":definition["campaign_sha256"],"group":group["group_key"]}),
              "scope_label":group["group_key"],"claims":claims,"registry_context":views,"existing_links":links,
              "current_claim_manifest_sha256":sha256_json({"group":group,"authority":authority})}
    size = len(canonical_json(packet).encode("utf-8"))
    sql_size = sum(len(canonical_json(r['payload']).encode()) for r in records.values())
    if size > 64*1024**2 or sql_size > 64*1024**2 or time.perf_counter()-started > 5:
        raise ValueError("SQL preparation exceeds mechanical budget; no legacy fallback")
    return {"packet":packet,"read_pins":pin_rows(pins(records)),"sources":list(sources.values()),
            "claim_authority":authority,"policy":definition["cvp_policy"],"semantic_contract":definition["semantic_contract"],
            "metrics":{"seconds":time.perf_counter()-started,"packet_bytes":size,"current_rows":len(records),
                       "current_head_rows":len(rows)*2,
                       "sql_payload_bytes":sql_size,
                       "historical_snapshot_reads":0}}


def next_group(store, campaign_sha):
    definition = dict(store.get_cvp_campaign(campaign_sha)); definition["campaign_sha256"] = campaign_sha
    progress = store.cvp_campaign_progress(campaign_sha)
    by_key = {row["group_key"]:row for row in progress}
    for group in definition["groups"]:
        row = by_key.get(group["group_key"])
        if row and row["checkpoint"] == "audited": continue
        return definition,group,row
    return definition,None,None


def execution_boundary(root, campaign_sha, definition, stop_after, *, persist=False):
    """An execution root cannot silently lose or expand its owner checkpoint."""
    if stop_after is not None and not 1 <= stop_after <= len(definition['groups']):
        raise ValueError('stop-after-group is outside the accepted original roster')
    path = root / 'execution-boundary.json'
    contract = {'schema_version':'cvp_sql_execution_boundary.v1',
                'campaign_sha256':campaign_sha, 'stop_after_group':stop_after}
    if path.exists() and read_artifact(path) != contract:
        raise ValueError('immutable execution boundary differs; no automatic expansion')
    if persist:
        write_immutable(path, contract)


def checkpoint_reached(group, stop_after):
    return stop_after is not None and (group is None or group['sequence'] > stop_after)


def stage_event(stage, **values):
    print(json.dumps({'stage':stage, **values}), flush=True)


def reviewed_cvp_package(root, materials, store, *, require_resolved=False):
    """Pure existing content compiler; no legacy apply/proof/resume entry."""
    from backend.pipeline.viewpoint_batch_resolution_runner import run_batch
    from backend.pipeline.deferred_claim_cvp_continuation import adapters,resolved_report
    from backend.api.canonical_repository.viewpoint_resolution import ReviewClaim
    from backend.api.canonical_repository.viewpoint_batch_changeset import compile_cvp_batch_package
    packet = materials["packet"]
    models = adapters({"cvp_policy":materials["policy"]})
    check_binding(materials["policy"]); check_binding(materials["semantic_contract"])
    for source in materials["sources"]:check_binding(source)
    report = run_batch(batch_id=packet["batch_id"],scope_label=packet["scope_label"],
        claims=[ReviewClaim.model_validate(row) for row in packet["claims"]],registry_context=packet["registry_context"],
        pending_candidates=[],output_dir=root,current_claim_links=packet["existing_links"],
        dependents_loader=store.load_revision_dependents,**models)
    if report['approval_outcome']!='pass' or report['escalations']:
        raise ValueError('normal CVP terminal review unresolved; no second correction')
    if require_resolved:resolved_report(report,[row["claim_id"] for row in packet["claims"]])
    proposal_name = "raw-reconsideration.json" if report["_final_review"] is not None else "raw-proposal.json"
    review_name = "raw-final-review.json" if report["_final_review"] is not None else "raw-review.json"
    proposal = read_artifact(root/proposal_name); review = read_artifact(root/review_name)
    package = compile_cvp_batch_package(proposal=report["_effective_proposal"],review=report["_review"],
        deterministic_validation_sha256=report["_effective_validation"]["artifact_sha256"],
        scope_manifest_sha256=packet["current_claim_manifest_sha256"],claims=[ReviewClaim.model_validate(row) for row in packet["claims"]],
        registry_context=packet["registry_context"],revision_dependents=report["_revision_dependents"],
        proposal_artifact_sha256=proposal["artifact_sha256"],review_artifact_sha256=review["artifact_sha256"],
        proposer_model_id=proposal["model_id"],reviewer_model_id=review["model_id"],decided_at=stable_decided_at(root),
        current_claim_links=packet["existing_links"])
    write_immutable(root/"reviewed-package.json",package)
    from backend.api.canonical_repository.viewpoint_batch_resolution import build_batch_disposition
    disposition = build_batch_disposition(proposal=report["_effective_proposal"],batch_identity={"batch_id":packet["batch_id"]},
        effective_proposal_sha256=sha256_json(report["_effective_proposal"].model_dump(mode="json")),apply_status="planned")
    return package,{"proposal":binding(root/proposal_name),"review":binding(root/review_name)},disposition


def commit_package(store, run_id, *, package, approval, authorization, backup, sources,
                   read_pins, reason, actor, source_kind="cvp_batch_resolution", viewpoint_ids=(), disposition=None, action=None,
                   pending_cvp_claim_ids=()):
    """The shared normal/source/link execution boundary after content approval."""
    for source in sources:check_binding(source)
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
    with store.connect() as conn:
        context=store._existing_cvp_context(conn,normalize_package(package))
    for key,pin in pins(context).items():
        if key in complete_pins and complete_pins[key]!=pin:
            raise ValueError('model materials changed before SQL approval')
        complete_pins[key]=pin
    for op in plan.operations:
        complete_pins.setdefault((op.collection,op.object_id),None if op.before_revision is None else (op.before_revision,op.before_sha256,False))
    evidence = {"package":{"sha256":sha256_json(package),"payload":package},"approval":approval,
                "authorization":authorization,"backup":backup,"sources":sources,"read_pins":pin_rows(complete_pins),
                "viewpoint_ids":list(viewpoint_ids),"disposition":disposition}
    evidence['execution']={'reason':reason,'actor':actor,'approval':approval,'authorization':authorization,'backup':backup}
    evidence['pending_cvp_claim_ids']=list(pending_cvp_claim_ids)
    if action:evidence['action']=action
    reviewed = store.review_cvp_group(run_id,plan=plan,evidence=evidence)
    context = TransactionValidation(run_id,reviewed,complete_pins)
    result = store.apply_plan(plan,transaction_context=context,metadata=evidence['execution'])
    if result['change_set_id'] is None:return result
    # A separate connection confirms commit visibility. Failure here preserves
    # SQL applied and resumes this read; it never calls apply a second time.
    history = store.get_change_history(plan.change_set_id)
    if not history or history["status"] != "applied": raise ValueError("committed ChangeSet not visible")
    for operation in history['operations']:
        actual=store.get_record_state(operation['collection'],operation['object_id'])
        if (actual is None or actual['payload']!=operation['after']
                or actual['revision']!=operation['after_revision']
                or actual['content_sha256']!=operation['after_sha256']
                or actual['retired']!=(operation['operation']=='retire')):
            raise ValueError('committed current postimage not visible; do not apply again')
    return result


def run_group(store, definition, group, row, *, root):
    campaign = definition["campaign_sha256"]
    if row is None:
        materials = prepare_group(store,definition,group)
        stage_event('prepared', sequence=group['sequence'], **materials['metrics'])
        run_id = store.prepare_cvp_group(campaign_sha256=campaign,group_key=group["group_key"],sequence=group["sequence"],
                                         claim_ids=group["claim_ids"],materials=materials)
    else:
        run_id = row["run_id"]
    saved = store.get_cvp_group(run_id); materials = saved["materials"]
    store.bind_cvp_execution_root(run_id,identity='group',root=root/run_id)
    model_root = root/run_id/"normal-review"
    if saved["checkpoint"] == "prepared":
        # The existing call-intent invariant handles interruption during any
        # model role; the SQL phase remains prepared until all answers pass.
        from backend.pipeline.viewpoint_s201129_source_continuation import canonical_runtime
        runtime = canonical_runtime()
        os.environ["CODEX_EXECUTABLE"] = runtime["identity"]["cli"]["path"]
        stage_event('generating_reviewing', sequence=group['sequence'], run_id=run_id)
        package,approval,disposition = reviewed_cvp_package(model_root,materials,store)
        from backend.pipeline.source_reconciliation_runtime import ProductionPorts
        backup = verified_group_backup(root/run_id,package,ProductionPorts())
        views = sorted({link["viewpoint_id"] for link in package.get("viewpoint_claim_links",[]) if link.get("effective_state")=="active"})
        commit_package(store,run_id,package=package,approval=approval,authorization=definition["authorization"],backup=backup,
                       sources=materials["sources"],read_pins=decoded_pins(materials["read_pins"]),
                       reason="reviewed original CVP group",actor=definition["authorized_by"],viewpoint_ids=views,disposition=disposition)
    saved = store.get_cvp_group(run_id)
    if saved["checkpoint"] == "reviewed":
        resume_reviewed_action(store,run_id)
    saved = store.get_cvp_group(run_id)
    if saved["checkpoint"] != "applied": return
    stage_event('applied', sequence=group['sequence'], run_id=run_id)
    evidence = saved.get('reviewed',{}).get('evidence') or saved.get('import_certificate',{})
    if evidence.get('pending_cvp_claim_ids'):
        from backend.pipeline.cvp_sql_actions import execute_coverage_action
        from backend.pipeline.source_reconciliation_runtime import ProductionPorts
        execute_coverage_action(store,run_id,definition,group,root=root/run_id/'required-coverage',ports=ProductionPorts())
        saved=store.get_cvp_group(run_id);evidence=saved['reviewed']['evidence']
    disposition=evidence.get('disposition')
    if disposition and any(component["disposition"]=="deferred" for claim in disposition["claims"] for component in claim["components"]):
        from backend.pipeline.viewpoint_deferred_source_check import run as investigate
        packet_path=root/run_id/'deferred-investigation-input.json'
        disposition_path=root/run_id/'deferred-disposition.json'
        write_immutable(packet_path,materials['packet']);write_immutable(disposition_path,disposition)
        handoff=investigate(packet_path,disposition_path,root/run_id/'deferred-investigation',store)
        if any(not a['models_agree'] or a['next_action']!='reviewed_cvp_repair_required' for a in handoff['actions']):
            raise ValueError('deferred disposition needs bounded direction: '+
                             json.dumps([{'item_id':a['item_id'],'next_action':a['next_action']} for a in handoff['actions']]))
        from backend.pipeline.cvp_sql_actions import execute_coverage_action
        from backend.pipeline.source_reconciliation_runtime import ProductionPorts
        execute_coverage_action(store,run_id,definition,group,root=root/run_id/'required-coverage',ports=ProductionPorts(),
                                investigation_root=root/run_id/'deferred-investigation')
        saved=store.get_cvp_group(run_id);evidence=saved['reviewed']['evidence']
    current=store.read_cvp_context(group['claim_ids'],viewpoint_ids=evidence['viewpoint_ids'])
    views=sorted(set(evidence['viewpoint_ids'])|{r['payload']['viewpoint_id'] for (col,_),r in current.items()
                 if col=='viewpoint_claim_links' and not r['retired'] and r['payload']['claim_id'] in group['claim_ids']})
    audit_dir = root/run_id/('audit-'+sha256_json(pin_rows(pins(current)))[:20]); audit_path = audit_dir/"audit.json"
    if not audit_path.exists():
        stage_event('independent_audit', sequence=group['sequence'], run_id=run_id)
        audit_dir.mkdir(parents=True,exist_ok=True)
        intent=audit_dir/'audit-intent.json'
        if intent.exists():raise ValueError('independent audit intent lacks saved complete result; no automatic replay')
        write_immutable(intent,{'claim_ids':group['claim_ids'],'viewpoint_ids':views,'read_pins':pin_rows(pins(current)),
                                'model':'gemini-3.8-flash','workers':2,'automatic_retry':False})
        subprocess.run([str(PROJECT/"backend/.venv/bin/python"),"scripts/audit-library.py","--scope","all","--layers","1,2,3,4",
                        "--claim-ids",",".join(group["claim_ids"]),"--viewpoint-ids",",".join(views),
                        "--workers","2","--model","gemini-3.8-flash","--no-status-file","--preserve-full-context","--sql-group-scope","--out",str(audit_dir)],
                       cwd=PROJECT,check=True,env={**os.environ,'KNOWLEDGE_DATABASE_URL':store.database_url})
    from backend.pipeline.viewpoint_content_blocked_fallback import apply as content_fallback
    validate_transaction_audit(read_artifact(audit_path),group['claim_ids'],views)
    audit_path = content_fallback(audit_path,root/run_id/"content-blocked-fallback")
    audit = read_artifact(audit_path)
    resolution=None
    if audit["layers"]["3"].get("disputed") or audit["layers"]["4"].get("disputed"):
        from backend.pipeline.viewpoint_audit_source_adjudication import build_packet,run as adjudicate
        from backend.pipeline.viewpoint_adjudication_history import target_identity
        packet=build_packet(audit_path,store);targets=[target_identity(i) for i in packet['items']]
        prior=store.adjudication_roots(targets)
        resolution=audit_dir/'gpt-source-final-resolution.json'
        from backend.pipeline.viewpoint_reused_source_resolution import compose,create as reuse_part
        from backend.pipeline.viewpoint_adjudication_history import validated_history
        from backend.pipeline.viewpoint_audit_source_adjudication import restricted_packet
        covered=set()
        # Authenticate known decisions before spending any new budget. New
        # findings never cause old unchanged targets to be sent again.
        for previous in prior:
            old,_=validated_history(previous)
            known={target_identity(i) for i in old['items']}
            ids={i['item_id'] for i in packet['items'] if target_identity(i) in known}
            if ids:
                reuse_part(audit_path,previous,audit_dir/('known-'+sha256_json(str(previous))[:20])/'resolution.json',
                           store,only_item_ids=ids)
                covered.update(ids)
        fresh={i['item_id'] for i in packet['items']}-covered
        if fresh:
            subset=restricted_packet(packet,fresh)
            adjudication=audit_dir/('own-source-adjudication-'+sha256_json(sorted(fresh))[:20])
            write_immutable(adjudication/'packet.json',subset)
            fresh_targets=[target_identity(i) for i in subset['items']]
            store.register_adjudication(run_id,targets=fresh_targets,root=adjudication,packet_binding=binding(adjudication/'packet.json'))
            if not (adjudication/'raw-gpt.json').exists():
                from backend.pipeline.viewpoint_s201129_source_continuation import canonical_runtime
                os.environ['CODEX_EXECUTABLE']=canonical_runtime()['identity']['cli']['path']
            report=adjudicate(audit_path,adjudication,store,final_gpt=True,history_roots=[],only_item_ids=fresh)
            if any(a['verdict']!='audit_objection_rejected' for a in report['actions']):
                from backend.pipeline.cvp_sql_actions import execute_supports_action
                from backend.pipeline.source_reconciliation_runtime import ProductionPorts
                execute_supports_action(store,run_id,adjudication,root=audit_dir/'bounded-supports',ports=ProductionPorts())
                # Exactly one narrow correction; the new committed pins choose
                # another complete audit. Unchanged judgement inputs reuse cache.
                return run_group(store,definition,group,store.get_cvp_group(run_id),root=root)
            prior.append(adjudication)
        compose(audit_path,prior,resolution,store)
    store.complete_cvp_group(run_id,audit=audit,audit_binding=binding(audit_path),read_pins=pins(current),
                             owner_resolution_path=resolution)
    stage_event('audited', sequence=group['sequence'], run_id=run_id)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sql-campaign",required=True)
    parser.add_argument("--database-url")
    parser.add_argument("--output-root",type=Path,required=True)
    parser.add_argument("--dry-run",action="store_true")
    parser.add_argument('--execute',action='store_true',help='Explicit new SQL execution; never implicitly resume a stopped campaign')
    parser.add_argument('--stop-after-group',type=int,help='Complete this original group then stop before preparing the next')
    parser.add_argument('--action',choices=('group','source','owner-link'),default='group')
    parser.add_argument('--run-id')
    parser.add_argument('--manifest',type=Path)
    parser.add_argument('--direction',type=Path)
    parser.add_argument('--adjudication-root',type=Path)
    parser.add_argument('--link-id')
    args = parser.parse_args(argv)
    load_dotenv(PROJECT/".env")
    if args.database_url:os.environ['KNOWLEDGE_DATABASE_URL']=args.database_url
    store = PostgresKnowledgeStore(args.database_url)
    definition,group,row = next_group(store,args.sql_campaign)
    execution_boundary(args.output_root,args.sql_campaign,definition,args.stop_after_group)
    if checkpoint_reached(group,args.stop_after_group):
        stage_event('owner_checkpoint', stop_after_group=args.stop_after_group)
        return 0
    if group is None:
        print(json.dumps({"status":"complete","groups":len(definition["groups"])})); return 0
    if args.dry_run:
        if args.action!='group':raise ValueError('bounded actions require their own saved direction, not a group dry run')
        result = prepare_group(store,definition,group)
        print(json.dumps({"status":"prepared_no_models_no_writes","group":group,**result["metrics"]})); return 0
    if not args.execute:
        raise RuntimeError('production remains stopped; explicit new SQL --execute required, no legacy fallback')
    from backend.pipeline.source_reconciliation_runtime import maintenance_lock
    with maintenance_lock():
        execution_boundary(args.output_root,args.sql_campaign,definition,args.stop_after_group,persist=True)
        if args.action!='group':
            if not args.run_id:raise ValueError('bounded action requires exact admitted SQL run ID')
            saved=store.get_cvp_group(args.run_id)
            if not saved or saved['campaign_sha256']!=args.sql_campaign:raise ValueError('bounded action campaign drift')
            if args.stop_after_group is not None and saved['sequence'] > args.stop_after_group:
                raise ValueError('action exceeds owner execution boundary')
            action_root=args.output_root/args.run_id/'actions'
            from backend.pipeline.cvp_sql_actions import execute_source_action,execute_owner_link_action
            from backend.pipeline.source_reconciliation_runtime import ProductionPorts
            if args.action=='source':
                if not args.manifest:raise ValueError('source action lacks bounded Manifest/authorization')
                execute_source_action(store,args.run_id,read_artifact(args.manifest),root=action_root/'source',ports=ProductionPorts())
            else:
                if not all((args.direction,args.adjudication_root,args.link_id)):raise ValueError('owner link direction incomplete')
                from backend.pipeline.owner_approved_link_correction import ReviewPorts
                execute_owner_link_action(store,args.run_id,args.adjudication_root,link_id=args.link_id,
                    authorization=binding(args.direction),root=action_root/'owner-link',ports=ReviewPorts())
            definition,group,row=next_group(store,args.sql_campaign)
        while group is not None:
            if checkpoint_reached(group,args.stop_after_group):
                stage_event('owner_checkpoint', stop_after_group=args.stop_after_group)
                break
            try:
                run_group(store,definition,group,row,root=args.output_root)
                finished = next((r for r in store.cvp_campaign_progress(args.sql_campaign)
                                 if r['group_key']==group['group_key']),None)
                if not finished or finished['checkpoint']!='audited':
                    raise ValueError('group returned without actual SQL audited checkpoint')
            except Exception as exc:
                current = store.cvp_campaign_progress(args.sql_campaign)
                active = next((r for r in current if r["group_key"]==group["group_key"]),None)
                if active:store.fail_cvp_group(active["run_id"],str(exc))
                raise
            if group['sequence']==args.stop_after_group:
                stage_event('owner_checkpoint', stop_after_group=args.stop_after_group)
                break
            definition,group,row = next_group(store,args.sql_campaign)
    return 0


if __name__ == "__main__": raise SystemExit(main())
