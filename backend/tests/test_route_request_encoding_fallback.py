"""A route request over the ceiling takes a lossless encoding instead of failing (#460)."""
import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from backend.api.canonical_repository.viewpoint_request_encoding import decode_shared_records
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.pipeline.viewpoint_resolution_runtime import call_model
from backend.pipeline.viewpoint_route_resolution_worker import OwnershipGuardedAdapter


def payload():
    evidence = [{"evidence_step_id": f"E{i}", "source_sha256": "a" * 64, "citation_status": "unresolved",
                 "verbatim_excerpt": "耶穌基督為我付出不能計算的贖價"} for i in range(150)]
    return {"claims": [{"claim_id": f"C{n}", "evidence": evidence} for n in range(4)]}


class Answer(BaseModel):
    pass


def plain_adapter(calls):
    client = SimpleNamespace(model="gpt-6.1-sol", backend="codex_subscription", reasoning_effort="high",
                             generate_json=lambda **kw: calls.append(kw) or {})
    return StructuredJsonReviewerAdapter(client=client, prompt="routes", response_model=Answer, schema_name="r")


def test_over_ceiling_plain_request_is_sent_losslessly_once(tmp_path):
    calls = []
    a = plain_adapter(calls)
    p = payload()
    a.max_request_bytes = a.shared_records_copy().request_bytes(p)
    assert a.request_bytes(p) > a.max_request_bytes
    call_model(a, p, tmp_path / "raw.json")
    assert decode_shared_records(json.loads(calls[0]["user_prompt"])) == p
    assert not a._shared_record_payload
    assert call_model(a, p, tmp_path / "raw.json")[1] == 0 and len(calls) == 1


def test_still_too_large_after_encoding_stops(tmp_path):
    a = plain_adapter([])
    a.max_request_bytes = 100
    with pytest.raises(ValueError, match="configured ceiling"):
        call_model(a, payload(), tmp_path / "raw.json")


def test_guarded_adapter_copies_stay_guarded():
    checks = []
    g = OwnershipGuardedAdapter(plain_adapter([]), lambda: checks.append(1))
    copy = g.shared_records_copy()
    assert isinstance(copy, OwnershipGuardedAdapter)
    copy.generate({"x": 1})
    assert checks == [1, 1]


def test_guarded_adapter_copy_keeps_guard_and_forwards_client():
    from copy import copy
    inner = plain_adapter([])
    g = OwnershipGuardedAdapter(inner, lambda: None)
    c = copy(g)
    c._client = "private"
    assert isinstance(c, OwnershipGuardedAdapter) and c._adapter is not inner
    assert c._adapter._client == "private" and inner._client != "private"
