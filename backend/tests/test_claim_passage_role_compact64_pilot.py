from backend.pipeline.claim_passage_role_compact64_pilot import compact_row


def test_compact_row_preserves_all_semantic_strings_in_order():
    row = {
        "claim_id": "C1", "statement": "what the passage means",
        "scripture_refs": ["太 1:1", "太 1:2"],
        "claim_type": "not_a_role_gate", "claim_content_sha256": "audit-only",
        "evidence_steps": [
            {
                "statement": "first step", "scripture_refs": ["太 1:1"],
                "content_sha256": "audit-only",
                "fragments": [
                    {"verbatim_excerpt": "first original", "fragment_id": "F1"},
                    {"verbatim_excerpt": "second original", "fragment_id": "F2"},
                ],
            },
            {
                "statement": "second step", "scripture_refs": [],
                "fragments": [{"verbatim_excerpt": "third original"}],
            },
        ],
    }

    assert compact_row(row) == {
        "claim_id": "C1", "statement": "what the passage means",
        "scripture_refs": ["太 1:1", "太 1:2"],
        "evidence_steps": [
            {"statement": "first step", "scripture_refs": ["太 1:1"],
             "verbatim_excerpts": ["first original", "second original"]},
            {"statement": "second step", "scripture_refs": [],
             "verbatim_excerpts": ["third original"]},
        ],
    }
