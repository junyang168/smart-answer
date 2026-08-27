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
