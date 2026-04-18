#!/usr/bin/env python3
"""Analyze transcript JSON files with OpenAI structured outputs.

This script reuses the repo's existing OpenAI client helper:
`backend.api.openai_client.generate_structured_json`.

It is designed for long-running batch analysis:
- one transcript per API request
- stable filename ordering
- append-only NDJSON output for resumability
- aggregate JSON output for downstream use
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Set


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_generate_structured_json():
    """Load backend/api/openai_client.py without importing backend.api package."""
    module_path = REPO_ROOT / "backend" / "api" / "openai_client.py"
    spec = importlib.util.spec_from_file_location("repo_openai_client", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load OpenAI client module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.generate_structured_json


_GENERATE_STRUCTURED_JSON = None


def get_generate_structured_json():
    global _GENERATE_STRUCTURED_JSON
    if _GENERATE_STRUCTURED_JSON is None:
        _GENERATE_STRUCTURED_JSON = _load_generate_structured_json()
    return _GENERATE_STRUCTURED_JSON


DEFAULT_INPUT_DIR = Path("/opt/homebrew/var/www/church/web/data/script_patched")
DEFAULT_OUTPUT_JSON = REPO_ROOT / "transcript_analysis_openai.json"
DEFAULT_OUTPUT_NDJSON = REPO_ROOT / "transcript_analysis_openai.ndjson"

MODEL_DEFAULT = "gpt-5.4"


ANALYSIS_SCHEMA: Dict[str, Any] = {
    "name": "transcript_analysis",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "file": {"type": "string"},
            "title_guess": {"type": "string"},
            "series": {"type": "string"},
            "primary_mode": {
                "type": "string",
                "enum": ["exegesis", "topical"],
            },
            "bible_books": {
                "type": "array",
                "items": {"type": "string"},
            },
            "bible_chapters": {
                "type": "array",
                "items": {"type": "string"},
            },
            "passage_focus": {
                "type": "array",
                "items": {"type": "string"},
            },
            "secondary_scripture_refs": {
                "type": "array",
                "items": {"type": "string"},
            },
            "theological_topics": {
                "type": "array",
                "items": {"type": "string"},
            },
            "summary": {"type": "string"},
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
            },
            "evidence": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "file",
            "title_guess",
            "series",
            "primary_mode",
            "bible_books",
            "bible_chapters",
            "passage_focus",
            "secondary_scripture_refs",
            "theological_topics",
            "summary",
            "confidence",
            "evidence",
        ],
        "additionalProperties": False,
    },
}


SYSTEM_PROMPT = """
你是「中文神學講道 / 釋經逐字稿分類與標註助手」。

你的任務是閱讀一篇 transcript，輸出結構化 JSON。

核心判準：
1. `primary_mode` 只能是 `exegesis` 或 `topical`。
2. 只要主軸是在解釋某一段或某一組連續經文，即使講員帶出大量神學、教義、聖經神學、系統神學、應用、跨經文比較，仍然算 `exegesis`。
3. 只有在講員主要是在講一個教義 / 主題，而不是持續解釋某一個主要 passage 時，才算 `topical`。
4. 若 transcript 屬於系列釋經的一講，仍優先判為 `exegesis`。

輸出規則：
1. 使用繁體中文。
2. `bible_books` 只列主要被釋經的書卷；若是 topical 且無單一主釋經書卷，可以留空陣列。
3. `bible_chapters` 只列主要被釋經的章；若是 topical 且無單一主章節，可以留空陣列。
4. `passage_focus` 盡量精確到段落，例如「馬太福音 13:1-53」；若無法精確，至少寫到章。
5. `secondary_scripture_refs` 放輔助引用、平行經文、神學展開時大量旁徵博引的經文。
6. `theological_topics` 可列神學主題；這些主題不能用來否定一篇明顯是釋經的 transcript。
7. `summary` 用 1-3 句簡潔描述主軸。
8. `confidence` 介於 0 到 1。
9. `evidence` 放你判斷時最關鍵的文字證據，應該是短句，不要太長。

重要：
- 不可因為一篇釋經講章帶出很多神學內容，就誤判成 topical。
- 若開頭或反覆出現「今天看馬太福音第十三章」這類線索，通常應判為 `exegesis`。
- 只輸出符合 schema 的 JSON。
""".strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch-analyze transcript JSON files with OpenAI structured outputs."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help=f"Directory containing transcript JSON files. Default: {DEFAULT_INPUT_DIR}",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=DEFAULT_OUTPUT_JSON,
        help=f"Aggregate JSON output path. Default: {DEFAULT_OUTPUT_JSON}",
    )
    parser.add_argument(
        "--output-ndjson",
        type=Path,
        default=DEFAULT_OUTPUT_NDJSON,
        help=f"Append-only NDJSON output path. Default: {DEFAULT_OUTPUT_NDJSON}",
    )
    parser.add_argument(
        "--model",
        default=MODEL_DEFAULT,
        help=f"OpenAI model name. Default: {MODEL_DEFAULT}",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only analyze the first N files after sorting and start offset.",
    )
    parser.add_argument(
        "--start",
        type=int,
        default=0,
        help="Zero-based start offset after filename sorting.",
    )
    parser.add_argument(
        "--pattern",
        default="*.json",
        help="Glob pattern inside input dir. Default: *.json",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Sampling temperature. Default: 0.0",
    )
    parser.add_argument(
        "--max-completion-tokens",
        type=int,
        default=2000,
        help="Max completion tokens for structured output. Default: 2000",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip files already present in NDJSON output.",
    )
    return parser.parse_args()


def list_input_files(input_dir: Path, pattern: str) -> List[Path]:
    return sorted(input_dir.glob(pattern), key=lambda p: p.name)


def slice_files(files: Sequence[Path], start: int, limit: int | None) -> List[Path]:
    sliced = list(files[start:])
    if limit is not None:
        sliced = sliced[:limit]
    return sliced


def load_processed_files(ndjson_path: Path) -> Set[str]:
    processed: Set[str] = set()
    if not ndjson_path.exists():
        return processed
    with ndjson_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            file_name = payload.get("file")
            if isinstance(file_name, str) and file_name:
                processed.add(file_name)
    return processed


def read_transcript_text(path: Path) -> str:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        texts: List[str] = []
        for item in raw:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
        return "\n\n".join(texts)
    if isinstance(raw, dict):
        for key in ("text", "content", "transcript"):
            value = raw.get(key)
            if isinstance(value, str):
                return value
    raise ValueError(f"Unsupported transcript structure: {path}")


def build_user_prompt(file_path: Path, transcript_text: str) -> str:
    return (
        f"檔名：{file_path.name}\n"
        f"完整 transcript 如下：\n\n"
        f"{transcript_text}"
    )


def analyze_file(
    file_path: Path,
    *,
    model: str,
    temperature: float,
    max_completion_tokens: int,
) -> Dict[str, Any]:
    transcript_text = read_transcript_text(file_path)
    result = get_generate_structured_json()(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=build_user_prompt(file_path, transcript_text),
        json_schema=ANALYSIS_SCHEMA,
        model=model,
        temperature=temperature,
        max_tokens=max_completion_tokens,
    )
    # Keep filename canonical even if the model paraphrases it.
    result["file"] = file_path.name
    return result


def append_ndjson(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def load_ndjson_items(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    items: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            items.append(json.loads(line))
    return items


def write_aggregate_json(path: Path, items: Iterable[Dict[str, Any]]) -> None:
    payload = {
        "items": sorted(items, key=lambda item: item.get("file", "")),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    input_dir: Path = args.input_dir.resolve()
    output_json: Path = args.output_json.resolve()
    output_ndjson: Path = args.output_ndjson.resolve()

    if not input_dir.exists():
        raise FileNotFoundError(f"Input dir does not exist: {input_dir}")

    files = list_input_files(input_dir, args.pattern)
    files = slice_files(files, args.start, args.limit)

    processed_files: Set[str] = set()
    if args.skip_existing:
        processed_files = load_processed_files(output_ndjson)
        files = [path for path in files if path.name not in processed_files]

    total = len(files)
    print(f"Input dir: {input_dir}")
    print(f"Files to analyze: {total}")
    print(f"Model: {args.model}")

    failures: List[Dict[str, str]] = []

    for index, file_path in enumerate(files, start=1):
        print(f"[{index}/{total}] Analyzing {file_path.name} ...", flush=True)
        try:
            result = analyze_file(
                file_path,
                model=args.model,
                temperature=args.temperature,
                max_completion_tokens=args.max_completion_tokens,
            )
            append_ndjson(output_ndjson, result)
            aggregate_items = load_ndjson_items(output_ndjson)
            write_aggregate_json(output_json, aggregate_items)
        except Exception as exc:
            message = str(exc)
            print(f"  ERROR: {message}", file=sys.stderr, flush=True)
            failures.append({"file": file_path.name, "error": message})

    if failures:
        failure_path = output_json.with_name(output_json.stem + ".failures.json")
        failure_path.write_text(
            json.dumps({"failures": failures}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Completed with {len(failures)} failures. See {failure_path}", file=sys.stderr)
        return 1

    print(f"Completed successfully. JSON: {output_json}")
    print(f"Completed successfully. NDJSON: {output_ndjson}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
