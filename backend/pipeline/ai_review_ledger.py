"""Carry AI review verdicts from their artifacts into the authoring store.

D4 puts a person at step 4 only: propose, review, arbitrate, then escalate. The
claim layer runs that way already -- 1,530 of 1,551 candidate claims each carry
a verdict from the independent review -- but none of it ever reached the store.
`review_status` stayed `candidate` and `wang_knowledge.review_events` holds 28
rows, every one of them `reviewer_kind='human'`.

The cause is narrow. `record_review` is the only function that writes a review
event, and the only auditable way to change `review_status`; the whole codebase
calls it from one place, `sync-review-state`, which hardcodes
`reviewer_kind="human"`. The AI stage has no way in. `reviewer_kind` has
accepted `'ai'` and `'system'` since the schema was written and no caller has
ever passed either.

So a library where AI review is the norm reads as a library where nothing has
been reviewed, and any question scoped by `review_status` -- what is approved,
what still needs a person, how wide the human queue is -- gets an answer drawn
from the 6 claims a person happened to approve by hand.

This module reads the verdicts and nothing else. Applying them is
`knowledge_store_runner sync-ai-review`, and only with `--apply`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


#: What a reviewer's verdict makes a claim.
#:
#: Not a new vocabulary. `_apply_claim_relation_review` in
#: `shared_knowledge_pilot.py` has drawn this same line for claim relations
#: since the cross-sermon work: consensus reached becomes
#: `ai_consensus_reviewed`, anything else becomes `human_review_required`.
#: Claims get the same two words for the same two outcomes.
#:
#: `changes_suggested` lands on the human side deliberately. It means the
#: reviewer wanted the claim changed, and whether the arbitration round applied
#: that change is not something this artifact records. Reading it as "reviewed,
#: fine as it stands" would approve 86 claims on the strength of a verdict that
#: said the opposite.
VERDICT_STATUS = {
    "pass": "ai_consensus_reviewed",
    "changes_suggested": "human_review_required",
    "human_review_required": "human_review_required",
}

#: A verdict this module does not recognise is left alone rather than guessed
#: at. The count is reported so a new verdict word cannot pass unnoticed.
UNKNOWN_VERDICT = None


@dataclass(frozen=True)
class Verdict:
    """One reviewer's decision on one claim, and where it came from."""

    claim_id: str
    decision: str
    target_status: str
    reason: str
    artifact_path: str
    artifact_sha256: str
    reviewer_model_ids: tuple[str, ...] = ()

    @property
    def reviewer_id(self) -> str:
        """Who to record as the reviewer.

        The model ids when the artifact names them, so the event says which
        model reviewed rather than only that some model did. Artifacts written
        before the reviewer block existed fall back to the schema name, which
        at least identifies the stage.
        """

        return ",".join(self.reviewer_model_ids) or "independent_ai_review"


@dataclass
class Plan:
    """What `--apply` would do, and what it would leave alone."""

    changes: list[Verdict] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    no_verdict: list[str] = field(default_factory=list)
    unknown_verdicts: dict[str, int] = field(default_factory=dict)
    missing_claims: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        by_status: dict[str, int] = {}
        for verdict in self.changes:
            by_status[verdict.target_status] = by_status.get(verdict.target_status, 0) + 1
        return {
            "changes": len(self.changes),
            "changes_by_status": by_status,
            "unchanged": len(self.unchanged),
            "no_verdict": len(self.no_verdict),
            "unknown_verdicts": dict(self.unknown_verdicts),
            "missing_claims": len(self.missing_claims),
        }


def _artifact_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*.json")):
        if path.is_file():
            yield path


def _reviewer_model_ids(payload: dict[str, Any]) -> tuple[str, ...]:
    reviewer = payload.get("reviewer")
    if not isinstance(reviewer, dict):
        return ()
    ids: list[str] = []
    for key in ("model", "model_id", "reviewer_model_ids", "models"):
        value = reviewer.get(key)
        if isinstance(value, str) and value.strip():
            ids.append(value.strip())
        elif isinstance(value, list):
            ids.extend(str(item).strip() for item in value if str(item).strip())
    return tuple(sorted(set(ids)))


def collect_verdicts(root: Path) -> dict[str, Verdict]:
    """Every per-claim verdict under `root`, one per claim.

    A claim can appear in several artifacts -- a regenerated review lands in a
    `generations/` directory beside the one it replaced. The later file wins,
    ordered by modification time, so re-running a review supersedes rather than
    races. Ties break on path so the result does not depend on filesystem
    ordering.
    """

    latest: dict[str, tuple[float, str, Verdict]] = {}
    for path in _artifact_files(root):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            # A file this module cannot read is not a review it can act on.
            continue
        if not isinstance(payload, dict):
            continue
        rows = payload.get("claim_reviews")
        if not isinstance(rows, list):
            continue
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        models = _reviewer_model_ids(payload)
        stamp = path.stat().st_mtime
        for row in rows:
            if not isinstance(row, dict):
                continue
            claim_id = str(row.get("claim_id") or "").strip()
            decision = str(row.get("decision") or "").strip()
            if not claim_id or not decision:
                continue
            target = VERDICT_STATUS.get(decision, UNKNOWN_VERDICT)
            verdict = Verdict(
                claim_id=claim_id,
                decision=decision,
                target_status=target or "",
                reason=str(row.get("rationale") or row.get("reason") or "").strip(),
                artifact_path=str(path),
                artifact_sha256=sha,
                reviewer_model_ids=models,
            )
            key = (stamp, str(path))
            current = latest.get(claim_id)
            if current is None or key > (current[0], current[1]):
                latest[claim_id] = (stamp, str(path), verdict)
    return {claim_id: entry[2] for claim_id, entry in latest.items()}


def build_plan(claims: dict[str, str], verdicts: dict[str, Verdict]) -> Plan:
    """Compare the verdicts against the store's current `review_status`.

    `claims` maps claim id to its current status. Anything already at its
    target is left alone, which is what makes a second run a no-op rather than
    a second event on every claim.

    Two kinds of claim are deliberately untouched. One that a person has
    already decided -- `approved`, `human_approved`, `superseded` -- keeps that
    decision: an AI verdict does not overwrite a human one, in either
    direction. And one with no verdict stays `candidate`, because "no artifact
    found" is not evidence of anything.
    """

    plan = Plan()
    human_settled = {"approved", "human_approved", "superseded"}
    for claim_id, verdict in sorted(verdicts.items()):
        current = claims.get(claim_id)
        if current is None:
            plan.missing_claims.append(claim_id)
            continue
        if not verdict.target_status:
            plan.unknown_verdicts[verdict.decision] = (
                plan.unknown_verdicts.get(verdict.decision, 0) + 1
            )
            continue
        if current in human_settled or current == verdict.target_status:
            plan.unchanged.append(claim_id)
            continue
        plan.changes.append(verdict)
    plan.no_verdict = sorted(set(claims) - set(verdicts))
    return plan


def review_reason(verdict: Verdict) -> str:
    """The `reason` stored on the review event.

    It names the artifact and its hash, so a status in the store can be traced
    back to the file that justified it. Without that, `ai_consensus_reviewed`
    is just as unaccountable as the `system_approved` it replaces.
    """

    head = f"独立 AI 复审：{verdict.decision}"
    body = verdict.reason.replace("\n", " ").strip()
    if len(body) > 400:
        body = body[:400] + "…"
    trail = f"[artifact {Path(verdict.artifact_path).name} sha256:{verdict.artifact_sha256[:12]}]"
    return " ".join(part for part in (head, body, trail) if part)
