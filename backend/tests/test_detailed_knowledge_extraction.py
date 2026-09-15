from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from backend.pipeline import detailed_knowledge_extraction_runner as extraction_runner
from backend.pipeline.corpus_ai_review_runner import _normalize_claim_layer
from backend.pipeline.detailed_knowledge_extraction import (
    DetailedExtractionValidationError,
    detailed_response_schema,
    extraction_identity,
    validate_response,
)
from backend.pipeline.detailed_knowledge_extraction_runner import (
    PROMPT_PATH,
    _validation_feedback,
    compile_package,
)
from backend.pipeline.extraction_sections import Section, namespace_response
from backend.pipeline.knowledge_package_merge import validate_merged_package
from backend.pipeline.source_projection import LOCATOR_SPACE, project_script
from backend.pipeline.knowledge_consensus_applier import (
    ConsensusApplicationError,
    _validate_overrides_artifact,
    apply_consensus_overrides,
    apply_final_ai_review_outcomes,
    reviewed_candidate_artifact_sha256,
    validate_reviewed_candidate_artifact,
)
from backend.pipeline import knowledge_consensus_applier as consensus_runner
from backend.pipeline.corpus_ai_adjudication_runner import (
    _overrides_artifact_sha256,
)


def _transcript() -> dict:
    return {
        "metadata": {"title": "测试讲道", "status": "published"},
        "script": [
            {"index": 10, "start_time": 1.0, "end_time": 8.0, "text": "有人说人子只强调人性。我说不对。"},
            {"index": 11, "start_time": 8.0, "end_time": 16.0, "text": "但以理书所说的那一位人子领受永远的权柄。"},
            {"index": 12, "start_time": 16.0, "end_time": 20.0, "text": "听众：所以这表明神性吗？"},
        ],
    }


def _response() -> dict:
    return {
        "questions": [
            {
                "question_id": "Q001", "text": "这表明神性吗？", "questioner": "audience",
                "question_type": "clarification", "answer_state": "answered", "answer_claim_ids": ["CL001"],
                "anchors": [{"segment_index": "S0003", "start_time": None, "end_time": None, "verbatim_excerpt": "所以这表明神性吗？"}],
            }
        ],
        "positions": [
            {
                "position_id": "POS001", "title": "人子只强调人性", "attribution": "external_view",
                "anchors": [{"segment_index": "S0001", "start_time": None, "end_time": None, "verbatim_excerpt": "有人说人子只强调人性"}],
            }
        ],
        "observations": [
            {
                "observation_id": "OBS001", "statement": "人子领受永远权柄", "observation_type": "scripture_text", "argument_role": "background",
                "scripture_refs": ["但以理书7:13-14"],
                "anchors": [{"segment_index": "S0002", "start_time": None, "end_time": None, "verbatim_excerpt": "那一位人子领受永远的权柄"}],
            }
        ],
        "evidence_steps": [
            {
                "evidence_step_id": "E001", "statement": "教授否定只强调人性的读法", "step_type": "reasoning",
                "speaker": "professor", "stance": "asserted", "discourse_role": "refutation",
                "support_eligibility": "eligible_candidate", "scripture_refs": [], "produced_claim_ids": ["CL001"],
                "anchors": [{"segment_index": "S0001", "start_time": None, "end_time": None, "verbatim_excerpt": "我说不对"}],
            },
            {
                "evidence_step_id": "E002", "statement": "听众追问神性", "step_type": "dialogue_context",
                "speaker": "audience", "stance": "questioned", "discourse_role": "audience_question",
                "support_eligibility": "context_only", "scripture_refs": [], "produced_claim_ids": [],
                "anchors": [{"segment_index": "S0003", "start_time": None, "end_time": None, "verbatim_excerpt": "所以这表明神性吗？"}],
            },
        ],
        "claims": [
            {
                "claim_id": "CL001", "statement": "那一位人子具有神性身份", "claim_kind": "reasoning_conclusion",
                "attribution": "professor", "scripture_refs": ["但以理书7:13-14"], "topic_terms": ["人子", "神性"],
                "evidence_step_ids": ["E001"], "opposed_position_ids": ["POS001"], "review_status": "candidate",
            }
        ],
        "evidence_relations": [
            {"relation_id": "ER001", "from_id": "E001", "to_id": "E002", "relation_type": "contextualizes", "reason": "听众追问承接教授反驳"}
        ],
        "claim_relations": [],
    }


def test_notes_source_forwards_batch_visual_attestation(
    tmp_path: Path, monkeypatch
) -> None:
    svg_path = tmp_path / "diagram.svg"
    svg = "<svg><text>结构</text></svg>"
    svg_path.write_text(svg, encoding="utf-8")
    markdown_path = tmp_path / "final.md"
    markdown_url = "/web/data/full_article/images/diagram.svg"
    markdown_path.write_text(
        f"## 标题\n\n![结构]({markdown_url})\n", encoding="utf-8"
    )
    descriptor = {
        "source_id": "notes_manuscript:visual",
        "source_type": "notes_manuscript",
        "source_path": str(markdown_path),
        "visual_source_assets": [{
            "markdown_url": markdown_url,
            "source_path": str(svg_path),
            "source_sha256": hashlib.sha256(svg.encode("utf-8")).hexdigest(),
        }],
    }
    captured: dict = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return "created", tmp_path / "package.json"

    monkeypatch.setattr(extraction_runner, "_run", fake_run)
    expected = {"S0001/V01": hashlib.sha256(svg.encode("utf-8")).hexdigest()}

    extraction_runner.run_source(
        descriptor,
        output_dir=tmp_path,
        client=object(),
        prompt="prompt",
        reasoning_effort="medium",
        force=False,
        visual_source_attestations=expected,
    )

    assert captured["visual_source_attestations"] == expected
    assert project_script(captured["source"]["script"]).visual_blocks[0].locator == "S0001/V01"


def _bound_extraction_identity(
    transcript: dict, **overrides: object
) -> dict:
    projection = project_script(transcript["script"])
    values = {
        "source_sha256": projection.body_sha256,
        "source_text_sha256": projection.spoken_text_sha256,
        "prompt": "prompt",
        "model_id": "gpt-5.6-sol",
        "reasoning_effort": "medium",
        "max_output_tokens": 32000,
    }
    values.update(overrides)
    return extraction_identity(**values)


def test_rejects_non_verbatim_anchor() -> None:
    response = _response()
    response["evidence_steps"][0]["anchors"][0]["verbatim_excerpt"] = "教授说不对"
    with pytest.raises(DetailedExtractionValidationError, match="not verbatim"):
        validate_response(response, _transcript())


def test_rejects_duplicate_source_anchor_before_package_compilation() -> None:
    response = _response()
    response["questions"][0]["anchors"].append(
        dict(response["questions"][0]["anchors"][0])
    )

    with pytest.raises(DetailedExtractionValidationError, match="duplicate source anchor"):
        validate_response(response, _transcript())


def test_rejects_excerpt_joined_across_a_removed_visual_block() -> None:
    transcript = _transcript()
    svg = "<svg><text>图</text></svg>"
    transcript["script"][0]["text"] = (
        "有人说人子只强调人性。" + svg + "我说不对。"
    )
    response = _response()
    response["evidence_steps"][0]["anchors"][0]["verbatim_excerpt"] = (
        "人性。\n我说不对"
    )

    with pytest.raises(DetailedExtractionValidationError, match="not verbatim"):
        validate_response(response, transcript)


def test_visual_anchor_compiles_raw_svg_and_literal_fact_provenance(tmp_path: Path) -> None:
    transcript = _transcript()
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80">'
        '<ellipse cx="50" cy="40" rx="30" ry="20" stroke="#82E047"/>'
        '<text x="50" y="45">重疊</text>'
        '</svg>'
    )
    transcript["script"][0]["text"] += "\n" + svg
    projection = project_script(transcript["script"])
    visual = projection.visual_blocks[0]
    response = _response()
    response["evidence_steps"][0]["anchors"].append(
        {
            "segment_index": visual.locator,
            "start_time": None,
            "end_time": None,
            "verbatim_excerpt": "",
            "source_modality": "visual",
            "visual_fact_ids": ["VF001", "VF002", "VF003"],
        }
    )
    validate_response(response, transcript)
    extraction = extraction_identity(
        source_sha256=projection.body_sha256,
        source_text_sha256=projection.spoken_text_sha256,
        prompt="prompt",
        model_id="gpt-5.6-sol",
        reasoning_effort="medium",
        max_output_tokens=32000,
        response_schema=detailed_response_schema(has_visual_source=True),
    )
    extraction["source_body_sha256"] = projection.body_sha256
    extraction["source_visual_sha256"] = projection.visual_content_sha256
    raw = json.dumps(transcript, ensure_ascii=False).encode("utf-8")
    package = compile_package(
        transcript_id="visual-sermon",
        transcript_path=tmp_path / "visual-sermon.json",
        transcript=transcript,
        raw=raw,
        response=response,
        extraction=extraction,
        visual_source_attestations={visual.locator: visual.raw_sha256},
    )

    visual_fragments = [
        row for row in package["source_fragments"]
        if row.get("source_modality") == "visual"
    ]
    assert len(visual_fragments) == 1
    fragment = visual_fragments[0]
    assert fragment["verbatim_excerpt"] == svg
    assert fragment["visual_locator"] == "S0001/V01"
    assert fragment["visual_block_sha256"] == visual.raw_sha256
    assert [row["fact_id"] for row in fragment["visual_facts"]] == [
        "VF001",
        "VF002",
        "VF003",
    ]
    source = package["source_documents"][0]
    assert source["source_visual_sha256"] == projection.visual_content_sha256
    assert source["visual_sources"][0]["locator"] == "S0001/V01"
    assert source["visual_sources"][0]["raw_svg"] == svg
    assert [
        row["fact_id"] for row in source["visual_sources"][0]["literal_facts"]
    ] == ["VF001", "VF002", "VF003"]
    assert source["visual_source_attestations"] == [{
        "locator": "S0001/V01",
        "raw_sha256": visual.raw_sha256,
        "attestation": "professor_displayed_or_drawn_visual_source",
    }]
    occurrence = package["claims"][0]["occurrences"][0]["anchors"]
    visual_occurrence = next(row for row in occurrence if row.get("source_modality") == "visual")
    assert visual_occurrence["proposed_highlight"]["text"] == svg


def test_linked_svg_fragment_points_to_svg_file_not_markdown_body(
    tmp_path: Path,
) -> None:
    svg_path = tmp_path / "diagram.svg"
    svg = '<svg><text x="10">结构图</text></svg>'
    svg_path.write_text(svg, encoding="utf-8")
    markdown_url = "/images/diagram.svg"
    transcript = _transcript()
    row = transcript["script"][0]
    row["text"] += f"\n![结构图]({markdown_url})"
    link_start = row["text"].index("![结构图]")
    row["_visual_source_assets"] = [{
        "char_start": link_start,
        "char_end": len(row["text"]),
        "markdown_url": markdown_url,
        "source_url": markdown_url,
        "source_path": str(svg_path),
        "source_file_sha256": hashlib.sha256(svg.encode("utf-8")).hexdigest(),
        "raw_svg": svg,
    }]
    projection = project_script(transcript["script"])
    visual = projection.visual_blocks[0]
    response = _response()
    response["evidence_steps"][0]["anchors"].append({
        "segment_index": visual.locator,
        "start_time": None,
        "end_time": None,
        "verbatim_excerpt": "",
        "source_modality": "visual",
        "visual_fact_ids": [row["fact_id"] for row in visual.facts],
    })
    extraction = extraction_identity(
        source_sha256=projection.body_sha256,
        source_text_sha256=projection.spoken_text_sha256,
        prompt="prompt",
        model_id="gpt-5.6-sol",
        reasoning_effort="medium",
        max_output_tokens=32000,
        response_schema=detailed_response_schema(has_visual_source=True),
    )
    extraction["source_body_sha256"] = projection.body_sha256
    extraction["source_visual_sha256"] = projection.visual_content_sha256
    source_descriptor = {
        "source_id": "notes_manuscript:visual",
        "source_type": "notes_manuscript",
        "visual_source_assets": [{
            "markdown_url": markdown_url,
            "source_path": str(svg_path),
            "source_sha256": visual.raw_sha256,
        }],
    }

    package = compile_package(
        transcript_id="notes_manuscript:visual",
        transcript_path=tmp_path / "final.md",
        transcript=transcript,
        raw=b"notes body",
        response=response,
        extraction=extraction,
        source_descriptor=source_descriptor,
        visual_source_attestations={visual.locator: visual.raw_sha256},
    )

    fragment = next(
        row for row in package["source_fragments"]
        if row.get("source_modality") == "visual"
    )
    assert fragment["verbatim_excerpt"] == svg
    assert fragment["visual_source_path"] == str(svg_path)
    assert fragment["visual_source_file_sha256"] == visual.raw_sha256
    assert svg not in transcript["script"][0]["text"]
    assert package["source_documents"][0]["source_path"] == str(
        tmp_path / "final.md"
    )
    assert package["source_documents"][0]["visual_sources"][0][
        "source_path"
    ] == str(svg_path)
    validate_merged_package(package)


def test_visual_extraction_must_account_for_every_literal_fact() -> None:
    transcript = _transcript()
    transcript["script"][0]["text"] += (
        '\n<svg><ellipse cx="10" cy="10" rx="4" ry="3"/>'
        '<text x="10">重叠</text></svg>'
    )
    visual = project_script(transcript["script"]).visual_blocks[0]
    response = _response()
    response["evidence_steps"][0]["anchors"].append(
        {
            "segment_index": visual.locator,
            "start_time": None,
            "end_time": None,
            "verbatim_excerpt": "",
            "source_modality": "visual",
            "visual_fact_ids": ["VF003"],
        }
    )

    with pytest.raises(DetailedExtractionValidationError, match="uncited literal facts"):
        validate_response(response, transcript)


def test_rejects_anchor_into_inline_blockquote_even_when_verbatim() -> None:
    transcript = _transcript()
    transcript["script"][0]["text"] = "> 我说不对。\n教授正文仍在这里。"
    response = _response()
    response["positions"] = []
    response["claims"][0]["opposed_position_ids"] = []
    response["evidence_steps"][0]["anchors"][0]["verbatim_excerpt"] = "我说不对"

    with pytest.raises(
        DetailedExtractionValidationError, match="provenance-ambiguous inline markup"
    ):
        validate_response(response, transcript)


def test_reviewed_notes_allow_scripture_blockquote_as_quoted_source() -> None:
    transcript = _transcript()
    transcript["metadata"]["source_type"] = "notes_manuscript"
    transcript["script"][1]["text"] = "> 那一位人子领受永远的权柄。"
    response = _response()
    response["evidence_steps"][0].update(
        {
            "statement": "经文说人子领受永远权柄",
            "step_type": "scripture_evidence",
            "speaker": "quoted_source",
            "stance": "quoted",
            "support_eligibility": "context_only",
            "anchors": [
                {
                    "segment_index": "S0002",
                    "start_time": None,
                    "end_time": None,
                    "verbatim_excerpt": "那一位人子领受永远的权柄",
                }
            ],
        }
    )

    validate_response(response, transcript)


def test_reviewed_notes_blockquote_cannot_be_professor_evidence() -> None:
    transcript = _transcript()
    transcript["metadata"]["source_type"] = "notes_manuscript"
    transcript["script"][0]["text"] = "> 我说不对。\n教授正文仍在这里。"
    response = _response()
    response["positions"] = []
    response["claims"][0]["opposed_position_ids"] = []
    response["evidence_steps"][0]["anchors"][0]["verbatim_excerpt"] = "我说不对"

    with pytest.raises(
        DetailedExtractionValidationError, match="provenance-ambiguous inline markup"
    ):
        validate_response(response, transcript)


def test_reports_all_anchor_errors_in_one_validation_pass() -> None:
    response = _response()
    response["evidence_steps"][0]["anchors"][0]["verbatim_excerpt"] = "错误证据"
    response["observations"][0]["anchors"][0]["verbatim_excerpt"] = "错误观察"
    with pytest.raises(DetailedExtractionValidationError) as exc_info:
        validate_response(response, _transcript())
    message = str(exc_info.value)
    assert "E001" in message
    assert "OBS001" in message
    assert message.count("not verbatim") == 2


def test_rejects_model_supplied_anchor_timing() -> None:
    response = _response()
    response["evidence_steps"][0]["anchors"][0]["start_time"] = 1.0
    with pytest.raises(DetailedExtractionValidationError, match="timing must be null"):
        validate_response(response, _transcript())


def test_reports_anchor_and_relation_errors_together() -> None:
    response = _response()
    response["evidence_steps"][0]["anchors"][0]["verbatim_excerpt"] = "错误证据"
    response["claim_relations"] = [
        {
            "claim_relation_id": "CR001",
            "from_id": "CL001",
            "to_id": "CL404",
            "relation_type": "supports",
            "reason": "测试",
        }
    ]
    with pytest.raises(DetailedExtractionValidationError) as exc_info:
        validate_response(response, _transcript())
    message = str(exc_info.value)
    assert "not verbatim" in message
    assert "unknown claim endpoint" in message


@pytest.mark.parametrize("direction", ["claim_only", "evidence_only"])
def test_rejects_nonreciprocal_claim_evidence_bindings(direction: str) -> None:
    response = _response()
    if direction == "claim_only":
        response["claims"][0]["evidence_step_ids"].append("E002")
    else:
        response["evidence_steps"][1]["produced_claim_ids"].append("CL001")

    with pytest.raises(
        DetailedExtractionValidationError,
        match="claim/evidence bindings must be reciprocal",
    ):
        validate_response(response, _transcript())


def test_rejects_duplicate_claim_evidence_reference() -> None:
    response = _response()
    response["claims"][0]["evidence_step_ids"].append("E001")

    with pytest.raises(DetailedExtractionValidationError, match="duplicate evidence"):
        validate_response(response, _transcript())


def test_sermon_prompt_warns_against_provenance_ambiguous_spoken_anchors() -> None:
    prompt = PROMPT_PATH.read_text(encoding="utf-8")

    assert "`> ...` blockquote" in prompt
    assert "spoken anchor" in prompt
    assert "`Sxxxx/Vnn` visual locator" in prompt


def test_validation_feedback_includes_exact_referenced_segment() -> None:
    feedback = _validation_feedback(
        DetailedExtractionValidationError("Q003: excerpt is not verbatim in S0002"),
        _transcript(),
    )
    assert "那一位人子领受永远的权柄" in feedback
    assert "上一版" in feedback
    assert "连续逐字复制" in feedback


def test_validation_feedback_uses_body_locator_not_physical_subtitle_row() -> None:
    transcript = _transcript()
    transcript["script"].insert(
        0,
        {
            "index": "subtitle-1",
            "type": "subtitle",
            "user_id": "editor@example.test",
            "text": "## 编辑小标题",
        },
    )

    feedback = _validation_feedback(
        DetailedExtractionValidationError("Q003: excerpt is not verbatim in S0002"),
        transcript,
    )

    assert "[S0002]\n但以理书所说的那一位人子领受永远的权柄。" in feedback
    assert "编辑小标题" not in feedback
    assert "有人说人子只强调人性" not in feedback


def test_model_context_and_render_contract_change_generation_not_source_identity() -> None:
    base = dict(
        source_sha256="body-sha",
        prompt="prompt",
        model_id="gpt-5.6-sol",
        reasoning_effort="medium",
        max_output_tokens=32000,
        editorial_structure_sha256="structure-sha",
        model_input_contract_version="projection-v1",
    )
    first = extraction_identity(**base, model_context_sha256="title-a")
    title_changed = extraction_identity(**base, model_context_sha256="title-b")
    renderer_changed = extraction_identity(
        **{**base, "model_input_contract_version": "projection-v2"},
        model_context_sha256="title-a",
    )
    section_metadata_changed = extraction_identity(
        **base,
        model_context_sha256="title-a",
        section_model_input_sha256s=[{"section_index": 1, "sha256": "changed"}],
    )

    assert first["source_sha256"] == title_changed["source_sha256"]
    assert len({
        first["generation_fingerprint_sha256"],
        title_changed["generation_fingerprint_sha256"],
        renderer_changed["generation_fingerprint_sha256"],
        section_metadata_changed["generation_fingerprint_sha256"],
    }) == 4


def test_editorial_structure_change_rebuilds_package_without_new_generation() -> None:
    base = dict(
        source_sha256="anchor-binding-sha",
        source_text_sha256="spoken-text-sha",
        prompt="prompt",
        model_id="gpt-5.6-sol",
        reasoning_effort="medium",
        max_output_tokens=32000,
    )
    before = extraction_identity(**base, editorial_structure_sha256="title-a")
    after = extraction_identity(**base, editorial_structure_sha256="title-b")

    assert before["generation_fingerprint_sha256"] == after[
        "generation_fingerprint_sha256"
    ]
    assert before["fingerprint_sha256"] != after["fingerprint_sha256"]


def test_container_sha_changes_package_but_not_model_generation_identity() -> None:
    base = dict(
        source_sha256="body-sha",
        prompt="prompt",
        model_id="gpt-5.6-sol",
        reasoning_effort="medium",
        max_output_tokens=32000,
    )
    first = extraction_identity(
        **base,
        source_file_sha256="file-a",
        package_compiler_version="compiler-v1",
    )
    physical_edit = extraction_identity(
        **base,
        source_file_sha256="file-b",
        package_compiler_version="compiler-v1",
    )
    compiler_edit = extraction_identity(
        **base,
        source_file_sha256="file-a",
        package_compiler_version="compiler-v2",
    )
    partial = extraction_identity(
        **base,
        source_file_sha256="file-a",
        package_compiler_version="compiler-v1",
        section_scope=[3, 1, 3],
    )

    assert {
        first["generation_fingerprint_sha256"],
        physical_edit["generation_fingerprint_sha256"],
        compiler_edit["generation_fingerprint_sha256"],
        partial["generation_fingerprint_sha256"],
    } == {first["generation_fingerprint_sha256"]}
    assert first["fingerprint_sha256"] != physical_edit["fingerprint_sha256"]
    assert first["source_file_sha256"] != physical_edit["source_file_sha256"]
    assert len({
        first["fingerprint_sha256"],
        compiler_edit["fingerprint_sha256"],
        partial["fingerprint_sha256"],
    }) == 3
    assert partial["section_scope"] == [1, 3]


def test_audience_evidence_cannot_be_eligible() -> None:
    response = _response()
    response["evidence_steps"][1]["support_eligibility"] = "eligible_candidate"
    with pytest.raises(DetailedExtractionValidationError, match="cannot be eligible"):
        validate_response(response, _transcript())


def test_compile_namespaces_ids_and_binds_source_hashes(tmp_path: Path) -> None:
    transcript = _transcript()
    raw = json.dumps(transcript, ensure_ascii=False).encode("utf-8")
    response = _response()
    validate_response(response, transcript)
    extraction = _bound_extraction_identity(transcript)
    package = compile_package(
        transcript_id="011WSR01", transcript_path=tmp_path / "011WSR01.json",
        transcript=transcript, raw=raw, response=response, extraction=extraction,
    )
    claim = package["claims"][0]
    assert claim["claim_id"].startswith("DK-")
    assert claim["claim_id"].endswith("-CL001")
    assert claim["evidence_step_ids"][0].endswith("-E001")
    assert claim["opposed_position_ids"][0].endswith("-POS001")
    assert package["questions"][0]["answer_claim_ids"] == [claim["claim_id"]]
    body_sha = project_script(transcript["script"]).body_sha256
    assert package["source_fragments"][0]["source_sha256"] == body_sha
    assert package["source_documents"][0]["source_body_sha256"] == body_sha
    assert package["source_documents"][0]["source_text_sha256"] == project_script(
        transcript["script"]
    ).spoken_text_sha256
    assert package["source_documents"][0]["anchor_binding_sha256"] == body_sha
    assert package["source_documents"][0]["source_file_sha256"] == hashlib.sha256(raw).hexdigest()
    assert package["source_documents"][0]["locator_space"] == LOCATOR_SPACE
    assert package["extraction"]["locator_space"] == LOCATOR_SPACE
    assert package["extraction"]["record_namespace"] == (
        package["source_documents"][0]["extraction_record_namespace"]
    )
    assert package["source_fragments"][0]["anchor_state"] == "source_version_bound"
    assert package["claims"][0]["extraction_fingerprints"] == [
        extraction["generation_fingerprint_sha256"]
    ]
    assert package["claims"][0]["occurrences"][0]["anchors"][0]["media_time"] == 1.0
    assert "extraction_section_index" not in package["source_fragments"][0]


def test_compile_resolves_locators_against_spoken_rows_not_mixed_json_rows(
    tmp_path: Path,
) -> None:
    transcript = _transcript()
    transcript["script"].insert(
        0, {"index": "subtitle-1", "type": "subtitle", "text": "## 编辑标题"}
    )
    transcript["script"].insert(
        2, {"index": "comment-1", "type": "comment", "text": "编辑备注"}
    )
    raw = json.dumps(transcript, ensure_ascii=False).encode("utf-8")
    package = compile_package(
        transcript_id="011WSR01",
        transcript_path=tmp_path / "011WSR01.json",
        transcript=transcript,
        raw=raw,
        response=_response(),
        extraction=_bound_extraction_identity(transcript),
    )

    times = {
        row["paragraph_key"]: row["media_time"]
        for row in package["source_fragments"]
    }
    assert times == {"S0001": 1.0, "S0002": 8.0, "S0003": 16.0}
    assert package["claims"][0]["occurrences"][0]["anchors"][0]["media_time"] == 1.0


@pytest.mark.parametrize("locator", ["S0000", "S0004", "S1", "not-a-locator"])
def test_compile_rejects_malformed_or_out_of_range_spoken_locator(
    tmp_path: Path, locator: str,
) -> None:
    transcript = _transcript()
    raw = json.dumps(transcript, ensure_ascii=False).encode("utf-8")
    response = _response()
    response["questions"][0]["anchors"][0]["segment_index"] = locator

    with pytest.raises(DetailedExtractionValidationError, match="locator"):
        compile_package(
            transcript_id="011WSR01",
            transcript_path=tmp_path / "011WSR01.json",
            transcript=transcript,
            raw=raw,
            response=response,
            extraction=_bound_extraction_identity(transcript),
        )


@pytest.mark.parametrize("mutation", ["missing-text-identity", "wrong-body-identity"])
def test_compile_refuses_unproved_source_binding(
    tmp_path: Path, mutation: str,
) -> None:
    transcript = _transcript()
    raw = json.dumps(transcript, ensure_ascii=False).encode("utf-8")
    extraction = _bound_extraction_identity(transcript)
    if mutation == "missing-text-identity":
        extraction.pop("source_text_sha256")
    else:
        extraction["source_sha256"] = "0" * 64

    with pytest.raises(DetailedExtractionValidationError, match="binding|text identity"):
        compile_package(
            transcript_id="011WSR01",
            transcript_path=tmp_path / "011WSR01.json",
            transcript=transcript,
            raw=raw,
            response=_response(),
            extraction=extraction,
        )


def test_split_package_fragments_record_their_extraction_section(tmp_path: Path) -> None:
    transcript = _transcript()
    raw = json.dumps(transcript, ensure_ascii=False).encode("utf-8")
    response = namespace_response(
        _response(),
        Section(index=2, start=0, end=2, title="内部 transport 分片"),
    )
    extraction = _bound_extraction_identity(
        transcript,
        section_plan={
            "origin": "source_headings",
            "section_count": 2,
            "boundaries": [0, 0],
            "titles_sha256": "titles",
            "section_policy": {
                "level": 2,
                "max_section_sentences": 125,
                "strategy": "sentence-ranges",
                "split_lineage": [{"section_index": 2}],
            },
        },
    )

    package = compile_package(
        transcript_id="011WSR01",
        transcript_path=tmp_path / "011WSR01.json",
        transcript=transcript,
        raw=raw,
        response=response,
        extraction=extraction,
    )

    validate_merged_package(package)
    assert {row["extraction_section_index"] for row in package["source_fragments"]} == {2}


def test_identical_excerpt_in_two_split_sections_does_not_share_fragment(tmp_path: Path) -> None:
    transcript = _transcript()
    raw = json.dumps(transcript, ensure_ascii=False).encode("utf-8")
    first = namespace_response(
        _response(), Section(index=1, start=0, end=2, title="分片一")
    )
    second = namespace_response(
        _response(), Section(index=2, start=0, end=2, title="分片二")
    )
    response = {key: first[key] + second[key] for key in first}
    extraction = _bound_extraction_identity(
        transcript,
        section_plan={
            "origin": "source_headings",
            "section_count": 2,
            "boundaries": [0, 0],
            "titles_sha256": "titles",
            "section_policy": {
                "level": 2,
                "max_section_sentences": 125,
                "strategy": "sentence-ranges",
                "split_lineage": [{"section_index": 1}, {"section_index": 2}],
            },
        },
    )

    package = compile_package(
        transcript_id="011WSR01",
        transcript_path=tmp_path / "011WSR01.json",
        transcript=transcript,
        raw=raw,
        response=response,
        extraction=extraction,
    )

    validate_merged_package(package)
    duplicates = [
        row
        for row in package["source_fragments"]
        if row["paragraph_key"] == "S0001"
        and row["verbatim_excerpt"] == "有人说人子只强调人性"
    ]
    assert len(duplicates) == 2
    assert {row["extraction_section_index"] for row in duplicates} == {1, 2}


def test_distinct_model_outputs_have_disjoint_record_generations(tmp_path: Path) -> None:
    transcript = _transcript()
    raw = json.dumps(transcript, ensure_ascii=False).encode("utf-8")
    extraction = _bound_extraction_identity(transcript)
    first = compile_package(
        transcript_id="011WSR01",
        transcript_path=tmp_path / "011WSR01.json",
        transcript=transcript,
        raw=raw,
        response=_response(),
        extraction=extraction,
    )
    same = compile_package(
        transcript_id="011WSR01",
        transcript_path=tmp_path / "011WSR01.json",
        transcript=transcript,
        raw=raw,
        response=_response(),
        extraction=extraction,
    )
    changed_response = _response()
    changed_response["claims"][0]["statement"] = "同一序号现在表达另一项主张"
    changed = compile_package(
        transcript_id="011WSR01",
        transcript_path=tmp_path / "011WSR01.json",
        transcript=transcript,
        raw=raw,
        response=changed_response,
        extraction=extraction,
    )

    first_ids = {
        first["claims"][0]["claim_id"],
        first["evidence_steps"][0]["evidence_step_id"],
    }
    same_ids = {
        same["claims"][0]["claim_id"],
        same["evidence_steps"][0]["evidence_step_id"],
    }
    changed_ids = {
        changed["claims"][0]["claim_id"],
        changed["evidence_steps"][0]["evidence_step_id"],
    }
    assert first_ids == same_ids
    assert first_ids.isdisjoint(changed_ids)


def test_editorial_container_only_change_preserves_semantic_record_ids(
    tmp_path: Path,
) -> None:
    transcript = _transcript()
    raw_before = json.dumps(transcript, ensure_ascii=False).encode("utf-8")
    raw_after = json.dumps(transcript, ensure_ascii=False, indent=2).encode("utf-8")
    base = dict(
        source_sha256=project_script(transcript["script"]).body_sha256,
        source_text_sha256=project_script(transcript["script"]).spoken_text_sha256,
        prompt="prompt",
        model_id="gpt-5.6-sol",
        reasoning_effort="medium",
        max_output_tokens=32000,
    )
    before_identity = extraction_identity(
        **base, source_file_sha256=hashlib.sha256(raw_before).hexdigest()
    )
    after_identity = extraction_identity(
        **base, source_file_sha256=hashlib.sha256(raw_after).hexdigest()
    )

    before = compile_package(
        transcript_id="011WSR01",
        transcript_path=tmp_path / "011WSR01.json",
        transcript=transcript,
        raw=raw_before,
        response=_response(),
        extraction=before_identity,
    )
    after = compile_package(
        transcript_id="011WSR01",
        transcript_path=tmp_path / "011WSR01.json",
        transcript=transcript,
        raw=raw_after,
        response=_response(),
        extraction=after_identity,
    )

    assert before_identity["fingerprint_sha256"] != after_identity[
        "fingerprint_sha256"
    ]
    assert before_identity["generation_fingerprint_sha256"] == after_identity[
        "generation_fingerprint_sha256"
    ]
    assert before["extraction"]["record_namespace"] == after["extraction"][
        "record_namespace"
    ]
    assert before["claims"][0]["claim_id"] == after["claims"][0]["claim_id"]
    assert before["source_documents"][0]["source_file_sha256"] != after[
        "source_documents"
    ][0]["source_file_sha256"]


def test_source_type_is_part_of_the_exact_generation_namespace(tmp_path: Path) -> None:
    transcript = _transcript()
    raw = json.dumps(transcript, ensure_ascii=False).encode("utf-8")
    extraction = _bound_extraction_identity(transcript)
    sermon = compile_package(
        transcript_id="same-key",
        transcript_path=tmp_path / "same-key.json",
        transcript=transcript,
        raw=raw,
        response=_response(),
        extraction=extraction,
    )
    notes = compile_package(
        transcript_id="same-key",
        transcript_path=tmp_path / "same-key.md",
        transcript=transcript,
        raw=raw,
        response=_response(),
        extraction=extraction,
        source_descriptor={
            "source_id": "same-key",
            "source_type": "notes_manuscript",
            "transcript_id": "same-key",
        },
    )

    assert sermon["extraction"]["record_namespace"] != (
        notes["extraction"]["record_namespace"]
    )


def test_model_ids_must_be_unique_across_record_and_relation_types() -> None:
    response = _response()
    response["claim_relations"] = [{
        "claim_relation_id": response["evidence_relations"][0]["relation_id"],
        "from_id": "CL001",
        "to_id": "CL001",
        "relation_type": "supports",
        "reason": "collision",
    }]

    with pytest.raises(DetailedExtractionValidationError, match="globally unique"):
        validate_response(response, _transcript())


@pytest.mark.parametrize(
    ("collection", "id_field", "from_id", "to_id"),
    [
        ("evidence_relations", "relation_id", "E001", "E001"),
        ("claim_relations", "claim_relation_id", "CL001", "CL001"),
    ],
)
def test_model_relations_cannot_point_to_themselves(
    collection: str, id_field: str, from_id: str, to_id: str
) -> None:
    response = _response()
    response[collection] = [{
        id_field: "SELF001",
        "from_id": from_id,
        "to_id": to_id,
        "relation_type": "supports",
        "reason": "invalid self edge",
    }]

    with pytest.raises(DetailedExtractionValidationError, match="cannot point to itself"):
        validate_response(response, _transcript())


@pytest.mark.parametrize(
    ("collection", "id_field"),
    [
        ("evidence_relations", "relation_id"),
        ("claim_relations", "claim_relation_id"),
    ],
)
def test_model_cannot_repeat_one_semantic_relation_under_two_ids(
    collection: str, id_field: str
) -> None:
    response = _response()
    original = (
        dict(response[collection][0])
        if response[collection]
        else {
            "claim_relation_id": "CR001",
            "from_id": "CL001",
            "to_id": "CL001",
            "relation_type": "supports",
            "reason": "first copy",
        }
    )
    response[collection] = [original]
    duplicate = {**original, id_field: "DUPLICATE002"}
    response[collection].append(duplicate)

    with pytest.raises(DetailedExtractionValidationError, match="duplicate .* relation"):
        validate_response(response, _transcript())


def test_compiled_package_can_feed_existing_claude_reviewer(tmp_path: Path) -> None:
    transcript = _transcript()
    raw = json.dumps(transcript, ensure_ascii=False).encode("utf-8")
    package = compile_package(
        transcript_id="011WSR01", transcript_path=tmp_path / "011WSR01.json",
        transcript=transcript, raw=raw, response=_response(),
        extraction=_bound_extraction_identity(transcript),
    )
    normalized = _normalize_claim_layer(package)
    assert len(normalized["candidate_claims"]) == 1
    assert normalized["candidate_claims"][0]["anchors"][0]["verbatim_excerpt"] == "我说不对"


def test_consensus_applier_removes_anchor_and_relation_without_approving(tmp_path: Path) -> None:
    transcript = _transcript()
    raw = json.dumps(transcript, ensure_ascii=False).encode("utf-8")
    response = _response()
    response["claim_relations"] = [
        {
            "claim_relation_id": "CR001", "from_id": "CL001", "to_id": "CL001",
            "relation_type": "contextualizes", "reason": "test",
        }
    ]
    package = compile_package(
        transcript_id="011WSR01", transcript_path=tmp_path / "011WSR01.json",
        transcript=transcript, raw=raw, response=response,
        extraction=_bound_extraction_identity(transcript),
    )
    package["coverage"] = {
        "available": True,
        "anchored_spans": len(package["source_fragments"]),
        "represented": 0,
        "unprocessed": 999,
    }
    claim = package["claims"][0]
    relation_id = package["claim_relations"][0]["claim_relation_id"]
    anchor = claim["occurrences"][0]["anchors"][0]
    overrides = {
        "adjudication_fingerprint": {"fingerprint_sha256": "fp"},
        "claims": {
            claim["claim_id"]: {
                "status": "ai_consensus_applied", "approval_status": "not_human_approved",
                "excluded_anchors": [{
                    "transcript_id": "011WSR01", "paragraph_key": anchor["paragraph_key"],
                    "evidence_id": anchor["evidence_id"],
                    "verbatim_excerpt": anchor["proposed_highlight"]["text"],
                }],
                "excluded_claim_relation_ids": [relation_id],
                "anchor_additions": [{
                    "transcript_id": "011WSR01", "source_index": "11",
                    "verbatim_excerpt": "那一位人子领受永远的权柄", "evidence_type": "scripture_evidence",
                }],
                "structural_notes": [], "adjudication_fingerprint": "fp",
            }
        },
    }
    result = apply_consensus_overrides(package, overrides, {"011WSR01": transcript})
    updated = result["claims"][0]
    assert relation_id not in {row["claim_relation_id"] for row in result["claim_relations"]}
    assert any(value.startswith("AI-ADJ-") for value in updated["evidence_step_ids"])
    original_evidence = next(
        row for row in result["evidence_steps"] if row["evidence_step_id"].endswith("E001")
    )
    added_evidence = next(
        row for row in result["evidence_steps"] if row["evidence_step_id"].startswith("AI-ADJ-")
    )
    assert original_evidence["produced_claim_ids"] == []
    assert added_evidence["produced_claim_ids"] == [updated["claim_id"]]
    assert result["consensus_application"]["approval_status"] == "not_human_approved"
    assert result["consensus_application"]["artifact_sha256"] == (
        reviewed_candidate_artifact_sha256(result)
    )
    validate_reviewed_candidate_artifact(result, require_review_completion=False)
    assert result["coverage"]["anchored_spans"] == len(result["source_fragments"])
    assert result["coverage"]["unprocessed"] != 999


def test_consensus_applier_accepts_combined_string_fingerprint(tmp_path: Path) -> None:
    transcript = _transcript()
    raw = json.dumps(transcript, ensure_ascii=False).encode("utf-8")
    package = compile_package(
        transcript_id="011WSR01", transcript_path=tmp_path / "011WSR01.json",
        transcript=transcript, raw=raw, response=_response(),
        extraction=_bound_extraction_identity(transcript),
    )
    result = apply_consensus_overrides(
        package,
        {"adjudication_fingerprint": "combined-fp", "claims": {}},
        {"011WSR01": transcript},
    )
    assert result["consensus_application"]["adjudication_fingerprint"] == "combined-fp"


def test_consensus_candidate_self_seal_rejects_graph_valid_tampering(
    tmp_path: Path,
) -> None:
    transcript = _transcript()
    raw = json.dumps(transcript, ensure_ascii=False).encode("utf-8")
    package = compile_package(
        transcript_id="011WSR01", transcript_path=tmp_path / "011WSR01.json",
        transcript=transcript, raw=raw, response=_response(),
        extraction=_bound_extraction_identity(transcript),
    )
    result = apply_consensus_overrides(
        package,
        {"adjudication_fingerprint": "combined-fp", "claims": {}},
        {"011WSR01": transcript},
    )
    result["claims"][0]["title"] = "结构仍合法，但内容已经被改写"

    validate_merged_package(result)
    with pytest.raises(ConsensusApplicationError, match="modified"):
        validate_reviewed_candidate_artifact(result)


def _final_review_fixture() -> tuple[dict, dict, dict]:
    claim_ids = [
        "CL-PASS",
        "CL-SPOT",
        "CL-AUTO",
        "CL-WITHDRAWN",
        "CL-CONFIRM",
        "CL-DISAGREE",
        "CL-MERGED",
    ]
    package = {
        "complete": True,
        "source_documents": [{"source_id": "SRC-FIXTURE"}],
        "extraction": {"fingerprint_sha256": "e" * 64},
        "claims": [
            {
                "claim_id": claim_id,
                "review_status": (
                    "superseded" if claim_id == "CL-MERGED" else "candidate"
                ),
                **(
                    {"superseded_by": "CL-PASS"}
                    if claim_id == "CL-MERGED"
                    else {}
                ),
            }
            for claim_id in claim_ids
        ],
        "consensus_application": {
            "schema_version": "wang_ai_consensus_application_v2",
            "scope_kind": "source_scoped",
            "adjudication_fingerprint": "adj-fp",
            "applied_claim_ids": ["CL-AUTO", "CL-MERGED"],
            "merged_claim_ids": {"CL-MERGED": "CL-PASS"},
            "approval_status": "not_human_approved",
        },
    }
    decisions = {
        "CL-PASS": "pass",
        "CL-SPOT": "pass",
        "CL-AUTO": "changes_suggested",
        "CL-WITHDRAWN": "changes_suggested",
        "CL-CONFIRM": "human_review_required",
        "CL-DISAGREE": "changes_suggested",
        "CL-MERGED": "changes_suggested",
    }
    review = {
        "reviewer": {"fingerprint_sha256": "review-fp"},
        "review_strategy": {
            "reviewer_batches": [
                {"reviewer": {"review_model_id": "claude-sonnet-5"}}
            ]
        },
        "claim_reviews": [
            {
                "claim_id": claim_id,
                "decision": decisions[claim_id],
                "spot_check_selected": claim_id == "CL-SPOT",
            }
            for claim_id in claim_ids
        ],
    }
    statuses = {
        "CL-AUTO": "auto_applied",
        "CL-WITHDRAWN": "withdrawn",
        "CL-CONFIRM": "human_confirmation_required",
        "CL-DISAGREE": "human_disagreement_required",
        "CL-MERGED": "auto_applied",
    }
    adjudication = {
        "adjudicator": {
            "fingerprint_sha256": "adj-fp",
            "review_fingerprint": "review-fp",
            "generated_at": "2026-09-12T17:08:53+00:00",
            "openai_model": "gpt-5.6-sol",
        },
        "results": [
            {"claim_id": claim_id, "status": status}
            for claim_id, status in statuses.items()
        ],
    }
    return package, review, adjudication


def test_final_ai_review_outcomes_compile_the_complete_state_machine() -> None:
    package, review, adjudication = _final_review_fixture()

    apply_final_ai_review_outcomes(
        package,
        review=review,
        review_artifact_sha256="a" * 64,
        adjudication=adjudication,
        adjudication_artifact_sha256="b" * 64,
        overrides_artifact_sha256="c" * 64,
    )

    statuses = {
        row["claim_id"]: row["review_status"] for row in package["claims"]
    }
    assert statuses == {
        "CL-PASS": "ai_consensus_reviewed",
        "CL-SPOT": "human_review_required",
        "CL-AUTO": "ai_consensus_reviewed",
        "CL-WITHDRAWN": "ai_consensus_reviewed",
        "CL-CONFIRM": "human_review_required",
        "CL-DISAGREE": "human_review_required",
        "CL-MERGED": "superseded",
    }
    assert package["consensus_application"]["final_review_status_counts"] == {
        "ai_consensus_reviewed": 3,
        "human_review_required": 3,
        "superseded": 1,
    }
    assert len(package["consensus_application"]["review_resolutions"]) == 7
    validate_reviewed_candidate_artifact(package)


def test_final_ai_review_outcomes_reject_mismatched_review_chain() -> None:
    package, review, adjudication = _final_review_fixture()
    adjudication["adjudicator"]["review_fingerprint"] = "another-review"

    with pytest.raises(ConsensusApplicationError, match="adjudication chain"):
        apply_final_ai_review_outcomes(
            package,
            review=review,
            review_artifact_sha256="a" * 64,
            adjudication=adjudication,
            adjudication_artifact_sha256="b" * 64,
            overrides_artifact_sha256="c" * 64,
        )


def test_final_ai_review_outcomes_reject_duplicate_or_missing_rows() -> None:
    package, review, adjudication = _final_review_fixture()
    review["claim_reviews"].append(dict(review["claim_reviews"][0]))
    with pytest.raises(ConsensusApplicationError, match="exactly once"):
        apply_final_ai_review_outcomes(
            package,
            review=review,
            review_artifact_sha256="a" * 64,
            adjudication=adjudication,
            adjudication_artifact_sha256="b" * 64,
            overrides_artifact_sha256="c" * 64,
        )


def test_final_ai_review_outcomes_reject_applied_override_mismatch() -> None:
    package, review, adjudication = _final_review_fixture()
    package["consensus_application"]["applied_claim_ids"] = ["CL-AUTO"]
    with pytest.raises(ConsensusApplicationError, match="exactly match"):
        apply_final_ai_review_outcomes(
            package,
            review=review,
            review_artifact_sha256="a" * 64,
            adjudication=adjudication,
            adjudication_artifact_sha256="b" * 64,
            overrides_artifact_sha256="c" * 64,
        )


def test_consensus_cannot_reclassify_visual_source_as_spoken_anchor(
    tmp_path: Path,
) -> None:
    transcript = _transcript()
    svg = '<svg><text x="4">教授的图</text></svg>'
    transcript["script"][1]["text"] += svg
    projection = project_script(transcript["script"])
    visual = projection.visual_blocks[0]
    extraction = _bound_extraction_identity(
        transcript,
        response_schema=detailed_response_schema(has_visual_source=True),
    )
    extraction["source_visual_sha256"] = projection.visual_content_sha256
    raw = json.dumps(transcript, ensure_ascii=False).encode("utf-8")
    response = _response()
    response["evidence_steps"][0]["anchors"].append(
        {
            "segment_index": visual.locator,
            "start_time": None,
            "end_time": None,
            "verbatim_excerpt": "",
            "source_modality": "visual",
            "visual_fact_ids": [
                str(fact["fact_id"]) for fact in visual.facts
            ],
        }
    )
    package = compile_package(
        transcript_id="011WSR01",
        transcript_path=tmp_path / "011WSR01.json",
        transcript=transcript,
        raw=raw,
        response=response,
        extraction=extraction,
        visual_source_attestations={visual.locator: visual.raw_sha256},
    )
    claim_id = package["claims"][0]["claim_id"]
    overrides = {
        "adjudication_fingerprint": {"fingerprint_sha256": "fp"},
        "claims": {
            claim_id: {
                "status": "ai_consensus_applied",
                "approval_status": "not_human_approved",
                "excluded_anchors": [],
                "excluded_claim_relation_ids": [],
                "anchor_additions": [{
                    "transcript_id": "011WSR01",
                    "source_index": "11",
                    "verbatim_excerpt": visual.raw_svg,
                    "evidence_type": "reasoning",
                }],
                "structural_notes": [],
                "adjudication_fingerprint": "fp",
            }
        },
    }

    with pytest.raises(ConsensusApplicationError, match="non-spoken inline"):
        apply_consensus_overrides(package, overrides, {"011WSR01": transcript})


def test_consensus_applier_refuses_stale_single_source_coverage_on_merged_package(
    tmp_path: Path,
) -> None:
    transcript = _transcript()
    raw = json.dumps(transcript, ensure_ascii=False).encode("utf-8")
    package = compile_package(
        transcript_id="011WSR01", transcript_path=tmp_path / "011WSR01.json",
        transcript=transcript, raw=raw, response=_response(),
        extraction=_bound_extraction_identity(transcript),
    )
    package["coverage"] = {"available": True, "source_id": package["source_documents"][0]["source_id"]}
    package["source_documents"].append(
        {**package["source_documents"][0], "source_id": "SRC-SECOND", "transcript_id": "SECOND"}
    )

    with pytest.raises(ConsensusApplicationError, match="exactly one source document"):
        apply_consensus_overrides(
            package,
            {"adjudication_fingerprint": "fp", "claims": {}},
            {"011WSR01": transcript, "SECOND": transcript},
        )


def test_consensus_cli_guard_binds_overrides_to_exact_package_and_bytes() -> None:
    artifact = {
        "adjudication_fingerprint": {
            "fingerprint_sha256": "fp",
            "source_package_sha256": "package-a",
        },
        "claims": {},
    }
    artifact["artifact_sha256"] = _overrides_artifact_sha256(artifact)

    _validate_overrides_artifact(artifact, package_sha256="package-a")

    tampered = json.loads(json.dumps(artifact))
    tampered["claims"]["CL-1"] = {"status": "ai_consensus_applied"}
    with pytest.raises(ConsensusApplicationError, match="modified"):
        _validate_overrides_artifact(tampered, package_sha256="package-a")
    with pytest.raises(ConsensusApplicationError, match="different package"):
        _validate_overrides_artifact(artifact, package_sha256="package-b")


def test_consensus_added_evidence_cannot_collide_with_another_collection(
    tmp_path: Path,
) -> None:
    transcript = _transcript()
    raw = json.dumps(transcript, ensure_ascii=False).encode("utf-8")
    package = compile_package(
        transcript_id="011WSR01",
        transcript_path=tmp_path / "011WSR01.json",
        transcript=transcript,
        raw=raw,
        response=_response(),
        extraction=_bound_extraction_identity(transcript),
    )
    claim_id = package["claims"][0]["claim_id"]
    generated_evidence_id = f"AI-ADJ-{claim_id}-01"
    package["questions"][0]["question_id"] = generated_evidence_id
    overrides = {
        "adjudication_fingerprint": {"fingerprint_sha256": "fp"},
        "claims": {
            claim_id: {
                "status": "ai_consensus_applied",
                "approval_status": "not_human_approved",
                "excluded_anchors": [],
                "excluded_claim_relation_ids": [],
                "anchor_additions": [{
                    "transcript_id": "011WSR01",
                    "source_index": "11",
                    "verbatim_excerpt": "那一位人子领受永远的权柄",
                    "evidence_type": "scripture_evidence",
                }],
                "structural_notes": [],
                "adjudication_fingerprint": "fp",
            }
        },
    }

    with pytest.raises(ConsensusApplicationError, match="globally unique"):
        apply_consensus_overrides(package, overrides, {"011WSR01": transcript})


def test_consensus_cli_exact_replay_writes_no_artifact_or_second_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transcript = _transcript()
    raw = json.dumps(transcript, ensure_ascii=False).encode("utf-8")
    transcript_path = tmp_path / "011WSR01.json"
    transcript_path.write_bytes(raw)
    package = compile_package(
        transcript_id="011WSR01",
        transcript_path=transcript_path,
        transcript=transcript,
        raw=raw,
        response=_response(),
        extraction=_bound_extraction_identity(transcript),
    )
    package_path = tmp_path / "package.json"
    review_path = tmp_path / "review.json"
    adjudication_path = tmp_path / "adjudication.json"
    overrides_path = tmp_path / "overrides.json"
    output_path = tmp_path / "consensus.json"
    package_path.write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
    package_sha256 = hashlib.sha256(package_path.read_bytes()).hexdigest()
    overrides = {
        "adjudication_fingerprint": {
            "fingerprint_sha256": "fp",
            "source_package_sha256": package_sha256,
        },
        "claims": {},
    }
    overrides["artifact_sha256"] = _overrides_artifact_sha256(overrides)
    overrides_path.write_text(
        json.dumps(overrides),
        encoding="utf-8",
    )
    claim_ids = [row["claim_id"] for row in package["claims"]]
    review = {
        "reviewer": {"fingerprint_sha256": "review-fp"},
        "review_strategy": {
            "reviewer_batches": [
                {"reviewer": {"review_model_id": "claude-sonnet-5"}}
            ]
        },
        "claim_reviews": [
            {"claim_id": claim_id, "decision": "pass"}
            for claim_id in claim_ids
        ],
    }
    review_bytes = json.dumps(review).encode("utf-8")
    review_path.write_bytes(review_bytes)
    adjudication = {
        "adjudicator": {
            "fingerprint_sha256": "fp",
            "review_fingerprint": "review-fp",
            "source_package_sha256": package_sha256,
            "review_artifact_sha256": hashlib.sha256(review_bytes).hexdigest(),
            "generated_at": "2026-09-12T00:00:00+00:00",
            "openai_model": "gpt-5.6-sol",
        },
        "results": [],
    }
    adjudication_path.write_text(json.dumps(adjudication), encoding="utf-8")
    runs: list[str] = []

    class FakeRecord:
        def __enter__(self):
            runs.append("merge")
            return self

        def __exit__(self, *_args):
            return False

        def inputs(self, *_args):
            return None

        def input_artifacts(self, *_args):
            return None

        def quality(self, *_args):
            return None

        def outputs(self, *_args):
            return None

    monkeypatch.setattr(consensus_runner, "run_record", lambda **_kwargs: FakeRecord())
    monkeypatch.setattr(
        consensus_runner,
        "_validated_review_context",
        lambda *_args, **_kwargs: (
            {},
            {claim_id: {} for claim_id in claim_ids},
            [("011WSR01", transcript)],
            {},
            review,
            review_bytes,
        ),
    )
    monkeypatch.setattr(
        consensus_runner, "_valid_adjudication_artifact", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        consensus_runner, "_valid_overrides_artifact", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "knowledge_consensus_applier",
            "--package", str(package_path),
                "--overrides", str(overrides_path),
                "--output", str(output_path),
                "--review", str(review_path),
                "--adjudication", str(adjudication_path),
                "--transcript-dir", str(tmp_path),
        ],
    )

    assert consensus_runner.main() == 0
    before = output_path.read_bytes()
    before_mtime = output_path.stat().st_mtime_ns
    assert consensus_runner.main() == 0

    assert runs == ["merge"]
    assert output_path.read_bytes() == before
    assert output_path.stat().st_mtime_ns == before_mtime


def _two_claim_package(tmp_path: Path) -> tuple[dict, dict]:
    """A package whose two claims say the same thing from different evidence."""
    transcript = _transcript()
    raw = json.dumps(transcript, ensure_ascii=False).encode("utf-8")
    response = _response()
    response["evidence_steps"].append({
        "evidence_step_id": "E003", "statement": "同一结论的另一处证据", "step_type": "reasoning",
        "speaker": "professor", "stance": "asserted", "discourse_role": "restatement",
        "support_eligibility": "eligible_candidate", "scripture_refs": [], "produced_claim_ids": ["CL002"],
        "anchors": [{
            "segment_index": "S0002", "start_time": None, "end_time": None,
            "verbatim_excerpt": "但以理书所说的那一位人子",
        }],
    })
    response["claims"].append({
        "claim_id": "CL002", "statement": "人子具有神性身份（第二章节重复）",
        "claim_kind": "reasoning_conclusion", "attribution": "professor",
        "scripture_refs": ["但以理书7:13-14"], "topic_terms": ["人子"],
        "evidence_step_ids": ["E003"], "opposed_position_ids": [], "review_status": "candidate",
    })
    response["claim_relations"] = [{
        "claim_relation_id": "CR001", "from_id": "CL002", "to_id": "CL001",
        "relation_type": "supports", "reason": "test",
    }]
    package = compile_package(
        transcript_id="011WSR01", transcript_path=tmp_path / "011WSR01.json",
        transcript=transcript, raw=raw, response=response,
        extraction=_bound_extraction_identity(transcript),
    )
    survivor_id, retired_id = (row["claim_id"] for row in package["claims"])
    overrides = {
        "adjudication_fingerprint": {"fingerprint_sha256": "fp"},
        "claims": {
            retired_id: {
                "status": "ai_consensus_applied", "approval_status": "not_human_approved",
                "excluded_anchors": [], "excluded_claim_relation_ids": [],
                "anchor_additions": [], "structural_notes": [],
                "superseded_by": survivor_id, "adjudication_fingerprint": "fp",
            }
        },
    }
    return package, overrides


def test_merging_a_duplicate_keeps_its_grip_on_the_source(tmp_path: Path) -> None:
    package, overrides = _two_claim_package(tmp_path)

    result = apply_consensus_overrides(package, overrides, {"011WSR01": _transcript()})

    survivor, retired = result["claims"]
    assert retired["superseded_by"] == survivor["claim_id"]
    assert retired["review_status"] == "superseded"
    # The retired claim is still in the file, but its evidence now hangs off
    # the claim that stays -- filtering superseded claims must not cost anchors.
    retired_evidence = set(retired["evidence_step_ids"])
    assert retired_evidence <= set(survivor["evidence_step_ids"])
    assert retired_evidence <= {
        str(anchor["evidence_id"])
        for occurrence in survivor["occurrences"] for anchor in occurrence["anchors"]
    }
    evidence = {
        row["evidence_step_id"]: row for row in result["evidence_steps"]
    }
    assert all(
        survivor["claim_id"] in evidence[evidence_id]["produced_claim_ids"]
        for evidence_id in retired_evidence
    )
    assert result["summary"]["active_claim_count"] == 1
    assert result["summary"]["superseded_claim_count"] == 1
    assert result["consensus_application"]["merged_claim_ids"] == {
        retired["claim_id"]: survivor["claim_id"]
    }


def test_merging_drops_the_relation_between_the_two_merged_claims(tmp_path: Path) -> None:
    package, overrides = _two_claim_package(tmp_path)

    result = apply_consensus_overrides(package, overrides, {"011WSR01": _transcript()})

    # CL002 supports CL001; retargeting would otherwise leave CL001 supporting itself.
    assert result["claim_relations"] == []


def test_merge_into_a_claim_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    package, overrides = _two_claim_package(tmp_path)
    retired_id = next(iter(overrides["claims"]))
    overrides["claims"][retired_id]["superseded_by"] = "CL999"

    with pytest.raises(ConsensusApplicationError, match="merge target does not exist"):
        apply_consensus_overrides(package, overrides, {"011WSR01": _transcript()})


def test_a_merge_may_also_exclude_the_relation_it_dissolves(tmp_path: Path) -> None:
    """One review finds both the duplicate and the wrong edge between the pair.

    The merge's own dedupe removes CR001 as a self-loop, so by the time the
    exclusion list is checked the id is gone from `claim_relations`.  "Already
    removed" has to satisfy "remove this" -- treating it as an unknown relation
    failed the entire application over a request that had been carried out.
    """
    package, overrides = _two_claim_package(tmp_path)
    retired_id = next(iter(overrides["claims"]))
    relation_id = package["claim_relations"][0]["claim_relation_id"]
    overrides["claims"][retired_id]["excluded_claim_relation_ids"] = [relation_id]

    result = apply_consensus_overrides(package, overrides, {"011WSR01": _transcript()})

    assert result["claim_relations"] == []
    assert result["consensus_application"]["dissolved_claim_relation_ids"] == [relation_id]
    assert result["consensus_application"]["removed_claim_relation_ids"] == [relation_id]


def test_an_exclusion_naming_no_relation_at_all_is_still_refused(tmp_path: Path) -> None:
    """Tolerating what the merge dissolved must not tolerate a typo."""
    package, overrides = _two_claim_package(tmp_path)
    retired_id = next(iter(overrides["claims"]))
    overrides["claims"][retired_id]["excluded_claim_relation_ids"] = ["CR-NOPE"]

    with pytest.raises(ConsensusApplicationError, match="unknown relations"):
        apply_consensus_overrides(package, overrides, {"011WSR01": _transcript()})


def test_merging_into_an_already_retired_claim_is_refused(tmp_path: Path) -> None:
    """Round two must not name round one's loser as the survivor.

    Both claims leave the live set at once, and the coverage guard then reports
    lost evidence -- true, but it names the symptom instead of the override.
    """
    package, overrides = _two_claim_package(tmp_path)
    merged = apply_consensus_overrides(package, overrides, {"011WSR01": _transcript()})
    survivor_id, retired_id = (row["claim_id"] for row in merged["claims"])
    second_round = {
        "adjudication_fingerprint": {"fingerprint_sha256": "fp2"},
        "claims": {
            survivor_id: {
                "status": "ai_consensus_applied", "approval_status": "not_human_approved",
                "excluded_anchors": [], "excluded_claim_relation_ids": [],
                "anchor_additions": [], "structural_notes": [],
                "superseded_by": retired_id, "adjudication_fingerprint": "fp2",
            }
        },
    }

    with pytest.raises(ConsensusApplicationError, match="already superseded"):
        apply_consensus_overrides(merged, second_round, {"011WSR01": _transcript()})
