"""Independent, stdlib-only exact-once and edge-preservation audit for #395."""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


def sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def checked(value):
    if value["artifact_sha256"] != sha({k: v for k, v in value.items() if k != "artifact_sha256"}):
        raise ValueError("artifact SHA differs")


def audit_provenance(packet, manifest, input_roots):
    """Read raw answers and repairs ourselves; reconstruct every derived SHA."""
    if not input_roots:
        raise ValueError("explicit input roots required for provenance audit")
    roots = {str(Path(p).resolve(strict=True)) for p in input_roots}
    declared = manifest["input_roots"]
    if len(roots) != len(input_roots) or len(declared) != len(roots) or {r["root"] for r in declared} != roots:
        raise ValueError("provenance input roots differ")

    def read(path, artifact_sha=None):
        p = Path(path).resolve(strict=True)
        if not any(p.is_relative_to(Path(root)) for root in roots):
            raise ValueError("provenance path outside input roots")
        value = json.loads(p.read_text())
        checked(value)
        if artifact_sha is not None and value["artifact_sha256"] != artifact_sha:
            raise ValueError("provenance artifact SHA differs")
        return value

    for item in declared:
        path = Path(item["root"]) / "routing-packet.json"
        if item["packet_path"] != str(path) or read(path) != packet or item["packet_sha256"] != packet["artifact_sha256"]:
            raise ValueError("provenance packet binding differs")
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["packet_file_sha256"]:
            raise ValueError("provenance packet file SHA differs")
    rows = {r["claim_id"]: r for r in packet["claims"]}
    provenance = manifest["artifact_provenance"]
    decisions, raw_roles, seen = {}, {}, {"primary": set(), "independent": set()}
    for digest, item in provenance.items():
        raw = read(item["raw_artifact_path"], item["raw_artifact_sha256"])
        if raw["packet_sha256"] != packet["artifact_sha256"]:
            raise ValueError("raw packet differs")
        role, ids = raw["role"], raw["claim_ids"]
        if role not in seen or len(set(ids)) != len(ids) or not set(ids) <= rows.keys() or seen[role] & set(ids):
            raise ValueError("duplicate/foreign raw classification")
        seen[role].update(ids)
        attempts = [read(p) for p in item["attempt_paths"]]
        if ([a["attempt_number"] for a in attempts] not in ([1], [1, 2])
                or any(a["role"] != role or a["claim_ids"] != ids or a["packet_sha256"] != packet["artifact_sha256"] for a in attempts)
                or attempts[-1] != raw):
            raise ValueError("raw retry provenance differs")
        body = {k: v for k, v in raw.items() if k != "artifact_sha256"}
        if raw.get("quote_protocol") == "exact_source_quote_ids_v3":
            if item["quote_repair_sha256"] is not None:
                raise ValueError("quote ID answer has external quote repair")
            response = json.loads(json.dumps(raw["response"]))
            if set(response) != {"decisions"} or set(response["decisions"]) != set(ids):
                raise ValueError("raw quote ID scope differs")
            for cid, decision in response["decisions"].items():
                texts = list(dict.fromkeys(text[:128] for text in
                    [rows[cid]["statement"], *rows[cid]["source_excerpts"]] if text.strip()))
                choices = {f"Q{i:04d}": text for i, text in enumerate(texts, 1)}
                selection = decision["basis_quote"]
                if selection not in choices:
                    raise ValueError("invalid raw quote ID")
                decision["basis_quote"] = choices[selection]
            body.update(response=response, raw_quote_id_response=raw["response"],
                        raw_artifact_sha256=raw["artifact_sha256"])
        if item["quote_repair_sha256"] is not None:
            repair = read(item["quote_repair_path"], item["quote_repair_sha256"])
            if (repair["raw_artifact_sha256"] != raw["artifact_sha256"]
                    or repair["packet_sha256"] != packet["artifact_sha256"]):
                raise ValueError("repair binding differs")
            # JSON roundtrip gives an independent copy; no backend helper used.
            response = json.loads(json.dumps(raw["response"]))
            changed = set()
            for change in repair["changes"]:
                cid = change["claim_id"]
                if (cid in changed or cid not in ids or change["claim_content_sha256"] != rows[cid]["claim_content_sha256"]
                        or response["decisions"][cid]["basis_quote"] != change["before"]):
                    raise ValueError("repair content pin differs")
                changed.add(cid)
                response["decisions"][cid]["basis_quote"] = change["after"]
            if not changed:
                raise ValueError("empty repair")
            body.update(response=response, raw_artifact_sha256=raw["artifact_sha256"],
                        quote_repair_sha256=repair["artifact_sha256"])
        elif item["quote_repair_path"] is not None:
            raise ValueError("unbound repair path")
        if sha(body) != digest or item["effective_artifact_sha256"] != digest:
            raise ValueError("effective provenance reconstruction differs")
        if set(body["response"]["decisions"]) != set(ids):
            raise ValueError("raw response scope differs")
        for cid, decision in body["response"]["decisions"].items():
            if not any(decision["basis_quote"] and decision["basis_quote"] in text
                       for text in [rows[cid]["statement"], *rows[cid]["source_excerpts"]]):
                raise ValueError("provenance decision ungrounded")
        decisions[digest] = body["response"]["decisions"]
        raw_roles[digest] = role
    referenced = set()
    entries = list(manifest["owners"].items()) + [(r["claim_id"], r) for r in manifest["held"]]
    for cid, entry in entries:
        for role in ("primary", "independent"):
            evidence = entry[role]
            if evidence is None:
                continue
            digest = evidence["artifact_sha256"]
            item = provenance[digest]
            if (raw_roles[digest] != role or decisions[digest].get(cid) != evidence["decision"]
                    or evidence["raw_artifact_sha256"] != item["raw_artifact_sha256"]
                    or evidence["quote_repair_sha256"] != item["quote_repair_sha256"]):
                raise ValueError("manifest decision provenance differs")
            referenced.add(digest)
    if referenced != set(provenance):
        raise ValueError("unreferenced/missing provenance")
    for item in manifest["transport_failure_history"]:
        failure = read(item["path"], item["artifact_sha256"])
        if (item["root"] not in roots or failure["status"] != "transport_failure"
                or failure["packet_sha256"] != packet["artifact_sha256"]
                or any(failure[k] != item[k] for k in ("role", "claim_ids", "attempt_number"))):
            raise ValueError("transport failure provenance differs")
    return len(provenance)


def audit_arbitrated(packet, manifest, roles, input_roots):
    """Independently reconstruct the overlay from retained raw source IDs."""
    meta = manifest["arbitration_provenance"]
    root = Path(meta["root"]).resolve(strict=True)
    def read(path, digest=None):
        path = Path(path).resolve(strict=True)
        if not path.is_relative_to(root):
            raise ValueError("arbitration provenance outside root")
        value = json.loads(path.read_text())
        checked(value)
        if digest is not None and value["artifact_sha256"] != digest:
            raise ValueError("arbitration provenance SHA differs")
        return value
    parent = read(meta["parent_path"], meta["parent_sha256"])
    if "arbitration_provenance" in parent:
        raise ValueError("multiple semantic arbitration rounds")
    parent_report = audit(packet, parent, roles, input_roots)
    arb = read(meta["packet_path"], meta["packet_sha256"])
    context = read(root / "source-context.json", arb["source_context_sha256"])
    priors = {r["claim_id"]: r for r in parent["held"]}
    if (arb["manifest_sha256"] != parent["artifact_sha256"] or arb["model"] != "gpt-6.1-sol"
            or arb["role_packet_sha256"] != packet["role_packet_sha256"]
            or arb["claim_count"] != len(arb["rows"]) or arb["claim_count"] != context["claim_count"]
            or len({r["claim_id"] for r in arb["rows"]}) != len(arb["rows"])
            or {r["claim_id"] for r in arb["rows"]} != set(priors)):
        raise ValueError("arbitration freeze scope differs")
    contexts = {r["claim_id"]: r for r in context["rows"]}
    rows = {r["claim_id"]: r for r in packet["claims"]}
    owners, held, counts = dict(parent["owners"]), [], Counter()
    if len(meta["proposals"]) != (len(arb["rows"]) + 15) // 16:
        raise ValueError("arbitration batch coverage differs")
    for index, item in enumerate(meta["proposals"]):
        batch = arb["rows"][index*16:index*16+16]
        ids = [r["claim_id"] for r in batch]
        proposal = read(item["proposal_path"], item["proposal_sha256"])
        raw = read(item["raw_path"], item["raw_sha256"])
        if (raw["claim_ids"] != ids or raw["model"] != arb["model"]
                or raw["packet_sha256"] != arb["artifact_sha256"] or raw["attempt_number"] not in (1, 2)
                or proposal["raw_artifact_sha256"] != raw["artifact_sha256"]
                or proposal["raw_artifact_path"] != item["raw_path"]
                or proposal["packet_sha256"] != arb["artifact_sha256"]
                or proposal["human_approval"] is not False or proposal["database_mutations"] != 0
                or proposal["grouping_authorization"] is not False
                or set(raw["response"]) != {"decisions"}
                or set(raw["response"]["decisions"]) != set(ids)):
            raise ValueError("arbitration raw binding differs")
        resolved = {}
        for row in batch:
            cid = row["claim_id"]
            if (row != contexts[cid] | {"prior_primary": priors[cid]["primary"],
                                       "prior_independent": priors[cid]["independent"]}
                    or row["claim_content_sha256"] != rows[cid]["claim_content_sha256"]
                    or hashlib.sha256(Path(row["source_path"]).read_bytes()).hexdigest() != row["source_file_sha256"]):
                raise ValueError("arbitration source/prior drift")
            choices = {f"E{i:04d}": part for i, part in enumerate(row["context"], 1) if part["text"].strip()}
            d = raw["response"]["decisions"][cid]
            if (set(d) != {"partition", "disposition", "source_selection", "reason"}
                    or d["partition"] not in {*packet["policy"]["partitions"], "unresolved"}
                    or d["disposition"] not in {"resolved", "needs_human", "repair_required"}
                    or not isinstance(d["reason"], str) or not d["reason"].strip()
                    or d["source_selection"] not in {"NONE", *choices}
                    or (d["disposition"] == "resolved" and (d["partition"] == "unresolved" or d["source_selection"] == "NONE"))
                    or (d["disposition"] != "resolved" and d["partition"] != "unresolved")):
                raise ValueError("invalid arbitration disposition/source")
            part = choices.get(d["source_selection"])
            effective = d | {"source_key": part["paragraph_key"] if part else "",
                "source_quote": part["text"] if part else "", "claim_content_sha256": row["claim_content_sha256"],
                "source_file_sha256": row["source_file_sha256"]}
            resolved[cid] = effective
            decision = effective | {"proposal_sha256": proposal["artifact_sha256"],
                                    "raw_artifact_sha256": raw["artifact_sha256"]}
            counts[d["disposition"]] += 1
            if d["disposition"] == "resolved":
                owners[cid] = {k: rows[cid][k] for k in ("claim_revision", "claim_content_sha256", "source_id")} | {
                    "partition": d["partition"], "primary": priors[cid]["primary"],
                    "independent": priors[cid]["independent"], "arbitration": decision}
            else:
                held.append(priors[cid] | {"reason_code": "arbitration_" + d["disposition"], "arbitration": decision})
        if proposal["decisions"] != resolved:
            raise ValueError("arbitration derived answer differs")
    def endpoint(cid):
        if cid in owners:
            return owners[cid]["partition"]
        if cid in rows:
            return "routing_held"
        return packet["source_role_by_claim"].get(cid, "outside_role_scope")
    links = [rel | {"from_partition": endpoint(rel["from_id"]), "to_partition": endpoint(rel["to_id"]),
        "cross_partition": endpoint(rel["from_id"]) != endpoint(rel["to_id"]),
        "ownership_inheritance": False, "read_only_context": True} for rel in packet["claim_relations"]]
    partition_counts = dict(sorted(Counter(o["partition"] for o in owners.values()).items()))
    expected = {k: v for k, v in parent.items() if k != "artifact_sha256"} | {
        "status": "arbitrated_with_explicit_holds" if held else "all_routed_after_arbitration",
        "owners": owners, "held": held, "counts": partition_counts, "preserved_relations": links,
        "arbitration_progress": dict(sorted(counts.items())), "arbitration_provenance": meta,
        "human_approval": False, "grouping_authorization": False, "database_mutations": 0}
    if {k: v for k, v in manifest.items() if k != "artifact_sha256"} != expected:
        raise ValueError("final arbitration overlay differs")
    return parent_report | {"owned": len(owners), "held": len(held), "partition_counts": partition_counts,
        "manifest_sha256": manifest["artifact_sha256"], "arbitration_progress": dict(counts),
        "arbitration_batches_verified": len(meta["proposals"])}


def audit(packet, manifest, roles, input_roots=None):
    for value in (packet, manifest, roles):
        checked(value)
    if "arbitration_provenance" in manifest:
        return audit_arbitrated(packet, manifest, roles, input_roots)
    if (manifest["packet_sha256"] != packet["artifact_sha256"]
            or manifest["role_ledger_sha256"] != roles["artifact_sha256"]
            or packet["role_ledger_sha256"] != roles["artifact_sha256"]):
        raise ValueError("input binding differs")
    expected = {r["claim_id"] for r in roles["decisions"] if r["role"] == "other"}
    rows = {r["claim_id"]: r for r in packet["claims"]}
    held = [r["claim_id"] for r in manifest["held"]]
    owned = set(manifest["owners"])
    if (len(expected) != roles["counts"]["other"] or expected != set(rows)
            or len(rows) != len(packet["claims"]) or packet["claim_count"] != len(rows)
            or len(held) != len(set(held)) or owned & set(held)
            or owned | set(held) != expected):
        raise ValueError("routing exact-once differs")
    for cid, owner in manifest["owners"].items():
        if owner["partition"] not in packet["policy"]["partitions"]:
            raise ValueError("foreign partition")
        for key in ("claim_revision", "claim_content_sha256", "source_id"):
            if owner[key] != rows[cid][key]:
                raise ValueError("owner pin drift")
        for role in ("primary", "independent"):
            d = owner[role]["decision"]
            if d["partition"] != owner["partition"] or not any(
                    d["basis_quote"] and d["basis_quote"] in text
                    for text in [rows[cid]["statement"], *rows[cid]["source_excerpts"]]):
                raise ValueError("unreviewed or ungrounded owner")
    originals = {r["claim_relation_id"]: r for r in packet["claim_relations"]}
    observed = {r["claim_relation_id"]: r for r in manifest["preserved_relations"]}
    if (set(originals) != set(observed) or len(originals) != len(packet["claim_relations"])
            or len(observed) != len(manifest["preserved_relations"])):
        raise ValueError("relation missing/duplicate/foreign")
    for rid, original in originals.items():
        edge = observed[rid]
        if any(edge.get(k) != v for k, v in original.items()):
            raise ValueError("relation content/state changed")
        if edge["ownership_inheritance"] or not edge["read_only_context"]:
            raise ValueError("relation promoted to ownership")
        def endpoint(cid):
            if cid in owned:
                return manifest["owners"][cid]["partition"]
            if cid in rows:
                return "routing_held"
            return packet["source_role_by_claim"].get(cid, "outside_role_scope")
        left, right = endpoint(edge["from_id"]), endpoint(edge["to_id"])
        if (edge["from_partition"], edge["to_partition"], edge["cross_partition"]) != (left, right, left != right):
            raise ValueError("relation endpoint owner differs")
    counts = dict(sorted(Counter(r["partition"] for r in manifest["owners"].values()).items()))
    if counts != manifest["counts"]:
        raise ValueError("partition counts differ")
    provenance_count = 0
    if manifest.get("provenance_version") == "routing_multi_root_provenance_v1":
        provenance_count = audit_provenance(packet, manifest, input_roots)
    return {"status": "coverage_and_relations_verified_not_semantic_approval",
            "eligible_other_claims": len(expected), "owned": len(owned), "held": len(held),
            "missing": 0, "duplicate": 0, "foreign": 0, "preserved_relations": len(observed),
            "partition_counts": counts, "manifest_sha256": manifest["artifact_sha256"],
            "provenance_artifacts_verified": provenance_count}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("packet", "manifest", "roles"):
        p.add_argument(f"--{name}", type=Path, required=True)
    p.add_argument("--input-root", type=Path, action="append")
    args = p.parse_args()
    print(json.dumps(audit(*(json.loads(path.read_text()) for path in
                           (args.packet, args.manifest, args.roles)), input_roots=args.input_root), ensure_ascii=False))
