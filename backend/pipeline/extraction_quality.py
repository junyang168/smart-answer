"""Recall against a fixed set of propositions, because counting claims does not work.

Every column the bench had measured either failed to discriminate or measured
the wrong thing. Prose coverage was 24-25/132 for four different models.
Orphans were 0 for all of them -- the gate works, so it cannot rank. And the
headline column, claim count, turned out to measure granularity: gpt-5.6-sol
states 可1:43-45's practical reason and its synoptic parallels as two claims,
gemini-3.7-flash writes one claim joining them with 且. Same propositions,
15 objects against 6.

So the unit of measurement is the proposition, not the object. A gold set is
built once from the union of what several models found, decomposed so each
entry can be checked on its own, and then every model is scored on how much of
it the package expresses -- however many claim objects it used to do so.

What this deliberately does not do is ask a model to judge. Matching is term
based and every decision is reported with the text that satisfied it, so a
wrong score is auditable rather than authoritative. `corpus_ai_review` already
supplies the other half -- whether what is there is *sound* -- and precision
without recall rewards the model that extracts least.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

GOLD_DIR = Path(__file__).resolve().parent / "gold_propositions"


@dataclass(frozen=True)
class Proposition:
    id: str
    text: str
    #: Groups of alternatives. A text matches when it contains at least one
    #: term from *every* group -- an AND of ORs, which is what distinguishes
    #: "可4:11 是啟示說明" from "可4:11 也見於太13:11-17" without either rule
    #: being so tight that a rephrasing escapes it.
    match: tuple[tuple[str, ...], ...]

    def matches(self, text: str) -> bool:
        return all(any(term in text for term in group) for group in self.match)


@dataclass
class GoldSet:
    gold_id: str
    source_id: str
    section: Optional[int]
    propositions: tuple[Proposition, ...]
    human_reviewed: bool = False

    @classmethod
    def load(cls, path: Path) -> "GoldSet":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            gold_id=raw["gold_id"], source_id=raw["source_id"],
            section=raw.get("section"), human_reviewed=bool(raw.get("human_reviewed")),
            propositions=tuple(
                Proposition(id=p["id"], text=p["text"],
                            match=tuple(tuple(g) for g in p["match"]))
                for p in raw["propositions"]
            ),
        )


@dataclass
class GoldScore:
    """How much of the argument a package expresses, and where it was found."""

    gold_id: str
    total: int
    in_claims: tuple[str, ...] = ()
    #: Found only once evidence_steps are searched too. A package that carries
    #: the proposition but never promotes it to a claim has done part of the
    #: work, and the gap between these two numbers is worth seeing.
    in_steps_only: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    evidence: dict[str, str] = field(default_factory=dict)
    human_reviewed: bool = False

    @property
    def claim_recall(self) -> float:
        return len(self.in_claims) / self.total if self.total else 0.0

    @property
    def recall(self) -> float:
        found = len(self.in_claims) + len(self.in_steps_only)
        return found / self.total if self.total else 0.0


def _claim_texts(package: Mapping[str, Any]) -> list[str]:
    return [
        " ".join(str(c.get(k) or "") for k in ("title", "statement"))
        for c in (package.get("claims") or [])
    ]


def _step_texts(package: Mapping[str, Any]) -> list[str]:
    return [str(s.get("statement") or "") for s in (package.get("evidence_steps") or [])]


def score_package(package: Mapping[str, Any], gold: GoldSet) -> GoldScore:
    claims = _claim_texts(package)
    steps = _step_texts(package)
    in_claims: list[str] = []
    in_steps: list[str] = []
    missing: list[str] = []
    evidence: dict[str, str] = {}
    for prop in gold.propositions:
        hit = next((t for t in claims if prop.matches(t)), None)
        if hit is not None:
            in_claims.append(prop.id)
            evidence[prop.id] = f"claim: {hit[:90]}"
            continue
        hit = next((t for t in steps if prop.matches(t)), None)
        if hit is not None:
            in_steps.append(prop.id)
            evidence[prop.id] = f"step: {hit[:90]}"
            continue
        missing.append(prop.id)
    return GoldScore(
        gold_id=gold.gold_id, total=len(gold.propositions),
        in_claims=tuple(in_claims), in_steps_only=tuple(in_steps),
        missing=tuple(missing), evidence=evidence,
        human_reviewed=gold.human_reviewed,
    )


def render_scores(rows: Sequence[tuple[str, GoldScore]]) -> str:
    lines = ["| model | claim recall | +steps | missing |", "|---|---:|---:|---|"]
    for label, score in rows:
        lines.append(
            f"| `{label}` | {len(score.in_claims)}/{score.total} "
            f"({score.claim_recall:.0%}) | {len(score.in_claims) + len(score.in_steps_only)}"
            f"/{score.total} ({score.recall:.0%}) | "
            f"{', '.join(score.missing) if score.missing else '—'} |"
        )
    if rows and not rows[0][1].human_reviewed:
        lines += ["", "> The gold set has not been reviewed by a person yet, so these "
                  "numbers rank models against each other but are not a standard."]
    return "\n".join(lines)
