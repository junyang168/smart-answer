"""Series -> Unit argument reader for human acceptance review (zero system IDs on screen).

Reads staged Layer 1 segmentation + #845 unit-argument.json + optional fidelity/roundtrip
reports under ``DATA_BASE_PATH / "unit_argument_series" / <series_id>``.
The only write is appending an owner acceptance ruling under ``DATA_BASE_PATH / "argument_layer"``.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from backend.pipeline import original_audio_index as audio

SERIES_DIR_NAME = "unit_argument_series"
RULINGS_REL = Path("argument_layer") / "series-unit-rulings.json"
VERDICTS = {"approved", "needs_revision"}

RELATION_IN_LABEL = {
    "supports": "理由／依據",
    "illustrates": "例證說明",
    "refutes": "他反對的看法",
    "qualifies": "限定條件",
    "contextualizes": "背景脈絡",
    "answers": "回答來源",
}
RELATION_OUT_LABEL = {
    "supports": "支持",
    "illustrates": "舉例說明",
    "refutes": "反駁",
    "qualifies": "限定",
    "contextualizes": "交代背景",
    "answers": "回答問題",
}
NODE_KIND_LABEL = {
    "claim": "論點",
    "evidence": "依據",
    "observation": "他指出",
    "example": "例證",
    "position": "他反對的看法",
    "question": "問題",
}


def _parse_clock_seconds(value: str | None) -> float | None:
    if not value:
        return None
    parts = value.strip().split(":")
    try:
        nums = [float(x) for x in parts]
    except ValueError:
        return None
    if len(nums) == 3:
        return round(nums[0] * 3600 + nums[1] * 60 + nums[2], 1)
    if len(nums) == 2:
        return round(nums[0] * 60 + nums[1], 1)
    return None


def _short_lecture_label(source_id: str, lecture_code: str | None = None) -> str:
    if lecture_code and re.fullmatch(r"L(\d+)", lecture_code):
        return f"第 {lecture_code[1:]} 講"
    m = re.search(r"(\d+)\s*$", source_id.strip())
    if m:
        return f"第 {m.group(1)} 講"
    return source_id


def _load_rulings(data_base_dir: Path) -> list[dict[str, Any]]:
    path = Path(data_base_dir) / RULINGS_REL
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def record_unit_ruling(
    data_base_dir: Path,
    series_id: str,
    unit_id: str,
    verdict: str,
    note: str = "",
) -> dict[str, Any]:
    if verdict not in VERDICTS:
        raise ValueError(f"invalid verdict: {verdict}")
    path = Path(data_base_dir) / RULINGS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = _load_rulings(data_base_dir)
    entry = {
        "series_id": series_id,
        "unit_id": unit_id,
        "verdict": verdict,
        "note": note.strip(),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    rows.append(entry)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return entry


def _latest_rulings(data_base_dir: Path, series_id: str) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in _load_rulings(data_base_dir):
        if row.get("series_id") == series_id:
            latest[row["unit_id"]] = row
    return latest


def _series_root(data_base_dir: Path) -> Path:
    return Path(data_base_dir) / SERIES_DIR_NAME


def list_series(data_base_dir: Path) -> list[dict[str, Any]]:
    root = _series_root(data_base_dir)
    if not root.exists():
        return []
    out: list[dict[str, Any]] = []
    for folder in sorted(root.iterdir()):
        seg_file = folder / "segmentation.json"
        if not folder.is_dir() or not seg_file.exists():
            continue
        seg = json.loads(seg_file.read_text(encoding="utf-8"))
        units = seg.get("units", [])
        ready = sum(1 for u in units if (folder / "units" / u["unit_id"] / "unit-argument.json").exists())
        rulings = _latest_rulings(data_base_dir, folder.name)
        approved = sum(1 for r in rulings.values() if r.get("verdict") == "approved")
        out.append(
            {
                "id": folder.name,
                "title": seg.get("series") or folder.name,
                "lecture_count": len(seg.get("sources", [])),
                "unit_count": len(units),
                "ready_count": ready,
                "approved_count": approved,
            }
        )
    return out


def get_series(data_base_dir: Path, series_id: str) -> dict[str, Any]:
    folder = _series_root(data_base_dir) / series_id
    seg_file = folder / "segmentation.json"
    if not seg_file.exists():
        raise ValueError(f"series not found: {series_id}")
    seg = json.loads(seg_file.read_text(encoding="utf-8"))
    rulings = _latest_rulings(data_base_dir, series_id)
    src_by_lec = {s["lecture"]: s for s in seg.get("sources", [])}

    unit_rows: list[dict[str, Any]] = []
    for u in seg.get("units", []):
        uid = u["unit_id"]
        udir = folder / "units" / uid
        arg_file = udir / "unit-argument.json"
        fid_file = udir / "fidelity_summary.json"
        rtv_file = udir / "roundtrip_diff.json"

        lectures_in_unit: list[str] = []
        for rng in u.get("ranges", []):
            lec = rng.split("-")[0]
            if lec not in lectures_in_unit:
                lectures_in_unit.append(lec)

        ready = arg_file.exists()
        node_count = edge_count = claim_count = 0
        if ready:
            cand = json.loads(arg_file.read_text(encoding="utf-8"))
            node_count = len(cand.get("nodes", {}))
            edge_count = len(cand.get("edges", []))
            claim_count = sum(1 for n in cand.get("nodes", {}).values() if n.get("kind") == "claim")

        fidelity_summary = None
        if fid_file.exists():
            fd = json.loads(fid_file.read_text(encoding="utf-8"))
            fidelity_summary = {
                "faithful": fd.get("faithful", 0),
                "total": fd.get("total_nodes", 0),
                "changed": fd.get("changed", 0),
                "added": fd.get("added", 0),
            }

        roundtrip_summary = None
        if rtv_file.exists():
            rd = json.loads(rtv_file.read_text(encoding="utf-8"))
            m = rd.get("summary_metrics", {})
            roundtrip_summary = {
                "core_covered": m.get("core_units_covered", 0),
                "core_total": m.get("core_units_total", 0),
                "distortions": m.get("distortion_count", 0),
                "omissions": m.get("omission_count", 0),
            }

        unit_rows.append(
            {
                "unit_id": uid,
                "title": u.get("title", uid),
                "passage": u.get("passage", ""),
                "chars": u.get("chars", 0),
                "cross_lecture": bool(u.get("cross_lecture")),
                "lectures": [_short_lecture_label(src_by_lec.get(l, {}).get("source_id", l), l) for l in lectures_in_unit],
                "ready": ready,
                "node_count": node_count,
                "edge_count": edge_count,
                "claim_count": claim_count,
                "fidelity": fidelity_summary,
                "roundtrip": roundtrip_summary,
                "ruling": rulings.get(uid),
            }
        )

    non_arg_rows = [
        {"pk": item["pk"], "reason": item["reason"]}
        for item in seg.get("non_argument_paragraphs", [])
        if item.get("reason") != "empty"
    ]
    return {
        "id": series_id,
        "title": seg.get("series") or series_id,
        "lectures": [
            {"lecture": s["lecture"], "label": _short_lecture_label(s["source_id"], s["lecture"]), "title": s["source_id"]}
            for s in seg.get("sources", [])
        ],
        "units": unit_rows,
        "non_argument_shells": non_arg_rows,
    }


def _resolve_media_map(data_base_dir: Path, sources: list[dict[str, Any]], lec_code_by_sid: dict[str, str]) -> dict[str, dict[str, Any]]:
    sermons = audio.Sermons(Path(data_base_dir))
    media: dict[str, dict[str, Any]] = {}
    for s in sources:
        sid = s["source_id"]
        info = sermons._media_for(sid)
        media[sid] = {
            "source_id": sid,
            "label": _short_lecture_label(sid, lec_code_by_sid.get(sid)),
            "title": sid,
            "media_url": info.get("media_url"),
            "duration": info.get("media_duration"),
        }
    return media


def get_unit(data_base_dir: Path, series_id: str, unit_id: str) -> dict[str, Any]:
    folder = _series_root(data_base_dir) / series_id
    seg_file = folder / "segmentation.json"
    arg_file = folder / "units" / unit_id / "unit-argument.json"
    if not seg_file.exists() or not arg_file.exists():
        raise ValueError(f"unit not ready: {series_id}/{unit_id}")

    seg = json.loads(seg_file.read_text(encoding="utf-8"))
    unit_meta = next((u for u in seg.get("units", []) if u["unit_id"] == unit_id), None)
    if not unit_meta:
        raise ValueError(f"unit metadata missing: {unit_id}")

    cand = json.loads(arg_file.read_text(encoding="utf-8"))
    lec_code_by_sid = {s["source_id"]: s["lecture"] for s in seg.get("sources", [])}
    media = _resolve_media_map(data_base_dir, cand.get("sources", []), lec_code_by_sid)

    sents = cand["sentences"]
    nodes = cand["nodes"]
    edges = cand["edges"]

    # Optional fidelity & roundtrip reports
    udir = folder / "units" / unit_id
    fidelity_by_node: dict[str, dict[str, Any]] = {}
    fidelity_overview = None
    if (udir / "fidelity_summary.json").exists():
        fd = json.loads((udir / "fidelity_summary.json").read_text(encoding="utf-8"))
        fidelity_overview = {
            "total": fd.get("total_nodes", 0),
            "faithful": fd.get("faithful", 0),
            "changed": fd.get("changed", 0),
            "added": fd.get("added", 0),
            "findings": fd.get("findings", []),
        }
        for f in fd.get("findings", []):
            fidelity_by_node[f["id"]] = f

    roundtrip_overview = None
    if (udir / "roundtrip_diff.json").exists():
        rd = json.loads((udir / "roundtrip_diff.json").read_text(encoding="utf-8"))
        roundtrip_overview = {
            "metrics": rd.get("summary_metrics", {}),
            "omissions": rd.get("core_argument_omissions", []),
            "distortions": rd.get("stance_and_logic_distortions", []),
            "noise": rd.get("non_argument_noise", []),
        }

    def _first_pos(at_list: list[int]) -> int:
        return min(at_list) if at_list else 10**9

    def _quotes_for_positions(at_list: list[int]) -> list[dict[str, Any]]:
        if not at_list:
            return []
        ordered = sorted(set(at_list))
        grouped: list[dict[str, Any]] = []
        for pos in ordered:
            row = sents[pos - 1]
            sid = row["source_id"]
            if (
                grouped
                and grouped[-1]["source_id"] == sid
                and grouped[-1]["paragraph"] == row["paragraph"]
                and grouped[-1]["_last_pos"] == pos - 1
            ):
                grouped[-1]["text"] += row["text"]
                grouped[-1]["_last_pos"] = pos
            else:
                grouped.append(
                    {
                        "text": row["text"],
                        "source_id": sid,
                        "lecture_label": _short_lecture_label(sid, lec_code_by_sid.get(sid)),
                        "paragraph": row["paragraph"],
                        "start": _parse_clock_seconds(row.get("time")),
                        "_last_pos": pos,
                    }
                )
        for g in grouped:
            g.pop("_last_pos", None)
        return grouped

    # Order Claims chronologically
    claim_ids = [nid for nid, n in nodes.items() if n["kind"] == "claim"]
    claim_ids.sort(key=lambda nid: _first_pos(nodes[nid]["at"]))
    point_no_by_id = {nid: i for i, nid in enumerate(claim_ids, 1)}

    # Order Questions chronologically
    q_ids = [nid for nid, n in nodes.items() if n["kind"] == "question"]
    q_ids.sort(key=lambda nid: _first_pos(nodes[nid]["at"]))
    q_no_by_id = {nid: i for i, nid in enumerate(q_ids, 1)}
    q_state_by_id = {row["question_id"]: row for row in cand.get("question_states", [])}

    # Build questions list with answers
    questions_out: list[dict[str, Any]] = []
    for qid in q_ids:
        qn = nodes[qid]
        first_row = sents[_first_pos(qn["at"]) - 1]
        st = q_state_by_id.get(qid, {"state": "open", "remaining": ""})
        answers = []
        for e in edges:
            if e["to_id"] == qid and e["relation"] == "answers":
                src_node = nodes[e["from_id"]]
                answers.append(
                    {
                        "point_no": point_no_by_id.get(e["from_id"]),
                        "statement": src_node["statement"],
                        "reason": e["reason"],
                        "lecture_label": _short_lecture_label(
                            sents[_first_pos(src_node["at"]) - 1]["source_id"],
                            lec_code_by_sid.get(sents[_first_pos(src_node["at"]) - 1]["source_id"]),
                        ),
                    }
                )
        questions_out.append(
            {
                "no": q_no_by_id[qid],
                "statement": qn["statement"],
                "source_id": first_row["source_id"],
                "lecture_label": _short_lecture_label(first_row["source_id"], lec_code_by_sid.get(first_row["source_id"])),
                "start": _parse_clock_seconds(first_row.get("time")),
                "state": st.get("state", "open"),
                "remaining": st.get("remaining", ""),
                "answers": answers,
            }
        )

    # Track which sentences are used by which points
    sent_uses: dict[int, set[int]] = {}
    points_out: list[dict[str, Any]] = []

    for cid in claim_ids:
        pno = point_no_by_id[cid]
        cn = nodes[cid]
        for pos in cn["at"]:
            sent_uses.setdefault(pos, set()).add(pno)
        first_row = sents[_first_pos(cn["at"]) - 1]

        incoming = []
        for e in edges:
            if e["to_id"] == cid:
                src = nodes[e["from_id"]]
                for pos in src["at"]:
                    sent_uses.setdefault(pos, set()).add(pno)
                for pos in e["at"]:
                    sent_uses.setdefault(pos, set()).add(pno)
                kind_label = (
                    "他反對的看法"
                    if src["kind"] == "position" or e["relation"] == "refutes"
                    else RELATION_IN_LABEL.get(e["relation"], NODE_KIND_LABEL.get(src["kind"], "理由"))
                )
                incoming.append(
                    {
                        "kind_label": kind_label,
                        "relation": e["relation"],
                        "from_point_no": point_no_by_id.get(e["from_id"]),
                        "statement": src["statement"],
                        "reason": e["reason"],
                        "quotes": _quotes_for_positions(src["at"]),
                        "fidelity_warning": fidelity_by_node.get(e["from_id"]),
                    }
                )
        # Also include outgoing refutes (this claim refutes an external position)
        outgoing = []
        for e in edges:
            if e["from_id"] == cid:
                dst = nodes[e["to_id"]]
                for pos in e["at"]:
                    sent_uses.setdefault(pos, set()).add(pno)
                if e["relation"] == "refutes" and dst["kind"] == "position":
                    for pos in dst["at"]:
                        sent_uses.setdefault(pos, set()).add(pno)
                    incoming.append(
                        {
                            "kind_label": "他反對的看法",
                            "relation": "refutes",
                            "from_point_no": None,
                            "statement": dst["statement"],
                            "reason": e["reason"],
                            "quotes": _quotes_for_positions(dst["at"]),
                            "fidelity_warning": fidelity_by_node.get(e["to_id"]),
                        }
                    )
                else:
                    outgoing.append(
                        {
                            "relation_label": RELATION_OUT_LABEL.get(e["relation"], e["relation"]),
                            "to_point_no": point_no_by_id.get(e["to_id"]),
                            "to_question_no": q_no_by_id.get(e["to_id"]),
                            "to_statement": dst["statement"],
                            "reason": e["reason"],
                        }
                    )

        points_out.append(
            {
                "no": pno,
                "statement": cn["statement"],
                "source_id": first_row["source_id"],
                "lecture_label": _short_lecture_label(first_row["source_id"], lec_code_by_sid.get(first_row["source_id"])),
                "start": _parse_clock_seconds(first_row.get("time")),
                "quotes": _quotes_for_positions(cn["at"]),
                "incoming": incoming,
                "outgoing": outgoing,
                "fidelity_warning": fidelity_by_node.get(cid),
            }
        )

    # Build multi-lecture transcript view
    transcript_lectures: list[dict[str, Any]] = []
    cur_sid = None
    cur_para = None
    for idx, row in enumerate(sents, 1):
        sid = row["source_id"]
        if sid != cur_sid:
            cur_sid = sid
            cur_para = None
            transcript_lectures.append(
                {
                    "source_id": sid,
                    "lecture_label": _short_lecture_label(sid, lec_code_by_sid.get(sid)),
                    "title": sid,
                    "paragraphs": [],
                }
            )
        if row["paragraph"] != cur_para:
            cur_para = row["paragraph"]
            transcript_lectures[-1]["paragraphs"].append(
                {
                    "paragraph": cur_para,
                    "pieces": [],
                }
            )
        transcript_lectures[-1]["paragraphs"][-1]["pieces"].append(
            {
                "text": row["text"],
                "start": _parse_clock_seconds(row.get("time")),
                "uses": sorted(sent_uses.get(idx, ())),
            }
        )

    rulings = _latest_rulings(data_base_dir, series_id)
    return {
        "series_id": series_id,
        "series_title": seg.get("series") or series_id,
        "unit_id": unit_id,
        "title": unit_meta.get("title", unit_id),
        "passage": unit_meta.get("passage", ""),
        "chars": unit_meta.get("chars", 0),
        "cross_lecture": bool(unit_meta.get("cross_lecture")),
        "media": media,
        "questions": questions_out,
        "points": points_out,
        "transcript": transcript_lectures,
        "fidelity": fidelity_overview,
        "roundtrip": roundtrip_overview,
        "ruling": rulings.get(unit_id),
    }
