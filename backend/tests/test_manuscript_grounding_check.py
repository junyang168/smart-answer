import pytest

from backend.pipeline.matthew_exposition_authoring import AuthoringContractError
from backend.pipeline.manuscript_grounding_check import (
    GroundingCheckError,
    build_grounding_packet,
    build_paragraph_material,
    cited_transcript_segments,
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
        "schema_version": "matthew-exposition-grounding-result.v2",
        "unsupported_assertions": [],
        "notes": "",
    }


def _ungrounded_result(paragraph_text):
    return {
        "schema_version": "matthew-exposition-grounding-result.v2",
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


def test_reviewer_cannot_return_a_verdict_of_its_own():
    """A reply that quotes ungrounded sentences and answers "no" to the yes/no
    question contradicts its own evidence, and only the opposite inconsistency
    used to be rejected. There is no longer a field to answer it in."""

    result = dict(_grounded_result())
    result["exceeds_material"] = False
    with pytest.raises(AuthoringContractError, match="exceeds_material"):
        validate_grounding_result(result, paragraph_text="任意段落")


def test_verdict_follows_the_quoted_sentences():
    paragraph = "彼得的話表面上像是出於愛護"
    flagged = check_paragraph_grounding(
        paragraph, ["CL1"], _knowledge(), client=FakeClient([_ungrounded_result(paragraph)])
    )
    assert flagged["exceeds_material"] is True
    clean = check_paragraph_grounding(
        paragraph, ["CL1"], _knowledge(), client=FakeClient([_grounded_result()])
    )
    assert clean["exceeds_material"] is False


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


def test_a_single_paragraph_call_failure_becomes_a_finding_not_a_crash():
    """Malformed JSON on one paragraph must not discard the other results.

    The gate still fails -- an unchecked paragraph is not an approved one --
    but the report survives to say which paragraph could not be checked.
    """

    class Failing:
        def __init__(self):
            self.calls = 0

        def generate_json(self, _prompt, _packet, _schema):
            self.calls += 1
            if self.calls == 1:
                raise ValueError("Expecting ':' delimiter: line 1 column 78")
            return _grounded_result()

    markdown = (
        '<!-- provenance: {"attribution":"professor","claim_ids":["CL1"]} -->\n第一段。\n\n'
        '<!-- provenance: {"attribution":"professor","claim_ids":["CL2"]} -->\n第二段。\n'
    )
    client = Failing()
    report = check_manuscript_grounding(markdown, _knowledge(), client=client)

    assert client.calls == 2, "第二段仍要檢查，不能因第一段失敗就中止"
    assert report["passed"] is False
    assert report["findings"][0]["code"] == "grounding_check_failed"
    assert "delimiter" in report["findings"][0]["error"]


def test_a_programming_error_still_propagates():
    class Buggy:
        def generate_json(self, _prompt, _packet, _schema):
            raise AssertionError("this is a bug, not a condition to report")

    markdown = '<!-- provenance: {"attribution":"professor","claim_ids":["CL1"]} -->\n段落。\n'
    with pytest.raises(AssertionError, match="this is a bug"):
        check_manuscript_grounding(markdown, _knowledge(), client=Buggy())


def test_a_footnote_definition_is_not_part_of_the_paragraph_it_follows():
    """The original-language policy puts the word form in a footnote on
    purpose; checking it against the paragraph's claims is meaningless, and
    its transliteration punctuation is what broke the model's JSON output.
    """
    markdown = (
        '<!-- provenance: {"attribution":"professor","claim_ids":["CL1"]} -->\n'
        "「體貼」的原文意思是關心、重視。[^1]\n\n"
        "[^1]: 原文動詞為 φρονέω（fron-eh'-o）。\n"
    )
    paragraphs = extract_provenance_paragraphs(markdown)
    assert paragraphs[0]["paragraph_text"] == "「體貼」的原文意思是關心、重視。[^1]"


def test_a_following_heading_is_not_part_of_the_paragraph():
    markdown = (
        '<!-- provenance: {"attribution":"professor","claim_ids":["CL1"]} -->\n'
        "段落內容。\n\n"
        "## 神學意義\n\n"
        "### 由基礎認識進入更深的真理\n"
    )
    paragraphs = extract_provenance_paragraphs(markdown)
    assert paragraphs[0]["paragraph_text"] == "段落內容。"

# ---- fragment id spellings and transcript segments ----


def _plural_spelling_knowledge():
    """Knowledge as the authoring store compiles it: the plural spelling only.

    `shared_knowledge_pilot` writes both `source_fragment_ids` and the singular
    `source_fragment_id` onto every step; knowledge compiled from the store
    keeps whichever its producer used. Reading only one spelling left the gate
    with no source excerpt at all on a store-compiled plan.
    """

    return {
        "claims": [
            {"claim_id": "CL1", "statement": "教導進入第二個階段。", "evidence_step_ids": ["E1"]},
        ],
        "evidence_steps": [
            {
                "evidence_step_id": "E1",
                "statement": "太16:21的「從此」是結構標誌。",
                "source_fragment_ids": ["F1", "F2"],
            },
        ],
        "source_fragments": [
            {
                "fragment_id": "F1",
                "source_id": "SRC-1",
                "source_segment_index": 732,
                "verbatim_excerpt": "馬太十六章二十一節是耶穌對門徒的教導的第二段的開始。",
            },
            {
                "fragment_id": "F2",
                "source_id": "SRC-1",
                "source_segment_index": 732,
                "verbatim_excerpt": "彼得認識耶穌為基督、彌賽亞，但不認識彌賽亞的性質。",
            },
        ],
    }


SEGMENT_732 = (
    "第一功課是什麼？你要先知道耶穌就是基督。可是門徒通過第一課的考試，耶穌開始教他們第二課。"
    "馬太十六章二十一節是耶穌對門徒的教導的第二段的開始。"
)
TRANSCRIPTS = {"SRC-1": {"732": SEGMENT_732}}


def test_material_resolves_excerpts_from_the_plural_fragment_spelling():
    material = build_paragraph_material(["CL1"], _plural_spelling_knowledge())
    assert material[0]["evidence"][0]["source_excerpt"] == (
        "馬太十六章二十一節是耶穌對門徒的教導的第二段的開始。\n"
        "彼得認識耶穌為基督、彌賽亞，但不認識彌賽亞的性質。"
    )


def test_material_still_resolves_the_singular_fragment_spelling():
    material = build_paragraph_material(["CL2"], _knowledge())
    assert material[0]["evidence"][0]["source_excerpt"] == (
        "彼得並非不認識耶穌是彌賽亞，他的問題在於他不認識彌賽亞的性質。"
    )


def test_cited_segments_carry_the_professors_wording_the_excerpt_left_out():
    segments = cited_transcript_segments(["CL1"], _plural_spelling_knowledge(), TRANSCRIPTS)
    assert [(item["source_id"], item["segment_index"]) for item in segments] == [("SRC-1", "732")]
    # The sentence rule 8e wants quoted is in the segment and in no excerpt.
    assert "門徒通過第一課的考試" in segments[0]["text"]


def test_a_segment_backing_several_claims_is_carried_once():
    knowledge = _plural_spelling_knowledge()
    knowledge["claims"].append(
        {"claim_id": "CL2", "statement": "彼得不認識彌賽亞的性質。", "evidence_step_ids": ["E1"]}
    )
    assert len(cited_transcript_segments(["CL1", "CL2"], knowledge, TRANSCRIPTS)) == 1


def test_only_segments_a_cited_fragment_points_at_are_carried():
    transcripts = {"SRC-1": {"732": SEGMENT_732, "733": "另一段講的是別的經文。"}}
    segments = cited_transcript_segments(["CL1"], _plural_spelling_knowledge(), transcripts)
    assert [item["segment_index"] for item in segments] == ["732"]


def test_packet_omits_the_segment_key_when_no_transcript_is_supplied():
    packet = build_grounding_packet("段落", ["CL1"], _plural_spelling_knowledge())
    assert "professor_transcript_segments" not in packet


def test_packet_omits_the_segment_key_when_the_claims_cite_no_sermon_segment():
    # A claim backed only by a notes fragment has no transcript behind it.
    packet = build_grounding_packet("段落", ["CL1"], _knowledge(), None, TRANSCRIPTS)
    assert "professor_transcript_segments" not in packet


def test_manuscript_check_passes_the_transcript_through_to_each_paragraph():
    seen = {}

    class RecordingClient:
        def generate_json(self, _prompt, packet, _schema):
            seen.update(__import__("json").loads(packet))
            return _grounded_result()

    markdown = (
        '<!-- provenance: {"attribution":"professor","claim_ids":["CL1"]} -->\n'
        "耶穌先要門徒認出祂是基督，通過了這一課，才開始教他們第二課。"
    )
    report = check_manuscript_grounding(
        markdown,
        _plural_spelling_knowledge(),
        client=RecordingClient(),
        transcript_texts=TRANSCRIPTS,
    )
    assert report["passed"]
    assert "門徒通過第一課的考試" in seen["professor_transcript_segments"][0]["text"]



def test_section_scope_material_carries_the_claim_without_its_whole_argument():
    """Regression: a paragraph is grounded against its own declaration widened
    by its section's scope. Sending every evidence chain for both put 19KB of
    argument behind a 192-byte paragraph, blew the packet budget, and left
    fifteen of eighteen paragraphs unchecked -- and an oversized packet raises
    `GroundingCheckError`, which is not an `unsupported_assertion`, so the
    repair path could not run either.

    What the paragraph cites needs its chain: that is what separates a
    supported inference from an invented one. The rest of the section only has
    to say whether an assertion is inside material this section may draw on,
    which the statement answers by itself.
    """

    knowledge = {
        "claims": [
            {
                "claim_id": "CL-CITED",
                "statement": "段落引用的主張。",
                "evidence_step_ids": ["E-1"],
            },
            {
                "claim_id": "CL-SECTION",
                "statement": "同節的其他材料。",
                "evidence_step_ids": ["E-2"],
            },
        ],
        "evidence_steps": [
            {"evidence_step_id": "E-1", "statement": "引用主張的證據。", "source_fragment_id": "FR-1"},
            {"evidence_step_id": "E-2", "statement": "其他材料的證據。", "source_fragment_id": "FR-2"},
        ],
        "source_fragments": [
            {"fragment_id": "FR-1", "verbatim_excerpt": "教授原話一"},
            {"fragment_id": "FR-2", "verbatim_excerpt": "教授原話二"},
        ],
    }
    material = build_paragraph_material(
        ["CL-CITED", "CL-SECTION"], knowledge, None, declared_claim_ids=["CL-CITED"]
    )
    by_id = {item["claim_id"]: item for item in material}

    assert by_id["CL-CITED"]["evidence"][0]["source_excerpt"] == "教授原話一"
    # In scope, and said so, but without an argument the paragraph never cited.
    assert by_id["CL-SECTION"]["claim_statement"] == "同節的其他材料。"
    assert "evidence" not in by_id["CL-SECTION"]
    assert by_id["CL-SECTION"]["scope"] == "section_material_not_cited_by_this_paragraph"


def test_omitting_declared_ids_keeps_every_claim_at_full_depth():
    """Callers that do not distinguish the two -- every existing one -- must
    keep the behaviour they had.
    """

    knowledge = {
        "claims": [{"claim_id": "CL-1", "statement": "主張。", "evidence_step_ids": ["E-1"]}],
        "evidence_steps": [
            {"evidence_step_id": "E-1", "statement": "證據。", "source_fragment_id": "FR-1"}
        ],
        "source_fragments": [{"fragment_id": "FR-1", "verbatim_excerpt": "原話"}],
    }
    material = build_paragraph_material(["CL-1"], knowledge)
    assert material[0]["evidence"][0]["source_excerpt"] == "原話"


def test_an_unchanged_paragraph_keeps_the_verdict_it_was_given(tmp_path):
    """Regression: these calls are not deterministic -- Sonnet 5 rejects
    `temperature` and thinks adaptively -- and grounding was the only stage
    without a generation cache. In a real run four paragraphs sent a
    byte-identical packet in two rounds, passed the first and failed the
    second. A repair that fixes three paragraphs while re-rolling the verdict
    on nineteen cannot converge, so the gate never settles however good the
    prose is.
    """

    knowledge = {
        "claims": [{"claim_id": "CL-1", "statement": "主張。", "evidence_step_ids": []}],
        "evidence_steps": [],
        "source_fragments": [],
    }

    class Flaky:
        model = "fake"

        def __init__(self):
            self.calls = 0

        def generate_json(self, _prompt, _payload, _schema, **_kwargs):
            self.calls += 1
            return {
                "schema_version": "matthew-exposition-grounding-result.v2",
                # Passes first, would flag on any later call.
                "unsupported_assertions": [] if self.calls == 1 else ["這段話"],
                "notes": "",
            }

    client = Flaky()
    cache = tmp_path / "grounding-cache"
    kwargs = dict(client=client, cache_dir=cache)

    first = check_paragraph_grounding("這段話有依據。", ["CL-1"], knowledge, **kwargs)
    second = check_paragraph_grounding("這段話有依據。", ["CL-1"], knowledge, **kwargs)
    assert first["exceeds_material"] is False
    assert second["exceeds_material"] is False
    assert client.calls == 1, "an unchanged paragraph must not be re-asked"

    # Different prose is a different question and is asked afresh.
    third = check_paragraph_grounding("這段話沒有依據。", ["CL-1"], knowledge, **kwargs)
    assert client.calls == 2
    assert third["exceeds_material"] is False or third["unsupported_assertions"]


def test_without_a_cache_directory_every_paragraph_is_asked(tmp_path):
    """Callers that pass no cache -- a diagnostic CLI, a test -- keep the
    behaviour they had."""

    knowledge = {
        "claims": [{"claim_id": "CL-1", "statement": "主張。", "evidence_step_ids": []}],
        "evidence_steps": [],
        "source_fragments": [],
    }

    class Counting:
        model = "fake"
        calls = 0

        def generate_json(self, _prompt, _payload, _schema, **_kwargs):
            type(self).calls += 1
            return {
                "schema_version": "matthew-exposition-grounding-result.v2",
                "unsupported_assertions": [],
                "notes": "",
            }

    client = Counting()
    check_paragraph_grounding("同一段。", ["CL-1"], knowledge, client=client)
    check_paragraph_grounding("同一段。", ["CL-1"], knowledge, client=client)
    assert Counting.calls == 2

def test_a_claim_carries_its_own_editorial_instruction():
    """The instruction map used to come from the contract's required steps.
    With those retired, a claim created from a base-manuscript sentence carries
    the instruction itself -- which is where it belonged: an instruction is
    about one piece of material, not about a checklist.
    """

    knowledge = {
        "claims": [{
            "claim_id": "CL1",
            "statement": "母本的承重推理。",
            "editorial_instruction": "保留完整推理，不要壓成一句結論。",
            "evidence_step_ids": [],
        }],
        "evidence_steps": [],
        "source_fragments": [],
    }
    material = build_paragraph_material(["CL1"], knowledge)
    assert material[0]["editorial_instruction"] == {
        "attribution": "editor",
        "statement": "保留完整推理，不要壓成一句結論。",
    }

