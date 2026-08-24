from copy import deepcopy

import pytest

from backend.api.canonical_repository.blind_semantic_graph import (
    build_blind_packet,
    canonicalize_component_key_delimiters,
    discovery_structure_sets,
    validate_discovery,
)


def _batch_packet():
    return {
        "claims": [
            {
                "claim_id": "CL-A",
                "pinned_claim_revision": 1,
                "claim_revision_sha256": "a" * 64,
                "source_id": "SRC-A",
                "statement": "观察甲，因此结论乙。",
                "attribution": "professor",
                "scripture_refs": ["Ref 1"],
                "active_full_viewpoint_id": "MUST-NOT-LEAK",
                "evidence": [
                    {
                        "evidence_step_id": "E-A",
                        "source_fragment_id": "F-A",
                        "source_id": "SRC-A",
                        "paragraph_key": "P1",
                        "media_time": None,
                        "evidence_statement": "观察支持结论",
                        "discourse_role": "free text",
                        "scripture_refs": ["Ref 1"],
                        "verbatim_excerpt": "观察甲，因此结论乙。",
                        "citation_status": "MUST-NOT-LEAK",
                    }
                ],
            }
        ],
        "registry_context": [{"canonical_text": "MUST-NOT-LEAK"}],
        "pending_candidates": [{"candidate": "MUST-NOT-LEAK"}],
    }


def _discovery(packet):
    return {
        "schema_version": "wang_blind_semantic_graph_discovery_v1",
        "input_packet_sha256": packet.packet_sha256,
        "claim_decompositions": [
            {
                "claim_id": "CL-A",
                "components": [
                    {
                        "component_id": "C01",
                        "start_char": 0,
                        "end_char": 3,
                        "exact_text": "观察甲",
                        "normalized_proposition": "甲被观察到",
                        "discourse_function": "premise",
                    },
                    {
                        "component_id": "C02",
                        "start_char": 6,
                        "end_char": 9,
                        "exact_text": "结论乙",
                        "normalized_proposition": "乙成立",
                        "discourse_function": "conclusion",
                    },
                ],
            }
        ],
        "proposition_nodes": [
            {
                "node_id": "N001",
                "canonical_proposition": "甲被观察到",
                "component_keys": ["CL-A#C01"],
                "semantic_kind": "textual_observation",
            },
            {
                "node_id": "N002",
                "canonical_proposition": "乙成立",
                "component_keys": ["CL-A#C02"],
                "semantic_kind": "interpretive_assertion",
            },
        ],
        "semantic_edges": [
            {
                "from_node_id": "N001",
                "to_node_id": "N002",
                "relation": "supports",
                "rationale": "Claim 明示因此关系",
            }
        ],
        "argument_complexes": [
            {
                "complex_id": "A01",
                "focal_node_ids": ["N002"],
                "member_node_ids": ["N001", "N002"],
                "structure_summary": "由甲推出乙",
            }
        ],
        "central_synthesis": [{"statement": "乙", "basis_node_ids": ["N002"]}],
        "unresolved_items": [],
    }


def test_blind_packet_drops_registry_and_non_evidence_fields():
    packet = build_blind_packet(_batch_packet())
    rendered = packet.model_dump_json()
    assert "MUST-NOT-LEAK" not in rendered
    assert len(packet.claims) == 1


def test_valid_discovery_round_trips():
    packet = build_blind_packet(_batch_packet())
    result = validate_discovery(packet, _discovery(packet))
    assert result.central_synthesis[0].basis_node_ids == ["N002"]


def test_discovery_rejects_dropped_component():
    packet = build_blind_packet(_batch_packet())
    raw = _discovery(packet)
    raw["proposition_nodes"][1]["component_keys"] = ["CL-A#C01"]
    with pytest.raises(ValueError, match="more than one proposition node"):
        validate_discovery(packet, raw)


def test_discovery_rejects_unanchored_component_text():
    packet = build_blind_packet(_batch_packet())
    raw = deepcopy(_discovery(packet))
    raw["claim_decompositions"][0]["components"][0]["exact_text"] = "编造"
    with pytest.raises(ValueError, match="does not match span"):
        validate_discovery(packet, raw)


def test_only_unambiguous_component_key_delimiter_is_normalized():
    packet = build_blind_packet(_batch_packet())
    raw = _discovery(packet)
    raw["proposition_nodes"][0]["component_keys"] = ["CL-A::C01"]
    normalized, count = canonicalize_component_key_delimiters(raw)
    assert count == 1
    assert normalized["proposition_nodes"][0]["component_keys"] == ["CL-A#C01"]
    assert raw["proposition_nodes"][0]["component_keys"] == ["CL-A::C01"]


def test_discovery_structure_sets_are_model_id_independent():
    packet = build_blind_packet(_batch_packet())
    result = validate_discovery(packet, _discovery(packet))
    structures = discovery_structure_sets(result)
    assert structures["relation_supports"] == {"CL-A|CL-A"}
    assert structures["focal_claim_ids"] == {"CL-A"}
    assert structures["central_basis_claim_ids"] == {"CL-A"}
