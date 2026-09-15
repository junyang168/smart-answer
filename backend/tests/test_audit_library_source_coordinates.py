from __future__ import annotations

import importlib.util
import json
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
