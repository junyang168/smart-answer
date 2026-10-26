"""Write a settled topic run's viewpoint relations to the store (#456, CVP 3.0 solution doc §9).

Input is a finished topic run: the final proposal, the reviewer's final
verdicts, and the owner's decisions. Only items the reviewer accepted and the
owner did not drop are written. `--dry-run` writes the plan and a readable
list to the run directory and touches nothing; `--execute` needs a backup made
just before and writes one SQL transaction under a topic run, then reads back.

Links and summaries are a later step; this module writes relations only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore, database_url_from_env, sha256_json
from backend.api.canonical_repository.transaction_validation import TransactionValidation
from backend.pipeline.cvp_sql_commit import binding, pin_rows, pins
from backend.pipeline.topic_cvp_pilot import assign_ids, build_packet

SOURCE_KIND = "wang_cvp_topic_v3"


def _load(path: Path) -> Any:
    return json.loads(Path(path).read_text())


def relation_records(run: Path, proposal: dict, verdicts: dict, decisions: dict, review_raw: Path,
                     packet: dict, private: dict, current: dict) -> tuple[list[dict], list[str]]:
    """Accepted, not-dropped new relations as complete viewpoint_relations records."""

    ids = {**private["ref_to_id"], **{slot: vid for slot, (vid, _) in new_viewpoint_ids(run, proposal).items()}}
    cards = {c["ref"]: c for c in packet["scope_claims"]}
    rel_by_ref = {r["ref"]: r for r in packet["claim_relations"]}
    titles = {s["ref"]: s["title"] for s in packet["source_index"]}
    dropped = set(decisions.get("drop_relations", []))
    review_sha = binding(review_raw)["file_sha256"]
    records, skipped = [], []
    for r in proposal["relations"]:
        if r["item"] in dropped:
            skipped.append(f"{r['item']}：负责人决定不记")
            continue
        if (verdicts.get(r["item"]) or {}).get("verdict") != "accept":
            skipped.append(f"{r['item']}：复核未通过")
            continue
        src, tgt = ids[r["from"]], ids[r["to"]]
        if src not in current or tgt not in current:
            # A new CVP that was not created (not accepted) cannot carry a relation.
            skipped.append(f"{r['item']}：一端的新观点没有建立")
            continue
        if r["type"] == "tensions_with" and src > tgt:
            src, tgt = tgt, src
        claim_relation_ids = sorted(ids[x] for x in r["claim_relations"])
        claims = {ids[rel_by_ref[x][end]] for x in r["claim_relations"] for end in ("from", "to")}
        quoted = []
        for q in r["quotes"]:
            quoted.append(f"「{q['text']}」（{titles.get(q['source'], q['source'])}，第 {q['block']} 段）")
            # Claims whose own evidence sits in the quoted block and that link to either end.
            for ref, card in cards.items():
                if {r["from"], r["to"]} & set(card["viewpoint_links"]) and any(
                        q["source"] == e["source"] and q["block"] in e["blocks"]
                        for step in card["evidence"] for e in step["quotes"]):
                    claims.add(ids[ref])
        reason = r["explanation"] + ("　原话：" + "；".join(quoted) if quoted else "")
        records.append({
            "viewpoint_relation_id": "CVP3-q-" + sha256_json({"run": str(run), "item": r["item"], "from": src,
                                                              "to": tgt, "type": r["type"]})[:24],
            "schema_version": "wang_viewpoint_relation_v1",
            "source_viewpoint_id": src, "target_viewpoint_id": tgt,
            "validated_source_viewpoint_revision_id": current[src],
            "validated_target_viewpoint_revision_id": current[tgt],
            "relation_type": r["type"], "reason": reason,
            "supporting_claim_ids": sorted(claims), "supporting_claim_relation_ids": claim_relation_ids,
            "temporal_assertion": None, "effective_state": "active",
            "review_provenance": {"review_artifact_sha256": review_sha, "basis_identity_decision_ids": []},
            "review_status": "system_approved", "visibility": "internal", "revision": 1,
        })
    return records, skipped


def write_list(records: list[dict], skipped: list[str], statements: dict[str, str]) -> str:
    lines = [f"# 写入清单：新观点关系 {len(records)} 条", ""]
    for n, r in enumerate(records, 1):
        lines += [f"{n}. **{r['relation_type']}**",
                  f"   - 从：{statements.get(r['source_viewpoint_id'], r['source_viewpoint_id'])}",
                  f"   - 到：{statements.get(r['target_viewpoint_id'], r['target_viewpoint_id'])}",
                  f"   - 依据：{r['reason']}",
                  f"   - Claim 关系 {len(r['supporting_claim_relation_ids'])} 条，Claim {len(r['supporting_claim_ids'])} 条", ""]
    lines += ["## 不写", ""] + [f"- {s}" for s in skipped]
    return "\n".join(lines) + "\n"


MEMBER_LINKS = ("equivalent_full", "equivalent_component")
# Claims on hold are not used as members or as evidence until they are reviewed.
HELD_CLAIMS = ("human_review_required", "human_hold", "withheld_unreviewed", "rejected", "superseded")


def held_claims(store, claim_ids) -> set[str]:
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT object_id FROM wang_knowledge.objects WHERE collection='claims' AND object_id=ANY(%s)
                       AND (retired_at IS NOT NULL OR review_status=ANY(%s))""", (sorted(claim_ids), list(HELD_CLAIMS)))
        return {row[0] for row in cur.fetchall()}
ID_FIELDS = {"viewpoint_relations": "viewpoint_relation_id", "viewpoint_identity_candidates": "identity_candidate_id",
             "viewpoint_identity_decisions": "identity_decision_id", "viewpoint_claim_links": "viewpoint_claim_link_id",
             "canonical_viewpoints": "viewpoint_id", "viewpoint_revisions": "viewpoint_revision_id"}
MODEL_IDS = ["claude-opus-5-5", "gpt-6.1-sol"]


def new_viewpoint_ids(run: Path, proposal: dict) -> dict[str, tuple[str, str]]:
    """Stable ids for the run's new CVP slots: slot -> (viewpoint_id, first revision id)."""
    out = {}
    for v in proposal.get("viewpoints", []):
        if v["action"] == "new":
            key = sha256_json({"run": str(run), "slot": v["viewpoint"], "statement": v["statement"]})
            out[v["viewpoint"]] = ("CVP3-v-" + key[:24], "CVP3-r-" + key[:24])
    return out


def _locator(claim: dict, component: str) -> dict:
    from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha
    start = claim["statement"].index(component)
    return {"statement_component": component, "claim_sha256": semantic_record_sha(claim),
            "canonical_spans": [{"start_char": start, "end_char": start + len(component), "exact_text": component}]}


def link_summary_package(run: Path, proposal: dict, verdicts: dict, packet: dict, private: dict, store,
                         proposal_raw: Path, review_raw: Path) -> tuple[dict, list[str], list[str]]:
    """New CVPs, reviewed link changes on any viewpoint, and accepted summaries, as complete records.

    A new CVP is written with its first revision, an identity candidate and a
    create_new decision covering its links. A member link takes the proposal's
    member_type and component; promoting an existing link keeps its component
    when the proposal gives none.
    """
    from datetime import datetime, timezone
    from backend.api.canonical_repository.cvp_management import summary_basis_for_records

    new_ids = new_viewpoint_ids(run, proposal)
    ids = {**private["ref_to_id"], **{slot: vid for slot, (vid, _) in new_ids.items()}}
    review_sha, proposal_sha = binding(review_raw)["file_sha256"], binding(proposal_raw)["file_sha256"]
    packet_sha = binding(run / "packet.json")["file_sha256"]
    now = datetime.now(timezone.utc).isoformat()
    accepted = lambda key: (verdicts.get(key) or {}).get("verdict") == "accept"
    new_views = {v["viewpoint"]: v for v in proposal["viewpoints"] if v["action"] == "new" and accepted("VP:" + v["viewpoint"])}
    changes, notes = [], []
    held = held_claims(store, {private["ref_to_id"][d["claim"]] for d in proposal["claim_dispositions"]})
    with store.connect() as conn, conn.cursor() as cur:
        for d in proposal["claim_dispositions"]:
            if d["disposition"] not in ("member", "supports", "qualifies", "extends"):
                continue
            label = f"{d['claim']} → {d['viewpoint']} {d['disposition']}"
            claim_id = private["ref_to_id"][d["claim"]]
            if d["viewpoint"] in new_ids and d["viewpoint"] not in new_views:
                notes.append(f"{label}：新观点没有通过复核，不建")
                continue
            view_id = ids.get(d["viewpoint"])
            cur.execute("""SELECT payload FROM wang_knowledge.objects WHERE collection='viewpoint_claim_links'
                           AND retired_at IS NULL AND payload->>'effective_state'='active'
                           AND payload->>'claim_id'=%s AND payload->>'viewpoint_id'=%s""", (claim_id, view_id))
            existing = [row[0] for row in cur.fetchall()]
            kinds = {"member" if l["link_type"] in MEMBER_LINKS else l["link_type"] for l in existing}
            if d["disposition"] in kinds:
                continue
            if claim_id in held:
                notes.append(f"{label}：这条 Claim 在等人工审核，暂不连接")
            elif len(existing) > 1:
                notes.append(f"{label}：现有 {len(existing)} 条连接，改哪一条不明确，暂不写")
            else:
                changes.append((d, claim_id, view_id, existing[0] if existing else None))
        cur.execute("SELECT object_id, payload FROM wang_knowledge.objects WHERE collection='claims' AND object_id=ANY(%s)",
                    ([c for _, c, _, _ in changes],))
        claims = dict(cur.fetchall())
    package: dict[str, list] = {"viewpoint_identity_candidates": [], "viewpoint_identity_decisions": [],
                                "viewpoint_revisions": [], "viewpoint_claim_links": [], "canonical_viewpoints": []}
    new_view_ids = {ids[slot] for slot in new_views}
    existing_changed = {view_id for _, _, view_id, _ in changes if view_id not in new_view_ids}
    summaries = {ids[v["viewpoint"]]: v["summary"] for v in proposal["viewpoints"]
                 if v["summary"] and v["viewpoint"].startswith("V") and accepted("VP:" + v["viewpoint"])}
    affected = sorted(existing_changed | set(summaries))
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT payload->>'claim_id' FROM wang_knowledge.objects WHERE collection='viewpoint_claim_links'
                       AND retired_at IS NULL AND payload->>'viewpoint_id'=ANY(%s)""", (affected,))
        member_claims = sorted({row[0] for row in cur.fetchall()} | {c for _, c, _, _ in changes})
    context = store.read_cvp_context(member_claims, viewpoint_ids=affected)
    payloads = {key: row["payload"] for key, row in context.items() if not row.get("retired")}
    viewpoint_rows = {key[1]: view for key, view in payloads.items() if key[0] == "canonical_viewpoints"}

    def decision(candidate_claims, view_id, action, fingerprint, link_decisions, reason, candidate_views):
        candidate_id = "VIC-" + sha256_json({"claims": candidate_claims, "viewpoints": candidate_views, "relations": [],
                                             "action": action, "blockers": [], "coverage_snapshot_id": None,
                                             "generation_fingerprint": fingerprint,
                                             "scope_manifest_sha256": packet_sha})[:20]
        decision_id = "CVP3-decision-" + fingerprint[:24]
        package["viewpoint_identity_candidates"].append({
            "identity_candidate_id": candidate_id, "schema_version": "wang_viewpoint_identity_candidate_v1",
            "candidate_claim_ids": candidate_claims, "candidate_viewpoint_ids": candidate_views, "seed_relation_ids": [],
            "proposed_action": action, "proposed_proposition_signature": None, "coverage_snapshot_id": None,
            "scope_manifest_sha256": packet_sha, "blocker_codes": [], "generation_fingerprint": fingerprint,
            "review_status": "candidate", "visibility": "internal", "revision": 1})
        package["viewpoint_identity_decisions"].append({
            "identity_decision_id": decision_id, "schema_version": "wang_viewpoint_identity_decision_v1",
            "identity_candidate_id": candidate_id, "decision": action, "resolved_viewpoint_id": view_id,
            "claim_link_decisions": link_decisions, "proposition_unit_link_decisions": [],
            "reviewer_kind": "system", "reviewer_id": "wang_cvp_topic_v3", "approval_basis": "dual_model_consensus",
            "reason": reason, "input_sha256": fingerprint, "review_artifact_sha256": review_sha,
            "policy_version": "wang_cvp_topic_v3", "reviewer_model_ids": MODEL_IDS,
            "semantic_call_artifact_sha256s": sorted([proposal_sha, review_sha]),
            "created_at": now, "review_status": "system_approved", "visibility": "internal", "revision": 1})
        return candidate_id, decision_id

    def link_for(d, claim_id, view_id, revision_id, decision_id, old):
        link_type, locator = d["disposition"], None
        if link_type == "member":
            if d.get("member_type") in MEMBER_LINKS:
                link_type = d["member_type"]
                if link_type == "equivalent_component":
                    locator = _locator(claims[claim_id], d["component"])
            else:
                link_type = "equivalent_component" if (old or {}).get("component_locator") else "equivalent_full"
        if old:
            link = {**old, "link_type": link_type, "decision_id": decision_id, "revision": int(old["revision"]) + 1}
            if locator:
                link["component_locator"] = locator
            elif link_type == "equivalent_full":
                # A whole-Claim membership has no locator.
                link.pop("component_locator", None)
            return link
        return {"viewpoint_claim_link_id": "CVP3-l-" + sha256_json({"run": str(run), "claim": claim_id, "viewpoint": view_id,
                                                                    "link_type": link_type})[:24],
                "schema_version": "wang_viewpoint_claim_link_v2", "viewpoint_id": view_id,
                "validated_against_viewpoint_revision_id": revision_id, "claim_id": claim_id,
                "pinned_claim_revision": int(claims[claim_id]["revision"]), "link_type": link_type,
                **({"component_locator": locator} if locator else {}),
                "supporting_relation_ids": [], "evidence_bindings": [], "occurrence_refs": [],
                "decision_id": decision_id, "effective_state": "active",
                "review_status": "system_approved", "visibility": "internal", "revision": 1}

    # New CVPs: one candidate and one create_new decision per CVP, covering all its links.
    for slot, v in new_views.items():
        view_id, revision_id = new_ids[slot]
        mine = [(d, c, vid, old) for d, c, vid, old in changes if vid == view_id]
        links = [link_for(d, c, view_id, revision_id, None, old) for d, c, vid, old in mine]
        fingerprint = sha256_json({"run": str(run), "slot": slot, "statement": v["statement"]})
        candidate_id, decision_id = decision(sorted({l["claim_id"] for l in links}), view_id, "create_new", fingerprint,
                                             [{"claim_id": l["claim_id"], "link_type": l["link_type"]} for l in links],
                                             v["reason"] or "topic run new viewpoint", [])
        for link in links:
            link["decision_id"] = decision_id
            package["viewpoint_claim_links"].append(link)
            payloads["viewpoint_claim_links", link["viewpoint_claim_link_id"]] = link
        revision = {"viewpoint_revision_id": revision_id, "schema_version": "wang_viewpoint_revision_v1",
                    "viewpoint_id": view_id, "revision_number": 1, "core_proposition": v["statement"],
                    "proposition_signature": v["signature"],
                    "attribution_subject": "professor", "representation_kind": "editorial_normalization_of_source_claims",
                    "not_a_direct_quote": True,
                    "scope": {"scripture_scope": v["scripture_scope"], "audience_scope": [], "historical_scope": []},
                    "provenance": {"basis_identity_decision_ids": [decision_id], "review_artifact_sha256": review_sha},
                    "approved_by": "wang_cvp_topic_v3", "approved_at": now, "supersedes_revision_id": None,
                    "editorial_aliases": [], "review_status": "system_approved", "visibility": "internal", "revision": 1}
        head = {"viewpoint_id": view_id, "schema_version": "wang_canonical_viewpoint_v1", "current_revision_id": revision_id,
                "identity_status": "active", "created_from_candidate_id": candidate_id, "redirect_to_viewpoint_id": None,
                "summary": v["summary"], "review_status": "system_approved", "visibility": "internal", "revision": 1}
        package["viewpoint_revisions"].append(revision)
        payloads["viewpoint_revisions", revision_id] = revision
        payloads["canonical_viewpoints", view_id] = head
        for c in {l["claim_id"] for l in links}:
            payloads.setdefault(("claims", c), claims[c])
        head["summary_basis"] = summary_basis_for_records(payloads, view_id)
        package["canonical_viewpoints"].append(head)
        notes.append(f"新建观点 {slot}：{v['statement']}（{len(links)} 条连接）")

    # Link changes on existing CVPs: one match_existing decision per link.
    for d, claim_id, view_id, old in changes:
        if view_id in new_view_ids:
            continue
        fingerprint = sha256_json({"run": str(run), "claim": claim_id, "viewpoint": view_id, "link_type": d["disposition"]})
        link = link_for(d, claim_id, view_id, viewpoint_rows[view_id]["current_revision_id"], None, old)
        _, decision_id = decision([claim_id], view_id, "match_existing", fingerprint,
                                  [{"claim_id": claim_id, "link_type": link["link_type"]}],
                                  d["note"] or "topic run disposition", [view_id])
        link["decision_id"] = decision_id
        package["viewpoint_claim_links"].append(link)
        payloads["viewpoint_claim_links", link["viewpoint_claim_link_id"]] = link
    for view_id in affected:
        view = viewpoint_rows[view_id]
        summary = summaries.get(view_id, view.get("summary"))
        package["canonical_viewpoints"].append({**view, "summary": summary, "revision": int(view["revision"]) + 1,
                                                "summary_basis": summary_basis_for_records(payloads, view_id)})
    return {k: v for k, v in package.items() if v}, notes, affected


def held_cleanup_package(run: Path, store) -> tuple[dict, list[str]]:
    """Undo, on this run's own writes, any reliance on a Claim that is on hold.

    Links to a held Claim are withdrawn (effective_state retired); a held Claim
    is dropped from relation evidence; affected viewpoints get their member
    basis recomputed. Nothing the run did not write is touched.
    """
    from backend.api.canonical_repository.cvp_management import summary_basis_for_records

    out = run / "write"
    written_links = [l["viewpoint_claim_link_id"] for l in _load(out / "links-package.json").get("viewpoint_claim_links", [])]
    written_relations = [r["viewpoint_relation_id"] for r in _load(out / "relations-package.json")["viewpoint_relations"]]
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT object_id, payload FROM wang_knowledge.objects WHERE collection='viewpoint_claim_links' AND object_id=ANY(%s) AND retired_at IS NULL", (written_links,))
        links = dict(cur.fetchall())
        cur.execute("SELECT object_id, payload FROM wang_knowledge.objects WHERE collection='viewpoint_relations' AND object_id=ANY(%s) AND retired_at IS NULL", (written_relations,))
        relations = dict(cur.fetchall())
    held = held_claims(store, {l["claim_id"] for l in links.values()} | {c for r in relations.values() for c in r["supporting_claim_ids"]})
    package: dict[str, list] = {"viewpoint_claim_links": [], "viewpoint_relations": [], "canonical_viewpoints": []}
    notes = []
    for link in links.values():
        if link["claim_id"] in held and link["effective_state"] == "active":
            package["viewpoint_claim_links"].append({**link, "effective_state": "retired", "revision": int(link["revision"]) + 1})
            notes.append(f"撤回连接 {link['claim_id']} → {link['viewpoint_id']}（{link['link_type']}）")
    for rel in relations.values():
        kept = [c for c in rel["supporting_claim_ids"] if c not in held]
        if kept != rel["supporting_claim_ids"]:
            package["viewpoint_relations"].append({**rel, "supporting_claim_ids": kept, "revision": int(rel["revision"]) + 1})
            notes.append(f"关系 {rel['viewpoint_relation_id']} 的证据去掉 {sorted(set(rel['supporting_claim_ids']) - set(kept))}")
    affected = sorted({l["viewpoint_id"] for l in package["viewpoint_claim_links"]})
    if affected:
        with store.connect() as conn, conn.cursor() as cur:
            cur.execute("""SELECT DISTINCT payload->>'claim_id' FROM wang_knowledge.objects WHERE collection='viewpoint_claim_links'
                           AND retired_at IS NULL AND payload->>'viewpoint_id'=ANY(%s)""", (affected,))
            member_claims = sorted(row[0] for row in cur.fetchall())
        payloads = {k: r["payload"] for k, r in store.read_cvp_context(member_claims, viewpoint_ids=affected).items() if not r.get("retired")}
        for link in package["viewpoint_claim_links"]:
            payloads["viewpoint_claim_links", link["viewpoint_claim_link_id"]] = link
        for view_id in affected:
            view = payloads["canonical_viewpoints", view_id]
            package["canonical_viewpoints"].append({**view, "revision": int(view["revision"]) + 1,
                                                    "summary_basis": summary_basis_for_records(payloads, view_id)})
            notes.append(f"观点 {view_id} 重算成员依据")
    return {k: v for k, v in package.items() if v}, notes


def relation_fix_package(run: Path, proposal: dict, verdicts: dict, decisions: dict, review_raw: Path,
                         packet: dict, private: dict, store) -> tuple[dict, list[str]]:
    """Retire existing relations the run found wrong, and record their reviewed replacements.

    `decisions["relation_fixes"]` names each existing relation (E ref) and its
    replacement (or none). Only checks the reviewer accepted are applied; the
    replacement carries the check's own evidence.
    """
    ids = private["ref_to_id"]
    checks = {c["relation"]: c for c in proposal["existing_relation_checks"]}
    # E refs are numbered when a packet is built, so after any relation write
    # they shift. Resolve them from the packet the run was reviewed on, by ends and type.
    existing = {e["ref"]: e for e in _load(run / "packet.json")["existing_viewpoint_relations"]}
    fixes, notes = [], []
    for fix in decisions.get("relation_fixes", []):
        ref = fix["relation"]
        if (verdicts.get("CHK:" + ref) or {}).get("verdict") != "accept":
            notes.append(f"{ref}：复核未通过，不改")
            continue
        new = fix["replace_with"]
        check = checks[ref]
        if new:
            fixes.append({"item": "FIX:" + ref, "from": new["from"], "to": new["to"], "type": new["type"],
                          "evidence_kind": "quote" if check["quote"] else "claim_relation",
                          "claim_relations": check["claim_relations"], "quotes": [check["quote"]] if check["quote"] else [],
                          "explanation": check["note"]})
    with store.connect() as conn, conn.cursor() as cur:
        olds = {}
        for f in decisions.get("relation_fixes", []):
            if (verdicts.get("CHK:" + f["relation"]) or {}).get("verdict") != "accept":
                continue
            e = existing[f["relation"]]
            cur.execute("""SELECT object_id, payload FROM wang_knowledge.objects WHERE collection='viewpoint_relations'
                           AND retired_at IS NULL AND payload->>'effective_state'='active' AND object_id NOT LIKE 'CVP3-%%'
                           AND payload->>'source_viewpoint_id'=%s AND payload->>'target_viewpoint_id'=%s
                           AND payload->>'relation_type'=%s""", (ids[e["from"]], ids[e["to"]], e["type"]))
            rows = cur.fetchall()
            if len(rows) != 1:
                raise SystemExit(f"{f['relation']}: expected one active relation {e['from']} {e['type']} {e['to']}, found {len(rows)}")
            olds[rows[0][0]] = rows[0][1]
        ends = sorted({ids[x] for f in fixes for x in (f["from"], f["to"])})
        cur.execute("SELECT object_id, payload->>'current_revision_id' FROM wang_knowledge.objects WHERE collection='canonical_viewpoints' AND object_id=ANY(%s)", (ends,))
        current = dict(cur.fetchall())
    retired = [{**r, "effective_state": "retired", "revision": int(r["revision"]) + 1} for r in olds.values() if r["effective_state"] == "active"]
    replacement, _ = relation_records(run, {"relations": fixes}, {f["item"]: {"verdict": "accept"} for f in fixes},
                                      {}, review_raw, packet, private, current)
    held = held_claims(store, {c for r in replacement for c in r["supporting_claim_ids"]})
    for r in replacement:
        r["supporting_claim_ids"] = [c for c in r["supporting_claim_ids"] if c not in held]
    back = {v: k for k, v in ids.items()}
    notes += [f"撤回 {back.get(r['source_viewpoint_id'])} {r['relation_type']} {back.get(r['target_viewpoint_id'])}" for r in retired]
    notes += [f"新记 {back.get(r['source_viewpoint_id'])} {r['relation_type']} {back.get(r['target_viewpoint_id'])}" for r in replacement]
    return {"viewpoint_relations": retired + replacement}, notes


def load_run(run: Path) -> tuple[dict, dict, dict]:
    """The packet a run was reviewed on and the short refs its models used.

    Refs come from refs.json, saved when the run was built; a rebuild from the
    store would renumber them once the run's own writes add CVPs or links.
    """
    saved = _load(run / "packet.json")
    if (run / "refs.json").exists():
        refs = _load(run / "refs.json")
        return saved, saved, {"ref_to_id": refs, "ref": {v: k for k, v in refs.items()}}
    spec = _load(run / "run.json") if (run / "run.json").exists() else {}
    packet, private = build_packet(saved["topic"], spec.get("aliases", ["Son of Man", "son of man"]), spec.get("claim_ids"))
    return saved, packet, private


def check_refs(part: str, saved: dict, packet: dict, private: dict | None = None) -> None:
    """Every viewpoint and Claim the refs name still exists with the statement that was reviewed."""
    refs = (private or {}).get("ref_to_id")
    if refs and packet is saved:
        store = PostgresKnowledgeStore(database_url_from_env())
        want_v = {refs[v["ref"]]: v["statement"] for v in saved["scope_viewpoints"]}
        want_c = {refs[c["ref"]]: c["statement"] for c in saved["scope_claims"]}
        with store.connect() as conn, conn.cursor() as cur:
            cur.execute("""SELECT v.object_id, r.payload->>'core_proposition' FROM wang_knowledge.objects v
                           JOIN wang_knowledge.objects r ON r.collection='viewpoint_revisions' AND r.object_id=v.payload->>'current_revision_id'
                           WHERE v.collection='canonical_viewpoints' AND v.object_id=ANY(%s) AND v.payload->>'identity_status'='active'""",
                        (sorted(want_v),))
            got_v = dict(cur.fetchall())
            cur.execute("SELECT object_id, payload->>'statement' FROM wang_knowledge.objects WHERE collection='claims' AND object_id=ANY(%s) AND retired_at IS NULL",
                        (sorted(want_c),))
            got_c = dict(cur.fetchall())
        if got_v != want_v or got_c != want_c:
            raise SystemExit("a reviewed viewpoint or Claim changed or is gone since this run was reviewed")
        return
    # Older runs without saved refs: the rebuilt packet must still match what was reviewed.
    same_refs = lambda p: ([(v["ref"], v["statement"]) for v in p["scope_viewpoints"]],
                           [(c["ref"], c["statement"]) for c in p["scope_claims"]], p["library_index"])
    if same_refs(packet) != same_refs(saved):
        raise SystemExit("the topic's viewpoints or Claims changed since this run was reviewed")


def settled_inputs(run: Path) -> tuple[dict, dict, dict, Path, Path]:
    """A finished run's proposal, verdicts and decisions, with the owner's answers applied.

    An escalated item the owner answered "write" counts as accepted; "skip" leaves
    it out. Reviewed corrections of existing relations become relation_fixes.
    """
    state = _load(run / "state.json")
    proposal = _load(run / "final-proposal.json")
    verdicts = _load(run / "final-review.json")["verdicts"]
    questions = _load(run / "questions.json") if (run / "questions.json").exists() else []
    unanswered = [q["item"] for q in questions if q["answer"] not in q["choices"]]
    if unanswered:
        raise SystemExit(f"owner has not answered: {unanswered}")
    decisions = {"drop_relations": [], "relation_fixes": [], "answers": questions}
    for q in questions:
        if q["answer"] == "write":
            verdicts[q["item"]] = {**verdicts.get(q["item"], {"item": q["item"]}), "verdict": "accept"}
        elif q["answer"] == "skip":
            verdicts[q["item"]] = {**verdicts.get(q["item"], {"item": q["item"]}), "verdict": "reject"}
            if q["item"].startswith("REL:"):
                decisions["drop_relations"].append(q["item"])
    for c in proposal["existing_relation_checks"]:
        if c["verdict"] in ("direction_or_type_mismatch", "duplicate"):
            decisions["relation_fixes"].append({"relation": c["relation"], "replace_with": c.get("corrected")})
    (run / "decisions.json").write_text(json.dumps(decisions, ensure_ascii=False, indent=1))
    return proposal, verdicts, decisions, Path(state["proposal_raw"]), Path(state["review_raw"])


def write_part(run: Path, part: str, *, proposal: dict, verdicts: dict, decisions: dict, proposal_raw: Path,
               review_raw: Path, decisions_path: Path, proposal_path: Path, backup: Path | None, execute: bool,
               saved: dict, packet: dict, private: dict) -> dict:
    """Plan one part; with `execute`, write it in one transaction under a topic run and read it back."""
    topic = saved["topic"]
    if (run / "write" / f"{part}-result.json").exists():
        # The package and list on disk are the record of what was written; keep them.
        return {"status": "already_written", "result": _load(run / "write" / f"{part}-result.json")}
    store = PostgresKnowledgeStore(database_url_from_env())
    out = run / "write"
    out.mkdir(exist_ok=True)
    if part == "relations":
        slot_ids = {slot: vid for slot, (vid, _) in new_viewpoint_ids(run, proposal).items()}
        viewpoint_ids = sorted({private["ref_to_id"].get(x) or slot_ids[x] for r in proposal["relations"] for x in (r["from"], r["to"])})
        with store.connect() as conn, conn.cursor() as cur:
            cur.execute("""SELECT v.object_id, v.payload->>'current_revision_id', r.payload->>'core_proposition'
                           FROM wang_knowledge.objects v JOIN wang_knowledge.objects r
                             ON r.collection='viewpoint_revisions' AND r.object_id=v.payload->>'current_revision_id'
                           WHERE v.collection='canonical_viewpoints' AND v.object_id=ANY(%s)""", (viewpoint_ids,))
            rows = cur.fetchall()
        current = {vid: rev for vid, rev, _ in rows}
        statements = {vid: text for vid, _, text in rows}
        records, skipped = relation_records(run, proposal, verdicts, decisions, review_raw, packet, private, current)
        held = held_claims(store, {c for r in records for c in r["supporting_claim_ids"]})
        for r in records:
            r["supporting_claim_ids"] = [c for c in r["supporting_claim_ids"] if c not in held]
        package = {"viewpoint_relations": records}
        listing = write_list(records, skipped, statements)
    elif part == "relation-fixes":
        package, notes = relation_fix_package(run, proposal, verdicts, decisions, review_raw, packet, private, store)
        listing = "# 已有关系的更正\n\n" + "\n".join(f"- {n}" for n in notes) + "\n"
    elif part == "held-cleanup":
        package, notes = held_cleanup_package(run, store)
        listing = "# 清理：本次写入中依赖待审 Claim 的部分\n\n" + "\n".join(f"- {n}" for n in notes) + "\n"
        if not package:
            print("nothing relies on a held Claim")
            return {"planned": 0}
    else:
        package, notes, affected = link_summary_package(run, proposal, verdicts, packet, private, store,
                                                        proposal_raw, review_raw)
        back = {v: k for k, v in private["ref_to_id"].items()}
        summary_ids = {private["ref_to_id"][v["viewpoint"]] for v in proposal["viewpoints"] if v["summary"] and v["viewpoint"].startswith("V")}
        listing = "\n".join([f"# 写入清单：连接 {len(package.get('viewpoint_claim_links', []))} 条，观点 {len(package.get('canonical_viewpoints', []))} 个", ""]
                            + [f"- 连接 {back.get(l['claim_id'])} → {back.get(l['viewpoint_id'])}：{l['link_type']}"
                               f"（{'改类型' if l['revision'] > 1 else '新增'}）" for l in package.get("viewpoint_claim_links", [])]
                            + [f"- 观点 {back.get(v['viewpoint_id'])}：{'summary 更新；' if v['viewpoint_id'] in summary_ids else ''}成员依据重算"
                               for v in package.get("canonical_viewpoints", [])]
                            + ["", "## 不写", ""] + [f"- {n}" for n in notes]) + "\n"
    if not any(package.values()):
        (out / f"{part}-write-list.md").write_text(listing)
        return {"planned": 0}
    plan = store.plan_package(package, source_kind=SOURCE_KIND, transactional=True)
    context = store.read_package_cvp_context(package)
    read_pins = pins(context, absent=[(op.collection, op.object_id) for op in plan.operations if op.operation == "create"])
    (out / f"{part}-package.json").write_text(json.dumps(package, ensure_ascii=False, indent=1))
    (out / f"{part}-write-list.md").write_text(listing)
    print(f"planned {len(plan.operations)} operations; list at {out / (part + '-write-list.md')}")
    if not execute:
        return {"planned": len(plan.operations), "list": str(out / (part + '-write-list.md'))}

    if not backup or not backup.exists():
        raise SystemExit("--execute needs the backup taken just before it")
    claim_ids = sorted(private["ref_to_id"][c["ref"]] for c in packet["scope_claims"])
    materials = {"packet": binding(run / "packet.json"), "proposal": binding(proposal_path)}
    if part != "relations":
        materials["part"] = part
    run_id = store.prepare_topic_run(topic=topic, claim_ids=claim_ids, materials=materials)
    evidence = {"package": {"sha256": sha256_json(package), "payload": package},
                "approval": {"gpt": binding(proposal_raw), "opus": binding(review_raw)},
                "authorization": binding(decisions_path), "backup": {"dump": binding(backup)},
                "sources": [binding(run / "packet.json")], "read_pins": pin_rows(read_pins)}
    reviewed = store.review_cvp_group(run_id, plan=plan, evidence=evidence)
    result = store.apply_plan(plan, metadata={"reason": f"CVP 3.0 topic run {topic}: reviewed {part}",
                                              "actor": "owner-authorized-topic-agent"},
                              transaction_context=TransactionValidation(run_id=run_id, reviewed_sha256=reviewed,
                                                                        read_pins=read_pins))
    # Read back every written record and compare it with what was planned.
    mismatched, written = [], 0
    with store.connect() as conn, conn.cursor() as cur:
        for collection, rows in package.items():
            id_field = ID_FIELDS[collection]
            for row in rows:
                cur.execute("SELECT payload FROM wang_knowledge.objects WHERE collection=%s AND object_id=%s AND retired_at IS NULL",
                            (collection, row[id_field]))
                got = cur.fetchone()
                written += bool(got)
                if not got or {k: v for k, v in got[0].items() if k in row} != row:
                    mismatched.append(f"{collection}/{row[id_field]}")
    (out / f"{part}-result.json").write_text(json.dumps({"run_id": run_id, "reviewed_sha256": reviewed, "apply": result,
                                                             "written": written, "readback_mismatched": mismatched},
                                                            ensure_ascii=False, indent=1, default=str))
    print(json.dumps({"run_id": run_id, "written": written, "readback_mismatched": mismatched}, ensure_ascii=False))
    return {"run_id": run_id, "written": written, "readback_mismatched": mismatched}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--part", choices=("relations", "links", "held-cleanup", "relation-fixes"), default="relations",
                        help="relations, or links with the summaries they change")
    parser.add_argument("--proposal", type=Path, required=True, help="final proposal JSON")
    parser.add_argument("--previous-proposal", type=Path,
                        help="the proposal the final one was revised from; numbers items the run numbered in memory")
    parser.add_argument("--verdicts", type=Path, required=True, help="final verdicts JSON (list or {verdicts: [...]})")
    parser.add_argument("--proposal-raw", type=Path, required=True, help="raw model output of the final proposal")
    parser.add_argument("--review-raw", type=Path, required=True, help="raw model output of the final review")
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--backup", type=Path, help="pg_dump taken just before --execute")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    run = args.run
    saved, packet, private = load_run(run)
    check_refs(args.part, saved, packet)
    proposal = _load(args.proposal)
    if args.previous_proposal:
        # Runs before the multi-round code saved the revision before numbering it.
        previous = _load(args.previous_proposal)
        assign_ids(previous)
        assign_ids(proposal, previous)
    if any(x["item"] == "" for field in ("relations", "merge_proposals", "unresolved") for x in proposal[field]):
        raise SystemExit("proposal has unnumbered items; pass --previous-proposal")
    raw = _load(args.verdicts)
    verdicts = {v["item"]: v for v in (raw["verdicts"] if isinstance(raw, dict) else raw)}
    decisions = _load(args.decisions)

    result = write_part(run, args.part, proposal=proposal, verdicts=verdicts, decisions=decisions,
                        proposal_raw=args.proposal_raw, review_raw=args.review_raw, decisions_path=args.decisions,
                        proposal_path=args.proposal, backup=args.backup, execute=args.execute,
                        saved=saved, packet=packet, private=private)
    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    main()
