from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.api import wang_article_reviews


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _fixture(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Path]:
    staging = tmp_path / "staging"
    authoring = staging / "topic-essays" / "church-foundation" / "authoring-v1"
    manuscript = authoring / "draft.md"
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text(
        '# 标题\n\n<!-- provenance: {"attribution":"professor","claim_ids":["CL-1"]} -->\n正文。',
        encoding="utf-8",
    )
    workflow = authoring / "workflow-status.json"
    _write_json(workflow, {"status": "drafted_grounding_not_run"})
    packet = authoring / "topic-authoring-packet.json"
    _write_json(
        packet,
        {
            "result": {
                "packet_sha256": "packet-sha",
                "knowledge": {
                    "claims": [{"claim_id": "CL-1", "evidence_step_ids": ["ES-1"]}],
                    "evidence_steps": [
                        {
                            "evidence_step_id": "ES-1",
                            "source_fragment_ids": ["FR-1", "FR-1", "FR-NOTES"],
                        }
                    ],
                    "source_fragments": [
                        {
                            "fragment_id": "FR-1",
                            "source_id": "SRC-1",
                            "media_time": 65,
                            "media_end_time": 82,
                            "verbatim_excerpt": "教授逐字稿原句。",
                        },
                        {
                            "fragment_id": "FR-NOTES",
                            "source_id": "NOTES-1",
                            "verbatim_excerpt": "母本中的对应段落。",
                        },
                    ],
                    "source_documents": [
                        {
                            "source_id": "SRC-1",
                            "source_type": "sermon_transcript",
                            "title": "讲道一",
                            "transcript_id": "讲道一",
                        },
                        {
                            "source_id": "NOTES-1",
                            "source_type": "notes_manuscript",
                            "title": "十六章母本",
                            "source_url": "/resources/notes_to_manuscript_series/series/十六章",
                        },
                    ],
                },
            }
        },
    )
    manifest_root = staging / "topic-essay-reviews"
    _write_json(
        manifest_root / "church-foundation-v1.json",
        {
            "schema_version": wang_article_reviews.MANIFEST_SCHEMA,
            "review_id": "church-foundation-v1",
            "title": "标题",
            "passage": "太16:16-23；弗2:20",
            "registered_at": "2026-08-28T00:00:00+00:00",
            "manuscript_relative_path": str(manuscript.relative_to(staging)),
            "manuscript_sha256": hashlib.sha256(manuscript.read_bytes()).hexdigest(),
            "workflow_status_relative_path": str(workflow.relative_to(staging)),
            "workflow_status_sha256": hashlib.sha256(workflow.read_bytes()).hexdigest(),
            "authoring_packet_relative_path": str(packet.relative_to(staging)),
            "authoring_packet_file_sha256": hashlib.sha256(packet.read_bytes()).hexdigest(),
            "authoring_packet_sha256": "packet-sha",
            "brief_sha256": "brief-sha",
        },
    )
    monkeypatch.setattr(wang_article_reviews, "WANG_STAGING_DIR", staging)
    monkeypatch.setattr(wang_article_reviews, "REVIEW_MANIFEST_ROOT", manifest_root)
    return manuscript, manifest_root, packet


def test_internal_review_is_sha_bound_and_not_a_publication(tmp_path: Path, monkeypatch) -> None:
    _fixture(tmp_path, monkeypatch)

    result = wang_article_reviews.article_review("church-foundation-v1")

    assert result["status"] == "internal_review"
    assert result["integrity_status"] == "verified"
    assert "provenance" not in result["markdown"]
    assert "正文。" in result["markdown"]
    assert "#review-source-evidence-p1" in result["markdown"]
    assert len(result["source_annotations"]) == 1
    assert [item["fragment_ids"] for item in result["source_annotations"][0]["sources"]] == [
        ["FR-1"],
        ["FR-NOTES"],
    ]
    transcript, notes = result["source_annotations"][0]["sources"]
    assert transcript["full_source_url"] == "/resources/sermons/%E8%AE%B2%E9%81%93%E4%B8%80"
    assert transcript["media"]["start_seconds"] == 65
    assert notes["full_source_url"] == "/resources/notes_to_manuscript_series/series/十六章"
    assert [item["state"] for item in result["stage_checks"]] == [
        "complete", "not_run", "not_run", "not_run", "not_run"
    ]
    assert "publication_decision" not in result


def test_review_projects_excerpt_level_audio_from_raw_timed_transcript(
    tmp_path: Path, monkeypatch
) -> None:
    _, _, packet = _fixture(tmp_path, monkeypatch)
    data = tmp_path / "data"
    published_path = data / "script_published" / "讲道一.json"
    raw_path = data / "script" / "讲道一.json"
    _write_json(
        published_path,
        {
            "script": [
                {
                    "index": 100,
                    "start_index": 100,
                    "end_index": 101,
                    "start_time": 50,
                    "end_time": 82,
                    "text": "前面是别的内容。教授逐字稿原句。",
                }
            ]
        },
    )
    _write_json(
        raw_path,
        {
            "entries": [
                {
                    "index": 100,
                    "start_ms": 50000,
                    "end_ms": 65000,
                    "text": "前面是别的内容。",
                },
                {
                    "index": 101,
                    "start_ms": 65000,
                    "end_ms": 82000,
                    "text": "教授逐字稿原句。",
                },
            ]
        },
    )
    payload = json.loads(packet.read_text(encoding="utf-8"))
    knowledge = payload["result"]["knowledge"]
    knowledge["source_documents"][0]["source_path"] = str(published_path)
    knowledge["source_documents"][0]["source_sha256"] = hashlib.sha256(
        published_path.read_bytes()
    ).hexdigest()
    knowledge["source_fragments"][0].update(
        {
            "paragraph_key": "S0001",
            "source_segment_index": 100,
            "media_time": 50,
            "media_end_time": 82,
        }
    )
    _write_json(packet, payload)
    manifest_path = packet.parents[3] / "topic-essay-reviews" / "church-foundation-v1.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["authoring_packet_file_sha256"] = hashlib.sha256(packet.read_bytes()).hexdigest()
    _write_json(manifest_path, manifest)
    monkeypatch.setattr(wang_article_reviews, "DATA_BASE_PATH", data)

    review = wang_article_reviews.article_review("church-foundation-v1")

    media = review["source_annotations"][0]["sources"][0]["media"]
    assert media["paragraph_start_seconds"] == 50
    assert media["excerpt_start_seconds"] == 65
    assert media["start_seconds"] == 63
    assert media["end_seconds"] == 84
    assert media["timing_status"] == "exact"
    assert review["source_playback_audit"]["passed"] is True
    assert review["source_playback_audit"]["exact_clips"] == 1
    assert review["source_playback_audit"]["paragraph_fallback_clips"] == 0


def test_published_workflow_reports_every_completed_stage() -> None:
    checks = wang_article_reviews._stage_checks({"status": "workflow_published"})

    assert [item["state"] for item in checks] == [
        "complete",
        "passed",
        "passed",
        "passed",
        "passed",
    ]


def test_changed_manuscript_invalidates_review_preview(tmp_path: Path, monkeypatch) -> None:
    manuscript, _, _ = _fixture(tmp_path, monkeypatch)
    manuscript.write_text(manuscript.read_text(encoding="utf-8") + "\n改变。", encoding="utf-8")

    listing = wang_article_reviews.list_article_reviews()
    assert listing["reviews"][0]["integrity_status"] == "changed"
    with pytest.raises(HTTPException) as exc:
        wang_article_reviews.article_review("church-foundation-v1")
    assert exc.value.status_code == 409


def test_review_manifest_cannot_escape_staging(tmp_path: Path, monkeypatch) -> None:
    _, manifest_root, _ = _fixture(tmp_path, monkeypatch)
    manifest_path = manifest_root / "church-foundation-v1.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["manuscript_relative_path"] = "../../secret.md"
    _write_json(manifest_path, manifest)

    listing = wang_article_reviews.list_article_reviews()
    assert listing["reviews"] == []
    assert "leaves Wang staging" in listing["warnings"][0]["message"]


def test_changed_authoring_packet_invalidates_review_preview(tmp_path: Path, monkeypatch) -> None:
    _, _, packet = _fixture(tmp_path, monkeypatch)
    packet.write_text(packet.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    listing = wang_article_reviews.list_article_reviews()
    assert listing["reviews"][0]["integrity_status"] == "changed"
    with pytest.raises(HTTPException) as exc:
        wang_article_reviews.article_review("church-foundation-v1")
    assert exc.value.status_code == 409


def test_paragraph_without_verifiable_fragments_has_no_empty_source_control(
    tmp_path: Path, monkeypatch
) -> None:
    manuscript, _, packet = _fixture(tmp_path, monkeypatch)
    payload = json.loads(packet.read_text(encoding="utf-8"))
    payload["result"]["knowledge"]["source_fragments"] = []
    _write_json(packet, payload)
    manifest_path = packet.parents[3] / "topic-essay-reviews" / "church-foundation-v1.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["authoring_packet_file_sha256"] = hashlib.sha256(packet.read_bytes()).hexdigest()
    _write_json(manifest_path, manifest)

    result = wang_article_reviews.article_review("church-foundation-v1")

    assert result["source_annotations"] == []
    assert "review-source-evidence" not in result["markdown"]
    assert manuscript.read_text(encoding="utf-8").endswith("正文。")


def test_claim_fallback_for_block_quote_selects_only_the_matching_fragment(
    tmp_path: Path, monkeypatch
) -> None:
    manuscript, _, packet = _fixture(tmp_path, monkeypatch)
    manuscript.write_text(
        '# 标题\n\n<!-- provenance: {"attribution":"professor","claim_ids":["CL-1"]} -->\n'
        '> 「教授逐字稿原句。」',
        encoding="utf-8",
    )
    manifest_path = packet.parents[3] / "topic-essay-reviews" / "church-foundation-v1.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["manuscript_sha256"] = hashlib.sha256(manuscript.read_bytes()).hexdigest()
    _write_json(manifest_path, manifest)

    review = wang_article_reviews.article_review("church-foundation-v1")

    sources = review["source_annotations"][0]["sources"]
    assert [source["fragment_ids"] for source in sources] == [["FR-1"]]


def test_footnote_keeps_its_own_source_annotation(tmp_path: Path, monkeypatch) -> None:
    manuscript, _, packet = _fixture(tmp_path, monkeypatch)
    manuscript.write_text(
        '# 标题\n\n正文。[^1]\n\n'
        '<!-- provenance: {"attribution":"professor","claim_ids":["CL-1"]} -->\n'
        '[^1]: 脚注中的来源判断。',
        encoding="utf-8",
    )
    manifest_path = packet.parents[3] / "topic-essay-reviews" / "church-foundation-v1.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["manuscript_sha256"] = hashlib.sha256(manuscript.read_bytes()).hexdigest()
    _write_json(manifest_path, manifest)

    review = wang_article_reviews.article_review("church-foundation-v1")

    assert len(review["source_annotations"]) == 1
    assert (
        "[^1]: 脚注中的来源判断。 [查看本注来源](#review-source-evidence-p1)"
        in review["markdown"]
    )


def test_section_argument_route_selects_step_bound_fragments_instead_of_all_claim_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    manuscript, _, packet = _fixture(tmp_path, monkeypatch)
    manuscript.write_text(
        '# 标题\n\n## 教皇推论\n\n'
        '<!-- provenance: {"attribution":"professor","claim_ids":["CL-POPE"]} -->\n'
        '不能由这段经文推出彼得是第一任教皇。',
        encoding="utf-8",
    )
    payload = json.loads(packet.read_text(encoding="utf-8"))
    result = payload["result"]
    result["editorial_decisions"] = {
        "sections": [
            {
                "heading": "教皇推论",
                "argument_route_revision_ids": ["ARR-POPE"],
            }
        ]
    }
    result["knowledge"]["claims"].append(
        {
            "claim_id": "CL-POPE",
            "evidence_step_ids": ["ES-UNRELATED", "ES-POPE"],
        }
    )
    result["knowledge"]["evidence_steps"].extend(
        [
            {
                "evidence_step_id": "ES-UNRELATED",
                "source_fragment_ids": ["FR-UNRELATED"],
            },
            {
                "evidence_step_id": "ES-POPE",
                "source_fragment_ids": ["FR-POPE"],
            },
        ]
    )
    result["knowledge"]["source_fragments"].extend(
        [
            {
                "fragment_id": "FR-UNRELATED",
                "source_id": "SRC-1",
                "media_time": 10,
                "media_end_time": 20,
                "verbatim_excerpt": "彼得受到责备。",
            },
            {
                "fragment_id": "FR-POPE",
                "source_id": "SRC-1",
                "media_time": 30,
                "media_end_time": 40,
                "verbatim_excerpt": "说彼得是第一任教皇，其实没有这回事。",
            },
        ]
    )
    result["argument_routes"] = [
        {
            "revision": {
                "argument_route_revision_id": "ARR-POPE",
                "route_label": "由经文论证反驳首任教皇说",
                "ordered_inference_nodes": [
                    {
                        "route_step_key": "C1",
                        "role": "conclusion",
                        "normalized_proposition": "不能推出彼得是第一任教皇。",
                    }
                ],
            },
            "attestations": [
                {
                    "source_id": "SRC-1",
                    "claim_ids": ["CL-POPE"],
                    "step_bindings": [
                        {
                            "route_step_key": "C1",
                            "source_fragment_ids": ["FR-POPE"],
                        }
                    ],
                }
            ],
        }
    ]
    result["argument_routes"][0]["attestations"].append(
        dict(result["argument_routes"][0]["attestations"][0])
    )
    _write_json(packet, payload)
    manifest_path = packet.parents[3] / "topic-essay-reviews" / "church-foundation-v1.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["manuscript_sha256"] = hashlib.sha256(manuscript.read_bytes()).hexdigest()
    manifest["authoring_packet_file_sha256"] = hashlib.sha256(packet.read_bytes()).hexdigest()
    _write_json(manifest_path, manifest)

    review = wang_article_reviews.article_review("church-foundation-v1")

    assert len(review["source_annotations"][0]["sources"]) == 1
    source = review["source_annotations"][0]["sources"][0]
    assert source["mapping_kind"] == "argument_route_attestation"
    assert source["route_revision_id"] == "ARR-POPE"
    assert source["route_label"] == "由经文论证反驳首任教皇说"
    assert source["excerpts"] == ["说彼得是第一任教皇，其实没有这回事。"]
    assert len(source["route_steps"]) == 1
    step = source["route_steps"][0]
    assert step["route_step_key"] == "C1"
    assert step["role"] == "conclusion"
    assert step["proposition"] == "不能推出彼得是第一任教皇。"
    assert step["fragment_ids"] == ["FR-POPE"]
    assert step["excerpts"] == ["说彼得是第一任教皇，其实没有这回事。"]
    assert len(step["media_clips"]) == 1
    assert step["media_clips"][0]["start_seconds"] == 30
    assert step["media_clips"][0]["timing_status"] == "unresolved"
    assert source["media"] is None


def test_route_conclusion_does_not_substitute_viewpoint_editorial_wording() -> None:
    packet = {
        "knowledge": {
            "claims": [{"claim_id": "CL-1", "evidence_step_ids": ["ES-1"]}],
            "source_fragments": [
                {
                    "fragment_id": "FR-1",
                    "source_id": "NOTES-1",
                    "verbatim_excerpt": "或者是信仰，或者是所传的真理。",
                }
            ],
            "source_documents": [
                {
                    "source_id": "NOTES-1",
                    "source_type": "notes_manuscript",
                    "title": "母本",
                }
            ],
        },
        "viewpoints": [
            {
                "revision": {
                    "viewpoint_revision_id": "CVR-1",
                    "core_proposition": "信仰也就是所传的真理。",
                }
            }
        ],
        "argument_routes": [
            {
                "revision": {
                    "argument_route_revision_id": "ARR-1",
                    "route_label": "两种解释",
                    "ordered_inference_nodes": [
                        {
                            "route_step_key": "C1",
                            "role": "conclusion",
                            "conclusion_viewpoint_revision_id": "CVR-1",
                        }
                    ],
                },
                "attestations": [
                    {
                        "source_id": "NOTES-1",
                        "claim_ids": ["CL-1"],
                        "step_bindings": [
                            {
                                "route_step_key": "C1",
                                "evidence_step_ids": ["ES-1"],
                                "source_fragment_ids": ["FR-1"],
                            }
                        ],
                    }
                ],
            }
        ],
    }

    sources = wang_article_reviews._route_sources(
        {"claim_ids": ["CL-1"]}, packet, ["ARR-1"], {}
    )

    assert sources[0]["route_steps"][0]["proposition"] is None
    assert sources[0]["route_steps"][0]["excerpts"] == [
        "或者是信仰，或者是所传的真理。"
    ]


def test_non_contiguous_route_steps_keep_separate_audio_clips() -> None:
    packet = {
        "knowledge": {
            "claims": [{"claim_id": "CL-1", "evidence_step_ids": ["ES-1", "ES-2"]}],
            "source_fragments": [
                {
                    "fragment_id": "FR-1",
                    "source_id": "SRC-1",
                    "verbatim_excerpt": "第一项证据。",
                    "media_time": 10,
                    "media_end_time": 20,
                    "excerpt_media_time": 12,
                    "excerpt_media_end_time": 16,
                    "excerpt_timing": {
                        "status": "exact",
                        "method": "normalized_exact",
                        "match_ratio": 1,
                        "reviewed_text_differs_from_raw": False,
                        "alignment_sha256": "a" * 64,
                    },
                },
                {
                    "fragment_id": "FR-2",
                    "source_id": "SRC-1",
                    "verbatim_excerpt": "很久以后才讲第二项证据。",
                    "media_time": 100,
                    "media_end_time": 120,
                    "excerpt_media_time": 108,
                    "excerpt_media_end_time": 114,
                    "excerpt_timing": {
                        "status": "exact",
                        "method": "normalized_exact",
                        "match_ratio": 1,
                        "reviewed_text_differs_from_raw": False,
                        "alignment_sha256": "b" * 64,
                    },
                },
            ],
            "source_documents": [
                {
                    "source_id": "SRC-1",
                    "source_type": "sermon_transcript",
                    "title": "讲道",
                    "transcript_id": "讲道",
                }
            ],
        },
        "argument_routes": [
            {
                "revision": {
                    "argument_route_revision_id": "ARR-1",
                    "route_label": "分散在讲道两处的论证",
                    "ordered_inference_nodes": [
                        {"route_step_key": "P1", "role": "premise"},
                        {"route_step_key": "C1", "role": "conclusion"},
                    ],
                },
                "attestations": [
                    {
                        "source_id": "SRC-1",
                        "claim_ids": ["CL-1"],
                        "step_bindings": [
                            {
                                "route_step_key": "P1",
                                "evidence_step_ids": ["ES-1"],
                                "source_fragment_ids": ["FR-1"],
                            },
                            {
                                "route_step_key": "C1",
                                "evidence_step_ids": ["ES-2"],
                                "source_fragment_ids": ["FR-2"],
                            },
                        ],
                    }
                ],
            }
        ],
    }

    sources = wang_article_reviews._route_sources(
        {"claim_ids": ["CL-1"]}, packet, ["ARR-1"], {}
    )

    assert sources[0]["media"] is None
    steps = sources[0]["route_steps"]
    assert [step["media_clips"][0]["start_seconds"] for step in steps] == [10, 106]
    assert [step["media_clips"][0]["end_seconds"] for step in steps] == [18, 116]


def test_route_projection_keeps_claim_evidence_for_an_unrepresented_premise(
    tmp_path: Path, monkeypatch
) -> None:
    manuscript, _, packet = _fixture(tmp_path, monkeypatch)
    manuscript.write_text(
        '# 标题\n\n## 教皇推论\n\n'
        '<!-- provenance: {"attribution":"professor","claim_ids":["CL-1","CL-POPE"]} -->\n'
        '彼得受到责备，因此不能推出彼得是第一任教皇。',
        encoding="utf-8",
    )
    payload = json.loads(packet.read_text(encoding="utf-8"))
    result = payload["result"]
    result["editorial_decisions"] = {
        "sections": [{"heading": "教皇推论", "argument_route_revision_ids": ["ARR-POPE"]}]
    }
    result["knowledge"]["claims"].append(
        {"claim_id": "CL-POPE", "evidence_step_ids": ["ES-POPE"]}
    )
    result["knowledge"]["evidence_steps"].append(
        {"evidence_step_id": "ES-POPE", "source_fragment_ids": ["FR-POPE"]}
    )
    result["knowledge"]["source_fragments"].append(
        {
            "fragment_id": "FR-POPE",
            "source_id": "SRC-1",
            "verbatim_excerpt": "彼得不是第一任教皇。",
        }
    )
    result["argument_routes"] = [
        {
            "revision": {
                "argument_route_revision_id": "ARR-POPE",
                "route_label": "首任教皇推论",
                "ordered_inference_nodes": [
                    {"route_step_key": "C1", "role": "conclusion"}
                ],
            },
            "attestations": [
                {
                    "source_id": "SRC-1",
                    "claim_ids": ["CL-POPE"],
                    "step_bindings": [
                        {
                            "route_step_key": "C1",
                            "evidence_step_ids": ["ES-POPE"],
                            "source_fragment_ids": ["FR-POPE"],
                        }
                    ],
                }
            ],
        }
    ]
    _write_json(packet, payload)
    manifest_path = packet.parents[3] / "topic-essay-reviews" / "church-foundation-v1.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["manuscript_sha256"] = hashlib.sha256(manuscript.read_bytes()).hexdigest()
    manifest["authoring_packet_file_sha256"] = hashlib.sha256(packet.read_bytes()).hexdigest()
    _write_json(manifest_path, manifest)

    review = wang_article_reviews.article_review("church-foundation-v1")

    sources = review["source_annotations"][0]["sources"]
    assert sources[0]["mapping_kind"] == "argument_route_attestation"
    assert all(source["mapping_kind"] == "claim_evidence" for source in sources[1:])
    assert any("CL-1" in source["claim_ids"] for source in sources[1:])
    assert review["source_projection_audit"]["passed"] is True


def test_route_cannot_hide_a_direct_quote_without_an_exact_original(
    tmp_path: Path, monkeypatch
) -> None:
    manuscript, _, packet = _fixture(tmp_path, monkeypatch)
    manuscript.write_text(
        '# 标题\n\n## 引文\n\n'
        '<!-- provenance: {"attribution":"professor","claim_ids":["CL-1"]} -->\n'
        '> 「原稿没有这句话。」',
        encoding="utf-8",
    )
    payload = json.loads(packet.read_text(encoding="utf-8"))
    result = payload["result"]
    result["editorial_decisions"] = {
        "sections": [{"heading": "引文", "argument_route_revision_ids": ["ARR-1"]}]
    }
    result["argument_routes"] = [
        {
            "revision": {
                "argument_route_revision_id": "ARR-1",
                "route_label": "测试路线",
                "ordered_inference_nodes": [
                    {"route_step_key": "P1", "role": "premise"}
                ],
            },
            "attestations": [
                {
                    "source_id": "SRC-1",
                    "claim_ids": ["CL-1"],
                    "step_bindings": [
                        {
                            "route_step_key": "P1",
                            "evidence_step_ids": ["ES-1"],
                            "source_fragment_ids": ["FR-1"],
                        }
                    ],
                }
            ],
        }
    ]
    _write_json(packet, payload)
    manifest_path = packet.parents[3] / "topic-essay-reviews" / "church-foundation-v1.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["manuscript_sha256"] = hashlib.sha256(manuscript.read_bytes()).hexdigest()
    manifest["authoring_packet_file_sha256"] = hashlib.sha256(packet.read_bytes()).hexdigest()
    _write_json(manifest_path, manifest)

    review = wang_article_reviews.article_review("church-foundation-v1")

    assert review["source_projection_audit"]["passed"] is False
    assert review["source_projection_audit"]["findings"] == [
        {
            "code": "direct_quote_without_exact_source",
            "paragraph_id": "p1",
            "message": "本段逐字引文没有命中原稿中的精确文本。",
        }
    ]
