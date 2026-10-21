"""Explicit once-only owner handoff; never erase an uncertain invocation.

This adapter is for the initial proposal of an otherwise unchanged normal
CVP batch. The ordinary call runtime still rejects every unanswered intent.
"""
from pathlib import Path

from backend.pipeline.source_reconciliation_runner import binding, body, read, write
from backend.pipeline import source_reconciliation as c
from backend.pipeline.viewpoint_resolution_runtime import model_request_identity

NAME = "owner-initial-proposal-reissue.json"
FILES = ("batch-packet.json", "raw-proposal.call-intent.json",
         "raw-proposal.call-failure.json", "raw-proposal.request-encoding.json")


def original(root, adapter):
    old = root / "review"
    if {p.name for p in old.iterdir()} != set(FILES):
        raise ValueError("reissue requires only the original failed initial proposal artifacts")
    intent = read(old / FILES[1]); failure = read(old / FILES[2])
    encoding = read(old / FILES[3])
    # batch-packet uses its own packet seal, not artifact_sha256.
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    packet = read_artifact(old / FILES[0])
    if packet.get("packet_sha256") != c.sha256_json({k:v for k,v in packet.items() if k != "packet_sha256"}):
        raise ValueError("original batch packet seal drift")
    identity = model_request_identity(adapter, packet)
    if (identity["model_id"] != "gpt-6.1-sol" or identity["backend"] != "codex_subscription"
            or any(intent.get(k) != v for k,v in identity.items())
            or intent.get("cache_path") != str((old / "raw-proposal.json").resolve())
            or intent.get("automatic_retry") is not False
            or failure.get("invocation_sha256") != intent["artifact_sha256"]
            or failure.get("execution_status") != "outcome_unknown"
            or failure.get("automatic_retry") is not False
            or body(encoding) != {"schema_version":"wang_model_request_encoding_proof_v1",
                                 **adapter.request_encoding_proof(packet)}):
        raise ValueError("original model/prompt/config/request/encoding/failure identity drift")
    return intent, identity, [binding(old / n) for n in FILES]


def authorize(root, adapter, quote, expected_intent):
    if not quote.strip() or len(expected_intent) != 64:
        raise ValueError("explicit owner quote and exact old invocation required")
    intent, identity, pins = original(root, adapter)
    if intent["artifact_sha256"] != expected_intent:
        raise ValueError("owner authorization belongs to another invocation")
    value = {"schema_version":"wang_owner_initial_proposal_reissue_v1",
        "authorization_quote":quote, "old_invocation_sha256":expected_intent,
        "request_identity":identity, "old_bindings":pins,
        "input_bindings":[binding(root/n) for n in ("packet.json","before.json","context.json","runtime-proof.json")],
        "review_directory":"review-owner-authorized-once",
        "maximum_reissues":1, "automatic_retry":False,
        "old_outcome_preserved":"outcome_unknown", "content_approval":False}
    # A single fixed marker per original batch, not one per timestamp/root.
    write(root / NAME, value)
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable
    write_immutable(root/value["review_directory"]/"batch-packet.json",
                    read_artifact(root/"review"/"batch-packet.json"))
    return directory(root, adapter)


def directory(root, adapter=None):
    marker = root / NAME
    if not marker.exists(): return root / "review"
    value = body(read(marker))
    if (value.get("schema_version") != "wang_owner_initial_proposal_reissue_v1"
            or not value.get("authorization_quote", "").strip()
            or value.get("maximum_reissues") != 1 or value.get("automatic_retry") is not False
            or value.get("content_approval") is not False
            or value.get("old_outcome_preserved") != "outcome_unknown"
            or value.get("review_directory") != "review-owner-authorized-once"):
        raise ValueError("owner once-reissue boundary drift")
    expected_paths = [root/"review"/n for n in FILES] + [root/n for n in ("packet.json","before.json","context.json","runtime-proof.json")]
    pins = value["old_bindings"] + value["input_bindings"]
    if [p["path"] for p in pins] != [str(p.resolve()) for p in expected_paths]:
        raise ValueError("owner reissue proof population drift")
    for pin in pins: c.verify_file(pin)
    if adapter is not None:
        intent, identity, old_pins = original(root, adapter)
        if (intent["artifact_sha256"] != value["old_invocation_sha256"]
                or identity != value["request_identity"] or old_pins != value["old_bindings"]):
            raise ValueError("owner reissue current request drift")
    target = root / value["review_directory"]
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    # run_batch must reproduce precisely this packet before reserving a call.
    if read_artifact(target / "batch-packet.json") != read_artifact(root/"review"/"batch-packet.json"):
        raise ValueError("authorized batch packet changed")
    return target
