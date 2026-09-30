"""Single resumable #411 background job; bounded repairs and arbitration, no grouping."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess

from dotenv import load_dotenv
from backend.pipeline import exegesis_passage_location_runner as loc


QUOTE_FEEDBACK = """此前逐字引文检查失败。这是仅一次纠正机会，只返回所列Claim。
quote只复制原文中连续10到30个字符，不改标点，不去除换行，不拼接不同位置。
重新核验原文支持的primary，不强求旧结论。"""
ARBITRATION_FEEDBACK = """这是一次且仅一次的经文归属分歧核对，不做角色分类。
重新读原件，回应独立复核的具体理由。Claim/EvidenceStep的scripture_refs是提取
元数据，不能证明原件实际给出了单节号；原件只证明范围就保留范围，不凭常识补
节号。复合Claim没有原文证明的单一主要对象就unresolved。不机械选较早或较宽
范围，不为求一致强行采用另一模型结论。quote只取连续10到30字。"""


def subset(batch, ids):
    claims = [c for c in batch["claims"] if c["claim_id"] in ids]
    sources = [s for s in batch.get("sources", [batch.get("source")])
               if s["source_id"] in {c["source_id"] for c in claims}]
    return {"claims": claims, "sources": sources}


def raw_answer(provider, directory):
    transport = loc.checked(directory / "transport.raw.json")
    if transport.get("returncode") != 0:
        raise ValueError(f"transport failure retained; no automatic retry: {directory}")
    if provider == "gpt":
        value = json.loads((directory / "last-message.raw.txt").read_text())
    else:
        wrapper = json.loads(transport["stdout"])
        if wrapper.get("is_error"):
            raise ValueError("provider error retained; no automatic retry")
        value = wrapper["structured_output"]
        if isinstance(value, str):
            value = json.loads(value)
    return loc.normalize_fields(value)


def verify_cached(path, batch, model):
    artifact = loc.checked(path)
    if artifact["batch_sha256"] != loc.digest(batch) or artifact["model"] != model:
        raise ValueError(f"cached review binding differs: {path}")
    loc.validate(artifact["response"], batch)
    return artifact


def obtain(provider, model, batch, directory, max_bytes):
    """Reuse valid answers; repair only invalid quotations once, retaining every original."""
    target = directory / "validated.json"
    if target.exists():
        return verify_cached(target, batch, model)
    if not directory.exists():
        try:
            return loc.call(provider, model, batch, directory, max_bytes)
        except Exception as exc:
            loc.seal(directory / "failure.json", {"error_type": type(exc).__name__, "error": str(exc),
                "claim_ids": [c["claim_id"] for c in batch["claims"]],
                "policy": "only verbatim-quotation failure permits one bounded correction"})
    request = loc.checked(directory / "request.json")
    if (request["batch_sha256"] != loc.digest(batch) or request["model"] != model
            or request["provider"] != provider or request["prompt"] != loc.PROMPT
            or request["schema"] != loc.response_schema()):
        raise ValueError("retained request binding changed")
    response, changes = raw_answer(provider, directory)
    # Reject missing/duplicate/foreign IDs before considering a selective repair.
    ids = [r["claim_id"] for r in response["decisions"]]
    if len(ids) != len(set(ids)) or set(ids) != {c["claim_id"] for c in batch["claims"]}:
        raise ValueError("structural failure retained; no automatic retry")
    bad = set()
    for row in response["decisions"]:
        try:
            loc.validate({"decisions": [row]}, subset(batch, {row["claim_id"]}))
        except ValueError as exc:
            if not str(exc).startswith("non-verbatim source evidence:"):
                raise
            bad.add(row["claim_id"])
    correction = None
    if bad:
        payload = subset(batch, bad) | {"validation_feedback": QUOTE_FEEDBACK}
        corrected_dir = directory / "background-quote-correction-1"
        if (corrected_dir / "validated.json").exists():
            correction = verify_cached(corrected_dir / "validated.json", payload, model)
        elif corrected_dir.exists():
            raise ValueError(f"bounded quotation correction exhausted: {corrected_dir}")
        else:
            try:
                correction = loc.call(provider, model, payload, corrected_dir, max_bytes)
            except Exception as exc:
                loc.seal(corrected_dir / "failure.json", {"error": str(exc),
                    "error_type": type(exc).__name__, "bounded_correction_exhausted": True})
                raise
        fixed = {r["claim_id"]: r for r in correction["response"]["decisions"]}
        response = {"decisions": [fixed.get(r["claim_id"], r) for r in response["decisions"]]}
    loc.validate(response, batch)
    return loc.seal(target, {"batch_sha256": loc.digest(batch), "provider": provider, "model": model,
        "response": response, "original_transport_sha256": loc.checked(directory / "transport.raw.json")["artifact_sha256"],
        "field_name_normalizations": changes, "corrected_claim_ids": sorted(bad),
        "correction_artifact_sha256": correction["artifact_sha256"] if correction else None})


def reconcile(args, number, batch, a, b):
    primary = {r["claim_id"]: r for r in a["response"]["decisions"]}
    review = {r["claim_id"]: r for r in b["response"]["decisions"]}
    ids = {cid for cid in primary if primary[cid]["status"] != review[cid]["status"]
           or primary[cid]["primary"] != review[cid]["primary"]}
    if not ids:
        return None
    payload = subset(batch, ids) | {
        "arbitration_feedback": [{"claim_id": cid, "prior_primary": primary[cid],
                                   "independent_review": review[cid]} for cid in sorted(ids)],
        "bounded_arbitration_instructions": ARBITRATION_FEEDBACK}
    prior = args.output / f"batch-{number:03d}" / "primary-arbitration-1" / "validated.json"
    if prior.exists():
        # The manually completed first arbitration is explicitly adopted with its
        # own retained prompt/input, never relabelled as a new request.
        artifact = loc.checked(prior)
        request = loc.checked(prior.parent / "request.json")
        if (artifact["model"] != args.primary_model or artifact["batch_sha256"] != loc.digest(request["payload"])
                or loc.digest(request["payload"]["claims"]) != loc.digest(payload["claims"])
                or loc.digest(request["payload"]["sources"]) != loc.digest(payload["sources"])):
            raise ValueError("prior arbitration binding differs")
        loc.validate(artifact["response"], payload)
        return artifact
    directory = args.output / f"batch-{number:03d}" / "background-primary-arbitration-1"
    return obtain("gpt", args.primary_model, payload, directory, args.max_request_bytes)


def preflight(args, inputs):
    ledger, packet = loc.checked(args.ledger), loc.checked(args.packet)
    if ledger["artifact_sha256"] != inputs["ledger_sha256"] or packet["artifact_sha256"] != inputs["packet_sha256"]:
        raise ValueError("formal artifact binding drift")
    frozen = {c["claim_id"]: c for c in packet["claims"]}
    claims = [c for batch in inputs["batches"] for c in batch["claims"]]
    ids = [c["claim_id"] for c in claims]
    eligible = {d["claim_id"] for d in ledger["decisions"] if d["role"] == "passage_exegesis"
                and d["passage_identity_status"] in {"disputed", "pending_context_reference_verification"}}
    if len(ids) != 624 or len(set(ids)) != 624 or set(ids) != eligible:
        raise ValueError("scope must cover exactly the 624 disputed/pending Claims")
    for c in claims:
        if any(c.get(k) != v for k, v in frozen[c["claim_id"]].items()):
            raise ValueError(f"frozen Claim changed: {c['claim_id']}")
    load_dotenv(".env")
    from backend.pipeline.claim_passage_role_runner import build_rows, PostgresKnowledgeStore
    pins = [{"claim_id": c["claim_id"], "source_id": c["source_id"], "statement": c["statement"],
             "scripture_refs": c["scripture_refs"], "pinned_claim_revision": c["claim_revision"],
             "claim_revision_sha256": c["claim_content_sha256"]} for c in claims]
    current = build_rows(PostgresKnowledgeStore(), pins)
    if any(row != frozen[row["claim_id"]] for row in current):
        raise ValueError("current Claim/source/evidence/fragment graph drift")
    paths = {}
    for batch in inputs["batches"]:
        for source in batch.get("sources", [batch.get("source")]):
            paths[source["source_path"]] = source["source_file_sha256"]
            for paragraph in source["paragraphs"]:
                if "visual_source_path" in paragraph:
                    paths[paragraph["visual_source_path"]] = paragraph["visual_file_sha256"]
    for path, expected in paths.items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
            raise ValueError(f"physical source drift: {path}")
    loc.seal(args.job_root / "preflight.json", {"input_sha256": inputs["artifact_sha256"],
        "current_graph_verified_claim_count": 624, "physical_file_count": len(paths),
        "database_mutations": 0, "deferred_excluded": 170})


def execute(args, inputs, progress):
    final = []
    for number, batch in enumerate(inputs["batches"]):
        progress("reviewing", batch_index=number, completed_claim_count=len(final))
        def review(provider, model):
            if number == 0:
                attempt = "attempt-3" if provider == "gpt" else "attempt-1"
                path = args.reuse_first_root / "batch-000" / provider / attempt / "validated.json"
                return verify_cached(path, batch, model)
            return obtain(provider, model, batch, args.output / f"batch-{number:03d}" / provider,
                          args.max_request_bytes)
        with ThreadPoolExecutor(max_workers=2) as pool:
            pa = pool.submit(review, "gpt", args.primary_model)
            pb = pool.submit(review, "claude", args.review_model)
            a, b = pa.result(), pb.result()
        progress("reconciling", batch_index=number, completed_claim_count=len(final))
        arbitration = reconcile(args, number, batch, a, b)
        aa = {r["claim_id"]: r for r in a["response"]["decisions"]}
        bb = {r["claim_id"]: r for r in b["response"]["decisions"]}
        rr = {r["claim_id"]: r for r in arbitration["response"]["decisions"]} if arbitration else {}
        for claim in batch["claims"]:
            cid = claim["claim_id"]
            adopted, independent = rr.get(cid, aa[cid]), bb[cid]
            agreed = adopted["status"] == independent["status"] == "resolved" and adopted["primary"] == independent["primary"]
            source = next(s for s in batch.get("sources", [batch.get("source")]) if s["source_id"] == claim["source_id"])
            final.append({"claim": claim, "source_binding": {k: v for k, v in source.items() if k != "paragraphs"},
                "status": "source_verified_independent_agreement" if agreed else "unresolved",
                "primary": adopted["primary"] if agreed else "", "original_primary_review": aa[cid],
                "independent_review": independent, "arbitration": rr.get(cid),
                "missing": "" if agreed else {"primary": adopted["missing"], "independent": independent["missing"],
                    "competing_primary": adopted["primary"], "competing_independent": independent["primary"],
                    "reason": "one bounded arbitration complete; source identification/scope remains unresolved"},
                "review_artifact_shas": [a["artifact_sha256"], b["artifact_sha256"]] +
                    ([arbitration["artifact_sha256"]] if cid in rr else []),
                "secondary_relations": {"original_primary": aa[cid]["secondary"],
                    "independent": independent["secondary"], "arbitration": rr[cid]["secondary"] if cid in rr else []}})
        progress("batch_completed", batch_index=number, completed_claim_count=len(final),
                 counts=dict(Counter(r["status"] for r in final)))
        loc.seal(args.job_root / f"checkpoint-{number:03d}.json", {"input_sha256": inputs["artifact_sha256"],
                 "completed_claim_count": len(final), "rows": final})
    assert len(final) == 624 and len({r["claim"]["claim_id"] for r in final}) == 624
    report = loc.seal(args.job_root / "passage-ownership-ledger.json", {"input_sha256": inputs["artifact_sha256"],
        "schema_version": "wkp411_passage_ownership_ledger_v1", "counts": dict(Counter(r["status"] for r in final)),
        "rows": final, "database_mutations": 0, "grouping_executed": False, "deferred_excluded": 170})
    loc.seal(args.job_root / "unresolved.json", {"ownership_ledger_sha256": report["artifact_sha256"],
        "rows": [r for r in final if r["status"] == "unresolved"], "not_declared_deferred": True})
    progress("completed", completed_claim_count=624, counts=report["counts"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["output", "job-root", "ledger", "packet", "reuse-first-root"]:
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--primary-model", default="gpt-6-sol")
    parser.add_argument("--review-model", default="claude-opus-5-5")
    parser.add_argument("--max-request-bytes", type=int, default=500000)
    parser.add_argument("--ticket", type=int, help="Post completion/failure status to the authorized ticket")
    args = parser.parse_args()
    lock = (args.output / ".passage-location-job.lock").open("a+")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    args.job_root.mkdir(parents=True, exist_ok=False)
    sequence = 0
    state = {}
    def notify_ticket():
        if args.ticket is None:
            return
        text = (f"#411 经文归属后台 job：{state['status']}\n\n"
                f"已完成审阅：{state.get('completed_claim_count', 0)}/624；"
                f"结果计数：{json.dumps(state.get('counts', {}), ensure_ascii=False)}。\n\n"
                f"产物目录：`{args.job_root}`。代码提交与输入／模型／prompt／schema SHA 见 invocation.json；"
                "原回答、一次有界引文纠正、一次归属仲裁和断点均保留。\n\n"
                "170 条延期项未处理；未运行 grouping、未生成 CVP、未写 Claim／Registry。"
                "这不是 #411 全卡完成。\n")
        if state.get("error"):
            text += f"\n停止原因：{state['error']}。未跳过失败批次，未继续重试。\n"
        body = args.job_root / "ticket-status.txt"
        with body.open("x") as file:
            file.write(text)
        result = subprocess.run(["gh", "issue", "comment", str(args.ticket), "--body-file", str(body)],
                                capture_output=True, text=True, timeout=60)
        loc.seal(args.job_root / "ticket-update.json", {"returncode": result.returncode,
                 "stdout": result.stdout, "stderr": result.stderr})
    def progress(status, **fields):
        nonlocal sequence
        state.update(fields)
        state.update(status=status, pid=os.getpid(), updated_at=datetime.now(timezone.utc).isoformat())
        loc.seal(args.job_root / f"event-{sequence:04d}.json", state)
        sequence += 1
        temporary = args.job_root / "progress.tmp"
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(args.job_root / "progress.json")
        print(json.dumps(state, ensure_ascii=False), flush=True)
    try:
        inputs = loc.checked(args.output / "location-input.json")
        if inputs["prompt_sha256"] != hashlib.sha256(loc.PROMPT.encode()).hexdigest() or inputs["schema_sha256"] != loc.digest(loc.response_schema()):
            raise ValueError("prompt/schema drift")
        progress("preflight", completed_claim_count=0)
        loc.seal(args.job_root / "invocation.json", {"input_sha256": inputs["artifact_sha256"],
            "pid": os.getpid(), "models": {"primary": args.primary_model, "independent": args.review_model},
            "job_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "runner_code_sha256": hashlib.sha256(Path(loc.__file__).read_bytes()).hexdigest(),
            "prompt_sha256": inputs["prompt_sha256"], "schema_sha256": inputs["schema_sha256"],
            "arguments": {k: str(v) for k, v in vars(args).items()}, "bounded_quote_corrections": 1,
            "bounded_semantic_arbitrations": 1, "api_fallback": False, "grouping_executed": False})
        preflight(args, inputs)
        execute(args, inputs, progress)
        notify_ticket()
    except Exception as exc:
        progress("stopped_on_failure", error_type=type(exc).__name__, error=str(exc),
                 next_action="inspect retained failure; no additional automatic retry")
        try:
            if not (args.job_root / "ticket-status.txt").exists():
                notify_ticket()
        except Exception as notify_error:
            loc.seal(args.job_root / "ticket-notification-failure.json", {"error": str(notify_error)})
        raise


if __name__ == "__main__":
    main()
