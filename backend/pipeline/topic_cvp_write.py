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

    ids = private["ref_to_id"]
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
ID_FIELDS = {"viewpoint_relations": "viewpoint_relation_id", "viewpoint_identity_candidates": "identity_candidate_id",
             "viewpoint_identity_decisions": "identity_decision_id", "viewpoint_claim_links": "viewpoint_claim_link_id",
             "canonical_viewpoints": "viewpoint_id"}
MODEL_IDS = ["claude-opus-5-5", "gpt-6.1-sol"]


def link_summary_package(run: Path, proposal: dict, verdicts: dict, packet: dict, private: dict, store,
                         proposal_raw: Path, review_raw: Path) -> tuple[dict, list[str], list[str]]:
    """Reviewed link changes, on any viewpoint, and accepted in-scope summaries, as complete records.

    Promoting a link to membership keeps the existing link's component: it
    becomes equivalent_component, or equivalent_full when the link has none.
    """
    from datetime import datetime, timezone
    from backend.api.canonical_repository.cvp_management import summary_basis_for_records

    ids = private["ref_to_id"]
    review_sha, proposal_sha = binding(review_raw)["file_sha256"], binding(proposal_raw)["file_sha256"]
    packet_sha = binding(run / "packet.json")["file_sha256"]
    now = datetime.now(timezone.utc).isoformat()
    changes, notes = [], []
    with store.connect() as conn, conn.cursor() as cur:
        for d in proposal["claim_dispositions"]:
            if d["disposition"] not in ("member", "supports", "qualifies", "extends"):
                continue
            label = f"{d['claim']} → {d['viewpoint']} {d['disposition']}"
            claim_id, view_id = ids[d["claim"]], ids.get(d["viewpoint"])
            cur.execute("""SELECT payload FROM wang_knowledge.objects WHERE collection='viewpoint_claim_links'
                           AND retired_at IS NULL AND payload->>'effective_state'='active'
                           AND payload->>'claim_id'=%s AND payload->>'viewpoint_id'=%s""", (claim_id, view_id))
            existing = [row[0] for row in cur.fetchall()]
            kinds = {"member" if l["link_type"] in MEMBER_LINKS else l["link_type"] for l in existing}
            if d["disposition"] in kinds:
                continue
            if d["disposition"] == "member" and len(existing) != 1:
                notes.append(f"{label}：升为成员要沿用现有连接的成分定位，现有 {len(existing)} 条，暂不写")
            elif len(existing) > 1:
                notes.append(f"{label}：现有 {len(existing)} 条连接，改哪一条不明确，暂不写")
            else:
                changes.append((d, claim_id, view_id, existing[0] if existing else None))
        cur.execute("SELECT object_id, payload FROM wang_knowledge.objects WHERE collection='claims' AND object_id=ANY(%s)",
                    ([c for _, c, _, _ in changes],))
        claims = dict(cur.fetchall())
    viewpoint_rows = {}
    package: dict[str, list] = {"viewpoint_identity_candidates": [], "viewpoint_identity_decisions": [],
                                "viewpoint_claim_links": [], "canonical_viewpoints": []}
    changed_views = {view_id for _, _, view_id, _ in changes}
    summaries = {ids[v["viewpoint"]]: v["summary"] for v in proposal["viewpoints"]
                 if v["summary"] and v["viewpoint"].startswith("V")
                 and (verdicts.get("VP:" + v["viewpoint"]) or {}).get("verdict") == "accept"}
    affected = sorted(changed_views | set(summaries))
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT payload->>'claim_id' FROM wang_knowledge.objects WHERE collection='viewpoint_claim_links'
                       AND retired_at IS NULL AND payload->>'viewpoint_id'=ANY(%s)""", (affected,))
        member_claims = sorted({row[0] for row in cur.fetchall()} | {c for _, c, _, _ in changes})
    context = store.read_cvp_context(member_claims, viewpoint_ids=affected)
    payloads = {key: row["payload"] for key, row in context.items() if not row.get("retired")}
    for key, view in payloads.items():
        if key[0] == "canonical_viewpoints":
            viewpoint_rows[key[1]] = view
    for d, claim_id, view_id, old in changes:
        fingerprint = sha256_json({"run": str(run), "claim": claim_id, "viewpoint": view_id, "link_type": d["disposition"]})
        # The foundation validator derives the candidate id from its content.
        candidate_id = "VIC-" + sha256_json({"claims": [claim_id], "viewpoints": [view_id], "relations": [],
                                             "action": "match_existing", "blockers": [], "coverage_snapshot_id": None,
                                             "generation_fingerprint": fingerprint,
                                             "scope_manifest_sha256": packet_sha})[:20]
        decision_id = "CVP3-decision-" + fingerprint[:24]
        package["viewpoint_identity_candidates"].append({
            "identity_candidate_id": candidate_id, "schema_version": "wang_viewpoint_identity_candidate_v1",
            "candidate_claim_ids": [claim_id], "candidate_viewpoint_ids": [view_id], "seed_relation_ids": [],
            "proposed_action": "match_existing", "proposed_proposition_signature": None, "coverage_snapshot_id": None,
            "scope_manifest_sha256": packet_sha, "blocker_codes": [], "generation_fingerprint": fingerprint,
            "review_status": "candidate", "visibility": "internal", "revision": 1})
        package["viewpoint_identity_decisions"].append({
            "identity_decision_id": decision_id, "schema_version": "wang_viewpoint_identity_decision_v1",
            "identity_candidate_id": candidate_id, "decision": "match_existing", "resolved_viewpoint_id": view_id,
            "claim_link_decisions": [{"claim_id": claim_id, "link_type": d["disposition"]}],
            "proposition_unit_link_decisions": [], "reviewer_kind": "system", "reviewer_id": "wang_cvp_topic_v3",
            "approval_basis": "dual_model_consensus", "reason": d["note"] or "topic run disposition",
            "input_sha256": fingerprint, "review_artifact_sha256": review_sha, "policy_version": "wang_cvp_topic_v3",
            "reviewer_model_ids": MODEL_IDS, "semantic_call_artifact_sha256s": sorted([proposal_sha, review_sha]),
            "created_at": now, "review_status": "system_approved", "visibility": "internal", "revision": 1})
        link_type = d["disposition"]
        if link_type == "member":
            link_type = "equivalent_component" if old.get("component_locator") else "equivalent_full"
        package["viewpoint_identity_decisions"][-1]["claim_link_decisions"][0]["link_type"] = link_type
        if old:
            link = {**old, "link_type": link_type, "decision_id": decision_id, "revision": int(old["revision"]) + 1}
        else:
            link = {"viewpoint_claim_link_id": "CVP3-l-" + fingerprint[:24], "schema_version": "wang_viewpoint_claim_link_v2",
                    "viewpoint_id": view_id, "validated_against_viewpoint_revision_id": viewpoint_rows[view_id]["current_revision_id"],
                    "claim_id": claim_id, "pinned_claim_revision": int(claims[claim_id]["revision"]),
                    "link_type": link_type, "supporting_relation_ids": [], "evidence_bindings": [],
                    "occurrence_refs": [], "decision_id": decision_id, "effective_state": "active",
                    "review_status": "system_approved", "visibility": "internal", "revision": 1}
        package["viewpoint_claim_links"].append(link)
        payloads["viewpoint_claim_links", link["viewpoint_claim_link_id"]] = link
    for view_id in affected:
        view = viewpoint_rows[view_id]
        summary = summaries.get(view_id, view.get("summary"))
        package["canonical_viewpoints"].append({**view, "summary": summary, "revision": int(view["revision"]) + 1,
                                                "summary_basis": summary_basis_for_records(payloads, view_id)})
    return {k: v for k, v in package.items() if v}, notes, affected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--part", choices=("relations", "links"), default="relations",
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
    saved = _load(run / "packet.json")
    topic = saved["topic"]
    packet, private = build_packet(topic, _load(run / "run.json")["aliases"] if (run / "run.json").exists() else ["Son of Man", "son of man"])
    strip = lambda p: {k: v for k, v in p.items() if k not in ("recall", "recalled_viewpoints")}
    if args.part == "relations" and strip(packet) != strip(saved):
        raise SystemExit("the store changed since this run was reviewed; the short refs no longer match")
    # Writing changes the packet (new relations appear in it), so a later part
    # checks only what the short refs stand on: the topic's viewpoints, Claims and the library index.
    same_refs = lambda p: ([(v["ref"], v["statement"]) for v in p["scope_viewpoints"]],
                           [(c["ref"], c["statement"]) for c in p["scope_claims"]], p["library_index"])
    if args.part != "relations" and same_refs(packet) != same_refs(saved):
        raise SystemExit("the topic's viewpoints or Claims changed since this run was reviewed")
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

    store = PostgresKnowledgeStore(database_url_from_env())
    out = run / "write"
    out.mkdir(exist_ok=True)
    if args.part == "relations":
        viewpoint_ids = sorted({private["ref_to_id"][x] for r in proposal["relations"] for x in (r["from"], r["to"])})
        with store.connect() as conn, conn.cursor() as cur:
            cur.execute("""SELECT v.object_id, v.payload->>'current_revision_id', r.payload->>'core_proposition'
                           FROM wang_knowledge.objects v JOIN wang_knowledge.objects r
                             ON r.collection='viewpoint_revisions' AND r.object_id=v.payload->>'current_revision_id'
                           WHERE v.collection='canonical_viewpoints' AND v.object_id=ANY(%s)""", (viewpoint_ids,))
            rows = cur.fetchall()
        current = {vid: rev for vid, rev, _ in rows}
        statements = {vid: text for vid, _, text in rows}
        records, skipped = relation_records(run, proposal, verdicts, decisions, args.review_raw, packet, private, current)
        package = {"viewpoint_relations": records}
        listing = write_list(records, skipped, statements)
    else:
        package, notes, affected = link_summary_package(run, proposal, verdicts, packet, private, store,
                                                        args.proposal_raw, args.review_raw)
        back = {v: k for k, v in private["ref_to_id"].items()}
        summary_ids = {private["ref_to_id"][v["viewpoint"]] for v in proposal["viewpoints"] if v["summary"] and v["viewpoint"].startswith("V")}
        listing = "\n".join([f"# 写入清单：连接 {len(package.get('viewpoint_claim_links', []))} 条，观点 {len(package.get('canonical_viewpoints', []))} 个", ""]
                            + [f"- 连接 {back.get(l['claim_id'])} → {back.get(l['viewpoint_id'])}：{l['link_type']}"
                               f"（{'改类型' if l['revision'] > 1 else '新增'}）" for l in package.get("viewpoint_claim_links", [])]
                            + [f"- 观点 {back.get(v['viewpoint_id'])}：{'summary 更新；' if v['viewpoint_id'] in summary_ids else ''}成员依据重算"
                               for v in package.get("canonical_viewpoints", [])]
                            + ["", "## 不写", ""] + [f"- {n}" for n in notes]) + "\n"
    plan = store.plan_package(package, source_kind=SOURCE_KIND, transactional=True)
    context = store.read_package_cvp_context(package)
    read_pins = pins(context, absent=[(op.collection, op.object_id) for op in plan.operations if op.operation == "create"])
    (out / f"{args.part}-package.json").write_text(json.dumps(package, ensure_ascii=False, indent=1))
    (out / f"{args.part}-write-list.md").write_text(listing)
    print(f"planned {len(plan.operations)} operations; list at {out / (args.part + '-write-list.md')}")
    if args.dry_run:
        return

    if not args.backup or not args.backup.exists():
        raise SystemExit("--execute needs the backup taken just before it")
    claim_ids = sorted(private["ref_to_id"][c["ref"]] for c in packet["scope_claims"])
    materials = {"packet": binding(run / "packet.json"), "proposal": binding(args.proposal)}
    if args.part != "relations":
        materials["part"] = args.part
    run_id = store.prepare_topic_run(topic=topic, claim_ids=claim_ids, materials=materials)
    evidence = {"package": {"sha256": sha256_json(package), "payload": package},
                "approval": {"gpt": binding(args.proposal_raw), "opus": binding(args.review_raw)},
                "authorization": binding(args.decisions), "backup": {"dump": binding(args.backup)},
                "sources": [binding(run / "packet.json")], "read_pins": pin_rows(read_pins)}
    reviewed = store.review_cvp_group(run_id, plan=plan, evidence=evidence)
    result = store.apply_plan(plan, metadata={"reason": f"CVP 3.0 topic run {topic}: reviewed {args.part}",
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
    (out / f"{args.part}-result.json").write_text(json.dumps({"run_id": run_id, "reviewed_sha256": reviewed, "apply": result,
                                                             "written": written, "readback_mismatched": mismatched},
                                                            ensure_ascii=False, indent=1, default=str))
    print(json.dumps({"run_id": run_id, "written": written, "readback_mismatched": mismatched}, ensure_ascii=False))


if __name__ == "__main__":
    main()
