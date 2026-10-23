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


def test_repeated_quote_in_same_paragraph_keeps_full_context(tmp_path):
    source, evidence, path, _ = fixture(tmp_path)
    quote = evidence.verbatim_excerpt
    text = quote + "這裡再次強調：" + quote
    path.write_text("## 標題\n\n" + text + "\n", encoding="utf-8")
    source["source_file_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    payload, _, _ = markdown_source_document(source)
    source["source_body_sha256"] = project_script(payload["script"]).body_sha256
    context = compile_source_context(evidence, source)
    assert context.paragraph_text == text
    assert context.paragraph_text.count(quote) == 2
    assert context.excerpt_start_char == 0
    assert context.excerpt_end_char == len(quote)
    assert evidence.verbatim_excerpt == quote
    evidence.source_context = context
    verify_source_context(evidence, source)
    # Another literal copy is not the deterministic packet binding.
    evidence.source_context = context.model_copy(update={
        "excerpt_start_char": text.rindex(quote), "excerpt_end_char": len(text)})
    with pytest.raises(ValueError, match="registered physical"):
        verify_source_context(evidence, source)


def test_quote_in_other_paragraph_cannot_supply_context(tmp_path):
    source, evidence, path, _ = fixture(tmp_path)
    path.write_text("這是第一段。\n\n" + evidence.verbatim_excerpt + "\n", encoding="utf-8")
    source["source_file_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    payload, _, _ = markdown_source_document(source)
    source["source_body_sha256"] = project_script(payload["script"]).body_sha256
    with pytest.raises(ValueError, match="own live paragraph"):
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


@pytest.fixture
def heading_transition(tmp_path, monkeypatch):
    import json
    from backend.pipeline import source_editorial_transition as transition
    original = {'metadata': {'title':'人子'}, 'script': [
        {'index': 12, 'start_time': 1.0, 'text': '這是前提，所以這是結論。'},
        {'index': 20, 'text': '另一個問題。'}]}
    path = tmp_path/'source.json'
    before_raw = json.dumps(original, ensure_ascii=False).encode()
    path.write_bytes(before_raw)
    source = {'source_id':'s', 'source_type':'sermon_transcript', 'source_path':str(path),
              'locator_space':'spoken_body_v1', 'source_file_sha256':transition.file_binding(path)['file_sha256'],
              'source_body_sha256':project_script(original['script']).body_sha256}
    after = {'metadata': original['metadata'], 'script': [
        {'index':'subtitle-added', 'type':'subtitle', 'text':'## 論證'}] + original['script']}
    after_raw = json.dumps(after, ensure_ascii=False).encode()
    proof_root = tmp_path/'proofs'
    transition.publish_transition(source=source, before_raw=before_raw, after_raw=after_raw,
                                   authorization='Synthetic heading metadata test', root=proof_root)
    monkeypatch.setattr(transition, 'transition_root', lambda root=None: proof_root)
    evidence = SimpleNamespace(source_id='s', paragraph_key='S0001', source_modality=None,
                               verbatim_excerpt='所以這是結論。', source_context=None)
    return source, evidence, path, after, after_raw


def test_heading_metadata_keeps_compiled_paragraph_packet_byte_identical(heading_transition):
    import json
    from backend.pipeline.viewpoint_evidence_source_context import load_context_source
    source, evidence, path, _, after_raw = heading_transition
    original = compile_source_context(evidence, source)
    original_bytes = original.model_dump_json().encode()
    path.write_bytes(after_raw)
    loaded = load_context_source(source)
    assert loaded[1] == hashlib.sha256(after_raw).hexdigest()
    assert loaded[1] != source['source_file_sha256']
    fresh = compile_source_context(evidence, source, loaded)
    assert fresh.model_dump_json().encode() == original_bytes
    assert fresh.source_file_sha256 == source['source_file_sha256']
    evidence.source_context = original
    verify_source_context(evidence, source, loaded)
    assert evidence.source_context.model_dump_json().encode() == original_bytes
    # Intermediate releases stored a live physical SHA in this field. Their
    # saved paragraph artifacts remain valid through the same exact proof.
    evidence.source_context = original.model_copy(update={'source_file_sha256':loaded[1]})
    verify_source_context(evidence, source, loaded)


@pytest.mark.parametrize('mutation', ['body', 'index', 'time', 'offset'])
def test_heading_proof_does_not_mask_real_paragraph_or_locator_change(heading_transition, mutation):
    import json
    source, evidence, path, after, after_raw = heading_transition
    original = compile_source_context(evidence, source)
    path.write_bytes(after_raw)
    evidence.source_context = original
    if mutation == 'body':
        after['script'][1]['text'] += '新內容'
    elif mutation == 'index':
        after['script'][1]['index'] = 99
    elif mutation == 'time':
        after['script'][1]['start_time'] = 2.0
    else:
        evidence.source_context = original.model_copy(update={'excerpt_start_char':0})
    if mutation != 'offset':
        path.write_text(json.dumps(after, ensure_ascii=False))
        with pytest.raises(ValueError):
            compile_source_context(evidence, source)
    with pytest.raises(ValueError):
        verify_source_context(evidence, source)


def test_identity_body_model_context_unchanged_by_headings(heading_transition, tmp_path, monkeypatch):
    import json
    from backend.pipeline import cvp_sql_flow as flow
    from backend.pipeline import viewpoint_batch_resolution_runner as runner
    from backend.pipeline import deferred_claim_cvp_continuation as deferred
    source, evidence, path, _, after_raw = heading_transition
    artifact = tmp_path/'policy.json'
    artifact.write_text('{}')
    bound = flow.binding(artifact)
    original_source_binding = flow.binding(path)
    raw_evidence = dict(evidence_step_id='E1', source_fragment_id='F1', source_id='s',
        paragraph_key='S0001', evidence_statement='statement', verbatim_excerpt=evidence.verbatim_excerpt,
        citation_id='C1', citation_revision=1, citation_status='candidate', source_sha256='a'*64,
        support_eligibility='pending', anchor_state='pending', valid_for_identity_review=False)
    claim = dict(claim_id='C', pinned_claim_revision=1, claim_revision_sha256='b'*64, source_id='s',
                 statement='結論', review_status='ai_consensus_reviewed', evidence=[raw_evidence])
    materials = {'policy':bound, 'semantic_contract':bound, 'sources':[original_source_binding],
        'packet':{'batch_id':'test', 'scope_label':'test', 'claims':[claim],
                  'registry_context':[], 'existing_links':[]}}
    model_bodies = []
    class Captured(Exception): pass
    def run_batch(**kwargs):
        model_bodies.append(kwargs['identity_source_loader'](['s']))
        raise Captured()
    monkeypatch.setattr(runner, 'run_batch', run_batch)
    monkeypatch.setattr(deferred, 'adapters', lambda _: {})
    store = SimpleNamespace(get_record=lambda col,sid: source, load_revision_dependents=lambda *a: None)
    with pytest.raises(Captured):
        flow.reviewed_cvp_package(tmp_path/'run', materials, store)
    path.write_bytes(after_raw)
    with pytest.raises(Captured):
        flow.reviewed_cvp_package(tmp_path/'run', materials, store)
    assert json.dumps(model_bodies[0], ensure_ascii=False).encode() == json.dumps(model_bodies[1], ensure_ascii=False).encode()
    assert model_bodies[1][0]['source_file_sha256'] == source['source_file_sha256']
    assert '## 論證' not in model_bodies[1][0]['paragraph_texts']
    assert materials['sources'] == [original_source_binding]
