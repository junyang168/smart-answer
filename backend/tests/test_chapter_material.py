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


def timed(claim_id: str, refs: list[str], minute: float):
    item = claim(claim_id, refs)
    item["occurrences"][0]["anchors"] = [{"media_time": minute * 60}]
    return item


def test_one_hop_must_be_said_near_a_seed_in_the_same_lecture():
    # （二）6：「解經必須運用理性」在讲太 6 试探时说，离结构那条起点 23.7 分。
    chosen = run(
        [timed("seed", ["馬太福音5:3"], 68), timed("far", [], 32), timed("near", [], 62)],
        [relation("applies", "far", "seed"), relation("supports", "near", "seed")],
    )
    assert set(chosen) == {"seed", "near"}


def test_nearness_is_to_any_seed_not_only_the_linked_one():
    # 雅各书那条连着 12 分的起点，可 51–53 分他正讲着 5:28。
    chosen = run(
        [
            timed("early", ["馬太福音5:28"], 12),
            timed("late", ["馬太福音5:28"], 52),
            timed("james", ["雅各書1:14-15"], 54.5),
        ],
        [relation("supports", "james", "early")],
    )
    assert "james" in chosen


def test_a_verse_belongs_to_the_section_where_it_starts():
    chapter = chapter_material.CHAPTERS["mat-5-1-48"]
    assert chapter_material.section_of(chapter, "mat-5-28") == "mat-5-27-30"
    assert chapter_material.section_of(chapter, "mat-5-39-44") == "mat-5-38-42"
    assert chapter_material.section_of(chapter, "mat-5-17-48") == "mat-5-17-20"


def test_the_same_minutes_belong_to_the_narrower_section_only():
    from backend.pipeline.original_audio_index import exclusive_minutes

    def row(stretches, marks):
        return {
            "transcript_id": "L1",
            "stretches": [{"start": a, "end": b} for a, b in stretches],
            "judgements": [{"at": at} for at in marks],
            "seconds": sum(b - a for a, b in stretches),
        }

    wide = {"sermons": [row([(60, 1200)], [100, 200, 1000])]}
    narrow = {"sermons": [row([(180, 270)], [190])]}
    exclusive_minutes([wide, narrow])
    assert wide["sermons"][0]["stretches"] == [{"start": 60, "end": 180}, {"start": 270, "end": 1200}]
    assert [m["at"] for m in wide["sermons"][0]["judgements"]] == [100, 1000]
    assert narrow["sermons"][0]["stretches"] == [{"start": 180, "end": 270}]


def test_superseded_claims_are_left_out_whatever_else_their_status():
    chosen = run(
        [
            claim("old", ["馬太福音5:28"], review_status="superseded", superseded_by="new"),
            claim("new", ["馬太福音5:28"], review_status="candidate"),
            claim("flagged", ["馬太福音5:28"], review_status="human_review_required"),
        ],
        [],
    )
    assert set(chosen) == {"new", "flagged"}
