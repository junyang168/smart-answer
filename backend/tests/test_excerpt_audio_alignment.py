from __future__ import annotations

import hashlib
import json
from pathlib import Path

from backend.pipeline.excerpt_audio_alignment import (
    align_excerpt,
    align_transcript_excerpt,
    project_excerpt_timings,
)


def _write(path: Path, value: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sources(tmp_path: Path, *, reviewed_word: str = "Petros") -> tuple[Path, Path, str]:
    published = tmp_path / "script_published" / "讲道一.json"
    raw = tmp_path / "script" / "讲道一.json"
    published_sha = _write(
        published,
        {
            "script": [
                {
                    "index": 10,
                    "start_index": 10,
                    "end_index": 12,
                    "start_time": 1,
                    "end_time": 12,
                    "text": (
                        "下面很重要。若教会建立在彼得身上，祂就不会说："
                        f"‘你是彼得（{reviewed_word}）’，阳性；"
                        "‘建造在磐石（Petra）上’，是阴性。"
                    ),
                }
            ]
        },
    )
    _write(
        raw,
        {
            "entries": [
                {
                    "index": 10,
                    "start_ms": 1000,
                    "end_ms": 4000,
                    "text": "下面很重要。若教会建立在彼得身上，",
                },
                {
                    "index": 11,
                    "start_ms": 4000,
                    "end_ms": 8000,
                    "text": "祂就不会说你是彼得 Petros，阳性；",
                },
                {
                    "index": 12,
                    "start_ms": 8000,
                    "end_ms": 12000,
                    "text": "建造在磐石 Petra 上，是阴性。",
                },
            ]
        },
    )
    return published, raw, published_sha


def test_exact_excerpt_uses_raw_entry_times_not_published_paragraph_start(
    tmp_path: Path,
) -> None:
    published, raw, published_sha = _sources(tmp_path)
    timing = align_excerpt(
        fragment={
            "paragraph_key": "S0001",
            "source_segment_index": 10,
            "verbatim_excerpt": "你是彼得（Petros），阳性；建造在磐石（Petra）上，是阴性。",
        },
        source={"source_sha256": published_sha},
        published_path=published,
        raw_path=raw,
    )

    assert timing["status"] == "exact"
    assert timing["method"] == "normalized_exact"
    assert (timing["excerpt_start_time"], timing["excerpt_end_time"]) == (4.0, 12.0)
    assert (timing["raw_start_index"], timing["raw_end_index"]) == (11, 12)
    assert timing["reviewed_text_differs_from_raw"] is False
    assert len(timing["alignment_sha256"]) == 64


def test_reviewed_lexical_correction_is_aligned_but_disclosed(tmp_path: Path) -> None:
    published, raw, published_sha = _sources(tmp_path, reviewed_word="Petrus")
    timing = align_excerpt(
        fragment={
            "paragraph_key": "S0001",
            "source_segment_index": 10,
            "verbatim_excerpt": "你是彼得（Petrus），阳性；建造在磐石（Petra）上，是阴性。",
        },
        source={"source_sha256": published_sha},
        published_path=published,
        raw_path=raw,
    )

    assert timing["status"] == "estimated"
    assert timing["method"] == "sequence_aligned"
    assert timing["match_ratio"] > 0.9
    assert (timing["excerpt_start_time"], timing["excerpt_end_time"]) == (4.0, 12.0)
    assert timing["reviewed_text_differs_from_raw"] is True


def test_alignment_recovers_small_published_lineage_end_error(tmp_path: Path) -> None:
    published = tmp_path / "script_published" / "讲道二.json"
    raw = tmp_path / "script" / "讲道二.json"
    published_sha = _write(
        published,
        {
            "script": [
                {
                    "index": 782,
                    "start_index": 782,
                    # The published paragraph wrongly stops before its last
                    # three raw rows, matching the measured production defect.
                    "end_index": 794,
                    "text": (
                        "所以我刚才已经给你看过了，所以罗马天主教会错就错在"
                        "这个地方，就只能强调彼得，说彼得是第一任教皇，其实没有。"
                    ),
                }
            ]
        },
    )
    _write(
        raw,
        {
            "entries": [
                {
                    "index": 793,
                    "start_ms": 1_966_198,
                    "end_ms": 1_972_198,
                    "text": "所以我刚才已经给你看过了",
                },
                {
                    "index": 794,
                    "start_ms": 1_973_798,
                    "end_ms": 1_975_398,
                    "text": "但是错就错在这个地方",
                },
                {
                    "index": 795,
                    "start_ms": 1_976_278,
                    "end_ms": 1_977_958,
                    "text": "就只能强调彼得",
                },
                {
                    "index": 796,
                    "start_ms": 1_978_318,
                    "end_ms": 1_982_758,
                    "text": "说彼得是第一任教皇",
                },
                {
                    "index": 797,
                    "start_ms": 1_983_078,
                    "end_ms": 1_985_038,
                    "text": "其实没有",
                },
            ]
        },
    )
    timing = align_excerpt(
        fragment={
            "source_segment_index": 782,
            "verbatim_excerpt": (
                "所以我刚才已经给你看过了，所以罗马天主教会错就错在"
                "这个地方，就只能强调彼得，说彼得是第一任教皇，其实没有。"
            ),
        },
        source={"source_sha256": published_sha},
        published_path=published,
        raw_path=raw,
    )

    assert timing["status"] == "estimated"
    assert timing["lineage_window_expanded"] is True
    assert timing["raw_end_index"] == 797
    assert timing["excerpt_end_time"] == 1985.038


def test_original_excerpt_resolves_unique_published_paragraph(tmp_path: Path) -> None:
    published, raw, published_sha = _sources(tmp_path)
    timing = align_transcript_excerpt(
        excerpt="你是彼得（Petros），阳性；建造在磐石（Petra）上，是阴性。",
        source={"source_sha256": published_sha},
        published_path=published,
        raw_path=raw,
    )

    assert timing["status"] == "exact"
    assert (timing["excerpt_start_time"], timing["excerpt_end_time"]) == (4.0, 12.0)


def test_projection_preserves_paragraph_timing_and_adds_sha_bound_excerpt_timing(
    tmp_path: Path,
) -> None:
    published, _, published_sha = _sources(tmp_path)
    knowledge = {
        "source_documents": [
            {
                "source_id": "SRC-1",
                "source_type": "sermon_transcript",
                "transcript_id": "讲道一",
                "source_path": str(published),
                "source_sha256": published_sha,
            }
        ],
        "source_fragments": [
            {
                "fragment_id": "FR-1",
                "source_id": "SRC-1",
                "paragraph_key": "S0001",
                "source_segment_index": 10,
                "media_time": 1,
                "media_end_time": 12,
                "verbatim_excerpt": "你是彼得（Petros），阳性；",
            }
        ],
    }

    projected = project_excerpt_timings(knowledge, data_base_path=tmp_path)
    original = knowledge["source_fragments"][0]
    fragment = projected["source_fragments"][0]

    assert "excerpt_timing" not in original
    assert (fragment["media_time"], fragment["media_end_time"]) == (1, 12)
    assert (fragment["excerpt_media_time"], fragment["excerpt_media_end_time"]) == (
        4.0,
        8.0,
    )
    assert fragment["excerpt_timing"]["published_source_sha256"] == published_sha
    assert fragment["excerpt_timing"]["raw_timed_source_sha256"] == hashlib.sha256(
        (tmp_path / "script" / "讲道一.json").read_bytes()
    ).hexdigest()


def test_source_sha_mismatch_never_claims_precise_alignment(tmp_path: Path) -> None:
    published, raw, _ = _sources(tmp_path)
    timing = align_excerpt(
        fragment={"paragraph_key": "S0001", "verbatim_excerpt": "你是彼得"},
        source={"source_sha256": "not-the-published-sha"},
        published_path=published,
        raw_path=raw,
    )

    assert timing["status"] == "unresolved"
    assert timing["excerpt_start_time"] is None
    assert "SHA" in timing["reason"]
