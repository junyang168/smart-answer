from __future__ import annotations

import hashlib
import json
import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from backend.pipeline.codex_subscription_client import (
    API_BILLING_ENV_VARS,
    CodexSubscriptionClient,
    CodexSubscriptionError,
    subscription_environment,
)
from backend.pipeline.detailed_knowledge_extraction import (
    DETAILED_RESPONSE_SCHEMA,
    DetailedExtractionValidationError,
    extraction_identity,
)
from backend.pipeline import detailed_knowledge_extraction_runner as extraction_runner
from backend.pipeline.detailed_knowledge_extraction_runner import (
    SectionSettings,
    _extract_sections,
    _package_artifact_sha256,
    _render_visual_png,
    _section_cache_artifact,
    _section_cache_path,
    _section_generation_fingerprint,
    _section_model_input_sha256,
    _visual_svg_for_render,
    _load_valid_section_cache,
    build_client,
    run_one,
)
from backend.pipeline.extraction_sections import Section, SectionPlan, apply_section_limit
from backend.pipeline.source_projection import visual_source_blocks


def _transcript() -> dict:
    return {
        "metadata": {"title": "固定抽取来源", "status": "published"},
        "script": [
            {
                "index": 10,
                "start_time": 1.0,
                "end_time": 8.0,
                "text": "有人说人子只强调人性。我说不对。",
            },
            {
                "index": 11,
                "start_time": 8.0,
                "end_time": 16.0,
                "text": "但以理书所说的那一位人子领受永远的权柄。",
            },
            {
                "index": 12,
                "start_time": 16.0,
                "end_time": 20.0,
                "text": "听众：所以这表明神性吗？",
            },
        ],
    }


def _titled_transcript() -> dict:
    transcript = _transcript()
    transcript["script"].insert(
        0,
        {
            "index": "subtitle-fixed-source",
            "type": "subtitle",
            "text": "## 固定抽取来源",
        },
    )
    return transcript


def test_explicit_canary_mode_never_opens_the_run_ledger(monkeypatch) -> None:
    def unexpected_run_record(**_kwargs):
        raise AssertionError("no-run-ledger must not connect")

    monkeypatch.setattr(extraction_runner, "run_record", unexpected_run_record)
    with extraction_runner._extraction_run_record(
        "canary", enabled=False
    ) as record:
        assert record.recording is False
        record.model_call_started()
        record.model_call_completed()


def _response() -> dict:
    return {
        "questions": [{
            "question_id": "Q001", "text": "这表明神性吗？", "questioner": "audience",
            "question_type": "clarification", "answer_state": "answered",
            "answer_claim_ids": ["CL001"],
            "anchors": [{"segment_index": "S0003", "start_time": None,
                         "end_time": None, "verbatim_excerpt": "所以这表明神性吗？"}],
        }],
        "positions": [{
            "position_id": "POS001", "title": "人子只强调人性", "attribution": "external_view",
            "anchors": [{"segment_index": "S0001", "start_time": None,
                         "end_time": None, "verbatim_excerpt": "有人说人子只强调人性"}],
        }],
        "observations": [{
            "observation_id": "OBS001", "statement": "人子领受永远权柄",
            "observation_type": "scripture_text", "argument_role": "background",
            "scripture_refs": ["但以理书7:13-14"],
            "anchors": [{"segment_index": "S0002", "start_time": None,
                         "end_time": None, "verbatim_excerpt": "那一位人子领受永远的权柄"}],
        }],
        "evidence_steps": [{
            "evidence_step_id": "E001", "statement": "教授否定只强调人性的读法",
            "step_type": "reasoning", "speaker": "professor", "stance": "asserted",
            "discourse_role": "refutation", "support_eligibility": "eligible_candidate",
            "scripture_refs": [], "produced_claim_ids": ["CL001"],
            "anchors": [{"segment_index": "S0001", "start_time": None,
                         "end_time": None, "verbatim_excerpt": "我说不对"}],
        }],
        "claims": [{
            "claim_id": "CL001", "statement": "那一位人子具有神性身份",
            "claim_kind": "reasoning_conclusion", "attribution": "professor",
            "scripture_refs": ["但以理书7:13-14"], "topic_terms": ["人子", "神性"],
            "evidence_step_ids": ["E001"], "opposed_position_ids": ["POS001"],
            "review_status": "candidate",
        }],
        "evidence_relations": [],
        "claim_relations": [],
        "sentence_audit": [
            {"sentence_id": "S0001#001", "status": "extracted", "covered_by": ["POS001"],
             "reason_code": None, "reason": ""},
            {"sentence_id": "S0001#002", "status": "extracted", "covered_by": ["E001"],
             "reason_code": None, "reason": ""},
            {"sentence_id": "S0002#003", "status": "extracted", "covered_by": ["OBS001"],
             "reason_code": None, "reason": ""},
            {"sentence_id": "S0003#004", "status": "extracted", "covered_by": ["Q001"],
             "reason_code": None, "reason": ""},
        ],
    }


def _completed(args: list[str], *, stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)


def test_section_cache_requires_self_hash_and_full_mechanical_validation(
    tmp_path: Path,
) -> None:
    source = _transcript()
    section = Section(index=1, start=0, end=3, title="固定章节")
    sentences = extraction_runner.section_sentences(source, section)
    response = _response()
    artifact = _section_cache_artifact(
        section,
        response,
        "generation-fingerprint",
        model_input_sha256="model-input",
    )
    path = tmp_path / "section.json"
    path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")

    assert _load_valid_section_cache(
        path,
        section=section,
        fingerprint="generation-fingerprint",
        source=source,
        sentences=sentences,
        model_input_sha256="model-input",
    ) == response

    artifact["response"]["claims"][0]["statement"] = "被篡改但仍是合法 JSON"
    path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    assert _load_valid_section_cache(
        path,
        section=section,
        fingerprint="generation-fingerprint",
        source=source,
        sentences=sentences,
        model_input_sha256="model-input",
    ) is None

    restamped = _section_cache_artifact(
        section,
        _response(),
        "generation-fingerprint",
        model_input_sha256="model-input",
    )
    restamped["response"]["claims"][0]["evidence_step_ids"] = ["E999"]
    restamped["artifact_sha256"] = extraction_runner._section_cache_artifact_sha256(
        restamped
    )
    path.write_text(json.dumps(restamped, ensure_ascii=False), encoding="utf-8")
    assert _load_valid_section_cache(
        path,
        section=section,
        fingerprint="generation-fingerprint",
        source=source,
        sentences=sentences,
        model_input_sha256="model-input",
    ) is None


def test_subscription_environment_removes_api_billing_credentials() -> None:
    source = {name: "secret" for name in API_BILLING_ENV_VARS}
    source.update({"PATH": "/bin", "CODEX_HOME": "/oauth"})
    result = subscription_environment(source)
    assert not API_BILLING_ENV_VARS.intersection(result)
    assert result["PATH"] == "/bin"
    assert result["CODEX_HOME"] == "/oauth"


def test_non_chatgpt_login_fails_closed_before_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return _completed(command, stdout="Logged in using an API key\n")

    monkeypatch.setattr("backend.pipeline.codex_subscription_client.subprocess.run", fake_run)
    client = CodexSubscriptionClient(model="gpt-5.6-sol", executable="codex")
    with pytest.raises(CodexSubscriptionError, match="ChatGPT login"):
        client.generate_json("system", "user", {"type": "object"})
    assert calls == [["codex", "login", "status"]]


def test_transport_failure_has_no_api_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        if command[1:3] == ["login", "status"]:
            return _completed(command, stdout="Logged in using ChatGPT\n")
        return _completed(command, stderr="quota exhausted", returncode=1)

    monkeypatch.setattr("backend.pipeline.codex_subscription_client.subprocess.run", fake_run)
    client = build_client(
        "gpt-5.6-sol", backend="codex-subscription",
        reasoning_effort="medium", max_output_tokens=64000,
    )
    with pytest.raises(CodexSubscriptionError, match="quota exhausted"):
        client.generate_json("system", "user", {"type": "object"})
    assert len(calls) == 2


def test_visual_source_image_is_attached_to_codex_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = tmp_path / "diagram.png"
    image.write_bytes(b"png")
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        if command[1:3] == ["login", "status"]:
            return _completed(command, stdout="Logged in using ChatGPT\n")
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text("{}", encoding="utf-8")
        return _completed(command)

    monkeypatch.setattr(
        "backend.pipeline.codex_subscription_client.subprocess.run", fake_run
    )
    client = CodexSubscriptionClient(model="gpt-5.6-sol", executable="codex")
    assert client.generate_json(
        "system", "user", {"type": "object"}, image_paths=[image]
    ) == {}
    command = calls[-1]
    assert command[command.index("--image") + 1] == str(image.resolve())
    assert command[-1] == "-"


def test_visual_render_adds_cjk_font_fallback_and_opaque_white_background() -> None:
    raw = '<svg width="40" height="40"><text x="5" y="20">神</text></svg>'
    block = visual_source_blocks(raw, segment_index="S0001")[0]

    render_input = _visual_svg_for_render(block)
    assert block.raw_svg == raw
    assert "Arial Unicode MS" in render_input
    assert render_input.endswith("<text x=\"5\" y=\"20\">神</text></svg>")

    image = Image.open(BytesIO(_render_visual_png(block))).convert("RGB")
    assert image.getpixel((39, 39)) == (255, 255, 255)


def test_visual_section_cache_identity_does_not_depend_on_host_png_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _transcript()
    source["script"][0]["text"] += "<svg><text>约的结构</text></svg>"
    section = Section(index=1, start=0, end=1, title="图示")
    sentences = extraction_runner.section_sentences(source, section)

    def unexpected_render(_block):
        raise AssertionError("model-input identity must not render a PNG")

    monkeypatch.setattr(extraction_runner, "_render_visual_png", unexpected_render)
    first = _section_model_input_sha256(source, "header", section, sentences)

    source["script"][0]["text"] = source["script"][0]["text"].replace(
        "约的结构", "新约的结构"
    )
    edited_sentences = extraction_runner.section_sentences(source, section)
    second = _section_model_input_sha256(
        source, "header", section, edited_sentences
    )
    assert first != second


def test_api_client_remains_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    constructed: list[dict] = []

    class FakeAPIClient:
        def __init__(self, **kwargs):
            constructed.append(kwargs)

    monkeypatch.setattr(extraction_runner, "Stage1OpenAIClient", FakeAPIClient)
    client = build_client(
        "gpt-5.6-sol", reasoning_effort="medium", max_output_tokens=64000,
    )
    assert isinstance(client, FakeAPIClient)
    assert constructed[0]["api_key_env"] == "OPENAI_API_KEY"


def test_malformed_inline_svg_fails_before_login_or_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    transcript = _transcript()
    transcript["script"][0]["text"] += "\n<svg><text>图形</text></tspan></svg>"
    transcript_path = tmp_path / "inline-svg.json"
    transcript_path.write_text(
        json.dumps(transcript, ensure_ascii=False), encoding="utf-8"
    )
    calls: list[list[str]] = []

    def unexpected_run(command, **_kwargs):
        calls.append(command)
        raise AssertionError("malformed visual source must fail before subscription login")

    monkeypatch.setattr(
        "backend.pipeline.codex_subscription_client.subprocess.run", unexpected_run
    )
    client = CodexSubscriptionClient(model="gpt-5.6-sol", executable="codex")
    with pytest.raises(DetailedExtractionValidationError, match="unreadable inline source"):
        run_one(
            transcript_path,
            output_dir=tmp_path / "output",
            client=client,
            prompt="extract",
            reasoning_effort="medium",
            force=False,
            sections=SectionSettings(allow_generated=False),
        )
    assert calls == []


def test_payload_dry_run_reports_clean_cli_error_without_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    transcript = _transcript()
    transcript["script"][0]["text"] += "\n<!-- editor payload -->"
    transcript_path = tmp_path / "inline-comment.json"
    transcript_path.write_text(
        json.dumps(transcript, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "detailed_knowledge_extraction_runner",
            "--output-dir", str(tmp_path / "output"),
            "--transcript-dir", str(tmp_path),
            "--ids", transcript_path.stem,
            "--dry-run",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        extraction_runner.main()

    assert exc_info.value.code == 2
    error = capsys.readouterr().err
    assert "unreadable inline source" in error
    assert "Traceback" not in error


def test_current_model_contract_is_v3_after_inline_markup_quarantine() -> None:
    assert extraction_runner.MODEL_INPUT_CONTRACT_VERSION == (
        "detailed-extraction-spoken-text-v3"
    )


def test_subscription_section_passes_schema_validator_and_sentence_ledger_and_then_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    transcript_path = tmp_path / "fixed-source.json"
    transcript_path.write_text(
        json.dumps(_titled_transcript(), ensure_ascii=False), encoding="utf-8"
    )
    output_dir = tmp_path / "output"
    calls: list[list[str]] = []
    child_environments: list[dict[str, str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        child_environments.append(kwargs["env"])
        if command[1:3] == ["login", "status"]:
            return _completed(command, stdout="Logged in using ChatGPT\n")
        schema_path = Path(command[command.index("--output-schema") + 1])
        assert json.loads(schema_path.read_text(encoding="utf-8")) == DETAILED_RESPONSE_SCHEMA["schema"]
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(json.dumps(_response(), ensure_ascii=False), encoding="utf-8")
        return _completed(command)

    monkeypatch.setattr("backend.pipeline.codex_subscription_client.subprocess.run", fake_run)
    environment = {"PATH": "/bin", "OPENAI_API_KEY": "must-not-leak", "CODEX_HOME": "/oauth"}
    client = CodexSubscriptionClient(
        model="gpt-5.6-sol", executable="codex", environment=environment,
    )
    status, output = run_one(
        transcript_path, output_dir=output_dir, client=client, prompt="extract",
        reasoning_effort="medium", force=False,
        sections=SectionSettings(allow_generated=False),
    )
    assert status == "created"
    package = json.loads(output.read_text(encoding="utf-8"))
    assert package["extraction"]["backend"] == "codex_subscription"
    assert len(package["extraction"]["model_output_sha256"]) == 64
    int(package["extraction"]["model_output_sha256"], 16)
    assert package["coverage"]["available"] is True
    assert package["coverage"]["unprocessed"] == 0
    assert package["extraction"]["artifact_sha256"] == _package_artifact_sha256(
        package
    )
    original_generation = package["extraction"]["generation_fingerprint_sha256"]
    original_full_fingerprint = package["extraction"]["fingerprint_sha256"]
    original_source_file_sha256 = package["source_documents"][0]["source_file_sha256"]
    original_claim_ids = [row["claim_id"] for row in package["claims"]]
    assert all("OPENAI_API_KEY" not in child for child in child_environments)
    section_plan_path = next((output_dir / "section-plans").glob("*.json"))
    section_plan_before = section_plan_path.read_bytes()
    section_plan_mtime_before = section_plan_path.stat().st_mtime_ns

    def unexpected_run(*_args, **_kwargs):
        raise AssertionError("an unchanged semantic generation must not launch Codex")

    monkeypatch.setattr("backend.pipeline.codex_subscription_client.subprocess.run", unexpected_run)
    fallback_status, fallback_output = run_one(
        transcript_path, output_dir=output_dir, client=client, prompt="extract",
        reasoning_effort="medium", force=False,
        sections=SectionSettings(
            allow_generated=False,
            fallback_max_sentences=1,
        ),
    )
    assert fallback_status == "skipped"
    assert fallback_output == output

    editorial_only_edit = _titled_transcript()
    editorial_only_edit["script"].insert(
        1,
        {
            "index": "comment-1",
            "type": "comment",
            "text": "这条编辑备注既不是来源，也不进入模型。",
        },
    )
    transcript_path.write_text(
        json.dumps(editorial_only_edit, ensure_ascii=False), encoding="utf-8"
    )
    fresh_client = CodexSubscriptionClient(
        model="gpt-5.6-sol", executable="codex", environment=environment,
    )
    cached_status, cached_output = run_one(
        transcript_path, output_dir=output_dir, client=fresh_client, prompt="extract",
        reasoning_effort="medium", force=False,
        sections=SectionSettings(allow_generated=False),
    )
    assert cached_status == "created"
    assert cached_output == output
    editorial_package = json.loads(output.read_text(encoding="utf-8"))
    assert editorial_package["extraction"]["generation_fingerprint_sha256"] == original_generation
    assert editorial_package["extraction"]["fingerprint_sha256"] != original_full_fingerprint
    assert editorial_package["source_documents"][0]["source_file_sha256"] != original_source_file_sha256
    assert [row["claim_id"] for row in editorial_package["claims"]] == original_claim_ids
    assert editorial_package["claims"][0]["extraction_fingerprints"] == [original_generation]
    assert editorial_package["sections"][0]["cached"] is True
    assert section_plan_path.read_bytes() == section_plan_before
    assert section_plan_path.stat().st_mtime_ns == section_plan_mtime_before

    # A subtitle is editorial structure in the same JSON container. It may
    # change the package provenance and the displayed section label, but not
    # the semantic model generation or any generation-scoped record ID.
    title_only_edit = json.loads(json.dumps(editorial_only_edit, ensure_ascii=False))
    title_only_edit["script"][0]["text"] = "## 编辑标题"
    transcript_path.write_text(
        json.dumps(title_only_edit, ensure_ascii=False), encoding="utf-8"
    )
    title_status, title_output = run_one(
        transcript_path, output_dir=output_dir, client=fresh_client, prompt="extract",
        reasoning_effort="medium", force=False,
        sections=SectionSettings(allow_generated=False),
    )
    assert title_status == "created"
    assert title_output == output
    title_package = json.loads(output.read_text(encoding="utf-8"))
    assert title_package["extraction"]["generation_fingerprint_sha256"] == original_generation
    assert [row["claim_id"] for row in title_package["claims"]] == original_claim_ids
    assert title_package["sections"][0]["cached"] is True

    # Timing is current locator metadata, not spoken text. Recompile it from
    # the authoritative row without asking the model to repeat its claims.
    timing_only_edit = json.loads(json.dumps(title_only_edit, ensure_ascii=False))
    first_spoken_row = next(
        row for row in timing_only_edit["script"] if row.get("index") == 10
    )
    first_spoken_row["start_time"] = 101.0
    first_spoken_row["end_time"] = 108.0
    transcript_path.write_text(
        json.dumps(timing_only_edit, ensure_ascii=False), encoding="utf-8"
    )
    timing_status, timing_output = run_one(
        transcript_path, output_dir=output_dir, client=fresh_client, prompt="extract",
        reasoning_effort="medium", force=False,
        sections=SectionSettings(allow_generated=False),
    )
    assert timing_status == "created"
    assert timing_output == output
    timing_package = json.loads(output.read_text(encoding="utf-8"))
    assert timing_package["extraction"]["generation_fingerprint_sha256"] == original_generation
    assert [row["claim_id"] for row in timing_package["claims"]] == original_claim_ids
    first_spoken_fragment = next(
        row
        for row in timing_package["source_fragments"]
        if row["paragraph_key"] == "S0001"
    )
    assert first_spoken_fragment["media_time"] == 101.0
    assert timing_package["claims"][0]["occurrences"][0]["anchors"][0]["media_time"] == 101.0
    assert timing_package["sections"][0]["cached"] is True

    # Legacy runner versions exposed the current package before coverage was
    # calculated. The exact validated model generation must be repaired
    # mechanically, not called again or mistaken for a complete no-op.
    incomplete = json.loads(output.read_text(encoding="utf-8"))
    del incomplete["coverage"]
    output.write_text(json.dumps(incomplete, ensure_ascii=False), encoding="utf-8")
    recovered_status, recovered_output = run_one(
        transcript_path, output_dir=output_dir, client=fresh_client, prompt="extract",
        reasoning_effort="medium", force=False,
        sections=SectionSettings(allow_generated=False),
    )
    assert recovered_status == "created"
    assert recovered_output == output
    recovered = json.loads(output.read_text(encoding="utf-8"))
    assert recovered["coverage"]["available"] is True
    assert recovered["extraction"]["artifact_sha256"] == _package_artifact_sha256(
        recovered
    )


def test_subscription_fallback_limit_splits_only_after_uncapped_cache_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    transcript_path = tmp_path / "new-source.json"
    transcript_path.write_text(
        json.dumps(_titled_transcript(), ensure_ascii=False), encoding="utf-8"
    )
    captured: dict[str, object] = {}

    def fake_extraction(**kwargs):
        captured.update(kwargs)
        return "created", kwargs["output_path"]

    monkeypatch.setattr(extraction_runner, "_run_extraction", fake_extraction)

    class FakeClient:
        model = "gpt-5.6-sol"
        max_output_tokens = 64000

    status, _ = run_one(
        transcript_path,
        output_dir=tmp_path / "output",
        client=FakeClient(),
        prompt="extract",
        reasoning_effort="medium",
        force=False,
        sections=SectionSettings(
            allow_generated=False,
            fallback_max_sentences=2,
        ),
    )

    assert status == "created"
    plan = captured["plan"]
    assert isinstance(plan, extraction_runner.SectionPlan)
    assert len(plan.sections) == 2
    assert plan.max_section_sentences == 2
    assert plan.split_lineage


def test_legacy_generated_plan_title_rename_is_rebuilt_without_model_call(
    tmp_path: Path,
) -> None:
    body = _transcript()["script"]
    old_source = {
        "metadata": {"title": "固定抽取来源", "status": "published"},
        "script": [
            {"index": "subtitle-1", "type": "subtitle", "text": "## 旧标题一"},
            body[0],
            body[1],
            {"index": "subtitle-2", "type": "subtitle", "text": "## 旧标题二"},
            body[2],
        ],
    }
    current_source = json.loads(json.dumps(old_source, ensure_ascii=False))
    current_source["script"][0]["text"] = "## 新标题一"
    current_source["script"][3]["text"] = "## 新标题二"
    old_projection = extraction_runner.project_script(old_source["script"])
    current_projection = extraction_runner.project_script(current_source["script"])
    legacy_plan = SectionPlan(
        sections=(
            Section(index=1, start=0, end=2, title="旧标题一"),
            Section(index=2, start=2, end=3, title="旧标题二"),
        ),
        origin="generated_subtitles",
    )
    plan_path = (
        tmp_path
        / "section-plans"
        / f"{extraction_runner._slug('source')}.json"
    )
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(
        json.dumps(
            {
                "source_body_sha256": old_projection.body_sha256,
                "editorial_structure_sha256": old_projection.editorial_structure_sha256,
                "origin": legacy_plan.origin,
                "section_level": 2,
                "max_section_sentences": None,
                "section_strategy": None,
                "split_lineage": [],
                "sections": [vars(section) for section in legacy_plan.sections],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class NoModelCall:
        def generate_json(self, *_args, **_kwargs):
            raise AssertionError("title rename must not regenerate section boundaries")

    resolved = extraction_runner.resolve_section_plan(
        source=current_source,
        source_id="source",
        source_sha256=current_projection.body_sha256,
        output_dir=tmp_path,
        allow_generated=True,
        client=NoModelCall(),
    )

    assert resolved.generation_identity() == legacy_plan.generation_identity()
    assert [section.title for section in resolved.sections] == ["新标题一", "新标题二"]
    saved = json.loads(plan_path.read_text(encoding="utf-8"))
    assert saved["editorial_topology_sha256"] == current_projection.editorial_topology_sha256


def test_section_settings_rejects_two_competing_section_caps() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        SectionSettings(max_sentences=125, fallback_max_sentences=125)


def test_fallback_split_reuses_an_unchanged_base_section_cache(
    tmp_path: Path,
) -> None:
    source = _transcript()
    base = SectionPlan(
        sections=(
            Section(index=1, start=0, end=2, title="超长部分"),
            Section(index=2, start=2, end=3, title="已完成部分"),
        ),
        origin="source_headings",
    )
    capped = apply_section_limit(
        base,
        [2, 1, 1],
        headings=(),
        max_section_sentences=2,
    )
    unchanged = base.sections[1]
    response = {
        "questions": [], "positions": [], "evidence_steps": [], "claims": [],
        "evidence_relations": [], "claim_relations": [],
        "observations": [{
            "observation_id": "OBS001",
            "statement": "听众提出问题",
            "observation_type": "narrative_structure",
            "argument_role": "background",
            "scripture_refs": [],
                "anchors": [{
                    "segment_index": "S0003",
                    "start_time": None,
                    "end_time": None,
                    "verbatim_excerpt": "所以这表明神性吗？",
                }],
        }],
        "sentence_audit": [{
            "sentence_id": "S0003#001",
            "status": "extracted",
            "covered_by": ["OBS001"],
            "reason_code": None,
            "reason": "",
        }],
    }
    model_contract_fingerprint = "model-contract"
    unchanged_sentences = extraction_runner.section_sentences(source, unchanged)
    model_input_sha256 = _section_model_input_sha256(
        source, "header", unchanged, unchanged_sentences
    )
    section_fingerprint = _section_generation_fingerprint(
        model_contract_fingerprint, model_input_sha256
    )
    cache_path = _section_cache_path(
        tmp_path, "source", section_fingerprint, unchanged
    )
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(
        json.dumps(
            _section_cache_artifact(
                unchanged,
                response,
                section_fingerprint,
                model_input_sha256=model_input_sha256,
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class NoModelCall:
        last_usage = None

        def generate_json(self, *_args, **_kwargs):
            raise AssertionError("unchanged base section must come from cache")

    combined, usage, rows, _ = _extract_sections(
        source_id="source",
        exclusion_source_id="SRC-source",
        source=source,
        headings=(),
        header="header",
        plan=capped,
        output_dir=tmp_path,
        client=NoModelCall(),
        prompt="prompt",
        fingerprint="capped-generation",
        cache_contract_fingerprint=model_contract_fingerprint,
        force=False,
        only=(3,),
    )

    assert usage == []
    assert rows == [{
        "index": 3,
        "start": 2,
        "end": 3,
        "title": "已完成部分",
        "attempts": 0,
        "cached": True,
    }]
    assert combined["observations"][0]["observation_id"] == "P03-OBS001"


def test_section_cache_identity_reuses_renumbered_section_but_rejects_text_edit(
    tmp_path: Path,
) -> None:
    source = _transcript()
    old_section = Section(index=2, start=2, end=3, title="旧标题")
    renumbered = Section(index=3, start=2, end=3, title="新标题")
    sentences = extraction_runner.section_sentences(source, old_section)
    response = {
        "questions": [], "positions": [], "evidence_steps": [], "claims": [],
        "evidence_relations": [], "claim_relations": [],
        "observations": [{
            "observation_id": "OBS001",
            "statement": "听众提出问题",
            "observation_type": "narrative_structure",
            "argument_role": "background",
            "scripture_refs": [],
            "anchors": [{
                "segment_index": "S0003", "start_time": None, "end_time": None,
                "verbatim_excerpt": "所以这表明神性吗？",
            }],
        }],
        "sentence_audit": [{
            "sentence_id": "S0003#001", "status": "extracted",
            "covered_by": ["OBS001"], "reason_code": None, "reason": "",
        }],
    }
    contract = "model-contract"
    old_input = _section_model_input_sha256(
        source, "header", old_section, sentences
    )
    fingerprint = _section_generation_fingerprint(contract, old_input)
    cache_path = _section_cache_path(tmp_path, "source", fingerprint, old_section)
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(
        json.dumps(
            _section_cache_artifact(
                old_section,
                response,
                fingerprint,
                model_input_sha256=old_input,
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert _section_cache_path(tmp_path, "source", fingerprint, renumbered) == cache_path
    assert _load_valid_section_cache(
        cache_path,
        section=renumbered,
        fingerprint=fingerprint,
        source=source,
        sentences=sentences,
        model_input_sha256=old_input,
    ) == response

    edited = json.loads(json.dumps(source, ensure_ascii=False))
    edited["script"][2]["text"] = "听众：所以这真的表明神性吗？"
    edited_sentences = extraction_runner.section_sentences(edited, renumbered)
    edited_input = _section_model_input_sha256(
        edited, "header", renumbered, edited_sentences
    )
    assert edited_input != old_input
    assert _section_generation_fingerprint(contract, edited_input) != fingerprint
    assert _load_valid_section_cache(
        cache_path,
        section=renumbered,
        fingerprint=fingerprint,
        source=edited,
        sentences=edited_sentences,
        model_input_sha256=edited_input,
    ) is None


def test_subscription_backend_changes_fingerprint_without_changing_api_identity() -> None:
    kwargs = {
        "source_sha256": hashlib.sha256(b"source").hexdigest(),
        "prompt": "prompt",
        "model_id": "gpt-5.6-sol",
        "reasoning_effort": "medium",
        "max_output_tokens": 64000,
    }
    existing_api_identity = extraction_identity(**kwargs)
    explicit_default_identity = extraction_identity(**kwargs, backend=None)
    subscription_identity = extraction_identity(**kwargs, backend="codex_subscription")
    assert existing_api_identity == explicit_default_identity
    assert "backend" not in existing_api_identity
    assert subscription_identity["backend"] == "codex_subscription"
    assert subscription_identity["fingerprint_sha256"] != existing_api_identity["fingerprint_sha256"]
