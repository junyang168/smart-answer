"""Turn one audit run's `audit.json` into what the admin page shows.

`scripts/audit-library.py` writes three files and stops. Reading them means
finding the newest timestamp on the machine and `cat`-ing a text file, which
nobody does -- and the constraint those files carry is "the library does not
move on to new passages until it passes", so a report nobody opens blocks
nothing.

This module does no measuring. Every number below was decided by the audit,
which deliberately shares no code with `backend/`; this only reshapes what it
already wrote. Reading its output is not the same as reusing its code, and the
independence the audit claims is untouched by anything here.

Two rules the page inherits from the extraction health view:

* **No thresholds and no traffic lights.** The ratios are measured, not graded.
  One run is not enough to know where a line belongs, and a line drawn today
  would be treated as meaningful tomorrow.
* **A finding must be actionable where it is read.** Every follow-up carries the
  record id, what is wrong with it, and the evidence for saying so -- because
  the alternative is a list of ids that sends the reader back to the database.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


#: What each follow-up group is, in one line, for a reader who has not read the
#: audit's own documentation. Keyed by the `kind` the audit assigns.
FOLLOWUP_GROUPS: dict[str, dict[str, Any]] = {
    "claim_support": {
        "title": "這條主張，證據撐不住",
        "note": "主張說的比它引的證據多。要人讀一遍才能決定是改主張還是補證據。",
        "needs_human": True,
    },
    "viewpoint_identity": {
        "title": "這幾條主張，可能不是同一個觀點",
        "note": "被判成同一個觀點的主張，說的其實不是同一件事。要人決定該不該拆開。",
        "needs_human": True,
    },
    "not_judged": {
        "title": "審計模型拒答，這幾條沒有判讀",
        "note": (
            "安全過濾器擋下了 prompt。擋掉的多半是離婚、情慾這類牧養上敏感的題目，"
            "而那正是最需要有人看過的。這幾條沒有人也沒有機器看過。"
        ),
        "needs_human": True,
    },
    "component_locator": {
        "title": "觀點指的那幾段字，在主張裡對不上",
        "note": (
            "觀點說「主張的這幾段和我等價」，並記下每一段的字元位置。"
            "位置框到的字不是它說的那段，或摘要與那幾段接不起來。"
        ),
        "needs_human": False,
    },
    "fragment_anchor": {
        "title": "引文在逐字稿裡對不上",
        "note": "打開磁碟上的原件核對，這幾條的引文不在它自己記的位置上。",
        "needs_human": False,
    },
    "dangling_reference": {
        "title": "指向的東西不在庫裡",
        "note": "這些記錄引用了一批從來沒進庫的計劃與決定。重跑那批 ingest 就沒了。",
        "needs_human": False,
    },
}

#: The order the groups appear in. Judgement first, then mechanics: a claim that
#: overreaches its evidence is a different day's work from a route pointing at a
#: plan that was never ingested, and the first kind is what this audit exists
#: for.
GROUP_ORDER = [
    "not_judged",
    "claim_support",
    "viewpoint_identity",
    "component_locator",
    "fragment_anchor",
    "dangling_reference",
]

#: A verdict word, said in a way that does not require reading the source.
VERDICT_TEXT = {
    "overreach": "結論走得比證據遠",
    "unsupported": "證據裡沒有這件事",
    "misattributed": "那不是教授自己的立場",
    "over_merge": "把兩個可分開的命題併成一條",
    "scope_mismatch": "適用範圍對不上",
    "different_proposition": "講的是另一件事",
    "stitched": "這段字不是主張裡的原話，而且沒有記字元位置可以查",
    "component_not_from_spans": "摘要那句話，與它自己記的那幾段字元位置接不起來",
    "punctuation_variant": "只差在標點寫法",
    "punctuation_only": "教授確實說過這句話，只是存下來的字串在省略號或空白上不逐字",
    "misplaced": "引文在原件裡，但不在所記位置",
    "deleted_text_only": "引文只存在於校對者劃掉的文字裡",
    "absent": "引文不在這份原件裡",
    "no_excerpt": "這筆記錄根本沒有存引文，沒有東西可以核對",
    "no_source_file": "找不到這份原件",
    "span_offsets_wrong": "字元位置框到的，不是它說的那段字",
    "unresolvable_dependency": "引用指向的物件不在庫裡",
    "blocked": "審計模型的安全過濾器擋下了這一條，沒有判讀",
    "other": "其他",
}


def latest_run(reports_root: Path) -> Path | None:
    """The newest audit run directory, or `None` when none has been written.

    Directories are named with a UTC timestamp, so sorting by name is sorting by
    time -- and unlike mtime it does not move when something touches the files.
    """

    if not reports_root.is_dir():
        return None
    runs = sorted(
        path for path in reports_root.iterdir() if path.is_dir() and (path / "audit.json").is_file()
    )
    return runs[-1] if runs else None


def _verdict(code: str) -> dict[str, str]:
    return {"code": code, "text": VERDICT_TEXT.get(code, code)}


def _evidence(label: str, field: str, text: Any) -> dict[str, str]:
    """One piece of evidence, named twice on purpose.

    `label` is what the row is, in words, for the person deciding. `field` is
    the store's own name for it, kept alongside because the next step is
    usually a query and `statement_component` is what that query has to say.
    Translating the field name away would make the page readable and the
    follow-up harder.
    """

    return {"label": label, "field": field, "text": str(text or "")}


def _ratio_layers(layers: dict[str, Any]) -> list[dict[str, Any]]:
    """The four headline numbers, in the audit's own words.

    Layers 1 and 2 are ratios over everything they check. Layers 3 and 4 are
    samples, and are reported as "N judged, X disputed" rather than as a
    percentage: 3 of 20 is not 15% of the library, and rendering it as one would
    invite exactly that reading.
    """

    rows: list[dict[str, Any]] = []
    first = layers.get("1")
    if first:
        # Two verdicts are not problems, and listing them as such made the
        # page read as though the library quoted words the professor never
        # said.
        #
        # `no_excerpt` stores no quote, so no check ran -- it leaves the
        # denominator. `punctuation_only` passed the question this layer asks:
        # the professor did say it, in the paragraph recorded, and only the
        # ellipsis is written differently (`……` in the transcript, `…` stored).
        # It counts as found.
        #
        # Neither disappears. Both stay as a quiet line under the number,
        # because a ratio whose denominator quietly shrinks is the exact move
        # this audit exists to catch, and the page does not get to make it
        # either. What changes is that they no longer sit in the list of things
        # to fix.
        counts = first.get("counts", {})
        skipped = counts.get("no_excerpt", 0)
        loose = counts.get("punctuation_only", 0)
        checked = first["total"] - skipped
        found = first["passed"] + loose
        mismatched = checked - found
        notes = []
        if loose:
            notes.append(f"其中 {loose} 條的省略號寫法與原文不同，話是同一句，位置也對。")
        if skipped:
            notes.append(f"另有 {skipped} 筆記錄沒有存引文，沒有東西可以核對，不算在裡面。")
        rows.append({
            "key": "verbatim",
            "layer": 1,
            "name": first["name"],
            "kind": "ratio",
            "passed": found,
            "total": checked,
            "skipped": skipped,
            "skipped_note": "".join(notes),
            "unit": "條引文",
            "question": "庫裡存的每一句教授原話，逐字稿裡真的有，而且就在它記的那一段",
            "headline": (
                f"{checked:,} 條引文，全部在逐字稿的所記位置找得到。"
                if mismatched == 0
                else f"{checked:,} 條引文裡，{mismatched} 條在逐字稿裡找不到。"
            ),
            "detail": [
                {"label": code, "count": count, "text": VERDICT_TEXT.get(code, code)}
                for code, count in sorted(counts.items(), key=lambda kv: -kv[1])
                if code not in ("pass", "one_locator_only", "no_excerpt", "punctuation_only")
            ],
        })
    second = layers.get("2")
    if second:
        broken = second["checked_objects"] - second["checked_clean"]
        rows.append({
            "key": "coverage",
            "layer": 2,
            "name": second["name"],
            "kind": "ratio",
            "passed": second["checked_clean"],
            "total": second["checked_objects"],
            "unit": "筆記錄",
            # "提到了庫裡沒有的東西" left the reader guessing what kind of thing.
            # Records point at each other by id -- a route names its plan, a
            # link names its claim -- and the sentence has to say that, or the
            # number means nothing without someone explaining it.
            "question": "記錄之間互相引用，被引用的那一筆還在嗎",
            "headline": (
                f"{second['checked_objects']:,} 筆記錄，它們指向的每一筆都還在。"
                if broken == 0
                else f"{second['checked_objects']:,} 筆記錄裡，{broken} 筆指向另一筆記錄，"
                "而那一筆不在庫裡。"
            ),
            "detail": [
                {
                    "label": "references_dangling",
                    "count": second["references_dangling"],
                    "text": "個 id 指向一筆從來沒進庫的記錄（幾乎全是同一批沒 ingest 的計劃）",
                },
                {
                    "label": "references_to_retired",
                    "count": second["references_to_retired"],
                    "text": "個 id 指向舊版本——那一筆還在庫裡，只是已經被新版取代",
                },
                {
                    "label": "component_locator_findings",
                    "count": len(second.get("component_locator_findings") or []),
                    "text": "個觀點引了一句話，而那句話不在它所引的主張裡",
                },
            ],
        })
    for key, layer_id, unit, question, verb in (
        ("claims", "3", "條主張", "主張說的，證據撐得住嗎", "條"),
        ("viewpoints", "4", "個觀點", "判成同一個觀點的，真的是同一件事嗎", "個"),
    ):
        layer = layers.get(layer_id)
        if not layer:
            continue
        rows.append({
            "key": key,
            "layer": int(layer_id),
            "name": layer["name"],
            "kind": "sample",
            "judged": layer["judged"],
            "disputed": layer["disputed"],
            "population": layer["population"],
            "model_errors": layer.get("model_errors", 0),
            "unit": unit,
            "question": question,
            # 全查與抽樣說法不能一樣。「20 條中 3 條」是抽到的那一批，「1,363
            # 條中 31 條」是範圍內的全部——後者才可以拿來說整批對不對，前者不行。
            "complete": bool(layer.get("complete")),
            "headline": _sample_headline(layer, verb),
            "note": (
                f"範圍內的 {layer['population']:,} {unit}全部查過"
                + (
                    f"，其中 {layer['model_errors']} {verb}模型沒答成。"
                    if layer.get("model_errors")
                    else "。"
                )
                if layer.get("complete")
                else f"全部 {layer['population']:,} {unit}裡抽的，這個數字說的是抽到的這一批。"
            ),
            "detail": [],
        })
    return rows


def _sample_headline(layer: dict[str, Any], verb: str) -> str:
    judged = layer["judged"]
    disputed = layer["disputed"]
    whole = layer.get("complete")
    if disputed == 0:
        return f"{'全部' if whole else '抽查'} {judged:,} {verb}，都沒問題。"
    return (
        f"{'全部' if whole else '抽查'} {judged:,} {verb}，"
        f"{disputed} {verb}看起來不對。"
    )


def _followups(layers: dict[str, Any]) -> list[dict[str, Any]]:
    """Everything a person still has to decide, grouped by what kind of work it is."""

    items: dict[str, list[dict[str, Any]]] = {key: [] for key in GROUP_ORDER}

    for entry in (layers.get("3") or {}).get("results", []):
        if entry.get("verdict") == "blocked":
            items["not_judged"].append({
                "object_id": entry["claim_id"],
                "collection": "claims",
                "verdict": _verdict("blocked"),
                "reason": entry.get("reason", ""),
                "evidence": [
                    _evidence("主張原文", "statement", entry.get("statement")),
                    _evidence("目前狀態", "review_status", entry.get("review_status")),
                ],
            })
            continue
        if entry.get("verdict") != "disputed":
            continue
        items["claim_support"].append({
            "object_id": entry["claim_id"],
            "collection": "claims",
            "verdict": _verdict(str(entry.get("issue") or "other")),
            "reason": entry.get("reason", ""),
            "evidence": [
                _evidence("主張原文", "statement", entry.get("statement")),
                _evidence("模型據以判斷的那句", "quote", entry.get("quote")),
                _evidence("它引的證據", "evidence_step_ids", "、".join(entry.get("evidence_step_ids") or [])),
                _evidence("目前狀態", "review_status", entry.get("review_status")),
            ],
        })

    for entry in (layers.get("4") or {}).get("results", []):
        if entry.get("verdict") != "disputed":
            continue
        items["viewpoint_identity"].append({
            "object_id": entry["viewpoint_id"],
            "collection": "canonical_viewpoints",
            "verdict": _verdict(str(entry.get("issue") or "other")),
            "reason": entry.get("reason", ""),
            "evidence": [
                _evidence("這個觀點說的是", "core_proposition", entry.get("core_proposition")),
                _evidence("目前版本", "current_revision_id", entry.get("revision_id")),
                _evidence("底下掛了幾條主張", "linked_claims", f"{entry.get('linked_claims', 0)} 條"),
                _evidence(
                    "有問題的是這幾條",
                    "claim_ids_in_question",
                    "、".join(entry.get("claim_ids_in_question") or []),
                ),
            ],
        })

    second = layers.get("2") or {}
    for entry in second.get("component_locator_findings", []):
        items["component_locator"].append({
            "object_id": entry["object_id"],
            "collection": "viewpoint_claim_links",
            "verdict": _verdict(str(entry.get("verdict") or "other")),
            "reason": "",
            "evidence": [
                _evidence("觀點指的那幾段", "statement_component", entry.get("component")),
                _evidence(
                    "接起來實際是",
                    "canonical_spans",
                    entry.get("spans_joined")
                    or "；".join(
                        f"記著「{s.get('expected')}」，位置上是「{s.get('at_offsets')}」"
                        for s in entry.get("spans") or []
                    ),
                ),
                _evidence("主張的原話", "claim.statement", entry.get("statement")),
                _evidence("哪一條主張", "claim_id", entry.get("claim_id")),
            ],
        })

    for entry in (layers.get("1") or {}).get("findings", []):
        if entry.get("verdict") in ("no_excerpt", "punctuation_only"):
            # Nothing to follow up. One stores no quote at all, so there is no
            # mismatch to resolve; the other is the professor's sentence in the
            # right paragraph with a different ellipsis. Neither is work.
            continue
        items["fragment_anchor"].append({
            "object_id": entry["fragment_id"],
            "collection": "source_fragments",
            "verdict": _verdict(str(entry.get("verdict") or "other")),
            "reason": entry.get("detail", ""),
            "evidence": [
                _evidence("這段引文", "verbatim_excerpt", entry.get("excerpt")),
                _evidence("哪一篇", "source_id", entry.get("source_id")),
                _evidence("記在第幾段", "paragraph_key", entry.get("paragraph_key")),
                _evidence("庫裡標的狀態", "anchor_state", entry.get("anchor_state")),
            ],
        })

    for entry in second.get("dangling", []):
        items["dangling_reference"].append({
            "object_id": entry["object_id"],
            "collection": entry.get("collection", ""),
            "verdict": _verdict("unresolvable_dependency"),
            "reason": "",
            "evidence": [
                _evidence("指向", entry.get("field", "→"), entry.get("value")),
            ],
            # What the reference points at. Ninety-seven rows that all point at
            # the same missing plan are one problem, not ninety-seven, so the
            # page groups on this rather than listing every referring record.
            "target": entry.get("value", ""),
        })

    groups: list[dict[str, Any]] = []
    for key in GROUP_ORDER:
        rows = items[key]
        if not rows:
            continue
        group = {
            "kind": key,
            "count": len(rows),
            **FOLLOWUP_GROUPS[key],
            "items": rows,
        }
        if key == "dangling_reference":
            group["targets"] = _by_target(rows)
        groups.append(group)
    return groups


def _by_target(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dangling references collapsed onto what they fail to reach."""

    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        buckets.setdefault(str(row.get("target") or ""), []).append(row)
    return [
        {
            "target": target,
            "count": len(members),
            "collections": sorted({str(item.get("collection") or "") for item in members}),
            "object_ids": [str(item["object_id"]) for item in members[:20]],
        }
        for target, members in sorted(buckets.items(), key=lambda kv: -len(kv[1]))
    ]


def build_view(audit: dict[str, Any], run_id: str) -> dict[str, Any]:
    """The whole page, from one run's `audit.json`."""

    meta = audit.get("meta") or {}
    layers = audit.get("layers") or {}
    groups = _followups(layers)
    scoped = meta.get("scope") == "current-run"
    return {
        "run_id": run_id,
        "generated_at": meta.get("generated_at"),
        "model": meta.get("model"),
        "seed": meta.get("seed"),
        "scope": {
            "mode": meta.get("scope"),
            "sources": meta.get("sources", 0),
            "sources_out_of_scope": meta.get("sources_out_of_scope", 0),
            "text": (
                "只查 run ledger 說這一輪 ingest 成功的來源"
                if scoped
                else "庫裡全部來源，包含更早批次留下的"
            ),
            "duplicate_sources": [
                {"name": name, "source_ids": ids}
                for name, ids in (meta.get("duplicate_sources") or [])
            ],
        },
        "corpus": {
            "fragments": meta.get("fragments", 0),
            "claims": meta.get("claims", 0),
            "viewpoints": meta.get("viewpoints", 0),
        },
        "layers": _ratio_layers(layers),
        "followups": groups,
        # The one split that decides what happens next. Four items needing a
        # person's judgement and a hundred needing a batch re-run are not the
        # same backlog, and one total of 113 hides which is which.
        "needs_human": sum(g["count"] for g in groups if g["needs_human"]),
        "mechanical": sum(g["count"] for g in groups if not g["needs_human"]),
    }


def load_view(reports_root: Path) -> dict[str, Any] | None:
    """The newest run, reshaped. `None` when the audit has never been run."""

    run = latest_run(reports_root)
    if run is None:
        return None
    audit = json.loads((run / "audit.json").read_text(encoding="utf-8"))
    return build_view(audit, run.name)
