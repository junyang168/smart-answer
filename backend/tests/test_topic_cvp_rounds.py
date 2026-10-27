"""Multi-round revision and escalation in the topic CVP agent (#456, solution doc §6)."""
from backend.pipeline import topic_cvp_pilot as m


class Scripted:
    """A model that answers each call with the next scripted reply."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.last_raw_response = None
        self.calls = 0

    def generate_json(self, system, text, schema, timeout_seconds=None):
        self.calls += 1
        return self.replies.pop(0)


def proposal(*unresolved):
    return {"claim_dispositions": [], "viewpoints": [], "relations": [], "existing_relation_checks": [],
            "merge_proposals": [], "unresolved": [{"item": i, "question": q, "refs": [], "positions": p}
                                                  for i, q, p in unresolved]}


def verdict(item, value):
    return {"item": item, "verdict": value, "reason": "r", "correction": ""}


PACKET = {"topic": "t", "scope_viewpoints": [], "scope_claims": [], "neighbour_claims": [], "claim_relations": [],
          "existing_viewpoint_relations": [], "library_index": [], "source_index": [], "recall": {}}
PRIVATE = {"blocks": {}, "members": {}}


def test_unsettled_items_go_to_the_owner_and_accepted_ones_stay(tmp_path):
    start = proposal(("UNR:1", "a", ""), ("UNR:2", "b", ""), ("UNR:3", "c", ""))
    reviewer = Scripted([
        {"verdicts": [verdict("UNR:1", "accept"), verdict("UNR:2", "revise"), verdict("UNR:3", "revise")], "other_findings": []},
        {"verdicts": [verdict("UNR:2", "revise")], "other_findings": []},
        {"verdicts": [verdict("UNR:2", "reject")], "other_findings": []},
    ])
    proposer = Scripted([
        # Round 1: UNR:2 changed, UNR:3 left as it was (no progress).
        proposal(("UNR:1", "a", ""), ("UNR:2", "b2", ""), ("UNR:3", "c", "")),
        # Round 2: the proposer disagrees on UNR:2; the reviewer holds.
        proposal(("UNR:1", "a", ""), ("UNR:2", "b2", "不同意复核：原话如此"), ("UNR:3", "c", "")),
    ])
    final, verdicts, escalated, _ = m.revise_rounds(proposer, reviewer, start, PACKET, PRIVATE, tmp_path, set(), "")

    assert verdicts["UNR:1"]["verdict"] == "accept"
    assert set(escalated) == {"UNR:2", "UNR:3"}
    assert "没有修改" in escalated["UNR:3"]["why"]
    assert "不同意复核" in escalated["UNR:2"]["why"]
    assert [h["round"] for h in escalated["UNR:2"]["history"]] == [0, 1, 2]
    # Nothing left to revise after round 2, so round 3 is never called.
    assert proposer.calls == 2 and reviewer.calls == 3


def test_rounds_stop_at_the_cap(tmp_path):
    start = proposal(("UNR:1", "a", ""))
    reviewer = Scripted([{"verdicts": [verdict("UNR:1", "revise")], "other_findings": []}] * (m.MAX_ROUNDS + 1))
    proposer = Scripted([proposal(("UNR:1", f"a{n}", "")) for n in range(m.MAX_ROUNDS)])
    _, _, escalated, _ = m.revise_rounds(proposer, reviewer, start, PACKET, PRIVATE, tmp_path, set(), "")

    assert proposer.calls == m.MAX_ROUNDS
    assert "用尽" in escalated["UNR:1"]["why"]


def test_everything_accepted_needs_no_revision(tmp_path):
    start = proposal(("UNR:1", "a", ""))
    reviewer = Scripted([{"verdicts": [verdict("UNR:1", "accept")], "other_findings": []}])
    proposer = Scripted([])
    _, verdicts, escalated, _ = m.revise_rounds(proposer, reviewer, start, PACKET, PRIVATE, tmp_path, set(), "")

    assert escalated == {} and proposer.calls == 0
