"""他念到哪一节——按他念的字认，不按他报的节号认。

他报的常常是预告。（五）1 的 2:15 他说「我再念一下，然後我特別來看十九節那一
段」，接着从十七节念起；照报的走，2:24 会显示十九节，而他正念着「乃是我在天上
的父指示你的」，十七节。
"""

from __future__ import annotations

from backend.pipeline.original_audio_index import verse_readings


def _sermon(text: str) -> dict:
    # 一段一个时间点，从 0 秒起；`_timeline` 按字数插值。
    return {"segments": [{"index": "1", "text": text, "start_time": 0}]}


def test_he_is_placed_where_he_reads_the_verse() -> None:
    sermon = _sermon(
        "十六節說，西門彼得回答說：「你是基督，是永生神的兒子。」"
        "好，我再念一下，然後我特別來看十九節那一段。"
        "耶穌對他說：「西門巴約拿，你是有福的！因為這不是屬血氣的指示你的，"
        "乃是我在天上的父指示你的。」"
    )
    verses = {
        "mat-16-16": "西門彼得回答說：「你是基督，是永生神的兒子。」",
        "mat-16-17": "耶穌對他說：「西門巴約拿，你是有福的！因為這不是屬血肉的指示你的，乃是我在天上的父指示的。」",
        "mat-16-19": "我要把天國的鑰匙給你，凡你在地上所捆綁的，在天上也要捆綁。",
    }
    found = verse_readings(sermon, verses)
    slugs = [slug for _, slug in found]
    # 他念了十六节和十七节；十九节只报了没念，所以不在里面。
    assert "mat-16-16" in slugs
    assert "mat-16-17" in slugs
    assert "mat-16-19" not in slugs
    # 十六节在前，十七节在后。
    assert min(at for at, s in found if s == "mat-16-16") < min(
        at for at, s in found if s == "mat-16-17"
    )


def test_a_word_he_changed_still_matches() -> None:
    """和合本「屬血肉」他念成「屬血氣」，八个字的窗口照样对得上。"""

    sermon = _sermon("因為這不是屬血氣的指示你的，乃是我在天上的父指示你的。")
    verses = {"mat-16-17": "因為這不是屬血肉的指示你的，乃是我在天上的父指示的。"}
    # 一节会对上好几个窗口，各带自己的时间；这里只关心认出了哪一节。
    assert {slug for _, slug in verse_readings(sermon, verses)} == {"mat-16-17"}


def test_a_verse_he_never_reads_is_not_placed() -> None:
    """没逐字念的就不认——由主张给的那一节兜底，不猜。"""

    sermon = _sermon("我們今天要看的這段經文，講的是彼得的認信。")
    verses = {"mat-16-16": "西門彼得回答說：「你是基督，是永生神的兒子。」"}
    assert verse_readings(sermon, verses) == []


def test_punctuation_does_not_block_the_match() -> None:
    """他念经文不照标点念，逐字稿的标点也是校对时加的。"""

    sermon = _sermon("耶穌說我要把天國的鑰匙給你凡你在地上所捆綁的")
    verses = {"mat-16-19": "我要把天國的鑰匙給你，凡你在地上所捆綁的，在天上也要捆綁；"}
    assert {slug for _, slug in verse_readings(sermon, verses)} == {"mat-16-19"}
