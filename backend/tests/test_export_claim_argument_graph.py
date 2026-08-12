from __future__ import annotations

import json
from pathlib import Path

from backend.pipeline.export_claim_argument_graph import export_graph


def test_export_graph_extracts_reviewable_relations(tmp_path: Path) -> None:
    source = tmp_path / "graph.html"
    destination = tmp_path / "graph.json"
    payload = {
        "nodes": [{"id": "A"}, {"id": "B"}, {"id": "C"}],
        "edges": [{"s": "A", "d": "B", "k": "sup"}, {"s": "B", "d": "C", "k": "qa"}],
        "bands": [], "framework": [], "lanes": [],
    }
    source.write_text(f'<script id="d" type="application/json">{json.dumps(payload)}</script>', encoding="utf-8")
    result = export_graph(source, destination)
    assert result["summary"] == {"evidence_count": 3, "relation_count": 2, "support_count": 1, "answer_count": 1}
    assert result["relations"][0]["relation_type"] == "supports"
    assert destination.exists()


def test_export_graph_attaches_multi_claim_memberships_and_rejects_orphans(tmp_path: Path) -> None:
    source = tmp_path / "graph.html"
    destination = tmp_path / "graph.json"
    claims_path = tmp_path / "claims.json"
    payload = {
        "nodes": [{"id": "L3-E001", "topic": "CG-PRIMARY"}],
        "edges": [], "bands": [], "framework": [], "lanes": [],
    }
    claims = {
        "claims": [
            {"claim_id": "CL-1", "group_id": "CG-PRIMARY", "occurrences": [{"lecture": "第3講", "source_evidence_ids": ["E001"]}]},
            {"claim_id": "CL-2", "group_id": "CG-METHOD", "occurrences": [{"lecture": "第3講", "source_evidence_ids": ["E001"]}]},
        ]
    }
    source.write_text(f'<script id="d" type="application/json">{json.dumps(payload)}</script>', encoding="utf-8")
    claims_path.write_text(json.dumps(claims), encoding="utf-8")
    result = export_graph(source, destination, claims_path)
    assert result["evidence_nodes"][0]["claim_group_ids"] == ["CG-PRIMARY", "CG-METHOD"]
