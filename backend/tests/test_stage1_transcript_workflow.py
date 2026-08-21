import json
from types import SimpleNamespace

from backend.pipeline import stage1
from backend.pipeline import transcript_pipeline
from backend.api import sermon_converter_service as service


class _FakeStream:
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def __iter__(self):
        return iter(())

    def get_final_completion(self):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"answer":"ok"}'))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )


class _FakeCompletions:
    def __init__(self):
        self.kwargs = None
        self.stream_kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"answer":"ok"}'))]
        )

    def stream(self, **kwargs):
        self.stream_kwargs = kwargs
        return _FakeStream(kwargs)


class _FakeOpenAI:
    def __init__(self, **_kwargs):
        self.completions = _FakeCompletions()
        self.chat = SimpleNamespace(completions=self.completions)

    def with_options(self, **_kwargs):
        return self


def test_transcript_prompt_bundle_is_runtime_specific():
    transcript = stage1.get_stage1_prompt_bundle("transcript")
    notes = stage1.get_stage1_prompt_bundle("sermon_note")

    assert transcript != notes
    assert set(transcript) == {
        "evidence_inventory",
        "manuscript_planner",
        "unit_generator",
        "coverage_auditor",
    }
    assert "不可先按连续行号切割全文" in transcript["evidence_inventory"]
    assert "每个 evidence ID 必须且只能被分配到一个单元" in transcript["manuscript_planner"]
    assert "supports_unit_ids" in transcript["manuscript_planner"]
    assert "問題 → 直接回答" in transcript["unit_generator"]
    assert "交叉經文及其證明作用" in transcript["unit_generator"]
    assert "出处 → 经文 → 解释" in transcript["unit_generator"]
    assert "scripture_presentations" in transcript["evidence_inventory"]
    assert "supporting_appendices" in transcript["unit_generator"]
    assert "### 釋經" in transcript["unit_generator"]
    assert "covered_evidence_ids" in transcript["unit_generator"]
    assert "完整 transcript" in transcript["coverage_auditor"]
    assert "{{CATEGORY_DEFINITIONS}}" not in transcript["unit_generator"]


def test_stage1_openai_client_uses_gpt56_structured_output(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(stage1, "OpenAI", _FakeOpenAI)
    # Below STREAMING_OUTPUT_THRESHOLD so this stays a test of the request
    # shape rather than of which transport carries it.
    client = stage1.Stage1OpenAIClient(
        model="gpt-5.6-sol", max_retries=1, max_output_tokens=8000
    )
    schema = {
        "name": "answer_schema",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
    }

    result = client.generate_json("system", "user", schema, temperature=0.7)

    assert result == {"answer": "ok"}
    request = client.client.completions.kwargs
    assert request["model"] == "gpt-5.6-sol"
    assert request["reasoning_effort"] == "medium"
    assert request["response_format"] == {"type": "json_schema", "json_schema": schema}
    assert "temperature" not in request
    assert client.client.completions.stream_kwargs is None


def test_stage1_openai_client_streams_a_budget_it_cannot_deliver_in_one_response(monkeypatch):
    """A budget over the threshold has to stream, and streaming has to ask for usage.

    The SDK timeout is httpx's idle timeout, so a non-streaming call survives
    only while the server keeps the socket busy; a reasoning model spending its
    budget on thinking sends nothing for minutes. And `stream()` without
    `stream_options` returns `usage = None`, which would cost every streamed
    call its token counts -- the ledger this pipeline prices runs from.
    """

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(stage1, "OpenAI", _FakeOpenAI)
    client = stage1.Stage1OpenAIClient(
        model="kimi-k3", max_retries=1,
        max_output_tokens=stage1.STREAMING_OUTPUT_THRESHOLD + 1,
        temperature=1.0,
        stream_large_output=True,
    )
    schema = {"name": "answer_schema", "strict": True, "schema": {"type": "object"}}

    result = client.generate_json("system", "user", schema)

    assert result == {"answer": "ok"}
    assert client.client.completions.kwargs is None, "must not use the blocking path"
    streamed = client.client.completions.stream_kwargs
    assert streamed["stream_options"] == {"include_usage": True}
    assert streamed["max_completion_tokens"] == stage1.STREAMING_OUTPUT_THRESHOLD + 1
    assert streamed["temperature"] == 1.0, "kimi-k3 rejects every temperature but 1"
    assert client.last_usage is not None


class _FakeTranscriptClient:
    user_prompts = []

    def __init__(self, **_kwargs):
        self.calls = []

    def generate_json(self, _system_prompt, _user_prompt, schema, **_kwargs):
        self.calls.append(schema["name"])
        self.__class__.user_prompts.append(_user_prompt)
        if schema["name"] == "transcript_evidence_inventory_v1":
            return {
                "evidence": [
                    {
                        "evidence_id": "E001",
                        "type": "question",
                        "category": "釋經",
                        "content": "问题是什么？",
                        "scripture_refs": [],
                        "scripture_presentations": [],
                        "source_ranges": [{"start_line": 1, "end_line": 1}],
                        "supports": [],
                        "answers_question": None,
                        "question_status": "answered",
                    },
                    {
                        "evidence_id": "E002",
                        "type": "answer",
                        "category": "釋經",
                        "content": "这是教授的回答。",
                        "scripture_refs": [],
                        "scripture_presentations": [],
                        "source_ranges": [{"start_line": 3, "end_line": 3}],
                        "supports": [],
                        "answers_question": "E001",
                        "question_status": None,
                    },
                    {
                        "evidence_id": "E003",
                        "type": "scripture_evidence",
                        "category": "釋經",
                        "content": "交叉经文支持这个回答。",
                        "scripture_refs": ["太17:5"],
                        "scripture_presentations": [{
                            "reference": "太 17:5",
                            "mode": "direct_quote",
                            "quoted_text": "这是我的爱子。",
                            "role": "支持这个回答。",
                        }],
                        "source_ranges": [{"start_line": 2, "end_line": 2}],
                        "supports": ["E002"],
                        "answers_question": None,
                        "question_status": None,
                    },
                ],
                "inventory_summary": {
                    "total_evidence": 3,
                    "question_ids": ["E001"],
                    "unanswered_question_ids": [],
                    "scripture_evidence_ids": ["E003"],
                },
            }
        if schema["name"] == "transcript_manuscript_plan_v1":
            return {
                "units": [{
                    "unit_id": "temporary",
                    "title": "问题与回答",
                    "unit_kind": "main",
                    "supports_unit_ids": [],
                    "central_question": "问题是什么？",
                    "direct_answer": "这是教授的回答。",
                    "scripture_range": "太17:5",
                    "objective": "说明问答及其经文根据",
                    "evidence_ids": ["E001", "E002", "E003"],
                    "category_assignments": {
                        "exegesis": ["E001", "E002", "E003"],
                        "theological_significance": [],
                        "application": [],
                        "appendix": [],
                    },
                    "source_ranges": [
                        {"start_line": 1, "end_line": 1},
                        {"start_line": 3, "end_line": 3},
                    ],
                    "plan_reason": "把相隔的提问与回答放在同一逻辑单元。",
                }],
                "unassigned_evidence_ids": [],
            }
        if schema["name"] == "transcript_manuscript_unit_v1":
            return {
                "manuscript_sections": {
                    "exegesis": (
                        "### 釋經\n\n问题的直接回答。太 17:5：\n\n"
                        "> 这是我的爱子。\n\n这段经文支持这个回答。"
                    ),
                    "theological_significance": None,
                    "application": None,
                    "appendix": None,
                },
                "covered_evidence_ids": ["E001", "E002", "E003"],
                "coverage_notes": [],
            }
        return {
            "overall_status": "pass",
            "findings": [],
            "missing_evidence_ids": [],
            "unanswered_question_ids": [],
            "misclassified_evidence_ids": [],
        }


def test_transcript_pipeline_uses_global_evidence_plan_generation_and_audit(monkeypatch, tmp_path):
    monkeypatch.setattr(transcript_pipeline, "Stage1OpenAIClient", _FakeTranscriptClient)
    source = tmp_path / "transcript.md"
    source.write_text("教授提出问题\n太17:5：这是我的爱子。\n教授后来回答", encoding="utf-8")
    output_dir = tmp_path / "output"

    analyzed = transcript_pipeline.run_transcript_pipeline(
        input_path=source,
        output_dir=output_dir,
        mode="analyze",
    )
    assert [unit["unit_id"] for unit in analyzed.units] == ["U001"]
    assert not list((output_dir / "transcript_generated_units").glob("*.json"))

    generated = transcript_pipeline.run_transcript_pipeline(
        input_path=source,
        output_dir=output_dir,
        mode="generate_all",
    )
    assert generated.audit["overall_status"] == "pass"
    assert generated.generated_units[0]["covered_evidence_ids"] == ["E001", "E002", "E003"]
    assert generated.generated_units[0]["heading_title"] == "一、问题与回答"
    assert generated.combined_markdown.startswith("## 一、问题与回答\n\n### 釋經")
    assert generated.combined_markdown.count("### 釋經") == 1
    assert not generated.generated_units[0]["manuscript_sections"]["exegesis"].startswith("###")
    assert "### 神學意義" not in generated.combined_markdown

    generated_unit_path = output_dir / "transcript_generated_units" / "U001.json"
    generated_unit_before_audit = generated_unit_path.read_bytes()
    human_edited_draft = (
        "## 一、人工整理後的講稿\n\n### 釋經\n\n保留人工修改。太 17:5：\n\n"
        "> 这是我的爱子。\n\n这段经文支持这个回答。"
    )
    (output_dir / "draft_v1.md").write_text(human_edited_draft, encoding="utf-8")
    audited = transcript_pipeline.run_transcript_pipeline(
        input_path=source,
        output_dir=output_dir,
        mode="audit",
        force=True,
    )
    assert audited.audit["overall_status"] == "pass"
    assert generated_unit_path.read_bytes() == generated_unit_before_audit
    assert audited.combined_markdown == human_edited_draft
    assert (output_dir / "draft_v1.md").read_text(encoding="utf-8") == human_edited_draft


def test_scripture_presentation_requires_reference_and_blockquote(monkeypatch):
    monkeypatch.setattr(transcript_pipeline, "Stage1OpenAIClient", _FakeTranscriptClient)
    pipeline = transcript_pipeline.TranscriptPipeline()
    evidence = [{
        "evidence_id": "E003",
        "scripture_presentations": [{
            "reference": "太 17:5",
            "mode": "direct_quote",
            "quoted_text": "这是我的爱子。",
            "role": "支持这个回答。",
        }],
    }]

    bad_issues = pipeline._scripture_presentation_issues(
        "太 17:5 说这是我的爱子，因此支持这个回答。",
        evidence,
    )
    assert bad_issues == [{
        "evidence_id": "E003",
        "reason": "经文原句未使用 Markdown blockquote：太 17:5",
    }]

    good_issues = pipeline._scripture_presentation_issues(
        "太 17:5：\n\n> 这是我的爱子。\n\n这段经文支持这个回答。",
        evidence,
    )
    assert good_issues == []


def test_appendix_links_use_stable_heading_anchors(monkeypatch):
    monkeypatch.setattr(transcript_pipeline, "Stage1OpenAIClient", _FakeTranscriptClient)
    pipeline = transcript_pipeline.TranscriptPipeline()
    unit = {
        "supporting_appendices": [{
            "unit_id": "U003",
            "title": "附錄一：啟示錄文體",
            "anchor": "附錄一-啟示錄文體",
        }],
    }

    assert pipeline._appendix_link_issues(
        {"exegesis": "詳見[附錄一：啟示錄文體](#附錄一-啟示錄文體)。"},
        unit,
    ) == []
    assert pipeline._appendix_link_issues(
        {"exegesis": "附錄另有說明，但沒有提供連結。"},
        unit,
    ) == ["缺少指向附錄一：啟示錄文體的内部链接 ](#附錄一-啟示錄文體)"]
    assert pipeline._strip_unit_numbering("附錄三：既有標題") == "既有標題"
    assert pipeline._chinese_number(12) == "十二"


def test_whole_manuscript_structure_checks_numbering_and_contextual_appendix_link(monkeypatch):
    monkeypatch.setattr(transcript_pipeline, "Stage1OpenAIClient", _FakeTranscriptClient)
    pipeline = transcript_pipeline.TranscriptPipeline()
    plan = {
        "units": [
            {
                "unit_id": "U001",
                "unit_kind": "main",
                "heading_title": "一、正文",
                "evidence_ids": ["E001"],
                "supporting_appendices": [{
                    "title": "附錄一：背景",
                    "anchor": "附錄一-背景",
                    "unit_id": "U002",
                }],
            },
            {
                "unit_id": "U002",
                "unit_kind": "appendix",
                "heading_title": "附錄一：背景",
                "evidence_ids": ["E002"],
                "supporting_appendices": [],
            },
        ]
    }
    good = "## 一、正文\n\n詳見[附錄一：背景](#附錄一-背景)。\n\n## 附錄一：背景\n\n內容。"
    assert pipeline._whole_manuscript_structure_findings(plan, good) == []

    missing_link = "## 一、正文\n\n沒有連結。\n\n## 附錄一：背景\n\n內容。"
    findings = pipeline._whole_manuscript_structure_findings(plan, missing_link)
    assert [item["finding_id"] for item in findings] == ["NAV001"]
    assert findings[0]["unit_id"] == "U001"

    missing_number = "## 正文\n\n內容。\n\n## 附錄一：背景\n\n內容。"
    findings = pipeline._whole_manuscript_structure_findings(plan, missing_number)
    assert findings[0]["description"] == "单元标题必须使用连续编号前缀：## 一、..."


def test_coverage_audit_reuses_legacy_analysis_without_generated_units(monkeypatch, tmp_path):
    monkeypatch.setattr(transcript_pipeline, "Stage1OpenAIClient", _FakeTranscriptClient)
    source = tmp_path / "transcript.md"
    source.write_text("教授提出问题\n太17:5：这是我的爱子。\n教授后来回答", encoding="utf-8")
    output_dir = tmp_path / "output"
    transcript_pipeline.run_transcript_pipeline(
        input_path=source,
        output_dir=output_dir,
        mode="analyze",
    )

    for artifact_name in ("evidence_inventory.json", "manuscript_plan.json"):
        artifact_path = output_dir / artifact_name
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        artifact["pipeline_signature"] = "legacy-signature"
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    (output_dir / "draft_v1.md").write_text(
        "## 一、人工稿\n\n太 17:5：\n\n> 这是我的爱子。\n\n这段经文支持教授的回答。",
        encoding="utf-8",
    )
    audited = transcript_pipeline.run_transcript_pipeline(
        input_path=source,
        output_dir=output_dir,
        mode="audit",
        force=True,
    )

    assert audited.audit["overall_status"] == "pass"
    assert not list((output_dir / "transcript_generated_units").glob("*.json"))


def test_integrated_coverage_audit_accepts_external_evidence_dispositions(monkeypatch, tmp_path):
    _FakeTranscriptClient.user_prompts.clear()
    monkeypatch.setattr(transcript_pipeline, "Stage1OpenAIClient", _FakeTranscriptClient)
    source = tmp_path / "transcript.md"
    source.write_text("教授提出问题\n太17:5：这是我的爱子。\n教授后来回答", encoding="utf-8")
    output_dir = tmp_path / "output"
    transcript_pipeline.run_transcript_pipeline(
        input_path=source,
        output_dir=output_dir,
        mode="analyze",
    )
    (output_dir / "draft_v1.md").write_text(
        "## 本讲新增内容\n\n### 釋經\n\n保留本讲新增内容。",
        encoding="utf-8",
    )
    (output_dir / "integration_application.json").write_text(
        json.dumps(
            {
                "status": "draft_generated_pending_patch_review",
                "local_units": [{"unit_title": "本讲新增内容", "evidence_ids": ["E001"]}],
                "pending_patches": [{"unit_title": "既有单元更新", "evidence_ids": ["E003"], "markdown": "更新内容"}],
                "evidence_dispositions": [
                    {"evidence_id": "E001", "disposition": "fully_represented"},
                    {"evidence_id": "E002", "disposition": "represented_by_existing_unit"},
                    {"evidence_id": "E003", "disposition": "merged_as_extension"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    audited = transcript_pipeline.run_transcript_pipeline(
        input_path=source,
        output_dir=output_dir,
        mode="audit",
        force=True,
    )

    assert audited.audit["overall_status"] == "pass"
    assert any("【Integration Application】" in prompt for prompt in _FakeTranscriptClient.user_prompts)
    assert not list((output_dir / "transcript_generated_units").glob("*.json"))


def test_integrated_stage1_status_distinguishes_total_and_remaining_patches(monkeypatch, tmp_path):
    project_id = "integrated-status"
    project_dir = tmp_path / project_id
    project_dir.mkdir()
    monkeypatch.setattr(service, "NOTES_TO_SERMON_DIR", tmp_path)
    (project_dir / "draft_v1.md").write_text("## 本讲新增内容", encoding="utf-8")
    (project_dir / "integration_application.json").write_text(
        json.dumps(
            {
                "status": "draft_generated_pending_patch_review",
                "series_id": "series-1",
                "local_units": [{"canonical_unit_id": "local-1", "unit_title": "本讲新增内容"}],
                "pending_patches": [
                    {"canonical_unit_id": "patch-1"},
                    {"canonical_unit_id": "patch-2"},
                    {"canonical_unit_id": "patch-3"},
                ],
                "patch_results": {
                    "patch-1": {"status": "applied"},
                    "patch-2": {"status": "applied"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    status = service._get_transcript_pipeline_status(
        project_id,
        project_dir,
        {},
        {},
        False,
        None,
        [],
    )

    assert status["summary"]["pending_patch_count"] == 3
    assert status["summary"]["applied_patch_count"] == 2
    assert status["summary"]["remaining_patch_count"] == 1
    assert status["summary"]["integration_series_id"] == "series-1"


def test_transcript_fidelity_audit_uses_only_assigned_unit_evidence(monkeypatch, tmp_path):
    project_id = "transcript-audit"
    project_dir = tmp_path / project_id
    project_dir.mkdir()
    (project_dir / "meta.json").write_text(
        json.dumps({"id": project_id, "title": "Test", "pages": [], "project_type": "transcript"}),
        encoding="utf-8",
    )
    (project_dir / "manuscript_plan.json").write_text(
        json.dumps({
            "payload": {
                "units": [
                    {
                        "unit_id": "U001",
                        "title": "第一单元",
                        "central_question": "第一问？",
                        "direct_answer": "第一答。",
                        "objective": "只处理第一主题",
                        "evidence_ids": ["E001"],
                        "category_assignments": {
                            "exegesis": ["E001"],
                            "theological_significance": [],
                            "application": [],
                            "appendix": [],
                        },
                        "source_ranges": [{"start_line": 1, "end_line": 1}],
                    },
                    {
                        "unit_id": "U002",
                        "title": "第二单元",
                        "central_question": "第二问？",
                        "direct_answer": "第二答。",
                        "objective": "只处理第二主题",
                        "evidence_ids": ["E002"],
                        "category_assignments": {
                            "exegesis": ["E002"],
                            "theological_significance": [],
                            "application": [],
                            "appendix": [],
                        },
                        "source_ranges": [{"start_line": 2, "end_line": 2}],
                    },
                ]
            }
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (project_dir / "evidence_inventory.json").write_text(
        json.dumps({
            "payload": {
                "evidence": [
                    {
                        "evidence_id": "E001",
                        "content": "第一单元证据",
                        "source_ranges": [{"start_line": 1, "end_line": 1}],
                    },
                    {
                        "evidence_id": "E002",
                        "content": "第二单元证据",
                        "source_ranges": [{"start_line": 2, "end_line": 2}],
                    },
                ]
            }
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(service, "NOTES_TO_SERMON_DIR", tmp_path)
    service._write_draft_chunks_from_markdown(
        project_id,
        "## 第一单元\n\n### 釋經\n\n第一正文\n\n## 第二单元\n\n### 釋經\n\n第二正文",
    )

    chunk_meta = json.loads((project_dir / "draft_chunks_meta.json").read_text(encoding="utf-8"))
    assert chunk_meta[0]["unit_id"] == "U001"
    assert chunk_meta[0]["evidence_ids"] == ["E001"]

    context = service._get_fidelity_audit_source_slice(
        project_id,
        "chunk_001",
        "第一单元原文\n第二单元原文",
    )
    assert '"evidence_id": "E001"' in context
    assert "第一单元原文" in context
    assert '"evidence_id": "E002"' not in context
    assert "第二单元原文" not in context

    (project_dir / "coverage_audit.json").write_text(
        json.dumps({"payload": {"overall_status": "pass", "findings": []}}),
        encoding="utf-8",
    )
    assert service.check_and_update_project_audit_status(project_id) is True
    service.update_transcript_coverage_audit_state(project_id, stale=True)
    assert service.check_and_update_project_audit_status(project_id) is False
    updated_meta = json.loads((project_dir / "meta.json").read_text(encoding="utf-8"))
    assert updated_meta["audit_passed"] is False
    assert updated_meta["coverage_audit_stale"] is True


def test_detached_stage1_job_preserves_project_workflow_and_model(monkeypatch, tmp_path):
    project_id = "transcript-project"
    project_dir = tmp_path / project_id
    project_dir.mkdir()
    (project_dir / "unified_source.md").write_text("講座逐字稿", encoding="utf-8")
    (project_dir / "meta.json").write_text(
        '{"id":"transcript-project","title":"Test","pages":[],"project_type":"transcript"}',
        encoding="utf-8",
    )
    captured = {}

    class _FakeProcess:
        pid = 12345

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(service, "NOTES_TO_SERMON_DIR", tmp_path)
    monkeypatch.setattr(service.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(service, "update_sermon_processing_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_is_pid_running", lambda _pid: False)

    state = service.start_stage1_pipeline_job(project_id, mode="analyze")

    assert state["project_type"] == "transcript"
    assert state["model"] == "gpt-5.6-sol"
    command = captured["command"]
    assert command[command.index("--project-type") + 1] == "transcript"
    assert command[command.index("--model") + 1] == "gpt-5.6-sol"
    assert command[command.index("--mode") + 1] == "analyze"


def test_theological_audit_gate_requires_every_clean_final_chunk(monkeypatch, tmp_path):
    project_id = "theology-gate"
    project_dir = tmp_path / project_id
    project_dir.mkdir()
    (project_dir / "meta.json").write_text(
        json.dumps({
            "id": project_id,
            "title": "Test",
            "pages": [],
            "project_type": "transcript",
            "theological_audit_passed": False,
        }),
        encoding="utf-8",
    )
    (project_dir / "chunks_meta.json").write_text(
        json.dumps([{"id": "chunk_001"}, {"id": "chunk_002"}]),
        encoding="utf-8",
    )
    (project_dir / "theological_audit.json").write_text(
        json.dumps({
            "chunk_001": {"summary": "clean", "issues": []},
            "chunk_002": {"summary": "clean", "issues": []},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(service, "NOTES_TO_SERMON_DIR", tmp_path)

    assert service.check_and_update_theological_audit_status(project_id) is True
    meta = json.loads((project_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["theological_audit_passed"] is True
    assert meta["theological_audit_completed"] is True

    audits = json.loads((project_dir / "theological_audit.json").read_text(encoding="utf-8"))
    audits["chunk_002"]["issues"] = [{"type": "overstatement"}]
    (project_dir / "theological_audit.json").write_text(json.dumps(audits), encoding="utf-8")
    assert service.check_and_update_theological_audit_status(project_id) is True
    meta = json.loads((project_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["theological_audit_passed"] is False
    assert meta["theological_audit_completed"] is True

    service.invalidate_theological_audit_chunk(project_id, "chunk_002")
    assert service.check_and_update_theological_audit_status(project_id) is False
    audits = json.loads((project_dir / "theological_audit.json").read_text(encoding="utf-8"))
    assert "chunk_002" not in audits
    meta = json.loads((project_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["theological_audit_passed"] is False
    assert meta["theological_audit_completed"] is False


def test_generated_units_do_not_overwrite_existing_human_draft_chunks(monkeypatch, tmp_path):
    project_id = "human-draft-authority"
    project_dir = tmp_path / project_id
    generated_dir = project_dir / "transcript_generated_units"
    chunks_dir = project_dir / "draft_chunks"
    generated_dir.mkdir(parents=True)
    chunks_dir.mkdir()
    (generated_dir / "U001.json").write_text("{}", encoding="utf-8")
    (project_dir / "draft_chunks_meta.json").write_text(
        json.dumps([{"id": "chunk_001"}]),
        encoding="utf-8",
    )
    (chunks_dir / "chunk_001.md").write_text("人工修改", encoding="utf-8")
    monkeypatch.setattr(service, "NOTES_TO_SERMON_DIR", tmp_path)

    assert service._should_sync_draft_chunks_from_generated_units(project_id) is False


def test_streaming_is_opt_in_so_adding_a_model_cannot_change_an_existing_one(monkeypatch):
    """gpt-5.6-sol runs at a 64000 budget, above the streaming threshold.

    Streaming it would change how production extracts, as a side effect of
    reaching a new vendor. The client therefore streams only when the backend
    entry asks for it.
    """

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(stage1, "OpenAI", _FakeOpenAI)
    client = stage1.Stage1OpenAIClient(
        model="gpt-5.6-sol", max_retries=1,
        max_output_tokens=stage1.STREAMING_OUTPUT_THRESHOLD * 4,
    )

    client.generate_json("system", "user", {"name": "s", "schema": {"type": "object"}})

    assert client.client.completions.stream_kwargs is None, "must not stream by default"
    assert client.client.completions.kwargs is not None
