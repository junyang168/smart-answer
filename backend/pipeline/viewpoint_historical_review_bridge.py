"""Authenticate a passing historical Claim review across coordinate-only repair.

This is not a new review, a consensus seal, or Claim approval. It binds an
existing passing review to actual applied version history and preserves the
original review's model/provenance. A different generation with the same Claim
ID is never sufficient.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from backend.api.canonical_repository.postgres_store import REVIEW_FIELDS, _normalize_records, record_content_sha
from backend.api.canonical_repository.knowledge_models import evidence_fragment_ids, EvidenceStepRecord
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import CvpProductionBlocked, validate_content_addressed
from backend.pipeline.corpus_ai_review_runner import _normalize_claim_layer


def _meaning(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in REVIEW_FIELDS | {"revision"}}


def _fragment_meaning(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = {field: payload.get(field) for field in (
        "source_id", "verbatim_excerpt", "media_time", "source_modality", "visual_block_sha256", "visual_facts",
    )}
    if result["source_modality"] in {None, "spoken"}:
        result["source_modality"] = "spoken"
        if not result["visual_facts"]:
            result["visual_facts"] = None
    return result


def _read_binding(binding: Mapping[str, str]) -> dict[str, Any]:
    path = Path(binding["path"])
    if not path.is_absolute() or hashlib.sha256(path.read_bytes()).hexdigest() != binding["file_sha256"]:
        raise CvpProductionBlocked(["historical review bridge physical SHA drift"])
    return json.loads(path.read_text(encoding="utf-8"))


def _sealed_adjudicated_bridge(*, claim_id, current_claim, item, resolution, versions,
                              current_evidence, current_fragments, review_binding,
                              review_input_binding, sealed_lineage_binding):
    """Reuse modern source eligibility, not an imaginary historical pass.

    This branch only accepts the exact applied sealed final candidate; it
    does not extend coordinate/semantic drift privileges to adjudicated data.
    """
    from backend.api.canonical_repository.knowledge_models import ClaimRecord
    from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha
    from backend.api.canonical_repository.viewpoint_source_attestation import build_source_eligibility_artifact
    final=item['payload']; normalized,_=_normalize_records(final)
    reviewed=normalized.get('claims',{}).get(claim_id)
    if not reviewed or _meaning(reviewed)!=_meaning(current_claim):
        raise CvpProductionBlocked([f'{claim_id}: sealed adjudicated final Claim differs from current generation'])
    for eid in reviewed['evidence_step_ids']:
        if eid not in current_evidence or _meaning(current_evidence[eid])!=_meaning(normalized['evidence_steps'][eid]):
            raise CvpProductionBlocked([f'{claim_id}: sealed adjudicated evidence drift'])
        for fid in evidence_fragment_ids(EvidenceStepRecord.model_validate(current_evidence[eid])):
            if fid not in current_fragments or _fragment_meaning(current_fragments[fid])!=_fragment_meaning(normalized['source_fragments'].get(fid,{})):
                raise CvpProductionBlocked([f'{claim_id}: sealed adjudicated fragment drift'])
    chain=sorted(versions,key=lambda v:v['revision'])
    for row in chain:
        if row.get('status')!='applied' or record_content_sha(row['payload'])!=row['content_sha256'] or row['payload'].get('revision')!=row['revision']:
            raise CvpProductionBlocked([f'{claim_id}: adjudicated history is not an authenticated applied chain'])
    if not chain or chain[-1]['payload']!=dict(current_claim) or chain[-1]['revision']!=current_claim['revision']:
        raise CvpProductionBlocked([f'{claim_id}: adjudicated history is not current'])
    review=item['review_payload']; adjudication=item['adjudication_payload']
    review_rows=[r for r in review.get('claim_reviews',[]) if r.get('claim_id')==claim_id]
    results=[r for r in adjudication.get('results',[]) if r.get('claim_id')==claim_id]
    if len(review_rows)!=1 or len(results)!=1:
        raise CvpProductionBlocked([f'{claim_id}: adjudicated review coverage is not exact-once'])
    if review_rows[0].get('spot_check_selected') or review_rows[0].get('human_review_reason') or review_rows[0].get('routing_status')=='human_spot_check':
        raise CvpProductionBlocked([f'{claim_id}: adjudication cannot override historical human hold'])
    manifest={'claims':[{'claim_id':claim_id,'pinned_claim_revision':current_claim['revision'],
                        'claim_revision_sha256':semantic_record_sha(ClaimRecord.model_validate(current_claim))}]}
    manifest['manifest_sha256']=sha256_json(manifest)
    sha=lambda key:hashlib.sha256(Path(item[key]).read_bytes()).hexdigest()
    artifact=build_source_eligibility_artifact(claim_manifest=manifest,claims=[dict(current_claim)],
        evidence_steps=list(current_evidence.values()),source_fragments=list(current_fragments.values()),
        reviewed_packages_by_claim_id={claim_id:{'payload':final,'artifact_sha256':item['application']['artifact_sha256'],'file_sha256':sha('path'),'path':str(item['path'])}},
        reviews_by_claim_id={claim_id:{'payload':review,'claim_review':review_rows[0],
            'review_input_artifact_sha256':item['review_input_sha256'],'artifact_sha256':sha('review_path'),'path':str(item['review_path']),
            'adjudication_payload':adjudication,'adjudication_result':results[0],
            'adjudication_artifact_sha256':sha('adjudication_path'),'overrides_artifact_sha256':sha('overrides_path')}})
    if artifact.exceptions or len(artifact.attestations)!=1:
        raise CvpProductionBlocked([f'{claim_id}: modern sealed source eligibility rejected: {artifact.exceptions}'])
    body={'schema_version':'wang_historical_claim_review_bridge_v1','status':'verified','claim_id':claim_id,
        'pinned_claim_revision':current_claim['revision'],'claim_revision_sha256':record_content_sha(current_claim),
        'review_binding':dict(review_binding),'review_input_binding':dict(review_input_binding),
        'sealed_lineage_binding':dict(sealed_lineage_binding),
        'review_model_id':resolution['reviewer_id'],'review_provider':review['reviewer']['provider'],
        'reviewer_fingerprint_sha256':review['reviewer']['fingerprint_sha256'],
        'reviewed_applied_revision':chain[-1]['revision'],'reviewed_applied_change_set_id':chain[-1]['change_set_id'],
        'coordinate_repairs':[],'authority_kind':'sealed_adjudicated_final_candidate_not_initial_review_pass',
        'independent_review_decision':resolution['independent_review_decision'],'adjudication_status':resolution['adjudication_status'],
        'modern_source_eligibility':artifact.model_dump(mode='json'),
        'evidence_dependency_sha256':sha256_json({eid:record_content_sha(p) for eid,p in sorted(current_evidence.items())}),
        'fragment_dependency_sha256':sha256_json({fid:record_content_sha(p) for fid,p in sorted(current_fragments.items())}),
        'master_data_mutations':0,'model_calls_executed':0,'approval_status':'not_human_approved'}
    return body|{'artifact_sha256':sha256_json(body)}


def verify_historical_review_bridge(
    *, claim_id: str, current_claim: Mapping[str, Any],
    review_binding: Mapping[str, str], review_input_binding: Mapping[str, str],
    versions: Sequence[Mapping[str, Any]],
    current_evidence: Mapping[str, Mapping[str, Any]],
    current_fragments: Mapping[str, Mapping[str, Any]],
    human_closure_binding: Mapping[str, str] | None = None,
    sealed_lineage_binding: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Verify one read-only DB version chain; never ignore arbitrary drift."""
    review = _read_binding(review_binding)
    package = _read_binding(review_input_binding)
    if (review.get("source") or {}).get("package_sha256") != review_input_binding["file_sha256"]:
        raise CvpProductionBlocked([f"{claim_id}: review input generation SHA mismatch"])
    projection = _normalize_claim_layer(package)
    if review.get("reviewed_claims") != projection["candidate_claims"]:
        raise CvpProductionBlocked([f"{claim_id}: historical review does not cover its input projection"])
    reviewer = review.get("reviewer") or {}
    sealed_model = None
    if reviewer.get("provider") == "anthropic_batched":
        if sealed_lineage_binding is None:
            raise CvpProductionBlocked([f"{claim_id}: batched review requires authenticated sealed lineage"])
        lineage = _read_binding(sealed_lineage_binding)
        from backend.pipeline.viewpoint_source_attestation_runner import _validated_lineage_inputs
        items = _validated_lineage_inputs(manifest_claim_ids={row["claim_id"] for row in lineage["claims"]}, lineage_manifest_path=Path(sealed_lineage_binding["path"]))
        item = items.get(claim_id)
        if item is None or item["review_payload"] != review or item["review_input_payload"] != package:
            raise CvpProductionBlocked([f"{claim_id}: sealed lineage binds another review generation"])
        resolution = next((row for row in item["application"]["review_resolutions"] if row["claim_id"] == claim_id), {})
        if resolution.get('target_review_status')=='ai_consensus_reviewed' and resolution.get('adjudication_status')=='auto_applied' and resolution.get('independent_review_decision')=='changes_suggested':
            if human_closure_binding is not None:
                raise CvpProductionBlocked([f'{claim_id}: automatic adjudication cannot override a human hold'])
            return _sealed_adjudicated_bridge(claim_id=claim_id,current_claim=current_claim,item=item,resolution=resolution,versions=versions,
                current_evidence=current_evidence,current_fragments=current_fragments,review_binding=review_binding,
                review_input_binding=review_input_binding,sealed_lineage_binding=sealed_lineage_binding)
        if resolution.get("target_review_status") != "ai_consensus_reviewed" or resolution.get("adjudication_status") != "not_required" or resolution.get("independent_review_decision") != "pass":
            raise CvpProductionBlocked([f"{claim_id}: sealed review does not resolve this passing Claim"])
        sealed_model = resolution.get("reviewer_id")
    identity = {key: value for key, value in reviewer.items() if key not in {"fingerprint_sha256", "generated_at", "artifact_sha256", "provider"}}
    if sealed_model is None and sha256_json(identity) != reviewer.get("fingerprint_sha256"):
        raise CvpProductionBlocked([f"{claim_id}: historical reviewer fingerprint mismatch"])
    rows = [row for row in review.get("claim_reviews") or [] if row.get("claim_id") == claim_id]
    human_confirmed = False
    if human_closure_binding is not None:
        closure = _read_binding(human_closure_binding)
        validate_content_addressed(closure, "artifact_sha256")
        approvals = [row for row in closure.get("claims") or [] if row.get("claim_id") == claim_id]
        if closure.get("schema_version") != "wang_claim_human_spot_check_closure_v1" or closure.get("confirmed_by") != "human_user" or closure.get("user_message") != "确认" or len(approvals) != 1:
            raise CvpProductionBlocked([f"{claim_id}: invalid human spot-check closure"])
        approved = approvals[0]
        if approved.get("decision") != "pass" or approved.get("claim_revision_sha256") != record_content_sha(current_claim) or approved.get("pinned_claim_revision") != current_claim["revision"] or approved.get("review_binding") != dict(review_binding):
            raise CvpProductionBlocked([f"{claim_id}: human closure does not bind current Claim/review"])
        if approved.get("evidence_dependency_sha256") != sha256_json({eid: record_content_sha(raw) for eid, raw in sorted(current_evidence.items())}) or approved.get("fragment_dependency_sha256") != sha256_json({fid: record_content_sha(raw) for fid, raw in sorted(current_fragments.items())}):
            raise CvpProductionBlocked([f"{claim_id}: human closure evidence drift"])
        human_confirmed = True
    if len(rows) != 1 or any((
        rows[0].get("decision") != "pass", bool(rows[0].get("issues")),
        rows[0].get("routing_status") != ("human_spot_check" if human_confirmed else "ai_reviewed"), bool(rows[0].get("human_review_reason")),
        rows[0].get("reviewer_fingerprint") != reviewer.get("fingerprint_sha256"),
        reviewer.get("provider") not in ({"anthropic_batched"} if sealed_model else {"anthropic"}), not (sealed_model or reviewer.get("review_model_id")),
        bool(rows[0].get("spot_check_selected")) != human_confirmed,
    )):
        raise CvpProductionBlocked([f"{claim_id}: historical review is not a resolved independent pass"])
    normalized, _ = _normalize_records(package)
    reviewed = normalized.get("claims", {}).get(claim_id)
    if reviewed is None or current_claim.get("claim_id") != claim_id:
        raise CvpProductionBlocked([f"{claim_id}: review input lacks this Claim"])
    ordered = sorted(versions, key=lambda row: row["revision"])
    for row in ordered:
        if row.get("status") != "applied" or record_content_sha(row["payload"]) != row["content_sha256"] or row["payload"].get("revision") != row["revision"]:
            raise CvpProductionBlocked([f"{claim_id}: version history is not an authenticated applied chain"])
    baselines = [row for row in ordered if _meaning(row["payload"]) == _meaning(reviewed)]
    if not baselines:
        raise CvpProductionBlocked([f"{claim_id}: no applied generation matches the independently reviewed Claim"])
    baseline = baselines[-1]
    chain = [row for row in ordered if row["revision"] >= baseline["revision"]]
    if [row["revision"] for row in chain] != list(range(baseline["revision"], int(current_claim["revision"]) + 1)) or chain[-1]["payload"] != dict(current_claim):
        raise CvpProductionBlocked([f"{claim_id}: historical chain is discontinuous or not current"])
    repairs = []
    for before, after in zip(chain, chain[1:]):
        if _meaning(before["payload"]) == _meaning(after["payload"]) and after.get("source_kind") == "review_decision":
            continue
        if after.get("source_kind") != "wkp368_source_contract_cleanup":
            raise CvpProductionBlocked([f"{claim_id}: post-review change is not a supported coordinate repair"])
        metadata = after.get("metadata") or {}
        plan_binding = {"path": metadata["source_plan_path"], "file_sha256": metadata["source_plan_file_sha256"]}
        plan = _read_binding(plan_binding)
        validate_content_addressed(plan, "artifact_sha256")
        if plan["plan"]["change_set_id"] != after["change_set_id"] or plan["plan"]["source_sha256"] != after.get("source_sha256"):
            raise CvpProductionBlocked([f"{claim_id}: coordinate plan is not the applied ChangeSet"])
        matches = [row for row in plan["changed_records"] if row["collection"] == "claims" and row["object_id"] == claim_id]
        if len(matches) != 1 or matches[0]["before_sha256"] != before["content_sha256"] or matches[0]["after_sha256"] != after["content_sha256"]:
            raise CvpProductionBlocked([f"{claim_id}: repair plan does not bind Claim before/after versions"])
        # Only paragraph coordinates may differ; source identity, wording,
        # evidence IDs, quotation, stance and every other field must remain.
        left, right = deepcopy(_meaning(before["payload"])), deepcopy(_meaning(after["payload"]))
        for payload in (left, right):
            for occurrence in payload.get("occurrences") or []:
                for anchor in occurrence.get("anchors") or []:
                    anchor.pop("paragraph_key", None)
        if left != right:
            raise CvpProductionBlocked([f"{claim_id}: repair changed more than paragraph coordinates"])
        source = plan["source"]
        physical_source = Path(source["source_path"])
        if not physical_source.is_absolute() or hashlib.sha256(physical_source.read_bytes()).hexdigest() != source["source_file_sha256"]:
            raise CvpProductionBlocked([f"{claim_id}: repaired source physical SHA drift"])
        if (review.get("source", {}).get("transcript_sha256") or {}).get(source["source_id"]) != source["source_file_sha256"]:
            raise CvpProductionBlocked([f"{claim_id}: review and repair bind different source bytes"])
        repairs.append({"change_set_id": after["change_set_id"], "plan_binding": plan_binding,
                        "before_revision": before["revision"], "after_revision": after["revision"],
                        "before_sha256": before["content_sha256"], "after_sha256": after["content_sha256"]})
    for evidence_id in reviewed["evidence_step_ids"]:
        if evidence_id not in current_evidence or _meaning(current_evidence[evidence_id]) != _meaning(normalized["evidence_steps"][evidence_id]):
            raise CvpProductionBlocked([f"{claim_id}: reviewed EvidenceStep changed after review"])
        for fragment_id in evidence_fragment_ids(EvidenceStepRecord.model_validate(current_evidence[evidence_id])):
            old = normalized["source_fragments"].get(fragment_id)
            current = current_fragments.get(fragment_id)
            if old is None or current is None or _fragment_meaning(old) != _fragment_meaning(current):
                raise CvpProductionBlocked([f"{claim_id}: reviewed fragment meaning/modality changed after review"])
    body = {
        "schema_version": "wang_historical_claim_review_bridge_v1", "status": "verified",
        "claim_id": claim_id, "pinned_claim_revision": current_claim["revision"],
        "claim_revision_sha256": record_content_sha(current_claim),
        "review_binding": dict(review_binding), "review_input_binding": dict(review_input_binding),
        "review_model_id": sealed_model or reviewer["review_model_id"], "review_provider": reviewer["provider"],
        "reviewer_fingerprint_sha256": reviewer["fingerprint_sha256"],
        "reviewed_applied_revision": baseline["revision"],
        "reviewed_applied_change_set_id": baseline["change_set_id"], "coordinate_repairs": repairs,
        "evidence_dependency_sha256": sha256_json({eid: record_content_sha(current_evidence[eid]) for eid in sorted(reviewed["evidence_step_ids"])}),
        "fragment_dependency_sha256": sha256_json({fid: record_content_sha(current_fragments[fid]) for eid in reviewed["evidence_step_ids"] for fid in evidence_fragment_ids(EvidenceStepRecord.model_validate(current_evidence[eid]))}),
        "master_data_mutations": 0, "model_calls_executed": 0, "approval_status": "not_human_approved",
    }
    if human_closure_binding is not None:
        body["human_spot_check_closure_binding"] = dict(human_closure_binding)
        body["human_spot_check_confirmed"] = True
    if sealed_lineage_binding is not None:
        body["sealed_lineage_binding"] = dict(sealed_lineage_binding)
    return body | {"artifact_sha256": sha256_json(body)}


def main() -> None:
    import argparse
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.viewpoint_source_attestation_runner import _write_immutable
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    selection = json.loads(args.selection.read_text(encoding="utf-8"))
    ids = [row["claim_id"] for row in selection["claims"]]
    if not ids or len(ids) != len(set(ids)):
        raise CvpProductionBlocked(["historical review selection must be nonempty and unique"])
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    store = PostgresKnowledgeStore()
    proofs = []
    with store.connect() as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        for selected in selection["claims"]:
            cid = selected["claim_id"]
            current = connection.execute("SELECT payload FROM wang_knowledge.objects WHERE collection='claims' AND object_id=%s AND retired_at IS NULL", (cid,)).fetchone()
            if current is None:
                raise CvpProductionBlocked([f"{cid}: current Claim missing"])
            history = connection.execute("SELECT v.revision,v.content_sha256,v.payload,v.change_set_id,c.source_kind,c.source_sha256,c.status,c.metadata FROM wang_knowledge.object_versions v JOIN wang_knowledge.change_sets c USING(change_set_id) WHERE v.collection='claims' AND v.object_id=%s ORDER BY v.revision", (cid,)).fetchall()
            columns = ("revision", "content_sha256", "payload", "change_set_id", "source_kind", "source_sha256", "status", "metadata")
            evidence, fragments = {}, {}
            for eid in current[0]["evidence_step_ids"]:
                raw = connection.execute("SELECT payload FROM wang_knowledge.objects WHERE collection='evidence_steps' AND object_id=%s AND retired_at IS NULL", (eid,)).fetchone()
                if raw is None:
                    raise CvpProductionBlocked([f"{cid}: active evidence missing"])
                evidence[eid] = raw[0]
                for fid in evidence_fragment_ids(EvidenceStepRecord.model_validate(raw[0])):
                    fragment = connection.execute("SELECT payload FROM wang_knowledge.objects WHERE collection='source_fragments' AND object_id=%s AND retired_at IS NULL", (fid,)).fetchone()
                    if fragment is None:
                        raise CvpProductionBlocked([f"{cid}: active fragment missing"])
                    fragments[fid] = fragment[0]
            proofs.append(verify_historical_review_bridge(
                claim_id=cid, current_claim=current[0], versions=[dict(zip(columns, row)) for row in history],
                review_binding=selected["review_binding"], review_input_binding=selected["review_input_binding"],
                current_evidence=evidence, current_fragments=fragments,
                human_closure_binding=selected.get("human_closure_binding"),
                sealed_lineage_binding=selected.get("sealed_lineage_binding"),
            ))
    body = {"schema_version": "wang_historical_claim_review_bridges_v1", "status": "verified",
            "selection_file_sha256": hashlib.sha256(args.selection.read_bytes()).hexdigest(),
            "proofs": proofs, "verified_claim_count": len(proofs), "model_calls_executed": 0,
            "master_data_mutations": 0, "consumer_source_eligibility_artifact": False}
    _write_immutable(args.output, body | {"artifact_sha256": sha256_json(body)})
    print(json.dumps({"status": "verified", "claims": len(proofs), "output": str(args.output)}))


if __name__ == "__main__":
    main()
