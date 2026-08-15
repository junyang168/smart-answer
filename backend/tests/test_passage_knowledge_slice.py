from backend.pipeline.passage_knowledge_slice import (
    Passage,
    build_passage_slice,
    reference_overlaps,
)


def test_reference_overlap_handles_chinese_and_english_ranges() -> None:
    passage = Passage("Matt", 16, 21, 23)
    assert reference_overlaps("太16:16-23", passage)
    assert reference_overlaps("Matt.16:22", passage)
    assert reference_overlaps("马太福音16:21", passage)
    assert reference_overlaps("馬太福音 16:21–23", passage)
    assert reference_overlaps("Matthew 16:13-23", passage)
    assert reference_overlaps("Mt. 16:21", passage)
    assert not reference_overlaps("太16:24-27", passage)
    assert not reference_overlaps("太17:21-23", passage)


def test_reference_overlap_treats_full_osis_range_as_one_span() -> None:
    reference = "Matt.16.13-Matt.16.20"

    assert reference_overlaps(reference, Passage("Matt", 16, 17, 17))
    assert not reference_overlaps(reference, Passage("Matt", 16, 21, 21))
    assert reference_overlaps(
        "Matt.16.28-Matt.17.2",
        Passage("Matt", 17, 1, 1),
    )
    assert reference_overlaps(
        "Matthew 16:28-17:2",
        Passage("Matt", 17, 1, 1),
    )


def test_passage_slice_keeps_full_chinese_book_name_claims() -> None:
    claims = [
        {
            "claim_id": f"CL-{index}",
            "scripture_refs": [reference],
            "evidence_step_ids": [f"E-{index}"],
        }
        for index, reference in enumerate(
            [
                "马太福音16:19",
                "馬太福音 16:18",
                "Matthew 16:13-20",
                "Matt.16.13-Matt.16.20",
            ],
            start=1,
        )
    ]
    package = {
        "claims": claims,
        "evidence_steps": [
            {
                "evidence_step_id": f"E-{index}",
                "produced_claim_ids": [f"CL-{index}"],
                "support_eligibility": "eligible",
            }
            for index in range(1, 5)
        ],
    }

    result = build_passage_slice(package, Passage("Matt", 16, 13, 20))

    assert [row["claim_id"] for row in result["claims"]] == [
        "CL-1",
        "CL-2",
        "CL-3",
        "CL-4",
    ]


def test_passage_slice_keeps_relations_with_real_and_legacy_endpoint_fields() -> None:
    package = {
        "claims": [
            {"claim_id": "CL-1", "scripture_refs": ["马太福音16:18"]},
            {"claim_id": "CL-2", "scripture_refs": ["马太福音16:18"]},
        ],
        "claim_relations": [
            {
                "claim_relation_id": "CR-REAL",
                "source_id": "CL-1",
                "target_id": "CL-2",
                "relation_type": "contrasts",
            },
            {
                "claim_relation_id": "CR-EXPLICIT",
                "source_claim_id": "CL-1",
                "target_claim_id": "CL-2",
                "relation_type": "extends",
            },
            {
                "claim_relation_id": "CR-LEGACY",
                "from_id": "CL-2",
                "to_id": "CL-1",
                "relation_type": "duplicate",
            },
            {
                "claim_relation_id": "CR-OUTSIDE",
                "source_id": "CL-1",
                "target_id": "CL-OUTSIDE",
                "relation_type": "contrasts",
            },
        ],
    }

    result = build_passage_slice(package, Passage("Matt", 16, 13, 20))

    assert [row["claim_relation_id"] for row in result["claim_relations"]] == [
        "CR-REAL",
        "CR-EXPLICIT",
        "CR-LEGACY",
    ]


def test_direct_scope_width_boundary_for_matthew_16_13_20() -> None:
    package = {
        "claims": [
            {
                "claim_id": "CL-WIDTH-24",
                "title": "二十四節範圍",
                "scripture_refs": ["馬太福音16:1-24"],
            },
            {
                "claim_id": "CL-WIDTH-25",
                "title": "二十五節範圍",
                "scripture_refs": ["馬太福音16:1-25"],
            },
        ]
    }

    result = build_passage_slice(package, Passage("Matt", 16, 13, 20))

    assert [row["claim_id"] for row in result["claims"]] == ["CL-WIDTH-24"]
    assert [row["claim_id"] for row in result["contextual_claim_leads"]] == [
        "CL-WIDTH-25"
    ]


def test_slice_reports_unparsed_matthew_references_without_blocking() -> None:
    package = {
        "claims": [
            {"claim_id": "CL-PARSED", "scripture_refs": ["馬太福音16:19"]},
            {
                "claim_id": "CL-MATTHEW-UNKNOWN",
                "scripture_refs": ["马太福音第十六章十九节"],
            },
            {"claim_id": "CL-OTHER-BOOK", "scripture_refs": ["彼后2:1"]},
        ]
    }

    result = build_passage_slice(package, Passage("Matt", 16, 13, 20))

    assert {
        key: result["summary"][key]
        for key in (
            "claim_reference_total",
            "parsed_claim_reference_total",
            "unparsed_claim_reference_total",
            "unparsed_matthew_reference_total",
        )
    } == {
        "claim_reference_total": 3,
        "parsed_claim_reference_total": 1,
        "unparsed_claim_reference_total": 2,
        "unparsed_matthew_reference_total": 1,
    }
    assert [row["claim_id"] for row in result["claims"]] == ["CL-PARSED"]


def test_passage_slice_keeps_only_transitive_provenance() -> None:
    package = {
        "package_id": "PKG",
        "source_documents": [
            {"source_id": "SRC-1"},
            {"source_id": "SRC-2"},
        ],
        "source_fragments": [
            {"fragment_id": "FR-1", "source_id": "SRC-1"},
            {"fragment_id": "FR-2", "source_id": "SRC-2"},
            {"fragment_id": "FR-O", "source_id": "SRC-1"},
        ],
        "observations": [
            {"observation_id": "OBS-1", "scripture_refs": ["太16:22"], "source_fragment_ids": ["FR-O"]}
        ],
        "claims": [
            {"claim_id": "CL-1", "scripture_refs": ["太16:16-23"], "evidence_step_ids": ["E-1"]},
            {"claim_id": "CL-2", "scripture_refs": ["太16:24"], "evidence_step_ids": ["E-2"]},
            {"claim_id": "CL-WIDE", "title": "宽范围背景", "scripture_refs": ["太16:1-23"], "evidence_step_ids": []},
        ],
        "evidence_steps": [
            {"evidence_step_id": "E-1", "produced_claim_ids": ["CL-1", "CL-2"], "source_fragment_ids": ["FR-1"], "support_eligibility": "eligible"},
            {"evidence_step_id": "E-2", "produced_claim_ids": ["CL-2"], "source_fragment_ids": ["FR-2"], "support_eligibility": "eligible"},
        ],
    }

    result = build_passage_slice(package, Passage("Matt", 16, 21, 23))

    assert [row["claim_id"] for row in result["claims"]] == ["CL-1"]
    assert [row["claim_id"] for row in result["contextual_claim_leads"]] == ["CL-WIDE"]
    assert [row["evidence_step_id"] for row in result["evidence_steps"]] == ["E-1"]
    assert result["evidence_steps"][0]["produced_claim_ids"] == ["CL-1"]
    assert {row["fragment_id"] for row in result["source_fragments"]} == {"FR-1", "FR-O"}
    assert [row["source_id"] for row in result["source_documents"]] == ["SRC-1"]
    assert result["passage_slice"] == {
        "passage": "Matt16:21–23",
        "selection_policy": "structured_scripture_reference_overlap",
        "covered_verses": [21, 22, 23],
        "missing_verses": [],
        "requires_model_extraction": False,
    }
