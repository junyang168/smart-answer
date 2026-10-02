from __future__ import annotations

import importlib.util
import json
import hashlib
from pathlib import Path

from backend.pipeline.source_projection import project_script


SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "audit-library.py"
SPEC = importlib.util.spec_from_file_location("audit_library_script", SCRIPT_PATH)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def test_modern_audit_uses_body_coordinates_and_body_identity(tmp_path: Path) -> None:
    script = [
        {"index": "subtitle-1", "type": "subtitle", "text": "## 编辑标题"},
        {"index": 17, "start_time": 1, "end_time": 2, "text": "第一句"},
        {"index": "comment-1", "type": "comment", "text": "编辑备注"},
        {"index": 18, "start_time": 3, "end_time": 4, "text": "第二句"},
    ]
    path = tmp_path / "source.json"
    path.write_text(json.dumps({"script": script}, ensure_ascii=False), encoding="utf-8")

    source = AUDIT.SourceFile(
        path, "sermon_transcript", {"locator_space": "spoken_body_v1"}
    )
    assert source.by_ordinal(1)["index"] == 17
    assert source.by_ordinal(2)["index"] == 18
    assert source.body_sha256 == project_script(script).body_sha256


def test_visual_locator_requires_exact_block_and_sha(tmp_path: Path) -> None:
    svg = '<svg><text x="1">图</text></svg>'
    script = [{"index": 9, "text": f"说明\n{svg}"}]
    path = tmp_path / "visual.json"
    path.write_text(json.dumps({"script": script}, ensure_ascii=False), encoding="utf-8")
    source = AUDIT.SourceFile(
        path, "sermon_transcript", {"locator_space": "spoken_body_v1"}
    )
    segment = source.by_ordinal(1)
    import hashlib

    sha = hashlib.sha256(svg.encode("utf-8")).hexdigest()
    assert source.excerpt_matches(segment, svg, visual_ordinal=1, visual_sha256=sha)
    assert not source.excerpt_matches(segment, svg, visual_ordinal=1, visual_sha256="0" * 64)


def test_linked_svg_audit_reopens_asset_and_requires_exact_link(tmp_path: Path) -> None:
    svg = '<svg><text>图</text></svg>'
    asset = tmp_path / "diagram.svg"
    asset.write_text(svg)
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    url = "https://example.org/diagram.svg"
    link = f"![图]({url})"
    manuscript = tmp_path / "final.md"
    manuscript.write_text(link)
    descriptor = {
        "locator": "S0001/V01", "binding_kind": "linked_svg_asset",
        "source_path": str(asset), "source_url": url,
        "source_file_sha256": digest, "raw_sha256": digest, "raw_svg": svg,
        "char_start": 0, "char_end": len(link),
    }
    payload = {
        "source_modality": "visual", "visual_locator": "S0001/V01",
        "visual_source_path": str(asset), "visual_source_url": url,
        "visual_source_file_sha256": digest, "visual_block_sha256": digest,
    }
    source = AUDIT.SourceFile(manuscript, "notes_manuscript", {"visual_sources": [descriptor]})
    segment = source.by_ordinal(1)
    assert source.linked_visual_matches(segment, svg, payload)
    assert not source.linked_visual_matches({"text": "没有链接"}, svg, payload)
    assert not source.linked_visual_matches(segment, svg, {**payload, "visual_block_sha256": "0" * 64})
    assert not source.linked_visual_matches(segment, svg, {**payload, "visual_source_path": "/wrong.svg"})
    assert not source.linked_visual_matches(segment, svg, {**payload, "source_modality": "spoken"})
    source.visual_sources.append(descriptor)
    assert not source.linked_visual_matches(segment, svg, payload)
    source.visual_sources.pop()
    asset.write_text('<svg><text>改图</text></svg>')
    assert not source.linked_visual_matches(segment, svg, payload)
