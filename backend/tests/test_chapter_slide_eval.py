import json

from backend.pipeline import chapter_slide_eval as ev

KEPT = [{"id": "U1", "start": 0.0, "end": 60.0,
         "text": "我們看五章三節。虛心的人有福了，因為天國是他們的。那個是字原文是現在式。"}]


def bible(tmp_path):
    root = tmp_path / "cuv" / "MAT"
    root.mkdir(parents=True, exist_ok=True)
    (root / "5.json").write_text(json.dumps({"3": "虛心的人有福了！因為天國是他們的。", "4": "哀慟的人有福了！因為他們必得安慰。"},
                                            ensure_ascii=False))
    return ev.Bible(tmp_path / "cuv")


def one_slide(scriptures, notes=(), points=("一", "二")):
    return [{"first": "S0001", "last": "S0003", "title": "八福", "scriptures": list(scriptures),
             "inline_notes": list(notes), "points": [{"sentence": "S0001", "text": t} for t in points]}]


def test_a_verse_he_reads_must_be_on_screen_when_he_reads_it(tmp_path):
    missing = ev.evaluate(one_slide([]), KEPT, candidates=["mat-5-1-200"], bible=bible(tmp_path))["problems"]
    assert any("他在念 mat-5-3" in p for p in missing)
    shown = ev.evaluate(one_slide([{"sentence": "S0001", "slug": "mat-5-3-4"}],
                                  notes=[{"original": "ἐστιν", "explanation": "現在式"}]),
                        KEPT, candidates=["mat-5-1-200"], bible=bible(tmp_path))["problems"]
    assert shown == []


def test_original_language_he_explains_must_be_on_the_slide(tmp_path):
    problems = ev.evaluate(one_slide([{"sentence": "S0001", "slug": "mat-5-3"}]), KEPT,
                           candidates=["mat-5-1-200"], bible=bible(tmp_path))["problems"]
    assert problems == ["八福（S0003）: 他在講原文或文法，這一張沒有原文註解或相應要點"]
    noted = one_slide([{"sentence": "S0001", "slug": "mat-5-3"}], points=("一", "「是」是現在式"))
    assert ev.evaluate(noted, KEPT, candidates=["mat-5-1-200"], bible=bible(tmp_path))["problems"] == []


def test_reading_exemption_requires_the_verse_to_be_displayed_already(tmp_path):
    from backend.pipeline.chapter_slide_eval import Bible,displayed_reading_sentences
    book=tmp_path/'MAT';book.mkdir()
    text='虛心的人有福了因為天國是他們的'
    (book/'5.json').write_text(json.dumps({'3':text}))
    lines=[{'id':'S0001','text':text},{'id':'S0002','text':text},
           {'id':'S0003','text':'這一節的解釋需要特別留意上下文和神學觀念，不能讀進自己的意思。'}]
    slides=[{'first':'S0001','scriptures':[{'sentence':'S0002','slug':'mat-5-3'}]}]
    assert displayed_reading_sentences(slides,lines,bible=Bible(tmp_path))=={'S0002'}


def test_story_phrase_is_not_a_scripture_reading(tmp_path):
    book = tmp_path / "JOS"
    book.mkdir()
    verse = "那些人就往約但河的渡口追趕他們去了。追趕他們的人一出去，城門就關了。"
    (book / "2.json").write_text(json.dumps({"7": verse}, ensure_ascii=False))
    bible = ev.Bible(tmp_path)
    passage = ["jos-2-7"]
    kept = [{"id": "U1", "start": 0, "end": 40, "text": "可是他們出去，城門就關了。"}]
    assert ev.evaluate(one_slide([]), kept, candidates=passage, bible=bible)["problems"] == []
    kept[0]["text"] = verse
    assert ev.evaluate(one_slide([]), kept, candidates=passage, bible=bible)["problems"]


def test_slide_eval_checks_the_required_diagram_in_its_retained_claim_scope(tmp_path):
    lecture = "2011 NYSC 專題：馬太福音釋經（二）1"
    figure = {"sha256": "d" * 64, "title": "結構", "layout": "overview",
              "kind": "professor", "origin": "原圖", "svg": "<svg/>", "labels": ["結構"]}
    context = {"lecture": lecture, "diagram_contract": "claim-bound.v1", "claims": [
        {"claim_id": "G01-CL0001", "stated": "S0002", "diagrams": [figure]},
    ]}
    slides = one_slide([], points=("原文是現在式", "二"))
    library = bible(tmp_path)
    assert any("論證圖缺漏" in p for p in ev.evaluate(
        slides, KEPT, candidates=[], bible=library, context=context)["problems"])
    slides[0]["diagram_ref"] = figure["sha256"]
    assert ev.evaluate(slides, KEPT, candidates=[], bible=library, context=context)["problems"] == []
    context["claims"][0]["stated"] = "S0100"
    assert any("沒有連到本張" in p for p in ev.evaluate(
        slides, KEPT, candidates=[], bible=library, context=context)["problems"])
    slides[0].pop("diagram_ref")
    assert ev.evaluate(slides, KEPT, candidates=[], bible=library, context=context)["problems"] == []


def test_split_finding_depends_on_first_fully_supported_sentence_of_each_part():
    from backend.pipeline.chapter_slide_point_timing import evaluate_points

    source = [{"id": sid, "text": text, "start": start} for sid, text, start in [
        ("S0001", "下一段講什麼？", 0), ("S0002", "第五章十三節起是門徒的使命。", 6),
        ("S0003", "認識神後有生命上的反應。", 19),
        ("S0004", "這些是門徒的使命及認識神後的反應。", 25)]]
    packet = {"slides": [{"first": "S0001", "last": "S0004", "points": [
        {"sentence": "S0003", "text": "門徒的使命和認識神後的反應"}]}], "source": source}
    candidates = [{"slide": "S0001", "sentence": "S0003", "parts": ["門徒的使命", "認識神後的反應"]}]

    def run(first, second, split="independent"):
        supported = {f"0:0:{first}", f"0:1:{second}", "0:0:S0004", "0:whole:S0003"}
        if second is not None:
            supported.add("0:1:S0004")  # A later restatement must not hide the first full sentence.
        calls = []

        def ask(state, questions):
            calls.append(questions)
            return {key: split if key.endswith(":split") else "full" if key in supported
                    else "question" if key.endswith("S0001") else "partial"
                    for key in questions}

        result = evaluate_points(packet, candidates, ask, model="jev-test", max_bytes=2000)
        assert len(calls) > 1 and sum(map(len, calls)) == 13
        return result

    finding, = run("S0002", "S0003")
    assert "S0002「門徒的使命」" in finding["evidence"] and "S0003「認識神後的反應」" in finding["evidence"]
    assert finding["status"] == "split"
    assert run("S0002", "S0002")[0]["status"] == "correct"  # One sentence expressed both.
    assert run("S0002", None)[0]["status"] == "correct"  # The suggested split has an unsupported part.
    assert run("S0002", "S0003", "dependent")[0]["status"] == "correct"  # One meaning.


def test_all_points_are_checked_per_slide_even_without_split_candidates():
    from backend.pipeline.chapter_slide_point_timing import evaluate_points

    packet = {
        "source": [{"id": f"S000{i}", "text": f"原文{i}。", "start": i * 10} for i in range(1, 6)],
        "slides": [
            {"first": "S0001", "last": "S0003", "points": [
                {"sentence": "S0001", "text": "這句是答案。"},
                {"sentence": "S0003", "text": "這句是問題嗎？"},
            ]},
            {"first": "S0004", "last": "S0005", "points": [
                {"sentence": "S0004", "text": "當時已經講出這個意思。"},
                {"sentence": "S0005", "text": "原文沒有提到的結論。"},
            ]},
        ],
    }
    seen = []

    def ask(state, questions):
        ids = set(state["原文"])
        seen.append(ids)
        assert {key.rsplit(":", 1)[1] for key in questions} == ids
        if "S0001" in ids:
            assert "提問不算回答" in questions["0:whole:S0001"]["instructions"]
            assert "忽略問題的答案" in questions["1:whole:S0001"]["instructions"]
            full = {"0:whole:S0002", "1:whole:S0001", "1:whole:S0003"}
        else:
            full = {"0:whole:S0004"}
        return {key: "full" if key in full else "other" for key in questions}

    rows = evaluate_points(packet, [], ask, model="jev", max_bytes=128000)
    assert seen == [{"S0001", "S0002", "S0003"}, {"S0004", "S0005"}]
    assert [(item["status"], item.get("expected_sentence"), item.get("delta_seconds")) for item in rows] == [
        ("early", "S0002", -10), ("late", "S0001", 20), ("correct", "S0004", 0), ("unresolved", None, None),
    ]


def test_verbatim_points_skip_jev_but_can_still_have_an_earlier_paraphrase():
    from backend.pipeline.chapter_slide_point_timing import evaluate_points

    source = [
        {"id": "S0001", "text": "你們的任務便是發光。", "start": 0},
        {"id": "S0002", "text": "門徒的使命就是要發光。", "start": 10},
    ]
    packet = {"source": source, "slides": [{
        "first": "S0001", "last": "S0002", "points": [
            {"sentence": "S0002", "text": "門徒的使命就是要發光"},
        ],
    }]}

    def ask(state, questions):
        assert set(questions) == {"0:whole:S0001"}
        return {"0:whole:S0001": "full"}

    row, = evaluate_points(packet, [], ask, model="jev", max_bytes=128000)
    assert (row["status"], row["expected_sentence"]) == ("late", "S0001")

    packet["source"] = source[1:]
    packet["slides"][0]["first"] = "S0002"

    def no_call(*_):
        raise AssertionError("a verbatim-only check should make no call")

    row, = evaluate_points(packet, [], no_call, model="jev", max_bytes=128000)
    assert (row["status"], row["expected_sentence"]) == ("correct", "S0002")


def test_matthew_conclusion_timing_keeps_reference_and_statement_distinctions():
    from backend.pipeline.chapter_slide_point_timing import evaluate_points

    def check(text, original, at, answers, verify):
        source = [{"id": f"S{i:04d}", "text": line, "start": i * 10}
                  for i, line in enumerate(original, 1)]
        packet = {"source": source, "slides": [{
            "first": source[0]["id"], "last": source[-1]["id"],
            "points": [{"sentence": f"S{at:04d}", "text": text}],
        }]}

        def ask(_, questions):
            verify(questions)
            return {key: answers[int(key[-4:]) - 1] for key in questions}

        row, = evaluate_points(packet, [], ask, model="jev", max_bytes=128000)
        return row

    def quote(questions):
        for q in questions.values():
            assert "七章十二節" in q["instructions"] and "律法和先知" in q["instructions"]
            assert "歸給" in q["instructions"]

    def assertion(questions):
        for q in questions.values():
            assert "補主詞" in q["instructions"] and "提問不算回答" in q["instructions"]
            assert "有關" in q["instructions"] and "結論" in q["instructions"]

    row = check("七章十二節末尾的「律法和先知」", [
        "五章十七節：『莫想我來要廢掉律法和先知。』",
        "七章十二節：『因為這就是律法和先知的道理。』",
    ], 2, ["none", "full"], quote)
    assert (row["status"], row["expected_sentence"]) == ("correct", "S0002")

    row = check("門徒的義要勝過文士和法利賽人的義", [
        "所以門徒的義要什麼？", "五章十七節到四十八節，要勝過文士和法利賽人的義。",
    ], 2, ["question", "full"], assertion)
    assert (row["status"], row["expected_sentence"]) == ("correct", "S0002")

    row = check("接著講禱告的問題", [
        "六章一節到七章十一節，講到施捨。", "然後六章五節到第十一節，講到禱告的問題。",
    ], 2, ["none", "full"], assertion)
    assert (row["status"], row["expected_sentence"]) == ("correct", "S0002")

    row = check("7:13-27 是門徒使命的結論", [
        "七章十三節到二十七節又是另一個結論。",
        "跟這裡有關係，就是跟門徒的使命有關係。",
        "就講到整個門徒的使命的結論。",
    ], 2, ["partial", "partial", "full"], assertion)
    assert (row["status"], row["expected_sentence"]) == ("early", "S0003")
