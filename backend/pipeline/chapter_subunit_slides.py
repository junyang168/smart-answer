"""Listening slides made one sub-unit at a time.

Owner 2026-11-18: one model call wrote a whole lecture's slides (27–36 slides over
400+ sentences), one call reviewed them all and one call rewrote them all. Points
were merged and arrived late, his answers were dropped, a revision removed a diagram
from a slide nobody flagged, and calls hit the 10-minute limit. 「do it in sub unit」.

A sub-unit is the listening plan's own part of a unit (`listening-units.json`
`sub_units`), backed by argument groups with their exact source ranges. For each one:

1. its selection is exactly those source ranges, the same rows the listening build
   checks a reviewed bundle against;
2. its Claim outline is compiled from the knowledge instance against that selection,
   so only Claims he argues inside the sub-unit come along;
3. a recorded plan binds indispensable inferences to the selected transcript, then
   the slide model writes once against those requirements;
4. Jev and the program correct supported timing, and independent Eval reports
   any remaining findings without rewriting the sub-unit.

Nothing is written to DATA_BASE_DIR: `stage` copies a passing sub-unit into a new
bundle directory only when called, and the plan is changed separately.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from backend.pipeline import chapter_exposition, chapter_slide_arguments, chapter_slide_diagram_timing, chapter_slide_eval, chapter_slides
from backend.pipeline.chapter_material import CHAPTERS

SLIDE_MODEL = "gpt-6.1-sol"
REVIEW_MODEL = "claude-opus-5-5"


def plan_path(data_root: Path, passage: str) -> Path:
    return data_root / "chapter_exposition" / passage / "listening-units.json"


def find(plan: dict, sub_id: str) -> tuple[str, dict, dict, list[dict]]:
    for unit_passage, unit in plan["units"].items():
        for sub in unit["sub_units"]:
            if sub["id"] == sub_id:
                groups = [g for g in unit["groups"] if g["key"] in sub["group_keys"]]
                return unit_passage, unit, sub, groups
    raise KeyError(f"No sub-unit {sub_id}")


def subunit_selection(plan: dict, passage: str, sub_id: str) -> tuple[str, dict]:
    """The sub-unit's own source ranges as a selection record (one lecture)."""
    unit_passage, _, sub, groups = find(plan, sub_id)
    sources = {g["source_id"] for g in groups}
    if len(sources) != 1:
        raise ValueError(f"Sub-unit {sub_id} spans {len(sources)} recordings")
    [source] = sources
    entry = next(e for e in plan["inputs"] if e["source_id"] == source)
    ranges = sorted({(r["paragraph_key"], r["start"], r["end"], r["text"])
                     for g in groups for r in g["source_ranges"]}, key=lambda r: (r[1], r[2]))
    pieces = [{"id": f"U{i:03d}", "source_paragraph_key": key, "start": start, "end": end, "text": text}
              for i, (key, start, end, text) in enumerate(ranges, 1)]
    selection = {
        "schema_version": "chapter_exposition_decisions_v1", "passage": passage, "lecture": entry["lecture"],
        "source_id": source, "transcript_file": f"{entry['lecture']}.json", "transcript_sha256": entry["source_sha256"],
        "subunit": {"id": sub_id, "title": sub["title"], "unit": unit_passage, "group_keys": sub["group_keys"]},
        "pieces": pieces,
        "decisions": [{"id": p["id"], "keep": True, "verses": [unit_passage], "kind": "釋經",
                       "reason": "本聆聽子單元的完整論證"} for p in pieces],
        "claims_by_piece": {},
    }
    return entry["lecture"], selection


def _client(model: str, calls: Path):
    from backend.pipeline.chapter_slide_claims import RecordedClient
    return RecordedClient(chapter_exposition.chapter_client(model), calls, once=True)


def run(data_root: Path, passage: str, sub_id: str, out: Path, *, conn,
        opus_api_spend_root: Path | None = None) -> dict:
    """Generate and review one sub-unit's slides in `out`; returns the outcome."""
    from backend.pipeline.chapter_slide_claims import compile_instance_context

    chapter = CHAPTERS[passage]
    plan = json.loads(plan_path(data_root, passage).read_text())
    lecture, selection = subunit_selection(plan, passage, sub_id)
    entry = next(e for e in plan["inputs"] if e["lecture"] == lecture)
    data = out / "data"
    selection_path = chapter_exposition.record_path(data, passage, lecture)
    selection_path.parent.mkdir(parents=True, exist_ok=True)
    if selection_path.exists() and json.loads(selection_path.read_text()) != selection:
        raise ValueError(f"{out} holds a different selection for {sub_id}; use a new directory")
    selection_path.write_text(json.dumps(selection, ensure_ascii=False, indent=1))
    context_path = out / "claims-context.json"
    slides_path = chapter_slides.record_path(data, passage, lecture)
    if context_path.exists():
        context = json.loads(context_path.read_text())
    else:
        context = compile_instance_context(passage, lecture, data_base_dir=data, conn=conn,
                                           change_set_id=entry["changeset"])
        context["scope"] = "subunit"
    if not slides_path.exists() and "diagram_spans" not in context:
        lines = chapter_slides.sentences(chapter_slides.kept_pieces(selection))
        spans, issues = chapter_slide_diagram_timing.plan_record(context, lines, out / "diagram-plan")
        if issues:
            return {"sub_unit": sub_id, "status": "unresolved", "rounds": 0, "problems": issues}
        context["diagram_spans"] = spans
    if not slides_path.exists():
        if chapter_slide_arguments.PLAN_KEY not in context:
            lines = chapter_slides.sentences(chapter_slides.kept_pieces(selection))
            planner = _client(SLIDE_MODEL, out / "argument-plan")
            context[chapter_slide_arguments.PLAN_KEY] = chapter_slide_arguments.plan(context, lines, planner)
        temp = context_path.with_suffix(".tmp")
        temp.write_text(json.dumps(context, ensure_ascii=False, indent=1))
        temp.replace(context_path)
    client = _client(SLIDE_MODEL, out / "calls")
    if not slides_path.exists():
        made = chapter_slides.decide(chapter, lecture, data_base_dir=data, client=client, force=True, claim_context=context)
        if made["status"] != "saved":
            return {"sub_unit": sub_id, "status": "generation_rejected", "problems": made.get("problems")}
    diagram_problems = chapter_slide_diagram_timing.check_record(slides_path, selection_path, out / "diagram-timing")
    if diagram_problems:
        return {"sub_unit": sub_id, "status": "unresolved", "rounds": 0, "problems": diagram_problems}
    transport = ".opus-api" if opus_api_spend_root is not None else ""
    receipt = out / f"round1{transport}.semantic-eval.json"
    if not receipt.exists():
        chapter_slide_eval.run_semantic_eval(slides_path, selection_path, receipt, REVIEW_MODEL,
                                             opus_api_spend_root=opus_api_spend_root)
    problems = chapter_slide_eval.semantic_eval_problems(slides_path, selection_path, receipt)
    if problems:
        return {"sub_unit": sub_id, "status": "unresolved", "rounds": 0, "problems": problems}
    return {"sub_unit": sub_id, "status": "passed", "rounds": 0, "receipt": str(receipt)}


def stage(out: Path, data_root: Path, passage: str) -> str:
    """Copy a passing sub-unit into a new reviewed bundle; returns the bundle id."""
    result = json.loads((out / "result.json").read_text())
    if result["status"] != "passed":
        raise ValueError(f"{result['sub_unit']} has not passed review")
    selection_path = next((out / "data" / "chapter_exposition" / passage).glob("*.json"))
    lecture = selection_path.stem
    slides_path = chapter_slides.record_path(out / "data", passage, lecture)
    receipt = Path(result["receipt"])
    if chapter_slide_eval.semantic_eval_problems(slides_path, selection_path, receipt):
        raise ValueError("Review no longer matches these slides")
    bundle = hashlib.sha256(slides_path.read_bytes()).hexdigest()
    target = data_root / "chapter_exposition" / passage / "unit-supplements" / bundle
    target.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(selection_path, target / f"{lecture}.selection.json")
    shutil.copyfile(slides_path, target / f"{lecture}.slides.json")
    shutil.copyfile(receipt, target / f"{lecture}.semantic-eval.json")
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("passage", choices=sorted(CHAPTERS))
    parser.add_argument("sub_unit", help="listening plan sub-unit id, e.g. 1.1")
    parser.add_argument("--out", type=Path, required=True, help="run directory for this sub-unit")
    parser.add_argument("--data-dir", type=Path, help="where the listening plan lives (read only)")
    parser.add_argument("--opus-api-spend-root", type=Path, help="Use the billed Opus API for Eval")
    args = parser.parse_args()
    from backend.api.config import DATA_BASE_PATH
    from backend.pipeline.argument_review import database_url
    import psycopg

    args.out.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(database_url()) as conn:
        result = run(args.data_dir or Path(DATA_BASE_PATH), args.passage, args.sub_unit, args.out, conn=conn,
                     opus_api_spend_root=args.opus_api_spend_root)
    (args.out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
