import hashlib

import pytest

from backend.pipeline.source_contract_cleanup import (
    BodyLocatorIndex,
    assert_claim_semantics_unchanged,
    changed_paths,
    build_source_contract_cleanup_plan,
    migrate_claim_occurrence_anchors,
    migrate_source_document,
    migrate_source_fragment,
    remap_claim_occurrence_source,
    remove_source_fragment_reference,
)
from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.pipeline.source_projection import project_script


SCRIPT = [
    {"index": 10, "text": "first professor row"},
    {"index": "subtitle-1", "type": "subtitle", "text": "# Editorial title"},
    {"index": 20, "text": "second professor row with exact evidence"},
]


def test_legacy_physical_locator_moves_past_editorial_row():
    index = BodyLocatorIndex(SCRIPT)
    migrated, resolution = migrate_source_fragment(
        {
            "fragment_id": "FR-1",
            "source_id": "SRC-1",
            "paragraph_key": "S0003",
            "source_segment_index": 20,
            "verbatim_excerpt": "exact evidence",
        },
        index,
        source_sha256="b" * 64,
    )

    assert resolution.proof == "physical_ordinal+source_segment_index"
    assert migrated["paragraph_key"] == "S0002"
    assert migrated["source_segment_index"] == 20
    assert migrated["anchor_state"] == "source_version_bound"
    assert migrated["paragraph_text_sha256"] == hashlib.sha256(
        SCRIPT[2]["text"].encode()
    ).hexdigest()


def test_attested_locator_resolves_one_proved_source_segment_conflict():
    script = [
        {"index": 1, "text": "first"},
        {"index": "subtitle-1", "type": "subtitle", "text": "# Editorial"},
        {"index": 236, "text": "same short answer"},
        {"index": 320, "text": "same short answer"},
    ]
    migrated, resolution = migrate_source_fragment(
        {
            "fragment_id": "FR-attested",
            "source_id": "SRC-1",
            "paragraph_key": "S0003",
            "source_segment_index": 236,
            "verbatim_excerpt": "same short answer",
        },
        BodyLocatorIndex(script),
        source_sha256="b" * 64,
        attested_locator="S0002",
    )

    assert resolution.proof == "attested_source_segment_index"
    assert migrated["paragraph_key"] == "S0002"
    assert migrated["source_segment_index"] == 236


def test_attested_locator_still_fails_when_source_segment_does_not_match():
    script = [
        {"index": 1, "text": "first"},
        {"index": "subtitle-1", "type": "subtitle", "text": "# Editorial"},
        {"index": 236, "text": "same short answer"},
        {"index": 320, "text": "same short answer"},
    ]
    migrated, resolution = migrate_source_fragment(
        {
            "fragment_id": "FR-attested",
            "paragraph_key": "S0003",
            "source_segment_index": 999,
            "verbatim_excerpt": "same short answer",
        },
        BodyLocatorIndex(script),
        source_sha256="b" * 64,
        attested_locator="S0002",
    )

    assert migrated is None
    assert resolution.status == "coordinate_conflict"


def test_unique_exact_text_is_the_only_fallback():
    index = BodyLocatorIndex(SCRIPT)
    resolved = index.resolve(paragraph_key="S9999", exact_text="exact evidence")
    assert (resolved.status, resolved.locator, resolved.proof) == (
        "resolved",
        "S0002",
        "unique_exact_text",
    )

    duplicate = BodyLocatorIndex(
        [{"index": 1, "text": "same"}, {"index": 2, "text": "same"}]
    ).resolve(paragraph_key="S9999", exact_text="same")
    assert duplicate.status == "ambiguous_exact_text"


def test_physical_locator_disambiguates_a_repeated_source_row_index():
    script = [
        {"index": 1, "text": "first exact evidence"},
        {"index": 1, "text": "second exact evidence"},
    ]
    resolution = BodyLocatorIndex(script).resolve(
        paragraph_key="S0002",
        source_segment_index=1,
        exact_text="second exact evidence",
    )
    assert resolution.status == "resolved"
    assert resolution.locator == "S0002"
    assert resolution.proof == (
        "body_locator+physical_ordinal+source_segment_index"
    )


def test_body_locator_disambiguates_repeated_text_after_editorial_row():
    script = [
        {"index": 1, "text": "same short answer"},
        {"index": "subtitle-1", "type": "subtitle", "text": "# Editorial title"},
        {"index": 2, "text": "same short answer"},
    ]
    resolution = BodyLocatorIndex(script).resolve(
        paragraph_key="S0002", exact_text="same short answer"
    )
    assert resolution.status == "resolved"
    assert resolution.locator == "S0002"
    assert resolution.proof == "body_locator"


def test_legacy_paragraph_key_can_name_the_source_segment_index():
    resolution = BodyLocatorIndex(SCRIPT).resolve(
        paragraph_key="20", exact_text="exact evidence"
    )
    assert resolution.status == "resolved"
    assert resolution.locator == "S0002"
    assert resolution.proof == "legacy_paragraph_source_segment_index"


def test_empty_excerpt_fails_closed():
    migrated, resolution = migrate_source_fragment(
        {"fragment_id": "FR-1", "verbatim_excerpt": ""},
        BodyLocatorIndex(SCRIPT),
        source_sha256="b" * 64,
    )
    assert migrated is None
    assert resolution.status == "no_excerpt"


def test_visual_fragment_requires_its_own_attested_locator_migration():
    migrated, resolution = migrate_source_fragment(
        {
            "fragment_id": "FR-V1",
            "paragraph_key": "S0001/V01",
            "source_modality": "visual",
            "verbatim_excerpt": "visible words",
        },
        BodyLocatorIndex(SCRIPT),
        source_sha256="b" * 64,
    )
    assert migrated is None
    assert resolution.status == "visual_requires_attested_locator_migration"


def test_source_document_uses_body_identity_not_file_identity():
    raw = b"physical bytes include editor structure"
    projection = project_script(SCRIPT)
    migrated = migrate_source_document(
        {"source_id": "SRC-1", "source_sha256": "old"},
        raw_source=raw,
        projection=projection,
        source_path="/canonical/sermon.json",
    )
    assert migrated["source_sha256"] == projection.body_sha256
    assert migrated["source_body_sha256"] == projection.body_sha256
    assert migrated["source_file_sha256"] == hashlib.sha256(raw).hexdigest()
    assert migrated["locator_space"] == "spoken_body_v1"
    assert migrated["source_path"] == "/canonical/sermon.json"


def test_claim_migration_changes_only_occurrence_locator():
    claim = {
        "claim_id": "CL-1",
        "statement": "reader-visible claim",
        "claim_type": "exegesis",
        "occurrences": [
            {
                "source_id": "SRC-1",
                "transcript_id": "sermon",
                "anchors": [
                    {
                        "paragraph_key": "S0003",
                        "proposed_highlight": {"text": "exact evidence"},
                    }
                ],
            }
        ],
    }
    migrated, findings = migrate_claim_occurrence_anchors(
        claim, BodyLocatorIndex(SCRIPT), source_id="SRC-1", transcript_id="sermon"
    )
    assert findings == [
        {
            "path": "occurrences[0].anchors[0].paragraph_key",
            "status": "changed",
            "proof": "physical_ordinal",
        }
    ]
    assert changed_paths(claim, migrated) == [
        "occurrences[0].anchors[0].paragraph_key"
    ]
    assert_claim_semantics_unchanged(claim, migrated)


def test_claim_migration_uses_source_fragments_to_resolve_short_answer_conflict():
    script = [
        {"index": 1, "text": "first"},
        {"index": "subtitle-1", "type": "subtitle", "text": "# Editorial"},
        {"index": 2, "text": "same short answer"},
        {"index": 3, "text": "same short answer"},
    ]
    claim = {
        "claim_id": "CL-1",
        "statement": "reader-visible claim",
        "occurrences": [
            {
                "source_id": "SRC-1",
                "transcript_id": "sermon",
                "anchors": [
                    {
                        "evidence_id": "E-1",
                        "paragraph_key": "S0003",
                        "proposed_highlight": {"text": "same short answer"},
                    }
                ],
            }
        ],
    }

    migrated, findings = migrate_claim_occurrence_anchors(
        claim,
        BodyLocatorIndex(script),
        source_id="SRC-1",
        transcript_id="sermon",
        anchor_locator_by_evidence_id={"E-1": "S0002"},
    )

    assert migrated["occurrences"][0]["anchors"][0]["paragraph_key"] == "S0002"
    assert findings == [
        {
            "path": "occurrences[0].anchors[0].paragraph_key",
            "status": "changed",
            "proof": "evidence_source_fragments",
        }
    ]
    assert_claim_semantics_unchanged(claim, migrated)


def test_claim_migration_rejects_non_coordinate_change():
    before = {"statement": "professor said this", "occurrences": []}
    after = {"statement": "editor changed this", "occurrences": []}
    with pytest.raises(ValueError, match="outside coordinate provenance"):
        assert_claim_semantics_unchanged(before, after)


def test_claim_source_alias_remap_changes_only_matching_occurrences():
    claim = {
        "claim_id": "CL-1",
        "statement": "reader-visible claim",
        "occurrences": [
            {"source_id": "SRC-OLD", "anchors": [{"paragraph_key": "S0003"}]},
            {"source_id": "SRC-OTHER", "anchors": []},
        ],
    }
    migrated = remap_claim_occurrence_source(
        claim, old_source_id="SRC-OLD", canonical_source_id="SRC-CANONICAL"
    )
    assert migrated["occurrences"][0]["source_id"] == "SRC-CANONICAL"
    assert migrated["occurrences"][1]["source_id"] == "SRC-OTHER"
    assert_claim_semantics_unchanged(claim, migrated)


def test_dedicated_plan_allows_only_coordinate_provenance_changes():
    before = {
        "claim_id": "CL-1",
        "statement": "reader-visible claim",
        "claim_type": "exegesis",
        "occurrences": [
            {
                "source_id": "SRC-1",
                "anchors": [{"paragraph_key": "S0003"}],
            }
        ],
    }
    after = {
        **before,
        "occurrences": [
            {
                "source_id": "SRC-1",
                "anchors": [{"paragraph_key": "S0002"}],
            }
        ],
    }
    current = {
        ("claims", "CL-1"): {
            "revision": 7,
            "content_sha256": record_content_sha(before),
            "payload": before,
        }
    }
    plan = build_source_contract_cleanup_plan(
        package_id="CLEANUP-1",
        current=current,
        replacements={("claims", "CL-1"): after},
    )
    assert plan.source_kind == "wkp368_source_contract_cleanup"
    assert plan.operations[0].before_revision == 7
    assert plan.operations[0].after_revision == 8
    assert plan.review_events == ()


def test_dedicated_plan_rejects_claim_text_or_human_settled_claim():
    before = {
        "claim_id": "CL-1",
        "statement": "original",
        "claim_type": "exegesis",
        "occurrences": [{"anchors": [{"paragraph_key": "S0003"}]}],
    }
    state = {
        "revision": 1,
        "content_sha256": record_content_sha(before),
        "payload": before,
    }
    changed_text = {**before, "statement": "rewritten"}
    with pytest.raises(ValueError, match="outside coordinate provenance"):
        build_source_contract_cleanup_plan(
            package_id="CLEANUP-1",
            current={("claims", "CL-1"): state},
            replacements={("claims", "CL-1"): changed_text},
        )

    moved = {
        **before,
        "occurrences": [{"anchors": [{"paragraph_key": "S0002"}]}],
    }
    with pytest.raises(ValueError, match="human-settled"):
        build_source_contract_cleanup_plan(
            package_id="CLEANUP-1",
            current={("claims", "CL-1"): state},
            replacements={("claims", "CL-1"): moved},
            human_settled_claim_ids={"CL-1"},
        )


def test_changed_paths_distinguishes_missing_field_from_explicit_null():
    assert changed_paths({}, {"source_visual_sha256": None}) == [
        "source_visual_sha256"
    ]


def test_placeholder_owner_cleanup_removes_only_fragment_pointer():
    before = {
        "evidence_step_id": "E-1",
        "statement": "candidate content remains unchanged",
        "source_fragment_id": "FR-MISSING",
        "support_eligibility": "withheld_missing_anchor",
        "review_status": "candidate",
        "visibility": "internal",
    }
    after = remove_source_fragment_reference(before, fragment_id="FR-MISSING")
    assert after == {
        "evidence_step_id": "E-1",
        "statement": "candidate content remains unchanged",
        "support_eligibility": "withheld_missing_anchor",
        "review_status": "candidate",
        "visibility": "internal",
    }

    current = {
        ("evidence_steps", "E-1"): {
            "revision": 2,
            "content_sha256": record_content_sha(before),
            "payload": before,
        }
    }
    plan = build_source_contract_cleanup_plan(
        package_id="CLEANUP-PLACEHOLDER",
        current=current,
        replacements={("evidence_steps", "E-1"): after},
    )
    assert plan.operations[0].collection == "evidence_steps"
    assert changed_paths(before, after) == ["source_fragment_id"]


def test_placeholder_owner_cleanup_rejects_content_change():
    before = {
        "question_id": "Q-1",
        "text": "original candidate question",
        "source_fragment_id": "FR-MISSING",
        "review_status": "candidate",
        "visibility": "internal",
    }
    changed = {**before, "text": "rewritten question"}
    state = {
        "revision": 1,
        "content_sha256": record_content_sha(before),
        "payload": before,
    }
    with pytest.raises(ValueError, match="outside the source-coordinate contract"):
        build_source_contract_cleanup_plan(
            package_id="CLEANUP-PLACEHOLDER",
            current={("questions", "Q-1"): state},
            replacements={("questions", "Q-1"): changed},
        )


def test_placeholder_owner_cleanup_cannot_rebind_or_touch_non_candidate():
    before = {
        "question_id": "Q-1",
        "text": "candidate question",
        "source_fragment_id": "FR-OLD",
        "review_status": "approved",
        "visibility": "internal",
    }
    state = {
        "revision": 1,
        "content_sha256": record_content_sha(before),
        "payload": before,
    }
    for changed in (
        {**before, "source_fragment_id": "FR-NEW"},
        {key: value for key, value in before.items() if key != "source_fragment_id"},
    ):
        with pytest.raises(ValueError, match="outside the source-coordinate contract"):
            build_source_contract_cleanup_plan(
                package_id="CLEANUP-PLACEHOLDER",
                current={("questions", "Q-1"): state},
                replacements={("questions", "Q-1"): changed},
            )
