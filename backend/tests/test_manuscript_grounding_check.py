import pytest

from backend.pipeline.manuscript_grounding_check import (
    GroundingCheckError,
    build_grounding_packet,
    build_paragraph_material,
    check_manuscript_grounding,
    check_paragraph_grounding,
    extract_provenance_paragraphs,
    validate_grounding_result,
)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def generate_json(self, _prompt, _packet, _schema):
        self.calls += 1
        if not self.responses:
            raise AssertionError("unexpected model call")
        return self.responses.pop(0)


def _knowledge():
    return {
        "claims": [
            {"claim_id": "CL1", "statement": "彼得認出耶穌是彌賽亞。", "evidence_step_ids": ["E1"]},
            {"claim_id": "CL2", "statement": "他不認識彌賽亞的性質。", "evidence_step_ids": ["E2"]},
        ],
        "evidence_steps": [
            {"evidence_step_id": "E1", "source_fragment_id": "F1"},
            {"evidence_step_id": "E2", "source_fragment_id": "F2"},
        ],
        "source_fragments": [
            {"fragment_id": "F1", "verbatim_excerpt": "彼得在該撒利亞腓立比宣認耶穌為基督。"},
            {"fragment_id": "F2", "verbatim_excerpt": "彼得並非不認識耶穌是彌賽亞，他的問題在於他不認識彌賽亞的性質。"},
        ],
    }


def _grounded_result():
    return {
        "schema_version": "matthew-exposition-grounding-result.v1",
        "exceeds_material": False,
        "unsupported_assertions": [],
        "notes": "",
    }


def _ungrounded_result(paragraph_text):
    return {
        "schema_version": "matthew-exposition-grounding-result.v1",
        "exceeds_material": True,
        "unsupported_assertions": [paragraph_text[:6]],
        "notes": "加了材料沒有的因果解釋",
    }


# ---- paragraph extraction ----


def test_extract_pairs_each_comment_with_the_paragraph_that_follows_it():
    markdown = (
        '<!-- provenance: {"attribution":"professor","claim_ids":["CL1"]} -->\n'
        "第一段文字。\n\n"
        '<!-- provenance: {"attribution":"scripture","scripture_refs":["Matt.16.21"]} -->\n'
        "> 經文引用。\n"
    )
    paragraphs = extract_provenance_paragraphs(markdown)
    assert len(paragraphs) == 2
    assert paragraphs[0]["paragraph_text"] == "第一段文字。"
    assert paragraphs[0]["provenance"]["attribution"] == "professor"
    assert paragraphs[1]["paragraph_text"] == "> 經文引用。"


def test_extract_marks_invalid_json_provenance_as_none():
    markdown = "<!-- provenance: {not json} -->\n段落。\n"
    paragraphs = extract_provenance_paragraphs(markdown)
    assert paragraphs[0]["provenance"] is None
    assert paragraphs[0]["paragraph_text"] == "段落。"


def test_extract_handles_a_dangling_comment_with_no_following_text():
    markdown = '<!-- provenance: {"attribution":"professor","claim_ids":["CL1"]} -->\n\n'
    paragraphs = extract_provenance_paragraphs(markdown)
    assert paragraphs[0]["paragraph_text"] == ""


# ---- material assembly ----


def test_material_carries_claim_statement_evidence_and_source_excerpt():
    material = build_paragraph_material(["CL2"], _knowledge())
    assert material == [
        {
            "claim_id": "CL2",
            "claim_statement": "他不認識彌賽亞的性質。",
            "attribution": "professor",
            "evidence": [
                {
                    "statement": None,
                    "source_excerpt": "彼得並非不認識耶穌是彌賽亞，他的問題在於他不認識彌賽亞的性質。",
                }
            ],
        }
    ]


def test_material_carries_an_editorial_instruction_as_separately_attributed_grounds():
    """A required step's instruction is the editorial board's decision, not the
    professor's words. It grounds the paragraph, but under its own attribution.
    """
    knowledge = _knowledge()
    knowledge["claims"][1]["editorial_instruction"] = "不把彌賽亞只寫成稱號。"
    material = build_paragraph_material(["CL2"], knowledge)
    assert material[0]["attribution"] == "professor"
    assert material[0]["editorial_instruction"] == {
        "attribution": "editor",
        "statement": "不把彌賽亞只寫成稱號。",
    }


def test_material_omits_the_instruction_key_when_a_claim_has_none():
    material = build_paragraph_material(["CL1"], _knowledge())
    assert "editorial_instruction" not in material[0]


def test_material_refuses_an_unknown_claim_id():
    with pytest.raises(GroundingCheckError, match="CL-MISSING"):
        build_paragraph_material(["CL-MISSING"], _knowledge())


def test_packet_rejects_oversized_material():
    knowledge = _knowledge()
    knowledge["claims"][0]["statement"] = "重複" * 20_000
    with pytest.raises(GroundingCheckError, match="exceeds"):
        build_grounding_packet("段落", ["CL1"], knowledge)


# ---- result validation ----


def test_validate_accepts_a_clean_pass():
    validate_grounding_result(_grounded_result(), paragraph_text="任意段落")


def test_validate_requires_assertions_when_exceeds_material_is_true():
    result = dict(_grounded_result())
    result["exceeds_material"] = True
    result["unsupported_assertions"] = []
    with pytest.raises(GroundingCheckError, match="unsupported_assertions"):
        validate_grounding_result(result, paragraph_text="任意段落")


def test_validate_requires_assertions_to_be_verbatim_substrings():
    result = _ungrounded_result("彼得的話表面上像是出於愛護")
    result["unsupported_assertions"] = ["模型自己編的話，不在段落裡"]
    with pytest.raises(GroundingCheckError, match="verbatim"):
        validate_grounding_result(result, paragraph_text="彼得的話表面上像是出於愛護")


# ---- end-to-end paragraph / manuscript checks ----


def test_check_paragraph_grounding_calls_the_model_once_and_returns_the_result():
    client = FakeClient([_grounded_result()])
    result = check_paragraph_grounding("彼得認出耶穌是彌賽亞。", ["CL1"], _knowledge(), client=client)
    assert result["exceeds_material"] is False
    assert client.calls == 1


def test_manuscript_check_flags_an_unsupported_professor_paragraph():
    paragraph = "這不是否定祂是王，而是糾正人用政治勝利界定彌賽亞的錯誤。"
    markdown = (
        f'<!-- provenance: {{"attribution":"professor","claim_ids":["CL1","CL2"]}} -->\n{paragraph}\n'
    )
    client = FakeClient([_ungrounded_result(paragraph)])
    report = check_manuscript_grounding(markdown, _knowledge(), client=client)
    assert report["passed"] is False
    assert report["paragraphs_checked"] == 1
    assert report["findings"][0]["code"] == "unsupported_assertion"
    assert report["findings"][0]["claim_ids"] == ["CL1", "CL2"]


def test_manuscript_check_passes_a_grounded_paragraph():
    markdown = (
        '<!-- provenance: {"attribution":"professor","claim_ids":["CL1"]} -->\n'
        "彼得認出耶穌是彌賽亞。\n"
    )
    client = FakeClient([_grounded_result()])
    report = check_manuscript_grounding(markdown, _knowledge(), client=client)
    assert report["passed"] is True
    assert report["findings"] == []


def test_manuscript_check_skips_scripture_and_bare_editor_paragraphs():
    markdown = (
        '<!-- provenance: {"attribution":"scripture","scripture_refs":["Matt.16.21"]} -->\n'
        "> 經文引用。\n\n"
        '<!-- provenance: {"attribution":"editor","visible_label":"編輯說明"} -->\n'
        "編輯說明文字。\n"
    )
    client = FakeClient([])
    report = check_manuscript_grounding(markdown, _knowledge(), client=client)
    assert report["paragraphs_checked"] == 0
    assert report["paragraphs_skipped"] == 2
    assert client.calls == 0


def test_manuscript_check_covers_both_professor_and_editorial_synthesis_attribution():
    markdown = (
        '<!-- provenance: {"attribution":"professor","claim_ids":["CL1"]} -->\n'
        "professor 段落。\n\n"
        '<!-- provenance: {"attribution":"editorial_synthesis","claim_ids":["CL2"],'
        '"synthesis_note":"綜合"} -->\n'
        "editorial_synthesis 段落。\n"
    )
    client = FakeClient([_grounded_result(), _grounded_result()])
    report = check_manuscript_grounding(markdown, _knowledge(), client=client)
    assert report["paragraphs_checked"] == 2
    assert client.calls == 2


def test_manuscript_check_records_a_finding_when_a_paragraph_cites_an_unknown_claim():
    markdown = (
        '<!-- provenance: {"attribution":"professor","claim_ids":["CL-GONE"]} -->\n段落。\n'
    )
    client = FakeClient([])
    report = check_manuscript_grounding(markdown, _knowledge(), client=client)
    assert report["passed"] is False
    assert report["findings"][0]["code"] == "grounding_check_failed"
    assert client.calls == 0


def test_grounding_uses_the_section_claim_scope_not_only_the_paragraph_declaration():
    """The plan assigns claims to a reader section; the paragraph declaration is
    finer than that. Checking only the ids a paragraph repeats rejects faithful
    sentences whose material was allotted to the section they sit in.
    """
    markdown = (
        "## 一節\n\n"
        '<!-- provenance: {"attribution":"professor","claim_ids":["CL1"]} -->\n'
        "段落內容。\n"
    )
    sections = [{"output_anchor": "## 一節", "claim_ids_used": ["CL1", "CL2"]}]

    seen = {}

    class Capturing(FakeClient):
        def generate_json(self, prompt, packet, schema):
            seen["packet"] = packet
            return super().generate_json(prompt, packet, schema)

    client = Capturing([_grounded_result()])
    report = check_manuscript_grounding(
        markdown, _knowledge(), client=client, author_sections=sections
    )
    assert report["passed"] is True
    assert "CL2" in seen["packet"], "section-assigned material must reach the checker"


def test_grounding_falls_back_to_the_paragraph_declaration_without_sections():
    markdown = (
        '<!-- provenance: {"attribution":"professor","claim_ids":["CL1"]} -->\n段落。\n'
    )
    seen = {}

    class Capturing(FakeClient):
        def generate_json(self, prompt, packet, schema):
            seen["packet"] = packet
            return super().generate_json(prompt, packet, schema)

    client = Capturing([_grounded_result()])
    check_manuscript_grounding(markdown, _knowledge(), client=client)
    assert "CL1" in seen["packet"]
    assert "CL2" not in seen["packet"]


def test_instructions_come_from_the_contract_even_for_a_reused_claim():
    """A claim created before the step backfill has no editorial_instruction of
    its own; the contract still imposes one, and grounding must see it.
    """
    from backend.pipeline.manuscript_grounding_check import instructions_from_contract

    contract = {
        "sections": [
            {
                "required_argument_steps": [
                    {"step_id": "S-C", "claim_id": "CL1", "statement": "說明責備的焦點。"}
                ]
            }
        ]
    }
    instructions = instructions_from_contract(contract)
    assert instructions == {"CL1": "說明責備的焦點。"}

    knowledge = _knowledge()          # CL1 carries no editorial_instruction
    material = build_paragraph_material(["CL1"], knowledge, instructions)
    assert material[0]["editorial_instruction"] == {
        "attribution": "editor",
        "statement": "說明責備的焦點。",
    }


def test_contract_instruction_takes_precedence_over_a_stale_claim_copy():
    from backend.pipeline.manuscript_grounding_check import instructions_from_contract

    knowledge = _knowledge()
    knowledge["claims"][0]["editorial_instruction"] = "舊的指令"
    contract = {
        "sections": [
            {"required_argument_steps": [{"claim_id": "CL1", "statement": "現行指令"}]}
        ]
    }
    material = build_paragraph_material(
        ["CL1"], knowledge, instructions_from_contract(contract)
    )
    assert material[0]["editorial_instruction"]["statement"] == "現行指令"
