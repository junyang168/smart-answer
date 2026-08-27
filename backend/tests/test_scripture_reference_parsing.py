"""认经文引用：书名、章节、繁简。

两个 bug 是做太16:18-19 的原声幻灯时撞上的，都藏在「章号有没有两位」这条线
后面——单位数章号一直是好的，所以没人看出来。
"""

from __future__ import annotations

import pytest

from backend.api.scripture import (
    convert_parenthetical_references,
    parse_reference,
    reference_slugs,
)


@pytest.mark.parametrize(
    "text, expected",
    [
        # 两位数章号。原来书名的字符类里含数字，贪婪的书名吃掉 `1`，剩下 `6` 当
        # 章号，`馬太福音1` 查不到书卷，整条引用就不转链接了。
        ("馬太福音16:19", ["mat-16-19"]),
        ("馬太福音16:18-23", ["mat-16-18-23"]),
        # 单位数章号一直是好的——这就是这个 bug 藏了很久的原因。
        ("馬太福音8:22", ["mat-8-22"]),
        # 数字仍要能开头，不然「彼得後書」「約翰一書」这些认不出。
        ("彼得後書1:16-18", ["2pe-1-16-18"]),
        ("約翰一書2:1", ["1jn-2-1"]),
        # 缩写、中间带空格的写法。
        ("太 16:18", ["mat-16-18"]),
        ("弗 2:20", ["eph-2-20"]),
        # 简体全名。书名表是繁体的，而观点的 scripture_scope 两种都有。
        ("马太福音16:1-4", ["mat-16-1-4"]),
        ("约翰福音20:23", ["jhn-20-23"]),
        # 一条 scope 里列着好几处。
        ("約翰福音20:23、馬太福音16:19、馬太福音18:18", ["jhn-20-23", "mat-16-19", "mat-18-18"]),
        # 不是节级引用的，一条都不认——幻灯保持上一张，不是报错。
        ("聖經", []),
        ("馬太福音", []),
        ("使徒行傳15章", []),
    ],
)
def test_reference_slugs(text: str, expected: list[str]) -> None:
    assert reference_slugs(text) == expected


def test_cross_chapter_range_does_not_become_a_wrong_verse() -> None:
    """`16:28-17:2` 跨章，这条正则处理不了，那就只认前半条。

    不加防备的话结尾的 `17` 会被当成节号，链接指向「16:28-17」——第 17 节根本
    不是它的意思。宁可少认，不可认错。
    """

    assert reference_slugs("馬太福音16:28-17:2") == ["mat-16-28"]
    assert convert_parenthetical_references("（馬太福音16:28-17:2）") == (
        "（[馬太福音 16:28](#scripture-mat-16-28)-17:2）"
    )


def test_parenthetical_reference_becomes_a_link() -> None:
    assert convert_parenthetical_references("（馬太福音16:19）") == (
        "[馬太福音 16:19](#scripture-mat-16-19)"
    )


def test_slugs_round_trip_through_parse_reference() -> None:
    """`reference_slugs` 产出的 slug，`parse_reference` 要吃得下。

    幻灯和文章页都靠这一步：slug 进去，API.Bible 的 passage id 出来。
    """

    single = parse_reference("mat-16-18")
    assert (single["osis_start"], single["start"], single["end"]) == ("Matt.16.18", 18, 18)

    span = parse_reference("mat-16-18-19")
    assert (span["osis_start"], span["osis_end"], span["display"]) == (
        "Matt.16.18",
        "Matt.16.19",
        "Matt 16:18-19",
    )


def test_single_verse_passage_id_is_not_a_bare_chapter_verse() -> None:
    """单节也要 `MAT.16.18`，不能是 `16:18`。

    `16:18` 不是 API.Bible 的 passage id，接口回空字符串——太16:18 单独一节时
    幻灯上的希腊原文就是空的。
    """

    from backend.api.scripture import passage_id

    assert passage_id("MAT", 16, 18, 18) == "MAT.16.18"
    assert passage_id("MAT", 16, 18, 19) == "MAT.16.18-MAT.16.19"


# ── 教授口里念的经文 ─────────────────────────────────────────────
#
# 观点的 scripture_scope 说不出「他此刻在念哪一节」——一个观点的证据能横跨十分
# 钟，中间他翻了三处。他自己是念出来的，用中文数字。


@pytest.mark.parametrize(
    "text, expected",
    [
        # 书名简称 + 中文数字。书名贪婪的话会把章号的第一个字「十」吃掉，
        # 「馬太十六章十八節」就成了马太 6:18。
        ("馬太十六章十八節", [(0, "mat-16-18")]),
        ("馬太十五章三十七節", [(0, "mat-15-37")]),
        # 「第」不能算进书名，否则「以弗所書第四章」查不到书卷。
        ("以弗所書第四章第十一節", [(0, "eph-4-11")]),
        ("約翰福音二十章二十三節", [(0, "jhn-20-23")]),
        # 书名号夹在书名和章号之间。不放行就退回上一次的书卷，帖前 5:23 会被
        # 显示成别的书卷 5:23。
        ("《帖撒羅尼迦前書》五章二十三節", [(1, "1th-5-23")]),
        # 阿拉伯数字也认。
        ("馬太16章19節", [(0, "mat-16-19")]),
    ],
)
def test_spoken_references(text: str, expected: list[tuple[int, str]]) -> None:
    from backend.api.scripture import spoken_references

    assert spoken_references(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("請各位看以弗所書第四章第十一節", "eph-4-11"),
        ("赦免「哪一種人」的罪約翰福音二十章二十三節", "jhn-20-23"),
        ("這裡，雖然馬太十六章十八節", "mat-16-18"),
    ],
)
def test_book_is_found_under_the_tail_of_the_previous_sentence(text: str, expected: str) -> None:
    """前面粘着上一句是常事，从最长的后缀往短了试。

    只查书卷，不查字位置——贪婪的书名会往前多吃几个字，多吃几个字在录音里约等于
    一秒，不值得钉成契约。
    """

    from backend.api.scripture import spoken_references

    assert [slug for _, slug in spoken_references(text)] == [expected]


def test_spoken_book_carries_forward_until_he_changes_book() -> None:
    """讲一段以弗所书时他会省掉书名，只说「第二章二十節」。"""

    from backend.api.scripture import spoken_references

    text = "以弗所書第四章第十一節……只有第四章第十一節有……可是第二章二十節……馬太十六章十八節"
    assert [slug for _, slug in spoken_references(text)] == [
        "eph-4-11",
        "eph-4-11",
        "eph-2-20",
        "mat-16-18",
    ]


def test_single_character_book_is_not_taken_from_the_tail_of_a_longer_name() -> None:
    """「書」单独一个字是约书亚记的缩写，但在连着的讲话里不是书名。

    不挡的话「帖撒羅尼迦前書五章二十三節」的后缀搜索会一路退到「書」，认成约书
    亚记——跟原话差了三十多卷。
    """

    from backend.api.scripture import spoken_references

    assert spoken_references("帖撒羅尼迦前書五章二十三節") == [(0, "1th-5-23")]
    assert spoken_references("歌羅西書四章十八節") == [(0, "col-4-18")]


def test_chinese_number() -> None:
    from backend.api.scripture import chinese_number

    assert chinese_number("二十三") == 23
    assert chinese_number("十") == 10
    assert chinese_number("十八") == 18
    assert chinese_number("一百二十") == 120
    assert chinese_number("16") == 16
    assert chinese_number("使徒") is None
