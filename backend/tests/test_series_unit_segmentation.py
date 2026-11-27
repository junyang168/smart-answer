import json
from pathlib import Path

import pytest

from backend.pipeline import claim_unit_plan as unit_plan
from backend.pipeline import series_unit_segmentation as seg


def _write_lecture(tmp_path: Path, name: str, texts: list[str]) -> Path:
    path = tmp_path / f"{name}.json"
    doc = {"script": [{"index": i + 1, "text": t} for i, t in enumerate(texts)]}
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def _make_two_lecture_series(tmp_path: Path) -> seg.ProjectedSeries:
    p1 = _write_lecture(
        tmp_path,
        "L1",
        [
            "",  # empty S0001
            "今天先講馬太福音第五章的結構與八福。",
            "第五章十七節說律法一點一畫也不能廢去，這不是很矛盾嗎？明天我們再繼續回答。",
        ],
    )
    p2 = _write_lecture(
        tmp_path,
        "L2",
        [
            "2011年紐約靈命進深會第二堂。",  # pure logistics
            "父神我們感謝祢，求祢帶領今天下午的時間。阿們。",  # pure prayer
            "我們接著昨天律法的矛盾來看宗主國之約，摩西律法與基督律法重疊的部分仍要守。",
            "接下來我們看不可姦淫與動淫念。",
        ],
    )
    specs = [
        seg.LectureSpec("L1", "src-L1", p1),
        seg.LectureSpec("L2", "src-L2", p2),
    ]
    return seg.load_series("test-series", specs)


class FakeClient:
    def __init__(self, model: str, handler):
        self.model = model
        self.handler = handler
        self.calls = []

    def generate_json(self, *, system_prompt, user_prompt, json_schema):
        payload = json.loads(user_prompt)
        self.calls.append(payload)
        return self.handler(payload)


def test_seam_excerpts_skip_pure_shell_paragraphs(tmp_path):
    series = _make_two_lecture_series(tmp_path)
    cards = {
        "L1": [
            {"pk": "L1-S0001", "chars": 0, "summary": "", "is_pure_shell": True},
            {"pk": "L1-S0002", "chars": 18, "summary": "八福結構", "is_pure_shell": False},
            {"pk": "L1-S0003", "chars": 36, "summary": "律法矛盾留待明天", "is_pure_shell": False},
        ],
        "L2": [
            {"pk": "L2-S0001", "chars": 15, "summary": "第二堂報幕", "is_pure_shell": True},
            {"pk": "L2-S0002", "chars": 23, "summary": "開會禱告", "is_pure_shell": True},
            {"pk": "L2-S0003", "chars": 38, "summary": "宗主國之約回答律法矛盾", "is_pure_shell": False},
            {"pk": "L2-S0004", "chars": 15, "summary": "動淫念", "is_pure_shell": False},
        ],
    }
    seams = seg.build_seam_excerpts(series, cards, window=2)
    assert len(seams) == 1
    assert [p["pk"] for p in seams[0]["prev_paragraphs"]] == ["L1-S0002", "L1-S0003"]
    # L2-S0001 (header) and L2-S0002 (prayer) must NOT crowd out L2-S0003 (actual continuation).
    assert [p["pk"] for p in seams[0]["next_paragraphs"]] == ["L2-S0003", "L2-S0004"]


def test_hard_invariants_reject_all_five_negative_fixtures(tmp_path):
    series = _make_two_lecture_series(tmp_path)
    good = {
        "units": [
            {
                "unit_id": "U01",
                "title": "八福結構",
                "passage": "太 5:3-12",
                "cross_lecture": False,
                "seam_evidence": None,
                "ranges": ["L1-S0002"],
            },
            {
                "unit_id": "U02",
                "title": "律法矛盾",
                "passage": "太 5:17-20",
                "cross_lecture": True,
                "seam_evidence": {
                    "prev_pk": "L1-S0003",
                    "prev_quote": "明天我們再繼續回答",
                    "next_pk": "L2-S0003",
                    "next_quote": "接著昨天律法的矛盾",
                },
                "ranges": ["L1-S0003", "L2-S0003"],
            },
            {
                "unit_id": "U03",
                "title": "動淫念",
                "passage": "太 5:28",
                "cross_lecture": False,
                "seam_evidence": None,
                "ranges": ["L2-S0004"],
            },
        ],
        "non_argument_paragraphs": [
            {"pk": "L1-S0001", "reason": "empty"},
            {"pk": "L2-S0001", "reason": "logistics"},
            {"pk": "L2-S0002", "reason": "prayer"},
        ],
    }
    validated = seg.validate_segmentation(series, good)
    assert len(validated["units"]) == 3

    # 1. Overlap
    overlap = json.loads(json.dumps(good))
    overlap["units"][0]["ranges"] = ["L1-S0002..L1-S0003"]
    with pytest.raises(ValueError, match="Overlapping"):
        seg.validate_segmentation(series, overlap)

    # 2. Missing paragraph
    missing = json.loads(json.dumps(good))
    missing["units"].pop()
    with pytest.raises(ValueError, match="Missing paragraphs"):
        seg.validate_segmentation(series, missing)

    # 3. Reversed range
    rev = json.loads(json.dumps(good))
    rev["units"][0]["ranges"] = ["L1-S0003..L1-S0002"]
    with pytest.raises(ValueError, match="Reversed range"):
        seg.validate_segmentation(series, rev)

    # 4. Empty paragraph inside unit
    empty_in = json.loads(json.dumps(good))
    empty_in["units"][0]["ranges"] = ["L1-S0001..L1-S0002"]
    empty_in["non_argument_paragraphs"] = [x for x in empty_in["non_argument_paragraphs"] if x["pk"] != "L1-S0001"]
    with pytest.raises(ValueError, match="Empty paragraph"):
        seg.validate_segmentation(series, empty_in)

    # 5. Fabricated seam_evidence (not verbatim substring)
    bad_seam = json.loads(json.dumps(good))
    bad_seam["units"][1]["seam_evidence"]["prev_quote"] = "原文裡根本沒這句話"
    with pytest.raises(ValueError, match="verbatim substring"):
        seg.validate_segmentation(series, bad_seam)

    # 6. cross_lecture flag contradiction
    bad_flag = json.loads(json.dumps(good))
    bad_flag["units"][1]["cross_lecture"] = False
    with pytest.raises(ValueError, match="cross_lecture"):
        seg.validate_segmentation(series, bad_flag)


def test_empty_paragraph_auto_stripping_and_manifest_compilation(tmp_path):
    p1 = _write_lecture(tmp_path, "L1", ["開頭解經。", "", "", "接續解經。"])
    series = seg.load_series("s", [seg.LectureSpec("L1", "src-L1", p1)])
    raw = {
        "units": [
            {
                "unit_id": "U01",
                "title": "整段",
                "passage": "太 5:1",
                "cross_lecture": False,
                "seam_evidence": None,
                "ranges": ["L1-S0001..L1-S0004"],
            }
        ],
        "non_argument_paragraphs": [],
    }
    norm = seg.normalize_empty_paragraphs(series, raw)
    assert norm["units"][0]["ranges"] == ["L1-S0001", "L1-S0004"]
    validated = seg.validate_segmentation(series, norm)
    manifests = seg.compile_unit_manifests(series, validated, tmp_path / "manifests")
    reading = unit_plan.read_unit(manifests["U01"])
    assert [s["paragraph"] for s in reading.sentences] == ["S0001", "S0004"]


def test_cross_vendor_seam_verify_catches_silent_cut_and_ruling_staleness_is_per_unit(tmp_path):
    series = _make_two_lecture_series(tmp_path)
    cards = {
        "L1": [
            {"pk": "L1-S0001", "chars": 0, "summary": "", "is_pure_shell": True},
            {"pk": "L1-S0002", "chars": 18, "summary": "八福", "is_pure_shell": False},
            {"pk": "L1-S0003", "chars": 36, "summary": "律法矛盾", "is_pure_shell": False},
        ],
        "L2": [
            {"pk": "L2-S0001", "chars": 15, "summary": "報幕", "is_pure_shell": True},
            {"pk": "L2-S0002", "chars": 23, "summary": "禱告", "is_pure_shell": True},
            {"pk": "L2-S0003", "chars": 38, "summary": "續講律法矛盾", "is_pure_shell": False},
            {"pk": "L2-S0004", "chars": 15, "summary": "動淫念", "is_pure_shell": False},
        ],
    }
    # Artificially split L1-S0003 and L2-S0003 into separate units (passes all hard invariants!).
    split_seg = seg.validate_segmentation(
        series,
        {
            "units": [
                {"unit_id": "U01", "title": "L1", "passage": "太 5", "cross_lecture": False, "seam_evidence": None, "ranges": ["L1-S0002..L1-S0003"]},
                {"unit_id": "U02", "title": "L2", "passage": "太 5", "cross_lecture": False, "seam_evidence": None, "ranges": ["L2-S0003..L2-S0004"]},
            ],
            "non_argument_paragraphs": [
                {"pk": "L1-S0001", "reason": "empty"},
                {"pk": "L2-S0001", "reason": "logistics"},
                {"pk": "L2-S0002", "reason": "prayer"},
            ],
        },
    )
    seam_client = FakeClient(
        "claude-opus-5-5",
        lambda p: {"should_merge": True, "severity": "high", "reason": "L2 直接續講 L1 律法矛盾", "evidence_quote": "接著昨天律法的矛盾"},
    )
    findings = seg.verify_split_seams(series, split_seg, cards, seam_client, tmp_path / "cache")
    assert len(findings) == 1 and findings[0]["should_merge"] is True and findings[0]["severity"] == "high"

    # Per-unit ruling staleness: editing L2 should only invalidate U02, keeping U01 valid_ruling.
    ruling_path = tmp_path / "ruling.json"
    ruling_path.write_text(json.dumps(split_seg, ensure_ascii=False), encoding="utf-8")
    p2_path = Path(series.lectures[1].spec.path)
    doc2 = json.loads(p2_path.read_text(encoding="utf-8"))
    doc2["script"][2]["text"] += "（修訂錯字）"
    p2_path.write_text(json.dumps(doc2, ensure_ascii=False), encoding="utf-8")
    reloaded = seg.load_series("test-series", [lec.spec for lec in series.lectures])
    statuses = seg.evaluate_ruling_staleness(reloaded, ruling_path)
    assert statuses == {"U01": "valid_ruling", "U02": "stale_ruling"}


def test_validate_segmentation_normalizes_unit_ids_and_enforces_72k_default(tmp_path):
    series = _make_two_lecture_series(tmp_path)
    raw = {
        "units": [
            {"unit_id": "U001", "title": "L1", "passage": "太 5", "cross_lecture": False, "seam_evidence": None, "ranges": ["L1-S0002..L1-S0003"]},
            {"unit_id": "U002", "title": "L2", "passage": "太 5", "cross_lecture": False, "seam_evidence": None, "ranges": ["L2-S0003..L2-S0004"]},
        ],
        "non_argument_paragraphs": [
            {"pk": "L1-S0001", "reason": "empty"},
            {"pk": "L2-S0001", "reason": "logistics"},
            {"pk": "L2-S0002", "reason": "prayer"},
        ],
    }
    validated = seg.validate_segmentation(series, raw)
    assert [u["unit_id"] for u in validated["units"]] == ["U01", "U02"]
