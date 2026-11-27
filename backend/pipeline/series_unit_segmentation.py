"""Series-level non-overlapping logical unit segmentation (Layer 1 upstream of #845)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable

from backend.pipeline import claim_unit_plan as unit_plan
from backend.pipeline.source_projection import project_script

VERSION = "series_unit_segmentation_v1"
PK_RE = re.compile(r"^(L\d+)-(S\d{4})$")
RANGE_RE = re.compile(r"^(L\d+-S\d{4})(?:\.\.(L\d+-S\d{4}))?$")

STEP1_PROMPT = (
    "你是圣经释经讲道逐字稿的目录整理者。输入是一堂讲道按顺序编号的非空段落（pk 与原文）。\n"
    "请为每一段输出一张极简段落卡（严禁漏段、严禁改 pk）：\n"
    "1. summary：40–60 字中文一句话摘要，必须写清本段当前主解经文（与顺带佐证引经区分开），"
    "以及本段是否提出悬念/作业、或承接/收束前序问题。\n"
    "2. is_pure_shell：布尔值。只有当整段完全是纯开场/结束祷告、纯行政报幕或纯场务通知时才为 true；"
    "只要段落内夹带任何实质释经、复述前讲问题或护教短评（哪怕首句是报幕或末句问下课时间），必须为 false。\n"
    "输出必须严格符合给定 JSON Schema。"
)

STEP2_PROMPT = (
    "你是圣经释经系列讲道的逻辑单元切分者。输入是一个连续系列（2–6 讲）的全套段落卡表，"
    "以及每处相邻讲次交界（前讲最后 2 个非外壳段 + 后讲最前 2 个非外壳段）的完整原文。\n"
    "请将全系列切分为平铺、无重叠、100% 覆盖的顶层逻辑单元（units）与纯外壳段落（non_argument_paragraphs）：\n"
    "1. 主解经文推进：主解经文未变、中间引旧约或平行经文作论据的，归入同一单元，不另切。\n"
    "2. 设问的起与落：从抛出张力/问题开始，到显式收束为止。\n"
    "3. 跨讲接缝合并：务必细读交界原文。只要前讲结尾留题、布置作业或截断举例，后讲开头接续作答，"
    "必须合并进同一 cross_lecture=true 单元，并在 seam_evidence 给出两端各一句真实原文逐字子串（10–40 字）。"
    "反之，若后讲明确开启全新独立主题（例如从动淫念转到离婚再婚、从离婚转到爱仇敌），绝不强行合并。\n"
    "4. 混合段保护：任何夹带实质教学或复述的段落（is_pure_shell=false）必须留在 units 内，不得归入 non_argument_paragraphs。\n"
    "5. 夹心短教导不撕裂大单元：若某段短教导夹在跨讲长论证中间（如比喻大论证中段夹着一段如何抓比喻中心），"
    "保留在大单元内，不要把大单元撕成不连续的两截。\n"
    "6. 仅把纯祷告、纯行政通知与 0 字空段放入 non_argument_paragraphs。ranges 格式为 "
    "\"L1-S0010..L1-S0012\" 或单段 \"L1-S0010\"，每个区间不得跨讲（跨讲单元请按讲次列出多个区间）。\n"
    "7. 粒度控制：单个单元尽量控制在约 2.2 万汉字（72KB UTF-8）以内，若同一大主题包含多个完整且可独立成篇的子议题，可在显式子题切换处切分；"
    "反之，不要把仅 1–2 句的简短过渡或零碎补充拆成独立小单元，除非是独立的听众问答。unit_id 统一用 U01、U02… 两位编号。\n"
    "输出必须严格符合给定 JSON Schema。"
)

SEAM_VERIFY_PROMPT = (
    "你是独立的讲道跨讲接缝复核员。上游切分器决定在以下两讲交界处「切开」为两个独立单元。\n"
    "输入是前讲最后 2 个实质教学段与后讲最前 2 个实质教学段的完整原文。\n"
    "请严格判断：后讲开头是否仍在直接续讲、回答或收束前讲末尾未完的同一个问题/论证？\n"
    "- 若是直接续讲（例如前讲截断举例/留作业，后讲紧接解释或解答），输出 should_merge=true, severity=\"high\"。\n"
    "- 若后讲只是礼貌回顾或开启新主题，输出 should_merge=false, severity=\"none\"。\n"
    "evidence_quote 必须摘录支持判断的原文短句。"
)

STEP1_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["cards"],
    "properties": {
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["pk", "summary", "is_pure_shell"],
                "properties": {
                    "pk": {"type": "string"},
                    "summary": {"type": "string"},
                    "is_pure_shell": {"type": "boolean"},
                },
            },
        }
    },
}

STEP2_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["units", "non_argument_paragraphs"],
    "properties": {
        "units": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["unit_id", "title", "passage", "cross_lecture", "seam_evidence", "ranges"],
                "properties": {
                    "unit_id": {"type": "string"},
                    "title": {"type": "string"},
                    "passage": {"type": "string"},
                    "cross_lecture": {"type": "boolean"},
                    "seam_evidence": {
                        "type": ["object", "null"],
                        "additionalProperties": False,
                        "required": ["prev_pk", "prev_quote", "next_pk", "next_quote"],
                        "properties": {
                            "prev_pk": {"type": "string"},
                            "prev_quote": {"type": "string"},
                            "next_pk": {"type": "string"},
                            "next_quote": {"type": "string"},
                        },
                    },
                    "ranges": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "non_argument_paragraphs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["pk", "reason"],
                "properties": {
                    "pk": {"type": "string"},
                    "reason": {"type": "string", "enum": ["empty", "logistics", "prayer"]},
                },
            },
        },
    },
}

SEAM_VERIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["should_merge", "severity", "reason", "evidence_quote"],
    "properties": {
        "should_merge": {"type": "boolean"},
        "severity": {"type": "string", "enum": ["high", "none"]},
        "reason": {"type": "string"},
        "evidence_quote": {"type": "string"},
    },
}


@dataclass(frozen=True)
class LectureSpec:
    lecture: str
    source_id: str
    path: Path
    visual_attestations: dict[str, str] = field(default_factory=dict)


@dataclass
class ProjectedParagraph:
    pk: str
    lecture: str
    source_id: str
    paragraph_key: str
    index: int
    text: str
    chars: int
    is_empty: bool


@dataclass
class ProjectedLecture:
    spec: LectureSpec
    file_sha256: str
    body_sha256: str
    paragraphs: list[ProjectedParagraph]
    headings: list[dict[str, Any]]


@dataclass
class ProjectedSeries:
    series_name: str
    lectures: list[ProjectedLecture]
    by_pk: dict[str, ProjectedParagraph]
    lecture_order: dict[str, int]
    all_pks: list[str]
    empty_pks: set[str]


def _sha256_json(obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_series(series_name: str, specs: list[LectureSpec]) -> ProjectedSeries:
    lectures: list[ProjectedLecture] = []
    by_pk: dict[str, ProjectedParagraph] = {}
    lecture_order: dict[str, int] = {}
    all_pks: list[str] = []
    empty_pks: set[str] = set()

    for order_idx, spec in enumerate(specs):
        if spec.lecture in lecture_order:
            raise ValueError(f"Duplicate lecture label: {spec.lecture}")
        lecture_order[spec.lecture] = order_idx
        raw_bytes = spec.path.read_bytes()
        file_sha = hashlib.sha256(raw_bytes).hexdigest()
        data = json.loads(raw_bytes.decode("utf-8"))
        proj = project_script(data["script"])
        paras: list[ProjectedParagraph] = []
        for idx, row in enumerate(proj.body_rows):
            pkey = f"S{idx + 1:04d}"
            pk = f"{spec.lecture}-{pkey}"
            text = (row.get("text") or "").strip()
            chars = len(text)
            p = ProjectedParagraph(
                pk=pk,
                lecture=spec.lecture,
                source_id=spec.source_id,
                paragraph_key=pkey,
                index=idx + 1,
                text=text,
                chars=chars,
                is_empty=(chars == 0),
            )
            paras.append(p)
            by_pk[pk] = p
            all_pks.append(pk)
            if p.is_empty:
                empty_pks.add(pk)
        headings = [{"title": getattr(h, "title", str(h)), "level": getattr(h, "level", 0)} for h in getattr(proj, "headings", [])]
        lectures.append(
            ProjectedLecture(
                spec=spec,
                file_sha256=file_sha,
                body_sha256=proj.body_sha256,
                paragraphs=paras,
                headings=headings,
            )
        )

    return ProjectedSeries(
        series_name=series_name,
        lectures=lectures,
        by_pk=by_pk,
        lecture_order=lecture_order,
        all_pks=all_pks,
        empty_pks=empty_pks,
    )


def _call_json(client: Any, system_prompt: str, payload: Any, schema: dict[str, Any]) -> dict[str, Any]:
    prompt_text = json.dumps(payload, ensure_ascii=False)
    return client.generate_json(
        system_prompt=system_prompt,
        user_prompt=prompt_text,
        json_schema=schema,
    )


def build_paragraph_cards(
    series: ProjectedSeries,
    client: Any,
    cache_dir: Path,
    *,
    max_workers: int = 4,
) -> dict[str, list[dict[str, Any]]]:
    """Step 1: parallel per-lecture 4-field paragraph cards with content-hash caching."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    prompt_sha = _sha256_text(STEP1_PROMPT)
    model_id = getattr(client, "model", "unknown")

    def process_lecture(lec: ProjectedLecture) -> tuple[str, list[dict[str, Any]]]:
        cache_key = _sha256_json(
            {
                "source_id": lec.spec.source_id,
                "body_sha256": lec.body_sha256,
                "model": model_id,
                "prompt_sha256": prompt_sha,
            }
        )[:16]
        cache_file = cache_dir / f"step1_{lec.spec.lecture}_{cache_key}.json"
        if cache_file.exists():
            return lec.spec.lecture, json.loads(cache_file.read_text(encoding="utf-8"))

        non_empty = [p for p in lec.paragraphs if not p.is_empty]
        by_pk_reply: dict[str, dict[str, Any]] = {}
        if non_empty:
            payload = {
                "lecture": lec.spec.lecture,
                "source_id": lec.spec.source_id,
                "paragraphs": [{"pk": p.pk, "chars": p.chars, "text": p.text} for p in non_empty],
            }
            reply = _call_json(client, STEP1_PROMPT, payload, STEP1_SCHEMA)
            cards_raw = reply.get("cards", [])
            expected_pks = [p.pk for p in non_empty]
            got_pks = [c["pk"] for c in cards_raw]
            if got_pks != expected_pks:
                raise ValueError(
                    f"Step 1 card pk mismatch for {lec.spec.lecture}: expected {len(expected_pks)} pks, got {len(got_pks)}"
                )
            for c in cards_raw:
                by_pk_reply[c["pk"]] = c

        final_cards: list[dict[str, Any]] = []
        for p in lec.paragraphs:
            if p.is_empty:
                final_cards.append(
                    {"pk": p.pk, "chars": 0, "summary": "", "is_pure_shell": True}
                )
            else:
                c = by_pk_reply[p.pk]
                final_cards.append(
                    {
                        "pk": p.pk,
                        "chars": p.chars,
                        "summary": c["summary"].strip(),
                        "is_pure_shell": bool(c["is_pure_shell"]),
                    }
                )
        cache_file.write_text(json.dumps(final_cards, ensure_ascii=False, indent=2), encoding="utf-8")
        return lec.spec.lecture, final_cards

    results: dict[str, list[dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(series.lectures)))) as pool:
        for lecture_label, cards in pool.map(process_lecture, series.lectures):
            results[lecture_label] = cards
    return results


def build_seam_excerpts(
    series: ProjectedSeries,
    cards_by_lecture: dict[str, list[dict[str, Any]]],
    *,
    window: int = 2,
) -> list[dict[str, Any]]:
    """Extract last `window` non-shell paragraphs of L_i and first `window` non-shell paragraphs of L_{i+1}."""
    seams: list[dict[str, Any]] = []
    for i in range(len(series.lectures) - 1):
        prev_lec = series.lectures[i]
        next_lec = series.lectures[i + 1]
        prev_non_shell = [
            c["pk"] for c in cards_by_lecture[prev_lec.spec.lecture] if not c["is_pure_shell"] and c["chars"] > 0
        ]
        next_non_shell = [
            c["pk"] for c in cards_by_lecture[next_lec.spec.lecture] if not c["is_pure_shell"] and c["chars"] > 0
        ]
        prev_tail = prev_non_shell[-window:] if prev_non_shell else []
        next_head = next_non_shell[:window] if next_non_shell else []
        seams.append(
            {
                "seam_id": f"{prev_lec.spec.lecture}->{next_lec.spec.lecture}",
                "prev_lecture": prev_lec.spec.lecture,
                "next_lecture": next_lec.spec.lecture,
                "prev_paragraphs": [
                    {"pk": pk, "text": series.by_pk[pk].text} for pk in prev_tail
                ],
                "next_paragraphs": [
                    {"pk": pk, "text": series.by_pk[pk].text} for pk in next_head
                ],
            }
        )
    return seams


def _expand_range(series: ProjectedSeries, range_str: str) -> list[str]:
    m = RANGE_RE.fullmatch(range_str.strip())
    if not m:
        raise ValueError(f"Invalid range syntax: {range_str!r}")
    start_pk = m.group(1)
    end_pk = m.group(2) or start_pk
    if start_pk not in series.by_pk or end_pk not in series.by_pk:
        raise ValueError(f"Unknown pk in range: {range_str!r}")
    sp = series.by_pk[start_pk]
    ep = series.by_pk[end_pk]
    if sp.lecture != ep.lecture:
        raise ValueError(f"Range cannot span multiple lectures: {range_str!r}")
    if sp.index > ep.index:
        raise ValueError(f"Reversed range: {range_str!r}")
    return [f"{sp.lecture}-S{idx:04d}" for idx in range(sp.index, ep.index + 1)]


def _compress_pks_to_ranges(series: ProjectedSeries, pks: list[str]) -> list[str]:
    if not pks:
        return []
    out: list[str] = []
    run_start = run_prev = pks[0]
    for pk in pks[1:]:
        p_prev = series.by_pk[run_prev]
        p_cur = series.by_pk[pk]
        if p_cur.lecture == p_prev.lecture and p_cur.index == p_prev.index + 1:
            run_prev = pk
        else:
            out.append(run_start if run_start == run_prev else f"{run_start}..{run_prev}")
            run_start = run_prev = pk
    out.append(run_start if run_start == run_prev else f"{run_start}..{run_prev}")
    return out


def normalize_empty_paragraphs(series: ProjectedSeries, raw_output: dict[str, Any]) -> dict[str, Any]:
    """Deterministically strip 0-char empty paragraphs from unit ranges into non_argument_paragraphs."""
    non_arg_by_pk: dict[str, str] = {}
    for item in raw_output.get("non_argument_paragraphs", []):
        pk = item["pk"]
        if pk in non_arg_by_pk:
            raise ValueError(f"Duplicate pk in non_argument_paragraphs: {pk}")
        non_arg_by_pk[pk] = item["reason"]

    cleaned_units: list[dict[str, Any]] = []
    for u in raw_output.get("units", []):
        kept_pks: list[str] = []
        for rng in u.get("ranges", []):
            for pk in _expand_range(series, rng):
                if pk in series.empty_pks:
                    non_arg_by_pk.setdefault(pk, "empty")
                else:
                    kept_pks.append(pk)
        cleaned = dict(u)
        cleaned["ranges"] = _compress_pks_to_ranges(series, kept_pks)
        cleaned_units.append(cleaned)

    for pk in series.empty_pks:
        non_arg_by_pk[pk] = "empty"

    non_arg_list = [
        {"pk": pk, "reason": non_arg_by_pk[pk]}
        for pk in series.all_pks
        if pk in non_arg_by_pk
    ]
    return {"units": cleaned_units, "non_argument_paragraphs": non_arg_list}


def validate_segmentation(
    series: ProjectedSeries,
    result: dict[str, Any],
    *,
    max_unit_utf8_bytes: int = 72000,
) -> dict[str, Any]:
    """Enforce all deterministic Hard Invariants and recompute chars/bytes."""
    units = result.get("units")
    non_arg = result.get("non_argument_paragraphs")
    if not isinstance(units, list) or not units:
        raise ValueError("Segmentation produced zero units")
    if not isinstance(non_arg, list):
        raise ValueError("non_argument_paragraphs must be a list")

    seen_pks: set[str] = set()
    non_arg_pks: set[str] = set()
    for item in non_arg:
        pk = item["pk"]
        if pk not in series.by_pk:
            raise ValueError(f"Unknown pk in non_argument_paragraphs: {pk}")
        if pk in non_arg_pks:
            raise ValueError(f"Duplicate pk in non_argument_paragraphs: {pk}")
        non_arg_pks.add(pk)
        seen_pks.add(pk)

    pad_width = 3 if len(units) >= 100 else 2
    unit_ids: set[str] = set()
    validated_units: list[dict[str, Any]] = []
    for u in units:
        raw_uid = u["unit_id"].strip()
        m_uid = re.fullmatch(r"U0*(\d+)", raw_uid)
        uid = f"U{int(m_uid.group(1)):0{pad_width}d}" if m_uid else raw_uid
        if not uid or uid in unit_ids:
            raise ValueError(f"Invalid or duplicate unit_id: {uid!r}")
        unit_ids.add(uid)
        ranges = u.get("ranges", [])
        if not ranges:
            raise ValueError(f"Unit {uid} has empty ranges")

        unit_pks: list[str] = []
        lectures_seen: list[str] = []
        last_pos_by_lec: dict[str, int] = {}
        for rng in ranges:
            expanded = _expand_range(series, rng)
            for pk in expanded:
                p = series.by_pk[pk]
                if p.is_empty:
                    raise ValueError(f"Empty paragraph {pk} must not be inside unit {uid}")
                if pk in seen_pks:
                    raise ValueError(f"Overlapping paragraph {pk} in unit {uid}")
                prev_idx = last_pos_by_lec.get(p.lecture, 0)
                if p.index <= prev_idx:
                    raise ValueError(f"Out-of-order range in unit {uid}: {pk}")
                last_pos_by_lec[p.lecture] = p.index
                seen_pks.add(pk)
                unit_pks.append(pk)
                if p.lecture not in lectures_seen:
                    lectures_seen.append(p.lecture)

        actually_cross = len(lectures_seen) >= 2
        if bool(u["cross_lecture"]) != actually_cross:
            raise ValueError(
                f"Unit {uid} cross_lecture={u['cross_lecture']} disagrees with lectures={lectures_seen}"
            )
        seam_ev = u.get("seam_evidence")
        if actually_cross:
            if not isinstance(seam_ev, dict):
                raise ValueError(f"Cross-lecture unit {uid} requires seam_evidence")
            for k_pk, k_q in (("prev_pk", "prev_quote"), ("next_pk", "next_quote")):
                epk = seam_ev.get(k_pk, "")
                quote = (seam_ev.get(k_q) or "").strip()
                if epk not in unit_pks:
                    raise ValueError(f"Unit {uid} seam_evidence {k_pk}={epk!r} not in unit")
                if not quote or quote not in series.by_pk[epk].text:
                    raise ValueError(f"Unit {uid} seam_evidence {k_q} is not a verbatim substring of {epk}")

        chars = sum(series.by_pk[pk].chars for pk in unit_pks)
        utf8_bytes = sum(len(series.by_pk[pk].text.encode("utf-8")) for pk in unit_pks)
        if utf8_bytes > max_unit_utf8_bytes:
            raise ValueError(
                f"Unit {uid} exceeds UTF-8 byte budget ({utf8_bytes} > {max_unit_utf8_bytes})"
            )
        vu = dict(u)
        vu["unit_id"] = uid
        vu["chars"] = chars
        vu["utf8_bytes"] = utf8_bytes
        validated_units.append(vu)

    # 100% coverage check
    expected = set(series.all_pks)
    missing = expected - seen_pks
    if missing:
        raise ValueError(f"Missing paragraphs from segmentation: {sorted(missing)[:10]}")

    # All empty paragraphs must be in non_argument_paragraphs
    if not series.empty_pks.issubset(non_arg_pks):
        raise ValueError("Not all empty paragraphs are in non_argument_paragraphs")

    # Anti-degeneration checks
    total_unit_chars = sum(u["chars"] for u in validated_units)
    non_empty_count = len(series.all_pks) - len(series.empty_pks)
    if len(series.lectures) >= 2 and len(validated_units) == 1 and total_unit_chars > 25000:
        raise ValueError("Degenerate segmentation: collapsed entire multi-lecture series into 1 unit")
    if non_empty_count >= 10 and len(validated_units) >= int(0.7 * non_empty_count):
        raise ValueError(
            f"Degenerate segmentation: over-fragmented ({len(validated_units)} units for {non_empty_count} paragraphs)"
        )

    return {
        "version": VERSION,
        "series": series.series_name,
        "sources": [
            {
                "lecture": lec.spec.lecture,
                "source_id": lec.spec.source_id,
                "file": str(lec.spec.path),
                "sha256": lec.file_sha256,
                "body_sha256": lec.body_sha256,
            }
            for lec in series.lectures
        ],
        "units": validated_units,
        "non_argument_paragraphs": non_arg,
    }


def segment_series(
    series: ProjectedSeries,
    cards_by_lecture: dict[str, list[dict[str, Any]]],
    client: Any,
    cache_dir: Path,
    *,
    max_retries: int = 2,
    max_unit_utf8_bytes: int = 72000,
) -> dict[str, Any]:
    """Step 2: single-pass series segmentation with full cache key and bounded invariant retries."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    seams = build_seam_excerpts(series, cards_by_lecture)
    cards_sha = _sha256_json(cards_by_lecture)
    seams_sha = _sha256_json(seams)
    prompt_sha = _sha256_text(STEP2_PROMPT)
    model_id = getattr(client, "model", "unknown")

    cache_key = _sha256_json(
        {
            "series_cards_sha256": cards_sha,
            "seam_bodies_sha256": seams_sha,
            "step2_model_id": model_id,
            "step2_prompt_sha256": prompt_sha,
        }
    )[:16]
    cache_file = cache_dir / f"step2_segmentation_{cache_key}.json"
    if cache_file.exists():
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        return validate_segmentation(series, cached, max_unit_utf8_bytes=max_unit_utf8_bytes)

    base_payload: dict[str, Any] = {
        "series": series.series_name,
        "lectures": [
            {"lecture": lec.spec.lecture, "cards": cards_by_lecture[lec.spec.lecture]}
            for lec in series.lectures
        ],
        "seams": seams,
    }
    last_error: str | None = None
    for attempt in range(max_retries + 1):
        payload = dict(base_payload)
        if last_error:
            payload["previous_validation_error"] = last_error
        raw = _call_json(client, STEP2_PROMPT, payload, STEP2_SCHEMA)
        try:
            normalized = normalize_empty_paragraphs(series, raw)
            validated = validate_segmentation(
                series, normalized, max_unit_utf8_bytes=max_unit_utf8_bytes
            )
            cache_file.write_text(json.dumps(validated, ensure_ascii=False, indent=2), encoding="utf-8")
            return validated
        except ValueError as exc:
            last_error = str(exc)
            if attempt == max_retries:
                raise ValueError(f"Step 2 segmentation failed after {max_retries + 1} attempts: {last_error}") from exc
    raise RuntimeError("unreachable")


def verify_split_seams(
    series: ProjectedSeries,
    segmentation: dict[str, Any],
    cards_by_lecture: dict[str, list[dict[str, Any]]],
    seam_client: Any,
    cache_dir: Path,
) -> list[dict[str, Any]]:
    """Cross-vendor check on every lecture boundary that Step 2 chose NOT to merge."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    pk_to_unit: dict[str, str] = {}
    for u in segmentation["units"]:
        for rng in u["ranges"]:
            for pk in _expand_range(series, rng):
                pk_to_unit[pk] = u["unit_id"]

    seams = build_seam_excerpts(series, cards_by_lecture)
    prompt_sha = _sha256_text(SEAM_VERIFY_PROMPT)
    model_id = getattr(seam_client, "model", "unknown")
    findings: list[dict[str, Any]] = []

    for seam in seams:
        if not seam["prev_paragraphs"] or not seam["next_paragraphs"]:
            continue
        prev_last_pk = seam["prev_paragraphs"][-1]["pk"]
        next_first_pk = seam["next_paragraphs"][0]["pk"]
        u_prev = pk_to_unit.get(prev_last_pk)
        u_next = pk_to_unit.get(next_first_pk)
        if u_prev is not None and u_prev == u_next:
            # Already merged across this seam.
            continue

        cache_key = _sha256_json(
            {
                "seam": seam,
                "model": model_id,
                "prompt_sha256": prompt_sha,
            }
        )[:16]
        cache_file = cache_dir / f"seam_verify_{seam['prev_lecture']}_{seam['next_lecture']}_{cache_key}.json"
        if cache_file.exists():
            verdict = json.loads(cache_file.read_text(encoding="utf-8"))
        else:
            verdict = _call_json(seam_client, SEAM_VERIFY_PROMPT, seam, SEAM_VERIFY_SCHEMA)
            cache_file.write_text(json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")

        findings.append(
            {
                "seam_id": seam["seam_id"],
                "prev_unit": u_prev,
                "next_unit": u_next,
                **verdict,
            }
        )
    return findings


def compile_unit_manifests(
    series: ProjectedSeries,
    segmentation: dict[str, Any],
    output_dir: Path,
) -> dict[str, Path]:
    """Compile each flat logical unit into a strict #845 UnitManifest JSON (with visual_attestations)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    lec_by_label = {lec.spec.lecture: lec for lec in series.lectures}
    written: dict[str, Path] = {}

    for u in segmentation["units"]:
        ranges_by_lec: dict[str, list[dict[str, str]]] = {}
        lec_seq: list[str] = []
        for rng in u["ranges"]:
            expanded = _expand_range(series, rng)
            first_p = series.by_pk[expanded[0]]
            last_p = series.by_pk[expanded[-1]]
            if first_p.lecture not in ranges_by_lec:
                ranges_by_lec[first_p.lecture] = []
                lec_seq.append(first_p.lecture)
            ranges_by_lec[first_p.lecture].append(
                {"first": first_p.paragraph_key, "last": last_p.paragraph_key}
            )

        sources_payload: list[dict[str, Any]] = []
        for lec_label in lec_seq:
            lec = lec_by_label[lec_label]
            sources_payload.append(
                {
                    "source_id": lec.spec.source_id,
                    "file": str(lec.spec.path.resolve()),
                    "sha256": lec.file_sha256,
                    "ranges": ranges_by_lec[lec_label],
                    "visual_attestations": dict(lec.spec.visual_attestations),
                }
            )
        manifest = unit_plan.UnitManifest.model_validate({"sources": sources_payload})
        unit_dir = output_dir / u["unit_id"]
        unit_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = unit_dir / "unit-manifest.json"
        manifest_path.write_text(
            json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written[u["unit_id"]] = manifest_path
    return written


def evaluate_ruling_staleness(
    series: ProjectedSeries,
    ruling_path: Path,
) -> dict[str, str]:
    """Check per-unit ruling staleness without overwriting ruling.json or freezing unaffected units."""
    if not ruling_path.exists():
        return {}
    ruling = json.loads(ruling_path.read_text(encoding="utf-8"))
    saved_sha = {s["lecture"]: s["sha256"] for s in ruling.get("sources", [])}
    current_sha = {lec.spec.lecture: lec.file_sha256 for lec in series.lectures}
    changed_lectures = {k for k, v in current_sha.items() if saved_sha.get(k) != v}

    status_by_unit: dict[str, str] = {}
    for u in ruling.get("units", []):
        touched = set()
        for rng in u.get("ranges", []):
            m = RANGE_RE.fullmatch(rng.strip())
            if m:
                touched.add(m.group(1).split("-")[0])
        status_by_unit[u["unit_id"]] = "stale_ruling" if (touched & changed_lectures) else "valid_ruling"
    return status_by_unit
