import json
from pathlib import Path

import pytest

from backend.pipeline.draft_first_review_runner import (
    DraftReviewContractError,
    merge_blocking_findings,
    validate_review,
)


def _profile():
    return {
        "dimensions": [
            {"id": "source_and_exegesis", "weight": 15, "minimum": 12},
            {"id": "general_reader_readability", "weight": 10, "minimum": 8},
        ],
        "hard_failures": ["conclusion_reader_answer_broken"],
    }


def _review(score_a=13, score_b=9, failed=False, findings=()):
    return {
        "summary": "ok",
        "dimension_scores": [
            {"dimension_id": "source_and_exegesis", "score": score_a, "evidence": "「證據句。」"},
            {"dimension_id": "general_reader_readability", "score": score_b, "evidence": "「證據句。」"},
        ],
        "hard_failure_assessments": [
            {"failure_id": "conclusion_reader_answer_broken", "failed": failed, "evidence": ""}
        ],
        "findings": list(findings),
    }


MANUSCRIPT = "第一段。證據句。經文沒有說明這件事。結尾句。"




def test_review_verdict_derives_from_minimums_not_totals():
    verdict = validate_review(_review(), manuscript=MANUSCRIPT, profile=_profile())
    assert verdict == {"dimensions_below_minimum": [], "hard_failures_failed": []}

    finding = {
        "anchor": "結尾句。",
        "dimension_id": "general_reader_readability",
        "summary": "s",
        "required_change": "r",
        "blocking": True,
    }
    verdict = validate_review(
        _review(score_b=7, findings=[finding]), manuscript=MANUSCRIPT, profile=_profile()
    )
    assert verdict["dimensions_below_minimum"] == ["general_reader_readability"]


def test_failing_review_without_blocking_finding_is_rejected():
    with pytest.raises(DraftReviewContractError, match="blocking finding"):
        validate_review(_review(score_b=7), manuscript=MANUSCRIPT, profile=_profile())
    with pytest.raises(DraftReviewContractError, match="blocking finding"):
        validate_review(_review(failed=True), manuscript=MANUSCRIPT, profile=_profile())


def test_review_anchor_must_be_verbatim():
    finding = {
        "anchor": "稿裡沒有的錨",
        "dimension_id": "general_reader_readability",
        "summary": "s",
        "required_change": "r",
        "blocking": True,
    }
    with pytest.raises(DraftReviewContractError, match="anchor not verbatim"):
        validate_review(
            _review(findings=[finding]), manuscript=MANUSCRIPT, profile=_profile()
        )


def _finding(anchor, dimension, blocking=True, kind="quality", direction="quality", item_id="", source_quote="", upholds=""):
    return {"direction": direction, "kind": kind, "dimension_id": dimension, "item_id": item_id,
            "anchor": anchor, "source_quote": source_quote, "summary": "s", "required_change": "r",
            "blocking": blocking, "upholds": upholds}


def test_blocking_findings_merge_across_gates():
    blocking = merge_blocking_findings(
        blind_read={"comprehension_obstacles": []},
        blind_compare={
            "answer_matches_settled_positions": True,
            "modality_preserved": False,
            "mismatches": ["把更可能複述成了就是"],
        },
        review=_review(
            findings=[
                _finding("經文沒有說明這件事", "source_and_exegesis", kind="attribution_swap", direction="added"),
                _finding("結尾句。", "general_reader_readability"),
                _finding("第一段。", "source_and_exegesis", blocking=False),
            ]
        ),
        quote_report={"quotes_failing": ["改動過的引文"]},
        round_number=1,
    )
    assert [item["gate"] for item in blocking] == ["quote_check", "blind_read", "review", "review"]
    assert {item["kind"] for item in blocking} >= {"attribution_swap", "reader_path_broken"}
    # Every finding carries an id the author's response names.
    assert [item["finding_id"] for item in blocking] == ["R1-01", "R1-02", "R1-03", "R1-04"]


def test_clean_gates_produce_no_blocking_findings():
    blocking = merge_blocking_findings(
        blind_read={"comprehension_obstacles": []},
        blind_compare={"answer_matches_settled_positions": True, "modality_preserved": True, "mismatches": []},
        review=_review(),
        quote_report={"quotes_failing": []},
    )
    assert blocking == []


def test_provider_failure_does_not_pass_but_other_independent_checks_continue(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from backend.pipeline import draft_first_review_runner as runner
    from backend.pipeline.codex_subscription_client import CodexSubscriptionError
    called = []

    def call(client, prompt, payload, schema):
        called.append(prompt.name)
        if prompt.name == "draft_first_blind_read.md":
            return {"answer_in_one_sentence": "answer", "comprehension_obstacles": []}, {}
        return {"answer_matches_settled_positions": True, "modality_preserved": True, "mismatches": []}, {}

    def review(**kwargs):
        raise CodexSubscriptionError("429")

    monkeypatch.setattr(runner, "_call", call)
    packet = {"focal_viewpoints": [], "source_originals": {"originals": []}}
    clients = {name: SimpleNamespace(model=name) for name in ["blind_read", "blind_compare"]}
    clients["review"] = SimpleNamespace(model="gpt", review=review)
    outcome = runner.run_gates(manuscript=MANUSCRIPT, packet=packet, profile=_profile(),
                               clients=clients, round_dir=tmp_path)
    assert outcome["passed"] is False
    assert outcome["blocking_findings"] == []
    assert outcome["review_completed"] is False
    assert {e["stage"] for e in outcome["stage_errors"]} == {"review"}
    assert called == ["draft_first_blind_read.md", "draft_first_blind_compare.md"]


def test_unresolved_items_reach_every_payload():
    """#291: the structure's unresolved list must travel with the charter."""

    from backend.pipeline.draft_first_author_runner import structure_unresolved_items
    import inspect
    from backend.pipeline import draft_first_review_runner as review

    packet = {
        "structure": {"revision": {"unresolved_items": ["三种正面识别之间的关系未统一"]}}
    }
    assert structure_unresolved_items(packet) == ["三种正面识别之间的关系未统一"]
    assert structure_unresolved_items({}) == []

    assert '"unresolved_items": unresolved' in inspect.getsource(review.run_gates)
    assert '"unresolved_items.json": structure_unresolved_items' in inspect.getsource(review.run_two_way_review)
    main_source = inspect.getsource(review.main)
    assert '"unresolved_items": structure_unresolved_items(dict(packet))' in main_source


# ---- #293 hardening ----




def test_gates_carry_routes_fingerprints_and_inherited_scores():
    import inspect
    from backend.pipeline import draft_first_review_runner as review

    assert '"argument_routes.json": argument_route_charter' in inspect.getsource(review.run_two_way_review)
    src = inspect.getsource(review.run_gates)
    assert 'fingerprints["review"]' in src and "merge_delta_scores(baseline_review, review)" in src
    assert "baseline_review=baseline_review" in inspect.getsource(review.main)


def test_excerpt_overlap_flags_unrelated_spans():
    from backend.pipeline.draft_first_source_binding import (
        excerpt_overlap,
        verify_bindings,
        reader_paragraphs,
    )

    assert excerpt_overlap("教會建立在信仰告白上", "教會建立在信仰告白上") == 1.0
    assert excerpt_overlap("教會建立在信仰告白上", "門徒忘了帶餅彼此議論") < 0.1

    markdown = "# T\n\n教會建立在彼得的信仰告白上。\n"
    paragraphs = reader_paragraphs(markdown)
    packet = {
        "source_originals": {
            "originals": [
                {
                    "source_id": "SRC-1",
                    "content": "門徒渡到那邊去忘了帶餅於是彼此議論起來這件事。教會建立在彼得的信仰告白上這是清楚的教導。",
                }
            ]
        }
    }
    result = verify_bindings(
        {
            "bindings": [
                {
                    "paragraph_index": 0,
                    "spans": [
                        {"source_id": "SRC-1", "excerpt": "教會建立在彼得的信仰告白上這是清楚的教導。"},
                        {"source_id": "SRC-1", "excerpt": "門徒渡到那邊去忘了帶餅於是彼此議論起來這件事。"},
                    ],
                }
            ]
        },
        paragraphs=paragraphs,
        packet=packet,
    )
    spans = result["bindings"][0]["spans"]
    assert len(spans) == 2  # kept, not dropped
    assert [f["code"] for f in result["findings"]] == ["low_overlap"]


def test_projection_audit_floors():
    from backend.api.wang_article_reviews import _draft_first_annotated_markdown
    from backend.pipeline.draft_first_source_binding import reader_paragraphs

    packet = {
        "evidence_packet_sha256": "pk",
        "source_originals": {
            "originals": [
                {"source_id": "SRC-N", "source_type": "notes_manuscript",
                 "title": "母本", "content": "教會建立在信仰告白上的完整教導原文在此。"}
            ]
        },
        "source_documents": [
            {"source_id": "SRC-N", "source_type": "notes_manuscript", "title": "母本",
             "source_url": "/resources/notes/n1"}
        ],
    }
    markdown = "# T\n\n第一段。\n\n第二段。\n\n第三段。\n"
    paragraphs = reader_paragraphs(markdown)
    # only one of three bound -> coverage 33% -> below floor
    bindings_record = {
        "bindings": [
            {"paragraph_index": i, "paragraph_sha256": p["paragraph_sha256"],
             "spans": ([{"source_id": "SRC-N", "excerpt": "教會建立在信仰告白上的完整教導原文在此。"}] if i == 0 else [])}
            for i, p in enumerate(paragraphs)
        ],
        "findings": [{"code": "low_overlap", "paragraph_index": 0, "overlap": 0.05}],
    }
    _, _, projection, _ = _draft_first_annotated_markdown(markdown, bindings_record, packet)
    codes = {f["code"] for f in projection["findings"]}
    assert "binding_coverage_below_floor" in codes
    assert "binding_low_overlap" in codes
    assert projection["passed"] is False


# ---- #302: Codex round-two gaps ----


def test_route_charter_carries_per_source_attested_steps():
    from backend.pipeline.draft_first_author_runner import argument_route_charter

    packet = {
        "argument_routes": [
            {
                "revision": {
                    "argument_route_revision_id": "ARR-1",
                    "route_label": "L",
                    "validated_against_conclusion_viewpoint_revision_id": "CVR-1",
                    "ordered_inference_nodes": [
                        {"route_step_key": "P1", "role": "observation", "normalized_proposition": "甲"},
                        {"route_step_key": "P2", "role": "inference", "normalized_proposition": "乙"},
                    ],
                },
                "attestations": [
                    {
                        "source_id": "SRC-A",
                        "completeness": "full",
                        "step_bindings": [
                            {"route_step_key": "P1", "attestation_status": "attested"},
                            {"route_step_key": "P2", "attestation_status": "attested"},
                        ],
                    },
                    {
                        "source_id": "SRC-B",
                        "completeness": "partial",
                        "step_bindings": [
                            {"route_step_key": "P1", "attestation_status": "attested"}
                        ],
                    },
                ],
            }
        ]
    }
    [route] = argument_route_charter(packet)
    assert route["steps"][0]["step_key"] == "P1"
    attestations = {a["source_id"]: a for a in route["source_attestations"]}
    assert attestations["SRC-A"]["completeness"] == "full"
    assert attestations["SRC-A"]["attested_step_keys"] == ["P1", "P2"]
    assert attestations["SRC-B"]["attested_step_keys"] == ["P1"]


def test_delta_scores_inherit_unless_affected():
    from backend.pipeline.draft_first_review_runner import merge_delta_scores

    baseline = {
        "dimension_scores": [
            {"dimension_id": "a", "score": 10, "evidence": "base-a"},
            {"dimension_id": "b", "score": 9, "evidence": "base-b"},
            {"dimension_id": "c", "score": 8, "evidence": "base-c"},
        ],
        "findings": [
            {"dimension_id": "c", "blocking": True, "anchor": "x", "summary": "", "required_change": ""}
        ],
    }
    delta = {
        "dimension_scores": [
            {"dimension_id": "a", "score": 4, "evidence": "delta rerolled"},
            {"dimension_id": "b", "score": 7, "evidence": "delta finding here"},
            {"dimension_id": "c", "score": 9, "evidence": "revised and rescored"},
        ],
        "findings": [
            {"dimension_id": "b", "blocking": True, "anchor": "y", "summary": "", "required_change": ""}
        ],
    }
    merged, provenance = merge_delta_scores(baseline, delta)
    by_id = {s["dimension_id"]: s["score"] for s in merged["dimension_scores"]}
    assert by_id == {"a": 10, "b": 7, "c": 9}  # a inherited; b delta-finding; c revised-last-round
    assert provenance == {
        "rescored_dimensions": ["b", "c"],
        "inherited_dimensions": ["a"],
    }


def test_gate_outputs_are_sha_bound():
    import inspect
    from backend.pipeline import draft_first_review_runner as review

    src = inspect.getsource(review.run_gates)
    assert src.count('["result_sha256"]') == 3
    assert '"outcome_sha256"' in inspect.getsource(review.main)


def test_packet_chain_requires_three_way_agreement():
    from backend.api.wang_article_reviews import _draft_first_packet_chain_ok

    packet = {"evidence_packet_sha256": "PK"}
    ok = _draft_first_packet_chain_ok(
        {"evidence_packet_sha256": "PK"},
        {"evidence_packet_sha256": "PK"},
        packet,
        workflow={"evidence_packet_sha256": "PK"},
    )
    assert ok is True
    assert (
        _draft_first_packet_chain_ok(
            {"evidence_packet_sha256": "PK"},
            {"evidence_packet_sha256": "OTHER"},
            packet,
            workflow={"evidence_packet_sha256": "PK"},
        )
        is False
    )


def test_author_payload_carries_routes_and_layered_license():
    """#304: routes reach the author; alignment licenses in two layers."""

    import inspect
    from pathlib import Path
    from backend.pipeline import draft_first_author_runner as author

    assert '"argument_routes": argument_route_charter(packet)' in inspect.getsource(author.main)
    prompts = Path(author.__file__).parent / "prompts"
    rules = (prompts / "draft_first_topic_author.md").read_text()
    assert "路线是骨架不是上限" in rules
    reviewer = (prompts / "draft_first_two_way_review.md").read_text()
    assert "不得再叠加一次否定" in reviewer


def test_backstage_language_flags_production_words_and_sermon_location_tags():
    from backend.pipeline.draft_first_author_runner import backstage_language_report

    # Phrasings taken from the #439 drafts the owner and the blind review rejected.
    manuscript = "\n".join(
        [
            "# 標題",
            "這兩種說法，材料裡各自站立。母本列了幾處平行經文。",
            "講提多書時他說「在聖經裡面沒有任何一個人來叫耶穌叫The Son of Man」。",
            "王守仁教授講馬太福音十六章28節時說，人子是耶穌自指。",
        ]
    )
    report = backstage_language_report(manuscript)
    assert {"材料", "各自站立", "母本"} <= set(report["backstage_terms"])
    assert len(report["source_location_tags"]) == 2


def test_backstage_language_exempts_footnotes_quotes_and_one_named_speaker():
    from backend.pipeline.draft_first_author_runner import backstage_language_report

    manuscript = "\n".join(
        [
            "# 標題",
            "耶穌講這比喻時，門徒並不明白。王教授說得很直接：「這個人子是耶穌基督。」",
            "> 我看見天開了、人子站在神的右邊。",
            "這個稱號只由耶穌說出。[^1]",
            "[^1]: 編者注：另有講義明列司提反為例外；本文保留兩種說法，不推定後期修正。",
        ]
    )
    report = backstage_language_report(manuscript)
    assert report == {
        "backstage_terms": [],
        "source_location_tags": [],
        "professor_mentions": 1,
        "professor_names": ["王教授說得很直接："],
    }


def test_backstage_findings_block_after_quote_check():
    blocking = merge_blocking_findings(
        blind_read={"comprehension_obstacles": []},
        blind_compare=None,
        review=_review(findings=[]),
        quote_report={"quotes_failing": []},
        backstage_report={
            "backstage_terms": ["母本"],
            "source_location_tags": ["講提多書時他"],
            "professor_mentions": 9,
        },
    )
    assert [(item["gate"], item["kind"]) for item in blocking] == [
        ("backstage_check", "backstage_language"),
        ("backstage_check", "source_location_tag"),
    ]


def _scripture_packet():
    return {
        "source_originals": {
            "originals": [
                {"content": "約5:27這裡人子沒有定冠詞。看哪、我們上耶路撒冷去、人子要被交給祭司長和文士。"}
            ]
        }
    }


def _chapters(book, number):
    assert (book, number) == ("JHN", 5)
    return {
        "26": "因為父怎樣在自己有生命，就賜給他兒子也照樣在自己有生命，",
        "27": "並且因為他是人子，就賜給他行審判的權柄。",
    }


def test_scripture_blocks_may_match_cuv_but_inline_quotes_stay_sermon_only():
    """#439: the rule demands the full verse; the professor only cited 約5:27."""

    from backend.pipeline.draft_first_author_runner import verbatim_quote_report
    from backend.pipeline.draft_first_scripture import cited_verses, scripture_corpus

    packet = _scripture_packet()
    corpus = scripture_corpus(packet, chapter=_chapters)
    manuscript = "\n".join(
        [
            "# 標題",
            "> 並且因為他是人子、就賜給他行審判的權柄。",
            "> 因為父怎樣在自己有生命……就賜給他行審判的權柄。",
            "> 看哪、我們上耶路撒冷去、人子要被交給祭司長和文士。",
            "> 並且因為祂是人子，就賜給祂審判的權柄。",
            "文章說「並且因為他是人子，就賜給他行審判的權柄」。",
        ]
    )
    report = verbatim_quote_report(manuscript, packet, corpus)
    # Punctuation differs between editions; words do not.
    assert report["scripture_quotes"] == [
        "並且因為他是人子、就賜給他行審判的權柄。",
        "因為父怎樣在自己有生命……就賜給他行審判的權柄。",
    ]
    assert report["quotes_failing"] == [
        "並且因為他是人子，就賜給他行審判的權柄",
        "並且因為祂是人子，就賜給祂審判的權柄。",
    ]
    assert list(report["scripture_chapters"]) == ["JHN/5"]
    assert cited_verses(packet, chapter=_chapters) == [
        {"reference": "jhn-5-27", "text": "並且因為他是人子，就賜給他行審判的權柄。"}
    ]


def test_viewpoint_charter_marks_viewpoints_without_a_full_route():
    from backend.pipeline.draft_first_author_runner import viewpoint_charter

    packet = {
        "focal_viewpoints": [
            {"structure_role": "central_claim", "revision": {"viewpoint_revision_id": "R1"}},
            {"structure_role": "qualification", "revision": {"viewpoint_revision_id": "R2"}},
        ],
        "argument_routes": [
            {"revision": {"validated_against_conclusion_viewpoint_revision_id": "R1"}, "full_attestation_count": 0},
            {"revision": {"validated_against_conclusion_viewpoint_revision_id": "R2"}, "full_attestation_count": 1},
        ],
    }
    assert [item["has_full_route"] for item in viewpoint_charter(packet)] == [False, True]


def test_placeholder_revision_is_rejected():
    """#439: opus returned the H1 plus PLACEHOLDER and no dispositions."""

    from backend.pipeline.draft_first_review_runner import validate_revision

    before = "# 標題\n\n" + "正文。" * 200
    findings = [{"finding_id": "R0-01", "gate": "review"}, {"finding_id": "R0-02", "gate": "blind_read"}]
    fixed = [{"finding_id": "R0-01", "response": "fixed", "note": "", "source_quote": ""},
             {"finding_id": "R0-02", "response": "fixed", "note": "", "source_quote": ""}]
    with pytest.raises(DraftReviewContractError, match="answered 0"):
        validate_revision(before, {"manuscript_markdown": "# 標題\nPLACEHOLDER", "responses": []}, findings)
    with pytest.raises(DraftReviewContractError, match="shrank"):
        validate_revision(before, {"manuscript_markdown": "# 標題\nPLACEHOLDER", "responses": fixed}, findings)
    revised = before.replace("正文。", "正文！", 1)
    assert validate_revision(before, {"manuscript_markdown": revised, "responses": fixed}, findings) == revised


def test_the_body_names_the_professor_at_most_twice():
    from backend.pipeline.draft_first_author_runner import backstage_language_report

    # Run 3 named him before nearly every quote: nine times, each allowed by
    # 「引语前交代一次说话人，同段不重复」. 「醫學院的教授」 is somebody else.
    manuscript = "\n".join(
        [
            "# 標題",
            "王教授說：「我們中文沒有定冠詞。」他的兒子是醫學院的教授。",
            "王教授說得很直接：「代表神嘛！」",
            "王教授這樣總結：「我就是神。」",
        ]
    )
    report = backstage_language_report(manuscript)
    assert len(report["professor_names"]) == 3
    blocking = merge_blocking_findings(
        blind_read={"comprehension_obstacles": []},
        blind_compare=None,
        review=_review(findings=[]),
        quote_report={"quotes_failing": []},
        backstage_report=report,
    )
    # The first naming stays; every later one is a finding the revision fixes.
    assert [item["kind"] for item in blocking] == ["professor_named_again"] * 2
    assert blocking[0]["anchor"] == "王教授說得很直接："
    two = backstage_language_report("\n".join(manuscript.splitlines()[:3]))
    assert not merge_blocking_findings(
        blind_read={"comprehension_obstacles": []}, blind_compare=None,
        review=_review(findings=[]), quote_report={"quotes_failing": []}, backstage_report=two)


def test_viewpoint_charter_carries_observations_and_the_proposition_parts():
    from backend.pipeline.draft_first_author_runner import viewpoint_charter

    packet = {
        "focal_viewpoints": [
            {"structure_role": "premise", "revision": {"viewpoint_revision_id": "R1", "core_proposition": "中文无法直接译出定冠词。",
             "proposition_signature": {"subject": "中文", "predicate": "能够直接译出定冠词", "object": "人子称谓",
                                       "polarity": "denied", "modality": "断言"}}},
            {"structure_role": "premise", "revision": {"viewpoint_revision_id": "R2"}},
        ],
        "observation_supports": [
            {"validated_against_viewpoint_revision_id": "R1", "source_id": "S1", "observation_summary": "中文没有定冠词。",
             "supports": "直接支持", "support_scope_note": "未逐例列举。", "evidence_bindings": [{"claim_id": "C"}]},
        ],
    }
    first, second = viewpoint_charter(packet)
    # 「中文沒有辦法翻」 was flagged when only the negated sentence and
    # polarity=denied were sent; the parts show where the negation lives.
    assert (first["subject"], first["predicate"], first["polarity"]) == ("中文", "能够直接译出定冠词", "denied")
    assert first["observation_supports"] == [{"source_id": "S1", "observation_summary": "中文没有定冠词。",
                                              "supports": "直接支持", "support_scope_note": "未逐例列举。"}]
    assert second["observation_supports"] == []


def test_prompts_state_one_naming_observations_and_the_sign_of_polarity():
    prompts = Path(__file__).resolve().parents[1] / "pipeline" / "prompts"
    author = (prompts / "draft_first_topic_author.md").read_text()
    assert "正文点名教授至多两次" in author and "没有路线不等于没有依据" in author
    assert "同段不重复" not in author and "第一次逐字引用" not in author
    # Owner, 2026-10-24: named only for his own life; exegesis and theology direct.
    for name in ("draft_first_topic_author.md", "draft_first_revision.md", "draft_first_two_way_review.md"):
        text = (prompts / name).read_text()
        assert "只有讲到教授本人的生活与经历" in text and "第一次逐字引用" not in text
    reviewer = (prompts / "draft_first_two_way_review.md").read_text()
    assert "不得再叠加一次否定" in reviewer and "polarity 为 denied 的立场不得被正面断言" not in reviewer
    assert "support_scope_note" in reviewer


def test_quote_check_reads_verse_labels_nested_marks_and_block_speech_as_the_reader_does():
    """#439: 24 of 35 failures on run 3 were verse references inside the block."""

    from backend.pipeline.draft_first_author_runner import verbatim_quote_report
    from backend.pipeline.draft_first_scripture import normalize

    packet = {"source_originals": {"originals": [{"content": "你看耶穌每次講「人子」、「Son of Man」都加上一個定冠詞，「the Son of Man」。"}]}}
    verse = "耶穌到了該撒利亞腓立比的境內，就問門徒說：人說我人子是誰"
    scripture = {"chapters": {}, "text": normalize(verse)}
    manuscript = "\n".join([
        "# 標題",
        "他說：「你看耶穌每次講『人子』、『Son of Man』都加上一個定冠詞，『the Son of Man』。」",
        "> 耶穌到了該撒利亞腓立比的境內，就問門徒說：「人說我人子是誰？」（太 16:13）",
        "他說：「我就是但以理書七章所說的那一位」。",
    ])
    report = verbatim_quote_report(manuscript, packet, scripture)
    assert report["quotes_failing"] == ["我就是但以理書七章所說的那一位"]
    assert report["scripture_quotes"] == ["耶穌到了該撒利亞腓立比的境內，就問門徒說：「人說我人子是誰？」"]


def test_author_and_reviewer_hold_one_plain_style_standard():
    """Owner, 2026-10-24: 「代表神嘛」 is too colloquial; plain, not inspirational preaching."""

    prompts = Path(__file__).resolve().parents[1] / "pipeline" / "prompts"
    standards = {name: next(line for line in (prompts / name).read_text().splitlines() if "成功的标准" in line)
                 for name in ("draft_first_topic_author.md", "draft_first_two_way_review.md")}
    author, reviewer = standards.values()
    assert author.split("**")[1] == reviewer.split("**")[1]
    assert "不是讲台上鼓舞人心的讲道" in author and "讲台上的资深神学教授" not in author
