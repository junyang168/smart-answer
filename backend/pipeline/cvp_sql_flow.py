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
    if not events:
        from backend.pipeline.cvp_sql_retained_evidence_locator_authority import authority as fixed_authority
        fixed=fixed_authority(store,cid,row)
        if fixed is not None:return fixed
        return coordinate_predecessor_authority(store, cid, row)
    if len(events) != 1 or events[0][1] not in {"ai","human","system"} or not events[0][3]:
        raise ValueError("current Claim has missing/ambiguous exact SQL review: " + cid)
    return {"claim_id":cid,"revision":row["revision"],"content_sha256":row["content_sha256"],
            "review_event_id":events[0][0],"reviewer_kind":events[0][1],"decision":events[0][2],
            "review_artifact_sha256":sha256_json(events[0][3])}


def coordinate_predecessor_authority(store, cid, row):
    """Carry one actual SQL review over an exact applied locator-only step.

    No new review event, status mutation, history-chain replay or legacy runner.
    The immediate predecessor must have its own unique review, either a bound
    SQL event or an exact passing imported-input/SQL-graph proof. Inherited
    review notes grant no authority. The original bounded plan, actual SQL
    operation and physical body must all agree, and the original deterministic
    locator compiler must reproduce the full row. Never walk older versions.
    """
    from backend.pipeline.cvp_sql_claim_authority import coordinate_authority
    return coordinate_authority(store, cid, row)


def prepare_group(store, definition, group):
    from backend.api.canonical_repository.models import Citation
    from backend.pipeline.viewpoint_evidence_source_context import attach_source_context,load_context_source
    from backend.pipeline.viewpoint_scope_packet_runner import registry_context
    started = time.perf_counter()
    records = store.read_cvp_context(group["claim_ids"])
    authority = []; claims = []; sources = {}; unavailable = []
    for cid in group["claim_ids"]:
        row = records.get(("claims",cid))
        if not row: raise ValueError("original Claim absent from SQL")
        admission = current_claim_authority(store,cid,row); authority.append(admission)
        for p in admission.get('current_dependency_pins',[]):
            if 'pin' not in p:continue  # other typed authority uses its own representation
            key=(p['collection'],p['object_id']);live=store.get_record_state(*key)
            if (not live or [live['revision'],live['content_sha256'],live['retired']]!=p['pin']
                    or record_content_sha(live['payload'])!=p['pin'][1]):
                raise ValueError('Claim authority current dependency drift')
            records[key]=live
        claim = ClaimRecord.model_validate(row["payload"])
        evidence = {oid:EvidenceStepRecord.model_validate(value["payload"]) for (col,oid),value in records.items() if col=="evidence_steps" and not value.get("retired")}
        fragments = {oid:SourceFragmentRecord.model_validate(value["payload"]) for (col,oid),value in records.items() if col=="source_fragments" and not value.get("retired")}
        missing_anchor_ids=set()
        for eid in row['payload'].get('withheld_evidence_step_ids',[]):
            step=evidence.get(eid)
            if (step and step.support_eligibility=='withheld_missing_anchor' and step.anchor_quality=='missing'
                    and not step.source_fragment_id and not step.source_fragment_ids):
                if eid not in claim.evidence_step_ids or cid not in step.produced_claim_ids:
                    raise ValueError('unbound withheld Evidence lacks exact own Claim binding')
                state=records['evidence_steps',eid]
                if state['content_sha256']!=record_content_sha(state['payload']):
                    raise ValueError('unbound withheld Evidence current SHA drift')
                missing_anchor_ids.add(eid)
                unavailable.append({'claim_id':cid,'evidence_step_id':eid,
                    'current_pin':[state['revision'],state['content_sha256'],False],
                    'payload':state['payload'],'valid_for_identity_review':False,
                    'reason':'withheld_missing_anchor; no usable source-fragment binding'})
        source_ids = {fragments[fid].source_id for eid in claim.evidence_step_ids
                      for fid in set(evidence[eid].source_fragment_ids or []) | ({evidence[eid].source_fragment_id} if evidence[eid].source_fragment_id else set())}
        if len(source_ids) != 1: raise ValueError("Claim has no unique bound own source")
        sid = next(iter(source_ids)); doc = records["source_documents",sid]["payload"]
        loaded = load_context_source(doc)
        sources[sid] = {"path":str(loaded[0]),"file_sha256":loaded[1]}
        compiled, findings = compile_review_claim(claim=claim,evidence_index=evidence,fragment_index=fragments,
            citation_index={oid:Citation.model_validate(value["payload"]) for (col,oid),value in records.items() if col=="citations"},
            coverage_source_sha256={sid:doc["source_body_sha256"]},attestation_sha=sha256_json(admission),
            unavailable_evidence_ids=frozenset(missing_anchor_ids))
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
    if unavailable:
        packet['unavailable_evidence_receipt']={'schema_version':'wang_withheld_unbound_evidence_disclosure_v1',
            'rows':unavailable,'instruction':'Unavailable rows are disclosed history, NOT evidence or permission to restore qualification.'}
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


def exact_saved_upheld_items(old, packet, report, ids):
    """Resume the existing narrow action; upheld is never a rejected proof."""
    from backend.pipeline.viewpoint_reused_source_resolution import exact_item
    upheld={a['item_id'] for a in report['actions'] if a['verdict']=='audit_objection_upheld'}
    if not upheld & ids:return set()
    old_index={i['item_id']:i for i in old['items']}
    current_index={i['item_id']:i for i in packet['items']}
    if not upheld <= ids or any(not exact_item(old_index.get(i),current_index.get(i)) for i in upheld):
        raise ValueError('saved upheld postimage input changed; no implicit new review')
    return upheld


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
    from backend.pipeline.claim_issue_management import IssuesDetected,review_findings
    def identity_source_loader(source_ids):
        from backend.pipeline.viewpoint_evidence_source_context import load_context_source
        result=[]
        for sid in source_ids:
            if sid not in {c['source_id'] for c in packet['claims']}:
                raise ValueError('integration context crossed original Claim source identity')
            source=store.get_record('source_documents',sid)
            if not source:
                raise ValueError('integration own source missing or retired')
            path,physical_sha,projection=load_context_source(source)
            if {'path':str(path),'file_sha256':physical_sha} not in materials['sources']:
                raise ValueError('integration source is outside prepared physical source bindings')
            result.append({'source_id':sid,'source_type':source['source_type'],
                'source_path':str(path),'source_file_sha256':physical_sha,
                'source_body_sha256':projection.body_sha256,
                'paragraph_texts':[r['text'] for r in projection.spoken_rows]})
        return result
    def claim_issue_handler(*,proposal,review,origin,report_path,correction_used,terminal_refusal):
        findings=review_findings(proposal,review)
        if findings:
            raise IssuesDetected(findings,origin=origin,report=binding(report_path),
                                 correction_used=correction_used,terminal_refusal=terminal_refusal)
    def retained_review_loader(**kwargs):
        from backend.pipeline.cvp_retained_review_separation import build_boundary,policy_for
        if policy_for(root) is None:return None
        expected=decoded_pins(materials['read_pins'])
        with store.connect() as conn, conn.cursor() as cursor:
            store._assert_transaction_read_pins(cursor,expected)
        result=build_boundary(root,store,source_loader=identity_source_loader,**kwargs)
        if result is not None:
            for row in result[1]['current_read_pins']:
                if expected.get((row['collection'],row['object_id'])) != (row['revision'],row['content_sha256'],False):
                    raise ValueError('retained approval outside immutable original SQL read set')
        return result
    report = run_batch(batch_id=packet["batch_id"],scope_label=packet["scope_label"],
        claims=[ReviewClaim.model_validate(row) for row in packet["claims"]],registry_context=packet["registry_context"],
        pending_candidates=[],output_dir=root,current_claim_links=packet["existing_links"],
        dependents_loader=store.load_revision_dependents,claim_issue_handler=claim_issue_handler,
        management_context=materials.get('claim_management_resolution'),
        identity_source_loader=identity_source_loader,retained_review_loader=retained_review_loader,**models)
    from backend.pipeline.cvp_retained_review_separation import write_scope_authority
    write_scope = write_scope_authority(root,store,materials,report)
    if (report['approval_outcome']!='pass' or report['escalations']) and write_scope is None:
        raise ValueError('normal CVP terminal review unresolved; no second correction')
    if require_resolved and write_scope is None:resolved_report(report,[row["claim_id"] for row in packet["claims"]])
    if require_resolved and write_scope is not None:
        # The original report/verdicts are NOT changed to pass. Split authority
        # already validates every actual write; still enforce coverage boundary.
        p=report['_effective_proposal']
        if (sorted(d.claim_id for d in p.claim_decisions)!=sorted(row['claim_id'] for row in packet['claims'])
                or any(c.disposition=='deferred' for d in p.claim_decisions for c in d.components)
                or p.viewpoint_revisions or getattr(p,'argument_route_candidates',[])):
            raise ValueError('split authority cannot waive unresolved coverage/CVP revision boundary')
    proposal_name = ("raw-reconsideration.json" if report["_final_review"] is not None
                     else report.get('_initial_proposal_artifact_name', 'raw-proposal.json'))
    review_name = "raw-final-review.json" if report["_final_review"] is not None else report.get('_initial_review_artifact_name',"raw-review.json")
    proposal = read_artifact(root/proposal_name); review = read_artifact(root/review_name)
    from backend.pipeline.cvp_retained_review_separation import passed_null_locator_retention
    null_retention = passed_null_locator_retention(root,store,materials,report,identity_source_loader)
    retained_null_ids = ((report.get('_retained_review_boundary') or {}).get('retained_null_locator_links',[])
                         if null_retention is None else null_retention['retained_null_locator_links'])
    package = compile_cvp_batch_package(proposal=report["_effective_proposal"],review=report["_review"],
        deterministic_validation_sha256=report["_effective_validation"]["artifact_sha256"],
        scope_manifest_sha256=packet["current_claim_manifest_sha256"],claims=[ReviewClaim.model_validate(row) for row in packet["claims"]],
        registry_context=packet["registry_context"],revision_dependents=report["_revision_dependents"],
        proposal_artifact_sha256=proposal["artifact_sha256"],review_artifact_sha256=review["artifact_sha256"],
        proposer_model_id=proposal["model_id"],reviewer_model_id=review["model_id"],decided_at=stable_decided_at(root),
        current_claim_links=packet["existing_links"],
        retained_write_scope=write_scope,
        retained_null_link_ids=retained_null_ids)
    write_immutable(root/"reviewed-package.json",package)
    from backend.api.canonical_repository.viewpoint_batch_resolution import build_batch_disposition
    disposition = build_batch_disposition(proposal=report["_effective_proposal"],batch_identity={"batch_id":packet["batch_id"]},
        effective_proposal_sha256=sha256_json(report["_effective_proposal"].model_dump(mode="json")),apply_status="planned")
    approval = {"proposal":binding(root/proposal_name),"review":binding(root/review_name)}
    if null_retention is not None:
        approval['passed_null_locator_retention']=binding(root/'passed-null-locator-retention.json')
    if write_scope is not None:
        approval['retained_write_scope']=binding(root/'retained-write-scope-authority.json')
        approval['human_retained_content_disposition']=write_scope['human_disposition']
    if report.get('_retained_review_boundary') is not None:
        approval['retained_review_boundary'] = binding(root/'retained-review-boundary.json')
    if (root/'batch-identity-integration.json').exists():
        for name in ('batch-identity-integration.json','raw-proposal.json','raw-consolidation.json',
                     'raw-identity-reconsideration.json'):
            if (root/name).exists():approval[name]=binding(root/name)
    if report.get('_initial_proposal_artifact_name') == 'raw-owner-identity-proposal.json':
        for name in ('owner-candidate-identity-authorization.json', 'owner-identity-validation.json',
                     'raw-proposal.json', 'raw-consolidation.json', 'raw-owner-identity-proposal.json'):
            approval[name] = binding(root/name)
    for stage in ('raw-review', 'raw-reconsideration', 'raw-final-review'):
        proof = root/(stage+'.program-binding.json')
        if proof.exists(): approval[stage+'_program_binding'] = binding(proof)
    if review_name == 'raw-review-pin-format-repair.json':
        for key, name in (
            ('original_review', 'raw-review.json'),
            ('owner_pin_authorization', 'owner-review-pin-format-authorization.json'),
            ('pin_format_validation', 'raw-review-pin-format-repair-validation.json'),
        ):
            approval[key] = binding(root/name)
    return package,approval,disposition


def commit_package(store, run_id, *, package, approval, authorization, backup, sources,
                   read_pins, reason, actor, source_kind="cvp_batch_resolution", viewpoint_ids=(), disposition=None, action=None,
                   pending_cvp_claim_ids=(), pending_cvp_management_record_keys=None):
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
        context=store._existing_cvp_context(conn,normalize_package(package),
            [(op.collection,op.object_id) for op in plan.operations if op.operation=='retire'])
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


def audit_resolution_directory(audit_dir,audit_path):
    """Pure resolution artifacts, not model roots, bind their exact assessment."""
    audit_dir=Path(audit_dir);audit_path=Path(audit_path)
    if audit_path==audit_dir/'audit.json':return audit_dir
    return audit_dir/('assessment-'+sha256_json(binding(audit_path))[:20])


def run_group(store, definition, group, row, *, root):
    from backend.pipeline.claim_issue_management import IssuesDetected,handle_issues,audit_claim_findings,prior_management_action
    campaign = definition["campaign_sha256"]
    if row is None:
        materials = prepare_group(store,definition,group)
        stage_event('prepared', sequence=group['sequence'], **materials['metrics'])
        run_id = store.prepare_cvp_group(campaign_sha256=campaign,group_key=group["group_key"],sequence=group["sequence"],
                                         claim_ids=group["claim_ids"],materials=materials)
    else:
        run_id = row["run_id"]
    saved = store.get_cvp_group(run_id); materials = saved["materials"]
    def supports_or_source(adjudication, action_root, evidence):
        from backend.pipeline.cvp_sql_actions import execute_supports_action
        from backend.pipeline.source_reconciliation_runtime import ProductionPorts
        try:
            return execute_supports_action(store,run_id,adjudication,root=action_root,ports=ProductionPorts())
        except IssuesDetected as detected:
            policy=root/'claim-issue-policy-binding.json'
            handle_issues(store,run_id,detected,root=root/run_id/'claim-issues',
                policy_binding=read_artifact(policy).get('policy') if policy.exists() else None,
                ports=ProductionPorts(),prior_management_action=prior_management_action(evidence,detected.findings))
            return {'status':'source_management'}
    if saved.get('reviewed',{}).get('evidence',{}).get('approval',{}).get('source_write_slice'):
        app=saved['reviewed']['evidence']['package']['payload'].get('source_repair_application',{})
        if (app.get('source_write_slice',{}).get('mode') not in {'policy_evidence_binding','policy_claim_source_revision'}
                or not saved.get('claim_layer_audit')):
            raise ValueError('source write slice applied; remaining exact source/relationship management pending before group continuation')
    store.bind_cvp_execution_root(run_id,identity='group',root=root/run_id)
    model_root = root/run_id/"normal-review"
    if saved["checkpoint"] == "prepared":
        from backend.pipeline.claim_evidence_completion import NAME as COMPLETION_NAME
        from backend.pipeline.claim_complete_input_repair import NAME as INPUT_REPAIR_NAME
        from backend.pipeline.claim_issue_rescope import NAME, execute as amend
        repair_binding=root/INPUT_REPAIR_NAME
        completion_binding=root/COMPLETION_NAME
        amendment=completion_binding if completion_binding.exists() and read_artifact(
            read_artifact(completion_binding)['authorization']['path'])['run_id']==run_id else repair_binding if repair_binding.exists() and read_artifact(
            read_artifact(repair_binding)['authorization']['path'])['run_id']==run_id else root/NAME
        authorization=read_artifact(amendment)['authorization'] if amendment.exists() else None
        direction=read_artifact(authorization['path']) if authorization else None
        if direction and direction['run_id']==run_id:
            # The original saved issue/action already proves discovery. Do
            # not rebuild/reinterpret the old normal prompt to discover it
            # again, or reserve a second normal proposal call.
            from backend.pipeline.source_reconciliation_runtime import ProductionPorts
            amend(store,run_id,authorization=authorization,root=root/run_id/'claim-issues',ports=ProductionPorts())
            return run_group(store,definition,group,store.get_cvp_group(run_id),root=root)
        # The existing call-intent invariant handles interruption during any
        # model role; the SQL phase remains prepared until all answers pass.
        from backend.pipeline.viewpoint_s201129_source_continuation import canonical_runtime
        runtime = canonical_runtime()
        os.environ["CODEX_EXECUTABLE"] = runtime["identity"]["cli"]["path"]
        stage_event('generating_reviewing', sequence=group['sequence'], run_id=run_id)
        try:
            package,approval,disposition = reviewed_cvp_package(model_root,materials,store)
        except IssuesDetected as detected:
            from backend.pipeline.claim_issue_management import handle_issues
            from backend.pipeline.source_reconciliation_runtime import ProductionPorts
            policy=root/'claim-issue-policy-binding.json'
            handle_issues(store,run_id,detected,root=root/run_id/'claim-issues',
                          policy_binding=read_artifact(policy).get('policy') if policy.exists() else None,
                          ports=ProductionPorts())
            # Actual SQL action committed first. Never apply the old normal
            # package or reinterpret its old approval as new-version approval.
            return run_group(store,definition,group,store.get_cvp_group(run_id),root=root)
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
    from backend.pipeline.claim_dependency_handoff import subject as dependency_subject,context as dependency_context
    joint_dependency=dependency_context(saved,store) if dependency_subject(saved) is not None else None
    if evidence.get('approval',{}).get('owner_claim_completion') or joint_dependency:
        from backend.pipeline.claim_owner_revision import finish_dependency_audit
        finish_dependency_audit(store,run_id)
    if evidence.get('pending_cvp_claim_ids') and not joint_dependency:
        from backend.pipeline.cvp_sql_actions import execute_coverage_action
        from backend.pipeline.source_reconciliation_runtime import ProductionPorts
        try:
            execute_coverage_action(store,run_id,definition,group,root=root/run_id/'required-coverage',ports=ProductionPorts())
        except IssuesDetected as detected:
            policy=root/'claim-issue-policy-binding.json'
            handle_issues(store,run_id,detected,root=root/run_id/'claim-issues',
                policy_binding=read_artifact(policy).get('policy') if policy.exists() else None,ports=ProductionPorts(),
                prior_management_action=prior_management_action(evidence,detected.findings))
            return run_group(store,definition,group,store.get_cvp_group(run_id),root=root)
        saved=store.get_cvp_group(run_id);evidence=saved['reviewed']['evidence']
    from backend.pipeline.cvp_source_impact_resolution import context as source_impact_context,prepare as prepare_source_impacts
    source_impacts=prepare_source_impacts(store,saved) if source_impact_context(saved,store) else None
    if evidence.get('pending_cvp_management_record_keys') and not evidence.get('approval',{}).get('retained_write_scope') and source_impacts is None:
        raise ValueError('new CVP coverage committed; existing CVP/link management remains pending, not group completion')
    if (evidence.get('pending_cvp_management_record_keys') and evidence.get('approval',{}).get('retained_write_scope')
            and not evidence['approval'].get('pending_link_material')):
        from backend.pipeline.cvp_pending_link_management import execute as finish_pending_links
        from backend.pipeline.source_reconciliation_runtime import ProductionPorts
        if finish_pending_links(store,run_id,root=root/run_id/'pending-link-management',ports=ProductionPorts()):
            return run_group(store,definition,group,store.get_cvp_group(run_id),root=root)
    disposition=evidence.get('disposition')
    if disposition and any(component["disposition"]=="deferred" for claim in disposition["claims"] for component in claim["components"]):
        from backend.pipeline.viewpoint_deferred_source_check import run as investigate
        packet_path=root/run_id/'deferred-investigation-input.json'
        disposition_path=root/run_id/'deferred-disposition.json'
        write_immutable(packet_path,materials['packet']);write_immutable(disposition_path,disposition)
        handoff=investigate(packet_path,disposition_path,root/run_id/'deferred-investigation',store)
        if any(not a['models_agree'] or a['next_action']!='reviewed_cvp_repair_required' for a in handoff['actions']):
            from backend.pipeline.claim_issue_management import handle_issues,IssuesDetected
            from backend.pipeline.source_reconciliation_runtime import ProductionPorts
            findings=[]
            packet_claims={r['claim_id']:r for r in materials['packet']['claims']}
            for a in handoff['actions']:
                cid=a['item_id'].rsplit(':',1)[0]
                claim=packet_claims[cid]
                findings.append({'claim_id':cid,'spans':[(0,len(claim['statement']),claim['statement'])],
                                 'reason':json.dumps(a,ensure_ascii=False)})
            policy=root/'claim-issue-policy-binding.json'
            handle_issues(store,run_id,IssuesDetected(findings,origin='deferred',
                report=binding(root/run_id/'deferred-investigation'/'handoff.json')),
                root=root/run_id/'claim-issues',policy_binding=read_artifact(policy).get('policy') if policy.exists() else None,
                ports=ProductionPorts())
            return run_group(store,definition,group,store.get_cvp_group(run_id),root=root)
        from backend.pipeline.cvp_sql_actions import execute_coverage_action
        from backend.pipeline.source_reconciliation_runtime import ProductionPorts
        execute_coverage_action(store,run_id,definition,group,root=root/run_id/'required-coverage',ports=ProductionPorts(),
                                investigation_root=root/run_id/'deferred-investigation')
        saved=store.get_cvp_group(run_id);evidence=saved['reviewed']['evidence']
    current=store.read_cvp_context(group['claim_ids'],viewpoint_ids=evidence['viewpoint_ids'])
    pending_keys=evidence.get('pending_cvp_management_record_keys',[])
    for key in pending_keys:
        col,oid=key.split('/',1)
        if source_impacts is not None and col=='groups':continue  # exact declared metadata, not SQL/CVP object
        if (col,oid) not in current:
            state=store.get_record_state(col,oid)
            if state is None:raise ValueError('pending management record missing from current SQL: '+key)
            current[col,oid]=state
    views=sorted(set(evidence['viewpoint_ids'])|{r['payload']['viewpoint_id'] for (col,_),r in current.items()
                 if col=='viewpoint_claim_links' and not r['retired'] and r['payload']['claim_id'] in group['claim_ids']})
    audit_dir = root/run_id/('audit-'+sha256_json(pin_rows(pins(current)))[:20]); audit_path = audit_dir/"audit.json"
    if not audit_path.exists():
        stage_event('independent_audit', sequence=group['sequence'], run_id=run_id)
        audit_dir.mkdir(parents=True,exist_ok=True)
        intent=audit_dir/'audit-intent.json'
        expected_intent={'claim_ids':group['claim_ids'],'viewpoint_ids':views,'read_pins':pin_rows(pins(current)),
                         'model':'gemini-3.8-flash','workers':2,'automatic_retry':False}
        from backend.pipeline.cvp_source_impact_resolution import audit_record_keys,validate_pre_model_startup_recovery
        audit_keys=audit_record_keys(pending_keys,source_impacts)
        if intent.exists():
            if source_impacts is None:raise ValueError('independent audit intent lacks saved complete result; no automatic replay')
            receipt=validate_pre_model_startup_recovery(audit_dir,expected_intent)
            if receipt['original_record_keys']!=pending_keys:raise ValueError('source audit recovery changed pending domain')
            write_immutable(audit_dir/'source-domain-startup-recovery-used.json',
                            {'recovery':binding(audit_dir/'source-domain-startup-recovery.json'),
                             'corrected_record_keys':audit_keys,'automatic_retry':False})
        else:write_immutable(intent,expected_intent)
        subprocess.run([str(PROJECT/"backend/.venv/bin/python"),"scripts/audit-library.py","--scope","all","--layers","1,2,3,4",
                        "--claim-ids",",".join(group["claim_ids"]),"--viewpoint-ids",",".join(views),
                        "--workers","2","--model","gemini-3.8-flash","--no-status-file","--preserve-full-context","--component-roles","--sql-group-scope",
                        *[a for k in audit_keys for a in ('--sql-record-key',k)],"--out",str(audit_dir)],
                       cwd=PROJECT,check=True,env={**os.environ,'KNOWLEDGE_DATABASE_URL':store.database_url})
    from backend.pipeline.viewpoint_content_blocked_fallback import apply as content_fallback
    validate_transaction_audit(read_artifact(audit_path),group['claim_ids'],views)
    audit_path = content_fallback(audit_path,root/run_id/"content-blocked-fallback")
    resolution_dir=audit_resolution_directory(audit_dir,audit_path)
    audit = read_artifact(audit_path)
    from backend.pipeline.cvp_sql_quota_resume import NAME as QUOTA_NAME,execute as resume_quota
    quota_binding=root/QUOTA_NAME
    if quota_binding.exists():
        auth=read_artifact(quota_binding)['authorization'];check_binding(auth)
        quota_direction=read_artifact(auth['path'])
        if quota_direction['run_id']==run_id and quota_direction['audit_binding']==binding(audit_path):
            resume_quota(store,run_id,Path(auth['path']))
    resolution=None
    from backend.pipeline.cvp_exact_human_audit_retention import complete as exact_human_complete, partition as exact_human_partition
    if audit['layers']['3'].get('disputed') or audit['layers']['4'].get('disputed'):
        resolution=exact_human_complete(audit_path,root,store,run_id,resolution_dir/'exact-human-link-resolution.json')
    if resolution is None and (audit['layers']['3'].get('disputed') or audit['layers']['4'].get('disputed')):
        from backend.pipeline.cvp_human_retained_audit_resolution import create_if_complete
        resolution=create_if_complete(audit_path,store,run_id,resolution_dir/'human-retained-source-final-resolution.json',execution_root=root)
    if resolution is None and (audit["layers"]["3"].get("disputed") or audit["layers"]["4"].get("disputed")):
        from backend.pipeline.viewpoint_audit_source_adjudication import build_packet,run as adjudicate
        from backend.pipeline.viewpoint_adjudication_history import target_identity
        packet=build_packet(audit_path,store,True)
        from backend.pipeline.cvp_human_retained_audit_resolution import partition_packet
        packet,human_retained_items=partition_packet(audit,packet,saved,store)
        packet,exact_human_items=exact_human_partition(audit,packet,root,store)
        targets=[target_identity(i) for i in packet['items']]
        prior=store.adjudication_roots(targets)
        input_repair_binding = root/'adjudication-input-repair-authorization-binding.json'
        if input_repair_binding.exists():
            auth = read_artifact(input_repair_binding)['authorization']
            if binding(auth['path']) != auth:
                raise ValueError('adjudication input repair direction physical drift')
            direction = read_artifact(Path(auth['path']))
            if direction.get('run_id') == run_id:
                # This consumed direction belongs only to its exact old head /
                # links. A legitimately committed new CVR is not another input
                # repair of the old audit, nor a reset of that old reservation.
                old_packet=read_artifact(Path(direction['original_root'])/'packet.json')
                from backend.pipeline.cvp_sql_adjudication_input_repair import applies_to_packet
                from backend.pipeline.cvp_sql_adjudication_input_repair import execute
                if applies_to_packet(old_packet,packet):
                    execute(store, run_id, Path(auth['path']))
                    prior = store.adjudication_roots(targets)
        from backend.pipeline.cvp_sql_adjudication_input_repair import effective_roots
        prior = effective_roots(prior)
        resolution=resolution_dir/'gpt-source-final-resolution.json'
        component_binding = root/'component-role-authorization-binding.json'
        if component_binding.exists():
            saved_binding = read_artifact(component_binding)['authorization']
            if binding(Path(saved_binding['path'])) != saved_binding:
                raise ValueError('owner component direction physical drift')
            direction = read_artifact(Path(saved_binding['path']))
            if direction.get('run_id') == run_id:
                from backend.pipeline.viewpoint_component_role_resolution import create
                exact = [p for p in prior if binding(p/'packet.json') == direction.get('packet_binding')]
                if len(exact) != 1:
                    raise ValueError('owner component direction lacks unique actual saved adjudication')
                resolution = resolution_dir/'owner-component-role-resolution.json'
                create(audit_path, exact[0], Path(saved_binding['path']), resolution, store,
                       campaign_sha256=saved['campaign_sha256'], run_id=run_id)
                store.complete_cvp_group(run_id,audit=audit,audit_binding=binding(audit_path),read_pins=pins(current),
                                         owner_resolution_path=resolution)
                stage_event('audited', sequence=group['sequence'], run_id=run_id)
                return
        from backend.pipeline.viewpoint_reused_source_resolution import compose,create as reuse_part,subset_key
        from backend.pipeline.viewpoint_adjudication_history import validated_history
        from backend.pipeline.viewpoint_audit_source_adjudication import restricted_packet
        covered=set()
        # Authenticate known decisions before spending any new budget. New
        # findings never cause old unchanged targets to be sent again.
        for previous in prior:
            old,_=validated_history(previous)
            prior_report=read_artifact(previous/'gpt-final-handoff.json')
            matched={target_identity(i) for i in packet['items']}&{target_identity(i) for i in old['items']}
            active_items=[i for i in old['items'] if target_identity(i) in matched]
            prior_claims=audit_claim_findings({'items':active_items},prior_report)
            if prior_claims:
                policy=root/'claim-issue-policy-binding.json'
                from backend.pipeline.source_reconciliation_runtime import ProductionPorts
                handle_issues(store,run_id,IssuesDetected(prior_claims,origin='gemini_audit',
                    report=binding(previous/'gpt-final-handoff.json')),root=root/run_id/'claim-issues',
                    policy_binding=read_artifact(policy).get('policy') if policy.exists() else None,ports=ProductionPorts(),
                    prior_management_action=prior_management_action(evidence,prior_claims))
                return run_group(store,definition,group,store.get_cvp_group(run_id),root=root)
            known={target_identity(i) for i in old['items']}
            ids={i['item_id'] for i in packet['items'] if target_identity(i) in known}
            if ids:
                if handle_audit_scope(store,run_id,{'items':active_items},prior_report,
                                      reports=[binding(previous/'packet.json'),binding(previous/'gpt-final-handoff.json')],root=root):
                    return run_group(store,definition,group,store.get_cvp_group(run_id),root=root)
                unresolved=[a['item_id'] for a in prior_report['actions']
                            if a['item_id'] in ids and a['verdict']=='unresolved']
                if unresolved:
                    raise ValueError('saved original-source adjudication unresolved; original once reservation retained: '
                                     + ', '.join(unresolved))
                if exact_saved_upheld_items(old,packet,prior_report,ids):
                    from backend.pipeline.cvp_sql_actions import execute_supports_action
                    from backend.pipeline.source_reconciliation_runtime import ProductionPorts
                    result=supports_or_source(previous,Path(previous).parent/'bounded-supports',evidence)
                    if result.get('status')=='already_applied':
                        raise ValueError('already repaired connection remains upheld; no repeated semantic repair')
                    return run_group(store,definition,group,store.get_cvp_group(run_id),root=root)
                reuse_part(audit_path,previous,resolution_dir/('known-'+subset_key(previous,ids))/'resolution.json',
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
            from backend.pipeline.cvp_pending_link_management import guard_repeated_semantic_repair
            guard_repeated_semantic_repair(saved,subset,report)
            claim_findings=audit_claim_findings(subset,report)
            if claim_findings:
                policy=root/'claim-issue-policy-binding.json'
                from backend.pipeline.source_reconciliation_runtime import ProductionPorts
                handle_issues(store,run_id,IssuesDetected(claim_findings,origin='gemini_audit',
                    report=binding(adjudication/'gpt-final-handoff.json')),root=root/run_id/'claim-issues',
                    policy_binding=read_artifact(policy).get('policy') if policy.exists() else None,ports=ProductionPorts(),
                    prior_management_action=prior_management_action(evidence,claim_findings))
                return run_group(store,definition,group,store.get_cvp_group(run_id),root=root)
            if handle_audit_scope(store,run_id,subset,report,
                                  reports=[binding(adjudication/'packet.json'),binding(adjudication/'gpt-final-handoff.json')],root=root):
                return run_group(store,definition,group,store.get_cvp_group(run_id),root=root)
            if any(a['verdict']=='unresolved' for a in report['actions']):
                raise ValueError('original-source adjudication unresolved; no automatic supports repair or second call')
            if any(a['verdict']!='audit_objection_rejected' for a in report['actions']):
                from backend.pipeline.cvp_sql_actions import execute_supports_action
                from backend.pipeline.source_reconciliation_runtime import ProductionPorts
                supports_or_source(adjudication,audit_dir/'bounded-supports',evidence)
                # Exactly one narrow correction; the new committed pins choose
                # another complete audit. Unchanged judgement inputs reuse cache.
                return run_group(store,definition,group,store.get_cvp_group(run_id),root=root)
            prior.append(adjudication)
        if exact_human_items and not human_retained_items:
            resolution=exact_human_complete(audit_path,root,store,run_id,resolution_dir/'exact-human-link-resolution.json')
            if resolution is None:raise ValueError('exact human retention does not resolve remaining findings')
        elif human_retained_items:
            # Excluding a human-owned target from adjudication is NOT a pass.
            # Rejoin the full actual audit population only after every remaining
            # finding has an exact authenticated source rejection.
            resolution=create_if_complete(audit_path,store,run_id,resolution_dir/'human-retained-source-final-resolution.json',execution_root=root)
            if resolution is None:
                raise ValueError('human retained routing does not resolve remaining current audit findings')
        else:
            compose(audit_path,prior,resolution,store)
    pending_resolution=None
    if evidence.get('pending_cvp_management_record_keys'):
        if source_impacts is not None:
            from backend.pipeline.cvp_source_impact_resolution import build
        else:
            from backend.pipeline.cvp_retained_impact_resolution import build
        pending_resolution=build(store,run_id,audit_path,resolution_dir/'retained-impact-resolution.json',
                                 semantic_resolution=resolution)
    from backend.pipeline.cvp_taxonomy_dependency import prepare as prepare_taxonomy
    taxonomy=prepare_taxonomy(store,run_id,audit_path,current)
    store.complete_cvp_group(run_id,audit=audit,audit_binding=binding(audit_path),read_pins=pins(current),
                             owner_resolution_path=resolution,retained_impacts_resolution_path=pending_resolution,
                             taxonomy_dependency_path=taxonomy)
    stage_event('audited', sequence=group['sequence'], run_id=run_id)


def handle_audit_scope(store,run_id,packet,report,*,reports,root):
    from backend.pipeline.cvp_scope_management import audit_targets,handle_scope
    targets=audit_targets(packet,report)
    if not targets:return False
    policy=root/'cvp-scope-policy-binding.json'
    if not policy.exists():return False
    from backend.pipeline.source_reconciliation_runtime import ProductionPorts
    for vid in targets:
        handle_scope(store,run_id,vid,policy_binding=read_artifact(policy)['policy'],reports=reports,
                     root=root/run_id/'cvp-management',ports=ProductionPorts())
    return True


def revalidate_applied_run(store, definition, run_id, *, root):
    """Audit one already applied group without entering another group's intake.

    The ordinary audit, resolution and completion gates remain authoritative.
    Pending source/coverage work must use its own management handoff first.
    """
    saved = store.get_cvp_group(run_id)
    if not saved or saved['campaign_sha256'] != definition['campaign_sha256']:
        raise ValueError('audit-run campaign drift')
    groups = [g for g in definition['groups'] if g['group_key'] == saved['group_key']]
    if (len(groups) != 1 or groups[0]['sequence'] != saved['sequence']
            or groups[0]['claim_ids'] != saved['claim_ids']):
        raise ValueError('audit-run original membership drift')
    evidence = saved.get('reviewed', {}).get('evidence') or saved.get('import_certificate', {})
    if evidence.get('approval', {}).get('source_write_slice'):
        from backend.pipeline.claim_source_write_slice import finish_impact_audit
        return finish_impact_audit(store,saved)
    if (saved['checkpoint'] not in {'applied', 'audited'}
            or evidence.get('pending_cvp_claim_ids')
            or evidence.get('pending_cvp_management_record_keys')
            or evidence.get('approval', {}).get('source_write_slice')):
        raise ValueError('audit-run requires a complete applied write domain; management remains pending')
    if saved['checkpoint'] == 'audited':
        return {'status': 'already_audited', 'run_id': run_id}
    try:
        run_group(store, definition, groups[0], saved, root=root)
        current = store.get_cvp_group(run_id)
        if current['checkpoint'] != 'audited' or current['status'] != 'succeeded':
            raise ValueError('audit-run returned without actual SQL audited completion')
    except Exception as exc:
        store.fail_cvp_group(run_id, str(exc))
        raise
    return {'status': 'audited', 'run_id': run_id}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sql-campaign",required=True)
    parser.add_argument("--database-url")
    parser.add_argument("--output-root",type=Path,required=True)
    parser.add_argument("--dry-run",action="store_true")
    parser.add_argument('--execute',action='store_true',help='Explicit new SQL execution; never implicitly resume a stopped campaign')
    parser.add_argument('--stop-after-group',type=int,help='Complete this original group then stop before preparing the next')
    parser.add_argument('--pause-after-group',type=int,help='Narrow this invocation for acceptance; never changes the immutable execution boundary')
    parser.add_argument('--integrate-batch-candidates',action='store_true',
                        help='Explicit new-group identity integration; never upgrades saved legacy answers')
    parser.add_argument('--action',choices=('group','audit-run','source-pending-check','source-impact-complete','source','source-write-slice','relation-retire','claim-retire','owner-link','link-target','link-retarget','link-retire','link-replace','link-representation'),default='group')
    parser.add_argument('--claim-first',action='store_true',
                        help='Finish only the original bounded Claim/Evidence action; keep all CVPs pending and stop before CVP/group execution')
    parser.add_argument('--candidate-viewpoint-id',action='append',default=[],
                        help='Exact existing target IDs for read-only link-target comparison; not content approval')
    parser.add_argument('--run-id')
    parser.add_argument('--manifest',type=Path)
    parser.add_argument('--direction',type=Path)
    parser.add_argument('--adjudication-root',type=Path)
    parser.add_argument('--link-id')
    parser.add_argument('--claim-issue-policy',type=Path,help='Explicit owner bounded Claim/Evidence/CVP policy; no inferred permission')
    parser.add_argument('--cvp-scope-policy',type=Path,help='Normal bounded CVP scope enrichment; no identity/core/Claim/source permission')
    parser.add_argument('--link-representation-disposition',type=Path,help='Exact human acceptance of saved representation postimage; no new model budget')
    parser.add_argument('--manual-quota-resume-authorization',type=Path,
                        help='Explicit owner once continuation of exact startup quota/no-answer receipt; never automatic retry')
    parser.add_argument('--request-limit-authorization',type=Path,help='Owner campaign request budget; does not change frozen model inputs or call budgets')
    parser.add_argument('--component-role-authorization', type=Path,
                        help='Exact owner acceptance of an existing structural duplicate finding; no new model/content permission')
    parser.add_argument('--adjudication-input-repair-authorization', type=Path,
                        help='One owner-directed complete-CVR call for an exact saved unresolved incomplete input')
    parser.add_argument('--candidate-identity-authorization', type=Path,
                        help='Owner-directed same-batch identity revision; original unresolved answer is retained')
    parser.add_argument('--source-review-input-repair-authorization', type=Path,
                        help='Exact once-only engineering review input repair; no content or correction permission')
    parser.add_argument('--claim-issue-rescope-authorization',type=Path,
                        help='Exact once-only pre-review scope amendment; original issue calls remain reserved')
    parser.add_argument('--claim-complete-input-repair-authorization',type=Path,
                        help='Exact once-only proven Claim input repair; original calls remain immutable')
    parser.add_argument('--claim-evidence-completion-authorization',type=Path,
                        help='Exact owner joint Evidence binding/qualification and erroneous relation withdrawal; fixed parent, original budgets retained')
    parser.add_argument('--normalization-engineering-recovery-authorization',type=Path,
                        help='Fixed-parent flag-only program defect recovery; one independent review, no new proposal')
    parser.add_argument('--retained-link-review-policy',type=Path,
                        help='Separate existing approved links from new proposed writes; no mutation/retry permission')
    parser.add_argument('--retained-generation-artifact',type=Path,action='append',default=[],
                        help='Exact original generation raw leaf; indexed by actual committed decision artifact SHA')
    parser.add_argument('--retained-content-disposition',type=Path,action='append',default=[],
                        help='Exact human no-change decision for current retained links; never a model pass or write permission')
    parser.add_argument('--audit-link-retention',type=Path,action='append',default=[],
                        help='Exact owner rejection of a named current audit link objection; zero writes or extra calls')
    parser.add_argument('--owner-claim-revision',type=Path,
                        help='Exact human identification of a Claim omission; normal GPT/Opus joint review still required')
    parser.add_argument('--owner-model-retry-authorization',type=Path,
                        help='Explicit once retry of an exact unanswered transport timeout; original unknown preserved')
    parser.add_argument('--source-permission-recovery-authorization',type=Path,
                        help='Fixed-parent source create-contract engineering input repair; original final only')
    args = parser.parse_args(argv)
    from backend.pipeline.cvp_model_retry_once import NAME as RETRY_NAME
    if (args.output_root/RETRY_NAME).exists() and not args.owner_model_retry_authorization:
        raise ValueError('owner retry requires original explicit authorization')
    from backend.pipeline.cvp_sql_quota_resume import NAME as QUOTA_NAME,SCHEMA as QUOTA_SCHEMA
    quota_binding=args.output_root/QUOTA_NAME
    if quota_binding.exists() and not args.manual_quota_resume_authorization:
        raise ValueError('manual quota continuation requires original explicit recovery authority')
    from backend.pipeline.cvp_exact_human_audit_retention import validate_direction,check_explicit_bindings,persist_bindings
    audit_retention_binding={'directions':[binding(p) for p in args.audit_link_retention]}
    check_explicit_bindings(args.output_root,audit_retention_binding['directions'])
    from backend.pipeline.cvp_batch_identity_integration import ENABLED, VERSION
    ENABLED.set(args.integrate_batch_candidates)
    integration_binding=args.output_root/'batch-identity-integration-binding.json'
    if integration_binding.exists() and not args.integrate_batch_candidates:
        raise ValueError('identity integration execution binding requires explicit recovery flag')
    if (args.output_root/'source-review-input-repair-binding.json').exists() and not args.source_review_input_repair_authorization:
        raise ValueError('source review input repair binding requires explicit recovery flag')
    from backend.pipeline.claim_issue_rescope import NAME as AMENDMENT_NAME
    if (args.output_root/AMENDMENT_NAME).exists() and not args.claim_issue_rescope_authorization:
        raise ValueError('Claim issue scope amendment binding requires explicit recovery flag')
    from backend.pipeline.claim_complete_input_repair import NAME as INPUT_REPAIR_NAME, SCHEMA as INPUT_REPAIR_SCHEMA
    from backend.pipeline.claim_evidence_completion import NAME as COMPLETION_NAME, SCHEMA as COMPLETION_SCHEMA
    if (args.output_root/COMPLETION_NAME).exists() and not args.claim_evidence_completion_authorization:
        raise ValueError('bound Claim Evidence completion direction must be explicit on recovery')
    if (args.output_root/INPUT_REPAIR_NAME).exists() and not args.claim_complete_input_repair_authorization:
        raise ValueError('Claim complete input repair binding requires explicit recovery flag')
    from backend.pipeline.source_normalization_recovery import BINDING as NORMALIZATION_BINDING
    if (args.output_root/NORMALIZATION_BINDING).exists() and not args.normalization_engineering_recovery_authorization:
        raise ValueError('normalization engineering recovery requires explicit original binding')
    from backend.pipeline.cvp_retained_review_separation import NAME as RETAINED_NAME
    if (args.output_root/RETAINED_NAME).exists() and not args.retained_link_review_policy:
        raise ValueError('retained-link separation binding requires explicit recovery flag')
    from backend.pipeline.cvp_retained_review_separation import human_bindings,persist_human_bindings
    if human_bindings(args.output_root) and not args.retained_content_disposition:
        raise ValueError('human retained disposition binding requires explicit recovery flag')
    from backend.pipeline.claim_owner_revision import NAME as OWNER_CLAIM_NAME
    if (args.output_root/OWNER_CLAIM_NAME).exists() and not args.owner_claim_revision:
        raise ValueError('owner Claim revision binding requires explicit recovery flag')
    load_dotenv(PROJECT/".env")
    if args.database_url:os.environ['KNOWLEDGE_DATABASE_URL']=args.database_url
    store = PostgresKnowledgeStore(args.database_url)
    if args.manual_quota_resume_authorization:
        d=read_artifact(args.manual_quota_resume_authorization)
        from backend.pipeline.cvp_sql_quota_resume import sealed
        sealed(d)
        if d.get('schema_version')!=QUOTA_SCHEMA or d.get('campaign_sha256')!=args.sql_campaign:
            raise ValueError('quota resume foreign execution campaign')
        if not Path(d['original_root']).is_relative_to(args.output_root.resolve()):
            raise ValueError('quota resume foreign execution root')
    definition,group,row = next_group(store,args.sql_campaign)
    execution_boundary(args.output_root,args.sql_campaign,definition,args.stop_after_group)
    if args.claim_first and args.action!='source':
        raise ValueError('Claim-first is an explicit original source action, never a group/CVP resume')
    if args.pause_after_group is not None and (not 1 <= args.pause_after_group <= len(definition['groups'])
            or args.stop_after_group is None or args.pause_after_group > args.stop_after_group):
        raise ValueError('acceptance pause must narrow an explicit original execution boundary')
    invocation_limit = args.pause_after_group or args.stop_after_group
    if args.action == 'link-target':
        if args.dry_run == args.execute or not args.link_id or not args.run_id:
            raise ValueError('link-target requires dry-run or explicitly authorized review, run/link IDs')
        saved = store.get_cvp_group(args.run_id)
        if not saved or saved['campaign_sha256'] != args.sql_campaign:
            raise ValueError('target selection requires an exact admitted campaign group')
        from backend.pipeline.cvp_link_target_selection import build_packet, review_selection, production_ports, validate_group_scope
        from backend.pipeline.source_reconciliation_runtime import maintenance_lock
        with maintenance_lock():
            packet = build_packet(store,link_id=args.link_id,candidate_viewpoint_ids=args.candidate_viewpoint_id)
            validate_group_scope(saved,packet)
            target = args.output_root/args.run_id/'target-selection'/sha256_json(packet)/'packet.json'
            write_immutable(target,packet)
            if args.execute:
                if not args.direction:
                    raise ValueError('target selection review needs exact direction; no inferred retry authority')
                direction = read_artifact(args.direction)
                wanted = {'schema_version':'cvp_link_target_selection_direction.v1',
                          'campaign_sha256':args.sql_campaign,'run_id':args.run_id,
                          'link_id':args.link_id,'packet_sha256':sha256_json(packet),
                          'registry_writes':0,'human_content_approval':False}
                if any(direction.get(k)!=v for k,v in wanted.items()) or not direction.get('authorization_quote'):
                    raise ValueError('target selection direction differs from exact packet/read-only scope')
                from backend.pipeline.viewpoint_resolution_runtime import authorized_request_limit
                with authorized_request_limit(args.output_root,args.sql_campaign,args.request_limit_authorization):
                    result = review_selection(store,packet,root=args.output_root/args.run_id/'target-selection',
                                              ports=production_ports())
                print(json.dumps(result)); return 0
        print(json.dumps({'status':'target_comparison_no_models_no_registry_writes',
                          'packet':binding(target),'candidate_viewpoint_ids':args.candidate_viewpoint_id,
                          'semantic_approval':False})); return 0
    if checkpoint_reached(group,invocation_limit):
        stage_event('owner_checkpoint', stop_after_group=args.stop_after_group, pause_after_group=args.pause_after_group)
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
    from backend.pipeline.viewpoint_resolution_runtime import authorized_request_limit
    from backend.pipeline.cvp_model_retry_once import authorized_retry
    with maintenance_lock(), authorized_request_limit(args.output_root,args.sql_campaign,args.request_limit_authorization), authorized_retry(
            args.output_root,args.sql_campaign,args.owner_model_retry_authorization,store):
        if args.manual_quota_resume_authorization:
            write_immutable(quota_binding,{'authorization':binding(args.manual_quota_resume_authorization)})
        if args.retained_content_disposition:
            if not args.retained_link_review_policy:
                raise ValueError('human retention requires exact campaign and retained policy')
            persist_human_bindings(args.output_root,[binding(p) for p in args.retained_content_disposition],
                                   campaign=args.sql_campaign,store=store,limit=invocation_limit)
        if args.owner_claim_revision:
            from backend.pipeline.claim_owner_revision import execute as owner_claim_revision
            direction=read_artifact(args.owner_claim_revision)
            target=store.get_cvp_group(direction['run_id'])
            if (direction['campaign_sha256']!=args.sql_campaign or not target
                    or target['campaign_sha256']!=args.sql_campaign
                    or (invocation_limit is not None and target['sequence']>invocation_limit)):
                raise ValueError('owner Claim completion outside current admitted execution boundary')
            write_immutable(args.output_root/OWNER_CLAIM_NAME,{'direction':binding(args.owner_claim_revision)})
        if args.integrate_batch_candidates:
            write_immutable(integration_binding, {'schema_version':VERSION,
                'campaign_sha256':args.sql_campaign,'new_groups_only':True,'correction_budget':1})
        execution_boundary(args.output_root,args.sql_campaign,definition,args.stop_after_group,persist=True)
        if args.audit_link_retention:
            for p in args.audit_link_retention:
                d=read_artifact(p);validate_direction(d);v=store.get_cvp_group(d['run_id'])
                if (d['campaign_sha256']!=args.sql_campaign or not v or v['campaign_sha256']!=args.sql_campaign
                        or not Path(d['audit']['path']).resolve().is_relative_to((args.output_root/d['run_id']).resolve())
                        or (invocation_limit is not None and v['sequence']>invocation_limit)):
                    raise ValueError('audit link retention campaign/root/boundary drift')
            persist_bindings(args.output_root,audit_retention_binding['directions'])
        if args.retained_link_review_policy:
            from backend.pipeline.cvp_retained_review_separation import validate_policy,generation_artifacts,persist_generation_binding,VERSION as RETAINED_VERSION
            validate_policy(read_artifact(args.retained_link_review_policy))
            persist_generation_binding(args.output_root/RETAINED_NAME,{'schema_version':RETAINED_VERSION,
                'campaign_sha256':args.sql_campaign,'policy':binding(args.retained_link_review_policy),
                'generation_artifacts':generation_artifacts(args.retained_generation_artifact)})
        if args.claim_evidence_completion_authorization:
            from backend.pipeline.claim_issue_rescope import validate_live
            authorization=binding(args.claim_evidence_completion_authorization)
            direction=read_artifact(args.claim_evidence_completion_authorization)
            target=store.get_cvp_group(direction['run_id'])
            if (direction.get('schema_version')!=COMPLETION_SCHEMA or direction['campaign_sha256']!=args.sql_campaign
                    or not target or target['campaign_sha256']!=args.sql_campaign
                    or (invocation_limit is not None and target['sequence']>invocation_limit)
                    or not Path(direction['old_action_root']).resolve().is_relative_to((args.output_root/direction['run_id']/'claim-issues').resolve())):
                raise ValueError('Claim Evidence completion campaign/root/boundary drift')
            aid='CVPA-'+sha256_json({'kind':'claim_issue_scope_amendment_once','parent':direction['old_action_id']})
            if not store.cvp_action_committed(direction['run_id'],aid):validate_live(store,direction)
            write_immutable(args.output_root/COMPLETION_NAME,{'authorization':authorization})
        if args.claim_issue_rescope_authorization:
            from backend.pipeline.claim_issue_rescope import validate_live
            authorization=binding(args.claim_issue_rescope_authorization)
            direction=read_artifact(args.claim_issue_rescope_authorization)
            if (direction['campaign_sha256']!=args.sql_campaign
                    or not Path(direction['old_action_root']).resolve().is_relative_to((args.output_root/direction['run_id']/'claim-issues').resolve())):
                raise ValueError('Claim issue amendment campaign/root mismatch')
            target=store.get_cvp_group(direction['run_id'])
            if not target or (invocation_limit is not None and target['sequence']>invocation_limit):
                raise ValueError('Claim issue amendment outside execution boundary')
            # Already committed amendment resumes through its own SQL facts;
            # its original preimage is not a reason to replay it.
            aid='CVPA-'+sha256_json({'kind':'claim_issue_scope_amendment_once','parent':direction['old_action_id']})
            if not store.cvp_action_committed(direction['run_id'],aid):validate_live(store,direction)
            write_immutable(args.output_root/AMENDMENT_NAME,{'authorization':authorization})
        if args.claim_complete_input_repair_authorization:
            from backend.pipeline.claim_issue_rescope import validate_live
            authorization=binding(args.claim_complete_input_repair_authorization)
            direction=read_artifact(args.claim_complete_input_repair_authorization)
            target=store.get_cvp_group(direction['run_id'])
            if (direction.get('schema_version')!=INPUT_REPAIR_SCHEMA or direction['campaign_sha256']!=args.sql_campaign
                    or not target or target['campaign_sha256']!=args.sql_campaign
                    or (invocation_limit is not None and target['sequence']>invocation_limit)
                    or not Path(direction['old_action_root']).resolve().is_relative_to((args.output_root/direction['run_id']/'claim-issues').resolve())):
                raise ValueError('Claim complete-input repair campaign/root/boundary drift')
            aid='CVPA-'+sha256_json({'kind':'claim_issue_scope_amendment_once','parent':direction['old_action_id']})
            if not store.cvp_action_committed(direction['run_id'],aid):validate_live(store,direction)
            write_immutable(args.output_root/INPUT_REPAIR_NAME,{'authorization':authorization})
        if args.normalization_engineering_recovery_authorization:
            from backend.pipeline.source_normalization_recovery import SCHEMA, NAME, packet as recovery_packet
            from backend.pipeline.source_reconciliation_runner import read, body
            direction=read_artifact(args.normalization_engineering_recovery_authorization)
            target=Path(direction['action_root']).resolve()
            saved=store.get_cvp_group(direction['run_id'])
            if (direction.get('schema_version')!=SCHEMA or direction.get('campaign_sha256')!=args.sql_campaign
                    or not saved or saved['campaign_sha256']!=args.sql_campaign
                    or (invocation_limit is not None and saved['sequence']>invocation_limit)
                    or target.name!=direction['action_id']
                    or not target.is_relative_to((args.output_root/direction['run_id']/'claim-issues').resolve())
                    or saved.get('execution_roots',{}).get(direction['action_id'])!=str(target)):
                raise ValueError('normalization recovery original SQL action/root/campaign drift')
            write_immutable(target/NAME,direction)
            if not store.cvp_action_committed(direction['run_id'],direction['action_id']):
                recovery_packet(target,body(read(target/'scoped-materials.json')))
            write_immutable(args.output_root/NORMALIZATION_BINDING,
                {'authorization':binding(args.normalization_engineering_recovery_authorization)})
        if args.source_review_input_repair_authorization:
            from backend.pipeline.source_review_input_repair import SCHEMA, NAME
            direction = read_artifact(args.source_review_input_repair_authorization)
            target = Path(direction['action_root']).resolve()
            saved = store.get_cvp_group(direction['run_id'])
            if (direction.get('schema_version') != SCHEMA or direction.get('campaign_sha256') != args.sql_campaign
                    or direction.get('authorized_by') != 'owner' or not direction.get('owner_direction')
                    or direction.get('artifact_sha256') != sha256_json({k:v for k,v in direction.items() if k!='artifact_sha256'})
                    or not saved or saved['campaign_sha256'] != args.sql_campaign
                    or (invocation_limit is not None and saved['sequence'] > invocation_limit)
                    or not target.is_relative_to((args.output_root/direction['run_id']/'claim-issues').resolve())
                    or target.name != direction['action_id'] or direction.get('additional_review_limit') != 1
                    or direction.get('content_change_authorized') is not False):
                raise ValueError('source review input repair outside exact owner/action boundary')
            write_immutable(target/NAME, direction)
            write_immutable(args.output_root/'source-review-input-repair-binding.json',
                            {'authorization': binding(args.source_review_input_repair_authorization)})
        if args.candidate_identity_authorization:
            from backend.pipeline.cvp_owner_candidate_identity import SCHEMA, NAME
            direction = read_artifact(args.candidate_identity_authorization)
            if (direction.get('schema_version') != SCHEMA or direction.get('campaign_sha256') != args.sql_campaign
                    or direction.get('authorized_by') != 'owner'
                    or direction.get('artifact_sha256') != sha256_json({k:v for k,v in direction.items() if k!='artifact_sha256'})):
                raise ValueError('candidate identity direction campaign/owner mismatch')
            saved = store.get_cvp_group(direction['run_id'])
            direction_path = args.output_root/direction['run_id']/'normal-review'/NAME
            if (not saved or saved['campaign_sha256'] != args.sql_campaign or
                    (invocation_limit is not None and saved['sequence'] > invocation_limit)
                    or (saved['change_set_ids'] and not direction_path.exists())):
                raise ValueError('candidate identity direction is outside a pre-write owner boundary')
            write_immutable(direction_path, direction)
        if args.adjudication_input_repair_authorization:
            from backend.pipeline.cvp_sql_adjudication_input_repair import SCHEMA
            direction = read_artifact(args.adjudication_input_repair_authorization)
            if direction.get('schema_version') != SCHEMA or direction.get('campaign_sha256') != args.sql_campaign:
                raise ValueError('adjudication input repair direction identity/campaign drift')
            write_immutable(args.output_root/'adjudication-input-repair-authorization-binding.json',
                            {'authorization': binding(args.adjudication_input_repair_authorization)})
        if args.component_role_authorization:
            from backend.pipeline.viewpoint_component_role_resolution import AUTH_SCHEMA
            direction = read_artifact(args.component_role_authorization)
            if (direction.get('schema_version') != AUTH_SCHEMA or direction.get('campaign_sha256') != args.sql_campaign
                    or direction.get('authorized_by') != 'owner' or not direction.get('targets')):
                raise ValueError('component direction identity/campaign missing')
            write_immutable(args.output_root/'component-role-authorization-binding.json',
                            {'authorization': binding(args.component_role_authorization)})
        if args.claim_issue_policy:
            from backend.pipeline.claim_issue_management import read_policy
            read_policy(binding(args.claim_issue_policy))
            write_immutable(args.output_root/'claim-issue-policy-binding.json',{'policy':binding(args.claim_issue_policy)})
        if args.cvp_scope_policy:
            from backend.pipeline.cvp_scope_management import read_policy
            read_policy(binding(args.cvp_scope_policy))
            write_immutable(args.output_root/'cvp-scope-policy-binding.json',{'policy':binding(args.cvp_scope_policy)})
        if args.owner_claim_revision:
            from backend.pipeline.source_reconciliation_runtime import ProductionPorts
            owner_direction=read_artifact(args.owner_claim_revision)
            owner_claim_revision(store,owner_direction['run_id'],args.owner_claim_revision,
                args.output_root/owner_direction['run_id']/'claim-issues',ProductionPorts())
            definition,group,row=next_group(store,args.sql_campaign)
        if args.action!='group':
            if not args.run_id:raise ValueError('bounded action requires exact admitted SQL run ID')
            saved=store.get_cvp_group(args.run_id)
            if not saved or saved['campaign_sha256']!=args.sql_campaign:raise ValueError('bounded action campaign drift')
            if invocation_limit is not None and saved['sequence'] > invocation_limit:
                raise ValueError('action exceeds owner execution boundary')
            action_root=args.output_root/args.run_id/'actions'
            from backend.pipeline.cvp_sql_actions import execute_source_action,execute_owner_link_action
            from backend.pipeline.source_reconciliation_runtime import ProductionPorts
            if args.action=='source-impact-complete':
                from backend.pipeline.cvp_source_impact_resolution import SLICE_IMPACTS,build_statement_impact_resolution
                if saved['checkpoint']=='audited':
                    completion=saved['completion']
                    proof=Path(completion['retained_impacts_resolution']['path'])
                    check_binding(completion['retained_impacts_resolution'])
                    if read_artifact(proof).get('schema_version')!=SLICE_IMPACTS:
                        raise ValueError('completed group lacks exact statement-impact authority')
                    audit=read_artifact(completion['audit']['path'])
                    store.complete_cvp_group(args.run_id,audit=audit,audit_binding=completion['audit'],
                        read_pins=decoded_pins(read_artifact(proof)['read_pins']),
                        owner_resolution_path=Path(completion['owner_resolution_path']),
                        retained_impacts_resolution_path=proof,
                        taxonomy_dependency_path=Path(completion['taxonomy_dependency']['path'])
                            if completion.get('taxonomy_dependency') else None)
                    stage_event('audited_cached',run_id=args.run_id,new_model_calls=0,registry_writes=0)
                    return 0
                completion_root=action_root/('statement-impact-completion-'+sha256_json({
                    'reviewed':saved['reviewed_sha256'],'change_set_ids':saved['change_set_ids']})[:20])
                proof,resolution,audit,read_pins=build_statement_impact_resolution(store,saved,completion_root/'resolution.json')
                from backend.pipeline.cvp_taxonomy_dependency import prepare as prepare_taxonomy
                current=store.read_cvp_context(saved['claim_ids'],viewpoint_ids=read_artifact(proof)['viewpoint_ids'])
                taxonomy=prepare_taxonomy(store,args.run_id,Path(read_artifact(proof)['audit']['path']),current)
                store.complete_cvp_group(args.run_id,audit=audit,audit_binding=read_artifact(proof)['audit'],
                    read_pins=decoded_pins(read_pins),owner_resolution_path=resolution,
                    retained_impacts_resolution_path=proof,taxonomy_dependency_path=taxonomy)
                stage_event('audited',sequence=saved['sequence'],run_id=args.run_id,
                            management_resolution=binding(proof),new_model_calls=0,registry_writes=0)
                return 0
            elif args.action=='source-pending-check':
                from backend.pipeline.cvp_source_impact_resolution import verify_pending_commits
                result=verify_pending_commits(store,saved)
                output=action_root/('source-pending-commits-'+sha256_json(result)[:20]+'.json')
                write_immutable(output,result)
                stage_event('source_pending_commits_verified',receipt=binding(output),
                            verified=[i['record_key'] for i in result['items']],
                            unapproved_context=[i['record_key'] for i in result['unapproved_context_items']],
                            remaining=result['remaining_record_keys'],group_complete=False)
                return 0
            elif args.action=='audit-run':
                result=revalidate_applied_run(store,definition,args.run_id,root=args.output_root)
                stage_event('independent_impact_audited_management_pending' if result.get('group_complete') is False
                            else 'bounded_audit_completed',result=result)
                return 0
            elif args.action=='relation-retire':
                if not args.direction:raise ValueError('relation retirement requires exact owner direction')
                from backend.pipeline.claim_relation_retirement import execute
                result=execute(store,args.run_id,authorization=binding(args.direction),
                               root=action_root/'relation-retire',ports=ProductionPorts())
                stage_event('relation_retired_group_pending',run_id=args.run_id,result=result,group_complete=False)
                return 0
            elif args.action=='source-write-slice':
                if not args.direction:raise ValueError('source write slice requires exact direction')
                from backend.pipeline.claim_source_write_slice import execute
                direction=read_artifact(args.direction)
                if direction.get('run_id')!=args.run_id:raise ValueError('source write slice foreign run')
                result=execute(store,binding(args.direction),ports=ProductionPorts())
                if direction.get('mode') in {'owner_statement_completion','policy_evidence_binding','policy_claim_source_revision'}:
                    from backend.pipeline.claim_first_management import finish_claim_audit
                    proof=finish_claim_audit(store,args.run_id,Path(direction['parent_root']))
                    stage_event('owner_statement_slice_claim_layer_audited_cvp_pending' if direction['mode']=='owner_statement_completion'
                                else 'evidence_binding_slice_claim_layer_audited_cvp_pending',run_id=args.run_id,
                                proof=proof,group_complete=False)
                stage_event('source_write_applied_pending',run_id=args.run_id,group_complete=False,result=result)
                return 0  # Never complete the group or start unrelated management.
            elif args.action=='source':
                if not args.manifest:raise ValueError('source action lacks bounded Manifest/authorization')
                from backend.pipeline.cvp_sql_actions import read_source_manifest
                manifest=read_source_manifest(args.manifest)
                if args.source_permission_recovery_authorization:
                    from backend.pipeline.source_permission_recovery import enable
                    enable(store,binding(args.source_permission_recovery_authorization),args.output_root)
                source_root=action_root/'source'
                if args.claim_first:
                    from backend.pipeline.cvp_sql_actions import source_action_id
                    from backend.pipeline.source_reconciliation import Manifest
                    aid=source_action_id(Manifest.model_validate(manifest))
                    registered=saved.get('execution_roots',{}).get(aid)
                    if not registered:raise ValueError('Claim-first requires original registered action; no new root/budget')
                    bound=Path(registered).resolve()
                    if not bound.is_relative_to(args.output_root.resolve()/args.run_id) or bound.name!=aid:
                        raise ValueError('Claim-first original SQL action root drift')
                    source_root=bound.parent
                execute_source_action(store,args.run_id,manifest,root=source_root,ports=ProductionPorts(),claim_first=args.claim_first)
                if manifest.get('evidence_binding_repair'):
                    from backend.pipeline.claim_evidence_binding import finish_claim_audit
                    proof=finish_claim_audit(store,args.run_id)
                    stage_event('evidence_binding_shared_owners_audited_cvp_pending',run_id=args.run_id,proof=proof,group_complete=False)
                    return 0
                if manifest.get('anchor_qualification'):
                    from backend.pipeline.claim_owner_revision import finish_dependency_audit
                    proof=finish_dependency_audit(store,args.run_id)
                    stage_event('anchor_qualification_claim_audited_cvp_pending',run_id=args.run_id,dependency_audit=proof,group_complete=False)
                    return 0
                if args.claim_first:
                    from backend.pipeline.claim_first_management import finish_claim_audit
                    finish_claim_audit(store,args.run_id,bound)
                    stage_event('claim_layer_audited_cvp_pending', run_id=args.run_id, group_complete=False)
                    return 0
            elif args.action=='claim-retire':
                if not args.direction:raise ValueError('Claim retirement requires exact human disposition')
                from backend.pipeline.claim_retirement import execute
                execute(store,args.run_id,authorization=binding(args.direction),
                        root=action_root/'claim-retire',ports=ProductionPorts())
            elif args.action=='link-representation':
                if not args.direction:raise ValueError('representation requires an exact owner direction')
                from backend.pipeline.cvp_link_representation import execute,production_ports
                if args.link_representation_disposition:
                    from backend.pipeline.cvp_link_representation import execute_human
                    execute_human(store,args.run_id,authorization=binding(args.direction),
                        disposition=binding(args.link_representation_disposition),
                        root=action_root/'link-representation',ports=production_ports())
                else:
                    execute(store,args.run_id,authorization=binding(args.direction),
                            root=action_root/'link-representation',ports=production_ports())
            elif args.action=='link-replace':
                if not args.direction:raise ValueError('replacement requires an exact owner direction')
                from backend.pipeline.cvp_link_replacement import execute, production_ports, WITHDRAWAL_DIRECTION
                withdrawal_only = read_artifact(args.direction).get('schema_version') == WITHDRAWAL_DIRECTION
                execute(store,args.run_id,authorization=binding(args.direction),
                        root=action_root/'link-replace',ports=production_ports(withdrawal_only=withdrawal_only))
            elif args.action=='link-retire':
                if not args.direction:raise ValueError('retirement requires exact human disposition')
                from backend.pipeline.cvp_link_retirement import execute
                execute(store,args.run_id,authorization=binding(args.direction),
                        root=action_root/'link-retire',ports=ProductionPorts())
            elif args.action=='link-retarget':
                if not args.direction:raise ValueError('retarget requires an exact owner direction')
                from backend.pipeline.cvp_link_retarget import execute, production_ports
                execute(store,args.run_id,authorization=binding(args.direction),
                        root=action_root/'link-retarget',ports=production_ports())
            else:
                if not all((args.direction,args.adjudication_root,args.link_id)):raise ValueError('owner link direction incomplete')
                from backend.pipeline.owner_approved_link_correction import ReviewPorts
                execute_owner_link_action(store,args.run_id,args.adjudication_root,link_id=args.link_id,
                    authorization=binding(args.direction),root=action_root/'owner-link',ports=ReviewPorts())
            definition,group,row=next_group(store,args.sql_campaign)
        while group is not None:
            if checkpoint_reached(group,invocation_limit):
                stage_event('owner_checkpoint', stop_after_group=args.stop_after_group, pause_after_group=args.pause_after_group)
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
            if group['sequence']==invocation_limit:
                stage_event('owner_checkpoint', stop_after_group=args.stop_after_group, pause_after_group=args.pause_after_group)
                break
            definition,group,row = next_group(store,args.sql_campaign)
    return 0


if __name__ == "__main__": raise SystemExit(main())
