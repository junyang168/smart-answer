"""Claim-local deferred repair, using the existing source/Claim transaction.

Saved diagnosis is not revision approval. No sermon/Claim/group ID is encoded
here. Physical sources, identities and genuine holds remain unchanged.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from backend.pipeline import source_reconciliation as c
from backend.api.canonical_repository.viewpoint_foundation import sha256_json, semantic_record_sha

VERSION = "wang_deferred_claim_repair_v1"
TRANSPORT = "wang_deferred_claim_json_records_v1"
HOLDS = c.HUMAN_HOLDS | {"human_hold", "withheld_unreviewed"}
CLAIM_FIELDS = {"occurrences", "evidence_step_ids", "eligible_evidence_step_ids", "context_evidence_step_ids", "withheld_evidence_step_ids"}
CONTENT_FIELDS = {"statement", "attribution", "discourse_mode", "claim_type"}
EVIDENCE_FIELDS = {"statement", "source_fragment_ids", "source_fragment_id"}
LINK_FIELDS = {"pinned_claim_revision", "component_locator", "evidence_bindings"}


class JsonRow(c.Contract):
    collection: str
    object_id: str
    payload_json: str


class JsonProposal(c.Contract):
    """Lossless JSON text for schema-open Registry records; no Any in wire schema."""
    records: list[JsonRow]
    dispositions: list[c.Disposition]
    unadmitted_candidates_json: list[str]
    sentence_dispositions: list[c.SentenceDisposition]


def decode_proposal(response):
    if "unadmitted_candidates_json" not in response:
        return response  # old read-only tests/fixtures, never a transport fallback
    wire = JsonProposal.model_validate(response)
    def unique(pairs):
        result = {}
        for k,v in pairs:
            if k in result: raise ValueError("duplicate JSON payload field")
            result[k] = v
        return result
    decode = lambda s: json.loads(s,object_pairs_hook=unique)
    return {"records":[{"collection":r.collection,"object_id":r.object_id,"payload":decode(r.payload_json)} for r in wire.records],
        "dispositions":[x.model_dump() for x in wire.dispositions],
        "unadmitted_candidates":[decode(s) for s in wire.unadmitted_candidates_json],
        "sentence_dispositions":[x.model_dump() for x in wire.sentence_dispositions]}


def verify_schema_rejection(bound, states):
    """Only explicit provider 400, no last message; never a timeout/no-answer inference."""
    failure = artifact(bound)
    root = Path(bound.path).parent
    raw = failure.get("provider_raw") or {}
    stderr = raw.get("stderr","")
    errors = []
    for line in stderr.splitlines():
        if line.startswith("ERROR: "):
            try: errors.append(json.loads(line[7:]))
            except json.JSONDecodeError: raise ValueError("unstructured rejection cannot authorize a new call")
    if (raw.get("last_message") is not None or raw.get("stdout") or not errors or
            any(e.get("status") != 400 or e.get("error",{}).get("code") != "invalid_json_schema"
                or e.get("error",{}).get("param") != "text.format.schema" for e in errors)
            or (root / "proposal.raw.json").exists() or (root / "initial_review.reservation.json").exists()):
        raise ValueError("not a proven pre-generation schema rejection")
    old = c.index(__import__('backend.pipeline.source_reconciliation_runner',fromlist=['read']).read(root / "frozen.json")["before_records"])
    if old != states: raise ValueError("Registry changed since the explicit schema rejection")
    frozen = json.loads((root / "frozen.json").read_text())
    if frozen["manifest"]["deferred_repair"].get("provider_schema_rejection") is not None:
        raise ValueError("schema repair budget already used")
    c.guard_files(frozen)
    if not (root / "proposal.reservation.json").exists(): raise ValueError("original call intent missing")
    return frozen


def artifact(bound, snapshots=None):
    value = json.loads(c.bound_bytes(bound, snapshots))
    if value.get("artifact_sha256") != sha256_json({k:v for k,v in value.items() if k != "artifact_sha256"}):
        raise ValueError("deferred diagnostic artifact SHA drift")
    return value


def diagnostic_adapter(provider, model):
    from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
    from backend.pipeline.viewpoint_deferred_source_check import PROMPT, Findings
    from backend.pipeline.viewpoint_resolution_runtime import subscription_client
    result = StructuredJsonReviewerAdapter(client=subscription_client(provider,model,"high"),prompt=PROMPT,
        response_model=Findings,schema_name="wang_deferred_source_findings_v1")
    result.max_request_bytes = 200000
    return result


def route(actions):
    if not actions or any(not a.get("models_agree") for a in actions):
        raise ValueError("deferred source disagreement/human judgement is not automatic Claim authority")
    routes = {a["next_action"] for a in actions}
    if routes == {"claim_evidence_repair_authorization_required"}: return "evidence_binding"
    if routes == {"claim_source_review_required"}: return "claim_statement"
    if routes == {"reviewed_cvp_repair_required"}:
        raise ValueError("Claim evidence is sufficient; return to normal CVP resolution, not Claim rewrite")
    raise ValueError("mixed/unknown deferred causes need explicit disposition")


def problem_identity(packet, kind):
    """Stable across roots and incidental diagnostic/context changes."""
    return sha256_json({"version":VERSION,"kind":kind,"claims":sorted({
        (i["claim_id"],i["claim_revision_sha256"],i["source_binding"]["file_sha256"])
        for i in packet["items"]})})


def verify_diagnosis(repair, states, sid, snapshots=None):
    from backend.pipeline.viewpoint_deferred_source_check import validate_findings
    from backend.pipeline.viewpoint_resolution_runtime import read_saved_model_response
    packet = artifact(repair.investigation_packet,snapshots)
    handoff = artifact(repair.handoff,snapshots)
    if (packet.get("schema_version") != "wang_deferred_source_investigation_v1"
            or handoff.get("schema_version") != "wang_deferred_source_handoff_v1"
            or handoff["packet_sha256"] != packet["artifact_sha256"]
            or handoff["status"] != "investigated_not_resolved" or handoff["group_complete"] is not False
            or handoff["master_data_mutations"] != 0 or route(handoff["actions"]) != repair.kind):
        raise ValueError("not an intact saved read-only deferred diagnosis")
    item_ids = [i["item_id"] for i in packet["items"]]
    if len(item_ids) != len(set(item_ids)) or set(repair.claim_ids) != {i["claim_id"] for i in packet["items"]}:
        raise ValueError("deferred Claim/component denominator drift")
    reviews = []
    for bound,provider,model in ((repair.raw_gpt,"codex","gpt-6.1-sol"),(repair.raw_opus,"claude","claude-opus-5-5")):
        raw = artifact(bound,snapshots)
        response = read_saved_model_response(diagnostic_adapter(provider,model),packet,Path(bound.path))
        if response != raw["response"] or raw.get("cache_handoff") is not None:
            raise ValueError("saved diagnostic actual raw/config/encoding/call identity drift")
        reviews.append(validate_findings(response,packet))
    doc = states[c.key("source_documents",sid)]
    source = c.source_snapshot({"path":doc["source_path"],"file_sha256":doc["source_file_sha256"]},snapshots)
    actions = []
    for item in packet["items"]:
        claim = states[c.key("claims",item["claim_id"])]
        if (claim.get("review_status") in HOLDS or semantic_record_sha(claim) != item["claim_revision_sha256"]
                or claim["statement"] != item["statement"] or item["source_binding"] != {
                    "path":doc["source_path"],"file_sha256":doc["source_file_sha256"],"body_sha256":doc["source_body_sha256"]}):
            raise ValueError("diagnostic current Claim/own source pin/human hold drift")
        for p in item["source_paragraphs"]:
            text = source["paragraphs"].get(p["paragraph_key"],{}).get("text")
            if p["text"] != text or p["text_sha256"] != sha256_json(text):
                raise ValueError("diagnostic paragraph is not exact own physical source")
        for e in item["evidence"]:
            step = states[c.key("evidence_steps",e["evidence_step_id"])]
            fragment = states[c.key("source_fragments",e["source_fragment_id"])]
            if (e["evidence_step_id"] not in claim["evidence_step_ids"] or claim["claim_id"] not in step["produced_claim_ids"]
                    or step["statement"] != e["evidence_statement"] or fragment["source_id"] != sid
                    or fragment["paragraph_key"] != e["paragraph_key"] or fragment["verbatim_excerpt"] != e["verbatim_excerpt"]
                    or e["source_fragment_id"] not in set(step.get("source_fragment_ids",[])) | {step.get("source_fragment_id")}):
                raise ValueError("diagnostic current Evidence/fragment/reciprocal chain drift")
        left,right = (review[item["item_id"]] for review in reviews)
        expected = "source_support_without_bound_evidence" if repair.kind == "evidence_binding" else "source_insufficient"
        if left["diagnosis"] != expected or right["diagnosis"] != expected:
            raise ValueError("diagnostic models do not agree on the selected Claim repair")
        for f in (left,right):
            if f["exact_quote"] and source["paragraphs"][f["paragraph_key"]]["text"].count(f["exact_quote"]) != 1:
                raise ValueError("diagnostic quotation is ambiguous")
        actions.append({"item_id":item["item_id"],"models_agree":True,
            "next_action":"claim_evidence_repair_authorization_required" if repair.kind == "evidence_binding" else "claim_source_review_required",
            "gpt":left,"opus":right})
    if handoff["actions"] != actions: raise ValueError("handoff differs from actual saved verdicts")
    return {"items":packet["items"],"actions":actions,"diagnosis_is_not_revision_approval":True}


def permissions(states,sid,claim_ids,kind="evidence_binding"):
    claim_fields = CLAIM_FIELDS | (CONTENT_FIELDS if kind == "claim_statement" else set())
    result = [c.Permission(collection="claims",object_id=cid,fields=sorted(claim_fields)) for cid in sorted(claim_ids)]
    # Current Registry qualifies Evidence on the Evidence record; historical
    # Claim bucket fields are optional and never a substitute for that gate.
    eligible = {eid for cid in claim_ids for eid in states[c.key("claims",cid)].get("evidence_step_ids",[])
        if states[c.key("evidence_steps",eid)].get("support_eligibility") in {"eligible","eligible_candidate","eligible_with_label"}}
    for eid in sorted(eligible):
        step = states[c.key("evidence_steps",eid)]
        if step.get("valid_for_identity_review") is False or step.get("review_status") in HOLDS: continue
        result.append(c.Permission(collection="evidence_steps",object_id=eid,fields=sorted(EVIDENCE_FIELDS)))
    if not any(p.collection == "evidence_steps" for p in result):
        raise ValueError("no eligible existing Evidence can be repaired without clearing a hold")
    for k,p in sorted(states.items()):
        if k.startswith("viewpoint_claim_links/") and p["claim_id"] in claim_ids and p.get("effective_state") == "active":
            result.append(c.Permission(collection="viewpoint_claim_links",object_id=p["viewpoint_claim_link_id"],
                fields=sorted(LINK_FIELDS | ({"link_type"} if kind == "claim_statement" else set()))))
    result.append(c.Permission(collection="source_fragments",object_id="FR-deferred-allocation-slot",create=True,
        parent_id=sid,fields=sorted(c.KNOWLEDGE_COLLECTIONS["source_fragments"][0].model_fields)))
    return result


def validate_authority(manifest,states,snapshots=None):
    repair = manifest.deferred_repair
    if len(manifest.current_sources) != 1 or manifest.current_sources[0] != manifest.before_source or manifest.prior_updates:
        raise ValueError("deferred Claim repair cannot change a source file or migrate another epoch")
    auth = json.loads(c.bound_bytes(manifest.authorization,snapshots))
    if auth.get("deferred_repair") != repair.model_dump() or auth.get("standing_policy") != VERSION:
        raise ValueError("exact deferred mode/diagnosis is not bound to owner execution authority")
    if manifest.permissions != permissions(states,manifest.source_id,repair.claim_ids,repair.kind):
        raise ValueError("deferred permission set exceeds exact Claim/Evidence/link impact scope")
    if repair.provider_schema_rejection:
        old = verify_schema_rejection(repair.provider_schema_rejection,states)
        old_repair = old["manifest"]["deferred_repair"]
        if any(old_repair.get(k) != repair.model_dump().get(k) for k in ("kind","claim_ids","investigation_packet","handoff","raw_gpt","raw_opus")):
            raise ValueError("schema correction changed the original deferred diagnosis/target")
    return verify_diagnosis(repair,states,manifest.source_id,snapshots)


def validate_postimages(frozen,post):
    before = c.index(frozen["before_records"])
    manifest = c.Manifest.model_validate(frozen["manifest"])
    repair = manifest.deferred_repair
    roots = set(repair.claim_ids)
    for k,old in before.items():
        new = post[k]
        if old == new: continue
        col,oid = c.split_key(k)
        if old.get("review_status") in HOLDS: raise ValueError("deferred repair cannot clear a real hold/ruling")
        if col == "claims":
            if oid not in roots or (repair.kind == "evidence_binding" and any(new.get(f) != old.get(f) for f in CONTENT_FIELDS)):
                raise ValueError("deferred Claim repair changed another identity or an unauthorized statement")
            for f in ("evidence_step_ids","context_evidence_step_ids","withheld_evidence_step_ids","eligible_evidence_step_ids"):
                if new.get(f,[]) != old.get(f,[]): raise ValueError("deferred repair cannot borrow/drop/promote Evidence")
            for occurrence in old["occurrences"]:
                matches = [o for o in new["occurrences"] if {a:b for a,b in o.items() if a != "anchors"} ==
                    {a:b for a,b in occurrence.items() if a != "anchors"}]
                strip_basis = lambda a: {f:v for f,v in a.items() if f != "evidence_independence"}
                if len(matches) != 1 or any(strip_basis(a) not in [strip_basis(n) for n in matches[0]["anchors"]] for a in occurrence["anchors"]):
                    raise ValueError("deferred repair dropped an old occurrence/anchor")
        elif col == "evidence_steps":
            if old.get("valid_for_identity_review") is False or not set(old.get("source_fragment_ids",[])) <= set(new.get("source_fragment_ids",[])):
                raise ValueError("deferred repair cannot promote held Evidence or drop old quotes")
        elif col == "viewpoint_claim_links":
            if new["claim_id"] not in roots or new["viewpoint_id"] != old["viewpoint_id"] or new["effective_state"] != old["effective_state"]:
                raise ValueError("deferred repair cannot change a CVP endpoint/identity or retire a link")
            if repair.kind == "evidence_binding" and (new["link_type"] != old["link_type"] or
                    new.get("component_locator",{}).get("canonical_spans") != old.get("component_locator",{}).get("canonical_spans")):
                raise ValueError("evidence binding repair cannot rewrite a predicate/locator")
            if new["link_type"] != old["link_type"] and not (old["link_type"].startswith("equivalent_") and new["link_type"] == "supports"):
                raise ValueError("Claim correction permits only independently justified equivalent-to-supports, not arbitrary relations")
        else: raise ValueError("deferred Claim repair changed an unapproved consumer: " + k)
    for k in post.keys() - before.keys():
        p = post[k]
        if not k.startswith("source_fragments/") or p["source_id"] != manifest.source_id or p["paragraph_key"] not in manifest.authorized_paragraph_keys:
            raise ValueError("new deferred quote outside own licensed source paragraphs")
    for k,link in post.items():
        if k.startswith("viewpoint_claim_links/") and link["claim_id"] in roots and link.get("effective_state") == "active":
            if link["pinned_claim_revision"] != post[c.key("claims",link["claim_id"])]["revision"]:
                raise ValueError("CVP member remains pinned to the old Claim")
    if repair.kind == "evidence_binding":
        for item,action in zip(frozen["deferred_diagnosis"]["items"],frozen["deferred_diagnosis"]["actions"],strict=True):
            claim = post[c.key("claims",item["claim_id"])]
            for finding in (action["gpt"],action["opus"]):
                covered = False
                for eid in claim["evidence_step_ids"]:
                    step = post[c.key("evidence_steps",eid)]
                    if step.get("support_eligibility") not in {"eligible","eligible_candidate","eligible_with_label"}: continue
                    for fid in set(step.get("source_fragment_ids",[])) | ({step["source_fragment_id"]} if step.get("source_fragment_id") else set()):
                        fragment = post[c.key("source_fragments",fid)]
                        if (fragment["source_id"] != manifest.source_id or fragment["paragraph_key"] != finding["paragraph_key"]
                                or finding["exact_quote"] not in fragment["verbatim_excerpt"]): continue
                        covered |= any(a.get("evidence_id") == eid and a.get("paragraph_key") == fragment["paragraph_key"]
                            and a.get("proposed_highlight",{}).get("text") == fragment["verbatim_excerpt"]
                            for o in claim["occurrences"] for a in o["anchors"])
                if not covered: raise ValueError("deferred support is still not bound through eligible Evidence and a Claim anchor")


def normalize_proposal(frozen,response):
    """Declared hash/default computation BEFORE independent Opus review."""
    proposed = c.Proposal.model_validate(decode_proposal(response))
    data = c.index(proposed.records)
    source = frozen["after"][0]
    for k,p in data.items():
        if not k.startswith("source_fragments/"): continue
        if p["source_id"] != frozen["manifest"]["source_id"]:
            raise ValueError("cannot normalize a foreign source quote")
        text = source["paragraphs"].get(p.get("paragraph_key"),{}).get("text","")
        quote = p.get("verbatim_excerpt","")
        if not quote or text.count(quote) != 1: raise ValueError("new source fragment is not exact/unique")
        p.update(source_sha256=source["body_sha256"],anchor_state="source_version_bound",paragraph_text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            verbatim_excerpt_sha256=hashlib.sha256(quote.encode()).hexdigest())
    for k,p in list(data.items()):
        if k.startswith("claims/"):
            # New long quotes can contain an existing short context anchor.
            # Retain every old quote/role, but count connected overlaps once.
            for occurrence in p.get("occurrences",[]):
                spans = []
                for anchor in occurrence.get("anchors",[]):
                    para = anchor.get("paragraph_key")
                    text = source["paragraphs"].get(para,{}).get("text","")
                    highlight = anchor.get("proposed_highlight",{})
                    quote = highlight.get("text") if isinstance(highlight,dict) else highlight
                    if not quote or text.count(quote) != 1: raise ValueError("Claim anchor not unique in own source")
                    start = text.index(quote)
                    spans.append((para,start,start+len(quote),anchor))
                remaining = set(range(len(spans)))
                while remaining:
                    group = {min(remaining)}; queue = list(group)
                    while queue:
                        i = queue.pop(); para,low,high,_ = spans[i]
                        for j in remaining-group:
                            pkey,l,h,_ = spans[j]
                            if pkey == para and low < h and l < high: group.add(j); queue.append(j)
                    remaining -= group
                    if len(group) < 2: continue
                    basis = "BASIS-"+sha256_json(sorted((spans[i][0],spans[i][1],spans[i][2],spans[i][3].get("evidence_id")) for i in group))[:20]
                    representative = min(group,key=lambda i: (-(spans[i][2]-spans[i][1]),i))
                    for i in group:
                        spans[i][3]["evidence_independence"] = {"basis_group_id":basis,"count_once_per_basis_group":True,"is_basis_representative":i == representative}
            data[k] = c.KNOWLEDGE_COLLECTIONS["claims"][0].model_validate(p).model_dump(mode="json")
    current = {**c.index(frozen["before_records"]),**data}
    for k,p in data.items():
        if k.startswith("viewpoint_claim_links/") and p.get("component_locator"):
            p["component_locator"]["claim_sha256"] = semantic_record_sha(current[c.key("claims",p["claim_id"])])
    return c.Proposal.model_validate({**proposed.model_dump(),"records":c.rows(data)})
