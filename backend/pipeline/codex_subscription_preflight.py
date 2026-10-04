"""One real diagnostic per client/login identity, not per group or restart."""
import hashlib
import json
from pathlib import Path

from backend.pipeline.codex_subscription_client import CodexSubscriptionError
from backend.pipeline.viewpoint_resolution_runtime import write_immutable


def verify_runtime(client, cache_root):
    client._verify_chatgpt_login()  # local version + subscription-login checks
    auth = Path(client.environment.get("CODEX_HOME", str(Path.home() / '.codex'))) / 'auth.json'
    # Hash only; no credentials or personal identifiers are printed or saved.
    # A login/credential change deliberately invalidates the saved diagnostic.
    with auth.open('rb') as stream:
        auth_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    identity = {"cli": client.cli_identity, "model": client.model,
                "backend": client.backend, "reasoning_effort": client.reasoning_effort,
                "login_state_sha256": auth_sha}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    cache_root = Path(cache_root)
    cache_root.mkdir(parents=True, exist_ok=True)
    path = cache_root / (key + '.json')
    if path.exists():
        proof = json.loads(path.read_text())
        expected = {"status": "verified", "backend": client.backend, "model": client.model,
                    "reasoning_effort": client.reasoning_effort, "cli": client.cli_identity,
                    "model_calls": 1, "corpus_writes": 0}
        if proof.get('identity') != identity or proof.get('result') != expected or proof.get('diagnostic_response') != {"ok": True}:
            raise CodexSubscriptionError("Saved runtime diagnostic identity/response mismatch; no fallback")
        return {"identity": identity, "proof_path": str(path), "reused": True,
                "new_model_calls": 0, "corpus_writes": 0}
    result = client.verify_model_access()
    write_immutable(path, {"identity": identity, "result": result,
                         "diagnostic_response": {"ok": True}, "raw_response": client.last_raw_response})
    return {"identity": identity, "proof_path": str(path), "reused": False,
            "new_model_calls": 1, "corpus_writes": 0}
