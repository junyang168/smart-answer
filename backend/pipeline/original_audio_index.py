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

from backend.api.scripture import format_chinese_reference, reference_slugs, spoken_references


#: 只有「等价」才算观点的成员。`supports`／`qualifies` 是关系不是身份，见
#: CanonicalViewpoint 设计第 4 节。
MEMBER_LINK_TYPES = {"equivalent_full", "equivalent_component"}

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

#: 每段往前留的引子。
#:
#: 引文定位到的是那句话本身，从那一刻起播会掐在半句上——「所以彼得叫一個人進天
#: 堂」前面的「所以」承的是上一句。往前退 8 秒（约 25 字）能接上一句的尾巴。
LEAD_IN_SECONDS = 8.0

#: 一段最短播多久。
#:
#: 按引文长度算，一句话只有五六秒，点开听完还没反应过来就停了。45 秒是教授把
#: 一个判断说完再举一句例子的长度——量下来单条引文中位 18 字（约 6 秒），而他
#: 说清一件事通常连着三到八句。
MIN_STRETCH_SECONDS = 45.0

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
                return {
                    "media_kind": kind,
                    "media_url": f"{MEDIA_URL_PREFIX}/{quote(transcript_id + suffix)}",
                }
        return {"media_kind": None, "media_url": None}


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


def stretch(intervals: Iterable[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """把区间并成「连着讲的几段」，每段记住它主要在讲哪一节。

    相隔 30 秒之内接起来，超过就断开——断开的地方教授在讲别的，接起来听会以为
    是连着的一句话。

    经节跟着时间走。一段录音是某个 focal 观点的证据带出来的，那个观点讲哪一节，
    这一段就显示哪一节。合并之后一段里可能混着几个观点的区间，取占时间最长的那
    个——幻灯只有一张，得挑教授在这一段里主要在讲的那一节。
    """

    ordered = sorted((max(0.0, a - LEAD_IN_SECONDS), b, ref) for a, b, ref in intervals)
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
        out.append((item["start"], max(item["end"], item["start"] + MIN_STRETCH_SECONDS), ref))
    return out


#: 一个观点讲的是哪一节。
#:
#: `scripture_scope` 的写法不统一，同一个结构底下就见过六种：`馬太福音16:18-23`、
#: `太 16:18、弗 2:20`、`太16:18`、`馬太福音16:16-18`，还有光写 `馬太福音` 和
#: `聖經` 的。交给 `api.scripture` 去认——书名表、缩写表、繁简别名都在那边，这里
#: 不另起一套。
#:
#: 取第一条解得出节号的。解不出就返回空字符串，幻灯保持上一张（`聖經`、`詩篇`、
#: `使徒行傳15章` 这类本来就不是节级引用，全库 170 条里有 13 条）。
def scripture_slug(
    scope: Iterable[str], prefer: str = "", within: tuple[int, int] | None = None
) -> str:
    """这个观点讲的是哪一节，写成 `mat-16-19` 这样的 slug。

    `scripture_scope` 的写法不统一，同一个结构底下就见过六种：`馬太福音16:18-23`、
    `太 16:18、弗 2:20`、`太16:18`、`馬太福音16:16-18`，还有光写 `馬太福音` 和
    `聖經` 的。认经文交给 `api.scripture`——书名表、缩写表、繁简别名都在那边。

    `prefer` 是这一页正在解的那段经文。一条 scope 常常列着好几处，
    `約翰福音20:23、馬太福音16:19、馬太福音18:18` 里教授解的是马太16章，约20:23
    是他引来对照的；幻灯先显示他正在解的那一节。没有匹配的才退回第一条。

    一条都解不出就返回空字符串，幻灯保持上一张（`聖經`、`詩篇`、`使徒行傳15章`
    这类本来就不是节级引用，全库 170 条 scope 里有 13 条）。
    """

    found: list[str] = []
    for piece in scope or []:
        found.extend(s for s in reference_slugs(str(piece)) if s not in found)
    if prefer:
        for slug in found:
            if slug.startswith(prefer):
                return _clip(slug, within)
    return _clip(found[0], within) if found else ""


def _clip(slug: str, within: tuple[int, int] | None) -> str:
    """把经节范围截到这一页讲的那几节。

    `馬太福音16:18-23` 是那个观点的 scope，但这一页只讲 16:18-19。六节经文摆一
    张幻灯，读者要在里面找教授正在讲的那一句。截到 16:18-19，多出来的 20-23 节
    不是这一页的事。

    只截同一章的。跨章的（`16:28-17:2` 这类）原样留着，宁可宽一点也不要截错。
    """

    if not within:
        return slug
    parts = slug.split("-")
    if len(parts) < 3:
        return slug
    book, chapter, start = parts[0], parts[1], int(parts[2])
    end = int(parts[3]) if len(parts) > 3 else start
    low, high = within
    start, end = max(start, low), min(end, high)
    if start > end:
        return slug
    return f"{book}-{chapter}-{start}" + (f"-{end}" if end != start else "")


#: 太16:18-19 这一段的 scope 在数据里有好几种写法：`馬太福音16:19`、
#: `馬太福音16:18-23`、`約翰福音20:23馬太福音16:19馬太福音18:18`。只找 `16:18`
#: 会漏掉四分之三。
#:
#: 不能用 `\b` 收尾：`16:19馬太福音18:18` 里数字后面紧跟中文，而中文与数字之间
#: 没有词边界，`16:19\b` 匹配不上——三分之二的 scope 长这样。改成「后面不是数
#: 字」。
SCRIPTURE_PATTERNS = {
    "16:18-19": re.compile(r"16:(?:18|19)(?!\d)|16:1[0-8]-(?:19|2\d)"),
}


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
        before = [(at, slug) for at, slug in timed if at <= start]
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


def judgement_during(
    spans: list[tuple[float, float, str]], stretches: list[tuple[float, float, str]]
) -> list[dict[str, Any]]:
    """教授在这几段里先后立的判断，按时间排。

    页面按经文聚合，一篇讲道一行，所以「他在讲哪个判断」不再由行来表示——它变成
    幻灯上的标题，跟着播放位置换。经文是他解的对象，一整段不变；判断是他解出来
    的结果，一堂课里会立好几个。

    不能照着证据区间逐条铺。教授论证时几个判断是缠在一起讲的，一句话同时是两个
    判断的证据，逐条铺开的话（五）1 十七分钟里标题要换 18 次，有的隔 4 秒。

    所以按块算主导：每半分钟看这一块里哪个判断占的时间最多，再把撑不满 90 秒的
    并进邻居。剩下的换法就是他真的转了话题。
    """

    if not spans:
        return []
    out: list[dict[str, Any]] = []
    for begin, finish, _ in stretches:
        blocks: list[tuple[float, str]] = []
        cursor = begin
        while cursor < finish:
            edge = min(cursor + JUDGEMENT_BLOCK_SECONDS, finish)
            scores: dict[str, float] = {}
            for start, end, proposition in spans:
                if not proposition:
                    continue
                overlap = min(end, edge) - max(start, cursor)
                if overlap > 0:
                    scores[proposition] = scores.get(proposition, 0.0) + overlap
            if scores:
                blocks.append((cursor, max(scores, key=lambda k: (scores[k], k))))
            cursor = edge
        # 连着一样的并成一段，撑不满的让前一个judgement 盖过去——他只是穿插提了
        # 一句，不是转了话题。
        runs: list[list[Any]] = []
        for at, judgement in blocks:
            if runs and runs[-1][1] == judgement:
                continue
            runs.append([at, judgement])
        for index, run in enumerate(runs):
            until = runs[index + 1][0] if index + 1 < len(runs) else finish
            if out and out[-1]["judgement"] == run[1]:
                continue
            if out and until - run[0] < MIN_JUDGEMENT_SECONDS:
                continue
            out.append({"at": run[0], "judgement": run[1]})
    return out


def build_index(store: Any, data_base_dir: Path, scripture: str = "16:18-19") -> dict[str, Any]:
    """整棵树：中心观点 → 讲道 → 连着讲的几段。

    `store` 只需要一个 `list_records(collection)`，所以既能接 PostgresKnowledgeStore，
    也能在测试里塞一个字典。
    """

    def by_id(collection: str, key: str) -> dict[str, dict[str, Any]]:
        return {str(row.get(key)): row for row in store.list_records(collection) if row.get(key)}

    viewpoints = by_id("canonical_viewpoints", "viewpoint_id")
    revisions = by_id("viewpoint_revisions", "viewpoint_revision_id")
    claims = by_id("claims", "claim_id")
    steps = by_id("evidence_steps", "evidence_step_id")
    fragments = by_id("source_fragments", "fragment_id")
    documents = by_id("source_documents", "source_id")
    structures = by_id("viewpoint_structures", "structure_id")
    # 键是 revision 不是 structure：26 个结构有 28 个 revision，按 structure 建
    # 索引会让新旧两版互相覆盖。
    structure_revisions = {
        str(row.get("structure_revision_id") or row.get("revision_id") or ""): row
        for row in store.list_records("viewpoint_structure_revisions")
    }
    # 每一个修订都要认，不能只认当前修订。
    #
    # 结构里的 focal 条目记的是它成形那一刻的 viewpoint_revision_id，观点后来又改
    # 过，那条 id 就不再是「当前」的了。只建 current_revision_id → viewpoint_id
    # 的话，这些条目查不到主人：轻则录音少收，重则去重失效——VS-454dd 的中心指的
    # 正是 CV-d8e50d04 的旧修订，查不到就当成了另一个中心观点。
    revision_to_viewpoint = {
        str(rid): str(row.get("viewpoint_id"))
        for rid, row in revisions.items()
        if row.get("viewpoint_id")
    }
    for vid, v in viewpoints.items():
        revision_to_viewpoint.setdefault(str(v.get("current_revision_id")), vid)

    members: dict[str, list[dict[str, Any]]] = {}
    for link in store.list_records("viewpoint_claim_links"):
        if link.get("link_type") not in MEMBER_LINK_TYPES:
            continue
        if str(link.get("effective_state") or "active") != "active":
            continue
        members.setdefault(str(link.get("viewpoint_id")), []).append(link)

    sermons = Sermons(data_base_dir)

    # 这一页正在解的那段经文，用来在一条列了好几处的 scope 里挑出主经文。
    # `scripture` 现在只有 "16:18-19"，讲的是马太福音。
    prefer_book = f"mat-{scripture.split(':')[0]}-" if ":" in scripture else ""
    verses = re.findall(r"\d+", scripture.split(":")[-1]) if ":" in scripture else []
    page_verses = (int(verses[0]), int(verses[-1])) if verses else None

    def occasions(claim_id: str, ref: str) -> dict[str, list[tuple[float, float, str]]]:
        """这条主张的录音，按讲道分开，每段记上它讲的哪一节。"""

        claim = claims.get(claim_id) or {}
        ids = claim.get("eligible_evidence_step_ids") or claim.get("evidence_step_ids") or []
        found: dict[str, list[tuple[float, float, str]]] = {}
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
                    found.setdefault(source_id, []).append((span[0], span[1], ref))
        return found

    def current_revision(structure: dict[str, Any]) -> dict[str, Any] | None:
        """结构的当前版本。

        只看当前版本。26 个结构有 28 个 revision，把 revision 当结构会让两个判断
        各出现两次——一次旧版一次新版，看起来像重复的数据。
        """

        return structure_revisions.get(str(structure.get("current_revision_id")))

    def central_of(revision: dict[str, Any]) -> dict[str, Any] | None:
        return next(
            (f for f in (revision.get("focal_viewpoints") or []) if f.get("structure_role") == "central_claim"),
            None,
        )

    # 同一个中心观点撞上两个结构时，只留一个。
    #
    # CV-d8e50d04e164c2e6c750（捆绑释放的标准已由天上决定）有两个结构：
    # VS-454dd942da1fa7c51657 停在第 1 版、5 个 focal，VS-11ee1cfd2f5252675588 到
    # 了第 2 版、8 个 focal。两者都没退役，也没有 supersedes 链接——结构生成器给
    # 同一个中心观点另起了一个结构，而不是改原来的。读者看到的就是同一句判断出现
    # 两次，其中旧的那个还挂着一条不属于它的 focal（「教会建立在彼得的信仰告
    # 白上」，属于「磐石是什么」那个结构），于是一段讲 Petrus／Petra 的话混进了
    # 捆绑释放。
    #
    # 这里按「版本高、focal 多」留后来的那个。真正的修法在结构生成器，不在这里。
    latest: dict[str, dict[str, Any]] = {}
    for structure in structures.values():
        revision = current_revision(structure)
        central = central_of(revision) if revision else None
        if not revision or not central:
            continue
        viewpoint_id = revision_to_viewpoint.get(str(central.get("viewpoint_revision_id")))
        key = viewpoint_id or str(structure.get("structure_id"))
        rank = (
            int(structure.get("revision") or 0),
            len(revision.get("focal_viewpoints") or []),
            str(structure.get("structure_id")),
        )
        if key not in latest or rank > latest[key]["rank"]:
            latest[key] = {"structure": structure, "rank": rank}

    # 一段经文一个入口，不是一个观点一个入口。
    #
    # 原来按中心观点分组，太16:18-19 分出五组，同一篇讲道在里面出现两到四次，每
    # 次只播它属于那一组的几段。听下来是碎的：（四）2 被切成 2+5+3 段散在三组
    # 里，而教授在课上是一口气把这段经文讲完的。
    #
    # 现在按经文聚合。一篇讲道一行，把它讲这段经文的所有材料并起来，教授原来的
    # 次序就还回去了。观点没丢，挪到幻灯上当标题——经文固定，标题跟着播放位置
    # 换，告诉听的人他此刻在立哪个判断。
    by_sermon: dict[str, dict[str, Any]] = {}
    for chosen in latest.values():
        structure = chosen["structure"]
        revision = current_revision(structure)
        if not revision:
            continue
        focal = revision.get("focal_viewpoints") or []
        central = central_of(revision)
        if not central:
            continue
        central_revision = revisions.get(str(central.get("viewpoint_revision_id"))) or {}
        scope = "".join(central_revision.get("scope", {}).get("scripture_scope") or [])
        pattern = SCRIPTURE_PATTERNS.get(scripture)
        if pattern is not None:
            if not pattern.search(scope):
                continue
        elif scripture and scripture not in scope:
            continue
        proposition = str(central_revision.get("core_proposition") or "")

        # 播整个结构的录音：中心判断，加上支撑它的那几条（正面说明、界线、限定、
        # 方法）。教授论证一个判断时话是连着说的，只挑中心那一句会把理由切掉。
        for entry in focal:
            viewpoint_id = revision_to_viewpoint.get(str(entry.get("viewpoint_revision_id")))
            if not viewpoint_id:
                continue
            for link in members.get(viewpoint_id, []):
                claim_id = str(link.get("claim_id"))
                for source_id, spans in occasions(claim_id, proposition).items():
                    slot = by_sermon.setdefault(source_id, {"spans": []})
                    slot["spans"].extend(spans)

    sermons_out: list[dict[str, Any]] = []
    for source_id, slot in by_sermon.items():
        sermon = sermons.load(source_id, documents.get(source_id) or {})
        merged = stretch(slot["spans"])
        if not merged or not sermon:
            continue
        sermons_out.append({
            "source_id": source_id,
            "transcript_id": sermon["transcript_id"],
            "title": sermon["title"],
            "media_kind": sermon["media_kind"],
            "media_url": sermon["media_url"],
            "stretches": [{"start": a, "end": b} for a, b, _ in merged],
            "judgements": judgement_during(slot["spans"], merged),
            "spoken": spoken_during(merged, sermon),
            "seconds": sum(b - a for a, b, _ in merged),
        })
    sermons_out.sort(key=lambda row: -row["seconds"])

    return {
        "schema_version": "wang_original_audio_index_v2",
        "scripture": scripture,
        # 幻灯上的那一节。一段经文一个入口，所以整页只有这一处经文。
        "reference": f"mat-{scripture.replace(':', '-')}" if ":" in scripture else "",
        "sermons": sermons_out,
    }
