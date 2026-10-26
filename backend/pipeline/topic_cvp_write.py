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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
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
    if strip(packet) != strip(saved):
        raise SystemExit("the store changed since this run was reviewed; the short refs no longer match")
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
    plan = store.plan_package(package, source_kind=SOURCE_KIND, transactional=True)
    context = store.read_package_cvp_context(package)
    read_pins = pins(context, absent=[(op.collection, op.object_id) for op in plan.operations if op.operation == "create"])
    out = run / "write"
    out.mkdir(exist_ok=True)
    (out / "relations-package.json").write_text(json.dumps(package, ensure_ascii=False, indent=1))
    (out / "relations-write-list.md").write_text(write_list(records, skipped, statements))
    print(f"planned {len(plan.operations)} operations; list at {out / 'relations-write-list.md'}")
    if args.dry_run:
        return

    if not args.backup or not args.backup.exists():
        raise SystemExit("--execute needs the backup taken just before it")
    claim_ids = sorted(private["ref_to_id"][c["ref"]] for c in packet["scope_claims"])
    run_id = store.prepare_topic_run(topic=topic, claim_ids=claim_ids,
                                     materials={"packet": binding(run / "packet.json"), "proposal": binding(args.proposal)})
    evidence = {"package": {"sha256": sha256_json(package), "payload": package},
                "approval": {"gpt": binding(args.proposal_raw), "opus": binding(args.review_raw)},
                "authorization": binding(args.decisions), "backup": {"dump": binding(args.backup)},
                "sources": [binding(run / "packet.json")], "read_pins": pin_rows(read_pins)}
    reviewed = store.review_cvp_group(run_id, plan=plan, evidence=evidence)
    result = store.apply_plan(plan, metadata={"reason": f"CVP 3.0 topic run {topic}: reviewed relations",
                                              "actor": "owner-authorized-topic-agent"},
                              transaction_context=TransactionValidation(run_id=run_id, reviewed_sha256=reviewed,
                                                                        read_pins=read_pins))
    # Read back every written record and compare it with what was planned.
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT object_id, payload FROM wang_knowledge.objects WHERE collection='viewpoint_relations' AND object_id=ANY(%s)",
                    ([r["viewpoint_relation_id"] for r in records],))
        stored = dict(cur.fetchall())
    mismatched = [r["viewpoint_relation_id"] for r in records
                  if {k: v for k, v in (stored.get(r["viewpoint_relation_id"]) or {}).items() if k in r} != r]
    (out / "relations-result.json").write_text(json.dumps({"run_id": run_id, "reviewed_sha256": reviewed, "apply": result,
                                                          "written": len(stored), "readback_mismatched": mismatched},
                                                         ensure_ascii=False, indent=1, default=str))
    print(json.dumps({"run_id": run_id, "written": len(stored), "readback_mismatched": mismatched}, ensure_ascii=False))


if __name__ == "__main__":
    main()
