"""一章的材料从主张数据算出来：起点主张、平行经文、往外一步。"""

from backend.pipeline import chapter_material
from backend.pipeline.chapter_material import Chapter, select
from backend.pipeline.original_audio_index import claim_points, claim_verse, in_passage


LECTURE = "2011 NYSC 專題：馬太福音釋經（二）3"
OTHER = "2017 NYSC 專題：馬太福音釋經（六）1"

CHAPTER = Chapter(
    passage="mat-5-1-48",
    series="2011 NYSC 專題：馬太福音釋經（二）",
    lectures=(LECTURE,),
    parallels=(("mat-5-31-32", ("mat-19-3-9", "luk-16-18")),),
)


def claim(claim_id: str, refs: list[str], lecture: str = LECTURE, **extra):
    return {
        "claim_id": claim_id,
        "statement": f"主張 {claim_id}",
        "scripture_refs": refs,
        "occurrences": [{"transcript_id": lecture}],
        **extra,
    }


def relation(kind: str, source: str, target: str):
    return {"relation_type": kind, "from_id": source, "to_id": target}


def run(claims, relations):
    return select(
        CHAPTER,
        {c["claim_id"]: c for c in claims},
        relations,
        in_passage,
        claim_verse,
    )


def test_seed_is_a_claim_whose_reference_lands_in_the_chapter():
    chosen = run([claim("seed", ["馬太福音5:28"]), claim("james", ["雅各書1:14-15"])], [])
    assert chosen == {"seed": "mat-5-28"}


def test_theology_derived_from_the_verse_comes_in_one_hop_with_the_seeds_verse():
    # 他讲 5:28 时带进的雅各书：引用落不进太 5，关系里记着它支持 5:28 的判断。
    chosen = run(
        [claim("seed", ["馬太福音5:28"]), claim("james", ["雅各書1:14-15"])],
        [relation("supports", "james", "seed")],
    )
    assert chosen == {"seed": "mat-5-28", "james": "mat-5-28"}


def test_claims_without_a_relation_stay_out():
    # （二）3 的三段见证与任何太 5 的主张都没有关系。
    chosen = run(
        [claim("seed", ["馬太福音5:28"]), claim("testimony", [])],
        [],
    )
    assert "testimony" not in chosen


def test_only_one_hop():
    # 两步会把（二）6 讲太 6 试探的一段间接拉进来。
    chosen = run(
        [claim("seed", ["馬太福音5:3"]), claim("method", []), claim("temptation", ["哥林多前書10:13"])],
        [relation("applies", "method", "seed"), relation("supports", "temptation", "method")],
    )
    assert set(chosen) == {"seed", "method"}


def test_refutes_is_not_followed():
    chosen = run(
        [claim("seed", ["馬太福音5:28"]), claim("opposed", [])],
        [relation("refutes", "seed", "opposed")],
    )
    assert "opposed" not in chosen


def test_parallel_passage_counts_as_the_chapter_verse():
    # （二）4 的问答从路加福音 16:18 讲 5:32。
    chosen = run([claim("luke", ["路加福音16:18"]), claim("matt19", ["馬太福音 19:3-9"])], [])
    assert chosen == {"luke": "mat-5-31-32", "matt19": "mat-5-31-32"}


def test_only_the_chapters_own_series_counts():
    # 2017（六）讲太 19 时也讲 5:32，不属于太 5 这一章的系列。
    chosen = run([claim("later", ["馬太福音5:32"], lecture=OTHER)], [])
    assert chosen == {}


def test_matthew_5_is_configured_for_all_six_lectures_of_its_series():
    chapter = chapter_material.CHAPTERS["mat-5-1-48"]
    assert len(chapter.lectures) == 6
    assert all(lecture.startswith(chapter.series) for lecture in chapter.lectures)


def test_points_are_the_claims_own_evidence_steps_with_his_words():
    claims = {
        "c": claim(
            "c",
            ["馬太福音5:3"],
            claim_type="application",
            evidence_step_ids=["q", "s1", "s2", "s3", "s4"],
        )
    }
    steps = {
        "q": {"statement": "問題本身", "step_type": "question"},
        "s1": {"statement": "第一步", "step_type": "literary_context", "source_fragment_ids": ["f1"]},
        "s2": {"statement": "第二步", "step_type": "original_language"},
        "s3": {"statement": "主張 c", "step_type": "reasoning"},
        "s4": {"statement": "第三步", "step_type": "reasoning"},
    }
    fragments = {"f1": {"fragment_id": "f1", "verbatim_excerpt": "他的原話"}}
    out = claim_points({"provenance": {"claim_ids": ["c"]}}, claims, steps, fragments)
    assert [p["text"] for p in out["points"]] == ["第一步", "第二步", "第三步"]
    assert out["points"][0]["quote"] == "他的原話"
    assert out["application"] is True
