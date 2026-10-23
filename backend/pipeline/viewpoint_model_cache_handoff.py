"""Reuse intact initial model results across a newly frozen batch identity.

Only batch-envelope IDs and their dependent hashes may change, together with
eligibility pins whose complete review proofs differ solely by a physically
verified sealed-file directory migration. Reconstructed old requests must match
the original model request SHA exactly; content drift fails closed.
"""
import copy
import hashlib
import json
from pathlib import Path

from backend.api.canonical_repository.viewpoint_foundation import sha256_json

ROLES = {"raw-proposal.json", "raw-consolidation.json", "raw-review.json"}
CORRECTION_ROLE = "raw-reconsideration.json"
FORMAT_ROLE = "raw-review-format-repair.json"


def bound_read(binding):
    path = Path(binding["path"])
    if hashlib.sha256(path.read_bytes()).hexdigest() != binding["file_sha256"]:
        raise ValueError("model cache handoff physical SHA drift")
    return json.loads(path.read_text())


def artifact_read(binding):
    value = bound_read(binding)
    if value.get("artifact_sha256") != sha256_json({k: v for k, v in value.items() if k != "artifact_sha256"}):
        raise ValueError("proof migration artifact SHA mismatch")
    return value


def eligibility_migrations(source_binding, target_binding):
    """Prove equal review authority; only a sealed-file path may migrate.

    Never normalize arbitrary paths or drop approval/status/source fields.
    Both physical sealed files and both complete bridge rows must agree.
    """
    source, target = artifact_read(source_binding), artifact_read(target_binding)
    old_rows, new_rows = source["attestations"], target["attestations"]
    if len(old_rows) != len(new_rows):
        raise ValueError("proof migration attestation population drift")
    old_bridges = artifact_read(source["bridge_manifest_binding"])
    new_bridges = artifact_read(target["bridge_manifest_binding"])
    def index(rows, key):
        result = {r[key]: r for r in rows}
        if len(result) != len(rows):
            raise ValueError("proof migration duplicate identity")
        return result
    old_proofs = index(old_bridges["proofs"], "artifact_sha256")
    new_proofs = index(new_bridges["proofs"], "artifact_sha256")
    index(old_rows, "claim_id")
    index(new_rows, "claim_id")
    migrations = []
    for old, new in zip(old_rows, new_rows):
        if old == new:
            continue
        for row in (old, new):
            if row.get("attestation_sha256") != sha256_json({k: v for k, v in row.items() if k != "attestation_sha256"}):
                raise ValueError("proof migration attestation SHA mismatch")
        a = old_proofs[old["historical_review_bridge_sha256"]]
        b = new_proofs[new["historical_review_bridge_sha256"]]
        for proof in (a, b):
            if proof.get("artifact_sha256") != sha256_json({k: v for k, v in proof.items() if k != "artifact_sha256"}):
                raise ValueError("proof migration bridge SHA mismatch")
            if proof.get("status") != "verified" or proof.get("claim_id") != old["claim_id"]:
                raise ValueError("proof migration unverified/wrong Claim bridge")
            for key in ("review_binding", "review_input_binding"):
                bound_read(proof[key])
        # Read both files independently. Equal stored hash alone is not proof.
        bound_read(a["sealed_lineage_binding"])
        bound_read(b["sealed_lineage_binding"])
        if a["sealed_lineage_binding"]["file_sha256"] != b["sealed_lineage_binding"]["file_sha256"]:
            raise ValueError("proof migration sealed lineage content drift")
        normalized = copy.deepcopy(b)
        normalized["sealed_lineage_binding"]["path"] = a["sealed_lineage_binding"]["path"]
        normalized["artifact_sha256"] = a["artifact_sha256"]
        if normalized != a:
            raise ValueError("proof migration review authority drift")
        normalized_row = dict(new, historical_review_bridge_sha256=old["historical_review_bridge_sha256"],
                              attestation_sha256=old["attestation_sha256"])
        if normalized_row != old:
            raise ValueError("proof migration Claim/source/revision drift")
        migrations.append({"claim_id": old["claim_id"], "old": old["attestation_sha256"],
                           "new": new["attestation_sha256"]})
    return migrations


def old_request(payload, old_batch_id, migrations=()):
    for key in ("proposal", "review", "invalid_review"):
        if key in payload and payload.get(key + "_sha256") != sha256_json(payload[key]):
            raise ValueError("cache handoff current input hash drift: " + key)
    result = copy.deepcopy(payload)
    result["batch_id"] = old_batch_id
    claims = result.get("packet", result).get("claims", [])
    by_claim = {m["claim_id"]: m for m in migrations}
    for claim in claims if by_claim else []:
        migration = by_claim.get(claim["claim_id"])
        if migration is None:
            continue
        for item in [claim, *claim["evidence"]]:
            if item.get("source_eligibility_attestation_sha256") != migration["new"]:
                raise ValueError("proof migration request pin drift")
            item["source_eligibility_attestation_sha256"] = migration["old"]
    if "packet" in result:
        result["packet"]["batch_id"] = old_batch_id
        result["packet"]["packet_sha256"] = sha256_json({
            k: v for k, v in result["packet"].items() if k != "packet_sha256"})
    if "proposal" in result:
        result["proposal"]["batch_id"] = old_batch_id
        result["proposal_sha256"] = sha256_json(result["proposal"])
    if "review" in result:
        result["review"]["proposal_sha256"] = result["proposal_sha256"]
        result["review_sha256"] = sha256_json(result["review"])
    if 'invalid_review' in result:
        result['invalid_review']['proposal_sha256'] = result['proposal_sha256']
        result['invalid_review_sha256'] = sha256_json(result['invalid_review'])
    if "packet_sha256" in result:
        result["packet_sha256"] = sha256_json({k: v for k, v in result.items() if k != "packet_sha256"})
    return result


def reuse_response(adapter, payload, cache):
    handoff_path = cache.parent / "prior-model-cache.json"
    if not handoff_path.exists() or cache.name not in ROLES | {CORRECTION_ROLE, FORMAT_ROLE}:
        return None
    handoff = json.loads(handoff_path.read_text())
    if handoff.get("schema_version") != "wang_cvp_model_cache_handoff_v1" or handoff.get("artifact_sha256") != sha256_json({k: v for k, v in handoff.items() if k != "artifact_sha256"}):
        raise ValueError("invalid model cache handoff")
    bound_read(handoff["target_freeze"])
    if cache.name in {CORRECTION_ROLE, FORMAT_ROLE} and cache.name not in handoff["raw_bindings"]:
        return None
    if payload.get("batch_id") != handoff["target_batch_id"]:
        raise ValueError("cache handoff target batch mismatch")
    if (cache.name == "raw-review.json" and handoff.get("initial_prefix_only") is True
            and cache.name not in handoff["raw_bindings"]):
        return None  # First review has genuinely never run; not a drift fallback.
    raw_binding = handoff["raw_bindings"][cache.name]
    raw = bound_read(raw_binding)
    if raw.get("artifact_sha256") != sha256_json({k: v for k, v in raw.items() if k != "artifact_sha256"}) or raw.get("response_sha256") != sha256_json(raw["response"]):
        raise ValueError("old raw model cache SHA mismatch")
    formatting_migration = False
    for key in ("model_id", "backend", "prompt_sha256", "generation_config_sha256"):
        if raw.get(key) != getattr(adapter, key):
            if (key == "generation_config_sha256"
                    and handoff.get("allow_lossless_compact_config_migration") is True
                    and getattr(adapter, "_compact_payload", False)
                    and sha256_json({"base_config_sha256": raw[key],
                                     "payload_serialization": "compact_json_v1"}) == getattr(adapter, key)):
                formatting_migration = True
                continue
            raise ValueError("old model cache generation config drift: " + key)
    migrations = eligibility_migrations(**handoff["eligibility_bindings"]) if handoff.get("eligibility_bindings") else []
    original_request = old_request(payload, handoff["source_batch_id"], migrations)
    if sha256_json(original_request) != raw["request_payload_sha256"]:
        raise ValueError("cache handoff semantic request drift; no model fallback")
    response = copy.deepcopy(raw["response"])
    changed = []
    if "batch_id" in response:
        if response["batch_id"] != handoff["source_batch_id"]:
            raise ValueError("source response batch identity drift")
        response["batch_id"] = handoff["target_batch_id"]
        changed.append("batch_id")
    if cache.name in {"raw-review.json", FORMAT_ROLE}:
        if response["proposal_sha256"] != original_request["proposal_sha256"]:
            raise ValueError("source review proposal binding drift")
        response["proposal_sha256"] = payload["proposal_sha256"]
        changed.append("proposal_sha256")
    if cache.name == CORRECTION_ROLE:
        for key in ("proposal_sha256", "review_sha256"):
            if response[key] != original_request[key]:
                raise ValueError("source correction input binding drift: " + key)
            response[key] = payload[key]
            changed.append(key)
    return response, {"source_raw_binding": raw_binding,
        "source_artifact_sha256": raw["artifact_sha256"],
        "source_request_payload_sha256": raw["request_payload_sha256"],
        "handoff_file_sha256": hashlib.sha256(handoff_path.read_bytes()).hexdigest(),
        "verified_eligibility_migrations": migrations,
        "verified_lossless_compact_config_migration": formatting_migration,
        "changed_metadata_paths": changed, "semantic_content_changed": False,
        "new_model_calls": 0}


def prepare_handoff(source_batch, target_batch, *, target_batch_id, target_freeze, source_eligibility=None, target_eligibility=None, include_correction=False, initial_prefix_only=False, allow_lossless_compact_config_migration=False):
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    def binding(p):
        return {"path": str(p.resolve()), "file_sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
    packet = json.loads((source_batch / "batch-packet.json").read_text())
    if initial_prefix_only and (include_correction or (source_batch / "raw-review.json").exists()):
        raise ValueError("initial-only prefix cannot discard a saved review/correction")
    roles = ROLES - {"raw-review.json"} if initial_prefix_only else ROLES
    if (source_batch / FORMAT_ROLE).exists():
        if initial_prefix_only:
            raise ValueError('initial-only prefix cannot discard saved format repair')
        roles = roles | {FORMAT_ROLE}
    body = {"schema_version": "wang_cvp_model_cache_handoff_v1",
        "source_batch_id": packet["batch_id"], "target_batch_id": target_batch_id,
        "target_freeze": binding(target_freeze),
        "raw_bindings": {name: binding(source_batch / name) for name in sorted(roles | ({CORRECTION_ROLE} if include_correction else set()))}}
    if initial_prefix_only:
        body["initial_prefix_only"] = True
    if allow_lossless_compact_config_migration:
        body["allow_lossless_compact_config_migration"] = True
    if source_eligibility is not None or target_eligibility is not None:
        if source_eligibility is None or target_eligibility is None:
            raise ValueError("both eligibility proofs required")
        bindings = {"source_binding": binding(source_eligibility), "target_binding": binding(target_eligibility)}
        eligibility_migrations(**bindings)
        body["eligibility_bindings"] = bindings
    write_immutable(target_batch / "prior-model-cache.json", body | {"artifact_sha256": sha256_json(body)})


def verify_prefix(scope, batch_id, output_dir, max_request_bytes, *, cvp_policy=None):
    """Exercise fresh scoped inputs through all saved stages; never call a model.

    Stop at correction, or final review if a saved correction is bound. These envelopes are
    normal SHA-bound caches the production runner will independently recheck.
    """
    from backend.pipeline.viewpoint_batch_resolution_runner import (
        run_batch, build_proposer, build_reviewer, build_consolidator, build_reconsiderer)
    from backend.api.canonical_repository.viewpoint_resolution import ReviewClaim
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    from backend.pipeline.viewpoint_cvp_policy import load_cvp_policy
    # This helper verifies frozen #357 prefix migrations. A changed default
    # must not reinterpret old raw/model/schema/codec identities as new ones.
    policy = cvp_policy if cvp_policy is not None else load_cvp_policy(
        Path(__file__).parent / 'policies/wang_cvp_resolution_policy_v3.json')
    proposer = build_proposer("gpt-6.1-sol", "high", provider="codex", cvp_policy=policy)
    reviewer = build_reviewer("claude-opus-5-5", "high", cvp_policy=policy)
    consolidator = build_consolidator("claude-opus-5-5", "high", cvp_policy=policy)
    correction = build_reconsiderer("gpt-6.1-sol", "high", provider="codex", cvp_policy=policy)
    final_review = reviewer.compact_copy()
    handoff = json.loads((output_dir / "prior-model-cache.json").read_text())
    has_correction = CORRECTION_ROLE in handoff["raw_bindings"]
    initial_only = handoff.get("initial_prefix_only") is True
    class PrefixVerified(Exception):
        pass
    def forbidden(_):
        raise ValueError("cache prefix would repeat a model stage; no fallback")
    def stop(payload):
        format_repair = payload.get('review_mode') == 'schema_format_repair'
        adapter = reviewer if initial_only or format_repair else final_review if has_correction else correction
        write_immutable(output_dir / "cache-prefix-preflight.json", {
            "all_three_initial_stages_reused": not initial_only, "new_model_calls": 0,
            "initial_proposal_and_consolidation_reused": True,
            "saved_correction_reused": has_correction,
            "master_data_mutations": 0,
            "next_stage": 'review_schema_format_repair' if format_repair else "initial_review" if initial_only else "final_review" if has_correction else "correction",
            "next_request_bytes": adapter.request_bytes(payload),
            **({"format_repair_request_bytes": adapter.request_bytes(payload)} if format_repair
               else {"initial_review_request_bytes": adapter.request_bytes(payload)} if initial_only
               else {"final_review_request_bytes": adapter.request_bytes(payload)} if has_correction
               else {"correction_request_bytes": adapter.request_bytes(payload)}),
            "max_request_bytes": max_request_bytes, "original_findings_preserved": True})
        raise PrefixVerified()
    for adapter in (proposer, reviewer, consolidator, correction, final_review):
        adapter.max_request_bytes = max_request_bytes
        adapter.generate = forbidden
    reviewer.generate = stop  # Only reached for a genuinely uncached review/format repair.
    if initial_only:
        reviewer.generate = stop
    elif has_correction:
        final_review.generate = stop
    else:
        correction.generate = stop
    try:
        run_batch(batch_id=batch_id, scope_label=scope["scope_label"],
            claims=[ReviewClaim.model_validate(c) for c in scope["claims"]],
            registry_context=scope["registry_context"], pending_candidates=[], output_dir=output_dir,
            proposer=proposer, reviewer=reviewer, final_reviewer=final_review,
            consolidator=consolidator, reconsiderer=correction)
    except PrefixVerified:
        return
    raise ValueError("expected mandatory model review at cache prefix boundary")
