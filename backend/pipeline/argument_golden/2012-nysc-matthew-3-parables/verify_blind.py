"""Reconstruct the frozen blind input and check the archived reply offline."""

import argparse
import hashlib
import json
from pathlib import Path

from backend.pipeline.source_projection import project_script


FIXTURE = Path(__file__).resolve().parent
RANGES = [(4, 9, 11), (5, 1, 43), (6, 1, 12)]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def check_digest(label, data, expected):
    if digest(data) != expected:
        raise ValueError(f"{label}: expected {expected}, got {digest(data)}")


def verify(source_dir):
    protocol = (FIXTURE / "blind-protocol.json").read_bytes()
    check_digest("protocol", protocol, "6dc41efede8f062fa0f1557670a87bc189c3074083d2c3720df3401c8e73ebbc")
    rows = []
    for (lecture, first, last), expected in zip(RANGES, json.loads(protocol)["source_hashes"], strict=True):
        source = source_dir / f"2012 NYSC 專題：馬太福音釋經（三）{lecture}.json"
        raw = source.read_bytes()
        check_digest(source.name, raw, expected)
        spoken = project_script(json.loads(raw)["script"]).spoken_rows
        for index in range(first, last + 1):
            row = spoken[index - 1]
            rows.append(dict(id=f"L{lecture}-S{index:04d}", time=row["start_timeline"], text=row["text"]))
    frozen = json.dumps(rows, indent=2, ensure_ascii=False).encode()
    check_digest("input", frozen, "4013ee375e0dd337119409f7913e7e9c37f1e766d947033553b3617c8c4ed92c")
    reply = (FIXTURE / "blind-response.json").read_bytes()
    check_digest("reply", reply, "439956c2b3bd9e37329643adcc9b9a24cd5ef69bf7a71a953ee9d7c084802cac")
    result = json.loads(reply)
    texts = {row["id"]: row["text"] for row in rows}
    nodes = [node for group in result["groups"] for node in group["nodes"]]
    ids = {node["id"] for node in nodes}
    if len(ids) != len(nodes):
        raise ValueError("duplicate node ID")
    quotes = 0
    for node in nodes:
        if not node["sources"]:
            raise ValueError(f"missing evidence for {node['id']}")
        for ref in node["sources"]:
            if not ref["quote"].strip() or ref["quote"] not in texts[ref["id"]]:
                raise ValueError(f"unmatched quote for {node['id']} in {ref['id']}")
            quotes += 1
    for edge in result["relations"]:
        if edge["from_id"] not in ids or edge["to_id"] not in ids:
            raise ValueError(f"unknown endpoint in {edge}")
    print(f"{len(rows)} rows, {len(result['groups'])} groups, {len(nodes)} nodes, "
          f"{len(result['relations'])} relations, {quotes} exact quotes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path, help="directory containing the three pinned script_published files")
    verify(parser.parse_args().source_dir)
