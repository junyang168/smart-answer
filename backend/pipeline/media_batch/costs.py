"""Usage-based estimates from saved responses, distinct from admission reserves."""

from datetime import date, datetime, timezone
from pathlib import Path
import json
import math

from .runtime import file_sha
from .store import key
from psycopg.types.json import Jsonb


def estimate(request, raw, *, at=None):
    at = at or datetime.now(timezone.utc).date()
    model = request.get("model") or request.get("config", {}).get("model") or "gemini"
    result = dict(
        currency="USD",
        pricing_version="2026-11-06",
        model=model,
        basis="saved usage; conservative rates, no cache discount; not a provider invoice",
    )
    if model == "gpt-transcribe":
        chunk = request["chunk"]
        seconds = (chunk["end_ms"] - chunk["start_ms"]) / 1000
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Invalid ASR duration")
        result.update(
            audio_seconds=seconds,
            estimated_usd_upper=seconds / 60 * 0.0045,
            pricing_source="https://developers.openai.com/api/docs/pricing",
        )
        return result
    if model == "gpt-6.1-sol":
        inputs, outputs = raw["usage"]["input_tokens"], raw["usage"]["output_tokens"]
        input_rate, output_rate = 2.5, 10
        result["pricing_source"] = "https://developers.openai.com/api/docs/pricing"
    elif model in ("gemini", "gemini-3.8-flash"):
        usage = raw["usage_metadata"]
        inputs = usage["prompt_token_count"]
        outputs = (usage.get("candidates_token_count") or 0) + (
            usage.get("thoughts_token_count") or 0
        )
        input_rate, output_rate = (
            (0.75, 3.75) if at <= date(2026, 12, 31) else (1.5, 7.5)
        )
        result["pricing_source"] = "https://ai.google.dev/gemini-api/docs/pricing"
    else:
        raise ValueError("Unknown pricing model")
    if not all(type(n) is int and n >= 0 for n in (inputs, outputs)):
        raise ValueError("Invalid token usage")
    result.update(
        input_tokens=inputs,
        output_including_reasoning_tokens=outputs,
        input_rate_per_million=input_rate,
        output_rate_per_million=output_rate,
        estimated_usd_upper=(inputs * input_rate + outputs * output_rate) / 1e6,
    )
    return result


def reconcile(store, batch):
    """Enrich completed receipts once. Verify exact call/request/response bindings."""
    with store.connection() as c:
        rows = list(
            c.execute(
                "SELECT m.*,t.payload FROM media_ingest.calls m JOIN media_ingest.tasks t ON t.id=m.task WHERE m.batch=%s AND m.state='complete' AND NOT m.info ? 'usage_cost'",
                (batch,),
            )
        )
    pending = {r["id"]: r for r in rows}
    roots = {Path(r["payload"]["out"]) for r in rows}
    roots |= {p.with_name(p.name + "-speakers") for p in roots}
    done = []
    for root in roots:
        if not root.is_dir():
            continue
        task_ids = {
            r["task"]
            for r in rows
            if root
            in (
                Path(r["payload"]["out"]),
                Path(r["payload"]["out"]).with_name(
                    Path(r["payload"]["out"]).name + "-speakers"
                ),
            )
        }
        for path in root.glob("**/calls/*/state.json"):
            request = json.loads(path.with_name("request.json").read_text())
            request_out = str(path.parent.parent.parent.resolve())
            candidates = [
                pending[call_id]
                for task in task_ids
                if (call_id := key([task, request_out, request])) in pending
            ]
            if len(candidates) != 1:
                continue
            row = candidates[0]
            state = json.loads(path.read_text())
            response = path.with_name("response.json")
            if (
                state.get("status") != "complete"
                or file_sha(response) != row["info"].get("response_sha256")
                or state.get("response_sha256") != row["info"].get("response_sha256")
            ):
                raise ValueError("Cost receipt response binding changed")
            cost = estimate(
                request,
                json.loads(response.read_text()),
                at=row["updated_at"].astimezone(timezone.utc).date(),
            )
            with store.connection() as c:
                updated = c.execute(
                    "UPDATE media_ingest.calls SET info=info || %s WHERE id=%s AND state='complete' AND info->>'response_sha256'=%s AND NOT info ? 'usage_cost' RETURNING id",
                    (Jsonb(dict(usage_cost=cost)), row["id"], state["response_sha256"]),
                ).fetchone()
            if updated:
                done.append(row["id"])
            pending.pop(row["id"])
    return dict(updated=len(done), unmatched=list(pending), model_calls=0)
