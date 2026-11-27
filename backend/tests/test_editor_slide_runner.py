import json
from argparse import Namespace

import cv2
import numpy as np
import pytest

from backend.pipeline import editor_slide_runner as runner
from backend.pipeline import full_video_transcript, slide_visual_evidence as visuals
from backend.pipeline.slide_editor_alignment import parse_fidelity_result
from backend.pipeline.slide_change_pilot import file_sha, read_json, save_json


def test_fidelity_envelope_recovery_preserves_complete_rows():
    rows = [{"asset_id": "a", "passed": False}, {"asset_id": "b", "passed": True}]
    prefix = '{"results":' + json.dumps(rows)
    for text in [prefix + " trailing}", prefix[:-1] + "箍}", prefix + '损坏了格式。"}']:
        assert parse_fidelity_result(text, ["a", "b"]) == {"results": rows}
    for text in [prefix + ', "unexpected":true BROKEN}', prefix[:-8] + "}"]:
        with pytest.raises(ValueError):
            parse_fidelity_result(text, ["a", "b"])
    with pytest.raises(ValueError):
        parse_fidelity_result(prefix + " trailing}", ["a", "b", "c"])


def test_video_to_editor_rehearsal_and_retry(tmp_path, monkeypatch):
    item = "lecture"
    root = tmp_path / "store"
    review = root / "transcript_review" / item
    script = root / "script_review" / f"{item}.json"
    save_json(script, [{"index": "1", "type": "content", "text": "Text explanation"}])
    context = {
        "script_sha256": file_sha(script),
        "revision": 1,
        "groups": [],
        "paragraph_bindings": {"1": {"start_ms": 0, "end_ms": 5000}},
        "reviews": {},
        "occurrences": {},
        "visual_assets": {},
    }
    save_json(review / "context.json", context)
    video = tmp_path / "lecture.mp4"
    writer = cv2.VideoWriter(
        str(video), cv2.VideoWriter_fourcc(*"mp4v"), 10, (640, 480)
    )
    assert writer.isOpened()
    for i in range(50):
        frame = np.full((480, 640, 3), (130, 28, 15), np.uint8)
        cv2.putText(
            frame,
            "Alpha" if i < 25 else "Beta",
            (50, 150),
            cv2.FONT_HERSHEY_SIMPLEX,
            2,
            (255, 255, 255),
            4,
        )
        writer.write(frame)
    writer.release()
    calls = []

    class Model:
        model = "fixture"
        input_rate = output_rate = 0

        def __init__(self, provider, prompt, schema):
            self.provider, self.prompt, self.schema = provider, prompt, schema
            self.config = {"response_json_schema": schema}

        def contents(self, ids, paths):
            return [ids]

        def count(self, contents):
            return 0

        def text(self, raw):
            return json.dumps(raw)

        def usage(self, raw):
            return {"estimated_usd_upper": 0.0}

        def generate(self, contents):
            ids = contents[0]
            rows = []
            for i in ids:
                if self.schema == full_video_transcript.slides.REVIEW_SCHEMA:
                    row = dict(
                        candidate_id=i,
                        kind="new_slide",
                        confidence=1,
                        label="Test",
                        reason="New slide",
                    )
                elif self.schema == visuals.SCHEMA:
                    row = dict(
                        asset_id=i,
                        background="#001155",
                        has_diagram=False,
                        issues=[],
                        shapes=[],
                        edges=[],
                        text_regions=[
                            dict(
                                id="caption",
                                text="Test",
                                bbox=[100, 100, 400, 300],
                                color="#ffffff",
                                uncertain=False,
                            )
                        ],
                    )
                elif self.schema == runner.alignment.ALIGNMENT_SCHEMA:
                    row = dict(
                        event_id=i,
                        important=True,
                        category="exegetical_evidence_text",
                        aligned_paragraph_id="1",
                        helps_reader_understand="Explanation",
                        cross_channel_notes="",
                    )
                else:
                    row = dict(
                        asset_id=i,
                        passed=True,
                        severity="none",
                        issues=[],
                        summary="Matches",
                    )
                rows.append(row)
            calls.append(ids)
            return {"results": rows}

    for module in (full_video_transcript, visuals, runner.alignment):
        monkeypatch.setattr(module, "VisionReviewer", Model)
    args = Namespace(
        root=root,
        out=tmp_path / "work",
        item=item,
        video=video,
        sha256=file_sha(video),
        replay=None,
        apply=True,
    )
    result = runner.run(args)
    assert result["selected"] >= 1
    assert len(calls) >= 4
    before_calls = len(calls)
    after = (review / "context.json").read_bytes()
    assert runner.run(args) == result
    assert (review / "context.json").read_bytes() == after
    assert len(calls) == before_calls
    assert read_json(review / "context.json")["revision"] == 2


def test_full_lecture_saved_rehearsal(tmp_path, monkeypatch):
    """Opt-in integration test. Work exclusively on a copy of revision one."""
    import os
    import shutil
    from pathlib import Path
    from backend.api.sc_api.transcript_review import encoded
    from backend.pipeline.editor_slide_eval import grade

    fixture = os.environ.get("SLIDE_881_CAPTURE")
    if not fixture:
        pytest.skip(
            "Set SLIDE_881_CAPTURE and SLIDE_881_VIDEO for the saved 75-minute rehearsal"
        )
    capture = Path(fixture)
    video = Path(os.environ["SLIDE_881_VIDEO"])
    root = tmp_path / "store"
    shutil.copytree(capture / "rehearsal", root)
    item = video.stem
    review = root / "transcript_review" / item
    first = read_json(review / "revisions/00000001.json")
    second = review / "revisions/00000002.json"
    (review / "operations" / (read_json(second)["operation_id"] + ".json")).unlink()
    second.unlink()
    (review / "context.json").write_bytes(encoded(first["context"]))
    assert not first["context"]["visual_assets"]
    shutil.rmtree(review / "assets")
    originals = {
        p: file_sha(p)
        for p in root.rglob("*")
        if p.is_file() and p != review / "context.json"
    }

    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected model invocation in offline rehearsal")

    monkeypatch.setattr(runner, "produce", forbidden)
    # Exercise container recovery against the raw responses, including the
    # three malformed envelopes. Every recovered verdict must match the audit.
    for audit_path in capture.glob("part-*/fidelity/fidelity_audit.json"):
        audit = {r["asset_id"]: r for r in read_json(audit_path)["results"]}
        for raw_path in audit_path.parent.glob("fidelity_calls/calls/*/response.json"):
            raw = read_json(raw_path)
            ids = read_json(raw_path.parent / "request.json")["asset_ids"]
            parts = raw["candidates"][0]["content"]["parts"]
            text = "".join(p["text"] for p in parts if not p.get("thought"))
            recovered = parse_fidelity_result(text, ids)
            assert recovered["results"] == [audit[aid] for aid in ids]
    args = Namespace(
        root=root,
        out=tmp_path / "run",
        item=item,
        video=video,
        sha256=file_sha(video),
        replay=capture,
        apply=True,
    )
    result = runner.run(args)
    assert result["selected"] > 0
    first_run = {p: file_sha(p) for p in root.rglob("*") if p.is_file()}
    assert runner.run(args) == result
    assert first_run == {p: file_sha(p) for p in root.rglob("*") if p.is_file()}
    assert originals == {p: file_sha(p) for p in originals}
    ctx = read_json(review / "context.json")
    assert ctx["revision"] == 2
    for k in ("paragraph_bindings", "reviews", "script_sha256"):
        assert ctx[k] == first["context"][k]
    events = {e["id"]: e for e in read_json(capture / "slides/report.json")["events"]}
    for g in ctx["groups"]:
        sid = g["slide_id"]
        actual = ctx["occurrences"][sid]
        original = events[sid]
        assert actual["time_ms"] == original["time_ms"]
        assert actual["capture_ms"] == original["capture_ms"]
        a = ctx["visual_assets"][actual["asset_id"]]
        assert file_sha(root / a["image"]) == original["slide_sha256"]
        assert file_sha(root / a["svg_path"]) == a["svg_sha256"]
        assert 'data-background-mode="white"' in (root / a["svg_path"]).read_text()
    reference = read_json(
        Path(__file__).parent / "fixtures/slide_editor/matthew_2021_first.json"
    )
    scores = grade(
        reference,
        read_json(args.out / "slide_evidence.json"),
        read_json(args.out / "report.json"),
    )
    assert len(scores["matched"]) == len(reference["placements"])
    assert (
        not scores["missing"]
        and not scores["misplaced"]
        and not scores["incorrect_merge"]
    )
    # These two additional mixed-colour slides were expressly held for
    # the same colour loss. Preserve the original baseline and score the delta.
    assert scores["extra"] == scores["previously_held_attached"] == ["c-0011", "c-0345"]
    assert len(ctx["groups"]) == len(scores["matched"]) + len(scores["extra"])
    import xml.etree.ElementTree as ET

    for sid in [
        "c-0043",
        "c-0074",
        "c-0136",
        "c-0188",
        "c-0195",
        "c-0210",
        "c-0264",
        *scores["extra"],
    ]:
        aid = ctx["occurrences"][sid]["asset_id"]
        a = ctx["visual_assets"][aid]
        svg = ET.parse(root / a["svg_path"]).getroot()
        assert svg.get("data-text-renderer") == "source-glyphs.v1"
        assert svg.find("{*}desc").text == a["visible_text"]
        fills = {p.get("fill") for p in svg.findall("{*}path")}
        assert "#111827" in fills
        if sid != "c-0074":
            assert "#92400E" in fills


def test_only_verified_display_punctuation_is_eligible():
    assert runner.punctuation_width_only("minor", ["部分全形逗號被擷取為半形逗號"])
    assert runner.punctuation_width_only(
        "minor", ["Half-width comma instead of full-width comma."]
    )
    for severity, issues in [
        ("major", ["冒號誤提取為半形標點符號"]),
        ("minor", ["半形標點差異及遺漏經文"]),
        ("minor", ["缺少逗號"]),
        ("minor", []),
    ]:
        assert not runner.punctuation_width_only(severity, issues)


def test_source_colour_reconstruction_refuses_unrepresented_red_text():
    from backend.pipeline.slide_source_ink import source_ink_svg

    im = np.full((120, 200, 3), (90, 25, 8), np.uint8)
    for text, y, colour in [
        ("ab", 30, (248, 248, 248)),
        ("cd", 55, (0, 245, 245)),
        ("ef", 85, (0, 0, 240)),
    ]:
        cv2.putText(im, text, (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.65, colour, 2)
    content = {
        "has_diagram": False,
        "shapes": [],
        "edges": [],
        "text_regions": [{"text": "ab cd ef", "bbox": [0, 0, 1000, 1000]}],
    }
    assert source_ink_svg(im, content, "0" * 64) is None
