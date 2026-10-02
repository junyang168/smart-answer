"""Build a source-bound, read-only inventory of #409 GPT-exegesis/Opus-other disagreements.

This is an analyst triage report, not an adjudicated role ledger. It reads only
the frozen packet and immutable model artifacts, and writes no database rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from backend.pipeline import claim_passage_role_audited_resume as audited
from backend.pipeline.claim_passage_role_ledger_assembler import artifact_paths


RECOMMEND_EXEGESIS = {
    5: "The Claim rejects a specific anthropological reading of Hebrews 4:12 and 1 Thessalonians 5:23; negative interpretation still counts.",
    27: "The original Matthew 22 sermon explicitly maps the wedding invitation to God's invitation and the possibility of refusal.",
    28: "The original Matthew 22 sermon maps refusal and the king's punishment to disobeying God and its consequence.",
    43: "Limits the Leviticus 17 slaughter rule to the wilderness setting.",
    47: "Reads Matthew 16:21's 'from then' as a two-stage narrative transition.",
    51: "Rejects a Gnostic referent for the phrase in 1 Timothy 1:4.",
    55: "Reads the unbelieving spouse's 'departure' in 1 Corinthians 7:15 as divorce.",
    56: "Reads the unbelieving spouse's 'departure' in 1 Corinthians 7:15 as divorce.",
    59: "Explains what it means for Paul, Apollos and Cephas to be the believers' own in 1 Corinthians 3:21–23.",
    67: "Identifies the boundary and function of Matthew 6:1–7:11.",
    86: "Resolves the apparent Acts 15 / 1 Corinthians 8–10 conflict by identifying the scope of their situational rules.",
    102: "Reads the singular heir/seed in Galatians 3:28–29 through union with Christ.",
    107: "Identifies the people to whom 2 Corinthians 9:13 applies 'submission,' countering a women-only referent.",
    129: "Explains what the peace offering in Leviticus 7:11–14 signifies.",
    144: "Interprets Mark 5 as a contextual exception to the messianic-secret pattern.",
    255: "Answers how Jesus fulfills the law in the Matthew 5 teaching, though the passage locator should be tightened before promotion.",
    295: "Reads 1 Corinthians 14:27–28 as presupposing translatable tongues, rejecting an unintelligible-tongues reading.",
}

RECOMMEND_OTHER = {
    22: "The Claim harmonizes Paul's general teaching on conscience across several texts, rather than resolving one text's wording.",
    39: "The Claim supplies a broad covenant-reading method rather than a reading of Exodus 20's specific wording.",
    57: "The Claim derives an ethical limit on submission by joining 1 Peter 2 with Acts 4; it does not itself explain either text's wording.",
    72: "The Claim repeats Matthew 5:19's do-then-teach order without resolving its meaning.",
    151: "The Claim repeats 1 Corinthians 6:19's explicit statement that the Spirit lives within believers.",
    167: "The Claim describes the attribution Matthew 9:34 already gives the Pharisees.",
}

UNRESOLVED = {
    25: "The Titus 2:3 causal link may explain the juxtaposed prohibitions, but could be a downstream practical inference.",
    68: "Matthew 16:25's 'because' may ground an interpretation of Jesus' demand, but the Claim may only apply it to discipleship.",
}

DATA_ISSUES = {
    6: "The claim introduces a reading of Philippians 2:7, but its pinned steps only establish a deferred topic.",
    158: "Claim cites 1 Corinthians 12:8 while its interpretive evidence cites 1 Corinthians 2:7.",
    175: "The claim's Mosaic-law qualification is not established by its two pinned evidence steps.",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_report(original_root: Path, opus_root: Path, primary_root: Path,
                 independent_root: Path) -> dict:
    packet_path = original_root / "role-packet.json"
    packet, _ = audited._check_resume(original_root, opus_root)
    claim_by_id = {row["claim_id"]: row for row in packet["claims"]}
    if len(claim_by_id) != len(packet["claims"]):
        raise ValueError("frozen packet has duplicate Claim IDs")
    disagreements = []
    for batch_id in range(1, 638):
        primary_path, independent_path, independent_model = artifact_paths(
            batch_id, original_root=original_root, opus_root=opus_root,
            primary_root=primary_root, independent_root=independent_root,
        )
        primary_decisions, primary_sha = audited._checked_decisions(
            primary_path, packet, batch_id, "primary", audited.PRIMARY_MODEL,
        )
        independent_decisions, independent_sha = audited._checked_decisions(
            independent_path, packet, batch_id, "independent", independent_model,
        )
        for gpt, opus in zip(primary_decisions, independent_decisions, strict=True):
            if gpt["claim_id"] != opus["claim_id"]:
                raise ValueError(f"batch {batch_id} Claim order differs")
            if gpt["role"] != "passage_exegesis" or opus["role"] != "other":
                continue
            index = len(disagreements) + 1
            claim = claim_by_id[gpt["claim_id"]]
            if index in RECOMMEND_EXEGESIS:
                triage, note = "recommend_exegesis", RECOMMEND_EXEGESIS[index]
            elif index in RECOMMEND_OTHER:
                triage, note = "recommend_other", RECOMMEND_OTHER[index]
            elif index in UNRESOLVED:
                triage, note = "unresolved", UNRESOLVED[index]
            elif index in DATA_ISSUES:
                triage, note = "data_issue", DATA_ISSUES[index]
            else:
                triage = "recommend_other"
                note = "The Claim's own wording does not resolve a passage-specific interpretive question; see the independent review's case-specific reason."
            disagreements.append({
                "index": index,
                "claim_id": gpt["claim_id"],
                "batch_id": batch_id,
                "source_id": claim["source_id"],
                "source_content_sha256": claim["source_content_sha256"],
                "claim_content_sha256": claim["claim_content_sha256"],
                "statement": claim["statement"],
                "claim_scripture_refs": claim["scripture_refs"],
                "evidence": [{
                    "evidence_step_id": step["evidence_step_id"],
                    "statement": step["statement"],
                    "scripture_refs": step["scripture_refs"],
                    "fragments": [{
                        "fragment_id": frag["fragment_id"],
                        "verbatim_excerpt": frag["verbatim_excerpt"],
                        "content_sha256": frag["content_sha256"],
                    } for frag in step["fragments"]],
                } for step in claim["evidence_steps"]],
                "gpt_reason": gpt["reason"],
                "opus_reason": opus["reason"],
                "primary_artifact_sha256": primary_sha,
                "independent_artifact_sha256": independent_sha,
                "triage": triage,
                "analyst_note": note,
            })
    if len(disagreements) != 302:
        raise ValueError(f"expected 302 disagreements; found {len(disagreements)}")
    labeled = [set(RECOMMEND_EXEGESIS), set(RECOMMEND_OTHER),
               set(UNRESOLVED), set(DATA_ISSUES)]
    if any(left & right for offset, left in enumerate(labeled)
           for right in labeled[offset + 1:]):
        raise ValueError("triage sets overlap")
    report = {
        "schema_version": "wang_claim_passage_role_disagreement_analysis_v1",
        "status": "analyst_recommendation_not_final_adjudication",
        "scope": "GPT passage_exegesis / Opus other, paired batches 1–637 only",
        "frozen_packet_sha256": packet["artifact_sha256"],
        "frozen_packet_file_sha256": _sha(packet_path),
        "counts": dict(sorted(Counter(row["triage"] for row in disagreements).items())),
        "entries": disagreements,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-root", type=Path, required=True)
    parser.add_argument("--opus-root", type=Path, required=True)
    parser.add_argument("--primary-root", type=Path, required=True)
    parser.add_argument("--independent-root", type=Path, required=True)
    parser.add_argument("--output-path", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(args.original_root, args.opus_root,
                          args.primary_root, args.independent_root)
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    if args.output_path.exists():
        raise FileExistsError(args.output_path)
    args.output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output_path": str(args.output_path),
                      "counts": report["counts"],
                      "frozen_packet_sha256": report["frozen_packet_sha256"]},
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
