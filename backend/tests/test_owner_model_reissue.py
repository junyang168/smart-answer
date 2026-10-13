from copy import deepcopy
from types import SimpleNamespace
import pytest
from backend.pipeline import owner_model_reissue as d
from backend.pipeline.source_reconciliation_runner import write, read, body
from backend.pipeline.viewpoint_resolution_runtime import model_request_identity, call_model
from backend.pipeline import source_reconciliation as c


def fixture(tmp_path):
    packet={"claims":[{"claim_id":"C","statement":"完整原文"}]}
    packet["packet_sha256"]=c.sha256_json(packet)
    adapter=SimpleNamespace(model_id="gpt-6.1-sol",backend="codex_subscription",
        prompt_sha256="p",generation_config_sha256="g")
    adapter.request_encoding_proof=lambda p:{"semantic_payload_sha256":c.sha256_json(p),"exact_roundtrip_verified":True}
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    write_immutable(tmp_path/"review/batch-packet.json",packet)
    identity=model_request_identity(adapter,packet)
    intent=write(tmp_path/"review/raw-proposal.call-intent.json",{
        "schema_version":"wang_model_call_intent_v1",**identity,
        "cache_path":str((tmp_path/"review/raw-proposal.json").resolve()),"automatic_retry":False})
    write(tmp_path/"review/raw-proposal.call-failure.json",{
        "schema_version":"wang_model_call_failure_v1","invocation_sha256":intent["artifact_sha256"],
        "execution_status":"outcome_unknown","automatic_retry":False})
    write(tmp_path/"review/raw-proposal.request-encoding.json",{
        "schema_version":"wang_model_request_encoding_proof_v1",**adapter.request_encoding_proof(packet)})
    for name in ("packet.json","before.json","context.json","runtime-proof.json"):
        write(tmp_path/name,{"value":name})
    return adapter,intent,packet


def test_exact_owner_handoff_preserves_old_unknown_and_one_fixed_directory(tmp_path):
    adapter,intent,packet=fixture(tmp_path)
    old={p.name:p.read_bytes() for p in (tmp_path/"review").iterdir()}
    target=d.authorize(tmp_path,adapter,"可以",intent["artifact_sha256"])
    assert target==tmp_path/"review-owner-authorized-once"
    assert {p.name:p.read_bytes() for p in (tmp_path/"review").iterdir()}==old
    assert d.authorize(tmp_path,adapter,"可以",intent["artifact_sha256"])==target
    assert read(tmp_path/d.NAME)["content_approval"] is False
    assert not (target/"raw-proposal.call-intent.json").exists()


@pytest.mark.parametrize("drift",["model","backend","prompt","config","encoding","old_intent","old_failure","old_answer","other_stage","before","packet","runtime","quote"])
def test_reissue_rejects_any_identity_or_scope_drift(tmp_path,drift):
    adapter,intent,packet=fixture(tmp_path)
    d.authorize(tmp_path,adapter,"可以",intent["artifact_sha256"])
    if drift in {"model","backend","prompt","config"}:
        setattr(adapter,{"model":"model_id","backend":"backend","prompt":"prompt_sha256","config":"generation_config_sha256"}[drift],"drift")
    elif drift=="encoding":adapter.request_encoding_proof=lambda p:{"changed":True}
    elif drift in {"old_answer","other_stage"}:
        write(tmp_path/"review"/("raw-proposal.json" if drift=="old_answer" else "raw-review.call-intent.json"),{})
    elif drift=="quote":
        with pytest.raises(ValueError): d.authorize(tmp_path,adapter,"new approval",intent["artifact_sha256"])
        return
    else:
        path={"old_intent":"review/raw-proposal.call-intent.json","old_failure":"review/raw-proposal.call-failure.json",
              "before":"before.json","packet":"packet.json","runtime":"runtime-proof.json"}[drift]
        (tmp_path/path).write_text("tampered")
    with pytest.raises(ValueError):d.directory(tmp_path,adapter)


def test_authorized_invocation_still_cannot_repeat_after_failure(tmp_path):
    adapter,intent,packet=fixture(tmp_path)
    target=d.authorize(tmp_path,adapter,"可以",intent["artifact_sha256"])
    calls=[]
    def generate(p):calls.append(p);raise RuntimeError("outcome unknown")
    adapter.generate=generate
    with pytest.raises(RuntimeError):call_model(adapter,packet,target/"raw-proposal.json")
    with pytest.raises(ValueError,match="outcome unknown"):
        call_model(adapter,packet,target/"raw-proposal.json")
    assert len(calls)==1
    assert read(tmp_path/"review/raw-proposal.call-failure.json")["execution_status"]=="outcome_unknown"


def test_default_directory_never_retries_or_creates_authorization(tmp_path):
    fixture(tmp_path)
    assert d.directory(tmp_path)==tmp_path/"review"
    assert not (tmp_path/d.NAME).exists()


def test_wrong_original_intent_and_empty_owner_quote_rejected(tmp_path):
    adapter,intent,_=fixture(tmp_path)
    for quote,pin in [("",intent["artifact_sha256"]),("可以","f"*64)]:
        with pytest.raises(ValueError):d.authorize(tmp_path,adapter,quote,pin)
