"""教授的原声，按中心观点重排。

这一层不产出任何新的文字。它做的只有一件事：把「教授在哪几个地方讲过这个判
断」算出来，每一处给一个能寻址的位置，让读者点进去听他自己讲。

三条规则，都是从材料本身量出来的，不是设计出来的：

**一、一个判断，按讲道分开列。** 跨讲道接起来会跳过半小时——量到的最大空隙是
28 分钟，那半小时里教授在讲别的。分开列就没有跳跃可言。

**二、一篇讲道里的几段，接着播。** 他在同一堂课里翻来覆去讲同一件事是常事，最
多的一条讲了四遍。这些属于同一次讲授，接起来算一行；同一篇内的空隙中位 4 分钟。

**三、不去重。** 同一个判断他在五篇讲道里各讲一遍，而每一遍的理由都不一样——有
的从信仰内容说，有的从希腊文性别说。删掉四遍等于删掉他四个论证，那正是
[D2](../../docs/wang-knowledge-platform/00-overview/solution_architecture.md#d2不用-rag)
说的「漏掉的可能正是他的限定」。要综合版的读者去看文章。

输出是一棵可寻址的树：`观点 × 讲道 × 起止时间`。文章那边将来引用这个地址就能
「听教授自己讲这一段」，不需要重新设计——所以这里不产出页面，只产出地址。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

import httpx
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4

from backend.api.scripture import (
    BIBLE_API_TRANSLATION_ZH,
    BOOK_SLUG_TO_NAME,
    format_chinese_reference,
    parse_reference,
    reference_slugs,
    spoken_references,
)


#: 同一篇讲道里，两段录音相隔多久之内算「接着讲」。
#:
#: 两分钟。跳过一两分钟省不下什么，却让人听见一次断口——原来是 30 秒，21 个空隙
#: 里有 8 个在 30 秒到 2 分之间，每一个都是一次没必要的跳跃。
#:
#: 放到 2 分钟，全篇总共多播 7 分钟，剩下的 13 个断口最小的也有 2 分 24 秒，那
#: 种长度确实是教授岔去讲了别的。再往上到 3 分钟能再吃掉 4 个，但要多播 10 分
#: 钟——两分半的无关内容坐着听已经久了。
CONTIGUOUS_GAP_SECONDS = 120.0

#: 一个判断至少在幻灯上停多久。
#:
#: 不设这条，标题比原来的经文翻得还碎：（五）1 十七分钟里换 18 次，有的隔 4 秒。
#: 教授论证时几个判断是缠在一起讲的，一句话同时是两个判断的证据，逐条铺开就是
#: 这个样子。
#:
#: 90 秒。撑不满 90 秒的判断不值得让标题跳一次——它是穿插，不是他转了话题。
MIN_JUDGEMENT_SECONDS = 90.0

#: 算「这一刻主要在讲哪个判断」时，一块看多久。
JUDGEMENT_BLOCK_SECONDS = 30.0

#: 角上那行「他此刻在念」，最多往回认多久。
#:
#: 不设限的话会拿到听的人根本没听到的东西：（四）1 的第一段从 42:29 起播，而上
#: 一处口语引用在 35:30——中间七分钟没有播，屏幕上却写着「他此刻在念 馬太福音
#: 16:13」。两分钟以内才算他还在讲那一处。
SPOKEN_LOOKBACK_SECONDS = 120.0

#: 每段往前留的引子。
#:
#: 引文定位到的是那句话本身，从那一刻起播会掐在半句上——「所以彼得叫一個人進天
#: 堂」前面的「所以」承的是上一句。往前退 8 秒（约 25 字）能接上一句的尾巴。
LEAD_IN_SECONDS = 8.0

#: 一段最短播多久。
#:
#: 按引文长度算，一句话只有五六秒，点开听完还没反应过来就停了。原来是 45 秒，
#: 估的是「他把一个判断说完再举一句例子」的长度。那个估计偏紧：太 16 章四页共
#: 33 段，有 8 段的长度**正好是 45.0 秒**——那不是量出来的长度，是下限本身，只
#: 是说明这几段的引文短到落回下限。整章段长中位 174 秒，45 秒离它太远。
#:
#: 提到 90 秒：不足 1 分钟的段从 8 降到 0，段数一段不变（33），总时长 173 分
#: 变 179 分。120 秒也是 33 段，但要再多播 6 分钟，而 90 秒已经把「点开还没
#: 反应过来就停了」解决掉了。
MIN_STRETCH_SECONDS = 90.0

#: 逐字稿目录的优先顺序，与抽取一致。
TRANSCRIPT_DIRS = ("script_published", "script_review", "script_patched")

#: 媒体文件的位置与 nginx 暴露的路径。
#:
#: 刻意**不读** `metadata.type`：全库 115 篇里 107 篇是 `None`，而现有页面的逻辑
#: 是「不是 audio 就当 mp4」——16:18-19 能用的那七篇 `type` 全是 `None`，磁盘上却
#: 只有 mp3。按文件实际存在与否决定，才不会去要一个不存在的文件。
#: 媒体不在 `DATA_BASE_DIR` 底下，是它的**同级**：`DATA_BASE_DIR` 指
#: `…/church/web/data`，而 mp3／mp4 在 `…/church/web/video`。拼成 `data/video`
#: 会让 35 篇来源全部「没有媒体」，而目录里其实有 350 个 mp4、125 个 mp3。
MEDIA_DIR_NAME = "video"
MEDIA_URL_PREFIX = "/web/video"


class Sermons:
    """磁盘上的讲道：逐字稿段落、时间、以及有没有录音可播。"""

    def __init__(self, data_base_dir: Path) -> None:
        self.root = Path(data_base_dir)
        self._cache: dict[str, dict[str, Any]] = {}

    def _transcript_path(self, document: dict[str, Any]) -> Path | None:
        declared = document.get("source_path")
        if declared and Path(str(declared)).is_file():
            return Path(str(declared))
        transcript_id = str(document.get("transcript_id") or "").strip()
        for directory in TRANSCRIPT_DIRS:
            candidate = self.root / directory / f"{transcript_id}.json"
            if candidate.is_file():
                return candidate
        return None

    def load(self, source_id: str, document: dict[str, Any]) -> dict[str, Any] | None:
        """一篇讲道的段落与媒体。母本没有录音，返回 None。"""

        if source_id in self._cache:
            return self._cache[source_id]
        if str(document.get("source_type") or "") == "notes_manuscript":
            self._cache[source_id] = None
            return None
        path = self._transcript_path(document)
        if path is None:
            self._cache[source_id] = None
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        # 两种格式都有：有的逐字稿是 {"metadata":…, "script":[…]}，有的直接是
        # 段落数组。只认前者会让整篇讲道无声无息地消失。
        script = raw["script"] if isinstance(raw, dict) else raw
        transcript_id = str(document.get("transcript_id") or source_id)
        media = self._media_for(transcript_id)
        full_text = "".join(
            STRIKETHROUGH.sub("", str(segment.get("text") or "")) for segment in (script or [])
        )
        entry = {
            "spoken": spoken_references(full_text),
            "source_id": source_id,
            "transcript_id": transcript_id,
            "title": str(document.get("title") or transcript_id),
            "segments": list(script or []),
            **media,
        }
        self._cache[source_id] = entry
        return entry

    def _media_for(self, transcript_id: str) -> dict[str, Any]:
        directory = self.root.parent / MEDIA_DIR_NAME
        for suffix, kind in ((".mp4", "video"), (".mp3", "audio")):
            candidate = directory / f"{transcript_id}{suffix}"
            if candidate.is_file():
                # 檔名裡有空格和全形冒號（`2016 NYSC 專題：…`）。不編碼就直接
                # 塞進 `src`，瀏覽器不會發出請求，播放器停在 0:00 而且沒有任何
                # 錯誤——產生 URL 的一方負責編碼。
                duration: float | None = None
                try:
                    parsed = MP4(candidate) if suffix == ".mp4" else MP3(candidate)
                    duration = float(parsed.info.length)
                # 一只坏档不能让整段经文的公开接口一起 500。没有读到时前端仍可
                # 播放，只是不在后端宣称已经验证过时长边界。
                except Exception:
                    pass
                return {
                    "media_kind": kind,
                    "media_url": f"{MEDIA_URL_PREFIX}/{quote(transcript_id + suffix)}",
                    "media_duration": duration,
                }
        return {"media_kind": None, "media_url": None, "media_duration": None}


#: 教授给经文里的字作注解时的写法：中文词后面跟着括号，括号里是原文。
#:
#: 「你是彼得(Petrus)」「我的教會建造在這磐石(petra)」「使徒（ἀποστόλων/
#: apostolōn）」。五篇讲道里这样的写法有 25 处。
GLOSS = re.compile(r"(?P<context>[\u4e00-\u9fff]{1,10})\s*[（(]\s*(?P<original>[^（）()]{1,60}?)\s*[）)]")

#: 括号里得是原文，不是随手一个中文注解。
#:
#: 希腊字母，或者一串拉丁字母（Petrus、petra、apostolōn 这类转写）。
ORIGINAL = re.compile(r"^[A-Za-z\u0370-\u03ff\u1f00-\u1fff/\s,·.'\u2019-]{2,60}$")

#: 括号里的有时是语法名称，不是希腊词本身：
#: `ἔσται δεδεμένον就是未來完成時態（Future Perfect Passive）`。把紧挨在前面的
#: 希腊词也原样带出来，页面才能把「词形／教授怎么解释」放在同一张卡上。
GREEK_TERM = re.compile(
    r"[\u0370-\u03ff\u1f00-\u1fff]+(?:\s+[\u0370-\u03ff\u1f00-\u1fff]+){0,3}"
)

#: 英文括注不自动等于「原文讲解」；要有逐字稿自己的语言学信号。否则
#: `完全了解你信心的內涵（implication）` 也会被标成希腊文卡。
ORIGINAL_LANGUAGE_SIGNAL = re.compile(
    r"希[臘腊]文|希伯[來来]文|亞蘭文|亚兰文|原文|文法|語法|语法|時態|时态|"
    r"完成式|未來式|未来式|被動|被动|主動|主动|陽性|阳性|陰性|阴性|中性|"
    r"Greek|Hebrew|Aramaic",
    re.I,
)

#: 校对时删掉的字。`~~…~~` 是软删除，念的时候没念，算字数要先去掉。
STRIKETHROUGH = re.compile(r"~~([^~]+?)~~", re.S)


def _timeline(sermon: dict[str, Any]) -> list[tuple[int, float]]:
    """把逐字稿变成「读到第几个字 → 第几秒」的一串锚点。

    逐字稿的时间是按段落记的，而段落很长：（四）2 整篇 26 段 17 个时间点，一段
    平均 3 分钟，最长的一段（四）3 有 28 分钟。片段的 `media_time` 就是它所在
    段落的段首，所以从段首起播，先听到的是这一段开头在讲的别的事——「捆绑释放的
    权柄不只给彼得」那一组，引用的话在段落第 237 字，而段落头 90 字在讲磐石的
    希腊文，于是点开听到的是磐石。

    段落里的字数可以定位。教授语速稳定（四篇量下来中位 3.0–3.5 字/秒，各篇一
    致，末段起点离录音结束都只差 2–3 分钟），所以「读到第几个字」和「第几秒」
    近似成正比，可以在两个锚点之间线性插值。段落里 26% 的位置就是这一段 26% 的
    时间。

    误差来自语速起伏（同一篇内 2.7–4.1 字/秒），一段 5 分钟的话大约 ±15 秒——
    比整段 5 分钟的误差小一个量级。这不是真正的对齐，真正的对齐要做强制对齐
    （见 #251），这里只是把现有材料用尽。
    """

    cached = sermon.get("_timeline")
    if cached is not None:
        return cached
    anchors: list[tuple[int, float]] = []
    position = 0
    for segment in sermon.get("segments") or []:
        if segment.get("start_time") is not None:
            anchors.append((position, float(segment["start_time"])))
        position += len(STRIKETHROUGH.sub("", str(segment.get("text") or "")))
    sermon["_timeline"] = anchors
    return anchors


def _at(anchors: list[tuple[int, float]], position: int) -> float | None:
    """第 `position` 个字大约在第几秒。"""

    if not anchors:
        return None
    if position <= anchors[0][0]:
        return anchors[0][1]
    for (p0, t0), (p1, t1) in zip(anchors, anchors[1:]):
        if position < p1:
            if p1 == p0:
                return t0
            return t0 + (position - p0) / (p1 - p0) * (t1 - t0)
    # 末段之后：按这一篇自己量出来的语速往后推，没有锚点可插值了。
    (p0, t0) = anchors[-1]
    span = anchors[-1][0] - anchors[0][0]
    seconds = anchors[-1][1] - anchors[0][1]
    rate = span / seconds if seconds > 0 else 3.2
    return t0 + (position - p0) / (rate or 3.2)


def _locate(sermon: dict[str, Any], fragment: dict[str, Any]) -> tuple[int, str] | None:
    """片段在整篇逐字稿里的字位置，以及它自己的原话。"""

    segments = sermon.get("segments") or []
    key = str(fragment.get("paragraph_key") or "")
    match = re.match(r"^S(\d+)$", key)
    index = None
    if match and 1 <= int(match.group(1)) <= len(segments):
        index = int(match.group(1)) - 1
    elif key:
        index = next((i for i, s in enumerate(segments) if str(s.get("index")) == key), None)
    if index is None and fragment.get("source_segment_index") is not None:
        wanted = str(fragment["source_segment_index"])
        index = next((i for i, s in enumerate(segments) if str(s.get("index")) == wanted), None)
    if index is None:
        return None
    before = sum(
        len(STRIKETHROUGH.sub("", str(s.get("text") or ""))) for s in segments[:index]
    )
    text = STRIKETHROUGH.sub("", str(segments[index].get("text") or ""))
    excerpt = str(fragment.get("verbatim_excerpt") or "")
    offset = text.find(excerpt) if excerpt else -1
    return before + max(offset, 0), excerpt


def segment_time(sermon: dict[str, Any], fragment: dict[str, Any]) -> tuple[float, float] | None:
    """一条片段在录音里的起止秒数。

    先按引文在逐字稿里的字位置插值（见 `_timeline`）。定位不到就退回片段自己记
    的 `media_time`——那是它所在段落的段首，会早好几分钟，但总比没有强。
    """

    located = _locate(sermon, fragment)
    anchors = _timeline(sermon)
    if located is not None and anchors:
        position, excerpt = located
        start = _at(anchors, position)
        end = _at(anchors, position + len(excerpt))
        if start is not None:
            return start, end if end is not None else start

    start = fragment.get("media_time")
    if start is not None:
        end = fragment.get("media_end_time")
        return float(start), float(end if end is not None else start)
    return None


def stretch(
    intervals: Iterable[tuple[float, float, str]],
    media_duration: float | None = None,
) -> list[tuple[float, float, str]]:
    """把区间并成「连着讲的几段」，每段记住它主要在讲哪一节。

    相隔 30 秒之内接起来，超过就断开——断开的地方教授在讲别的，接起来听会以为
    是连着的一句话。

    经节跟着时间走。一段录音是某个 focal 观点的证据带出来的，那个观点讲哪一节，
    这一段就显示哪一节。合并之后一段里可能混着几个观点的区间，取占时间最长的那
    个——幻灯只有一张，得挑教授在这一段里主要在讲的那一节。
    """

    limit = media_duration if media_duration is not None and media_duration > 0 else None
    ordered = []
    for raw_start, raw_end, ref in intervals:
        start = max(0.0, raw_start - LEAD_IN_SECONDS)
        if limit is not None and start >= limit:
            # 对齐估算可能落到 EOF 后面。那不是一段可以播放的录音，不能靠伪造一
            # 个 90 秒区间把它救回来。
            continue
        end = max(start, raw_end)
        if limit is not None:
            end = min(end, limit)
        ordered.append((start, end, ref))
    ordered.sort()
    if not ordered:
        return []
    merged: list[dict[str, Any]] = []
    for start, end, ref in ordered:
        if merged and start - merged[-1]["end"] <= CONTIGUOUS_GAP_SECONDS:
            merged[-1]["end"] = max(merged[-1]["end"], end)
        else:
            merged.append({"start": start, "end": end, "weight": {}})
        if ref:
            merged[-1]["weight"][ref] = merged[-1]["weight"].get(ref, 0.0) + (end - start)
    out = []
    for item in merged:
        weight = item["weight"]
        ref = max(weight, key=lambda k: (weight[k], k)) if weight else ""
        finish = max(item["end"], item["start"] + MIN_STRETCH_SECONDS)
        if limit is not None:
            finish = min(finish, limit)
        if finish > item["start"]:
            out.append((item["start"], finish, ref))
    return out


def spoken_during(
    stretches: list[tuple[float, float, str]], sermon: dict[str, Any]
) -> list[dict[str, Any]]:
    """教授在这几段里翻到的经文，按时间排。

    页面按经文聚合，幻灯上的主经文一整段不变。可是他讲着讲着会翻去别处作证：
    「請各位看以弗所書第四章第十一節」「可是以弗所書二章二十節」。五篇讲道里这
    样的口语引用有 88 处（（五）1 一篇就有 61 处）。

    这些只在幻灯角上标一行小字，不换主经文——他整段都在拆 16:18-19 的希腊文，
    翻出去的是旁证。
    """

    spoken = sermon.get("spoken") or []
    anchors = _timeline(sermon)
    if not spoken or not anchors:
        return []
    timed = sorted((_at(anchors, position), slug) for position, slug in spoken)
    marks: dict[float, str] = {}
    for start, end, _ in stretches:
        before = [
            (at, slug)
            for at, slug in timed
            if start - SPOKEN_LOOKBACK_SECONDS <= at <= start
        ]
        if before:
            marks[start] = before[-1][1]
        for at, slug in timed:
            if start < at < end:
                marks[at] = slug
    return [
        {
            "at": at,
            "scripture": marks[at],
            # 中文写法在这边生成——书名表在 `api.scripture`，前端不该再抄一份
            # 六十六卷的对照。
            "label": format_chinese_reference(marks[at]),
        }
        for at in sorted(marks)
    ]


def original_language_during(
    stretches: list[tuple[float, float, str]], sermon: dict[str, Any]
) -> list[dict[str, Any]]:
    """教授在这几段里明确讲了哪些原文词或语法。

    幻灯上不铺整节的希腊原文——读者不看希腊文，铺开只是一堵墙。他在课上真正做的
    是挑几个字讲：「你是彼得(Petrus)，我要把我的教會建造在這磐石(petra)上」。他
    讲到哪个字，就把那个字在经文里标出来，旁边写上原文。

    经文正文由页面取得；括号前的中文若能配上经文字词，页面同时高亮该词。配不上
    也不自动丢掉——`ἔσται δεδεμένον…（Future Perfect Passive）` 讲的是语法，正
    是这次 POC 必须呈现的内容。每条都带逐字稿原样切片和字符位置，页面只负责显
    示，不替教授补一层语法分析。
    """

    anchors = _timeline(sermon)
    if not anchors:
        return []
    text = "".join(
        STRIKETHROUGH.sub("", str(segment.get("text") or ""))
        for segment in (sermon.get("segments") or [])
    )
    out: list[dict[str, Any]] = []
    for match in GLOSS.finditer(text):
        original = match.group("original").strip()
        if not ORIGINAL.match(original):
            continue
        nearby = text[max(0, match.start() - 100):min(len(text), match.end() + 100)]
        if not GREEK_TERM.search(original) and not ORIGINAL_LANGUAGE_SIGNAL.search(nearby):
            continue
        at = _at(anchors, match.start())
        if at is None or not any(begin <= at < finish for begin, finish, _ in stretches):
            continue
        # 保留一个短的、逐字稿中的原样切片。页面可以把它折叠在「教授原话」下面，
        # 而不是由系统替教授解释希腊文。
        excerpt_start = max(0, match.start() - 60)
        excerpt_end = min(len(text), match.end() + 100)
        before = text[max(0, match.start() - 100):match.start()]
        greek = list(GREEK_TERM.finditer(before))
        greek_term = greek[-1].group(0) if greek else ""
        if GREEK_TERM.fullmatch(original):
            greek_term = original
        out.append({
            "at": at,
            "context": match.group("context"),
            "original": original,
            "greek": greek_term,
            "transcript_excerpt": text[excerpt_start:excerpt_end],
            "transcript_span": {"start": excerpt_start, "end": excerpt_end},
            "source_kind": "transcript_explicit",
        })
    return out


#: 逐字稿和经文比对时都去掉的标点。
#:
#: 他念经文不会照标点念，逐字稿的标点也是校对时加的。留着标点比不上。
PUNCTUATION = re.compile(r"[\s\u3000，。：；、！？「」『』（）〔〕《》…·,.!?:;\"'()\[\]]+")

#: 比对时用多长的窗口。
#:
#: 八个字够独特，也短到经得起他改一两个字：和合本是「因為這不是屬血肉的指示你
#: 的」，他念成「因為這不是屬血氣的指示你的」——「乃是我在天上的父指示」这一段
#: 照样对得上。
READING_WINDOW = 8


#: 取过的经文存在磁盘上。
#:
#: 不存的话内容会随取数成败而变：实测同一段经文，一次取全十一节，下一次第十七
#: 节空了——于是幻灯从「乃是我在天上的父指示你的」跳回了别节。外部接口偶发失败
#: 是常态，而这一页显示哪一节不该由它决定。
#:
#: 也省下重复请求：uvicorn 每次热重载都是新进程，只放内存的话每次都要重打三十
#: 几次。
_VERSE_CACHE_NAME = "bible-verses.json"


def _verse_cache_path(data_base_dir: Path) -> Path:
    return Path(data_base_dir) / "wang-knowledge-platform" / "cache" / _VERSE_CACHE_NAME


def verse_texts(data_base_dir: Path, book: str, chapter: int, low: int, high: int) -> dict[str, str]:
    """这一段每一节的中文经文，键是 `mat-16-17` 这样的 slug。

    用来在逐字稿里找他念到哪一节（见 `verse_readings`）。取不到就用存过的那一
    份；两边都没有才少这一节，那一节由主张给的经文兜底。
    """

    path = _verse_cache_path(data_base_dir)
    cached: dict[str, str] = {}
    if path.is_file():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = {}

    wanted = [f"{book}-{chapter}-{verse}" for verse in range(low, high + 1)]
    missing = [slug for slug in wanted if not cached.get(slug)]
    if missing:
        with httpx.Client(timeout=10) as client:
            for slug in missing:
                try:
                    reference = parse_reference(slug)
                    response = client.get(
                        f"https://bible-api.com/{reference['slug_book']} {chapter}:{slug.rsplit('-', 1)[1]}",
                        params={"translation": BIBLE_API_TRANSLATION_ZH},
                    )
                    response.raise_for_status()
                    text = str(response.json().get("text") or "").strip()
                except (httpx.HTTPError, ValueError, KeyError):
                    continue
                if text:
                    cached[slug] = text
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(cached, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except OSError:
            pass

    return {slug: cached[slug] for slug in wanted if cached.get(slug)}


def verse_readings(
    sermon: dict[str, Any], verses: dict[str, str]
) -> list[tuple[float, str]]:
    """教授在这篇讲道里，什么时候念到哪一节。

    他自己报的节号靠不住：（五）1 的 2:15 他说「我再念一下，然後我特別來看十九
    節那一段」——那是预告，接着他从十七节念起。照着报节号走，2:24 会显示十九节，
    而他正在念「乃是我在天上的父指示你的」，十七节。

    他念的经文本身靠得住。把每一节的原文切成八个字的窗口，在逐字稿里找。窗口短
    到经得起他改字（和合本「屬血肉」他念成「屬血氣」），长到不会撞车。

    没命中的节就是他没逐字念（复述或跳过），由主张给的那一节兜底。
    """

    text = "".join(
        STRIKETHROUGH.sub("", str(segment.get("text") or ""))
        for segment in (sermon.get("segments") or [])
    )
    anchors = _timeline(sermon)
    if not anchors or not text:
        return []
    # 去标点之后再比，同时记住每个字在原文里的位置——时间是按原文的字数插值的。
    keep = [i for i, char in enumerate(text) if not PUNCTUATION.match(char)]
    flat = "".join(text[i] for i in keep)

    # 一句话在几节里都出现时，它认不出他在念哪一节。
    #
    # 「法利賽人和撒都該人」在太16 的第 1、6、11、12 节里都有；靠它定位，4:22
    # 他明明在讲第一节的定冠詞，幻灯却跳到第六节。只留在这一段里唯一的窗口。
    owner: dict[str, str] = {}
    ambiguous: set[str] = set()
    for slug, verse_text in verses.items():
        stripped = PUNCTUATION.sub("", verse_text)
        for start in range(0, max(1, len(stripped) - READING_WINDOW + 1), 2):
            window = stripped[start : start + READING_WINDOW]
            if len(window) < READING_WINDOW:
                break
            if owner.setdefault(window, slug) != slug:
                ambiguous.add(window)

    found: dict[float, str] = {}
    for window, slug in owner.items():
        if window in ambiguous:
            continue
        at = flat.find(window)
        while at >= 0:
            moment = _at(anchors, keep[at])
            if moment is not None:
                found.setdefault(moment, slug)
            at = flat.find(window, at + 1)
    return sorted(found.items())


def judgement_during(
    spans: list[tuple[float, float, str]],
    stretches: list[tuple[float, float, str]],
    verses: dict[str, str] | None = None,
    provenance: dict[str, dict[str, list[str]]] | None = None,
) -> list[dict[str, Any]]:
    """教授在这几段里先后立的判断，按时间排。

    页面按经文聚合，一篇讲道一行，所以「他在讲哪个判断」不再由行来表示——它变成
    幻灯上的标题，跟着播放位置换。经文是他解的对象，一整段不变；判断是他解出来
    的结果，一堂课里会立好几个。

    不能照着证据区间逐条铺。教授论证时几个判断是缠在一起讲的，一句话同时是两个
    判断的证据，逐条铺开的话（五）1 十七分钟里标题要换 18 次，有的隔 4 秒。

    所以按块算主导：每半分钟看这一块里哪个判断占的时间最多，再把撑不满 90 秒的
    并进邻居。剩下的换法就是他真的转了话题。

    `verses` 把每个判断配上它讲的那一节——主张自己的 `scripture_refs` 就写着，
    「彼得認信耶穌是基督、永生神的兒子」是太16:16，「相較於別人如何評價耶穌」是
    太16:13-15。幻灯上摆哪一节由它决定。
    """

    if not spans:
        return []

    def dominant(begin: float, finish: float) -> str:
        """这一段里哪个判断占的时间最多。"""

        scores: dict[str, float] = {}
        for start, end, proposition in spans:
            if not proposition:
                continue
            overlap = min(end, finish) - max(start, begin)
            if overlap > 0:
                scores[proposition] = scores.get(proposition, 0.0) + overlap
        return max(scores, key=lambda k: (scores[k], k)) if scores else ""

    out: list[dict[str, Any]] = []
    for begin, finish, _ in stretches:
        blocks: list[tuple[float, str]] = []
        cursor = begin
        while cursor < finish:
            edge = min(cursor + JUDGEMENT_BLOCK_SECONDS, finish)
            judgement = dominant(cursor, edge)
            if judgement:
                blocks.append((cursor, judgement))
            cursor = edge
        # 连着一样的并成一段，撑不满的让前一个盖过去——他只是穿插提了一句，不是
        # 转了话题。
        runs: list[list[Any]] = []
        for at, judgement in blocks:
            if runs and runs[-1][1] == judgement:
                continue
            runs.append([at, judgement])
        kept = len(out)
        for index, run in enumerate(runs):
            until = runs[index + 1][0] if index + 1 < len(runs) else finish
            if out and out[-1]["judgement"] == run[1]:
                continue
            # 第一个也要撑得住。
            #
            # 原来这条只挡后面的判断，第一个免检——于是（四）1 一开头顶着「阴间
            # 的权柄不能胜过基督的教会」，而教授那时候才刚念到「你是彼得，我要
            # 把我的教會建造在這磐石上」。那 60 秒之所以挂在阴间名下，是因为这
            # 句话是「陰間的門不能勝過教會」那条主张的引证；录音收进来没错，拿
            # 它当标题就错了。
            if until - run[0] < MIN_JUDGEMENT_SECONDS:
                continue
            mark = {"at": run[0], "judgement": run[1],
                    "scripture": (verses or {}).get(run[1], "")}
            if run[1] in (provenance or {}):
                mark["provenance"] = provenance[run[1]]
            out.append(mark)
        # 每一段都得有自己的抬头。
        #
        # 90 秒那条线会把短段的抬头全滤掉：（四）3 的 2:05–3:03 和 35:27–36:12
        # 各只有一分钟，一条都没剩，前端就退回列表里的第一条——拿 38:51 那段
        # 「磐石不是彼得本人」去标 2:05，而他那时候讲的是天国钥匙。
        #
        # 一条都没剩就用整段的主导判断。它撑不满 90 秒不是因为他在穿插，是因为
        # 这一段本来就短。
        if len(out) == kept:
            judgement = dominant(begin, finish)
            if judgement and (not out or out[-1]["judgement"] != judgement):
                mark = {"at": begin, "judgement": judgement,
                        "scripture": (verses or {}).get(judgement, "")}
                if judgement in (provenance or {}):
                    mark["provenance"] = provenance[judgement]
                out.append(mark)
    # 每个判断能听多久。
    #
    # 判断不再只是幻灯上的抬头，它同时是读者的收听单位——一段一个判断，点它开始
    # 播。所以每条要报出自己有多长，读者才看得出「这一条听 3 分钟还是 13 分钟」。
    #
    # 算的是**能播的**秒数，不是首尾相减：一个判断可以横跨两段录音，中间隔着教
    # 授岔去讲别的，那段不播，也就不该计进去。
    for index, mark in enumerate(out):
        until = out[index + 1]["at"] if index + 1 < len(out) else float("inf")
        mark["seconds"] = sum(
            max(0.0, min(finish, until) - max(begin, mark["at"]))
            for begin, finish, _ in stretches
        )
    return out


def in_passage(claim: dict[str, Any], book: str, chapter: int, low: int, high: int) -> bool:
    """这条主张讲的是不是这段经文。

    主张自己带 `scripture_refs`，写法跟观点的 `scripture_scope` 一样杂：
    `馬太福音16:28-17:2`、`馬可福音8:38（聽眾口述為「馬可福音九章最後一節」）`、
    `啟示錄（未指明章節）`。认经文交给 `api.scripture`。
    """

    # 引用太多处的，是在跨经文作综合，不是在解这一段。
    #
    # 「父神的宣告揭示雙重身分：詩2的受膏君王，與賽42神所喜悅的受苦僕人」引了 11
    # 处——诗2:7、赛42:1、太3:16-17、太17:1-5、可9:2-7、路9:28-35、彼后1:16-18、
    # 徒13:33……只有一处是太16:21-23。它讲的是登山变像，太16:21-23 是它引来对照
    # 的。可「任一处命中就算」把它收进了 16:21-23，于是那一页上出现了两段讲登山
    # 变像的录音。另一条「太16:28…應驗於登山變像」同样，引 7 处，混进了 16:24-27。
    #
    # 门槛量出来的：这四段命中的 226 条主张里，163 条只引 1 处，42 条引 2 处，18
    # 条引 3 处，然后断层——5 处 1 条、7 处 1 条、11 处 1 条。5 处那条是对的（「福
    # 音書中的彼得宣認、榮進耶路撒冷、大祭司審問……」，引的是四福音里同一件事的平
    # 行记载），7 处和 11 处那两条是错的。
    #
    # 这条线只有三个样本撑着，边界不硬。真正的修法在抽取那边——那 11 处引用里
    # 太17:5 和赛42:1 各出现两次，像是多轮抽取合并出来的。
    if len({slug for ref in (claim.get("scripture_refs") or [])
            for slug in reference_slugs(
                ref if isinstance(ref, str) else json.dumps(ref, ensure_ascii=False)
            )}) >= 6:
        return False

    for reference in claim.get("scripture_refs") or []:
        text = (
            reference
            if isinstance(reference, str)
            else json.dumps(reference, ensure_ascii=False)
        )
        for slug in reference_slugs(text):
            parts = slug.split("-")
            if len(parts) < 3 or parts[0] != book or parts[1] != str(chapter):
                continue
            start = int(parts[2])
            end = int(parts[3]) if len(parts) > 3 else start
            overlap = min(end, high) - max(start, low) + 1
            if overlap <= 0:
                continue
            # 光有交集不够。
            #
            # 「太16:16-23」跨八节，与 18-19 有交集，可它说的是彼得认耶稣是基督
            # 到耶稣责备彼得那一整段——七条讲「彼得不认识弥赛亚受苦」的主张就是
            # 这么串进来的，把（四）3、（四）4 和母本整篇拉进了 16:18-19。
            #
            # 分界在**起点**，不只在宽度。引用从哪一节起头，主张就锚在哪里：
            # 「16:18-23」从 18 起，那条「耶穌不會把教會建立在彼得本人身上」正是
            # 讲 18 节的；「16:16-23」从 16 起，锚在彼得认信，不在磐石。
            #
            # 起点不在这段里的，退一步看交集占不占这条引用的一半——「16:17-19」
            # 占三分之二，算；「16:16-21」占三分之一，不算。
            if low <= start <= high or overlap * 2 >= (end - start + 1):
                return True
    return False


def claim_verse(claim: dict[str, Any], book: str, chapter: int, low: int, high: int) -> str:
    """这条主张讲的是哪一节，截到本段范围内。

    幻灯上摆哪一节由它决定。铺整段不行——16:13-23 有十一节，380 个字是一堵墙，
    而且每张幻灯长得都一样。跟着「他此刻念到哪一节」走也不行：他到 2:18 还一节
    都没念，而那时候讲的是「彼得認信耶穌是基督、永生神的兒子」——太16:16。

    主张自己就写着。「彼得認信」是太16:16，「相較於別人如何評價耶穌」是
    太16:13-15，「耶穌禁止門徒立即公開祂是基督」是太16:20-23。

    取落在本段里最窄的那一处：一条主张常引好几处，`mat-16-19` 比 `mat-16-16-23`
    更说得清他此刻在讲什么。截到本段是因为 `mat-16-16-23` 在 16:13-23 页上还有
    八节，摆出来又是一堵墙。
    """

    best: tuple[int, str] | None = None
    for reference in claim.get("scripture_refs") or []:
        text = (
            reference
            if isinstance(reference, str)
            else json.dumps(reference, ensure_ascii=False)
        )
        for slug in reference_slugs(text):
            parts = slug.split("-")
            if len(parts) < 3 or parts[0] != book or parts[1] != str(chapter):
                continue
            start = max(int(parts[2]), low)
            end = min(int(parts[3]) if len(parts) > 3 else int(parts[2]), high)
            if start > end:
                continue
            width = end - start
            if best is None or width < best[0]:
                tail = f"-{end}" if end != start else ""
                best = (width, f"{book}-{chapter}-{start}{tail}")
    return best[1] if best else f"{book}-{chapter}-{low}"


def claim_primary_scripture(claim: dict[str, Any]) -> str:
    """单篇讲道 slide 上放哪一处经文。

    单篇页没有预先限定的经文范围，所以从 Claim 自己的 `scripture_refs` 里取最窄
    的一处。它是教授这条判断的来源绑定，不从逐字稿标题或系统常识猜。
    """

    best: tuple[int, int, str] | None = None
    order = 0
    for reference in claim.get("scripture_refs") or []:
        text = (
            reference
            if isinstance(reference, str)
            else json.dumps(reference, ensure_ascii=False)
        )
        for slug in reference_slugs(text):
            parts = slug.split("-")
            if len(parts) < 3:
                continue
            start = int(parts[2])
            end = int(parts[3]) if len(parts) > 3 else start
            candidate = (max(0, end - start), order, slug)
            if best is None or candidate < best:
                best = candidate
            order += 1
    return best[2] if best else ""


def claim_scripture_label(claim: dict[str, Any], scripture: str) -> str:
    """可取正文就用规范中文引用；只有章级范围时仍保留 Claim 的原始写法。"""

    if scripture:
        return format_chinese_reference(scripture)
    references = claim.get("scripture_refs") or []
    if not references:
        return ""
    first = references[0]
    return first if isinstance(first, str) else json.dumps(first, ensure_ascii=False)


def build_sermon_slides(
    store: Any,
    data_base_dir: Path,
    transcript_id: str,
) -> dict[str, Any] | None:
    """一篇完整讲道的同步 slide 时间轴。

    这与按经文摘取录音的 `build_index` 是两个入口。这里从 0:00 播到媒体结束，
    Claim 只提供规范化标题和经文范围；教授的原文解释只从该 Claim 绑定的
    SourceFragment 或逐字稿明确标注中取得。没有来源的空档由上一张继续停留，不
    生成系统自写的过场文字。
    """

    def by_id(collection: str, key: str) -> dict[str, dict[str, Any]]:
        return {
            str(row.get(key)): row
            for row in store.list_records(collection)
            if row.get(key)
        }

    claims = by_id("claims", "claim_id")
    steps = by_id("evidence_steps", "evidence_step_id")
    fragments = by_id("source_fragments", "fragment_id")
    documents = by_id("source_documents", "source_id")
    document = next(
        (
            row
            for row in documents.values()
            if str(row.get("transcript_id") or "") == transcript_id
            or str(row.get("source_id") or "") == transcript_id
        ),
        None,
    )
    if document is None:
        return None

    source_id = str(document.get("source_id") or "")
    sermon = Sermons(data_base_dir).load(source_id, document)
    if not sermon or not sermon.get("media_url") or not sermon.get("media_duration"):
        return None
    duration = float(sermon["media_duration"])

    spans: list[tuple[float, float, str]] = []
    verses: dict[str, str] = {}
    verse_labels: dict[str, str] = {}
    provenance: dict[str, dict[str, list[str]]] = {}
    language_notes: dict[str, list[dict[str, Any]]] = {}

    for claim_id, claim in claims.items():
        statement = str(claim.get("statement") or "").strip()
        if not statement:
            continue
        evidence_ids = (
            claim.get("eligible_evidence_step_ids")
            or claim.get("evidence_step_ids")
            or []
        )
        claim_fragments: list[dict[str, Any]] = []
        seen_fragments: set[str] = set()
        for evidence_id in evidence_ids:
            step = steps.get(str(evidence_id))
            if not step:
                continue
            fragment_ids: list[str] = []
            if step.get("source_fragment_id"):
                fragment_ids.append(str(step["source_fragment_id"]))
            fragment_ids.extend(str(x) for x in (step.get("source_fragment_ids") or []))
            for fragment_id in fragment_ids:
                if fragment_id in seen_fragments:
                    continue
                fragment = fragments.get(fragment_id)
                if not fragment or str(fragment.get("source_id") or "") != source_id:
                    continue
                span = segment_time(sermon, fragment)
                if not span or span[0] >= duration:
                    continue
                seen_fragments.add(fragment_id)
                start, end = span[0], min(span[1], duration)
                spans.append((start, end, statement))
                item = {
                    "claim_id": claim_id,
                    "evidence_step_id": str(evidence_id),
                    "source_fragment_id": fragment_id,
                    "at": start,
                    "text": str(fragment.get("verbatim_excerpt") or "").strip(),
                }
                claim_fragments.append(item)

                trace = provenance.setdefault(statement, {
                    "claim_ids": [],
                    "evidence_step_ids": [],
                    "source_fragment_ids": [],
                })
                for key, value in (
                    ("claim_ids", claim_id),
                    ("evidence_step_ids", str(evidence_id)),
                    ("source_fragment_ids", fragment_id),
                ):
                    if value not in trace[key]:
                        trace[key].append(value)

        if not claim_fragments:
            continue
        scripture = claim_primary_scripture(claim)
        verses.setdefault(statement, scripture)
        verse_labels.setdefault(statement, claim_scripture_label(claim, scripture))
        explicit = [
            item
            for item in claim_fragments
            if item["text"]
            and (
                ORIGINAL_LANGUAGE_SIGNAL.search(item["text"])
                or GREEK_TERM.search(item["text"])
            )
        ]
        candidates = explicit
        if not candidates and ORIGINAL_LANGUAGE_SIGNAL.search(statement):
            candidates = [item for item in claim_fragments if item["text"]]
        if candidates:
            target = language_notes.setdefault(statement, [])
            for item in candidates:
                if not any(note["source_fragment_id"] == item["source_fragment_id"] for note in target):
                    target.append(item)

    full = [(0.0, duration, "")]
    marks = judgement_during(spans, full, verses, provenance)
    slides: list[dict[str, Any]] = []
    first_scripture = marks[0].get("scripture", "") if marks else ""
    first_title = str(marks[0].get("judgement", "")) if marks else ""
    first_at = float(marks[0]["at"]) if marks else duration
    if first_at > 0 or not marks:
        slides.append({
            "at": 0.0,
            "seconds": first_at,
            "kind": "cover",
            "title": sermon["title"],
            "scripture": first_scripture,
            "scripture_label": verse_labels.get(first_title, ""),
            "language_notes": [],
        })
    for mark in marks:
        statement = str(mark["judgement"])
        notes = sorted(
            language_notes.get(statement, []),
            key=lambda item: (abs(float(item["at"]) - float(mark["at"])), float(item["at"])),
        )[:2]
        scripture = str(mark.get("scripture") or "")
        slides.append({
            "at": mark["at"],
            "seconds": mark["seconds"],
            "kind": "claim",
            "title": statement,
            "scripture": scripture,
            "scripture_label": verse_labels.get(statement, ""),
            "provenance": mark.get("provenance"),
            "language_notes": notes,
        })

    return {
        "schema_version": "wang_sermon_slide_deck_v1",
        "source_id": source_id,
        "source_sha256": document.get("source_sha256"),
        "transcript_id": sermon["transcript_id"],
        "title": sermon["title"],
        "media_duration": duration,
        "slides": slides,
        "original_language_events": original_language_during(full, sermon),
    }


def build_index(store: Any, data_base_dir: Path, passage: str = "mat-16-13-20") -> dict[str, Any]:
    """一段经文底下，教授在哪几篇讲道里讲过、各讲了哪几段。

    `passage` 是 `api.scripture` 的经文 slug（`mat-16-13-20`），书卷、章、节全从
    它读出来——原来书名写死成 `"mat"`，章节另用一个正则拆，于是这一层只能服务马
    太福音的一页。同一个 slug 也是页面的地址和幻灯要取的那段经文，三处一个写法。

    `store` 只需要一个 `list_records(collection)`，所以既能接 PostgresKnowledgeStore，
    也能在测试里塞一个字典。
    """

    def by_id(collection: str, key: str) -> dict[str, dict[str, Any]]:
        return {str(row.get(key)): row for row in store.list_records(collection) if row.get(key)}

    claims = by_id("claims", "claim_id")
    steps = by_id("evidence_steps", "evidence_step_id")
    fragments = by_id("source_fragments", "fragment_id")
    documents = by_id("source_documents", "source_id")
    sermons = Sermons(data_base_dir)

    reference = parse_reference(passage)
    book = str(reference["slug"])
    chapter = int(reference["chapter"])
    low, high = int(reference["start"]), int(reference["end"])
    # 这一段每一节的经文，用来在逐字稿里找他念到哪一节。
    passage_verses = verse_texts(data_base_dir, book, chapter, low, high)

    def spans_of(
        claim_id: str,
        label: str,
    ) -> tuple[
        dict[str, list[tuple[float, float, str]]],
        dict[str, dict[str, list[str]]],
    ]:
        """这条主张的录音，按讲道分开，每段记上它是哪句话。"""

        claim = claims.get(claim_id) or {}
        ids = claim.get("eligible_evidence_step_ids") or claim.get("evidence_step_ids") or []
        found: dict[str, list[tuple[float, float, str]]] = {}
        trace: dict[str, dict[str, list[str]]] = {}
        for step_id in ids:
            step = steps.get(str(step_id))
            if not step:
                continue
            fragment_ids = []
            if step.get("source_fragment_id"):
                fragment_ids.append(str(step["source_fragment_id"]))
            fragment_ids.extend(str(x) for x in (step.get("source_fragment_ids") or []))
            for fragment_id in fragment_ids:
                fragment = fragments.get(fragment_id)
                if not fragment:
                    continue
                source_id = str(fragment.get("source_id") or "")
                sermon = sermons.load(source_id, documents.get(source_id) or {})
                if not sermon or not sermon.get("media_url"):
                    continue
                span = segment_time(sermon, fragment)
                if span:
                    found.setdefault(source_id, []).append((span[0], span[1], label))
                    item = trace.setdefault(source_id, {
                        "claim_ids": [],
                        "evidence_step_ids": [],
                        "source_fragment_ids": [],
                    })
                    if claim_id not in item["claim_ids"]:
                        item["claim_ids"].append(claim_id)
                    if str(step_id) not in item["evidence_step_ids"]:
                        item["evidence_step_ids"].append(str(step_id))
                    if fragment_id not in item["source_fragment_ids"]:
                        item["source_fragment_ids"].append(fragment_id)
        return found, trace

    # 直接从主张出发，不绕 CanonicalViewpoint。
    #
    # 一度是从中心结构走：结构 → focal 观点 → 成员主张。观点是跨讲道的抽象，为
    # 了它得先处理结构撞号、focal 挂错、revision 新旧这些事，而拿到的材料反而更
    # 少——量下来观点那条路只覆盖 46 条主张，主张自己带 scripture_refs 的有 66
    # 条，前者是后者的子集。（五）1 从 17 分变 24 分，（四）3 从 5 分变 8 分。
    #
    # 而且主张是**某一篇讲道里的**一句话，正是幻灯上该写的：他这一分钟说的是
    # 什么。观点是把五篇里的同一件事归成一条，那是文章层要的东西，不是听原声的
    # 人要的。
    by_sermon: dict[str, dict[str, Any]] = {}
    # 每个判断讲的是哪一节。主张自己的 `scripture_refs` 就写着，截到本段范围内。
    verses: dict[str, str] = {}
    for claim_id, claim in claims.items():
        if not in_passage(claim, book, chapter, low, high):
            continue
        statement = str(claim.get("statement") or "").strip()
        if not statement:
            continue
        verses.setdefault(statement, claim_verse(claim, book, chapter, low, high))
        located, traces = spans_of(claim_id, statement)
        for source_id, spans in located.items():
            slot = by_sermon.setdefault(source_id, {"spans": [], "provenance": {}})
            slot["spans"].extend(spans)
            target = slot["provenance"].setdefault(statement, {
                "claim_ids": [],
                "evidence_step_ids": [],
                "source_fragment_ids": [],
            })
            for key, values in traces.get(source_id, {}).items():
                for value in values:
                    if value not in target[key]:
                        target[key].append(value)

    sermons_out: list[dict[str, Any]] = []
    for source_id, slot in by_sermon.items():
        sermon = sermons.load(source_id, documents.get(source_id) or {})
        merged = stretch(slot["spans"], sermon.get("media_duration") if sermon else None)
        if not merged or not sermon:
            continue
        sermons_out.append({
            "source_id": source_id,
            "transcript_id": sermon["transcript_id"],
            "title": sermon["title"],
            "media_kind": sermon["media_kind"],
            "media_url": sermon["media_url"],
            "media_duration": sermon["media_duration"],
            "stretches": [{"start": a, "end": b} for a, b, _ in merged],
            "judgements": judgement_during(
                slot["spans"], merged, verses, slot["provenance"]
            ),
            "spoken": spoken_during(merged, sermon),
            # 他什么时候念到哪一节。报节号是预告，念出来才算数。
            "readings": [
                {"at": at, "scripture": slug}
                for at, slug in verse_readings(sermon, passage_verses)
                if any(begin <= at < finish for begin, finish, _ in merged)
            ],
            "original_language_events": original_language_during(merged, sermon),
            "seconds": sum(b - a for a, b, _ in merged),
        })
    sermons_out.sort(key=lambda row: -row["seconds"])

    return {
        "schema_version": "wang_original_audio_index_v4",
        # 一段经文一个入口，所以整页只有这一处经文——页面标题、幻灯要取的经文、
        # 页面地址，用的都是这一个 slug。
        "passage": passage,
        "label": format_chinese_reference(passage),
        "title": PASSAGE_TITLES.get(passage, ""),
        "sermons": sermons_out,
    }


#: 有原声页面的经文段落。
#:
#: 跟文章层的单元走，不跟数据走。量过太 16 章 27 个切点，数的是「有多少条主张
#: 的经文范围跨过这一刀」：9｜10 有 20 条（全章最不能切，他讲「你們還不明白嗎」
#: 的连贯论证），21｜22 有 17 条，20｜21 有 15 条，12｜13 只有 1 条。纯按数据切
#: 会得到 16:1-12 / 16:13-23 / 16:24-27 三段，而文章层切在 20｜21。
#:
#: 骑跨不等于切断：那 14 条主张会在 16:13-20 和 16:21-23 两页上各出现一次，而不
#: 去重本来就是这一层的规则（同一件事他在几篇里各讲一遍，删掉就是删掉他的论
#: 证）。14 条重复换全站一致——落地页上文章和原声并排显示，范围对不齐会被读者当
#: 成 bug。
#:
#: 但 20｜21 是全章第三糟的一刀，文章重写若要重划边界，这里最该重新考虑，而且两
#: 边要一起动。证据记在 #36。
#:
#: 16:28 不做：那 24 条主张引的是 `馬太福音16:28-17:2`、`馬可福音9:1-2`、`路加
#: 福音9:28`、`彼得後書1:16-18`，全指向登山变像，跨章，归 #20。
#:
#: 读者看到的写法（`太16:1–12`）跟文章卡片上的一样，两列并排才对得齐；那是编辑
#: 决定，不是从 slug 推出来的，所以写在这里。
#: 马太16章分成哪几段。
#:
#: 一度跟文章层的单元走（16:13-20 / 16:21-23），为的是落地页上文章和原声并排时
#: 范围对齐。读者报的一处错推翻了它：16:13-20 那一页上，（四）4 的 30:05 顶着
#: 「彼得雖認識耶穌是基督、彌賽亞，卻對彌賽亞的性質與使命抱有錯誤觀念」，而那
#: 一刻他念的是「彼得就拉著祂，勸祂說：主啊，萬不可如此」——太16:22。幻灯自己
#: 角上那行小字写着「他此刻在念 馬太福音 16:21」。
#:
#: 这正是当初量出来的：20｜21 骑跨 15 条主张，是全章第三糟的切点。彼得认信
#: （v16）→ 吩咐不可对人说（v20）→ 从此指示必须受害（v21）→ 彼得劝阻被责备
#: （v22-23）是一段连续的教导，切在中间，材料必然掉到错的一边。查下来三条落错
#: 边：两条本属 21-23 的挂在 13-20，一条本属 v20 的挂在 21-23。
#:
#: 合成一页全部消失，而且 87 分钟比分开的 87+16 还少——少掉的正是骑跨造成的重
#: 复。21 个 topic 当目录，一屏文字，找什么点一下就到。
#:
#: 三个切点是量出来的，切断的主张数：12｜13 一条，23｜24 零条，27｜28 零条。全
#: 章最不能切的是 9｜10（20 条）。
#:
#: 代价是落地页上「文章 太16:13–20」与「原聲 太16:13–23」范围不一致。边界证据
#: 记在 #36（16:21-23 的修订卡），文章重写时两边应一起动。
#:
#: 16:28 不在这里：跨章，归 #20（16:28–17:8 登山变像）。
#: 每段的标题。
#:
#: 这是这一页唯一由人写的字——其余全是教授的原话和圣经经文。用词取自逐字稿里他
#: 自己的分段小标题，不另造说法：
#:
#:   16:1-12   「小信」的真正含義 · 防備錯誤教導的「酵」
#:   16:13-23  教會的磐石根基 · 權柄的對象：不只彼得，更是教會
#:             彌賽亞的真諦：從身份認同到使命認知
#:   16:24-27  何謂「捨己」？ · 何謂「背起十字架」？
PASSAGE_TITLES: dict[str, str] = {
    "mat-16-1-12": "求神蹟的試探，與防備法利賽人的酵",
    "mat-16-13-23": "認信、磐石與天國的鑰匙，到第一次預言受難",
    "mat-16-24-27": "捨己、背十字架，與人子按行為的報應",
}

PASSAGES: tuple[tuple[str, str], ...] = (
    ("mat-16-1-12", "太16:1–12"),
    ("mat-16-13-23", "太16:13–23"),
    ("mat-16-24-27", "太16:24–27"),
)

#: 原声页面的地址。文库落地页按这个拼链接。
PASSAGE_URL_PREFIX = "/resources/wang-repository/audio"


def passage_summaries(store: Any, data_base_dir: Path) -> list[dict[str, Any]]:
    """每一段有多少可听的、分成几个判断——文库落地页上那一排。

    落地页自己写着「每篇文章都可完整閱讀，也可隨時切換聆聽相關原聲講解」，而从
    那里通不到任何原声。这个列表就是兑现那一句。

    经卷和章按文章那边的写法给（`Matt` / `馬太福音` / 16），落地页才能把原声和
    文章排进同一章底下。16:21-23 和 16:24-27 只有原声没有文章——原声可以先于文
    章上线。

    现读现算，四段合起来不到一秒。缓存会在观点层改动之后继续端出旧的数字，而这
    一排给的正是「有多少可听」。
    """

    out: list[dict[str, Any]] = []
    for slug, label in PASSAGES:
        index = build_index(store, data_base_dir, slug)
        if not index["sermons"]:
            continue
        reference = parse_reference(slug)
        out.append({
            "passage": slug,
            "label": label,
            "title": PASSAGE_TITLES.get(slug, ""),
            "scripture": {
                "book": reference["osis_book"],
                "book_label": BOOK_SLUG_TO_NAME.get(str(reference["slug"]), label),
                "chapter": reference["chapter"],
                "verse_start": reference["start"],
                "end_chapter": reference["chapter"],
                "verse_end": reference["end"],
                "display": label,
            },
            "sermons": len(index["sermons"]),
            "seconds": sum(row["seconds"] for row in index["sermons"]),
            "topics": sum(len(row["judgements"]) for row in index["sermons"]),
            "href": f"{PASSAGE_URL_PREFIX}/{slug}",
        })
    return out
