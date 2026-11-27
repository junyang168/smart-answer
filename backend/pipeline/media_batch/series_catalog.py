"""Build a source-bound private reading index, without model calls or publication.

The review manifest owns classification; chapter anchors are reading positions,
not historical lecture numbers. Every source appears once in the primary index.
"""

from __future__ import annotations

import argparse
from collections import Counter
import html
import json
from pathlib import Path
import re

from .runtime import atomic, file_sha

EXPOSITION = ["羅馬書釋經", "約翰福音釋經", "馬太福音釋經"]
SERIES = EXPOSITION + ["問答系列", "獨立主題系列"]


def build(inputs: Path, review: Path):
    raw = json.loads(inputs.read_text())
    decisions = json.loads(review.read_text())
    if decisions["input_sha256"] != file_sha(inputs):
        raise ValueError("Review belongs to different inputs")
    rows = raw["recordings"]
    ids = [r["recording"] for r in rows]
    entries = decisions["records"]
    if (
        len(set(ids)) != len(ids)
        or len(entries) != len(rows)
        or len({r["recording"] for r in entries}) != len(entries)
        or set(ids) != {r["recording"] for r in entries}
    ):
        raise ValueError("Classification must cover every source exactly once")
    by_id = {r["recording"]: r for r in entries}
    result = []
    for source in rows:
        entry = by_id[source["recording"]]
        out = Path(source["out"])
        for label, name in [
            ("rough_sha256", "rough.json"),
            ("summary_sha256", "rough-summary.json"),
        ]:
            if entry[label] != source[label] or file_sha(out / name) != source[label]:
                raise ValueError("Reviewed source changed: " + source["recording"])
        if entry["series"] not in SERIES:
            raise ValueError("Only five approved series are allowed")
        if entry["series"] in EXPOSITION and not re.fullmatch(
            r"\d+:\d+", entry["anchor"] or ""
        ):
            raise ValueError("Exposition needs a chapter and verse reading anchor")
        if entry["series"] not in EXPOSITION and not entry["primary_topic"]:
            raise ValueError("Independent recording needs a topic")
        transcript = json.loads((out / "rough.json").read_text())
        if (
            not transcript.get("uncorrected")
            or transcript["source_sha256"] != source["source_sha256"]
        ):
            raise ValueError("Unexpected source or review status")
        chunks = transcript["chunks"]
        evidence = []
        seen = set()
        for side, selection in [("開場", chunks[:2]), ("結尾", chunks[-2:])]:
            for chunk in selection:
                if not chunk["text"].strip() or (side, chunk["chunk_id"]) in seen:
                    continue
                seen.add((side, chunk["chunk_id"]))
                evidence.append(
                    dict(
                        side=side,
                        chunk_id=chunk["chunk_id"],
                        start_ms=chunk["start_ms"],
                        end_ms=chunk["end_ms"],
                        text=chunk["text"],
                    )
                )
        result.append(
            {
                **source,
                **entry,
                "boundary_evidence": evidence,
                "href": f"https://dallas-hlc.org/api/sc_api/media_batches/{raw['batch']}/rough/{source['recording']}",
            }
        )

    def sort_key(row):
        if row["series"] in EXPOSITION:
            chapter, verse = map(int, row["anchor"].split(":"))
            return (
                SERIES.index(row["series"]),
                chapter,
                verse,
                row["date"] or "9999",
                row["camp_position"] or 999,
                row["recording"],
            )
        return (
            SERIES.index(row["series"]),
            row["date"] or "9999",
            row["camp_position"] or 999,
            row["recording"],
        )

    result.sort(key=sort_key)
    count = Counter()
    for row in result:
        group = row["series"]
        count[group] += 1
        row["reading_position"] = count[group]
    return dict(
        schema_version="sermon-series-catalog.v1",
        batch=raw["batch"],
        input_sha256=file_sha(inputs),
        review_sha256=file_sha(review),
        source_status="uncorrected-rough-transcripts",
        boundary_check="saved transcript opening and closing chunks; no new listening or transcription",
        counts=dict(count),
        records=result,
    )


def render(catalog):
    e = lambda value: html.escape(str(value), quote=True)
    body = []
    for row in catalog["records"]:
        group = row["series"]
        book = group.replace("釋經", "") if row["series"] in EXPOSITION else ""
        anchor = (
            book + " " + row["anchor"] + "（閱讀錨點）"
            if row["series"] in EXPOSITION
            else row["primary_topic"]
        )
        name = Path(row["name"]).name
        if name.startswith("MTS-G"):
            transcript = json.loads((Path(row["out"]) / "rough.json").read_text())
            parts = transcript.get("source_parts", [])
            labels = [Path(p["path"]).name for p in parts]
            name += "（" + " + ".join(labels) + "）" if labels else "（拼接錄音組）"
        boundary = "".join(
            f"<section><b>{e(c['side'])} · {e(c['chunk_id'])} · 區塊 {c['start_ms']//1000}–{c['end_ms']//1000} 秒</b><p>{e(c['text'])}</p></section>"
            for c in row["boundary_evidence"]
        )
        refs = "；".join(p["reference"] for p in row["summary"]["passages"])
        context = f"錄製日期：{row['date'] or '未核定'}；{row['date_basis']}。同場參考位置：{row['camp_position'] or '未確認'}。"
        note = (
            row["review_note"]
            or "依經文內容、原檔名與開結場編排；閱讀位置不表示跨營會錄音無缺課地連續。"
        )
        search = " ".join(
            [
                name,
                row["name"],
                row["date"],
                row["summary"]["theme"],
                group,
                anchor,
                refs,
                row["primary_topic"] or "",
                row["review_note"],
            ]
        )
        body.append(
            f"<article data-group='{e(group)}' data-search='{e(search.lower())}' data-note='{int(bool(row['review_note']))}'>"
            f"<div class='line'><span class='badge'>{e(group)} · {row['reading_position']}</span><span>{e(row['date'] or '日期未核定')} · {round(row['duration_ms']/60000)} 分鐘</span></div>"
            f"<h2>{e(anchor)}</h2><h3>{e(row['summary']['theme'])}</h3><p class='filename'>{e(name)}</p>"
            f"<p>{e(note)}</p><a href='{e(row['href'])}' target='_blank' rel='noreferrer'>查看粗逐字稿與概要 ↗</a>"
            f"<details><summary>開結場核對依據與來源資訊</summary><p>{e(context)}</p><p>摘要引證經文：{e(refs)}</p>"
            f"<p>來源：{e(row['name'])}</p>{boundary}<small>粗逐字稿 SHA256：{e(row['rough_sha256'])}</small></details></article>"
        )
    options = "".join(f"<option>{e(group)}</option>" for group in SERIES)
    counts = " · ".join(f"{k} {v} 篇" for k, v in catalog["counts"].items())
    return (
        """<!doctype html><html lang='zh-Hant'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>五個講道系列與閱讀順序</title>
<style>body{background:#f3f6fa;color:#18243a;font:16px/1.7 system-ui;margin:0}header,main{max-width:1100px;margin:auto;padding:24px}header{padding-bottom:10px}h1{font-size:28px}h2{font-size:18px;margin:.6rem 0}h3{font-size:20px;margin:.4rem 0}article{background:white;border:1px solid #d7e0eb;border-radius:12px;margin:16px 0;padding:22px}p{margin:.6rem 0}a{color:#234cc5}input,select,button{font:inherit;padding:10px;border:1px solid #bcc9da;border-radius:7px;background:white;max-width:100%}.filters{position:sticky;top:0;background:#f3f6faf5;padding:12px 0;display:flex;gap:10px;flex-wrap:wrap;z-index:1}input{flex:1;min-width:180px}.line{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;color:#586983}.badge{color:#274f92;font-weight:700}.filename,small{overflow-wrap:anywhere;color:#617087}details{margin-top:14px;border-top:1px solid #edf0f6;padding-top:10px}details section{padding:12px;background:#f6f8fc;margin:8px 0}details p{white-space:pre-wrap}summary{cursor:pointer;color:#375a97}[hidden]{display:none!important}aside{background:#e9eff9;border-radius:10px;padding:15px}#count{font-weight:700}@media print{.filters{display:none}article{break-inside:avoid}}</style>
<header><h1>講道系列與閱讀順序</h1><p>羅馬書釋經、約翰福音釋經、馬太福音釋經、問答、獨立主題，共五個系列。釋經按經文排序，問答與獨立主題按日期及同場順序排列。</p>
<aside>這是根據未校對粗逐字稿、摘要及開結場文字核對的整理索引，沒有重新聽音。閱讀順序不是原營會的官方講次；沒有因章節接近而宣稱錄音完整連續。洗禮及主日專題歸獨立主題系列；問答與已確認的問答延伸歸問答系列。主題標籤保留，可搜尋查找。</aside>
<p>"""
        + e(counts)
        + """</p></header><main><div class='filters'><input id='search' aria-label='搜尋' placeholder='搜尋主題、經文、檔名或日期'><select id='group' aria-label='系列'><option value=''>全部</option>"""
        + options
        + """</select><label><input type='checkbox' id='notes'>只看歸類修正與注意事項</label></div><p id='count'></p>"""
        + "".join(body)
        + """</main><script>
const rows=[...document.querySelectorAll('article')];function filter(){const q=document.getElementById('search').value.trim().toLowerCase(),g=document.getElementById('group').value,n=document.getElementById('notes').checked;let count=0;for(const r of rows){r.hidden=!!((g&&r.dataset.group!==g)||(n&&r.dataset.note!=='1')||(q&&!r.dataset.search.includes(q)));if(!r.hidden)count++;}document.getElementById('count').textContent='顯示 '+count+' / '+rows.length+' 篇';}for(const id of ['search','group','notes'])document.getElementById(id).addEventListener('input',filter);filter();</script></html>"""
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--inputs", type=Path, required=True)
    p.add_argument("--review", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    catalog = build(args.inputs, args.review)
    args.output.mkdir(parents=True, exist_ok=True)
    atomic(args.output / "catalog.json", catalog)
    (args.output / "catalog.html").write_text(render(catalog))
    print(
        json.dumps(
            dict(counts=catalog["counts"], html=str(args.output / "catalog.html")),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
