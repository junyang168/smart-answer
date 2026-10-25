"""One real diagnostic per client/login identity, not per group or restart."""
import hashlib
import json
from pathlib import Path

from backend.pipeline.codex_subscription_client import CodexSubscriptionError
from backend.pipeline.viewpoint_resolution_runtime import write_immutable


def runtime_identity(client):
    client._verify_chatgpt_login()  # local version + subscription-login checks
    auth = Path(client.environment.get("CODEX_HOME", str(Path.home() / '.codex'))) / 'auth.json'
    # Hash only; no credentials or personal identifiers are printed or saved.
    # A login/credential change deliberately invalidates the saved diagnostic.
    with auth.open('rb') as stream:
        auth_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    identity = {"cli": client.cli_identity, "model": client.model,
                "backend": client.backend, "reasoning_effort": client.reasoning_effort,
                "login_state_sha256": auth_sha}
    return identity


def consume_saved_runtime(client, result):
    """Fresh local identity and saved inference proof; NEVER a diagnostic call."""
    identity = runtime_identity(client)
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    path = Path(result.get("proof_path", ""))
    if result.get("identity") != identity or path.name != key+'.json' or not path.is_absolute():
        raise CodexSubscriptionError("Startup saved runtime identity drift; no inference fallback")
    proof = json.loads(path.read_text())
    expected = {"status": "verified", "backend": client.backend, "model": client.model,
                "reasoning_effort": client.reasoning_effort, "cli": client.cli_identity,
                "model_calls": 1, "corpus_writes": 0}
    if proof.get('identity') != identity or proof.get('result') != expected or proof.get('diagnostic_response') != {"ok": True}:
        raise CodexSubscriptionError("Startup saved diagnostic proof drift; no inference fallback")
    return {"identity":identity,"proof_path":str(path),"reused":True,
            "new_model_calls":0,"corpus_writes":0}


def verify_runtime(client, cache_root):
    identity = runtime_identity(client)
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
    intent = cache_root / (key + '.call-intent.json')
    if intent.exists():
        raise CodexSubscriptionError('Prior runtime diagnostic has no saved success; no automatic retry')
    write_immutable(intent, {'identity':identity, 'automatic_retry':False}, require_new=True)
    try:
        result = client.verify_model_access()
    except BaseException as exc:
        write_immutable(cache_root / (key + '.call-failure.json'),
                        {'identity':identity, 'outcome':'unknown_or_rejected',
                         'automatic_retry':False, 'error':str(exc)})
        raise
    write_immutable(path, {"identity": identity, "result": result,
                         "diagnostic_response": {"ok": True}, "raw_response": client.last_raw_response})
    return {"identity": identity, "proof_path": str(path), "reused": False,
            "new_model_calls": 1, "corpus_writes": 0}
