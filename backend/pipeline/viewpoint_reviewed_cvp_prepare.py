"""Prepare the deterministic first CVP group from shared accepted grouping.

The full parent denominator is retained and rechecked. Only the mechanically
selected intact canary is qualified for this first production phase; nothing
asserts that the remaining groups have been processed or qualified for CVP.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore, normalize_package
from backend.api.canonical_repository.reviewed_candidate_contract import validate_store_package_authorization
from backend.api.canonical_repository.viewpoint_foundation import build_coverage_snapshot, build_input_claim_manifest, sha256_json, semantic_record_sha
from backend.pipeline.viewpoint_evidence_source_context import load_context_source
from backend.api.canonical_repository.viewpoint_production_safety import (
    CvpProductionBlocked, accepted_grouping_canary, accepted_grouping_audit_scope,
    build_cvp_freeze, build_canary_selection, validate_apply_authorization, accepted_grouping_phase_group,
    CVP_APPLY_AUTHORIZATION_VERSION,
)
from backend.pipeline.viewpoint_grouping_repository import SharedGroupingRepository
from backend.pipeline.viewpoint_historical_source_eligibility import build_historical_source_eligibility
from backend.pipeline.viewpoint_scope_packet_runner import build_scope_packet
from backend.pipeline.viewpoint_scoped_audit_disposition import scope_audit_disposition
from backend.pipeline.viewpoint_source_attestation_runner import _write_immutable
from backend.pipeline.viewpoint_cvp_policy import load_cvp_policy, cvp_policy_fingerprint, cvp_policy_prompt_sha256s
from backend.pipeline.viewpoint_route_policy import load_route_policy, route_policy_fingerprint, route_policy_prompt_sha256s
from backend.pipeline.viewpoint_resolution_runtime import PROMPT_DIR

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def validate_completed_parent_audit(row, group_rows):
    """Reconsume the exact completed-group decision at the source-repair gate."""
    from backend.pipeline.viewpoint_cvp_campaign_runner import binding, read, validate_audit
    if row['group_key'] not in group_rows or any(binding(b['path']) != b for b in row['bindings']):
        raise CvpProductionBlocked(['completed parent proof physical drift'])
    if binding(row['audit_path']) not in row['bindings']:
        raise CvpProductionBlocked(['completed parent audit lacks physical binding'])
    audited = read(row['audit_path'])
    from backend.pipeline.viewpoint_g035_temporary_audit import validate_completion_disclosure
    validate_completion_disclosure(row, audited)
    claim_path = row.get('claim_audit_path', row['audit_path'])
    if binding(claim_path) not in row['bindings']:
        raise CvpProductionBlocked(['completed parent Claim audit lacks physical binding'])
    resolution_path = row.get('owner_resolution_path')
    if resolution_path is not None and binding(resolution_path) not in row['bindings']:
        raise CvpProductionBlocked(['completed parent resolution lacks physical binding'])
    claims = group_rows[row['group_key']]['claim_ids']
    validate_audit({'meta': audited['meta'], 'layers': {
        '3': read(claim_path)['layers']['3'], '4': audited['layers']['4']}},
        claims, row['viewpoint_ids'], owner_resolution_path=resolution_path)
    return claims


def verify_parent_current_pins(manifest: dict[str, Any], records: dict[tuple[str, str], dict[str, Any]]) -> None:
    pins: dict[tuple[str, str], tuple[int, str]] = {}
    def pin(collection, oid, revision, content_sha):
        key, value = (collection, oid), (revision, content_sha)
        if key in pins and pins[key] != value:
            raise CvpProductionBlocked([f"{collection}/{oid}: conflicting accepted pins"])
        pins[key] = value
    for claim in manifest["claim_packets"]:
        pin("claims", claim["claim_id"], claim["claim_revision"], claim["claim_content_sha256"])
        for step in claim["evidence_steps"]:
            pin("evidence_steps", step["evidence_step_id"], step["revision"], step["content_sha256"])
            for fragment in step["fragments"]:
                pin("source_fragments", fragment["fragment_id"], fragment["revision"], fragment["content_sha256"])
    for source in manifest["sources"]:
        pin("source_documents", source["source_id"], source["source_revision"], source["source_content_sha256"])
    for key, expected in pins.items():
        current = records.get(key)
        if current is None or current.get("retired") or (current["revision"], current["content_sha256"]) != expected:
            raise CvpProductionBlocked([f"{key[0]}/{key[1]}: accepted parent current pin drift"])


def completed_repair_parent_snapshot(manifest, records, packages, completed_ids, selected_ids):
    """Keep the accepted historical membership without granting stale authority.

    Only already independently completed Claims may carry a fresh source repair.
    Their exact reviewed current records and physical additions are validated;
    the accepted predecessor pins remain explicit historical membership proof.
    No repaired Claim can enter a new runtime scope through this adapter.
    """
    checked=deepcopy(records); rows={c['claim_id']:c for c in manifest['claim_packets']}
    used=set()
    for package in packages:
        validate_store_package_authorization(package)
        app=package.get('source_repair_application')
        if not app: raise CvpProductionBlocked(['parent repair lacks fresh source-repair authority'])
        packet=app['review_packet']; normalized=normalize_package(package)
        if app.get('schema_version') == 'wang_s201129_owner_opus_source_reconciliation_v1':
            from backend.pipeline.viewpoint_s201129_source_epoch import membership_snapshot
            checked, changed = membership_snapshot(manifest,checked,package,completed_ids,selected_ids)
            if used.intersection(changed):
                raise CvpProductionBlocked(['duplicate pending source epoch Claim authority'])
            used.update(changed)
            continue
        if app.get('schema_version') == 'wang_g041_owner_qualified_transcript_reconciliation_v2':
            from backend.pipeline.viewpoint_g041_source_epoch import membership_snapshot
            checked, changed = membership_snapshot(manifest,checked,package,completed_ids,selected_ids)
            if used.intersection(changed):
                raise CvpProductionBlocked(['duplicate completed source epoch Claim authority'])
            used.update(changed)
            continue
        if app.get('schema_version') in {'wang_g037_claim_statement_repair_v1','wang_g037_claim_statement_repair_v2'}:
            old,new=packet['claim_revision']['before'],packet['claim_revision']['after'];cid=new['claim_id']
            if cid in used or cid not in completed_ids or cid in selected_ids or cid not in rows:
                raise CvpProductionBlocked([f'{cid}: statement repair must be completed and never selected'])
            prior=rows[cid]
            if old['revision']!=prior['claim_revision'] or semantic_record_sha(old)!=prior['claim_content_sha256']:
                raise CvpProductionBlocked([f'{cid}: statement predecessor membership drift'])
            for collection,objects in normalized.items():
                for oid,expected in objects.items():
                    current=records.get((collection,oid))
                    if not current or current.get('retired') or current['revision']!=expected['revision'] or current['payload']!=expected or current['content_sha256']!=semantic_record_sha(expected):
                        raise CvpProductionBlocked([f'{collection}/{oid}: reviewed statement repair current drift'])
            for t in packet['targets']:
                for row in t['evidence']:
                    for collection,key,id_key in [('evidence_steps','step','evidence_step_id'),('source_fragments','fragment','fragment_id')]:
                        expected=row[key];current=records.get((collection,expected[id_key]))
                        if not current or current.get('retired') or current['payload']!=expected:
                            raise CvpProductionBlocked([f'{cid}: preserved statement evidence drift'])
            for d in packet['preserved_claim_dependents']:
                current=records.get((d['collection'],d['object_id']))
                if not current or current.get('retired') or current['payload']!=d['payload']:
                    raise CvpProductionBlocked([f'{cid}: preserved statement dependent drift'])
            used.add(cid)
            checked[('claims',cid)]={'revision':old['revision'],'content_sha256':semantic_record_sha(old),'payload':old,'retired':False}
            continue
        if app.get('schema_version') == 'wang_owner_evidence_annotation_v1':
            # Exact labeled editorial annotation on an independently completed
            # Claim. Restore only its historical Evidence pin for membership;
            # current reviewed postimage and original physical source are checked.
            from backend.pipeline.viewpoint_owner_evidence_annotation import verify_files
            old,new=packet['before_evidence'],packet['after_evidence'];cid=packet['claim']['claim_id']
            if cid in used or cid not in completed_ids or cid in selected_ids or cid not in rows:
                raise CvpProductionBlocked([f'{cid}: annotation parent must be completed and never selected'])
            prior=rows[cid]
            if packet['claim']['revision']!=prior['claim_revision'] or semantic_record_sha(packet['claim'])!=prior['claim_content_sha256']:
                raise CvpProductionBlocked([f'{cid}: annotation Claim predecessor drift'])
            eid=new['evidence_step_id'];key=('evidence_steps',eid);live=records.get(key)
            pins=[e for e in prior['evidence_steps'] if e['evidence_step_id']==eid]
            if len(pins)!=1 or old['revision']!=pins[0]['revision'] or semantic_record_sha(old)!=pins[0]['content_sha256']:
                raise CvpProductionBlocked([f'{eid}: annotation predecessor pin drift'])
            if not live or live.get('retired') or live['payload']!=new or live['revision']!=new['revision'] or live['content_sha256']!=semantic_record_sha(new):
                raise CvpProductionBlocked([f'{eid}: reviewed annotation current drift'])
            verify_files(packet)
            used.add(cid);checked[key]={'revision':old['revision'],'content_sha256':semantic_record_sha(old),'payload':old,'retired':False}
            continue
        if app.get('schema_version') == 'wang_claim_qualifier_repair_v2':
            old,new=packet['before_claim'],package['claims'][0]; cid=new['claim_id']
            if cid in used or cid not in completed_ids or cid in selected_ids or cid not in rows:
                raise CvpProductionBlocked([f'{cid}: qualifier repair must be independently completed and never selected'])
            prior=rows[cid]
            if old['revision']!=prior['claim_revision'] or semantic_record_sha(old)!=prior['claim_content_sha256']:
                raise CvpProductionBlocked([f'{cid}: qualifier predecessor differs from accepted membership'])
            for collection,objects in normalized.items():
                for oid,expected in objects.items():
                    current=records.get((collection,oid))
                    if not current or current.get('retired') or current['revision']!=expected['revision'] or current['payload']!=expected or current['content_sha256']!=semantic_record_sha(expected):
                        raise CvpProductionBlocked([f'{collection}/{oid}: reviewed qualifier current drift'])
            source_ids={row['fragment']['source_id'] for row in packet['bound_evidence']}
            if len(source_ids)!=1:
                raise CvpProductionBlocked([f'{cid}: qualifier source population mismatch'])
            source=records[('source_documents',next(iter(source_ids)))]['payload']
            path,file_sha,projection=load_context_source(source)
            if str(path)!=packet['source_path'] or file_sha!=packet['source_file_sha256'] or projection.body_sha256!=packet['source_body_sha256'] or [r['text'] for r in projection.spoken_rows]!=packet['complete_original']:
                raise CvpProductionBlocked([f'{cid}: qualifier source physical drift'])
            for row in packet['bound_evidence']:
                for collection,key in [('evidence_steps','step'),('source_fragments','fragment')]:
                    payload=row[key]; oid=payload['evidence_step_id'] if key=='step' else payload['fragment_id']
                    current=records.get((collection,oid))
                    if not current or current.get('retired') or current['payload']!=payload:
                        raise CvpProductionBlocked([f'{collection}/{oid}: preserved qualifier evidence drift'])
            used.add(cid)
            checked[('claims',cid)]={'revision':old['revision'],'content_sha256':semantic_record_sha(old),'payload':old,'retired':False}
            continue
        for item in packet['items']:
            old,new=item['before'],item['after']; cid=new['claim_id']
            if cid in used or cid not in completed_ids or cid in selected_ids or cid not in rows:
                raise CvpProductionBlocked([f'{cid}: parent repair must be unique and independently completed, never selected'])
            used.add(cid); prior=rows[cid]
            if old['revision']!=prior['claim_revision'] or semantic_record_sha(old)!=prior['claim_content_sha256']:
                raise CvpProductionBlocked([f'{cid}: repair predecessor differs from accepted membership'])
            for collection,oid in [('claims',cid),('evidence_steps',item['new_evidence_step']['evidence_step_id']),('source_fragments',item['new_fragment']['fragment_id'])]:
                live=records.get((collection,oid)); expected=normalized[collection][oid]
                if not live or live.get('retired') or live['revision']!=expected['revision'] or live['content_sha256']!=semantic_record_sha(expected) or live['payload']!=expected:
                    raise CvpProductionBlocked([f'{collection}/{oid}: reviewed parent repair current drift'])
            source=records[('source_documents',item['new_fragment']['source_id'])]['payload']
            path,file_sha,projection=load_context_source(source)
            text=projection.spoken_rows[int(item['new_fragment']['paragraph_key'][1:])-1]['text']
            if str(path)!=packet['source_path'] or file_sha!=packet['source_file_sha256'] or text!=item['source_paragraph']:
                raise CvpProductionBlocked([f'{cid}: repaired source physical drift'])
            checked[('claims',cid)]={'revision':old['revision'],'content_sha256':semantic_record_sha(old),'payload':old,'retired':False}
    verify_parent_current_pins(manifest,checked)
    return sorted(used)


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    load_dotenv(PROJECT_ROOT / ".env")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=PROJECT_ROOT, text=True).strip():
        raise CvpProductionBlocked(["commit implementation before production preparation"])
    runner_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip()
    repository = SharedGroupingRepository(args.grouping_repository)
    from backend.pipeline.viewpoint_applied_source_epochs import GROUPING_EPOCH_KEY, compose
    repair_paths=getattr(args,'completed_source_repair',None) or []
    applied_epoch=compose(repair_paths)
    loaded = repository.load(args.accepted_grouping_sha, source_epoch_binding=applied_epoch)
    manifest = loaded["manifest"]
    stage = "group:" + args.group_key if getattr(args, "group_key", None) else "canary"
    canary = accepted_grouping_phase_group(manifest, stage)
    selected_ids = sorted(canary["claim_ids"])
    wanted = set(selected_ids)
    all_ids = {row["claim_id"] for row in manifest["claim_packets"]}
    for claim in manifest["claim_packets"]:
        for step in claim["evidence_steps"]:
            all_ids.add(step["evidence_step_id"])
            all_ids.update(row["fragment_id"] for row in step["fragments"])
    all_ids.update(row["source_id"] for row in manifest["sources"])
    repair_paths=getattr(args,'completed_source_repair',None) or []
    repairs=[json.loads(path.read_text(encoding='utf-8')) for path in repair_paths]
    for repair in repairs:
        if repair.get('source_repair_application',{}).get('schema_version') == 'wang_s201129_owner_opus_source_reconciliation_v1':
            for objects in normalize_package(repair).values(): all_ids.update(objects)
        if repair.get('source_repair_application',{}).get('schema_version') == 'wang_g041_owner_qualified_transcript_reconciliation_v2':
            for objects in normalize_package(repair).values(): all_ids.update(objects)
            packet=repair['source_repair_application']['review_packet']
            for collection,key,id_key in [('claims','changed_paragraph_claims','claim_id'),('evidence_steps','changed_paragraph_evidence','evidence_step_id')]:
                for obj in packet[key]: all_ids.add(obj[id_key])
        if repair.get('source_repair_application',{}).get('schema_version') in {'wang_g037_claim_statement_repair_v1','wang_g037_claim_statement_repair_v2'}:
            for objects in normalize_package(repair).values(): all_ids.update(objects)
            packet=repair['source_repair_application']['review_packet']
            for t in packet['targets']:
                for r in t['evidence']:all_ids.update([r['step']['evidence_step_id'],r['fragment']['fragment_id']])
            for d in packet['preserved_claim_dependents']:all_ids.add(d['object_id'])
        if repair.get('source_repair_application',{}).get('schema_version')=='wang_claim_qualifier_repair_v2':
            for link in repair.get('viewpoint_claim_links',[]): all_ids.add(link['viewpoint_claim_link_id'])
        for item in repair.get('source_repair_application',{}).get('review_packet',{}).get('items',[]):
            all_ids.add(item['new_evidence_step']['evidence_step_id']); all_ids.add(item['new_fragment']['fragment_id'])
    store = PostgresKnowledgeStore()
    records = {}
    with store.connect() as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        for collection, oid, revision, content_sha, payload, retired_at in connection.execute(
            "SELECT collection,object_id,revision,content_sha256,payload,retired_at FROM wang_knowledge.objects WHERE object_id=ANY(%s)", (sorted(all_ids),),
        ).fetchall():
            records[(collection, oid)] = dict(revision=revision, content_sha256=content_sha, payload=payload, retired=retired_at is not None)
    completed_ids=set()
    if repairs:
        proof_path=getattr(args,'parent_completed_proofs',None)
        if not proof_path: raise CvpProductionBlocked(['source repair requires physical completed-group audit proofs'])
        from backend.pipeline.viewpoint_cvp_campaign_runner import read
        group_rows={g['group_key']:g for g in manifest['groups']}
        for row in read(proof_path)['groups']:
            completed_ids.update(validate_completed_parent_audit(row, group_rows))
    repaired_ids=completed_repair_parent_snapshot(manifest,records,repairs,completed_ids,wanted)
    claims = [records[("claims", cid)]["payload"] for cid in selected_ids]
    source_ids = {row["source_id"] for row in manifest["claim_packets"] if row["claim_id"] in wanted}
    sources = [records[("source_documents", sid)]["payload"] for sid in sorted(source_ids)]
    evidence = [state["payload"] for (collection, _), state in records.items() if collection == "evidence_steps"]
    fragments = [state["payload"] for (collection, _), state in records.items() if collection == "source_fragments"]
    inputs = args.output_root / "inputs"
    universe = {"schema_version": "wang_cvp_phase_source_universe_v1",
                "source_universe_manifest_id": "CVP357-CANARY-" + args.accepted_grouping_sha[:20],
                "accepted_parent_manifest_sha256": args.accepted_grouping_sha,
                "stage": stage, "coverage_status": "partial",
                "sources": sorted([{"source_id": row["source_id"], "source_revision_id": f"{row['source_id']}@{row['revision']}", "source_sha256": row["source_sha256"]} for row in sources], key=lambda row: row["source_revision_id"])}
    universe["manifest_sha256"] = sha256_json(universe)
    coverage = build_coverage_snapshot(sources, roles_by_source={sid: ["source_universe"] for sid in source_ids},
                                      source_universe_manifest=universe, created_at=args.created_at, coverage_status="partial")
    claim_manifest = build_input_claim_manifest(claims, evidence, fragments, coverage)
    eligibility = build_historical_source_eligibility(claim_manifest=claim_manifest, bridge_manifest_path=args.historical_bridges, store=store)
    source_lineage = json.loads(args.historical_bridges.read_text(encoding="utf-8"))
    audit = json.loads(args.independent_audit.read_text(encoding="utf-8"))
    disposition = scope_audit_disposition(audit=audit, manifest=accepted_grouping_audit_scope(manifest, stage),
                                         audit_file_sha256=hashlib.sha256(args.independent_audit.read_bytes()).hexdigest(), authorized_by=args.scope_authorized_by)
    if disposition["status"] != "passed":
        raise CvpProductionBlocked(["first-group independent audit has in-scope findings"])
    # Both reports derive from checks above, not an operator-supplied pass flag.
    phase = {"schema_version": "wang_cvp_qualified_group_phase_v1", "status": "passed", "stage": stage,
             "accepted_parent_manifest_sha256": args.accepted_grouping_sha,
             "parent_claim_count": len(manifest["claim_packets"]), "parent_group_count": len(manifest["groups"]),
             "qualified_claim_ids": selected_ids, "qualified_group_key": canary["group_key"],
             "deferred_claim_ids": sorted({row["claim_id"] for row in manifest["claim_packets"]} - wanted),
             "parent_current_revision_content_pins_verified": True,
             "completed_parent_source_repairs": repaired_ids,
             "completed_source_repair_bindings": [{'path':str(p.resolve()),'file_sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in repair_paths],
             "accepted_predecessor_pins_used_only_for_completed_membership": bool(repaired_ids),
             "historical_bridge_sha256": source_lineage["artifact_sha256"],
             "source_eligibility_sha256": eligibility.artifact_sha256,
             "scope_audit_disposition_sha256": disposition["artifact_sha256"],
             "full_corpus_source_eligibility_asserted": False, "master_data_mutations": 0}
    phase["artifact_sha256"] = sha256_json(phase)
    packet = build_scope_packet(scope=None, scope_label="Matthew accepted group: " + canary["group_key"],
                               passage_unit_ids=[], claim_manifest=claim_manifest, source_attestation=eligibility,
                               repository_root=args.wang_repository, database_url=None,
                               partition_claim_ids=selected_ids, partition_manifest_sha256=args.accepted_grouping_sha,
                               include_source_context=True)
    packet.update(scope_kind="accepted_grouping_canary", accepted_grouping_stage=stage)
    if applied_epoch is not None:
        packet[GROUPING_EPOCH_KEY] = applied_epoch
    packet.pop("packet_sha256")
    packet["packet_sha256"] = sha256_json(packet)
    files = {"source_universe_manifest": ("source-universe.json", universe), "coverage_snapshot": ("coverage.json", coverage.model_dump(mode="json")),
             "claim_manifest": ("claim-manifest.json", claim_manifest), "source_lineage_manifest": ("historical-lineage.json", source_lineage),
             "source_attestation": ("source-eligibility.json", eligibility.model_dump(mode="json")),
             "source_state_reconciliation": ("phase-reconciliation.json", phase), "source_state_validation": ("phase-validation.json", phase),
             "audit_disposition": ("audit-disposition.json", disposition)}
    prereqs = {"independent_audit": args.independent_audit,
               "reviewed_grouping_reference": repository.entry_path(args.accepted_grouping_sha)}
    for role, (name, artifact) in files.items():
        path = inputs / name
        _write_immutable(path, artifact)
        if role != "coverage_snapshot":
            prereqs[role] = path
    packet_path = inputs / "scope-packet.json"
    _write_immutable(packet_path, packet)
    cvp_policy = load_cvp_policy(args.cvp_policy)
    route_policy = load_route_policy(args.route_policy)
    freeze = build_cvp_freeze(ticket_id=357, scope_packet_path=packet_path, prerequisite_paths=prereqs, store=store,
        cvp_policy_sha256=cvp_policy_fingerprint(cvp_policy, prompt_sha256s=cvp_policy_prompt_sha256s(cvp_policy, prompt_dir=PROMPT_DIR)),
        route_policy_sha256=route_policy_fingerprint(route_policy, prompt_sha256s=route_policy_prompt_sha256s(route_policy, prompt_dir=PROMPT_DIR)),
        runner_commit=runner_commit, worktree_root=PROJECT_ROOT, output_root=args.output_root / "run", global_lock_path=args.global_lock)
    grouping = repository.admit(args.accepted_grouping_sha, scope_packet=packet, freeze=freeze)
    selection = build_canary_selection(grouping_envelope=grouping, freeze=freeze, scope_packet=packet, batch_size=int(cvp_policy["batch_size"])) if stage == "canary" else None
    backup_sha = hashlib.sha256(args.backup_dump.read_bytes()).hexdigest()
    if backup_sha != args.backup_sha256:
        raise CvpProductionBlocked(["backup dump differs from verified backup SHA"])
    restore = subprocess.run(["pg_restore", "--list", str(args.backup_dump)], check=True, capture_output=True).stdout
    authorization = {"schema_version": CVP_APPLY_AUTHORIZATION_VERSION, "status": "authorized", "authorized_by": args.scope_authorized_by,
                     "freeze_sha256": freeze["artifact_sha256"], "grouping_sha256": grouping["artifact_sha256"],
                     "backup_dump": {"path": str(args.backup_dump.resolve()), "sha256": backup_sha},
                     "pg_restore_list_sha256": hashlib.sha256(restore).hexdigest()}
    authorization["artifact_sha256"] = sha256_json(authorization)
    validate_apply_authorization(authorization, freeze=freeze, grouping_sha256=grouping["artifact_sha256"])
    for name, artifact in [("freeze.json", freeze), ("grouping.json", grouping), ("canary-selection.json", selection), ("apply-authorization.json", authorization)]:
        if artifact is not None:
            _write_immutable(inputs / name, artifact)
    return {"status": "prepared", "stage": stage, "group_key": canary["group_key"], "claims": len(selected_ids),
            "parent_claims": len(manifest["claim_packets"]), "parent_groups": len(manifest["groups"]),
            "runner_commit": runner_commit, "freeze_sha256": freeze["artifact_sha256"], "model_calls_executed": 0, "master_data_mutations": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("grouping-repository", "historical-bridges", "independent-audit", "output-root", "wang-repository", "cvp-policy", "route-policy", "backup-dump", "global-lock"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("accepted-grouping-sha", "scope-authorized-by", "created-at", "backup-sha256"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--group-key", help="one intact accepted group explicitly authorized for sequential continuation")
    parser.add_argument('--completed-source-repair',type=Path,action='append',help='fresh dual-reviewed repair of already independently completed parent Claims')
    parser.add_argument('--parent-completed-proofs',type=Path,help='SHA-bound independent completed-group audits; never a manual Claim pass list')
    print(json.dumps(prepare(parser.parse_args()), ensure_ascii=False))


if __name__ == "__main__":
    main()
