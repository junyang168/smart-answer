"""Evidence-bound analyst triage of #409 batches 638–798 role disagreements.

This is a role/function decision, not a judgement on the professor's theology.
The claim's own assertion must interpret an identifiable passage.  Interpretive
EvidenceSteps do not automatically turn a downstream doctrinal Claim into
passage exegesis.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from backend.pipeline import claim_passage_role_new_disagreement_inventory as inventory
from backend.pipeline import claim_passage_role_runner as base


EXEGESIS_NOTES = {
    3: "Identifies the wicked in Psalm 32:10 by their failure to trust the LORD.",
    36: "Explains why 1 Timothy 1:1 calls God, rather than only Christ, Savior.",
    37: "Reads the 'disqualified' test in 2 Corinthians 13:5 as continued following with Christ in believers.",
    43: "Reads the contrast in Jesus' Matthew 16:13–15 question as directing attention to the disciples' confession.",
    47: "Explains remembrance in 1 Corinthians 11:25 as present participation in Christ's redemption.",
    56: "Explains in what sense John is greater in Matthew 11:11.",
    57: "Explains the narrative purpose of the rolled-away stone in Matthew 28:2–6, against an exit-for-Jesus reading.",
    63: "Reads Peter's three shelters in Matthew 17:4–5 as wrongly placing Jesus beside Moses and Elijah as equals.",
    66: "Explains Jerusalem's being troubled in Matthew 2:3 as fear of Herod, not joy at the new king.",
    99: "Reads Jude 1:3's once-given faith as a single giving with continuing validity.",
    101: "Explains the unforgivable offense in Matthew 12:32 and Mark 3:28–30 through attributing the Spirit's work to the devil and refusing repentance.",
    102: "Identifies the experience compared by Acts 10:47's 'as we have' with Pentecost.",
    105: "Explains the weak conscience and knowledge roles in the specific 1 Corinthians 8:10–11 scene.",
    113: "Explains whom Jesus identifies with 'me' when Saul persecutes believers in Acts 9:4–5.",
    118: "Reads the agency of faithfulness in Romans 3:25–26 as God's own faithfulness.",
    119: "Explains who eats first in 1 Corinthians 11:20–21 and why that meal is not the Lord's Supper.",
    125: "Explains 'Christ became our wisdom' in 1 Corinthians 1:30 through union with Christ.",
    134: "Reads 'you will not always have me' in Matthew 26:10–11 as Jesus' impending death.",
    139: "Reads Romans 12:9's abhorring evil and clinging to good as the expression of sincere love.",
    151: "Distinguishes the permitted and forbidden scopes of eating idol food in 1 Corinthians 8:4–11.",
    152: "Explains the mercy named in 1 Timothy 1:2 as mercy toward human weakness.",
    153: "Reads the Passover-timing contrast between Jesus and the leaders in Matthew 26:2–5.",
    155: "Explains the particular demand to sell everything in Mark 10:17–22 by the man's wealth becoming an idol.",
    163: "Identifies the function of Matthew 5:3–12 as disclosing the speaking Messiah's identity.",
    164: "Reads Matthew 23:37–39's closing lament as a call to repentance after the rebukes.",
    166: "Interprets Matthew 5:3's Greek wording against the Septuagint of Isaiah 61:1.",
    171: "Identifies Matthew 4:17 and 16:21 as narrative division markers.",
    174: "Explains Matthew 11:12's kingdom violence as persecution, in its John the Baptist context.",
    176: "Explains the unforgivable offense in Mark 3:28–30 as attributing the Spirit's work to the devil and refusing repentance.",
}

UNRESOLVED_NOTES = {
    46: "Interprets a definite-article contrast, but only Ezekiel as a book and the New Testament as a whole are located.",
    54: "Interprets why Jesus forbade publicity about healing, but no particular narrative is pinned.",
    154: "Interprets John's doubled Amen, but only the Gospel as a whole is located.",
    158: "Interprets the divorce-certificate rule's purpose, but no particular passage is pinned.",
}

DATA_ISSUE_NOTES = {
    29: "The 'put on the Lord Jesus Christ' reading is pinned to Romans 14:14; its passage locator needs repair before role promotion.",
    79: "The Claim about tongues is pinned to 1 Corinthians 12:3, which is not a tongues passage; no EvidenceStep supplies another locator.",
    130: "The 'fire' definition is pinned to Numbers 21:8 without an evidence link showing it interprets that verse.",
    135: "The Claim limits the uncut-hair rule to Samson, but its pinned EvidenceStep says men generally must not cut their hair.",
    141: "The lexical comparison is pinned to Ezekiel 44:30, but the assertion that the terms refer to Christ in Paul lacks a Pauline passage locator.",
}


def build_analysis(inventory_path: Path, original_root: Path, opus_root: Path,
                   primary_root: Path, independent_root: Path) -> dict:
    supplied = base._read_json(inventory_path)
    base._check_artifact(supplied)
    expected = inventory.build_inventory(original_root, opus_root,
                                         primary_root, independent_root)
    if supplied != expected:
        raise ValueError("disagreement inventory differs from frozen inputs")
    special = [set(EXEGESIS_NOTES), set(UNRESOLVED_NOTES), set(DATA_ISSUE_NOTES)]
    if any(a & b for offset, a in enumerate(special) for b in special[offset + 1:]):
        raise ValueError("analyst triage sets overlap")
    indices = {row["index"] for row in supplied["entries"]}
    if not set.union(*special).issubset(indices):
        raise ValueError("analyst triage references absent disagreement")
    decisions = []
    for row in supplied["entries"]:
        index = row["index"]
        if index in EXEGESIS_NOTES:
            disposition, role, note = "resolved", "passage_exegesis", EXEGESIS_NOTES[index]
        elif index in UNRESOLVED_NOTES:
            disposition, role, note = "needs_human", "unresolved", UNRESOLVED_NOTES[index]
        elif index in DATA_ISSUE_NOTES:
            disposition, role, note = "repair_required", "unresolved", DATA_ISSUE_NOTES[index]
        else:
            disposition, role = "resolved", "other"
            note = ("The Claim itself is a quotation, theological or ethical conclusion, "
                    "historical/causal inference, textual observation, or method, without "
                    "settling an identified passage's meaning; compare both model reasons.")
        decisions.append({
            "index": index,
            "batch_id": row["batch_id"],
            "claim_id": row["claim_id"],
            "claim_content_sha256": row["claim_content_sha256"],
            "source_id": row["source_id"],
            "source_content_sha256": row["source_content_sha256"],
            "primary_artifact_sha256": row["primary_artifact_sha256"],
            "independent_artifact_sha256": row["independent_artifact_sha256"],
            "primary_role": row["primary"]["role"],
            "independent_role": row["independent"]["role"],
            "role": role,
            "disposition": disposition,
            "analyst_note": note,
        })
    counts = dict(sorted(Counter(row["disposition"] for row in decisions).items()))
    if counts != {"needs_human": 4, "repair_required": 5, "resolved": 168}:
        raise ValueError("new disagreement triage denominator changed")
    return base._artifact({
        "schema_version": "wang_claim_passage_role_new_disagreement_analysis_v1",
        "status": "analyst_adjudication_under_user_instruction_not_model_review",
        "scope": "177 role disagreements in paired batches 638–798; 44 both-unresolved remain outside disagreement scope",
        "instruction": "2026-09-29 user: 审核完成的这一批你也用同样方法 resolve difference",
        "inventory_sha256": supplied["artifact_sha256"],
        "packet_sha256": supplied["packet_sha256"],
        "counts_by_disposition": counts,
        "counts_by_role": dict(sorted(Counter(row["role"] for row in decisions).items())),
        "decisions": decisions,
    })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory-path", type=Path, required=True)
    parser.add_argument("--original-root", type=Path, required=True)
    parser.add_argument("--opus-root", type=Path, required=True)
    parser.add_argument("--primary-root", type=Path, required=True)
    parser.add_argument("--independent-root", type=Path, required=True)
    parser.add_argument("--output-path", type=Path, required=True)
    args = parser.parse_args()
    report = build_analysis(args.inventory_path, args.original_root,
                            args.opus_root, args.primary_root, args.independent_root)
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    base._write_immutable(args.output_path, report)
    print(json.dumps({"artifact_sha256": report["artifact_sha256"],
                      "counts_by_role": report["counts_by_role"],
                      "counts_by_disposition": report["counts_by_disposition"],
                      "output_path": str(args.output_path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
