from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.pipeline.matthew16_atomic_identity_coverage_runner import (
    AtomicIdentityCoverageAssessment,
    validate_assessment_binding,
)


def _assessment(ids: list[str]) -> AtomicIdentityCoverageAssessment:
    return AtomicIdentityCoverageAssessment(
        atomic_execution_artifact_sha256="a" * 64,
        target_proposition="太 16:18 的「磐石」不指彼得本人",
        decisions=[
            {
                "proposition_unit_id": unit_id,
                "disposition": "equivalent",
                "rationale": "同一真值条件",
            }
            for unit_id in ids
        ],
    )


def test_identity_coverage_canonicalizes_order_but_rejects_duplicate_units():
    assessment = _assessment(["VPU-2", "VPU-1"])
    assert [item.proposition_unit_id for item in assessment.decisions] == ["VPU-1", "VPU-2"]

    with pytest.raises(ValidationError, match="sorted and unique"):
        _assessment(["VPU-1", "VPU-1"])


def test_identity_coverage_requires_exact_execution_universe():
    assessment = _assessment(["VPU-1", "VPU-2"])
    validate_assessment_binding(
        role="proposal",
        assessment=assessment,
        atomic_execution_artifact_sha256="a" * 64,
        target_proposition="太 16:18 的「磐石」不指彼得本人",
        universe_ids=["VPU-1", "VPU-2"],
    )

    with pytest.raises(ValueError, match="exact unit universe"):
        validate_assessment_binding(
            role="proposal",
            assessment=assessment,
            atomic_execution_artifact_sha256="a" * 64,
            target_proposition="太 16:18 的「磐石」不指彼得本人",
            universe_ids=["VPU-1", "VPU-2", "VPU-3"],
        )
