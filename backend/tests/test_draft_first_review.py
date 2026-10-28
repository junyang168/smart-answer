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


def test_scripture_blocks_may_match_cuv_but_inline_quotes_only_repeat_shown_verses():
    """#439: the rule demands the full verse; the professor only cited 約5:27.

    An inline quote may repeat a verse the article has shown in a block; a
    verse it has not shown stays sermon-only.
    """

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
            "又說「因為父怎樣在自己有生命，就賜給他兒子」。",
        ]
    )
    report = verbatim_quote_report(manuscript, packet, corpus)
    # Punctuation differs between editions; words do not.
    assert report["scripture_quotes"] == [
        "並且因為他是人子、就賜給他行審判的權柄。",
        "因為父怎樣在自己有生命……就賜給他行審判的權柄。",
    ]
    assert report["quotes_failing"] == [
        "因為父怎樣在自己有生命，就賜給他兒子",
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


def test_a_revision_is_exact_edits_that_answer_every_finding():
    """#439: asked for the whole essay in one JSON string, opus returned PLACEHOLDER, ized, zzz."""

    from backend.pipeline.draft_first_review_runner import merge_self_check, validate_revision

    before = "# 標題\n\n王教授說得很直接：代表神嘛！\n\n" + "正文。" * 200
    findings = [{"finding_id": "R0-01", "gate": "review"}, {"finding_id": "R0-02", "gate": "blind_read"}]
    fixed = [{"finding_id": "R0-01", "response": "fixed", "note": "", "source_quote": ""},
             {"finding_id": "R0-02", "response": "fixed", "note": "", "source_quote": ""}]
    edit = [{"old_str": "王教授說得很直接：代表神嘛！", "new_str": "比獸更高的人子代表神。"}]
    with pytest.raises(DraftReviewContractError, match="answered 0"):
        validate_revision(before, {"edits": edit, "responses": []}, findings)
    with pytest.raises(DraftReviewContractError, match="occurs 200 times"):
        validate_revision(before, {"edits": [{"old_str": "正文。", "new_str": ""}], "responses": fixed}, findings)
    with pytest.raises(DraftReviewContractError, match="occurs 0 times"):
        validate_revision(before, {"edits": [{"old_str": "不在稿裡", "new_str": "x"}], "responses": fixed}, findings)
    revised = validate_revision(before, {"edits": edit, "responses": fixed}, findings)
    assert "比獸更高的人子代表神。" in revised and "代表神嘛" not in revised
    previous = [{"item_id": "V:1", "status": "landed", "anchor": "代表神嘛", "reason": ""},
                {"item_id": "V:2", "status": "landed", "anchor": "正文。", "reason": ""}]
    merged = merge_self_check(previous, [{"item_id": "V:1", "status": "landed", "anchor": "人子代表神", "reason": ""}])
    assert [item["anchor"] for item in merged] == ["人子代表神", "正文。"]


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


def test_quote_check_holds_verse_words_not_glyphs_notes_or_skipped_verses():
    """#439 fresh draft, 2026-10-24: 7 of 12 failures were verbatim verses."""

    from backend.pipeline.draft_first_author_runner import verbatim_quote_report
    from backend.pipeline.draft_first_scripture import normalize

    sermon = ("「又有一獸，第二個獸，如熊。」夠了，跳過去。「此後我觀看，又有一獸，如豹。」"
              "「耶穌說：『你們中間誰有一隻羊，當安息日掉在坑裡，不把他抓住拉上來呢？』人比羊何等貴重呢！」")
    packet = {"source_originals": {"originals": [{"content": sermon}]}}
    verses = ("因為，凡要救自己生命〔生命：或作靈魂；下同〕的，必喪掉生命。"
              "我觀看，見有寶座設立，上頭坐着亙古常在者。說：人子必須被交在罪人手裏。")
    scripture = {"chapters": {}, "text": normalize(verses)}
    manuscript = "\n".join([
        "# 標題",
        "> 又有一獸，第二個獸，如熊。……此後我觀看，又有一獸，如豹。（但7:5-6）",
        "> 耶穌說：「你們中間誰有一隻羊，當安息日掉在坑裡，不把他抓住拉上來呢？」人比羊何等貴重呢！",
        "> 因為，凡要救自己生命的，必喪掉生命。（太16:25）",
        "> 我觀看，見有寶座設立，上頭坐著亙古常在者。（但7:9）",
        "> 說：『人子必須被交在罪人手裏。』（路24:7）",
        "路24:7 的「人子必須被交在罪人手裡」是轉引。",
        "> 又有一獸，第二個獸，如熊，口中啣著三根肋骨。",
    ])
    report = verbatim_quote_report(manuscript, packet, scripture)
    assert report["quotes_failing"] == ["又有一獸，第二個獸，如熊，口中啣著三根肋骨。"]


def test_quote_check_rejects_translator_notes_and_spoken_echoes_as_the_verse():
    """#439 round 2: 〔有古卷無我字〕 and 「使罪得赦，為多人。」 both reached final.md."""

    from backend.pipeline.draft_first_author_runner import verbatim_quote_report
    from backend.pipeline.draft_first_scripture import normalize

    sermon = "『因為這是我立約的血，為多人流出來，使罪得赦，為多人。耶穌就和門徒唱了詩，出來往橄欖山去。』"
    packet = {"source_originals": {"originals": [{"content": sermon}]}}
    verses = ["耶穌到了該撒利亞腓立比的境內，就問門徒說：「人說我〔有古卷無我字〕人子是誰？」",
              "因為這是我立約的血，為多人流出來，使罪得赦。"]
    scripture = {"chapters": {}, "text": "|".join(normalize(v) for v in verses),
                 "verses": [normalize(v) for v in verses]}
    manuscript = "\n".join([
        "# 標題",
        "> 耶穌到了該撒利亞腓立比的境內，就問門徒說：「人說我〔有古卷無我字〕人子是誰？」（太16:13）",
        "> 耶穌到了該撒利亞腓立比的境內，就問門徒說：「人說我人子是誰？」（太16:13）",
        "> 因為這是我立約的血，為多人流出來，使罪得赦，為多人。（太26:28）",
        "> 因為這是我立約的血，為多人流出來，使罪得赦。（太26:28）",
        "> 因為這是我立約的血，為多人流出來，使罪得赦，為多人。耶穌就和門徒唱了詩，出來往橄欖山去。",
    ])
    report = verbatim_quote_report(manuscript, packet, scripture)
    assert report["quotes_failing"] == [
        "耶穌到了該撒利亞腓立比的境內，就問門徒說：「人說我〔有古卷無我字〕人子是誰？」",
        "因為這是我立約的血，為多人流出來，使罪得赦，為多人。",
    ]


def test_an_extended_round_revises_the_stopped_draft_against_rechecked_programs(tmp_path):
    """Owner, 2026-10-24: one more round for #439, with the fixed quote check."""

    import pytest
    from backend.pipeline.draft_first_review_runner import DraftReviewContractError, continued_round
    from backend.pipeline.matthew_exposition_authoring import sha256_text

    manuscript = "# 標題\n> 因為這是我立約的血，為多人流出來，使罪得赦，為多人。（太26:28）\n"
    (tmp_path / "final.md").write_text(manuscript)
    (tmp_path / "final-self-check.json").write_text("[]")
    (tmp_path / "round-02").mkdir()
    outcome = {"round": 2, "manuscript_sha256": sha256_text(manuscript), "passed": False,
               "blind_read": {}, "blind_compare": None, "review": None,
               "backstage_language_report": {}, "self_check_problems": [], "blocking_findings": []}
    (tmp_path / "round-02" / "outcome.json").write_text(json.dumps(outcome))
    packet = {"source_originals": {"originals": [{"content": "因為這是我立約的血，為多人流出來，使罪得赦，為多人。"}]}}
    verse = "因為這是我立約的血為多人流出來使罪得赦"
    _, _, seeded = continued_round(tmp_path, packet, {"chapters": {}, "text": verse, "verses": [verse]})
    assert [(f["finding_id"], f["kind"]) for f in seeded["blocking_findings"]] == [("R2-01", "unverbatim_quote")]

    _, _, noted = continued_round(tmp_path, packet, {"chapters": {}, "text": verse, "verses": [verse]},
                                  [{"anchor": "為多人。（太26:28）", "summary": "多了一個字"}])
    assert [(f["finding_id"], f["gate"]) for f in noted["blocking_findings"]] == [("R2-01", "quote_check"), ("R2-02", "owner")]
    with pytest.raises(DraftReviewContractError):
        continued_round(tmp_path, packet, {"chapters": {}, "text": verse, "verses": [verse]},
                        [{"anchor": "not in the draft", "summary": "x"}])

    (tmp_path / "final.md").write_text(manuscript + "改過了。\n")
    with pytest.raises(DraftReviewContractError):
        continued_round(tmp_path, packet, {"chapters": {}, "text": verse, "verses": [verse]})


def test_author_gets_the_verses_of_the_essays_own_passages():
    """#439: 太9:1-8 is in scope, the sources cite only 9:7, and 9:6 had to be quoted."""

    from backend.pipeline.draft_first_scripture import cited_verses

    packet = {**_scripture_packet(), "scope": {"passage_refs": ["約5:26-27"]}}
    assert [v["reference"] for v in cited_verses(packet, chapter=_chapters)] == ["jhn-5-26-27", "jhn-5-27"]


def test_a_revision_gets_the_verses_its_draft_cites_within_taught_chapters():
    """#439 round 3: 太26:28 was required as a block but cited by no source."""

    from backend.pipeline.draft_first_scripture import cited_verses

    refs = [v["reference"] for v in cited_verses(
        _scripture_packet(), chapter=_chapters, manuscript="約5:26這裡講生命。羅8:1這裡講定罪。")]
    assert "jhn-5-26" in refs and not any(r.startswith("rom") for r in refs)
    # Owner, 2026-10-24: the scene may come from the verse's own preceding verses.
    scene = cited_verses(_scripture_packet(), chapter=_chapters, manuscript="約5:27這裡講審判。")
    assert {"reference": "jhn-5-24-26", "text": "因為父怎樣在自己有生命，就賜給他兒子也照樣在自己有生命，"} in scene


def test_author_and_reviewer_share_the_2026_10_24_reader_rules():
    """Owner, 2026-10-24: four rounds each found one more verse lacking a scene.

    Each rule must reach both sides: a rule only the author knows is revised
    back out by a reviewer who does not.
    """

    prompts = Path(__file__).resolve().parents[1] / "pipeline" / "prompts"
    author = (prompts / "draft_first_topic_author.md").read_text()
    reviewer = (prompts / "draft_first_two_way_review.md").read_text()
    shared = (prompts / "draft_first_two_way_rules.md").read_text()
    for text in (author, reviewer):
        assert "场景只在论证需要时" in text
        assert "完整的一句话" in text and "不留不成对的引号" in text
        assert "三个『因为』" in text
        assert "约三到五句" in text and "干巴巴" in text
        assert "后期修正" in text and "一般惯例" in text
    assert "首次触及的经文须先交代场景" not in reviewer
    assert "用一两句直接回答开头的问题" not in author
    assert "同一类问题一次列全" in reviewer and "覆盖压低它的全部原因" in reviewer
    assert "不应为此另写一句复述立场的话" in shared
    assert "落地是讲清楚，不是复述" in author


def test_concise_is_one_standard_and_the_reviewer_knows_what_wordy_looks_like():
    """Owner, 2026-10-24: 言之有物，言简意赅 -- concision scored 4/4 on a wordy draft."""

    prompts = Path(__file__).resolve().parents[1] / "pipeline" / "prompts"
    author = (prompts / "draft_first_topic_author.md").read_text()
    reviewer = (prompts / "draft_first_two_way_review.md").read_text()
    standard = [next(line for line in text.splitlines() if "成功的标准" in line).split("**")[1]
                for text in (author, reviewer)]
    assert standard[0] == standard[1] and "言之有物，言简意赅，重点突出，不枝不蔓" in standard[0]
    assert "不要原样嵌进正文当总结" in author
    for kind in ("引完经文紧接着", "相邻几段说两三遍", "路标句", "Claim 的原句嵌进正文"):
        assert kind in reviewer
    # The author knows the same four kinds the reviewer checks, as understanding, not rules.
    understanding = author.split("**什么是啰嗦**")[1].split("\n")[0]
    for kind in ("刚引完经文", "相邻几段换着说", "路标句", "Claim 的原句贴上"):
        assert kind in understanding
    assert "通读一遍" in understanding


def test_writing_rules_are_grouped_and_referred_to_by_name():
    """2026-10-24: rules had accreted by ruling; regrouped, so no 「第 9 条」 can go stale."""

    import re

    prompts = Path(__file__).resolve().parents[1] / "pipeline" / "prompts"
    author = (prompts / "draft_first_topic_author.md").read_text()
    for heading in ("## 一、忠实", "## 二、结构", "## 三、读者", "## 四、口吻"):
        assert heading in author
    assert author.index("你是一位神学编辑") < author.index("成功的标准")
    for name in ("draft_first_topic_author.md", "draft_first_revision.md",
                 "draft_first_two_way_review.md", "draft_first_two_way_rules.md"):
        assert not re.search(r"第 ?\d+ ?条", (prompts / name).read_text()), name
    source = (Path(__file__).resolve().parents[1] / "pipeline" / "draft_first_review_runner.py").read_text()
    assert not re.search(r"写作规则第 ?\d+ ?条", source)


def test_the_reviewer_is_told_what_every_scored_dimension_measures():
    """2026-10-24: dimensions were bare ids; readability sat at 7 on a moving
    reason for four rounds and concision scored 4/4 on a wordy draft."""

    root = Path(__file__).resolve().parents[1]
    reviewer = (root / "pipeline" / "prompts" / "draft_first_two_way_review.md").read_text()
    profile = json.loads((root / "config" / "editorial_quality_profiles"
                          / "WQ-theological-topic-essay-v1.json").read_text())
    rubric = reviewer.split("## 三、评分")[1].split("## 四、")[0]
    for dimension in profile["dimensions"]:
        assert f"- `{dimension['id']}`：" in rubric, dimension["id"]
    for heading in ("**忠实**", "**结构**", "**读者**", "**口吻**"):
        assert heading in rubric


def test_bindings_follow_the_self_check_and_the_reviewers_finds_without_a_model():
    """Owner, 2026-10-24: the sources were found during writing and review already."""

    from backend.pipeline.draft_first_source_binding import derive_bindings, verify_bindings, reader_paragraphs
    import backend.pipeline.draft_first_source_binding as binding

    # No model writes a span: the only model step chooses among program candidates.
    assert not hasattr(binding, "BINDING_SCHEMA")
    item = binding.MATCH_SCHEMA["schema"]["properties"]["matches"]["items"]["properties"]
    assert "excerpt" not in item and set(item) == {"paragraph_index", "candidate_index", "reason"}
    sermon = "人子這個稱號是耶穌自己用的，從來沒有別人這樣稱呼祂。我家裡有兩位 Dr. Wong，打電話來的人分不清是哪一位。"
    packet = {
        "source_originals": {"originals": [{"source_id": "S1", "content": sermon}]},
        "focal_viewpoints": [{"revision": {"viewpoint_revision_id": "R1"}, "member_claim_ids": ["C1"]}],
        "claims": [{"claim_id": "C1", "eligible_evidence_step_ids": ["E1"], "evidence_step_ids": ["E1", "E2"]}],
        "evidence_steps": [{"evidence_step_id": "E1", "source_fragment_ids": ["F1"]},
                           {"evidence_step_id": "E2", "source_fragment_ids": ["F2"]}],
        "source_fragments": [{"fragment_id": "F1", "source_id": "S1", "verbatim_excerpt": "人子這個稱號是耶穌自己用的，從來沒有別人這樣稱呼祂。"},
                             {"fragment_id": "F2", "source_id": "S1", "verbatim_excerpt": "我家裡有兩位 Dr. Wong，打電話來的人分不清是哪一位。"}],
        "observation_supports": [], "argument_routes": [],
    }
    manuscript = "# 標題\n\n「人子」是耶穌自己用的稱號，沒有別人這樣稱呼祂。\n\n他家裡有兩位 Dr. Wong，來電的人分不清是哪一位。\n\n第三段沒有出處。"
    checklist = [{"item_id": "V:R1", "kind": "viewpoint", "viewpoint_revision_id": "R1", "text": "t"}]
    self_check = [{"item_id": "V:R1", "status": "landed", "anchor": "沒有別人這樣稱呼祂", "reason": ""}]
    finds = [{"anchor": "來電的人分不清", "source_id": "S1", "excerpt": "我家裡有兩位 Dr. Wong，打電話來的人分不清是哪一位。"},
             {"anchor": "第三段沒有出處", "source_id": "S1", "excerpt": "這一句不在任何來源裡，所以不得被當成這一段的出處。"}]
    proposed = derive_bindings(manuscript, packet, self_check, checklist, finds)
    result = verify_bindings(proposed, paragraphs=reader_paragraphs(manuscript), packet=packet)
    spans = [[s["excerpt"][:6] for s in row["spans"]] for row in result["bindings"]]
    # The viewpoint brings only its eligible evidence (F1, not the withheld F2);
    # the reviewer's find binds the story; an invented excerpt is dropped.
    assert spans == [["人子這個稱號"], ["我家裡有兩位"], []]
    assert [row["basis"] for row in proposed["bindings"]] == [["V:R1"], ["review"], ["review"]]
    assert [f["code"] for f in result["findings"]] == ["excerpt_not_verbatim"]


def test_every_quoted_verse_is_bound_to_where_the_professor_reads_or_names_it():
    """Owner, 2026-10-24: an unbound verse can be taken for the article's own use of Scripture."""

    from backend.pipeline.draft_first_source_binding import scripture_bindings, verse_mentions

    packet = {"source_originals": {"originals": [
        {"source_id": "S1", "content": "我們來看第七章。第七章第三節：「有四個大獸從海中上來，形狀各有不同，頭一個像獅子。」好，跳過去。"},
        {"source_id": "S2", "content": "另外約翰福音 5:27 這一處沒有定冠詞，這是有文法依據的。"},
    ]}}
    read = scripture_bindings("> 有四個大獸從海中上來、形狀各有不同、頭一個像獅子。（但 7:3）", packet)
    assert [(s["source_id"], s["basis"], s["excerpt"]) for s in read] == [
        ("S1", "scripture_read", "有四個大獸從海中上來，形狀各有不同，頭一個像獅子")]
    cited = scripture_bindings("> 並且因為他是人子，就賜給他行審判的權柄。（約 5:27）", packet)
    assert [(s["source_id"], s["basis"]) for s in cited] == [("S2", "scripture_cited")]
    assert cited[0]["excerpt"] in packet["source_originals"]["originals"][1]["content"]
    assert scripture_bindings("> 我是好牧人。（約 10:11）", packet) == []
    assert any(slug.startswith("jhn-5-27") for _, slug in verse_mentions("見約翰福音 5:27。"))


def test_an_unlabelled_verse_block_is_found_by_its_cuv_text():
    """#488: the reference sat in the sentence before the block, so three cited verses were flagged."""

    from backend.pipeline.draft_first_source_binding import scripture_bindings

    packet = {"source_originals": {"originals": [
        {"source_id": "S1", "content": "再看林前10:33，保羅說，就好像我凡事都叫所有的人喜歡。"},
        {"source_id": "S2", "content": "林前10:1 我們的祖宗從前都在雲下。"},
    ]}}
    cited = scripture_bindings("> 就好像我凡事都叫眾人喜歡，不求自己的益處，只求眾人的益處，叫他們得救。", packet)
    assert [(s["source_id"], s["basis"]) for s in cited] == [("S1", "scripture_cited")]
    # 林前10:31: a chapter the sources cite, a verse none of them names.
    assert scripture_bindings("> 你們或吃或喝，無論做甚麼，都要為榮耀神而行。", packet) == []


def test_a_model_only_chooses_which_program_candidate_is_a_paragraphs_source():
    """Owner, 2026-10-24: every paragraph but the summary should be traceable."""

    from backend.pipeline.draft_first_source_binding import candidate_passages, confirm_matches, derive_bindings

    story = "那我的兒子，他有 Ph.D.，他也有 M.D.。所以我到他家裡去的時候，很麻煩。人家打電話來，要找 Dr. Wong。"
    packet = {"source_originals": {"originals": [
        {"source_id": "S1", "content": "今天講人子。" + story + "後來才知道要找的是癌症專科的教授。"},
        {"source_id": "S2", "content": "安息日門徒掐起麥穗來吃，法利賽人就說他們犯了安息日。"}]},
        "focal_viewpoints": [], "claims": [], "evidence_steps": [], "source_fragments": [],
        "observation_supports": [], "argument_routes": []}
    paragraph = {"paragraph_index": 0, "text": "他兒子有 Ph.D. 也有 M.D.，電話打來找 Dr. Wong，分不清是哪一位，原來要找癌症專科的教授。"}
    offered = candidate_passages(paragraph["text"], packet)
    assert offered[0]["source_id"] == "S1" and offered[0]["excerpt"] in packet["source_originals"]["originals"][0]["content"]

    class Chooser:
        backend, model = "fake", "fake"
        def generate_json(self, prompt, payload, schema):
            self.payload = json.loads(payload)
            return {"matches": [{"paragraph_index": 0, "candidate_index": 0, "reason": "同一個故事"},
                                {"paragraph_index": 0, "candidate_index": 9, "reason": "越界"}]}
    chooser = Chooser()
    chosen = confirm_matches(chooser, [paragraph], packet)
    assert [(c["source_id"], c["excerpt"] == offered[0]["excerpt"]) for c in chosen] == [("S1", True)]
    assert set(chooser.payload["paragraphs"][0]["candidates"][0]) == {"source_id", "excerpt"}

    manuscript = "耶穌隨即說：\n\n> 但要叫你們知道人子在地上有赦罪的權柄、就對癱子說、起來、拿你的褥子回家去罷。（太 9:6）"
    packet["source_originals"]["originals"].append({"source_id": "S3", "content": "第九章第六節：「但要叫你們知道，人子在地上有赦罪的權柄。就對癱子說：起來，拿你的褥子回家去罷！」這是命令，也是憑據。"})
    rows = derive_bindings(manuscript, packet, [], [])["bindings"]
    assert rows[0]["basis"] == ["lead_in"] and rows[0]["spans"] == rows[1]["spans"] != []


def test_a_passed_draft_reopens_only_for_the_owners_own_notes(tmp_path):
    import pytest
    from backend.pipeline.draft_first_review_runner import DraftReviewContractError, continued_round
    from backend.pipeline.matthew_exposition_authoring import sha256_text

    manuscript = "# 標題\n\n正文。兩者的先後也不加判斷。\n"
    (tmp_path / "final.md").write_text(manuscript)
    (tmp_path / "final-self-check.json").write_text("[]")
    (tmp_path / "round-02").mkdir()
    (tmp_path / "round-02" / "outcome.json").write_text(json.dumps(
        {"round": 2, "manuscript_sha256": sha256_text(manuscript), "passed": True, "blind_read": {},
         "blind_compare": None, "review": None, "backstage_language_report": {}, "self_check_problems": []}))
    packet = {"source_originals": {"originals": [{"content": "正文。"}]}}
    scripture = {"chapters": {}, "text": "", "verses": []}
    with pytest.raises(DraftReviewContractError, match="already passed"):
        continued_round(tmp_path, packet, scripture)
    _, _, seeded = continued_round(tmp_path, packet, scripture, [{"anchor": "兩者的先後也不加判斷。", "summary": "刪去"}])
    assert [f["gate"] for f in seeded["blocking_findings"]] == ["owner"]
    # The first run stopped at once: the seeded round still said passed.
    assert seeded["passed"] is False


def test_program_checks_catch_a_stray_character_and_an_unsourced_verse():
    """#451: both were caught by eye in a session on the 人子 essay, not by a gate."""

    from backend.pipeline.draft_first_program_checks import program_check_report, stray_characters
    from backend.pipeline.draft_first_review_runner import merge_blocking_findings

    # A Bengali nukta before the title is a stray; Hebrew points after a letter
    # are words; a zero-width space is never wanted.
    assert [s["codepoint"] for s in stray_characters("় # 標題\n\n像一位人子（כְּבַר אֱנָשׁ）")] == ["U+09BC"]
    assert [s["codepoint"] for s in stray_characters("人子​在地上")] == ["U+200B"]
    assert stray_characters("﻿# 標題") == []

    packet = {"source_originals": {"originals": [{"source_id": "S1", "content": "約翰福音 5:27 這裡人子沒有定冠詞。"}]}}
    manuscript = "# 標題\n\n> 並且因為他是人子，就賜給他行審判的權柄。（約 5:27）\n\n> 我是好牧人，好牧人為羊捨命。（約 10:11）\n"
    report = program_check_report(manuscript, packet)
    assert report["unsourced_verse_blocks"] == ["> 我是好牧人，好牧人為羊捨命。（約 10:11）"]
    findings = merge_blocking_findings(blind_read={}, blind_compare=None, review=None,
                                       quote_report={}, program_report=report)
    assert [(f["gate"], f["kind"]) for f in findings] == [("program_check", "unsourced_verse")]


def test_a_reviewer_is_calibrated_only_by_a_record_for_its_own_fingerprint(tmp_path):
    """#451/#494: wrap-up skips the reviewer call when this exact reviewer agreed."""

    import pytest
    from backend.pipeline.draft_first_calibration import (
        CalibrationRequired, record, require_calibrated, reviewer_fingerprint)

    path = tmp_path / "records.json"
    with pytest.raises(CalibrationRequired, match="not been calibrated"):
        require_calibrated("gpt-x", path)
    rows = [{"id": "K1", "result": "hit"}, {"id": "K5", "result": "manual"}, {"id": "K2", "result": "hit_nonblocking"}]
    record(reviewer_fingerprint("gpt-x"), "key", rows, "run-1", path)
    with pytest.raises(CalibrationRequired, match="K2"):
        require_calibrated("gpt-x", path)
    record(reviewer_fingerprint("gpt-x"), "key", rows[:2], "run-2", path)
    assert require_calibrated("gpt-x", path)["review_dir"] == "run-2"
    # Another model is another reviewer.
    with pytest.raises(CalibrationRequired):
        require_calibrated("gpt-y", path)
