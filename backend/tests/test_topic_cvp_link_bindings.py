"""Topic-run links carry their Claim's evidence pairs, or routes never see the member (#460)."""
from backend.pipeline import topic_cvp_write as w


class _Cursor:
    def __init__(self, steps):
        self.steps = steps

    def execute(self, sql, params):
        self.rows = [(k, v) for k, v in self.steps.items() if k in params[0]]

    def fetchall(self):
        return self.rows


def test_bindings_are_the_claims_own_pairs_without_withheld_evidence():
    cur = _Cursor({"E1": {"source_fragment_ids": ["F2", "F1"], "support_eligibility": "eligible_candidate"},
                   "E2": {"source_fragment_id": "F3", "support_eligibility": "withheld_missing_anchor"},
                   "E9": {"source_fragment_id": "F9", "support_eligibility": "eligible"}})
    got = w.evidence_bindings(cur, {"C1": {"evidence_step_ids": ["E2", "E1"]}, "C2": {"evidence_step_ids": []}})
    assert got == {"C1": [{"evidence_step_id": "E1", "source_fragment_id": "F1"},
                          {"evidence_step_id": "E1", "source_fragment_id": "F2"}], "C2": []}
