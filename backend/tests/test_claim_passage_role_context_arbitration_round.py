"""The #409 context arbitration is one bounded, source-bound proposal round."""

from __future__ import annotations

import json

import pytest

from backend.pipeline import claim_passage_role_context_arbitration_round as round1
from backend.pipeline import claim_passage_role_runner as base


def _row():
    return {"claim_id": "CL-1", "statement": "解释义", "source_id": "SRC-1",
            "source_match": "exact_file", "claim_scripture_refs": ["提多书 2:12"],
            "evidence_steps": [], "anchor_indices": [0],
            "context": [{"paragraph_key": "S0001", "text": "义是好的关系。"}]}


def _decision(quote="义是好的关系"):
    return {"decisions": {"CL-1": {"role": "passage_exegesis", "disposition": "resolved",
                                    "candidate_reference": "提多书 2:12",
                                    "source_key": "S0001", "source_quote": quote,
                                    "reason": "词义解释"}}}


def test_round_requires_exact_source_quote_and_passage() -> None:
    round1.validate(_decision(), [_row()])
    with pytest.raises(ValueError, match="not verbatim"):
        round1.validate(_decision("不在原件里"), [_row()])
    invalid = _decision()
    invalid["decisions"]["CL-1"]["candidate_reference"] = ""
    with pytest.raises(ValueError, match="lacks passage locator"):
        round1.validate(invalid, [_row()])


def test_existing_repair_and_human_holds_cannot_be_promoted() -> None:
    repair_row = _row() | {"reason_code": "REVIEWED_SOURCE_OR_CLAIM_REPAIR_REQUIRED"}
    with pytest.raises(ValueError, match="repair hold was overridden"):
        round1.validate(_decision(), [repair_row])
    human_row = _row() | {"reason_code": "REVIEWED_HUMAN_DECISION_REQUIRED"}
    with pytest.raises(ValueError, match="human hold was overridden"):
        round1.validate(_decision(), [human_row])

    # A citation can guide the eventual human review without assigning a role.
    clue = _decision()
    clue["decisions"]["CL-1"].update(role="unresolved", disposition="needs_human")
    round1.validate(clue, [human_row])


def test_round_persists_first_raw_answer_before_bounded_retry(tmp_path) -> None:
    audit = base._artifact({"schema_version": "wang_claim_role_source_context_audit_v1",
                            "rows": [_row()]})
    queue = base._artifact({"schema_version": "wang_claim_passage_role_exception_queue_v2",
                            "rows": [{"claim_id": "CL-1", "primary_role": "unresolved",
                                      "primary_reason": "missing context",
                                      "independent_role": "other",
                                      "independent_reason": "general doctrine",
                                      "reason_code": "ONE_REVIEWER_UNRESOLVED"}]})

    class Fake:
        calls = 0

        def generate_json(self, *_args):
            self.calls += 1
            return _decision("wrong quote" if self.calls == 1 else "义是好的关系")

    client = Fake()
    result = round1.run_batch(audit=audit, queue=queue, rows=[_row()], root=tmp_path,
                              client=client, retry_invalid_once=True)
    original = list(tmp_path.glob("batch-????????????????.json"))
    assert len(original) == 1
    assert json.loads(original[0].read_text())["response"]["decisions"]["CL-1"][
        "source_quote"
    ] == "wrong quote"
    assert result["path"].endswith(".retry-2.json")
    assert client.calls == 2
