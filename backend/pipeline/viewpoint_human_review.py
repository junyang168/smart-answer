"""Immutable human-review handoffs for stopped CVP production.

This module never calls a model, applies a ChangeSet, or changes a review verdict.
An interpretation is not permission to apply a proposed postimage. Executors must
still enforce their source, structural, backup, exact-once and readback gates.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import file_sha256
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    handoff_sha256: str
    decided_by: str = Field(min_length=1)
    authorization_quotes: list[str] = Field(min_length=1)
    item_ids: list[str] = Field(min_length=1)
    action: Literal["interpretation", "accept_exact_postimage", "authorize_revision", "defer"]
    rationale: str = Field(min_length=1)
    # Only an explicit acceptance of the identical submitted packet can use this.
    accepted_packet_sha256: str | None = None


def sealed(body):
    return {**body, "artifact_sha256": sha256_json(body)}


def verify_seal(body, schema):
    if body.get("schema_version") != schema or body.get("artifact_sha256") != sha256_json(
        {k: v for k, v in body.items() if k != "artifact_sha256"}
    ):
        raise ValueError("human review artifact schema/SHA drift")


def binding(path):
    path = Path(path).resolve(strict=True)
    return {"path": str(path), "file_sha256": file_sha256(path)}


def verify_bindings(bindings):
    for row in bindings:
        if binding(row["path"]) != row:
            raise ValueError("human review physical artifact/source drift")


def create_handoff(output, *, stage, packet_path, review_paths, items, source_bindings=(),
                   before_path=None, context_paths=()):
    """Capture exact pending population, raw reviews and physical own sources.

    items contain stable IDs and the *original* findings, not invented pass rows.
    No partial application is authorized by generating a handoff.
    """
    if stage not in {"production_review", "repair_review"}:
        raise ValueError("human review is not an audit/source/schema/quota waiver")
    ids = [row["item_id"] for row in items]
    if not ids or len(ids) != len(set(ids)) or any(not i for i in ids):
        raise ValueError("human handoff missing/duplicate item population")
    if any(not row.get("findings") for row in items) or not review_paths:
        raise ValueError("human handoff requires saved substantive findings/reviews")
    packet = read_artifact(Path(packet_path))
    sources = sorted([dict(row) for row in source_bindings], key=lambda row: row["path"])
    if len({row["path"] for row in sources}) != len(sources):
        raise ValueError("duplicate physical source binding")
    verify_bindings(sources)
    artifacts = [binding(packet_path), *[binding(p) for p in review_paths],
                 *[binding(p) for p in context_paths]]
    baseline = binding(before_path) if before_path is not None else None
    body = sealed({"schema_version": "wang_cvp_human_review_handoff_v1",
        "stage": stage, "packet_sha256": sha256_json(packet), "packet_path": str(Path(packet_path).resolve()),
        "artifacts": artifacts, "source_bindings": sources, "before_binding": baseline,
        "items": sorted(items, key=lambda row: row["item_id"]),
        "allowed_actions": ["interpretation", "accept_exact_postimage", "authorize_revision", "defer"],
        "approval_basis": "pending_human_exception_review", "master_data_mutations": 0,
        "execution_boundary": "Interpretation does not authorize data changes; revised content needs a new exact packet. Original model verdicts remain unchanged. No automatic retry."})
    write_immutable(Path(output), body)
    return body


def validate_handoff(path):
    handoff = read_artifact(Path(path))
    verify_seal(handoff, "wang_cvp_human_review_handoff_v1")
    verify_bindings(handoff["artifacts"] + handoff["source_bindings"])
    if handoff["before_binding"]:
        verify_bindings([handoff["before_binding"]])
    if sha256_json(read_artifact(Path(handoff["packet_path"]))) != handoff["packet_sha256"]:
        raise ValueError("human review packet drift")
    return handoff


def record_decision(handoff_path, decision, output):
    handoff = validate_handoff(handoff_path)
    value = Decision.model_validate(decision).model_dump(mode="json")
    if any(not s.strip() for s in [value["decided_by"], value["rationale"], *value["authorization_quotes"]]):
        raise ValueError("human decision requires explicit actor, quotes and rationale")
    if value["handoff_sha256"] != handoff["artifact_sha256"]:
        raise ValueError("human decision belongs to another handoff")
    ids = value["item_ids"]
    if len(ids) != len(set(ids)) or not set(ids) <= {row["item_id"] for row in handoff["items"]}:
        raise ValueError("human decision foreign/duplicate item population")
    if value["action"] == "accept_exact_postimage":
        if value["accepted_packet_sha256"] != handoff["packet_sha256"]:
            raise ValueError("human acceptance must name the exact postimage packet")
    elif value["accepted_packet_sha256"] is not None:
        raise ValueError("interpretation/revision/defer is not exact postimage acceptance")
    result = sealed({"schema_version": "wang_cvp_human_review_decision_v1", **value,
        "approval_basis": "human_exception_review", "not_a_professor_quote": True,
        "model_verdicts_rewritten": False, "master_data_mutations": 0})
    write_immutable(Path(output), result)
    return result


def resolution_status(handoff_path, decision_paths, *, packet, registry_fingerprint_sha256=None):
    """Read-only authority gate; ready does not mean applied or completed.

    Consumption is idempotent. Any changed postimage/baseline invalidates permission;
    the existing executor, never this function, owns the apply-intent/receipt lock.
    """
    handoff = validate_handoff(handoff_path)
    if sha256_json(packet) != handoff["packet_sha256"]:
        raise ValueError("human resolution cannot approve a changed postimage")
    if handoff["before_binding"]:
        baseline = read_artifact(Path(handoff["before_binding"]["path"]))
        if not registry_fingerprint_sha256 or registry_fingerprint_sha256 != baseline["pre_fingerprint_sha256"]:
            raise ValueError("human resolution live Registry fingerprint missing/drifted")
    rows = {}; interpretations = {}; decisions = []; seen = set()
    for path in decision_paths:
        d = read_artifact(Path(path)); verify_seal(d, "wang_cvp_human_review_decision_v1")
        if d["artifact_sha256"] in seen:
            raise ValueError("duplicate human decision")
        seen.add(d["artifact_sha256"])
        # Revalidate strict action and population without changing the saved artifact.
        fields = {k: d[k] for k in Decision.model_fields}
        Decision.model_validate(fields)
        if any(not s.strip() for s in [d["decided_by"], d["rationale"], *d["authorization_quotes"]]):
            raise ValueError("human resolution empty actor/authorization/rationale")
        if d["approval_basis"] != "human_exception_review" or d["model_verdicts_rewritten"] is not False or d["master_data_mutations"] != 0:
            raise ValueError("human resolution cannot claim model approval or writes")
        if d["handoff_sha256"] != handoff["artifact_sha256"]:
            raise ValueError("foreign human resolution")
        if d["action"] == "accept_exact_postimage" and d["accepted_packet_sha256"] != handoff["packet_sha256"]:
            raise ValueError("human acceptance packet drift")
        if d["action"] != "accept_exact_postimage" and d["accepted_packet_sha256"] is not None:
            raise ValueError("non-acceptance cannot authorize a postimage")
        valid_ids = {i["item_id"] for i in handoff["items"]}
        if len(d["item_ids"]) != len(set(d["item_ids"])) or not set(d["item_ids"]) <= valid_ids:
            raise ValueError("human resolution population drift")
        for item in d["item_ids"]:
            destination = interpretations if d["action"] == "interpretation" else rows
            if item in destination:
                raise ValueError("duplicate/conflicting human decision; explicit new handoff required")
            destination[item] = d["action"]
        decisions.append(binding(path))
    pending = sorted(i["item_id"] for i in handoff["items"] if rows.get(i["item_id"]) != "accept_exact_postimage")
    return {"status": "needs_owner_action" if pending else "human_resolved_pending_execution_gates",
        "approval_basis": "human_exception_review", "pending_item_ids": pending,
        "decisions": decisions, "interpretations_recorded": sorted(interpretations),
        "packet_sha256": handoff["packet_sha256"], "apply_allowed": False,
        "next_step": "explicit bounded action required" if pending else "existing executor must validate backup/dry-run/exact-once/source/readback/audit",
        "model_calls": 0, "master_data_mutations": 0}


def repair_handoff(root, packet, reviews, *, output=None):
    """Shared terminal content-review hook, not a group-specific exception."""
    items = []
    for label, result in reviews.items():
        raw = read_artifact(root / f"raw-{label}.json")
        verify_seal(raw, "wang_canonical_viewpoint_batch_raw_response_v1")
        expected_model, expected_backend = {"gpt": ("gpt-6.1-sol", "codex_subscription"),
                                            "opus": ("claude-opus-5-5", "claude_subscription")}[label]
        if (raw["response"] != result or raw["response_sha256"] != sha256_json(result)
            or raw["request_payload_sha256"] != sha256_json(packet)
            or (raw["model_id"], raw["backend"]) != (expected_model, expected_backend)):
            raise ValueError("saved human handoff review/model/packet drift")
        ids = [r["claim_id"] for r in result["verdicts"]]
        if len(ids) != len(set(ids)) or set(ids) != {t["claim"]["claim_id"] for t in packet["targets"]}:
            raise ValueError("human handoff review population drift")
        for row in result["verdicts"]:
            if not row["approved"] or row["problems"]:
                items.append({"item_id": f"{label}:{row['claim_id']}", "claim_id": row["claim_id"],
                    "reviewer": label, "findings": row["problems"] or [row["reason"]], "reason": row["reason"]})
    sources = {}
    for target in packet["targets"]:
        for source in target["sources"]:
            actual = binding(source["path"])
            if actual["file_sha256"] != source["file_sha256"]:
                raise ValueError("saved repair physical source drift")
            sources[actual["path"]] = actual
    return create_handoff(output or root / "human-review-handoff.json", stage="repair_review",
        packet_path=root / "packet.json", review_paths=[root / f"raw-{label}.json" for label in reviews],
        items=items, source_bindings=list(sources.values()), before_path=root / "before.json")


def execution_authority(handoff_path, decision_paths, *, packet, registry_fingerprint_sha256):
    result = resolution_status(handoff_path, decision_paths, packet=packet,
                               registry_fingerprint_sha256=registry_fingerprint_sha256)
    if result["pending_item_ids"]:
        raise ValueError("human interpretation recorded; exact execution action remains unapproved")
    actors = sorted({read_artifact(Path(p))["decided_by"] for p in decision_paths
                     if read_artifact(Path(p))["action"] == "accept_exact_postimage"})
    return sealed({"schema_version": "wang_cvp_human_execution_authority_v1",
        "handoff_sha256": validate_handoff(handoff_path)["artifact_sha256"],
        "packet_sha256": sha256_json(packet), "decided_by": actors,
        "decision_bindings": result["decisions"], "approval_basis": "human_exception_review",
        "model_consensus": False, "execution_gates_waived": False})


def mark_human_package(package, authority):
    """Use native human approval fields; never label rejected models consensus.

    Called only after the executor validates the exact authority against live
    Registry/source state. The package still faces the unmodified dry-run/apply.
    """
    from copy import deepcopy
    verify_seal(authority, "wang_cvp_human_execution_authority_v1")
    if authority["model_consensus"] is not False or authority["execution_gates_waived"] is not False:
        raise ValueError("human authority cannot fabricate consensus or waive gates")
    result = deepcopy(package)
    # Use the existing strict Registry provenance contract. Human detail lives
    # in the SHA-bound authority artifact, never new fields older readers reject.
    proof = {"review_artifact_sha256": authority["artifact_sha256"]}
    actor = ",".join(authority["decided_by"])
    if not actor:
        raise ValueError("human authority actor missing")
    for row in result.get("viewpoint_identity_decisions", []):
        row.update(reviewer_kind="human_editor", reviewer_id=actor, review_status="human_approved",
                   approval_basis="human_exception_review", review_artifact_sha256=authority["artifact_sha256"],
                   reason="Exact owner exception review; original model disagreements retained. " + row["reason"])
    for row in result.get("viewpoint_revisions", []):
        row.update(review_status="human_approved", approved_by=actor,
                   provenance={**row["provenance"], **proof})
    for row in result.get("viewpoint_claim_links", []):
        row["review_status"] = "human_approved"
    for row in result.get("canonical_viewpoints", []):
        row["review_status"] = "human_approved"
    for row in result.get("viewpoint_relations", []):
        row.update(review_status="human_approved", review_provenance={**row["review_provenance"], **proof})
    if "package_id" in result:
        result["package_id"] += "-HUMAN-" + authority["artifact_sha256"][:20]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--decision-input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = record_decision(args.handoff, read_artifact(args.decision_input), args.output)
    print(result["artifact_sha256"])


if __name__ == "__main__":
    main()
