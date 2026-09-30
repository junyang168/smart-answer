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


def audit(packet, manifest, roles):
    for value in (packet, manifest, roles):
        checked(value)
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
    return {"status": "coverage_and_relations_verified_not_semantic_approval",
            "eligible_other_claims": len(expected), "owned": len(owned), "held": len(held),
            "missing": 0, "duplicate": 0, "foreign": 0, "preserved_relations": len(observed),
            "partition_counts": counts, "manifest_sha256": manifest["artifact_sha256"]}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("packet", "manifest", "roles"):
        p.add_argument(f"--{name}", type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(audit(*(json.loads(path.read_text()) for path in
                           (args.packet, args.manifest, args.roles))), ensure_ascii=False))
