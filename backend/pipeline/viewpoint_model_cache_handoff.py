"""Reuse intact initial model results across a newly frozen batch identity.

Only batch-envelope IDs and their dependent hashes may change. Reconstructed
old requests must match the original model request SHA exactly; content drift
fails closed, without silently spending another model call.
"""
import copy
import hashlib
import json
from pathlib import Path

from backend.api.canonical_repository.viewpoint_foundation import sha256_json

ROLES = {"raw-proposal.json", "raw-consolidation.json", "raw-review.json"}


def bound_read(binding):
    path = Path(binding["path"])
    if hashlib.sha256(path.read_bytes()).hexdigest() != binding["file_sha256"]:
        raise ValueError("model cache handoff physical SHA drift")
    return json.loads(path.read_text())


def old_request(payload, old_batch_id):
    result = copy.deepcopy(payload)
    result["batch_id"] = old_batch_id
    if "packet" in result:
        result["packet"]["batch_id"] = old_batch_id
        result["packet"]["packet_sha256"] = sha256_json({
            k: v for k, v in result["packet"].items() if k != "packet_sha256"})
    if "proposal" in result:
        result["proposal"]["batch_id"] = old_batch_id
        result["proposal_sha256"] = sha256_json(result["proposal"])
    if "packet_sha256" in result:
        result["packet_sha256"] = sha256_json({k: v for k, v in result.items() if k != "packet_sha256"})
    return result


def reuse_response(adapter, payload, cache):
    handoff_path = cache.parent / "prior-model-cache.json"
    if not handoff_path.exists() or cache.name not in ROLES:
        return None
    handoff = json.loads(handoff_path.read_text())
    if handoff.get("schema_version") != "wang_cvp_model_cache_handoff_v1" or handoff.get("artifact_sha256") != sha256_json({k: v for k, v in handoff.items() if k != "artifact_sha256"}):
        raise ValueError("invalid model cache handoff")
    bound_read(handoff["target_freeze"])
    if payload.get("batch_id") != handoff["target_batch_id"]:
        raise ValueError("cache handoff target batch mismatch")
    raw_binding = handoff["raw_bindings"][cache.name]
    raw = bound_read(raw_binding)
    if raw.get("artifact_sha256") != sha256_json({k: v for k, v in raw.items() if k != "artifact_sha256"}) or raw.get("response_sha256") != sha256_json(raw["response"]):
        raise ValueError("old raw model cache SHA mismatch")
    for key in ("model_id", "backend", "prompt_sha256", "generation_config_sha256"):
        if raw.get(key) != getattr(adapter, key):
            raise ValueError("old model cache generation config drift: " + key)
    original_request = old_request(payload, handoff["source_batch_id"])
    if sha256_json(original_request) != raw["request_payload_sha256"]:
        raise ValueError("cache handoff semantic request drift; no model fallback")
    response = copy.deepcopy(raw["response"])
    changed = []
    if "batch_id" in response:
        if response["batch_id"] != handoff["source_batch_id"]:
            raise ValueError("source response batch identity drift")
        response["batch_id"] = handoff["target_batch_id"]
        changed.append("batch_id")
    if cache.name == "raw-review.json":
        if response["proposal_sha256"] != original_request["proposal_sha256"]:
            raise ValueError("source review proposal binding drift")
        response["proposal_sha256"] = payload["proposal_sha256"]
        changed.append("proposal_sha256")
    return response, {"source_raw_binding": raw_binding,
        "source_artifact_sha256": raw["artifact_sha256"],
        "source_request_payload_sha256": raw["request_payload_sha256"],
        "handoff_file_sha256": hashlib.sha256(handoff_path.read_bytes()).hexdigest(),
        "changed_metadata_paths": changed, "semantic_content_changed": False,
        "new_model_calls": 0}


def prepare_handoff(source_batch, target_batch, *, target_batch_id, target_freeze):
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    def binding(p):
        return {"path": str(p.resolve()), "file_sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
    packet = json.loads((source_batch / "batch-packet.json").read_text())
    body = {"schema_version": "wang_cvp_model_cache_handoff_v1",
        "source_batch_id": packet["batch_id"], "target_batch_id": target_batch_id,
        "target_freeze": binding(target_freeze),
        "raw_bindings": {name: binding(source_batch / name) for name in sorted(ROLES)}}
    write_immutable(target_batch / "prior-model-cache.json", body | {"artifact_sha256": sha256_json(body)})
