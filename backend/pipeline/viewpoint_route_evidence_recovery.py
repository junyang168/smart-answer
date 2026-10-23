"""Continue a fixed initial review after the Claim-evidence projection defect.

The old packet, provider answers, defer and normal correction budget stay intact.
This is one continuation inside the original job, never a fresh proposal root.
"""
from pathlib import Path
import json
import subprocess

from backend.api.canonical_repository.viewpoint_batch_resolution import (
    ArgumentRouteProposalV2Response, ArgumentRouteReviewV2Response,
    canonicalize_proposal, canonicalize_review,
    route_evidence_is_usable,
)
from backend.api.canonical_repository.viewpoint_resolution import ReviewEvidence
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_resolution_runtime import read_saved_model_response, write_immutable
from backend.pipeline.viewpoint_route_resolution import (
    CLAIM_EVIDENCE_BINDING_CONTRACT, CLAIM_EVIDENCE_BINDING_NOTE,
)

REPAIR_DIRECTORY = "claim-evidence-input-repair-v1"


def prepare_evidence_input_recovery(parent, packet, materials, *, proposer, reviewer,
                                    max_correction_rounds, reserve=True):
    parent = Path(parent).resolve()
    def read(name):
        return json.loads((parent / name).read_text())
    old = read("route-packet.json")
    old_material = read("cvp-route-materials.json")
    if (old_material != materials
            or old.get("packet_sha256") != sha256_json({k:v for k,v in old.items() if k != "packet_sha256"})
            or packet != {**old, "evidence_binding_contract": CLAIM_EVIDENCE_BINDING_CONTRACT,
                          "evidence_binding_note": CLAIM_EVIDENCE_BINDING_NOTE,
                          "packet_sha256": packet["packet_sha256"]}
            or packet["packet_sha256"] != sha256_json({k:v for k,v in packet.items() if k != "packet_sha256"})
            or "evidence_binding_contract" in old):
        raise ValueError("Route input recovery must change only the exact evidence binding contract")
    if ((parent / "route-change-plan.json").exists()
            or (parent / "route-cvp-run.json").exists()
            or any(parent.glob("*reconsideration*"))
            or any(parent.glob("*proposal-validation*"))
            or any(p.name not in {"raw-route-proposal-000.call-intent.json", "raw-route-review-000.call-intent.json"}
                   for p in parent.glob("raw-*.call-intent.json"))
            or any(p.name != "raw-route-review-000.json" for p in parent.glob("raw-route-review-[0-9][0-9][0-9].json"))):
        raise ValueError("Route input recovery requires the initial uncommitted review and original budget")
    proposal_doc, review_doc = read("route-proposal-000.json"), read("route-review-000.json")
    raw_proposal = read_saved_model_response(proposer, old, parent / "raw-route-proposal-000.json")
    proposal = ArgumentRouteProposalV2Response.model_validate(canonicalize_proposal(raw_proposal)[0]).model_dump(mode="json")
    review_packet = read("route-review-packet-000.json")
    if (review_packet.get("route_evidence_context") != old
            or review_packet.get("route_proposal_sha256") != sha256_json(proposal)):
        raise ValueError("Route input recovery original review context drift")
    raw_review = read_saved_model_response(reviewer, review_packet, parent / "raw-route-review-000.json")
    review = ArgumentRouteReviewV2Response.model_validate(canonicalize_review(raw_review)[0]).model_dump(mode="json")
    if (proposal_doc["proposal"] != proposal or proposal_doc["proposal_sha256"] != sha256_json(proposal)
            or review_doc["review"] != review or review_doc["review_sha256"] != sha256_json(review)
            or review["route_proposal_sha256"] != sha256_json(proposal)
            or review["route_evidence_packet_sha256"] != old["packet_sha256"]
            or review["cross_source_composition_found"] or review["cvp_re_review_exceptions"]):
        raise ValueError("Route input recovery original answers or scope drift")
    deferred = [v for v in review["change_reviews"] if v["decision"] == "defer"]
    if (not deferred or any(v["decision"] not in {"pass", "correct", "defer"}
                           for v in review["change_reviews"])
            or any(v["target_kind"] != "member_source"
                   or v["finding_codes"] != ["MEMBER_SOURCE_COMPONENT_COVERAGE_GAP"] for v in deferred)):
        raise ValueError("Route input recovery cannot reopen a semantic rejection or unrelated defer")
    claims = {c["claim_id"]:c for c in packet["claims"]}
    sources, proof = [], []
    for verdict in deferred:
        revision, source_id = verdict["target_key"].split("::", 1)
        added = []
        for component in packet["claim_components"]:
            if (component["source_id"] != source_id or component["disposition"] != "member_existing"
                    or component["target_viewpoint_revision_id"] != revision
                    or not component["viewpoint_claim_link_id"]):
                continue
            claim = claims[component["claim_id"]]
            pairs = {(e["evidence_step_id"],e["source_fragment_id"])
                     for e in claim["evidence"] if route_evidence_is_usable(ReviewEvidence.model_validate(e))}
            selected = {p for p in pairs if p[0] in component["evidence_step_ids"]
                        and p[1] in component["source_fragment_ids"]}
            if selected and pairs - selected:
                added.append({"claim_component_key":component["claim_component_key"],
                              "claim_id":claim["claim_id"],
                              "intermediate_pairs_previously_excluded":[list(p) for p in sorted(pairs-selected)],
                              "conclusion_pairs_unchanged":[list(p) for p in sorted(selected)]})
        if not added:
            raise ValueError("Route input recovery has no proven Claim-evidence projection defect")
        sources.append(source_id)
        proof.append({"original_defer":verdict,"evidence_boundary_delta":added})
    reservation = parent / "evidence-input-recovery-reservation.json"
    saved = json.loads(reservation.read_text()) if reservation.exists() else None
    original_paths = ([parent/name for name in saved["original_files"]] if saved else
                      list(parent.glob("*.json")))
    original_files = {p.name:sha256_json(json.loads(p.read_text())) for p in original_paths}
    receipt = {"schema_version":"wang_route_claim_evidence_input_recovery_v1",
        "parent_job_root":str(parent), "original_files":original_files,
        "original_packet_sha256":old["packet_sha256"], "corrected_packet_sha256":packet["packet_sha256"],
        "material_sha256":materials["artifact_sha256"], "defect":"cvp_link_selection_used_as_intermediate_evidence_ceiling",
        "proof":proof, "authorization_quote":"那你继续修", "human_content_approval":False,
        "original_correction_limit":max_correction_rounds, "previous_corrections_consumed":0,
        "recovery_correction_consumes_original_budget":True, "new_initial_proposal_calls":0,
        "automatic_retry_unknown_calls":False,
        "fixed_code_commit":subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[2], text=True).strip(),
        "implementation_sha256s":{name:sha256_json((Path(__file__).parent/name).read_text()) for name in (
            "viewpoint_route_evidence_recovery.py", "viewpoint_route_cvp_resolution.py",
            "viewpoint_route_resolution.py", "viewpoint_route_cvp_worker.py",
            "../api/canonical_repository/viewpoint_batch_resolution.py")}}
    receipt["artifact_sha256"] = sha256_json(receipt)
    if saved is not None:
        code_fields = {"fixed_code_commit", "implementation_sha256s", "artifact_sha256"}
        if (saved.get("artifact_sha256") != sha256_json({k:v for k,v in saved.items() if k != "artifact_sha256"})
                or {k:v for k,v in saved.items() if k not in code_fields}
                != {k:v for k,v in receipt.items() if k not in code_fields}):
            raise ValueError("Route input recovery fixed parent, proof or original budget changed")
        if receipt != saved:
            project = Path(__file__).resolve().parents[2]
            for name, digest in saved["implementation_sha256s"].items():
                relative = (Path(__file__).parent/name).resolve().relative_to(project)
                text = subprocess.check_output(["git", "show", f"{saved['fixed_code_commit']}:{relative}"],
                                               cwd=project, text=True)
                if sha256_json(text) != digest:
                    raise ValueError("Route input recovery original implementation cannot be authenticated")
            continuation = {"original_reservation_sha256":saved["artifact_sha256"],
                "current_code_commit":receipt["fixed_code_commit"],
                "current_implementation_sha256s":receipt["implementation_sha256s"],
                "original_inputs_answers_verdicts_budget_unchanged":True,
                "new_initial_proposal_calls":0}
            if reserve:
                write_immutable(parent / f"evidence-input-program-continuation-{sha256_json(continuation)}.json",
                                continuation)
        receipt = saved
    if reserve:
        write_immutable(reservation, receipt)
    return {"receipt":receipt, "proposal":proposal, "proposal_sha256":sha256_json(proposal),
            "review":review, "review_sha256":sha256_json(review), "deferred_source_ids":sorted(sources)}
