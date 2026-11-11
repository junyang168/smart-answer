from backend.pipeline.transcript_proofreading_layout import proofreading_blocks


def test_reference_and_answer_can_cross_short_pauses_without_changing_cues():
    cues = [
        dict(id="0-0", start=1, end=2, speaker="a", text="以弗所書"),
        dict(id="0-1", start=4.4, end=10, speaker="a", text="第五章，講的是什麼？"),
        dict(id="0-2", start=12.3, end=14, speaker="b", text="~~哦。~~彼此順服。"),
    ]
    blocks = proofreading_blocks(cues)
    assert len(blocks) == 1
    assert blocks[0] == {"start": 1, "end": 14, "cues": cues}


def test_does_not_join_across_long_pause_or_create_overlong_paragraph():
    cues = [
        dict(id="0-0", start=0, end=23, text="這是一句完整而且足夠長的獨立內容，現在講完。"),
        dict(id="0-1", start=26, end=34, text="接著。"),
        dict(id="1-0", start=42, end=44, text="另外。"),
    ]
    assert [block["cues"] for block in proofreading_blocks(cues)] == [[cue] for cue in cues]
