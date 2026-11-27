"""Two-phase series-to-unit argument pipeline (Phase A: GPT+Jev, Phase B: Claude Opus).

Decouples high-volume GPT/Jev generation & prefiltering (Phase A) from Opus
verification (Phase B), and explicitly avoids Gemini:
  - Phase A (GPT ``gpt-6.1-sol`` + Jev, zero Opus, zero Gemini):
      1. Layer 1 series segmentation (``series_unit_segmentation``)
      2. Unit planning & argument extraction (``claim_unit_plan`` + ``claim_unit_argument``)
      3. Fidelity prefilter (``argument_fidelity`` prefilter via Jev)
      4. Round-trip Stage 1 blind reconstruction (``argument_roundtrip_verify`` stage1_only)
  - Phase B (Claude Opus ``claude-opus-5-5``, zero Gemini):
      1. Fidelity judge (``argument_fidelity.run_judge`` -> ``fidelity_summary.json``)
      2. Round-trip Stage 2 & 3 (``argument_roundtrip_verify`` reusing Stage 1 ->
         ``roundtrip_diff.json`` & optional ``roundtrip_attribution.json``)
  - Optional ``--load``: loads active units into PostgreSQL via ``unit_argument_load``.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from backend.pipeline import argument_fidelity as fidelity
from backend.pipeline import argument_roundtrip_verify as roundtrip
from backend.pipeline import claim_unit_argument as unit_arg
from backend.pipeline import claim_unit_plan as unit_plan
from backend.pipeline import evidence_refresh as artifacts
from backend.pipeline import series_unit_segmentation as seg

DEFAULT_DATA_BASE_DIR = Path("/opt/homebrew/var/www/church/web/data")
DEFAULT_GPT_MODEL = "gpt-6.1-sol"
DEFAULT_OPUS_MODEL = "claude-opus-5-5"

SERIES_PRESETS: dict[str, dict[str, str]] = {
    "2011-nysc-matthew-2": {
        "title": "2011 NYSC 專題：馬太福音釋經（二）",
        "prefix": "2011 NYSC 專題：馬太福音釋經（二）",
    },
    "2012-nysc-matthew-3": {
        "title": "2012 NYSC 專題：馬太福音釋經（三）",
        "prefix": "2012 NYSC 專題：馬太福音釋經（三）",
    },
    "2014-nysc-matthew-4": {
        "title": "2014 NYSC 專題：馬太福音釋經（四）",
        "prefix": "2016 NYSC 專題：馬太福音釋經（四）",
    },
    "2016-nysc-matthew-5": {
        "title": "2016 NYSC 專題：馬太福音釋經（五）",
        "prefix": "2016 NYSC 專題：馬太福音釋經（五）",
    },
    "2017-nysc-matthew-6": {
        "title": "2017 NYSC 專題：馬太福音釋經（六）",
        "prefix": "2017 NYSC 專題：馬太福音釋經（六）",
    },
    "2018-nysc-matthew-7": {
        "title": "2018 NYSC 專題：馬太福音釋經（七）",
        "prefix": "2018 NYSC 專題：馬太福音釋經（七）",
    },
}


def _natural_lecture_key(path: Path) -> tuple[int, str]:
    m = re.search(r"(\d+)$", path.stem.strip())
    return (int(m.group(1)) if m else 0, path.stem)


def discover_lecture_specs(
    series_id: str,
    data_base_dir: Path = DEFAULT_DATA_BASE_DIR,
    *,
    title: Optional[str] = None,
    lecture_paths: Optional[list[Path]] = None,
) -> tuple[str, list[seg.LectureSpec]]:
    """Resolve series title and ordered LectureSpec list from explicit paths or script_published."""
    preset = SERIES_PRESETS.get(series_id, {})
    resolved_title = title or preset.get("title") or series_id
    if lecture_paths:
        files = sorted((Path(p).resolve() for p in lecture_paths), key=_natural_lecture_key)
    else:
        prefix = preset.get("prefix") or resolved_title
        pub_dir = Path(data_base_dir) / "script_published"
        files = sorted(
            (p for p in pub_dir.glob(f"{prefix}*.json") if p.is_file()),
            key=_natural_lecture_key,
        )
    if not files:
        raise FileNotFoundError(
            f"No lecture JSON files found for series={series_id!r} under {data_base_dir / 'script_published'}"
        )
    specs = [
        seg.LectureSpec(lecture=f"L{idx}", source_id=path.stem, path=path)
        for idx, path in enumerate(files, start=1)
    ]
    return resolved_title, specs


def _build_gpt_client(model: str = DEFAULT_GPT_MODEL) -> Any:
    from dotenv import load_dotenv
    from backend.pipeline.codex_subscription_client import CodexSubscriptionClient

    load_dotenv(".env")
    client = CodexSubscriptionClient(model=model, reasoning_effort="medium")
    client.capture_usage_events = True
    return client


def _build_opus_clients(model: str = DEFAULT_OPUS_MODEL) -> tuple[Any, Any]:
    from dotenv import load_dotenv
    from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient

    load_dotenv(".env")
    judge_client = ClaudeSubscriptionClient(model=model, reasoning_effort="high")
    attr_client = ClaudeSubscriptionClient(model=model, reasoning_effort="medium")
    return judge_client, attr_client


def _unit_dirs(series_dir: Path) -> list[Path]:
    units_root = series_dir / "units"
    if not units_root.exists():
        return []
    return sorted(
        (d for d in units_root.iterdir() if d.is_dir() and not d.name.endswith("_bak")),
        key=lambda d: d.name,
    )


def _write_fidelity_summary(unit_dir: Path, final_report: dict[str, Any], elapsed_s: float) -> Path:
    total = int(final_report.get("records") or 0)
    findings = list(final_report.get("findings") or [])
    changed = sum(1 for f in findings if f.get("verdict") == "changed")
    added = sum(1 for f in findings if f.get("verdict") == "added")
    summary = {
        "total_nodes": total,
        "faithful": max(0, total - len(findings)),
        "changed": changed,
        "added": added,
        "seconds": round(elapsed_s, 1),
        "findings": findings,
    }
    out_path = unit_dir / "fidelity_summary.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def run_phase_a(
    series_id: str,
    *,
    title: Optional[str] = None,
    lecture_paths: Optional[list[Path]] = None,
    data_base_dir: Path = DEFAULT_DATA_BASE_DIR,
    gpt_client: Any = None,
    skip_roundtrip_stage1: bool = False,
    workers: int = 1,
) -> dict[str, Any]:
    """Run Phase A (GPT + Jev only, zero Opus, zero Gemini) for one series."""
    series_dir = Path(data_base_dir) / "unit_argument_series" / series_id
    units_root = series_dir / "units"
    seg_path = series_dir / "segmentation.json"

    gpt = gpt_client or _build_gpt_client()

    # Step A1: Layer 1 segmentation (idempotent if segmentation.json already exists)
    if seg_path.exists():
        segmentation = json.loads(seg_path.read_text(encoding="utf-8"))
    else:
        resolved_title, specs = discover_lecture_specs(
            series_id, data_base_dir, title=title, lecture_paths=lecture_paths
        )
        projected = seg.load_series(resolved_title, specs)
        cache_dir = series_dir / ".seg_cache"
        cards = seg.build_paragraph_cards(projected, gpt, cache_dir)
        segmentation = seg.segment_series(projected, cards, gpt, cache_dir)
        seam_findings = seg.verify_split_seams(projected, segmentation, cards, gpt, cache_dir)
        if seam_findings:
            (series_dir / "seam_verify.json").write_text(
                json.dumps(seam_findings, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        series_dir.mkdir(parents=True, exist_ok=True)
        seg_path.write_text(json.dumps(segmentation, ensure_ascii=False, indent=2), encoding="utf-8")
        seg.compile_unit_manifests(projected, segmentation, units_root)

    # Step A2-A4: Unit plan, unit argument, fidelity prefilter, round-trip stage 1
    def process_unit(unit_dir: Path) -> dict[str, Any]:
        # Each worker thread uses its own client so call_artifacts never races.
        unit_gpt = gpt_client or _build_gpt_client()
        ports = artifacts.Models(unit_gpt, unit_dir)
        if not (unit_dir / "unit-plan.json").exists():
            unit_plan.run(unit_dir / "unit-manifest.json", unit_dir, ports)
        if not (unit_dir / "unit-argument.json").exists():
            unit_arg.run(unit_dir, unit_dir, ports)

        candidate = json.loads((unit_dir / "unit-argument.json").read_text(encoding="utf-8"))
        fid_dir = unit_dir / "fidelity"
        prefilter_path = fid_dir / "prefilter.json"
        if not prefilter_path.exists():
            rows = fidelity.records(candidate)
            risk = fidelity.prefilter(rows, fid_dir, ports)
            pre = fidelity.prefilter_report(rows, risk, candidate=candidate)
            pre["usage"] = fidelity.usage(fid_dir)
            fid_dir.mkdir(parents=True, exist_ok=True)
            prefilter_path.write_text(json.dumps(pre, ensure_ascii=False, indent=1), encoding="utf-8")
        else:
            pre = json.loads(prefilter_path.read_text(encoding="utf-8"))

        rt_summary = None
        if not skip_roundtrip_stage1:
            rt_dir = unit_dir / "roundtrip"
            rt_summary = roundtrip.run_verification(
                unit_dir / "unit-argument.json",
                rt_dir,
                stage1_only=True,
                reuse_stage1=True,
                reconstructor_client=unit_gpt,
            )

        return {
            "unit_id": unit_dir.name,
            "records": pre.get("records", 0),
            "needs_look": len(pre.get("needs_look") or []),
            "stage1_gaps": rt_summary.get("stage1_gaps") if rt_summary else None,
        }

    unit_dirs = _unit_dirs(series_dir)
    max_workers = max(1, int(workers))
    if max_workers == 1 or len(unit_dirs) <= 1:
        unit_summaries = [process_unit(d) for d in unit_dirs]
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            unit_summaries = list(pool.map(process_unit, unit_dirs))

    return {
        "phase": "a",
        "series_id": series_id,
        "series_dir": str(series_dir),
        "unit_count": len(unit_summaries),
        "units": unit_summaries,
    }


def run_phase_b(
    series_id: str,
    *,
    data_base_dir: Path = DEFAULT_DATA_BASE_DIR,
    gpt_client: Any = None,
    opus_judge_client: Any = None,
    opus_attr_client: Any = None,
) -> dict[str, Any]:
    """Run Phase B (Claude Opus 5.5 verification, zero Gemini) for one series."""
    series_dir = Path(data_base_dir) / "unit_argument_series" / series_id
    units = _unit_dirs(series_dir)
    if not units:
        raise FileNotFoundError(f"No units found under {series_dir / 'units'}; run --phase a first")

    if opus_judge_client is None or opus_attr_client is None:
        default_judge, default_attr = _build_opus_clients()
        opus_judge_client = opus_judge_client or default_judge
        opus_attr_client = opus_attr_client or default_attr

    unit_summaries: list[dict[str, Any]] = []
    for unit_dir in units:
        unit_arg_path = unit_dir / "unit-argument.json"
        if not unit_arg_path.exists():
            raise FileNotFoundError(f"Missing {unit_arg_path}; run --phase a first")
        candidate = json.loads(unit_arg_path.read_text(encoding="utf-8"))

        # Step B1: Fidelity judge (only on prefilter needs_look)
        fid_dir = unit_dir / "fidelity"
        fid_summary_path = unit_dir / "fidelity_summary.json"
        if not fid_summary_path.exists():
            if not (fid_dir / "prefilter.json").exists():
                raise FileNotFoundError(f"Missing {fid_dir / 'prefilter.json'}; run --phase a first")
            t0 = time.time()
            final_report = fidelity.run_judge(candidate, fid_dir, opus_judge_client)
            _write_fidelity_summary(unit_dir, final_report, time.time() - t0)
        fid_summary = json.loads(fid_summary_path.read_text(encoding="utf-8"))

        # Step B2: Round-trip Stage 2 & 3 (reusing Stage 1 when cached)
        rt_diff_path = unit_dir / "roundtrip_diff.json"
        rt_attr_path = unit_dir / "roundtrip_attribution.json"
        if not rt_diff_path.exists():
            rt_dir = unit_dir / "roundtrip"
            gpt = gpt_client or _build_gpt_client()
            roundtrip.run_verification(
                unit_arg_path,
                rt_dir,
                reuse_stage1=True,
                reconstructor_client=gpt,
                judge_client=opus_judge_client,
                attributor_client=opus_attr_client,
            )
            shutil.copyfile(rt_dir / "04_structured_diff_report.json", rt_diff_path)
            attr_src = rt_dir / "05_root_cause_attribution.json"
            if attr_src.exists():
                shutil.copyfile(attr_src, rt_attr_path)

        unit_summaries.append(
            {
                "unit_id": unit_dir.name,
                "fidelity_faithful": fid_summary.get("faithful"),
                "fidelity_total": fid_summary.get("total_nodes"),
                "fidelity_findings": len(fid_summary.get("findings") or []),
                "roundtrip_diff": rt_diff_path.exists(),
                "roundtrip_attribution": rt_attr_path.exists(),
            }
        )

    return {
        "phase": "b",
        "series_id": series_id,
        "series_dir": str(series_dir),
        "unit_count": len(unit_summaries),
        "units": unit_summaries,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Two-phase series unit segmentation & argument pipeline")
    parser.add_argument("--series", required=True, help="Series ID, e.g. 2016-nysc-matthew-5")
    parser.add_argument("--title", default=None, help="Optional display title for the series")
    parser.add_argument("--lectures", nargs="*", type=Path, default=None, help="Optional explicit lecture JSON paths")
    parser.add_argument("--phase", choices=["a", "b", "all"], default="a", help="Pipeline phase to run")
    parser.add_argument("--data-base-dir", type=Path, default=DEFAULT_DATA_BASE_DIR)
    parser.add_argument("--skip-roundtrip-stage1", action="store_true", help="Skip Stage 1 blind reconstruction in Phase A")
    parser.add_argument("--workers", type=int, default=1, help="Parallel unit workers for Phase A")
    parser.add_argument("--load", action="store_true", help="Load units into PostgreSQL after pipeline completes")
    args = parser.parse_args(argv)

    out: dict[str, Any] = {"series_id": args.series, "phase": args.phase}
    if args.phase in {"a", "all"}:
        out["phase_a"] = run_phase_a(
            args.series,
            title=args.title,
            lecture_paths=args.lectures,
            data_base_dir=args.data_base_dir,
            skip_roundtrip_stage1=args.skip_roundtrip_stage1,
            workers=args.workers,
        )
    if args.phase in {"b", "all"}:
        out["phase_b"] = run_phase_b(
            args.series,
            data_base_dir=args.data_base_dir,
        )
    if args.load:
        from backend.pipeline import argument_review
        from backend.pipeline import unit_argument_load as loader

        with argument_review.connect() as conn:
            out["load"] = loader.load_series(
                conn, args.series, data_base_dir=args.data_base_dir, allow_unapproved=True
            )
            conn.commit()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
