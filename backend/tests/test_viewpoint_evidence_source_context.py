import hashlib
from types import SimpleNamespace

import pytest

from backend.pipeline.knowledge_source import markdown_source_document
from backend.pipeline.source_projection import project_script
from backend.pipeline.viewpoint_evidence_source_context import compile_source_context, verify_source_context
from backend.api.canonical_repository.viewpoint_resolution import ReviewEvidence


def fixture(tmp_path):
    text = "太4:17與16:21不以καὶ承接且使用ἀπὸ τότε加ἤρξατο。正是這一結構上的差異，構成分段標誌。"
    path = tmp_path / "source.md"
    path.write_text("## 四、結構\n\n" + text + "\n", encoding="utf-8")
    source = {"source_id": "s", "source_type": "notes_manuscript", "source_path": str(path),
              "locator_space": "spoken_body_v1", "source_file_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    payload, _, _ = markdown_source_document(source)
    source["source_body_sha256"] = project_script(payload["script"]).body_sha256
    evidence = SimpleNamespace(source_id="s", paragraph_key="S0001", source_modality=None,
                               verbatim_excerpt="正是這一結構上的差異，構成分段標誌。", source_context=None)
    return source, evidence, path, text


def test_preserves_anaphoric_quote_and_supplies_actual_same_paragraph(tmp_path):
    source, evidence, _, text = fixture(tmp_path)
    quote = evidence.verbatim_excerpt
    ctx = compile_source_context(evidence, source)
    assert ctx.paragraph_text == text
    assert "καὶ" in ctx.paragraph_text and "ἤρξατο" in ctx.paragraph_text
    assert ctx.paragraph_text[ctx.excerpt_start_char:ctx.excerpt_end_char] == quote
    assert evidence.verbatim_excerpt == quote
    evidence.source_context = ctx
    verify_source_context(evidence, source)


def test_file_drift_stops(tmp_path):
    source, evidence, path, _ = fixture(tmp_path)
    path.write_text("different", encoding="utf-8")
    with pytest.raises(ValueError, match="physical SHA"):
        compile_source_context(evidence, source)


@pytest.mark.parametrize("mutation", ["source", "paragraph", "quote", "body", "locator"])
def test_wrong_binding_stops(tmp_path, mutation):
    source, evidence, _, _ = fixture(tmp_path)
    if mutation == "source":
        evidence.source_id = "another"
    elif mutation == "paragraph":
        evidence.paragraph_key = "S0002"
    elif mutation == "quote":
        evidence.verbatim_excerpt = "unrelated"
    elif mutation == "body":
        source["source_body_sha256"] = "0" * 64
    else:
        source["locator_space"] = "legacy"
    with pytest.raises(ValueError):
        compile_source_context(evidence, source)


def test_forged_context_rejected_against_disk(tmp_path):
    source, evidence, _, _ = fixture(tmp_path)
    actual = compile_source_context(evidence, source)
    evidence.source_context = actual.model_copy(update={"paragraph_text": "forged premise"})
    with pytest.raises(ValueError, match="registered physical"):
        verify_source_context(evidence, source)


def test_visual_evidence_keeps_its_existing_projection(tmp_path):
    source, evidence, _, _ = fixture(tmp_path)
    evidence.source_modality = "visual"
    assert compile_source_context(evidence, source) is None


def test_typed_evidence_keeps_legacy_shape_and_rejects_wrong_context(tmp_path):
    source, evidence, _, _ = fixture(tmp_path)
    raw = dict(evidence_step_id="E1", source_fragment_id="F1", source_id="s",
               paragraph_key="S0001", evidence_statement="statement",
               verbatim_excerpt=evidence.verbatim_excerpt, citation_id="C1",
               citation_revision=1, citation_status="candidate", source_sha256="a" * 64,
               support_eligibility="pending", anchor_state="pending",
               valid_for_identity_review=False)
    legacy = ReviewEvidence.model_validate(raw)
    assert "source_context" not in legacy.model_dump(mode="json")
    context = compile_source_context(evidence, source).model_dump(mode="json")
    assert ReviewEvidence.model_validate(raw | {"source_context": context}).source_context is not None
    for change in ({"source_id": "another"}, {"paragraph_key": "S0002"},
                   {"verbatim_excerpt": "unrelated"}):
        with pytest.raises(ValueError, match="exact source/paragraph/excerpt"):
            ReviewEvidence.model_validate(raw | {"source_context": context} | change)
