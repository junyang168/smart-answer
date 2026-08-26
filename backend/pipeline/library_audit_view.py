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
FOLLOWUP_GROUPS: dict[str, dict[str, str]] = {
    "claim_support": {
        "title": "主張撐不撐得住它引的證據",
        "note": "獨立模型抽樣判讀。有異議不等於錯，等於需要人看一眼。",
    },
    "viewpoint_identity": {
        "title": "判為同一個觀點的主張，真值條件對不對得上",
        "note": "只問 equivalent_full 與 equivalent_component 兩種成員連結。",
    },
    "component_locator": {
        "title": "成分定位對不上主張原文",
        "note": "觀點的成員資格掛在這段字上，所以它必須是主張裡的一段連續文字。",
    },
    "fragment_anchor": {
        "title": "引文在原件裡對不上所記位置",
        "note": "直接打開磁碟上的逐字稿比對，不採信 anchor_state 與存下來的雜湊。",
    },
    "dangling_reference": {
        "title": "依賴解不開",
        "note": "引用指向的物件不在庫裡，任何狀態都沒有。",
    },
}

#: The order the groups appear in. Judgement first, then mechanics: a claim that
#: overreaches its evidence is a different day's work from a route pointing at a
#: plan that was never ingested, and the first kind is what this audit exists
#: for.
GROUP_ORDER = [
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
    "stitched": "把主張裡不相鄰的兩截接了起來",
    "punctuation_variant": "只差在標點寫法",
    "punctuation_only": "只差在省略號或空白的寫法",
    "misplaced": "引文在原件裡，但不在所記位置",
    "deleted_text_only": "引文只存在於校對者劃掉的文字裡",
    "absent": "引文不在這份原件裡",
    "no_excerpt": "片段沒有引文，無從核對",
    "no_source_file": "找不到這份原件",
    "span_offsets_wrong": "字元位置框到的不是它宣稱的那段",
    "unresolvable_dependency": "引用指向的物件不在庫裡",
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
        rows.append({
            "key": "verbatim",
            "layer": 1,
            "name": first["name"],
            "kind": "ratio",
            "passed": first["passed"],
            "total": first["total"],
            "unit": "條片段",
            "question": "片段的 verbatim_excerpt 在原件裡真的存在於所記位置嗎",
            "detail": [
                {"label": code, "count": count, "text": VERDICT_TEXT.get(code, code)}
                for code, count in sorted(first.get("counts", {}).items(), key=lambda kv: -kv[1])
                if code not in ("pass", "one_locator_only")
            ],
        })
    second = layers.get("2")
    if second:
        rows.append({
            "key": "coverage",
            "layer": 2,
            "name": second["name"],
            "kind": "ratio",
            "passed": second["checked_clean"],
            "total": second["checked_objects"],
            "unit": "個當前物件",
            "question": "已寫入的內容，它依賴的東西都還在嗎",
            "detail": [
                {
                    "label": "references_dangling",
                    "count": second["references_dangling"],
                    "text": "引用指向的物件不在庫裡",
                },
                {
                    "label": "references_to_retired",
                    "count": second["references_to_retired"],
                    "text": "引用指向已經 retire 的物件（記錄還在，只是不再是當前版本）",
                },
                {
                    "label": "component_locator_findings",
                    "count": len(second.get("component_locator_findings") or []),
                    "text": "成分定位對不上主張原文",
                },
            ],
        })
    for key, layer_id, unit, question in (
        ("claims", "3", "條主張", "這條主張能否從它所引的證據推出"),
        ("viewpoints", "4", "個觀點", "判為同一觀點的主張，真值條件是否真的一致"),
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
            "detail": [],
        })
    return rows


def _followups(layers: dict[str, Any]) -> list[dict[str, Any]]:
    """Everything a person still has to decide, grouped by what kind of work it is."""

    items: dict[str, list[dict[str, Any]]] = {key: [] for key in GROUP_ORDER}

    for entry in (layers.get("3") or {}).get("results", []):
        if entry.get("verdict") != "disputed":
            continue
        items["claim_support"].append({
            "object_id": entry["claim_id"],
            "collection": "claims",
            "verdict": _verdict(str(entry.get("issue") or "other")),
            "reason": entry.get("reason", ""),
            "evidence": [
                {"label": "statement", "text": entry.get("statement", "")},
                {"label": "quote", "text": entry.get("quote", "")},
                {"label": "evidence_step_ids", "text": "、".join(entry.get("evidence_step_ids") or [])},
                {"label": "review_status", "text": entry.get("review_status", "")},
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
                {"label": "core_proposition", "text": entry.get("core_proposition", "")},
                {"label": "current_revision_id", "text": entry.get("revision_id", "")},
                {"label": "成員主張", "text": f"{entry.get('linked_claims', 0)} 條"},
                {
                    "label": "claim_ids_in_question",
                    "text": "、".join(entry.get("claim_ids_in_question") or []),
                },
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
                {"label": "statement_component", "text": entry.get("component", "")},
                {"label": "claim.statement", "text": entry.get("statement", "")},
                {"label": "claim_id", "text": entry.get("claim_id", "")},
            ],
        })

    for entry in (layers.get("1") or {}).get("findings", []):
        items["fragment_anchor"].append({
            "object_id": entry["fragment_id"],
            "collection": "source_fragments",
            "verdict": _verdict(str(entry.get("verdict") or "other")),
            "reason": entry.get("detail", ""),
            "evidence": [
                {"label": "verbatim_excerpt", "text": entry.get("excerpt", "")},
                {"label": "source_id", "text": entry.get("source_id", "")},
                {"label": "paragraph_key", "text": str(entry.get("paragraph_key") or "")},
                {"label": "anchor_state", "text": str(entry.get("anchor_state") or "")},
            ],
        })

    for entry in second.get("dangling", []):
        items["dangling_reference"].append({
            "object_id": entry["object_id"],
            "collection": entry.get("collection", ""),
            "verdict": _verdict("unresolvable_dependency"),
            "reason": "",
            "evidence": [
                {"label": entry.get("field", "→"), "text": entry.get("value", "")},
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
        "followups": _followups(layers),
    }


def load_view(reports_root: Path) -> dict[str, Any] | None:
    """The newest run, reshaped. `None` when the audit has never been run."""

    run = latest_run(reports_root)
    if run is None:
        return None
    audit = json.loads((run / "audit.json").read_text(encoding="utf-8"))
    return build_view(audit, run.name)
