"""Shared artifact and subscription runtime for CVP and Route resolution."""

from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.viewpoint_foundation import canonical_json, sha256_json
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
# 900s proved too tight for opus reviewing a 20-claim effective proposal at
# high effort (#315: final review timed out with every earlier stage done).
CALL_TIMEOUT_SECONDS = 1800.0
_saved_validation = ContextVar('cvp_saved_artifact_validation', default=False)
_authorized_limit = ContextVar('cvp_authorized_request_limit', default=None)


@contextmanager
def authorized_request_limit(root, campaign, authorization=None):
    """Execution budget only: never changes a model request or its cache identity."""
    from backend.pipeline.cvp_sql_flow import binding, check_binding
    root = Path(root).resolve()
    saved = root/'request-limit-binding.json'
    if authorization is None:
        if saved.exists():
            raise ValueError('saved request limit requires explicit owner authorization')
        yield
        return
    pin = binding(authorization)
    document = read_artifact(authorization)
    if (set(document) != {'schema_version','campaign_sha256','max_request_bytes','authorized_by','owner_direction'}
            or document['schema_version'] != 'cvp_sql_request_limit_authorization.v1'
            or document['campaign_sha256'] != campaign
            or type(document['max_request_bytes']) is not int or document['max_request_bytes'] <= 0
            or document['authorized_by'] != 'owner' or not document['owner_direction']):
        raise ValueError('invalid owner request limit authorization')
    budget = {'authorization':pin, 'campaign_sha256':campaign,
              'max_request_bytes':document['max_request_bytes']}
    write_immutable(saved, budget)
    token = _authorized_limit.set((root, budget))
    try:
        check_binding(pin)
        yield
    finally:
        _authorized_limit.reset(token)


def effective_request_limit(adapter, cache):
    scope = _authorized_limit.get()
    if scope is None:
        return getattr(adapter, 'max_request_bytes', None), None
    root, budget = scope
    if not Path(cache).resolve().is_relative_to(root):
        raise ValueError('model request outside authorized SQL execution root')
    from backend.pipeline.cvp_sql_flow import check_binding
    check_binding(budget['authorization'])
    return budget['max_request_bytes'], budget


def validating_saved_artifacts():
    return _saved_validation.get()


@contextmanager
def saved_artifact_validation():
    """Explicit bounded offline reconstruction; no artifact/model side effects.

    Immutable outputs must already exist and match exactly. Derived runtime
    summaries are not approval evidence and are neither consumed nor rewritten.
    The owning context authenticates every actual input again on exit.
    """
    from backend.pipeline.viewpoint_validation_context import historical_context
    ctx = historical_context()
    if ctx is None or ctx.limits is None or validating_saved_artifacts():
        raise ValueError('saved reconstruction requires its own bounded historical context')
    token = _saved_validation.set(True)
    try:
        yield
    finally:
        _saved_validation.reset(token)


def read_artifact(path: Path) -> dict[str, Any]:
    path = Path(path)
    from backend.pipeline.viewpoint_validation_context import historical_context
    ctx = historical_context()
    if ctx is not None:
        return ctx.read(path)
    return json.loads(path.read_text(encoding="utf-8"))


def write_immutable(path: Path, payload: dict[str, Any], *, require_new: bool = False) -> None:
    """Durably publish without replacing another writer's immutable artifact.

    A reservation uses require_new: even an identical second writer must lose.
    A crash can leave an unreferenced temporary file, never a partial final file.
    """
    path = Path(path)
    if validating_saved_artifacts():
        if require_new or read_artifact(path) != payload:
            raise ValueError(f'saved immutable reconstruction differs: {path}')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix="." + path.name + ".", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                if require_new:
                    raise ValueError(f"once-only artifact already reserved at {path}")
                if read_artifact(path) != payload:
                    raise ValueError(f"immutable artifact differs at {path}")
            else:
                directory = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)


def write_derived(path: Path, payload: dict[str, Any]) -> None:
    if validating_saved_artifacts():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def recorded_model_executions(
    output_dir: Path, *, raw_artifacts: dict[str, str]
) -> dict[str, Any]:
    stages: dict[str, Any] = {}
    for stage, filename in raw_artifacts.items():
        path = output_dir / filename
        if not path.exists():
            intent = path.with_name(path.stem + '.call-intent.json')
            encoding = path.with_name(path.stem + '.request-encoding.json')
            stages[stage] = {"calls_recorded": 0, "wall_seconds_recorded": 0.0,
                "execution_status": "outcome_unknown" if intent.exists() or encoding.exists() else "not_started"}
            continue
        artifact = read_artifact(path)
        body = {key: value for key, value in artifact.items() if key != "artifact_sha256"}
        if artifact.get("artifact_sha256") != sha256_json(body):
            raise ValueError(f"raw response artifact SHA mismatch: {path}")
        stages[stage] = {
            "calls_recorded": int(artifact.get("calls_recorded", 1)),
            "wall_seconds_recorded": round(float(artifact["wall_seconds"]), 3),
            "backend": artifact.get("backend"),
            "model_id": artifact.get("model_id"),
            "artifact_sha256": artifact["artifact_sha256"],
        }
    return {
        "stages": stages,
        "unknown_execution_stages": [name for name, item in stages.items()
                                     if item.get('execution_status') == 'outcome_unknown'],
        "calls_recorded_total": sum(item["calls_recorded"] for item in stages.values()),
        "wall_seconds_recorded_total": round(
            sum(item["wall_seconds_recorded"] for item in stages.values()), 3
        ),
    }


def write_current_state(
    output_dir: Path,
    *,
    schema_version: str,
    identity: dict[str, str],
    status: str,
    authoritative_artifact: str,
    authoritative_artifact_sha256: str,
    superseded_artifacts: list[str] | None = None,
) -> dict[str, Any]:
    state = {
        "schema_version": schema_version,
        **identity,
        "status": status,
        "authoritative_artifact": authoritative_artifact,
        "authoritative_artifact_sha256": authoritative_artifact_sha256,
        "superseded_artifacts": sorted(set(superseded_artifacts or [])),
    }
    state["artifact_sha256"] = sha256_json(state)
    write_derived(output_dir / "current-state.json", state)
    return state


def stable_decided_at(batch_dir: Path) -> str:
    path = batch_dir / "decision-time.json"
    if path.exists():
        payload = read_artifact(path)
        body = {key: value for key, value in payload.items() if key != "artifact_sha256"}
        if payload.get("artifact_sha256") != sha256_json(body):
            raise ValueError(f"decision time artifact SHA mismatch: {path}")
        return str(payload["decided_at"])
    body = {
        "schema_version": "wang_cvp_batch_decision_time_v1",
        "decided_at": datetime.now(timezone.utc).isoformat(),
    }
    write_immutable(path, body | {"artifact_sha256": sha256_json(body)})
    return str(body["decided_at"])


def subscription_client(
    provider: str,
    model: str,
    reasoning_effort: str,
    *,
    timeout_seconds: float = CALL_TIMEOUT_SECONDS,
) -> ClaudeSubscriptionClient | CodexSubscriptionClient:
    client_type = {
        "claude": ClaudeSubscriptionClient,
        "codex": CodexSubscriptionClient,
    }.get(provider)
    if client_type is None:
        raise ValueError(f"unsupported subscription provider: {provider}")
    return client_type(
        model=model,
        reasoning_effort=reasoning_effort,
        timeout_seconds=timeout_seconds,
    )


def model_request_identity(adapter: Any, payload: dict[str, Any]) -> dict[str, str]:
    return {"model_id": adapter.model_id, "backend": adapter.backend,
            "prompt_sha256": adapter.prompt_sha256,
            "generation_config_sha256": adapter.generation_config_sha256,
            "request_payload_sha256": sha256_json(payload)}


def response_adapter(adapter, saved=None):
    """New calls use program pins; saved calls retain their actual wire identity."""
    factory = getattr(adapter, 'program_bound_copy', None)
    if not callable(factory): return adapter
    bound = factory()
    roles_factory = getattr(bound, 'component_roles_copy', None)
    roles = roles_factory() if callable(roles_factory) else bound
    if saved is None: return roles
    keys = ('model_id', 'backend', 'prompt_sha256', 'generation_config_sha256')
    if all(saved.get(k) == getattr(roles, k) for k in keys): return roles
    if all(saved.get(k) == getattr(bound, k) for k in keys): return bound
    for base in (roles,bound,adapter):
        if not getattr(base,'_shared_record_payload',False):continue
        for factory in ('shared_keys_copy','shared_keys_v2_copy'):
            method=getattr(base,factory,None)
            if callable(method):
                candidate=method()
                if all(saved.get(k)==getattr(candidate,k) for k in keys):return candidate
    return adapter  # Exact legacy authentication still runs; never a fallback call.


def bound_model_response(adapter, payload, raw, cache, *, persist=True):
    from backend.api.canonical_repository.viewpoint_response_binding import fields, bind_response, VERSION
    model = getattr(adapter, '_response_model', None)
    if model is None or not any(fields(model)): return raw
    from backend.pipeline.viewpoint_validation_context import historical_context
    # Completed legacy reconstructions keep their exact old interpretation.
    if (historical_context() is not None or validating_saved_artifacts()) and not getattr(adapter, '_program_response_binding', False):
        return raw
    result = bind_response(model, raw, payload)
    artifact = read_artifact(cache)
    proof = {'schema_version':VERSION, 'request_identity':model_request_identity(adapter,payload),
        'raw_artifact_sha256':artifact['artifact_sha256'], 'raw_response_sha256':sha256_json(raw),
        'bound_response_sha256':sha256_json(result), 'bound_response':result,
        'model_raw_preserved':True, 'semantic_decisions_unchanged':True}
    proof['artifact_sha256'] = sha256_json(proof)
    path = cache.with_name(cache.stem+'.program-binding.json')
    if path.exists():
        if read_artifact(path) != proof: raise ValueError('program response binding proof drift')
    elif persist and historical_context() is None and not validating_saved_artifacts():
        write_immutable(path,proof)
    return result


def read_saved_model_response(adapter: Any, payload: dict[str, Any], cache: Path) -> dict[str, Any]:
    """Authenticate a saved response with no writes, phase closure or fallback."""
    artifact = read_artifact(cache)
    adapter = response_adapter(adapter, artifact)
    expected = model_request_identity(adapter, payload)
    if artifact.get("request_payload_sha256") != expected["request_payload_sha256"]:
        raise ValueError(f"cached response belongs to another request payload: {cache}")
    mismatched = [key for key, value in expected.items() if artifact.get(key) != value]
    if mismatched:
        raise ValueError(f"cached response belongs to another generation config ({', '.join(mismatched)}): {cache}")
    response = dict(artifact.get("response") or {})
    if artifact.get("response_sha256") != sha256_json(response):
        raise ValueError(f"cached response SHA mismatch: {cache}")
    body = {key: value for key, value in artifact.items() if key != "artifact_sha256"}
    if artifact.get("artifact_sha256") != sha256_json(body):
        raise ValueError(f"cached response artifact SHA mismatch: {cache}")
    if artifact.get('request_encoding_proof_sha256') is not None:
        proof = adapter.request_encoding_proof(payload)
        encoding_body = {'schema_version': 'wang_model_request_encoding_proof_v1', **proof}
        encoding = read_artifact(cache.with_name(cache.stem + '.request-encoding.json'))
        if (artifact['request_encoding_proof_sha256'] != sha256_json(proof)
                or encoding != {**encoding_body, 'artifact_sha256': sha256_json(encoding_body)}):
            raise ValueError(f"cached request encoding proof drift: {cache}")
    intent_path = cache.with_name(cache.stem + '.call-intent.json')
    from backend.pipeline.viewpoint_validation_context import optional_file_exists
    has_intent = optional_file_exists(intent_path)
    if artifact.get('invocation_sha256') is not None or has_intent:
        intent = read_artifact(intent_path)
        intent_body = {'schema_version': 'wang_model_call_intent_v1', **expected,
                       'cache_path': str(cache.resolve()), 'automatic_retry': False}
        if (intent != {**intent_body, 'artifact_sha256': sha256_json(intent_body)}
                or artifact.get('invocation_sha256') != intent['artifact_sha256']):
            raise ValueError(f"cached model invocation identity drift: {cache}")
    if artifact.get("cache_handoff") is not None:
        from backend.pipeline.viewpoint_model_cache_handoff import reuse_response
        reused = reuse_response(adapter, payload, cache)
        if reused is None or reused[0] != response or reused[1] != artifact["cache_handoff"]:
            raise ValueError(f"cached handoff provenance drift: {cache}")
    return response


def call_model(
    adapter: Any, payload: dict[str, Any], cache: Path
) -> tuple[dict[str, Any], int, float]:
    from backend.pipeline.viewpoint_validation_context import finish_validation_phase, historical_context
    cache = Path(cache)
    saved = cache.exists()
    if validating_saved_artifacts() and not saved:
        raise ValueError(f'saved reconstruction has no response; no invocation/fallback: {cache}')
    if not saved and historical_context() is not None:
        raise ValueError(f"read-only historical validation has no saved response; no model fallback: {cache}")
    adapter = response_adapter(adapter, read_artifact(cache) if saved else None)
    # Only an uninvoked stage may choose a smaller lossless wire encoding.
    # Saved answers and unknown intents retain their exact original identity.
    intent_path = cache.with_name(cache.stem + '.call-intent.json')
    encoding_path = cache.with_name(cache.stem + '.request-encoding.json')
    if not saved and not intent_path.exists() and not encoding_path.exists():
        limit, _ = effective_request_limit(adapter, cache)
        key_factory = getattr(adapter, 'shared_keys_copy', None)
        if (limit is not None and callable(key_factory)
                and getattr(adapter, '_shared_record_payload', False)
                and adapter.request_bytes(payload) > int(limit)):
            smaller = key_factory()
            if smaller.request_bytes(payload) < adapter.request_bytes(payload):
                adapter = smaller
            v2_factory = getattr(adapter, 'shared_keys_v2_copy', None)
            if callable(v2_factory) and adapter.request_bytes(payload) > int(limit):
                smaller = v2_factory()
                if smaller.request_bytes(payload) < adapter.request_bytes(payload):
                    adapter = smaller
    elif saved:
        key_factory = getattr(adapter, 'shared_keys_copy', None)
        if callable(key_factory) and getattr(adapter, '_shared_record_payload', False):
            smaller = key_factory()
            artifact = read_artifact(cache)
            if all(artifact.get(k) == getattr(smaller, k) for k in
                   ('model_id', 'backend', 'prompt_sha256', 'generation_config_sha256')):
                adapter = smaller
            else:
                v2_factory = getattr(smaller, 'shared_keys_v2_copy', None)
                if callable(v2_factory):
                    v2 = v2_factory()
                    if all(artifact.get(k) == getattr(v2, k) for k in
                           ('model_id', 'backend', 'prompt_sha256', 'generation_config_sha256')):
                        adapter = v2
    if not saved:
        # Verify the complete live/input cut before even entering the adapter.
        # Cache authentication above this boundary remains purely read-only.
        finish_validation_phase()
    encoding_proof = (adapter.request_encoding_proof(payload)
        if callable(getattr(adapter, 'request_encoding_proof', None)) else None)
    request_bytes = (
        adapter.request_bytes(payload)
        if callable(getattr(adapter, "request_bytes", None))
        else len(canonical_json(payload).encode("utf-8"))
    )
    max_request_bytes, request_budget = effective_request_limit(adapter, cache)
    if max_request_bytes is not None and request_bytes > int(max_request_bytes):
        raise ValueError(
            f"model request is {request_bytes} bytes, exceeding the configured ceiling "
            f"{int(max_request_bytes)}; explicit budget direction required"
        )
    from backend.pipeline.viewpoint_model_cache_handoff import reuse_response
    if saved:
        raw = read_saved_model_response(adapter, payload, cache)
        return bound_model_response(adapter,payload,raw,cache), 0, 0.0
    intent_path = cache.with_name(cache.stem + '.call-intent.json')
    encoding_path = cache.with_name(cache.stem + '.request-encoding.json')
    if intent_path.exists() or encoding_path.exists():
        raise ValueError(f"prior model invocation has no saved response; outcome unknown, no automatic retry: {cache}")
    if request_budget is not None:
        write_immutable(cache.with_name(cache.stem+'.request-budget.json'), {
            'schema_version':'cvp_sql_model_request_budget.v1', **request_budget,
            'request_identity':model_request_identity(adapter,payload),
            'cache_path':str(cache.resolve()), 'actual_request_bytes':request_bytes})
    started = time.monotonic()
    reused = reuse_response(adapter, payload, cache)
    request_identity = model_request_identity(adapter, payload)
    intent = None
    if reused is None:
        body = {'schema_version': 'wang_model_call_intent_v1', **request_identity,
                'cache_path': str(cache.resolve()), 'automatic_retry': False}
        intent = {**body, 'artifact_sha256': sha256_json(body)}
        write_immutable(intent_path, intent, require_new=True)
    if encoding_proof is not None:
        body = {'schema_version': 'wang_model_request_encoding_proof_v1', **encoding_proof}
        write_immutable(encoding_path, body | {'artifact_sha256': sha256_json(body)})
    try:
        raw = dict(reused[0] if reused is not None else adapter.generate(payload))
    except BaseException as exc:
        body = {'schema_version': 'wang_model_call_failure_v1', 'invocation_sha256': intent['artifact_sha256'],
                'error_type': type(exc).__name__, 'execution_status': 'outcome_unknown', 'automatic_retry': False}
        write_immutable(cache.with_name(cache.stem + '.call-failure.json'),
                        body | {'artifact_sha256': sha256_json(body)})
        raise
    elapsed = round(time.monotonic() - started, 3)
    artifact = {
        "schema_version": "wang_canonical_viewpoint_batch_raw_response_v1",
        **request_identity,
        "wall_seconds": elapsed,
        "response_sha256": sha256_json(raw),
        "response": raw,
    }
    if encoding_proof is not None:
        artifact['request_encoding_proof_sha256'] = sha256_json(encoding_proof)
    if reused is not None:
        artifact.update({"calls_recorded": 0, "wall_seconds": 0.0,
                         "cache_handoff": reused[1]})
    else:
        artifact['invocation_sha256'] = intent['artifact_sha256']
    artifact["artifact_sha256"] = sha256_json(artifact)
    write_immutable(cache, artifact)
    return bound_model_response(adapter,payload,raw,cache), 0 if reused is not None else 1, 0.0 if reused is not None else elapsed
