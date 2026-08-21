"""One table, N models, the same source through the prompt that ships.

Three models have been compared by hand so far, and the cost of doing it that
way is visible in `detailed_knowledge_extraction_workflow_v1.md`: the
`deepseek-v4-pro` row has six empty cells, not because the run never happened
but because transcribing a row by hand is work nobody finished. The numbers
were there; the table is where they went missing.

Everything here is read from the package the runner already writes --
`summary`, `coverage`, `sections`, `usage` -- except the two columns nothing
computed before: what a run cost, and whether it answered in 繁體.

**Candidates do not need a production registry entry.** `run_source` takes a
client, so a model can be measured before anything in `MODEL_BACKENDS` knows it
exists. That is the point: deciding whether to adopt a model should not require
first editing the code that runs production. A model already in the registry is
benched exactly as production would run it; anything else is built from
`CANDIDATES` below and stays here until it earns promotion.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from backend.pipeline.detailed_knowledge_extraction_runner import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    NOTES_PROMPT_PATH,
    PROMPT_PATH,
    SectionSettings,
    build_client,
    run_source,
)
from backend.pipeline.knowledge_source import load_source_manifest
from backend.pipeline.model_prices import price_usage
from backend.pipeline.stage1 import Stage1AnthropicClient, Stage1OpenAIClient


#: Models under evaluation that production does not know about. Same shape as
#: `MODEL_BACKENDS` plus the per-model call settings, because a candidate whose
#: settings are wrong measures the settings rather than the model -- kimi-k3
#: ran fifteen times harder than it needed to for exactly one missing
#: `reasoning_effort`.
CANDIDATES: dict[str, dict[str, Any]] = {
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key_env": "GEMINI_API_KEY",
    },
}


def _bench_client(
    model: str, *, reasoning_effort: str, max_output_tokens: Optional[int]
) -> Stage1OpenAIClient | Stage1AnthropicClient:
    """Production's client for a known model, this file's for a candidate."""

    try:
        return build_client(
            model, reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS,
        )
    except ValueError:
        pass
    candidate = CANDIDATES.get(model.split("-", 1)[0])
    if candidate is None:
        raise ValueError(
            f"{model!r} is in neither MODEL_BACKENDS nor CANDIDATES; add it to "
            "CANDIDATES to bench it without touching the production registry"
        )
    return Stage1OpenAIClient(
        model=model,
        reasoning_effort=candidate.get("reasoning_effort", reasoning_effort),
        timeout_seconds=900, max_retries=3,
        max_output_tokens=max_output_tokens or candidate.get(
            "max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS),
        base_url=candidate.get("base_url"),
        api_key_env=candidate.get("api_key_env", "OPENAI_API_KEY"),
        temperature=candidate.get("temperature"),
        send_reasoning_effort=candidate.get("reasoning_effort_supported"),
    )


def _simplified_converter():
    """OpenCC, or None where it is unavailable.

    A hand-written character list was how this was counted the first time, and
    it disagreed with the workflow doc's own numbers because the two counted
    different fields. A converter is the only version of this that another
    person can reproduce.
    """

    try:
        from opencc import OpenCC
    except ImportError:  # pragma: no cover - dependency is in requirements.txt
        return None
    return OpenCC("s2t")


#: Characters OpenCC rewrites that are nonetheless correct Traditional, so
#: counting a conversion as evidence of 简体 would be wrong.
#:
#: Two different reasons, both found in one real package. 准, 布, 只 and 才
#: are one-to-many: Simplified merged 準 into 准, 佈 into 布, 隻 into 只 and 纔
#: into 才, but each of these is an ordinary Traditional character in its own
#: right, so 不准, 宣布, 只有 and 才有 convert to 不準, 宣佈, 隻有 and 纔有
#: while being correct as written. 秘, 群, 台 and 征 are variant preferences --
#: OpenCC normalises toward 祕, 羣, 臺, 徵, which are choices between
#: Traditional forms rather than a script change.
#:
#: The first version of this column called all twenty of these 简体 in a
#: package that had none, which is a worse failure than the hand count it
#: replaced: a wrong number that looks authoritative because a library
#: produced it.
TRADITIONAL_DESPITE_CONVERSION = frozenset("准布秘群台征只才")


def script_counts(
    texts: Iterable[str], converter: Any
) -> tuple[int, int]:
    """(traditional, simplified) Han character counts.

    Conversion runs over whole strings rather than single characters, because
    OpenCC resolves one-to-many mappings from context and loses that context
    per character: 征服 stays 征服 in a string and becomes 徵服 alone.

    What is left after `TRADITIONAL_DESPITE_CONVERSION` is a character that
    changed under conversion and has no Traditional reading -- 这, 说, 经 --
    which is the mixed-script failure this column exists to catch: anchors
    quoted in 繁體 with statements written in 简体.
    """

    trad = simp = 0
    for text in texts:
        text = text or ""
        converted = converter.convert(text) if converter is not None else text
        # A length change means the mapping was not one-to-one; fall back to
        # counting nothing as simplified rather than aligning the wrong pairs.
        aligned = converted if len(converted) == len(text) else text
        for char, after in zip(text, aligned):
            if not ("一" <= char <= "鿿"):
                continue
            if char != after and char not in TRADITIONAL_DESPITE_CONVERSION:
                simp += 1
            else:
                trad += 1
    return trad, simp


@dataclass
class BenchRow:
    """One model's line in the table."""

    model: str
    reasoning_effort: str
    observations: int = 0
    evidence_steps: int = 0
    claims: int = 0
    load_bearing: int = 0
    orphans: int = 0
    prose_represented: Optional[int] = None
    prose_total: Optional[int] = None
    unprocessed: Optional[int] = None
    fragments_unplaced: Optional[int] = None
    retries: int = 0
    traditional: int = 0
    simplified: int = 0
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: Optional[float] = None
    price_version: str = ""
    #: False when a number in this row cannot stand on its own -- a cached
    #: section contributes objects but no usage row, so its tokens and cost are
    #: missing while its observations are counted.
    cost_complete: bool = True
    cached_sections: int = 0
    elapsed_seconds: float = 0.0
    error: str = ""
    notes: list[str] = field(default_factory=list)


def row_from_package(
    package: Mapping[str, Any], *, model: str, reasoning_effort: str,
    elapsed_seconds: float, converter: Any = None,
) -> BenchRow:
    """Every column the table has, read out of one package."""

    row = BenchRow(model=model, reasoning_effort=reasoning_effort,
                   elapsed_seconds=elapsed_seconds)
    observations = list(package.get("observations") or [])
    steps = list(package.get("evidence_steps") or [])
    claims = list(package.get("claims") or [])
    relations = list(package.get("knowledge_relations") or [])

    row.observations = len(observations)
    row.evidence_steps = len(steps)
    row.claims = len(claims)
    load_bearing = [o for o in observations if o.get("argument_role") == "load_bearing"]
    row.load_bearing = len(load_bearing)

    # A load_bearing observation nothing points at is the failure this column
    # exists for: the model marked a sentence as carrying the argument and then
    # never connected it to anything.
    referenced = {
        value for relation in relations for value in relation.values()
        if isinstance(value, str)
    }
    row.orphans = sum(
        1 for o in load_bearing if o.get("observation_id") not in referenced
    )

    coverage = package.get("coverage") or {}
    prose = (coverage.get("by_category") or {}).get("prose") or {}
    row.prose_represented = prose.get("represented")
    row.prose_total = prose.get("total")
    row.unprocessed = coverage.get("unprocessed")
    row.fragments_unplaced = coverage.get("fragments_unplaced")

    sections = list(package.get("sections") or [])
    # `attempts` counts tries, so retries are the ones after the first. A
    # cached section reports 0 and contributes none.
    row.retries = sum(max(int(s.get("attempts") or 0) - 1, 0) for s in sections)
    row.cached_sections = sum(1 for s in sections if s.get("cached"))

    converter = converter if converter is not None else _simplified_converter()
    row.traditional, row.simplified = script_counts(
        [o.get("statement", "") for o in observations]
        + [s.get("statement", "") for s in steps]
        + [c.get("title", "") for c in claims],
        converter,
    )

    usage_rows = list(package.get("usage") or [])
    row.prompt_tokens = sum(int(u.get("prompt_tokens") or 0) for u in usage_rows)
    row.cached_tokens = sum(int(u.get("cached_tokens") or 0) for u in usage_rows)
    row.completion_tokens = sum(int(u.get("completion_tokens") or 0) for u in usage_rows)

    model_id = (package.get("extraction") or {}).get("model_id") or model
    cost = price_usage(usage_rows, model_id)
    row.cost_usd = cost.cost_usd
    row.price_version = cost.price_version
    row.cost_complete = cost.complete
    if cost.unpriced:
        row.notes.append(f"unpriced: {', '.join(cost.unpriced)}")
    if row.cached_sections:
        # price_usage returns 0.0 for no rows, which is right for a stage that
        # calls no model and wrong for a run that replayed a cache. The
        # objects were counted; the tokens that produced them were not.
        row.cost_complete = False
        row.notes.append(
            f"{row.cached_sections} of {len(sections)} sections replayed from "
            "cache; their tokens are not in this row -- rerun with --force for "
            "a cost that covers the whole source"
        )
    return row


def _cell(value: Any) -> str:
    return "—" if value is None else str(value)


def render_markdown(rows: Sequence[BenchRow]) -> str:
    """The table as it goes into the workflow doc, so nobody transcribes one."""

    header = (
        "| model | effort | obs | step | claim | load_bearing | orphans | "
        "prose | unplaced | retries | 繁/簡 | prompt | cached | completion | "
        "cost (USD) |"
    )
    divider = "|---|---|" + "---:|" * 13
    lines = [header, divider]
    for row in rows:
        if row.error:
            lines.append(
                f"| `{row.model}` | {row.reasoning_effort} | "
                + " | ".join(["—"] * 13) + " |"
            )
            continue
        prose = (
            f"{row.prose_represented}/{row.prose_total}"
            if row.prose_total is not None else "—"
        )
        cost = "—" if row.cost_usd is None else f"${row.cost_usd:.2f}"
        if not row.cost_complete:
            cost += "*"
        lines.append(
            f"| `{row.model}` | {row.reasoning_effort} | {row.observations} | "
            f"{row.evidence_steps} | {row.claims} | {row.load_bearing} | "
            f"{row.orphans} | {prose} | {_cell(row.fragments_unplaced)} | "
            f"{row.retries} | {row.traditional}/{row.simplified} | "
            f"{row.prompt_tokens} | {row.cached_tokens} | "
            f"{row.completion_tokens} | {cost} |"
        )
    notes = [f"- `{r.model}`: {note}" for r in rows for note in r.notes]
    errors = [f"- `{r.model}` failed: {r.error}" for r in rows if r.error]
    if notes or errors:
        lines.append("")
        lines.append("\\* cost does not cover the whole source.")
        lines.extend(notes + errors)
    return "\n".join(lines)


def bench_source(
    source_row: Mapping[str, Any], *, models: Sequence[str], output_dir: Path,
    reasoning_effort: str = "medium", max_output_tokens: Optional[int] = None,
    force: bool = True, sections: Optional[SectionSettings] = None,
) -> list[BenchRow]:
    """Run one source through every model and read a row out of each package.

    Each model writes into its own directory: the package name is derived from
    the source, so a shared one would have them overwrite each other and the
    last model would appear to be all of them.
    """

    prompt_path = (
        NOTES_PROMPT_PATH
        if str(source_row.get("source_type", "notes_manuscript")) == "notes_manuscript"
        else PROMPT_PATH
    )
    prompt = Path(prompt_path).read_text(encoding="utf-8")
    converter = _simplified_converter()
    rows: list[BenchRow] = []
    for model in models:
        started = time.monotonic()
        model_dir = output_dir / model.replace("/", "_")
        try:
            client = _bench_client(
                model, reasoning_effort=reasoning_effort,
                max_output_tokens=max_output_tokens,
            )
            _status, package_path = run_source(
                dict(source_row), output_dir=model_dir, client=client,
                prompt=prompt, reasoning_effort=reasoning_effort, force=force,
                sections=sections or SectionSettings(),
            )
            package = json.loads(Path(package_path).read_text(encoding="utf-8"))
            rows.append(row_from_package(
                package, model=model, reasoning_effort=reasoning_effort,
                elapsed_seconds=time.monotonic() - started, converter=converter,
            ))
        except Exception as exc:  # one model failing is a row, not a run
            rows.append(BenchRow(
                model=model, reasoning_effort=reasoning_effort,
                elapsed_seconds=time.monotonic() - started,
                error=f"{type(exc).__name__}: {exc}",
            ))
    return rows


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--model", action="append", dest="models", required=True,
                        help="repeatable; a model needs no MODEL_BACKENDS entry")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reasoning-effort", default="medium")
    parser.add_argument("--max-output-tokens", type=int, default=None)
    parser.add_argument("--section-level", type=int, default=None)
    parser.add_argument("--only-sections", type=int, nargs="+", metavar="N")
    parser.add_argument("--reuse-cache", action="store_true",
                        help="replay cached sections; cost columns then cover "
                             "only the sections that actually ran")
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)

    source_rows = load_source_manifest(args.source_manifest)
    section_kwargs: dict[str, Any] = {}
    if args.section_level is not None:
        section_kwargs["level"] = args.section_level
    if args.only_sections:
        section_kwargs["only"] = tuple(args.only_sections)
    sections = SectionSettings(**section_kwargs)

    all_rows: list[BenchRow] = []
    for source_row in source_rows:
        rows = bench_source(
            source_row, models=args.models, output_dir=args.output_dir,
            reasoning_effort=args.reasoning_effort,
            max_output_tokens=args.max_output_tokens,
            force=not args.reuse_cache, sections=sections,
        )
        print(f"\n## {source_row.get('source_id')}\n")
        print(render_markdown(rows))
        all_rows.extend(rows)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps([vars(r) for r in all_rows], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 1 if any(r.error for r in all_rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
