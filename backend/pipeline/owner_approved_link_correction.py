"""Code-owned approved link correction and source/Claim/group continuation.

Authorization is per exact old link/postimage, not future arbitrary repairs.
The original source transaction, refusal and audit remain immutable. A child
transaction has its own durable call/apply intent and a strict SQL store gate.
"""
from copy import deepcopy
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv
from backend.pipeline import source_reconciliation as c
from backend.pipeline.source_reconciliation_runner import (
    Runner, Stop, binding, body, read, write, validate_package, audit_jobs,
    validate_completion_population,
)
from backend.pipeline.source_reconciliation_runtime import (
    ProductionPorts, expected_invalidations, maintenance_lock, PROJECT,
)
from backend.api.canonical_repository.postgres_store import ChangeOperation, stored_operation_payload
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.api.canonical_repository.viewpoint_production_safety import exclusive_cvp_run_lock
from backend.pipeline.viewpoint_authorized_source_repair_357 import Review
from backend.pipeline.viewpoint_resolution_runtime import subscription_client, read_artifact
from backend.pipeline.viewpoint_semantic_contract import semantic_prompt

VERSION = "wang_owner_approved_link_correction_v1"
REVIEWER = "owner_approved_link_opus_terminal_review"
PROMPT = semantic_prompt('''独立审核负责人授权的一条 equivalent_component → equivalent_full 精确连接后像。
只审核这一次，不重新提议、不重复GPT仲裁、不审核教授神学对错。
完整Claim、实际CVP、完整自身原文、旧审计/GPT异议裁决、其他成员与依赖均提供。
旧locator须覆盖完整Claim。equivalent_full在存储schema中以component_locator=null表示全文，
不是删掉引文或缩窄内容；原locator/Claim/所有证据/历史版本均保留在授权证明中。
除连接类型、locator的全文存储形状、revision及fresh decision provenance外不得变化。
fresh candidate/decision由程序按此唯一Opus审核回答、实际时间和精确包创建，不能继承旧批准。
不能把负责人执行授权或GPT旧仲裁冒充新审核通过。若需改正文、端点、证据、归属或其他范围则拒绝。
每个指定Claim恰好一个verdict，只有全部成立才approved=true及problems=[]。''')


def adapter(live=False):
    client = subscription_client("claude", "claude-opus-5-5", "high") if live else SimpleNamespace(
        model="claude-opus-5-5", backend="claude_subscription", reasoning_effort="high", max_output_tokens=64000)
    obj = StructuredJsonReviewerAdapter(client=client, prompt=PROMPT, response_model=Review,
        schema_name="owner_approved_link_correction_review_v1", compact_payload=True,
        shared_text_payload=True, shared_record_payload=True)
    obj.max_request_bytes = 500000
    return obj


class ReviewPorts(ProductionPorts):
    def adapter(self, stage):
        if stage != "initial_review": raise ValueError("no extra proposal/preapproval/review round")
        return adapter(True)


def target_postimage(old, claim, live, new_type):
    if old["link_type"] != "equivalent_component" or new_type != "equivalent_full":
        raise ValueError("this correction contract only permits component to full")
    locator = old.get("component_locator")
    text = claim["statement"]
    if (old["effective_state"] != "active" or old["pinned_claim_revision"] != claim["revision"]
            or claim.get("review_status") in c.HUMAN_HOLDS
            or not locator or locator["claim_sha256"] != c.semantic_record_sha(claim)
            or locator["statement_component"] != text
            or locator["canonical_spans"] != [{"start_char":0,"end_char":len(text),"exact_text":text}]):
        raise ValueError("full Claim locator/version/hold mismatch")
    if any(p.get("effective_state") == "active" and p["claim_id"] == old["claim_id"]
            and p["link_type"] == "equivalent_full" for k,p in live.items()
            if k.startswith("viewpoint_claim_links/") and p["viewpoint_claim_link_id"] != old["viewpoint_claim_link_id"]):
        raise ValueError("another active full membership exists")
    new = deepcopy(old)
    new.update(link_type=new_type, component_locator=None, revision=old["revision"]+1)
    return new


def validate_target(packet):
    old = packet["before_link"]; new = packet["after_link"]
    states = c.index(packet["context_records"])
    expected = target_postimage(old,packet["item"]["claim"],states,"equivalent_full")
    if new != expected: raise ValueError("unapproved link/locator/endpoint/evidence change")
    auth = packet["authorization"]
    if (auth["schema_version"] != VERSION or auth["human_content_approval"] is not False
            or not auth["authorization_quote"] or auth["automatic_future_repairs_authorized"] is not False
            or auth["before_link_sha256"] != c.sha256_json(old)
            or auth["after_link_sha256"] != c.sha256_json(new)):
        raise ValueError("precise authorization missing or changed")
    for source in packet["sources"]:
        c.verify_file({"path":source["path"],"file_sha256":source["file_sha256"]})


def review_raw(root, packet):
    obj = adapter(); proof = obj.request_encoding_proof(packet)
    expected = {"model_id":obj.model_id,"backend":obj.backend,"prompt_sha256":obj.prompt_sha256,
        "generation_config_sha256":obj.generation_config_sha256,"request_payload_sha256":c.sha256_json(packet),
        "request_encoding_proof":proof,"request_bytes":obj.request_bytes(packet),
        "stage":"initial_review","automatic_retry":False}
    raw = read(root/"initial_review.raw.json")
    if (expected["request_bytes"] > 500000 or not proof or proof["exact_roundtrip_verified"] is not True
            or body(read(root/"initial_review.reservation.json")) != expected
            or any(raw.get(k) != v for k,v in expected.items())
            or raw["response_sha256"] != c.sha256_json(raw["response"])
            or raw["provider_raw_sha256"] != c.sha256_json(raw.get("provider_raw"))):
        raise ValueError("actual Opus wire/raw/reservation identity changed")
    if datetime.fromisoformat(raw["completed_at"]).utcoffset() is None:
        raise ValueError("actual review completion time missing")
    identity = raw.get("runtime_identity")
    if identity is not None and (identity.get("model_id") != obj.model_id
            or identity.get("backend") != obj.backend or identity.get("reasoning_effort") != "high"
            or not identity.get("file_sha256")):
        raise ValueError("actual subscription executable/model/effort identity changed")
    response = Review.model_validate(raw["response"])
    if (len(response.verdicts) != 1 or response.verdicts[0].claim_id != packet["before_link"]["claim_id"]
            or not response.verdicts[0].approved or response.verdicts[0].problems):
        raise Stop("authorized exact link correction rejected; no further correction or partial write")
    return raw


def package_for(packet, raw, root):
    validate_target(packet)
    old = packet["before_link"]; link = deepcopy(packet["after_link"])
    states = c.index(packet["context_records"])
    prior = states[c.key("viewpoint_identity_decisions",old["decision_id"])]
    candidate = deepcopy(states[c.key("viewpoint_identity_candidates",prior["identity_candidate_id"])])
    seed = c.sha256_json({"policy":VERSION,"packet":c.sha256_json(packet),"review":raw["artifact_sha256"]})
    candidate.update(revision=1,identity_candidate_id="VIC-"+seed[:20],
        candidate_claim_ids=[old["claim_id"]],candidate_viewpoint_ids=[old["viewpoint_id"]],
        proposed_action="match_existing",generation_fingerprint=seed)
    identity = {"claims":candidate["candidate_claim_ids"],"viewpoints":candidate["candidate_viewpoint_ids"],
        "relations":candidate.get("seed_relation_ids",[]),"action":candidate["proposed_action"],
        "blockers":candidate.get("blocker_codes",[]),"coverage_snapshot_id":candidate.get("coverage_snapshot_id"),
        "generation_fingerprint":seed}
    if candidate.get("scope_manifest_sha256"): identity["scope_manifest_sha256"] = candidate["scope_manifest_sha256"]
    candidate["identity_candidate_id"] = "VIC-"+c.sha256_json(identity)[:20]
    decision = deepcopy(prior)
    decision.update(revision=1,identity_decision_id="VID-"+seed[:20],identity_candidate_id=candidate["identity_candidate_id"],
        input_sha256=seed,decision="match_existing",resolved_viewpoint_id=old["viewpoint_id"],
        claim_link_decisions=[{"claim_id":old["claim_id"],"link_type":"equivalent_full"}],
        proposition_unit_link_decisions=[],review_artifact_sha256=raw["artifact_sha256"],
        semantic_call_artifact_sha256s=[raw["artifact_sha256"]],reviewer_model_ids=["claude-opus-5-5"],
        reviewer_kind="system",reviewer_id=REVIEWER,approval_basis="owner_scoped_independent_terminal_review",
        review_status="system_approved",created_at=raw["completed_at"],policy_version=VERSION,
        reason="Owner authorized the exact full-Claim classification correction; a single fresh Opus independently reviewed its postimage. Original audit and upheld GPT finding retained.")
    link["decision_id"] = decision["identity_decision_id"]
    return {"package_id":"OWNER-LINK-"+seed[:24],"viewpoint_claim_links":[link],
        "viewpoint_identity_candidates":[candidate],"viewpoint_identity_decisions":[decision],
        "source_repair_application":{"schema_version":VERSION,"run_root":str(root),
            "packet_sha256":c.sha256_json(packet),"review_sha256":raw["artifact_sha256"],
            "approval_status":"not_human_approved"}}


def validate_reviewed_package(package):
    """Planner/store entry, not a policy/status whitelist or supplied boolean."""
    app = package.get("source_repair_application",{})
    root = Path(app.get("run_root",""))
    if app.get("schema_version") != VERSION or not root.is_absolute():
        raise ValueError("exact approved-link package authority missing")
    packet = body(read(root/"packet.json"))
    before = c.index(read(root/"before.json")["records"])
    validate_parent(root,before,ProductionPorts())
    raw = review_raw(root,packet)
    if package != package_for(packet,raw,root):
        raise ValueError("store package differs from real scoped Opus answer/authorized records")


def assert_plan(plan, package, before):
    ids = {(col,row[c.KNOWLEDGE_COLLECTIONS[col][1]]):row for col,rows in package.items()
           if col in c.KNOWLEDGE_COLLECTIONS for row in rows}
    if len(plan["operations"]) != 3 or len(ids) != 3:
        raise ValueError("authorized link transaction must have exactly 3 operations")
    seen = set()
    for raw in plan["operations"]:
        op = ChangeOperation(**raw); key = (op.collection,op.object_id); k = c.key(*key)
        if key not in ids or key in seen: raise ValueError("extra or repeated write")
        desired = "update" if op.collection == "viewpoint_claim_links" else "create"
        if op.operation != desired or (k in before) != (desired == "update"):
            raise ValueError("replay/identity overwrite/retirement not authorized")
        model = c.KNOWLEDGE_COLLECTIONS[op.collection][0]
        expected = model.model_validate(ids[key]).model_dump(mode="json")
        if stored_operation_payload(op) != expected:
            raise ValueError("plan differs from reviewed exact postimage")
        seen.add(key)


def sql_applied(plan, store):
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute("SELECT source_kind,source_sha256,fingerprint_sha256,status FROM wang_knowledge.change_sets WHERE change_set_id=%s",(plan["change_set_id"],))
        row = cursor.fetchone()
    if row is None: return False
    if tuple(row) != (VERSION,plan["source_sha256"],plan["fingerprint_sha256"],"applied"):
        raise ValueError("actual child SQL source/version/fingerprint/status changed")
    return True


def rewind(plan, states, before):
    result = deepcopy(states)
    post = deepcopy(before)
    for raw in plan["operations"]:
        op = ChangeOperation(**raw)
        post[c.key(op.collection,op.object_id)] = stored_operation_payload(op)
    with_effects = expected_invalidations(before,post,plan)
    for k,value in with_effects.items():
        if k in post and value == post[k]: continue
        if result.get(k) != value: raise ValueError("child dependency invalidation drift")
        if k in before: result[k] = deepcopy(before[k])
        else: del result[k]
    for raw in plan["operations"]:
        op = ChangeOperation(**raw); k = c.key(op.collection,op.object_id)
        if result.get(k) != stored_operation_payload(op): raise ValueError("child current postimage drift")
        if op.operation == "create": del result[k]
        elif op.operation == "update": result[k] = before[k]
        else: raise ValueError("unapproved retired record")
    return result


PROOF_FILES = ("packet.json","before.json","preflight.json","initial_review.packet.json",
    "initial_review.reservation.json","initial_review.raw.json","effective-package.json","plan.json",
    "backup.json","apply-intent.json","apply-receipt.json")


def child_proof(root):
    before = c.index(read(root/"before.json")["records"])
    plan = body(read(root/"plan.json")); post = deepcopy(before)
    for raw in plan["operations"]:
        op = ChangeOperation(**raw); post[c.key(op.collection,op.object_id)] = stored_operation_payload(op)
    parent = Path(body(read(root/"packet.json"))["source_root"])
    return {"schema_version":VERSION,"status":"applied_readback_verified","before_sha256":c.fingerprint(before),
        "post_sha256":c.fingerprint(expected_invalidations(before,post,plan)),
        "source_root":str(parent),"source_update_id":read(parent/"frozen.json")["update_id"],
        "change_set_id":plan["change_set_id"],"master_data_mutations":3,
        "bindings":[binding(root/n) for n in PROOF_FILES]}


class HistoricalStore:
    """Only reconstruct an already-saved own-source packet; never write SQL."""
    def __init__(self, states): self.states = states
    def list_records(self, collection):
        return [jsonb_order(p) for k,p in sorted(self.states.items()) if k.startswith(collection+"/")]
    def get_record_state(self, collection, oid):
        p = self.states.get(c.key(collection,oid))
        return None if p is None else {"retired":False,"payload":jsonb_order(p)}


def jsonb_order(value):
    """Restore PostgreSQL jsonb object-key storage order, without content edits.

    The independent legacy prompt emits signature/scope without sort_keys.
    Frozen canonical JSON has a different order despite identical values.
    """
    if isinstance(value,dict):
        return {key:jsonb_order(value[key]) for key in sorted(value,key=lambda k:(len(k.encode("utf-8")),k.encode("utf-8")))}
    if isinstance(value,list): return [jsonb_order(item) for item in value]
    return value


class HistoricalAuditStore:
    """Private frame of a proven predecessor, never a current-SQL cache.

    Construct jsonb-order rows once per collection for this one old audit.
    Return isolated copies, so the independent reader cannot mutate the frame.
    Retired SQL reads remain independently fetched once in this invocation.
    """
    def __init__(self,before,sql):
        self.before,self.sql=before,sql
        self.active,self.retired={},{}

    def collection(self,collection,*,include_retired=False):
        if collection not in c.KNOWLEDGE_COLLECTIONS:
            raise ValueError("unknown historical collection")
        if collection not in self.active:
            self.active[collection]=[{"object_id":c.split_key(k)[1],"payload":jsonb_order(p),
                "revision":p["revision"],"review_status":p.get("review_status"),"retired":False}
                for k,p in self.before.items() if k.startswith(collection+"/")]
        rows=list(self.active[collection])
        if include_retired:
            if collection not in self.retired:
                self.retired[collection]=self.sql._query("select json_build_object('object_id',object_id,'payload',payload,"
                    "'revision',revision,'review_status',review_status,'retired',true)::text from wang_knowledge.objects "
                    f"where collection='{collection}' and retired_at is not null order by object_id")
            rows+=self.retired[collection]
        return deepcopy(sorted(rows,key=lambda r:r["object_id"]))


def historical_audit_proofs(audit_path, before):
    """Reconstruct the OLD independent input from its authenticated predecessor.

    After a legitimate child write, current SQL is not the old audit input.
    Use the independent reader's own code and physical source files, substituting
    only the proven active predecessor rows. Retired evidence is still read by
    that reader's independent psql path. This never performs a new judgement.
    """
    import hashlib
    import importlib.util
    import random
    import sys
    from backend.pipeline.viewpoint_semantic_contract import CONTRACT_PATH, CONTEXTUAL_SEMANTICS
    audit = read_artifact(audit_path)
    version = audit["meta"].get("semantic_contract_version")
    if (audit["meta"]["model"] != "gemini-3.8-flash" or version != CONTEXTUAL_SEMANTICS
            or audit["meta"]["semantic_contract_file_sha256"] != c.file_sha256(CONTRACT_PATH)):
        raise ValueError("historical independent audit model/semantic rules changed")
    spec = importlib.util.spec_from_file_location("owner_link_historical_audit_reader",PROJECT/"scripts/audit-library.py")
    reader = importlib.util.module_from_spec(spec); sys.modules[spec.name] = reader; spec.loader.exec_module(reader)
    settings = reader.load_settings(); sql = reader.Store(settings["KNOWLEDGE_DATABASE_URL"])
    store = HistoricalAuditStore(before,sql)
    sources = reader.SourceIndex(store.collection("source_documents"),Path(settings["DATA_BASE_DIR"]))
    class Capture:
        preserve_full_context = True
        def judge(self,prompt,schema):
            self.prompt,self.schema = prompt,schema
            return {"verdict":"blocked","issue":"other","reason":"historical capture only; no model"}
    bindings = [binding(audit_path)]
    cache_root = Path(settings["DATA_BASE_DIR"])/"wang-knowledge-platform/staging/reports/library-audit/.judgements"
    for layer,key in (("3","claim_id"),("4","viewpoint_id")):
        report = audit["layers"][layer]
        if not report["complete"] or any(report.get(k,0) for k in ("model_errors","no_source_text","blocked")):
            raise ValueError("incomplete old audit cannot authorize child repair")
        for row in report["results"]:
            if row.get("paragraphs_dropped_to_pass_filter",0): raise ValueError("shortened old audit input")
            model = Capture(); fn = reader.audit_claims if layer == "3" else reader.audit_viewpoints
            kwargs = {"semantic_contract_version":version} if layer == "4" else {}
            captured = fn(store,sources,model,None,random.Random(0),workers=1,only={row[key]},**kwargs)
            if len(captured["results"]) != 1 or not hasattr(model,"prompt"):
                raise ValueError("historical complete input missing")
            cache = cache_root/(hashlib.sha256(f"gemini-3.8-flash\n{model.prompt}".encode()).hexdigest()+".json")
            if not cache.is_file(): raise ValueError("old before snapshot/full prompt not bound to actual audit cache")
            response = read_artifact(cache)
            if any(row.get(k) != v for k,v in response.items() if k != "provider_content_block"):
                raise ValueError("historical actual judgement drift")
            bindings += [binding(cache)]
            bindings += [binding(source.path) for source in sources._files.values() if source]
    # The independent source index accumulates across targets; normalize by
    # exact path rather than mistaking iteration order for input semantics.
    return list({bound["path"]:bound for bound in bindings}.values())


def validate_parent(root, before, ports):
    packet = body(read(root/"packet.json")); validate_target(packet)
    parent = Path(packet["source_root"])
    ident = "LR-"+c.sha256_json({"parent":binding(parent/"effective-package.json"),
        "before_link":packet["before_link"],"after_link":packet["after_link"]})
    if root != parent/"authorized-link-corrections"/ident:
        raise ValueError("child task/root identity changed; budget cannot be reset")
    if packet["original_audit"]["path"] != str(Path(packet["adjudication_root"]).parent/"independent-audit/audit.json"):
        raise ValueError("child detached from the actual parent audit")
    frozen = body(read(parent/"frozen.json")); c.guard_files(frozen)
    package = body(read(parent/"effective-package.json")); validate_package(package)
    parent_plan = body(read(parent/"plan.json"))
    if not ports.committed(parent_plan): raise ValueError("parent source write not actually applied")
    old = c.index(frozen["before_records"])
    post = c.assert_plan_postimages(package,parent_plan,old)
    if before != expected_invalidations(old,post,parent_plan):
        raise ValueError("child before differs from exact entire applied source boundary")
    for bound in packet["prior_bindings"]: c.verify_file(bound)
    from backend.pipeline.viewpoint_audit_source_adjudication import build_packet, run as adjudicate, source_review_adapter
    adjudication = Path(packet["adjudication_root"])
    audit = Path(packet["original_audit"]["path"])
    from backend.pipeline.immutable_proof_reuse import verify_once
    bindings=packet['prior_bindings']+packet['old_audit_source_proofs']
    bindings += [binding(root/n) for n in ('packet.json','before.json')]
    bindings += [binding(Path(__file__)),binding(PROJECT/'scripts/audit-library.py')]
    verify_once(str(root),bindings,ports.store,lambda:validate_historical_parent(packet,audit,adjudication,before))
    return frozen,old,post,parent_plan


def validate_historical_parent(packet,audit,adjudication,before):
    from backend.pipeline.viewpoint_audit_source_adjudication import build_packet, run as adjudicate, source_review_adapter
    actual_proofs = {b["path"]:b for b in historical_audit_proofs(audit,before)}
    if actual_proofs != {b["path"]:b for b in packet["old_audit_source_proofs"]}:
        raise ValueError("prior actual full audit input/cache proof changed")
    history = HistoricalStore(before)
    if build_packet(audit,history) != read_artifact(adjudication/"packet.json"):
        raise ValueError("saved GPT packet/source/current before identity changed")
    if not (adjudication/"raw-gpt.json").exists(): raise ValueError("no saved GPT answer; no fallback")
    client = SimpleNamespace(model="gpt-6.1-sol",backend="codex_subscription",reasoning_effort="high",max_output_tokens=64000)
    obj = source_review_adapter(client,adjudication/"raw-gpt.json")
    def no_call(payload): raise ValueError("prior GPT arbitration cache missing; no second call")
    obj.generate = no_call
    handoff = adjudicate(audit,adjudication,history,adapters=[obj],final_gpt=True)
    if handoff != packet["handoff"]: raise ValueError("upheld adjudication changed")


def validate_child(root, store, current=None):
    packet = body(read(root/"packet.json")); before = c.index(read(root/"before.json")["records"])
    if c.index(packet["context_records"]) != {k:before[k] for k in c.index(packet["context_records"])}:
        raise ValueError("child review context differs from precise before snapshot")
    ports = ProductionPorts(store); validate_parent(root,before,ports)
    raw = review_raw(root,packet)
    package = body(read(root/"effective-package.json"))
    if package != package_for(packet,raw,root): raise ValueError("child actual review/package drift")
    plan = body(read(root/"plan.json")); assert_plan(plan,package,before)
    intent = body(read(root/"apply-intent.json"))
    if (intent["plan_sha256"] != c.sha256_json(plan) or intent["package_sha256"] != c.sha256_json(package)
            or intent["backup"] != binding(root/"backup.json")):
        raise ValueError("child durable reviewed write intent drift")
    backup = body(read(root/"backup.json")); c.verify_file(backup["dump"])
    if not backup["restore_catalog_nonempty"] or backup["restore_list_exit_code"] != 0:
        raise ValueError("child backup restore proof absent")
    if not sql_applied(plan,store): raise ValueError("child applied receipt not proven in SQL")
    receipt = read(root/"apply-receipt.json")
    if receipt["status"] != "applied" or receipt["change_set_id"] != plan["change_set_id"]:
        raise ValueError("child receipt mismatch")
    current = ports.snapshot() if current is None else current
    # Authenticate retained packet readset without freezing unrelated future
    # campaign inserts. Actual child full-Registry readback was bound at commit.
    written = {c.key(o["collection"],o["object_id"]) for o in plan["operations"]}
    for key,value in c.index(packet["context_records"]).items():
        if key not in written and current.get(key) != value:
            raise ValueError("child retained context/current version drift: "+key)
    return rewind(plan,current,before)


def source_projection(complete, current, store):
    children = complete.get("authorized_link_corrections",[])
    if not children: return current
    if len(children) != 1: raise ValueError("competing/multiple child corrections need a new proven contract")
    proof = read(c.verify_file(children[0])); root = Path(children[0]["path"]).parent
    for bound in proof["bindings"]: c.verify_file(bound)
    if (body(proof) != child_proof(root) or proof["source_update_id"] != complete["update_id"]
            or root.parent.parent != Path(proof["source_root"])):
        raise ValueError("child is not genuinely completed")
    projected = validate_child(root,store,current)
    if proof["before_sha256"] != c.fingerprint(c.index(read(root/"before.json")["records"])):
        raise ValueError("child predecessor proof changed")
    return projected


def validate_apply_plan(plan, metadata):
    root = Path((metadata or {}).get("owner_approved_link_root",""))
    if not root.is_absolute(): raise ValueError("exact authorized child root required at SQL boundary")
    packet = body(read(root/"packet.json")); before = c.index(read(root/"before.json")["records"])
    validate_parent(root,before,ProductionPorts())
    raw = review_raw(root,packet); package = package_for(packet,raw,root)
    if body(read(root/"effective-package.json")) != package: raise ValueError("write package changed")
    actual = json.loads(json.dumps(plan.as_dict())); assert_plan(actual,package,before)
    if body(read(root/"plan.json")) != actual: raise ValueError("SQL plan differs from exact dry-run")
    intent = body(read(root/"apply-intent.json"))
    if intent != {"plan_sha256":c.sha256_json(actual),"package_sha256":c.sha256_json(package),"backup":binding(root/"backup.json")}:
        raise ValueError("SQL write intent differs")
    backup = body(read(root/"backup.json")); c.verify_file(backup["dump"])
    if not backup["restore_catalog_nonempty"] or backup["restore_list_exit_code"] != 0:
        raise ValueError("SQL backup gate failed")
    return c.fingerprint(before)


def prepare(parent, adjudication, link_id, quote, ports):
    saved = read_artifact(adjudication/"packet.json"); handoff = read_artifact(adjudication/"gpt-final-handoff.json")
    matches = [(item,action) for item in saved["items"] for action in handoff["actions"]
        if item["item_id"] == action["item_id"] and any(l["viewpoint_claim_link_id"] == link_id for l in item["connections"])]
    if (len(matches) != 1 or len(saved["items"]) != 1 or len(handoff["actions"]) != 1
            or matches[0][1]["verdict"] != "audit_objection_upheld"):
        raise ValueError("authorization must cover the sole exact upheld item, no partial clearance")
    item,action = matches[0]
    if len(item["connections"]) != 1: raise ValueError("ambiguous current connection")
    old = item["connections"][0]
    provisional = deepcopy(old)
    provisional.update(link_type="equivalent_full",component_locator=None,revision=old["revision"]+1)
    ident = "LR-"+c.sha256_json({"parent":binding(parent/"effective-package.json"),"before_link":old,"after_link":provisional})
    root = parent/"authorized-link-corrections"/ident
    if (root/"packet.json").exists():
        packet = body(read(root/"packet.json"))
        if (packet["authorization"]["authorization_quote"] != quote
                or packet["before_link"] != old or packet["after_link"] != provisional
                or packet["source_root"] != str(parent) or packet["adjudication_root"] != str(adjudication)):
            raise ValueError("existing approved task input differs; no new budget")
        before = c.index(read(root/"before.json")["records"])
        validate_parent(root,before,ports)
        return root
    before = ports.snapshot()
    if before.get(c.key("viewpoint_claim_links",link_id)) != old: raise ValueError("old link drift")
    new = target_postimage(old,item["claim"],before,"equivalent_full")
    original = adjudication.parent/"independent-audit/audit.json"
    from backend.pipeline.viewpoint_evidence_source_context import load_context_source
    # Complete target CVP member/source context, not only the disputed edge.
    vids = {old["viewpoint_id"]}; keys = {c.key("canonical_viewpoints",old["viewpoint_id"]),
        c.key("viewpoint_revisions",old["validated_against_viewpoint_revision_id"])}
    own_links = [p for k,p in before.items() if k.startswith("viewpoint_claim_links/")
        and p["viewpoint_id"] in vids and p["effective_state"] == "active"]
    sources = {}
    for link in own_links:
        claim = before[c.key("claims",link["claim_id"])]
        keys |= {c.key("claims",claim["claim_id"]),c.key("viewpoint_claim_links",link["viewpoint_claim_link_id"]),
            c.key("viewpoint_identity_decisions",link["decision_id"])}
        decision = before[c.key("viewpoint_identity_decisions",link["decision_id"])]
        keys.add(c.key("viewpoint_identity_candidates",decision["identity_candidate_id"]))
        for eid in claim["evidence_step_ids"]:
            evidence = before[c.key("evidence_steps",eid)]; keys.add(c.key("evidence_steps",eid))
            for fid in set(evidence.get("source_fragment_ids",[]))|({evidence["source_fragment_id"]} if evidence.get("source_fragment_id") else set()):
                fragment = before[c.key("source_fragments",fid)]; keys.add(c.key("source_fragments",fid))
                sid = fragment["source_id"]; keys.add(c.key("source_documents",sid))
                if sid not in sources:
                    path,physical,projection = load_context_source(before[c.key("source_documents",sid)])
                    sources[sid] = {"source_id":sid,"path":str(path),"file_sha256":physical,
                        "body_sha256":projection.body_sha256,"paragraphs":[{"paragraph_key":f"S{i+1:04d}","text":p["text"]} for i,p in enumerate(projection.spoken_rows)]}
    auth = {"schema_version":VERSION,"authorization_quote":quote,"human_content_approval":False,
        "automatic_future_repairs_authorized":False,"before_link_sha256":c.sha256_json(old),"after_link_sha256":c.sha256_json(new),
        "execution":"code_owned_review_apply_readback_full_audit_and_continuation"}
    packet = {"schema_version":VERSION,"source_root":str(parent),"adjudication_root":str(adjudication),
        "authorization":auth,"item":item,"handoff":handoff,"before_link":old,"after_link":new,
        "context_records":c.rows({k:before[k] for k in keys}),"sources":list(sources.values()),
        "old_audit_source_proofs":historical_audit_proofs(original,before),"original_audit":binding(original),
        "prior_bindings":[binding(adjudication/n) for n in ("packet.json","raw-gpt.json","gpt-final-handoff.json")],
        "provenance_contract":"fresh single actual Opus, actual completion time; not owner content approval or dual consensus"}
    write(root/"packet.json",packet); write(root/"before.json",{"records":c.rows(before)})
    validate_parent(root,before,ports)
    obj = adapter(); size = obj.request_bytes(packet)
    if size > 500000: raise ValueError("complete request exceeds frozen 500000 ceiling")
    write(root/"preflight.json",{"status":"passed_zero_models_zero_writes","request_bytes":size,
        "encoding":obj.request_encoding_proof(packet),"intended_operations":3})
    return root


def execute(root, context, ports):
    packet = body(read(root/"packet.json")); before = c.index(read(root/"before.json")["records"])
    if not (root/"apply-intent.json").exists():
        if ports.snapshot() != before: raise ValueError("whole Registry changed before child review")
        runner = Runner(root.parent,ReviewPorts(ports.store)); runner.root = root
        runner.call("initial_review",packet)
        raw = review_raw(root,packet); package = package_for(packet,raw,root)
        plan = json.loads(json.dumps(ports.store.plan_package(package,source_kind=VERSION).as_dict()))
        assert_plan(plan,package,before)
        write(root/"effective-package.json",package); write(root/"plan.json",plan)
        write(root/"backup.json",ports.backup(root))
        if ports.snapshot() != before: raise ValueError("whole Registry changed before child apply")
        write(root/"apply-intent.json",{"plan_sha256":c.sha256_json(plan),"package_sha256":c.sha256_json(package),"backup":binding(root/"backup.json")})
        from backend.pipeline.source_reconciliation_runtime import workspace
        with exclusive_cvp_run_lock({"global_lock_path":str(workspace().parent/"cvp-production-357/registry-cvp.lock")}):
            receipt = ports.store.apply_plan(ports.store.plan_package(package,source_kind=VERSION),
                metadata={"owner_approved_link_root":str(root)},expected_registry_fingerprint_sha256=c.fingerprint(before),
                registry_collections=sorted(c.KNOWLEDGE_COLLECTIONS))
        write(root/"apply-receipt.json",receipt)
    plan = body(read(root/"plan.json"))
    if not sql_applied(plan,ports.store): raise Stop("child intent outcome unknown; no apply retry")
    current = ports.snapshot()
    if rewind(plan,current,before) != before: raise ValueError("child full Registry reverse/readback mismatch")
    validate_child(root,ports.store,current)
    proof = child_proof(root)
    if proof["post_sha256"] != c.fingerprint(current): raise ValueError("child exact full postimage mismatch")
    write(root/"verified-result.json",proof)
    # The parent source completion consumes the child proof and a FRESH full
    # original-group audit. Original failed audit is not rewritten as pass.
    parent = Path(packet["source_root"]); frozen,old,post,parent_plan = validate_parent(root,before,ports)
    outcomes = []
    for job in audit_jobs(frozen,current):
        out = root/"full-group-audits"/c.sha256_json(job)
        if (out/"intent.json").exists(): result = ports.audit_saved(out,job["claim_ids"],job["viewpoint_ids"])
        else:
            write(out/"intent.json",job)
            result = ports.audit(out,job["claim_ids"],job["viewpoint_ids"])
        ports.validate_audit(result,job["claim_ids"],job["viewpoint_ids"])
        write(out/"result.json",result)
        outcomes.append({"job":job,"result":result,"binding":binding(out/"result.json")})
    if validate_child(root,ports.store,ports.snapshot()) != before:
        raise ValueError("whole Registry changed during full child audit")
    jobs = [o["job"] for o in outcomes]
    completion = {"status":"audited_complete","update_id":frozen["update_id"],"change_set_id":parent_plan["change_set_id"],
        "source_epoch":frozen["manifest"]["current_sources"],"audits":outcomes,
        "affected_group_keys":[j["group_key"] for j in jobs if j["group_key"]],
        "bindings":[binding(parent/n) for n in ("frozen.json","effective-package.json","plan.json","apply-intent.json","backup.json","readback.json")],
        "authorized_link_corrections":[binding(root/"verified-result.json")],"campaign_membership_changed":False,"production_resumed":False,
        "revisit_required":any(j["revisit_required"] or o["result"]["revisit_required"] for j,o in zip(jobs,outcomes)),
        "independent_audit_complete":all(j["independent_audit_complete"] and o["result"]["independent_audit_complete"] for j,o in zip(jobs,outcomes))}
    validate_completion_population(completion,frozen,post)
    write(parent/"complete.json",completion)
    from backend.pipeline.deferred_claim_cvp_continuation import run
    return run(context,parent,ports)


def initialize_normal_runtime(root, resolve=None, *, reissue_quote=None, reissue_intent=None, cfg=None):
    """Authenticate the canonical client before reserving any new call.

    Old intent-without-answer remains a hard hold even after fixing the client.
    An environment fix is not permission to reset an invocation budget.
    """
    from backend.pipeline.owner_model_reissue import directory, authorize
    review=directory(root)
    for intent in review.glob('*.call-intent.json'):
        raw=review/(intent.name.removesuffix('.call-intent.json')+'.json')
        if not raw.exists() and not reissue_quote:
            raise Stop('normal CVP invocation has no saved answer; outcome_unknown requires explicit owner direction')
    if resolve is None:
        from backend.pipeline.viewpoint_s201129_source_continuation import canonical_runtime
        resolve=canonical_runtime
    proof=resolve()
    if not proof.get('reused') or proof.get('new_model_calls')!=0:
        raise ValueError('normal CVP runtime must reuse existing authenticated identity proof')
    write(root/'runtime-proof.json',proof)
    if reissue_quote:
        if cfg is None:raise ValueError('exact normal campaign config required')
        from backend.pipeline.deferred_claim_cvp_continuation import adapters
        review=authorize(root,adapters(cfg)['proposer'],reissue_quote,reissue_intent)
        for intent in review.glob('*.call-intent.json'):
            if not (review/(intent.name.removesuffix('.call-intent.json')+'.json')).exists():
                raise Stop('authorized reissue outcome unknown; no second reissue')
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root",type=Path,required=True)
    parser.add_argument("--adjudication-root",type=Path,required=True)
    parser.add_argument("--resume-input",type=Path,required=True)
    parser.add_argument("--link-id",required=True)
    parser.add_argument("--approval-quote",required=True)
    parser.add_argument("--execute",action="store_true")
    parser.add_argument("--proposal-reissue-approval",help="explicit owner authorization for one unchanged initial proposal reissue")
    parser.add_argument("--proposal-reissue-intent",help="exact SHA of the retained unanswered invocation")
    parser.add_argument("--recover-prewrite-registry-cut",type=Path,
        help="authenticate a saved empty-collection SQL guard conflict; never retry an uncertain commit")
    parser.add_argument("--continue-saved",action="store_true",
        help="consume actual audited parent/child completion only; no child review/apply/audit replay")
    args = parser.parse_args(); load_dotenv(PROJECT/".env")
    if (args.proposal_reissue_approval or args.proposal_reissue_intent) and (
            not args.continue_saved or not args.execute
            or not args.proposal_reissue_approval or not args.proposal_reissue_intent):
        raise ValueError('once-reissue authorization requires both exact pins and saved-only execution')
    if args.recover_prewrite_registry_cut and (not args.continue_saved or not args.execute):
        raise ValueError('prewrite recovery requires saved-only execution')
    if PROJECT.name != "wkp-357-worktree-per-session": raise ValueError("#357 worktree only")
    with maintenance_lock():
        ports = ProductionPorts()
        if args.execute: ports._environment_guard()
        if args.continue_saved:
            if not args.execute:raise ValueError('continuation requires explicit execution')
            parent=args.source_root.resolve()
            complete=read(parent/'complete.json')
            context=body(read(args.resume_input)); c.verify_file(context['standing_authorization'])
            if c.Manifest.model_validate(context['manifest']).model_dump()!=body(read(parent/'frozen.json'))['manifest']:
                raise ValueError('saved continuation source task changed')
            children=complete.get('authorized_link_corrections',[])
            if len(children)!=1:raise ValueError('exact saved child completion required')
            proof=read(c.verify_file(children[0]));root=Path(children[0]['path']).parent
            packet=body(read(root/'packet.json'))
            if (packet['before_link']['viewpoint_claim_link_id']!=args.link_id
                    or packet['authorization']['authorization_quote']!=args.approval_quote
                    or packet['adjudication_root']!=str(args.adjudication_root.resolve())):
                raise ValueError('saved authorization identity changed')
            ports._environment_guard(planned_snapshot_bytes=(parent/'frozen.json').stat().st_size)
            from backend.pipeline.viewpoint_cvp_recovery_executor import load_campaign
            cfg=load_campaign(Path(context['campaign_root']))[0]
            initialize_normal_runtime(parent/'normal-cvp',reissue_quote=args.proposal_reissue_approval,
                reissue_intent=args.proposal_reissue_intent,cfg=cfg)
            from backend.pipeline.deferred_claim_cvp_continuation import run
            # run starts by proving actual SQL/source/child/audit completion;
            # the original child execute path is deliberately not called.
            print(json.dumps({'status':'saved_completion_continuation','root':str(parent)}),flush=True)
            result=run(context,parent,ports,prewrite_conflict_log=args.recover_prewrite_registry_cut)
            write(parent/'normal-cvp/owner-storage-continuation-result.json',result)
            print(json.dumps(result,ensure_ascii=False),flush=True)
            return
        root = prepare(args.source_root.resolve(),args.adjudication_root.resolve(),args.link_id,args.approval_quote,ports)
        print(json.dumps({"root":str(root),"status":"preflight_passed"}),flush=True)
        if not args.execute: return
        from backend.pipeline.viewpoint_s201129_source_continuation import canonical_runtime
        write(root/"runtime-proof.json",canonical_runtime())
        try:
            context = body(read(args.resume_input)); c.verify_file(context["standing_authorization"])
            if c.Manifest.model_validate(context["manifest"]).model_dump() != body(read(args.source_root/"frozen.json"))["manifest"]:
                raise ValueError("original Claim task/continuation context changed")
            write(root/"continuation-context.json",context)
            result = execute(root,context,ports)
            write(root/"result.json",result)
            print(json.dumps(result,ensure_ascii=False),flush=True)
        except Exception as exc:
            failure = {"error":str(exc),"automatic_retry":False,"production_resumed":False}
            if not (root/"failure.json").exists(): write(root/"failure.json",failure)
            write(root/"failures"/(c.sha256_json(failure)+".json"),failure)
            write(root/"state.json",{"status":getattr(exc,"status","proof_failure"),
                "waiting_for":getattr(exc,"waiting_for","system"),"next_action":str(exc),
                "production_resumed":False},immutable=False)
            raise


if __name__ == "__main__": main()
