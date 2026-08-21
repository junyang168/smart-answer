"""What the corpus's extraction packages measure about themselves, today.

The overview table answers "which sources have been run".  This module answers
the other question -- "is there anything I should be looking at" -- across the
whole corpus at once, and it answers it only from files already on disk: no
model is called, nothing is re-run.

Three of the four numbers the health view wants exist already and have simply
never been added up:

``coverage``   the package's own `coverage.by_category.prose.represented_pct`,
               which is the fraction `/admin/wang/source-coverage` shows for
               one source.  Prose is the denominator that means something -- a
               markdown heading is structure, is represented 0% of the time by
               design, and drags a 97% down to 69% while telling nobody
               anything (`source_coverage_view._stats`).
``stranded``   records that entered the package and that authoring cannot
               reach.  Authoring starts at a claim and walks
               `evidence_step_ids` (`manuscript_grounding_check.py:172`), so a
               step no claim names is lost, and an observation is lost unless
               its content reached a step that a claim does name.
``sound``      the pass rate of `corpus_ai_review`'s per-claim decisions, which
               every review file has held all along.

``reachable`` is not here.  It needs the answer key that only a second run
produces (#148), and a health view that quietly omitted it would be claiming a
completeness it does not have.  It is reported as pending, with the reason,
because the failure mode this page exists to prevent is a dashboard that is
green because nothing ran.

**No thresholds.**  Nothing here declares that 0.8 is good.  A document is
called out only when it is worse than nine tenths of the documents that have
actually been measured, which is a statement the corpus makes rather than one
this module makes up; with too few measured documents to rank anything, nothing
is called out and the page says so.  Once several hundred have been measured
the same rule means much more, because the distribution behind it is real.
"""

from __future__ import annotations

import json
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from backend.pipeline.observation_argument_coverage import REACHED, measure_coverage

SCHEMA_VERSION = "wang_extraction_health_v1"

#: Directory names holding superseded output.  A rejected or historical
#: generation is kept for provenance and is not what the corpus currently
#: holds, so measuring it would report a document as worse than it is.
ARCHIVED_DIRS = frozenset({"generations", "rejected-generations", "review-generations",
                           "adjudication-generations", "qa-diagnostic-generations"})

PACKAGE_SUFFIX = ".detailed-knowledge.json"
REVIEW_SUFFIX = ".independent-review.json"

#: The document fingerprint every record id in a package carries.  It is what
#: `argument_layer_view._source_key` groups the store's nodes by, so it is the
#: one identifier that links a measurement here to the page that owns the
#: detail behind it.
DK_ID = re.compile(r"DK-([0-9a-f]{6,})")

#: A distribution needs enough members to say what "normal" is.  Below this the
#: module reports every value and flags nothing: with a handful of documents
#: one unusual package moves the median and the deviation together, and the
#: rule would flag everything or nothing depending on which moved further.
MIN_DISTRIBUTION = 8

#: A document is called out when it is worse than nine tenths of the measured
#: corpus.  This is the relative form the card asks for -- "worse than 90% of
#: the corpus" -- and not a line anyone drew: it moves when the corpus moves,
#: and it means more, not less, as more documents are measured.
#:
#: A robust 3-MAD rule was tried first and flagged nothing at all on the real
#: 25 documents, because the spread is itself the story: stranded runs from 2%
#: to 42%, so three deviations lands outside the range any document can reach.
#: A rule that can only ever say "nothing to see" is the silent-equals-healthy
#: failure this page exists to prevent.
OUTLIER_QUANTILE = 0.9

#: Scales MAD so it estimates the same spread a standard deviation would on
#: normal data.  Reported so the page can say how wide the corpus actually is.
MAD_TO_SIGMA = 1.4826


@dataclass(frozen=True)
class Document:
    """One corpus document, whether or not anything has ever been run on it."""

    document_id: str
    label: str
    kind: str  # "sermon_transcript" | "notes_manuscript"


@dataclass(frozen=True)
class PackageMeasurement:
    """One extraction package, measured against itself."""

    document: Document
    path: Path
    source_id: Optional[str]
    argument_layer_key: Optional[str]
    generated_at: Optional[str]
    model_id: Optional[str]
    prompt_sha256: Optional[str]
    findings: int
    claims: int
    coverage: Optional[float]
    stranded: int
    stranded_rate: Optional[float]
    stranded_steps: tuple[str, ...]
    stranded_observations: tuple[str, ...]
    sound: Optional[float]
    sound_failures: tuple[dict[str, Any], ...]
    sound_unavailable: Optional[str]
    review_path: Optional[Path]


# --------------------------------------------------------------------------
# reachability
# --------------------------------------------------------------------------


def reachable_step_ids(package: Mapping[str, Any]) -> set[str]:
    """Steps authoring can walk to from some claim.

    A claim names its steps in `evidence_step_ids`, and a step may name the
    claims it produced; either direction makes the step reachable, because the
    walk starts at claims and resolves that list.  A claim retired by an
    accepted duplicate finding is not asserted any more, so what only it
    reached is not reached.
    """

    steps = package.get("evidence_steps") or []
    named = {
        str(step_id)
        for claim in (package.get("claims") or [])
        if not claim.get("superseded_by")
        for step_id in (claim.get("evidence_step_ids") or [])
    }
    produced = {
        str(step.get("evidence_step_id"))
        for step in steps
        if step.get("produced_claim_ids")
    }
    present = {str(step.get("evidence_step_id")) for step in steps}
    return (named | produced) & present


def stranded_records(package: Mapping[str, Any]) -> dict[str, list[str]]:
    """The ids of everything in the package that no claim can reach.

    Observations are not simply counted as lost.  `observation_argument_coverage`
    already decides, for one observation, whether its content reached the
    argument layer -- by a recorded relation, by a shared fragment, or by an
    evidence step quoting the same sentence cut at a different point.  That
    judgement is reused here with the evidence steps narrowed to the ones a
    claim reaches, so "reached the argument layer" means the part of it
    authoring can actually walk to, and an observation feeding an orphaned step
    is correctly still stranded.
    """

    reachable = reachable_step_ids(package)
    steps = package.get("evidence_steps") or []
    orphaned = [
        str(step.get("evidence_step_id"))
        for step in steps
        if str(step.get("evidence_step_id")) not in reachable
    ]
    probe = dict(package)
    probe["evidence_steps"] = [
        step for step in steps if str(step.get("evidence_step_id")) in reachable
    ]
    observations = [
        str(row["observation_id"])
        for row in measure_coverage(probe)["observations"]
        if row["status"] not in REACHED
    ]
    return {"steps": orphaned, "observations": observations}


# --------------------------------------------------------------------------
# reading what is on disk
# --------------------------------------------------------------------------


def _is_archived(path: Path, root: Path) -> bool:
    return any(part in ARCHIVED_DIRS for part in path.relative_to(root).parts)


def discover_packages(staging_root: Path) -> list[Path]:
    """Every current extraction package under the claim-layer staging tree."""

    if not staging_root.is_dir():
        return []
    return sorted(
        path
        for path in staging_root.rglob(f"*{PACKAGE_SUFFIX}")
        if not _is_archived(path, staging_root)
    )


def _review_path(package_path: Path) -> Optional[Path]:
    """The review of *this* run, not of another run of the same source.

    Two conventions are in use -- the review beside the package, and a sibling
    `reviews/` directory (`research_batch_runner.artifact_paths`).  Both keep
    one run's outputs together, which is what makes stem matching safe: the
    same source extracted three times has the same stem three times, in three
    different directories.
    """

    stem = package_path.name[: -len(PACKAGE_SUFFIX)]
    candidates = [
        package_path.with_name(f"{stem}{REVIEW_SUFFIX}"),
        package_path.parent.parent / "reviews" / f"{stem}{REVIEW_SUFFIX}",
    ]
    return next((path for path in candidates if path.is_file()), None)


def _document(package: Mapping[str, Any], package_path: Path) -> Document:
    documents = package.get("source_documents") or [{}]
    first = documents[0]
    kind = str(first.get("source_type") or "sermon_transcript")
    if kind == "notes_manuscript":
        # `project_id` is the directory the manuscript lives in, which is what
        # `wang_operations._notes_rows` calls the source: matching it is what
        # lets a measured manuscript be recognised as a corpus document rather
        # than as something nobody has a list of.
        document_id = str(
            first.get("project_id")
            or str(first.get("source_id") or package_path.name).removeprefix("notes_manuscript:")
        )
        label = str(first.get("title") or document_id)
    else:
        # A transcript's catalog identity is its file name, not the title the
        # package copied into `transcript_id`: `sermon_catalog.json` keys on
        # the file stem, and two sermons can share a title.
        source_path = str(first.get("source_path") or "")
        document_id = Path(source_path).stem if source_path else str(first.get("transcript_id") or "")
        label = str(first.get("title") or document_id)
    return Document(document_id=document_id, label=label, kind=kind)


def _argument_layer_key(package: Mapping[str, Any]) -> Optional[str]:
    for row in (package.get("claims") or []) + (package.get("evidence_steps") or []):
        match = DK_ID.search(str(row.get("claim_id") or row.get("evidence_step_id") or ""))
        if match:
            return match.group(1)
    return None


def _coverage(package: Mapping[str, Any]) -> Optional[float]:
    prose = ((package.get("coverage") or {}).get("by_category") or {}).get("prose") or {}
    value = prose.get("represented_pct")
    return round(float(value) / 100.0, 4) if isinstance(value, (int, float)) else None


def _sound(
    review: Mapping[str, Any], claim_ids: set[str]
) -> tuple[Optional[float], tuple[dict[str, Any], ...], Optional[str]]:
    """The review's pass rate, or why it cannot be trusted for this package.

    A review whose claims are not this package's claims is a review of an
    earlier run that happens to sit in the same directory.  Reporting its pass
    rate as this package's soundness is the quiet lie the health view exists to
    catch, so an unmatched review yields no number and a reason instead.
    """

    rows = review.get("claim_reviews") or []
    if not rows:
        return None, (), "複審檔沒有逐條判定"
    reviewed = {str(row.get("claim_id")) for row in rows}
    if claim_ids and not reviewed & claim_ids:
        return None, (), "複審檔對應的是另一次抽取"
    passed = sum(1 for row in rows if row.get("decision") == "pass")
    failures = tuple(
        {
            "claim_id": str(row.get("claim_id")),
            "decision": str(row.get("decision")),
            "issues": [str(issue) for issue in (row.get("issues") or [])][:3],
        }
        for row in rows
        if row.get("decision") != "pass"
    )
    return round(passed / len(rows), 4), failures, None


def measure_package(package_path: Path) -> PackageMeasurement:
    """Measure one package, reading only that package and its own review."""

    package = json.loads(package_path.read_text(encoding="utf-8"))
    stranded = stranded_records(package)
    steps = package.get("evidence_steps") or []
    observations = package.get("observations") or []
    claims = [row for row in (package.get("claims") or []) if not row.get("superseded_by")]
    findings = len(steps) + len(observations)
    count = len(stranded["steps"]) + len(stranded["observations"])
    extraction = package.get("extraction") or {}
    first_document = (package.get("source_documents") or [{}])[0]

    review_path = _review_path(package_path)
    sound: Optional[float] = None
    failures: tuple[dict[str, Any], ...] = ()
    unavailable: Optional[str] = "沒有複審檔"
    if review_path is not None:
        review = json.loads(review_path.read_text(encoding="utf-8"))
        sound, failures, unavailable = _sound(review, {str(row.get("claim_id")) for row in claims})

    return PackageMeasurement(
        document=_document(package, package_path),
        path=package_path,
        source_id=str(first_document.get("source_id") or "") or None,
        argument_layer_key=_argument_layer_key(package),
        generated_at=extraction.get("generated_at"),
        model_id=extraction.get("model_id"),
        prompt_sha256=extraction.get("prompt_sha256"),
        findings=findings,
        claims=len(claims),
        coverage=_coverage(package),
        stranded=count,
        stranded_rate=round(count / findings, 4) if findings else None,
        stranded_steps=tuple(stranded["steps"]),
        stranded_observations=tuple(stranded["observations"]),
        sound=sound,
        sound_failures=failures,
        sound_unavailable=unavailable,
        review_path=review_path,
    )


def latest_per_document(rows: Iterable[PackageMeasurement]) -> list[PackageMeasurement]:
    """The current state of each document: its most recent extraction.

    Re-extractions of one source are the same document measured again, not two
    documents.  Counting both would let a source that was re-run after a fix go
    on dragging its old number through the distribution.
    """

    newest: dict[str, PackageMeasurement] = {}
    for row in rows:
        current = newest.get(row.document.document_id)
        if current is None or (row.generated_at or "") >= (current.generated_at or ""):
            newest[row.document.document_id] = row
    return sorted(newest.values(), key=lambda row: row.document.document_id)


# --------------------------------------------------------------------------
# distribution
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Distribution:
    values: tuple[float, ...]
    median: Optional[float]
    #: The corpus's spread, as a standard-deviation-equivalent from MAD.
    #: Median and MAD rather than mean and standard deviation, because the
    #: values this reads are exactly the ones an outlier would distort: one
    #: package with fifty stranded records must not be allowed to widen
    #: "normal" until it contains itself.
    spread: Optional[float]
    #: The value a document has to be worse than to be called out, or None
    #: when too few documents have been measured for the corpus to rank them.
    cutoff: Optional[float]


def _quantile(ordered: Sequence[float], q: float) -> float:
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def distribution(values: Sequence[float], bad_direction: str) -> Distribution:
    """Where the corpus sits, and how far out a document has to be to be named."""

    ordered = tuple(sorted(values))
    if not ordered:
        return Distribution((), None, None, None)
    median = statistics.median(ordered)
    if len(ordered) < MIN_DISTRIBUTION:
        # Not enough documents to have a distribution.  Every value is still
        # reported -- the band is the point -- but nothing is ranked against
        # a corpus that is four documents wide.
        return Distribution(ordered, median, None, None)
    spread = statistics.median([abs(value - median) for value in ordered]) * MAD_TO_SIGMA
    quantile = OUTLIER_QUANTILE if bad_direction == "high" else 1 - OUTLIER_QUANTILE
    return Distribution(ordered, median, spread, _quantile(ordered, quantile))


# --------------------------------------------------------------------------
# the corpus, including what was never run
# --------------------------------------------------------------------------


def corpus_documents(rows: Iterable[Mapping[str, Any]]) -> list[Document]:
    """The corpus as the overview table enumerates it, extracted or not.

    This is the denominator that makes "never measured" visible: without it the
    page would divide the measured documents by themselves and report the
    corpus as healthy on the strength of a tenth of it.  The rows come from
    `wang_operations.corpus_rows` rather than being rebuilt here, because two
    pages that count the corpus separately will eventually count it
    differently, and then neither number can be trusted.
    """

    return [
        Document(
            document_id=str(row["source_id"]),
            label=str(row.get("title") or row["source_id"]),
            kind="notes_manuscript" if row.get("kind") == "notes_manuscript" else "sermon_transcript",
        )
        for row in rows
        if row.get("source_id")
    ]


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricSpec:
    name: str
    question: str
    #: "low" when a small value is the bad one.
    bad_direction: str
    owner: str
    owner_href: Optional[str]


METRICS: tuple[MetricSpec, ...] = (
    MetricSpec("coverage", "來源的每一句都讀到了嗎？", "low", "來源覆蓋", "/admin/wang/source-coverage"),
    MetricSpec("reachable", "每條記錄都有 claim 走得到嗎？", "low", "論證層", "/admin/wang/argument-layer"),
    MetricSpec("stranded", "抽到了，但沒有東西走得到", "high", "論證層", "/admin/wang/argument-layer"),
    MetricSpec("sound", "交付出去的內容站得住嗎？", "low", "單篇詳情", None),
)

#: Why a metric has no numbers.  Stated on the page rather than left blank: a
#: band that is empty because nothing measured it looks exactly like a band
#: that is empty because nothing went wrong.
PENDING: dict[str, str] = {
    "reachable": "分母要「同一份跑兩次、取聯集當答案卷」才有（#148）",
}

_VALUE = {
    "coverage": lambda row: row.coverage,
    "stranded": lambda row: row.stranded_rate,
    "sound": lambda row: row.sound,
}


def _measured(rows: Sequence[PackageMeasurement], name: str) -> list[tuple[PackageMeasurement, float]]:
    getter = _VALUE.get(name)
    if getter is None:
        return []
    return [(row, value) for row in rows if (value := getter(row)) is not None]


def _outliers(
    spec: MetricSpec, dist: Distribution, measured: Sequence[tuple[PackageMeasurement, float]]
) -> list[tuple[PackageMeasurement, float]]:
    """The documents worse than nine tenths of the corpus on this metric.

    A value equal to the median is never one of them however the cutoff falls:
    when a corpus is uniform there is nothing to be worse than, and calling out
    a document for sitting exactly where everything sits would make the page
    cry wolf on its quietest day.
    """

    if dist.cutoff is None or dist.median is None:
        return []
    if spec.bad_direction == "low":
        hits = [pair for pair in measured if pair[1] < dist.cutoff and pair[1] < dist.median]
        return sorted(hits, key=lambda pair: pair[1])
    hits = [pair for pair in measured if pair[1] > dist.cutoff and pair[1] > dist.median]
    return sorted(hits, key=lambda pair: pair[1], reverse=True)


def _sentence(spec: MetricSpec, row: PackageMeasurement, value: float, median: Optional[float]) -> str:
    """One plain sentence saying what is wrong, in numbers a person can check.

    Numbers, not a colour, and every one of them stated next to what it is
    being compared against -- the reader has to be able to disagree with the
    judgement, which needs both sides of it on the page.
    """

    if spec.name == "stranded":
        times = round(value / median, 1) if median else None
        comparison = f"，是語料中位數的 {times} 倍" if times and times >= 1.5 else ""
        return (
            f"{row.stranded} 條記錄走不到 —— 這份包共 {row.findings} 條，其中 {value:.0%} "
            f"沒有任何 claim 連得到{comparison}。抽得沒錯，但撰稿看不到它們。"
        )
    if spec.name == "sound":
        tail = f"，語料中位數 {median:.0%}" if median is not None else ""
        return (
            f"{row.claims} 條主張裡有 {len(row.sound_failures)} 條沒有通過複審 —— "
            f"通過率 {value:.0%}{tail}。"
        )
    if spec.name == "coverage":
        tail = f"，語料中位數 {median:.0%}" if median is not None else ""
        return f"來源散文只有 {value:.0%} 進到記錄裡{tail}。沒進去的句子不在任何記錄背後。"
    return f"{spec.name} = {value}"


def _link(spec: MetricSpec, row: PackageMeasurement) -> Optional[dict[str, str]]:
    if spec.name == "stranded" and row.argument_layer_key:
        return {
            "label": f"{row.stranded} 條走不到的記錄",
            "href": f"/admin/wang/argument-layer?source={row.argument_layer_key}&only=stranded",
        }
    if spec.name == "coverage" and row.source_id:
        return {"label": "逐句看覆蓋", "href": f"/admin/wang/source-coverage?source={row.source_id}"}
    if spec.name == "sound" and row.sound_failures and row.argument_layer_key:
        return {
            "label": f"{len(row.sound_failures)} 條沒過複審的主張",
            "href": f"/admin/wang/argument-layer?source={row.argument_layer_key}&only=unsound",
        }
    return None


def _trend(rows: Sequence[PackageMeasurement]) -> dict[str, Any]:
    """Median stranded rate per extraction day, with the prompt changes on it.

    One document's score cannot show a change that moved the whole corpus.  The
    events are not a hand-kept list either: `extraction.prompt_sha256` records
    which prompt produced a package, so the day a new one first appears is a
    prompt change that happened, not one somebody remembered to write down.
    """

    dated = sorted(
        [row for row in rows if row.generated_at and row.stranded_rate is not None],
        key=lambda row: str(row.generated_at),
    )
    by_day: dict[str, list[PackageMeasurement]] = defaultdict(list)
    for row in dated:
        by_day[str(row.generated_at)[:10]].append(row)

    points = [
        {
            "date": day,
            "median": round(statistics.median([row.stranded_rate for row in members]), 4),
            "packages": len(members),
        }
        for day, members in sorted(by_day.items())
    ]

    # Two prompt changes on one day are one marker: drawn separately they land
    # on the same x and the second label is written over the first.
    changes: dict[str, list[str]] = {}
    seen: set[str] = set()
    for row in dated:
        sha = str(row.prompt_sha256 or "")
        if not sha or sha in seen:
            continue
        seen.add(sha)
        if len(seen) == 1:
            continue  # The first prompt is the starting state, not a change.
        changes.setdefault(str(row.generated_at)[:10], []).append(sha[:8])
    events = [
        {
            "date": date,
            "prompt_sha256": "、".join(shas),
            "label": f"prompt 換成 {'、'.join(shas)}",
        }
        for date, shas in sorted(changes.items())
    ]
    return {"points": points, "events": events}


def build_report(
    *,
    staging_root: Path,
    corpus: Sequence[Document],
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """The whole health view, from files alone."""

    packages = [measure_package(path) for path in discover_packages(staging_root)]
    current = latest_per_document(packages)

    corpus_ids = {document.document_id for document in corpus}
    measured_ids = {row.document.document_id for row in current}
    manuscripts = [row for row in current if row.document.kind == "notes_manuscript"]
    # A package whose document the corpus enumeration does not know about.
    # Counted rather than dropped: it is measured work, and a document that
    # has been extracted but that no list contains is itself worth seeing.
    off_corpus = sorted(measured_ids - corpus_ids)

    metrics: list[dict[str, Any]] = []
    flagged: dict[str, list[tuple[MetricSpec, PackageMeasurement, float]]] = defaultdict(list)
    for spec in METRICS:
        measured = _measured(current, spec.name)
        dist = distribution([value for _, value in measured], spec.bad_direction)
        outliers = _outliers(spec, dist, measured)
        for row, value in outliers:
            flagged[row.document.document_id].append((spec, row, value))
        metrics.append({
            "name": spec.name,
            "question": spec.question,
            "state": "pending" if spec.name in PENDING else "measured",
            "pending_reason": PENDING.get(spec.name),
            "bad_direction": spec.bad_direction,
            "owner": spec.owner,
            "owner_href": spec.owner_href,
            "measured_documents": len(measured),
            "median": dist.median,
            "spread": dist.spread,
            "cutoff": dist.cutoff,
            "distribution_is_thin": len(measured) < MIN_DISTRIBUTION,
            "values": [
                {
                    "document_id": row.document.document_id,
                    "label": row.document.label,
                    "value": value,
                    "outlier": any(row is hit for hit, _ in outliers),
                }
                for row, value in measured
            ],
        })

    exceptions = []
    for document_id, hits in flagged.items():
        row = hits[0][1]
        reasons = [
            {
                "metric": spec.name,
                "value": value,
                "sentence": _sentence(
                    spec, item, value,
                    next(m["median"] for m in metrics if m["name"] == spec.name),
                ),
                "link": _link(spec, item),
            }
            for spec, item, value in hits
        ]
        exceptions.append({
            "document_id": document_id,
            "label": row.document.label,
            "argument_layer_key": row.argument_layer_key,
            "source_id": row.source_id,
            "generated_at": row.generated_at,
            "reasons": reasons,
        })
    exceptions.sort(key=lambda item: (-len(item["reasons"]), item["document_id"]))

    quiet = len(current) - len(exceptions)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "advisory": "這頁只指路，不擋任何流程。",
        "corpus": {
            "documents": len(corpus) + len(off_corpus),
            "measured": len(current),
            "never_extracted": len(corpus_ids - measured_ids),
            "needs_attention": len(exceptions),
            "within_normal_range": quiet,
            "measured_manuscripts": len(manuscripts),
            "packages_on_disk": len(packages),
            "off_corpus_documents": off_corpus,
        },
        "metrics": metrics,
        "trend": _trend(packages),
        "exceptions": exceptions,
        "documents": [
            {
                "document_id": row.document.document_id,
                "label": row.document.label,
                "kind": row.document.kind,
                "argument_layer_key": row.argument_layer_key,
                "source_id": row.source_id,
                "generated_at": row.generated_at,
                "model_id": row.model_id,
                "prompt_sha256": (row.prompt_sha256 or "")[:8] or None,
                "findings": row.findings,
                "claims": row.claims,
                "coverage": row.coverage,
                "stranded": row.stranded,
                "stranded_rate": row.stranded_rate,
                "sound": row.sound,
                "sound_unavailable": row.sound_unavailable,
            }
            for row in current
        ],
    }


def document_findings(staging_root: Path, argument_layer_key: str) -> Optional[dict[str, Any]]:
    """Which records of one document the health view is pointing at.

    The argument layer owns the detail behind `stranded` and behind a failed
    review, and it reads the merged store rather than these files.  Handing it
    the ids keeps one owner for the fact itself: the two pages cannot disagree
    about which records are stranded, because only one of them decides.
    """

    packages = [measure_package(path) for path in discover_packages(staging_root)]
    row = next(
        (item for item in latest_per_document(packages) if item.argument_layer_key == argument_layer_key),
        None,
    )
    if row is None:
        return None
    return {
        "schema_version": SCHEMA_VERSION,
        "argument_layer_key": argument_layer_key,
        "document_id": row.document.document_id,
        "label": row.document.label,
        "generated_at": row.generated_at,
        "findings": row.findings,
        "stranded": {
            "count": row.stranded,
            "evidence_step_ids": list(row.stranded_steps),
            "observation_ids": list(row.stranded_observations),
        },
        "unsound": {
            "count": len(row.sound_failures),
            "claim_ids": [failure["claim_id"] for failure in row.sound_failures],
            "reviews": list(row.sound_failures),
        },
    }
