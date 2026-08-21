from __future__ import annotations

import json
from pathlib import Path

from backend.pipeline.extraction_health import (
    Document,
    build_report,
    distribution,
    latest_per_document,
    measure_package,
    reachable_step_ids,
    stranded_records,
)


def _fragment(fragment_id: str, excerpt: str, paragraph: str = "S0001"):
    return {
        "fragment_id": fragment_id,
        "source_id": "SRC-1",
        "paragraph_key": paragraph,
        "verbatim_excerpt": excerpt,
    }


def _package(**overrides):
    package = {
        "source_documents": [{
            "source_id": "SRC-A",
            "source_type": "sermon_transcript",
            "source_path": "/data/script_published/A.json",
            "title": "講道 A",
        }],
        "extraction": {"generated_at": "2026-08-01T00:00:00+00:00", "model_id": "m", "prompt_sha256": "aa" * 16},
        "source_fragments": [],
        "observations": [],
        "evidence_steps": [],
        "claims": [],
        "knowledge_relations": [],
        "coverage": {"by_category": {"prose": {"represented_pct": 90.0}}},
    }
    package.update(overrides)
    return package


def _write(directory: Path, stem: str, package: dict, review: dict | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}.detailed-knowledge.json"
    path.write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
    if review is not None:
        (directory / f"{stem}.independent-review.json").write_text(
            json.dumps(review, ensure_ascii=False), encoding="utf-8"
        )
    return path


# -- reachability -----------------------------------------------------------


def test_a_step_a_claim_names_is_reachable_and_one_no_claim_names_is_not():
    package = _package(
        evidence_steps=[
            {"evidence_step_id": "E-1"},
            {"evidence_step_id": "E-2"},
        ],
        claims=[{"claim_id": "CL-1", "evidence_step_ids": ["E-1"]}],
    )
    assert reachable_step_ids(package) == {"E-1"}
    assert stranded_records(package)["steps"] == ["E-2"]


def test_a_step_reached_only_by_a_retired_claim_is_stranded():
    """A claim retired by an accepted duplicate finding asserts nothing, so what
    only it reached is not delivered -- and counting it as delivered would let a
    resolved duplicate keep vouching for the evidence behind it."""

    package = _package(
        evidence_steps=[{"evidence_step_id": "E-1"}],
        claims=[{"claim_id": "CL-1", "evidence_step_ids": ["E-1"], "superseded_by": "CL-2"}],
    )
    assert reachable_step_ids(package) == set()
    assert stranded_records(package)["steps"] == ["E-1"]


def test_an_observation_feeding_an_orphaned_step_is_still_stranded():
    """The step is in the package and the relation is recorded, but no claim
    reaches the step, so authoring never arrives at either of them."""

    package = _package(
        source_fragments=[_fragment("FR-1", "ψυχή 可譯作生命。")],
        observations=[{"observation_id": "OBS-1", "source_fragment_ids": ["FR-1"]}],
        evidence_steps=[{"evidence_step_id": "E-1", "source_fragment_ids": ["FR-2"]}],
        knowledge_relations=[{"from_id": "OBS-1", "to_id": "E-1", "relation_type": "contextualizes"}],
    )
    stranded = stranded_records(package)
    assert stranded["steps"] == ["E-1"]
    assert stranded["observations"] == ["OBS-1"]


def test_an_observation_feeding_a_reachable_step_is_not_stranded():
    package = _package(
        source_fragments=[_fragment("FR-1", "ψυχή 可譯作生命。")],
        observations=[{"observation_id": "OBS-1", "source_fragment_ids": ["FR-1"]}],
        evidence_steps=[{"evidence_step_id": "E-1", "source_fragment_ids": ["FR-2"]}],
        claims=[{"claim_id": "CL-1", "evidence_step_ids": ["E-1"]}],
        knowledge_relations=[{"from_id": "OBS-1", "to_id": "E-1", "relation_type": "contextualizes"}],
    )
    assert stranded_records(package) == {"steps": [], "observations": []}


# -- sound ------------------------------------------------------------------


def test_sound_is_the_pass_rate_of_this_package_s_own_review(tmp_path: Path):
    path = _write(
        tmp_path, "A-1234",
        _package(claims=[{"claim_id": "DK-1234-CL001"}, {"claim_id": "DK-1234-CL002"}]),
        {"claim_reviews": [
            {"claim_id": "DK-1234-CL001", "decision": "pass"},
            {"claim_id": "DK-1234-CL002", "decision": "changes_suggested", "issues": ["anchor 不支持"]},
        ]},
    )
    measured = measure_package(path)
    assert measured.sound == 0.5
    assert measured.sound_unavailable is None
    assert [failure["claim_id"] for failure in measured.sound_failures] == ["DK-1234-CL002"]


def test_a_review_of_another_run_yields_no_score_rather_than_a_wrong_one(tmp_path: Path):
    """Directories hold one run's outputs together, but a stale review left
    beside a re-extraction would otherwise be reported as this package's pass
    rate -- the quiet lie the health view exists to catch."""

    path = _write(
        tmp_path, "A-1234",
        _package(claims=[{"claim_id": "DK-1234-CL001"}]),
        {"claim_reviews": [{"claim_id": "DK-9999-CL001", "decision": "pass"}]},
    )
    measured = measure_package(path)
    assert measured.sound is None
    assert measured.sound_unavailable == "複審檔對應的是另一次抽取"


def test_a_review_in_the_sibling_reviews_directory_is_found(tmp_path: Path):
    path = _write(tmp_path / "detailed-extractions", "A-1234",
                  _package(claims=[{"claim_id": "DK-1234-CL001"}]))
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    (reviews / "A-1234.independent-review.json").write_text(
        json.dumps({"claim_reviews": [{"claim_id": "DK-1234-CL001", "decision": "pass"}]}), encoding="utf-8"
    )
    assert measure_package(path).sound == 1.0


# -- one document, several runs ---------------------------------------------


def test_a_re_extracted_document_is_measured_once_at_its_newest_run(tmp_path: Path):
    old = _write(tmp_path / "v1", "A-1234", _package(
        evidence_steps=[{"evidence_step_id": "E-1"}, {"evidence_step_id": "E-2"}],
        claims=[{"claim_id": "DK-1234-CL001", "evidence_step_ids": ["E-1"]}],
    ))
    new_package = _package(
        evidence_steps=[{"evidence_step_id": "E-1"}, {"evidence_step_id": "E-2"}],
        claims=[{"claim_id": "DK-1234-CL001", "evidence_step_ids": ["E-1", "E-2"]}],
    )
    new_package["extraction"]["generated_at"] = "2026-08-20T00:00:00+00:00"
    new = _write(tmp_path / "v2", "A-1234", new_package)

    current = latest_per_document([measure_package(old), measure_package(new)])
    assert len(current) == 1
    assert current[0].path == new
    assert current[0].stranded == 0


# -- distribution -----------------------------------------------------------


def test_too_few_documents_to_rank_names_nobody():
    dist = distribution([0.1, 0.9, 0.4], "high")
    assert dist.cutoff is None
    assert dist.median == 0.4


def test_a_uniform_corpus_has_nothing_to_be_worse_than():
    dist = distribution([0.5] * 12, "high")
    # The cutoff lands on the median itself, and `_outliers` refuses a value
    # that only equals it -- otherwise the page would cry wolf on its
    # quietest possible day.
    assert dist.cutoff == 0.5
    assert dist.median == 0.5


def test_the_cutoff_is_the_corpus_s_own_ninetieth_percentile():
    dist = distribution([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0], "high")
    assert dist.cutoff == 0.9
    low = distribution([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0], "low")
    assert low.cutoff == 0.1


# -- the report -------------------------------------------------------------


def _corpus_package(stem: str, source_id: str, stranded_steps: int) -> dict:
    package = _package(
        source_documents=[{
            "source_id": f"SRC-{source_id}",
            "source_type": "sermon_transcript",
            "source_path": f"/data/script_published/{source_id}.json",
            "title": source_id,
        }],
        evidence_steps=[{"evidence_step_id": f"DK-{stem}-E{index}"} for index in range(10)],
        claims=[{
            "claim_id": f"DK-{stem}-CL001",
            "evidence_step_ids": [f"DK-{stem}-E{index}" for index in range(10 - stranded_steps)],
        }],
    )
    return package


def test_the_report_counts_what_was_never_run_as_loudly_as_what_was(tmp_path: Path):
    """A corpus of ten with one measured document is not a healthy corpus, and
    a page that divided the measured by themselves would say it was."""

    _write(tmp_path, "A-1234", _corpus_package("1234", "A", stranded_steps=1))
    corpus = [Document(f"S{index}", f"講道 {index}", "sermon_transcript") for index in range(10)]
    corpus.append(Document("A", "講道 A", "sermon_transcript"))

    report = build_report(staging_root=tmp_path, corpus=corpus)
    assert report["corpus"]["documents"] == 11
    assert report["corpus"]["measured"] == 1
    assert report["corpus"]["never_extracted"] == 10


def test_a_metric_nobody_can_measure_yet_says_so_instead_of_reading_as_clean(tmp_path: Path):
    _write(tmp_path, "A-1234", _corpus_package("1234", "A", stranded_steps=1))
    report = build_report(staging_root=tmp_path, corpus=[Document("A", "講道 A", "sermon_transcript")])
    reachable = next(metric for metric in report["metrics"] if metric["name"] == "reachable")
    assert reachable["state"] == "pending"
    assert "#148" in reachable["pending_reason"]
    assert reachable["values"] == []


def test_the_worst_document_is_named_with_a_sentence_and_a_link(tmp_path: Path):
    for index in range(12):
        source_id = f"S{index:02d}"
        stem = f"{index:04x}aaaaaaaa"
        _write(tmp_path / source_id, f"{source_id}-{stem}",
               _corpus_package(stem, source_id, stranded_steps=9 if index == 0 else 1))

    corpus = [Document(f"S{index:02d}", f"講道 {index}", "sermon_transcript") for index in range(12)]
    report = build_report(staging_root=tmp_path, corpus=corpus)

    assert report["corpus"]["needs_attention"] == 1
    exception = report["exceptions"][0]
    assert exception["document_id"] == "S00"
    reason = exception["reasons"][0]
    assert reason["metric"] == "stranded"
    assert "9 條記錄走不到" in reason["sentence"]
    assert reason["link"]["href"] == "/admin/wang/argument-layer?source=0000aaaaaaaa&only=stranded"
