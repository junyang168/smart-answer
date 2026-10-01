"""Measure one extraction package: was it complete, is it reachable, does it repeat.

Extraction is the only stage of this pipeline with no acceptance test, because
it is the only one whose output cannot be predicted from its input.  A
deterministic ETL reconciles rows in against rows out; here there is no
expected result to compare against, which is why the stage has a schema
validator -- a type check -- and nothing else.

The way to test a component you cannot predict is to run it more than once and
compare.  Two runs over one document, every finding from both merged into one
list, each run scored against that list.  This is pooling, standard in
information retrieval since 1992, and it carries pooling's known hazard: a run
that did not contribute to the list gets no credit for what the contributors
missed.  A list ranks the runs that built it and must never be published as a
fixed standard.

**Reachability, not tier.**  Authoring starts at a claim and walks
`evidence_step_ids` to the steps and their source fragments
(`manuscript_grounding_check.py:172`).  A step some claim links to is therefore
read, and counting only `claims` scored one model at 5 of 18 where 16 were
reachable -- a filing decision read as a capability gap.  What is genuinely
lost is a step no claim points at, and an observation, which the walk never
visits.  That is `stranded`, and in the pilot it was the largest measurable
loss in every model.

Nothing here asks a model to judge.  Matching is character overlap and every
decision reports the text behind it, because a yardstick that needs a model to
read it inherits that model's blind spots.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

#: Two sentences are the same finding when this share of the shorter one's
#: character bigrams appears in the other.  Tuned on real packages: 0.45 keeps
#: 「可4:11 是啟示說明」 apart from 「可4:11 也見於太13:11-17」 while still
#: matching a claim against the more verbose step that carries the same point.
MATCH_THRESHOLD = 0.45

#: Where a finding sits in one package, best first.  Authoring reaches the
#: first two; nothing reaches the last two.
CLAIM = "claim"
LINKED_STEP = "linked_step"
ORPHAN_STEP = "orphan_step"
OBSERVATION = "observation"
TIERS = (CLAIM, LINKED_STEP, ORPHAN_STEP, OBSERVATION)
REACHABLE_TIERS = (CLAIM, LINKED_STEP)


def _bigrams(text: str) -> set[str]:
    han = "".join(ch for ch in str(text) if "一" <= ch <= "鿿")
    return {han[i : i + 2] for i in range(len(han) - 1)}


def similarity(left: str, right: str) -> float:
    """Overlap of the shorter sentence's bigrams, so a long compound sentence
    that contains a short one still matches it."""

    a, b = _bigrams(left), _bigrams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def same_finding(left: str, right: str, threshold: float = MATCH_THRESHOLD) -> bool:
    return similarity(left, right) >= threshold


@dataclass(frozen=True)
class Record:
    """One statement from one package, and how the package filed it."""

    tier: str
    text: str

    @property
    def reachable(self) -> bool:
        return self.tier in REACHABLE_TIERS


def records(package: Mapping[str, Any]) -> list[Record]:
    """Every statement in a package, tagged with whether a claim reaches it.

    A step is reachable when a claim names it in `evidence_step_ids` or the
    step names the claim it produced -- either direction, because the walk
    starts from claims and resolves that list.
    """

    claims = list(package.get("claims") or [])
    steps = list(package.get("evidence_steps") or [])
    by_id = {str(s.get("evidence_step_id") or s.get("id") or ""): s for s in steps}
    linked = {
        str(step_id)
        for claim in claims
        for step_id in (claim.get("evidence_step_ids") or [])
    }
    linked |= {sid for sid, step in by_id.items() if step.get("produced_claim_ids")}

    out: list[Record] = []
    for claim in claims:
        text = str(claim.get("title") or claim.get("statement") or "")
        if text:
            out.append(Record(CLAIM, text))
    for sid, step in by_id.items():
        text = str(step.get("statement") or "")
        if text:
            out.append(Record(LINKED_STEP if sid in linked else ORPHAN_STEP, text))
    for observation in package.get("observations") or []:
        text = str(observation.get("statement") or "")
        if text:
            out.append(Record(OBSERVATION, text))
    return out


@dataclass
class Finding:
    """One thing a document says, however many runs found it."""

    id: str
    text: str
    #: run label -> best tier that run gave it
    seen_in: dict[str, str] = field(default_factory=dict)

    @property
    def found_by(self) -> int:
        return len(self.seen_in)


def combined_list(runs: Mapping[str, Mapping[str, Any]]) -> list[Finding]:
    """Merge every finding from every run into one list.

    `runs` maps a label to a package.  The list is only as wide as the runs
    that built it: a finding none of them produced cannot appear, which is why
    coverage -- measured against the manuscript rather than against output --
    stays in the metric set as the outer check.
    """

    findings: list[Finding] = []
    for label, package in runs.items():
        for record in records(package):
            for finding in findings:
                if same_finding(record.text, finding.text):
                    best = finding.seen_in.get(label)
                    if best is None or TIERS.index(record.tier) < TIERS.index(best):
                        finding.seen_in[label] = record.tier
                    break
            else:
                findings.append(
                    Finding(
                        id=f"F{len(findings) + 1:03d}",
                        text=record.text,
                        seen_in={label: record.tier},
                    )
                )
    return findings


@dataclass
class Score:
    """One run against the combined list."""

    label: str
    total: int
    reachable: tuple[str, ...] = ()
    asserted: tuple[str, ...] = ()
    stranded: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()

    @property
    def recall(self) -> float:
        """What authoring can actually use."""

        return len(self.reachable) / self.total if self.total else 0.0


def score(label: str, findings: Sequence[Finding]) -> Score:
    reachable, asserted, stranded, missing = [], [], [], []
    for finding in findings:
        tier = finding.seen_in.get(label)
        if tier is None:
            missing.append(finding.id)
        elif tier in REACHABLE_TIERS:
            reachable.append(finding.id)
            if tier == CLAIM:
                asserted.append(finding.id)
        else:
            stranded.append(finding.id)
    return Score(
        label=label, total=len(findings),
        reachable=tuple(reachable), asserted=tuple(asserted),
        stranded=tuple(stranded), missing=tuple(missing),
    )


@dataclass
class Agreement:
    """How much two runs of the same job produced in common."""

    shared: int
    only_a: tuple[str, ...]
    only_b: tuple[str, ...]
    total: int

    @property
    def ratio(self) -> float:
        return self.shared / self.total if self.total else 0.0

    @property
    def disputed(self) -> tuple[str, ...]:
        """Findings one run produced and the other did not.

        The actionable output: not a score, a worklist.
        """

        return self.only_a + self.only_b


def agreement(findings: Sequence[Finding], label_a: str, label_b: str) -> Agreement:
    shared = [f.id for f in findings if label_a in f.seen_in and label_b in f.seen_in]
    only_a = [f.id for f in findings if label_a in f.seen_in and label_b not in f.seen_in]
    only_b = [f.id for f in findings if label_b in f.seen_in and label_a not in f.seen_in]
    return Agreement(
        shared=len(shared), only_a=tuple(only_a), only_b=tuple(only_b),
        total=len(findings),
    )


def review_pass_rate(review: Mapping[str, Any]) -> tuple[int, int]:
    """(passed, judged) from an independent-review file.

    The verdicts already exist, one row per claim; nothing has ever summed
    them, which is why no package carries a correctness number today.
    """

    rows = list(review.get("claim_reviews") or [])
    passed = sum(1 for row in rows if row.get("decision") == "pass")
    return passed, len(rows)


def render(findings: Sequence[Finding], scores: Sequence[Score]) -> str:
    lines = [
        f"combined list: {len(findings)} findings from {len({l for f in findings for l in f.seen_in})} runs",
        "",
        "| run | reachable | asserted | stranded | missing |",
        "|---|---:|---:|---:|---:|",
    ]
    for s in scores:
        lines.append(
            f"| `{s.label}` | {len(s.reachable)}/{s.total} ({s.recall:.0%}) | "
            f"{len(s.asserted)} | {len(s.stranded)} | {len(s.missing)} |"
        )
    lines += [
        "",
        "> The list is built from these runs, so it ranks them against each other "
        "and cannot fairly judge a run that did not help build it.",
    ]
    return "\n".join(lines)


def load(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packages", nargs="+", type=Path,
                        help="two or more extraction packages of the same source")
    parser.add_argument("--label", action="append",
                        help="name for each package, in order; defaults to file stem")
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)

    labels = args.label or [p.stem for p in args.packages]
    if len(labels) != len(args.packages):
        parser.error("give one --label per package, or none")
    runs = {label: load(path) for label, path in zip(labels, args.packages)}

    findings = combined_list(runs)
    scores = [score(label, findings) for label in runs]
    print(render(findings, scores))

    if len(labels) == 2:
        ag = agreement(findings, labels[0], labels[1])
        print(f"\nagreement: {ag.shared}/{ag.total} ({ag.ratio:.0%})")
        print(f"disputed:  {len(ag.disputed)} findings produced by one run only")

    if args.json_out:
        args.json_out.write_text(json.dumps({
            "findings": [{"id": f.id, "text": f.text, "seen_in": f.seen_in} for f in findings],
            "scores": [vars(s) for s in scores],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
