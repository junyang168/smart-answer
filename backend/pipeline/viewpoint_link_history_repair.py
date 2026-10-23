"""Repair stale Registry pins only across authenticated, meaning-preserving history.

This never edits Claims, adds evidence to a CVP, or reissues editorial approval.
Old link versions remain in object_versions. Coordinate repairs must have their
original applied, SHA-bound plans; evidence additions must already be adjudicated.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Mapping

from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha, sha256_json
from backend.api.canonical_repository.viewpoint_claim_repin import CLAIM_REVIEW_FIELDS
from backend.api.canonical_repository.viewpoint_production_safety import file_sha256, validate_content_addressed


def _substance(row: Mapping[str, Any]) -> dict[str, Any]:
    return {k: deepcopy(v) for k, v in row.items() if k not in CLAIM_REVIEW_FIELDS}


@lru_cache(maxsize=128)
def authenticated_plan(path: str, expected_sha: str) -> dict[str, Any]:
    source = Path(path)
    if not source.is_absolute() or file_sha256(source) != expected_sha:
        raise ValueError("applied repair plan physical SHA mismatch")
    artifact = json.loads(source.read_text())
    validate_content_addressed(artifact, "artifact_sha256")
    materials = [artifact["source"]] if "source" in artifact else artifact["frozen_sources"]
    for material in materials:
        physical = Path(material["source_path"])
        if not physical.is_absolute() or file_sha256(physical) != material["source_file_sha256"]:
            raise ValueError("repair source bytes changed")
    return artifact


def verify_transition(before: Mapping[str, Any], after: Mapping[str, Any], *,
                      collection: str, object_id: str) -> dict[str, Any]:
    """Accept only exact applied repair operations and their bounded differences."""
    left, right = _substance(before["payload"]), _substance(after["payload"])
    kind = after["source_kind"]
    proof: dict[str, Any] = {"change_set_id": after["change_set_id"], "source_kind": kind,
                            "before_sha256": before["content_sha256"],
                            "after_sha256": after["content_sha256"]}
    if kind == "review_decision" and left == right:
        return proof
    if kind in {"wkp368_source_contract_cleanup", "wkp368_source_duplicate_cleanup"}:
        metadata = after["metadata"]
        artifact = authenticated_plan(metadata["source_plan_path"], metadata["source_plan_file_sha256"])
        plan = artifact["plan"]
        if plan["change_set_id"] != after["change_set_id"] or plan["source_sha256"] != after["source_sha256"] or plan["source_kind"] != kind:
            raise ValueError("repair plan does not match applied ChangeSet")
        operations = [op for op in plan["operations"] if (op["collection"], op["object_id"]) == (collection, object_id)]
        if len(operations) != 1:
            raise ValueError("repair operation absent or ambiguous")
        op = operations[0]
        if op["operation"] != "update" or (op["before_sha256"], op["after_sha256"], op["before_revision"], op["after_revision"]) != (before["content_sha256"], after["content_sha256"], before["revision"], after["revision"]):
            raise ValueError("repair operation does not bind exact before/after")
        if record_content_sha(op["payload"]) != after["content_sha256"]:
            raise ValueError("repair operation payload SHA mismatch")
        aliases = {g["alias_source_id"]: g["canonical_source_id"] for g in artifact.get("groups", [])}
        if collection == "claims":
            for a, b in zip(left.get("occurrences", []), right.get("occurrences", [])):
                if a.get("source_id") != b.get("source_id"):
                    if kind != "wkp368_source_duplicate_cleanup" or aliases.get(a.get("source_id")) != b.get("source_id"):
                        raise ValueError("unproved source alias reassignment")
                    a["source_id"] = b["source_id"]
                for occurrence in (a, b):
                    for anchor in occurrence.get("anchors", []):
                        anchor.pop("paragraph_key", None)
        elif collection == "source_fragments":
            if left.get("source_id") != right.get("source_id"):
                if aliases.get(left.get("source_id")) != right.get("source_id"):
                    raise ValueError("unproved fragment source reassignment")
                left["source_id"] = right["source_id"]
            # The applied coordinate migration may change provenance, never quotation/modality.
            for row in (left, right):
                for key in ("paragraph_key", "source_segment_index", "paragraph_text_sha256", "anchors", "source_revision_sha256", "source_sha256", "anchor_state",
                            "anchor_binding_sha256", "locator_space", "source_body_sha256",
                            "source_file_sha256", "source_text_sha256", "source_visual_sha256",
                            "editorial_structure_sha256", "editorial_topology_sha256"):
                    row.pop(key, None)
        if left != right:
            raise ValueError(f"{object_id}: applied repair changed meaning-bearing fields")
        proof["plan_binding"] = {"path": metadata["source_plan_path"], "file_sha256": metadata["source_plan_file_sha256"]}
        return proof
    if kind == "wkp364_claim_evidence_pair_adjudication":
        repair = after["metadata"]["claim_evidence_reciprocity_repair"]
        guard = repair["store_guard"]
        validate_content_addressed(guard, "guard_sha256")
        authorization = guard["expected_pair_adjudication_authorization"]
        validate_content_addressed(authorization, "artifact_sha256")
        identity = guard["plan_identity"]
        if identity["change_set_id"] != after["change_set_id"] or identity.get("source_sha256") != after["source_sha256"] or identity.get("source_kind") != kind or authorization["artifact_sha256"] != repair["pair_adjudication_authorization_sha256"]:
            raise ValueError("pair authorization is not the applied adjudication")
        if collection == "claims":
            old = left.pop("evidence_step_ids")
            new = right.pop("evidence_step_ids")
            added = set(new) - set(old)
            if not added or len(new) != len(set(new)) or [x for x in new if x in set(old)] != old:
                raise ValueError("evidence deleted/reordered or not strictly additive")
            matches = [a for a in authorization["actions"] if a.get("claim_id") == object_id and a.get("evidence_step_id") in added]
            if len(matches) != len(added) or {a["evidence_step_id"] for a in matches} != added or any(a.get("effective_decision") != "include" or a.get("reviewed_decision") != "include" or a.get("decision_basis") != "independent_model_consensus_include" or a.get("claim_content_sha256") != before["content_sha256"] for a in matches):
                raise ValueError("new evidence lacks exact independent include decisions")
            proof["authorized_added_evidence_ids"] = sorted(added)
            proof["pair_authorization_sha256"] = authorization["artifact_sha256"]
        elif collection == "evidence_steps":
            # Reverse Claim index changes do not change the evidence argument.
            old_ids, new_ids = set(left.pop("produced_claim_ids", [])), set(right.pop("produced_claim_ids", []))
            for cid in old_ids ^ new_ids:
                actions = [a for a in authorization["actions"] if a.get("claim_id") == cid and a.get("evidence_step_id") == object_id]
                expected = "include" if cid in new_ids else "exclude"
                if len(actions) != 1 or actions[0].get("effective_decision") != expected or actions[0].get("evidence_content_sha256") != before["content_sha256"]:
                    raise ValueError("reverse index change lacks applied authorization")
        else:
            raise ValueError("pair repair cannot change this collection")
        if left != right:
            raise ValueError("adjudication changed non-index content")
        return proof
    raise ValueError(f"unsupported applied transition: {kind}/{collection}/{object_id}")


def verify_chain(rows: list[dict[str, Any]], *, collection: str, object_id: str,
                 current: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not rows or rows[-1]["payload"] != dict(current):
        raise ValueError("history does not reach current generation")
    revisions = [r["revision"] for r in rows]
    if revisions != list(range(revisions[0], revisions[-1] + 1)):
        raise ValueError("discontinuous version chain")
    for row in rows:
        if row["status"] != "applied" or row["revision"] != row["payload"]["revision"] or record_content_sha(row["payload"]) != row["content_sha256"]:
            raise ValueError("unauthenticated version chain")
    return [verify_transition(a, b, collection=collection, object_id=object_id) for a, b in zip(rows, rows[1:])]


def repair_link(link: Mapping[str, Any], *, claim: Mapping[str, Any],
                history: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    if history[0]["revision"] != link["pinned_claim_revision"]:
        raise ValueError("history does not start at the pinned Claim")
    proofs = verify_chain(history, collection="claims", object_id=link["claim_id"], current=claim)
    pinned = history[0]["payload"]
    component = link.get("component_locator")
    if component and component["claim_sha256"] != semantic_record_sha(pinned):
        raise ValueError("old component does not bind its pinned Claim")
    if pinned["statement"] != claim["statement"]:
        raise ValueError("Claim words changed")
    # occurrence_refs are opaque historical packet hashes, not Claim occurrence
    # IDs. Keep them stable: existing route attestations also refer to these IDs.
    if not {b["evidence_step_id"] for b in link.get("evidence_bindings", [])} <= set(pinned["evidence_step_ids"]) & set(claim["evidence_step_ids"]):
        raise ValueError("old CVP evidence binding removed or newly introduced")
    updated = deepcopy(dict(link))
    updated["revision"] += 1
    updated["pinned_claim_revision"] = claim["revision"]
    if component:
        updated["component_locator"]["claim_sha256"] = semantic_record_sha(claim)
    return updated, {"link_id": link["viewpoint_claim_link_id"], "claim_id": link["claim_id"],
                     "before_link_sha256": record_content_sha(link), "after_link_sha256": record_content_sha(updated),
                     "claim_transitions": proofs, "new_evidence_added_to_cvp": False}
