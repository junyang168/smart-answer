"""Run two independent, subscription-backed blind semantic discoveries.

Calibration only: this runner writes immutable files under an explicit output
directory and has no Canonical Repository dependency or apply path.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.blind_semantic_graph import (
    BlindSemanticGraphDiscovery,
    build_blind_packet,
    canonicalize_component_key_delimiters,
    discovery_metrics,
    discovery_structure_sets,
    validate_discovery,
)
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient


PROMPT_PATH = Path(__file__).with_name("prompts") / "blind_semantic_graph_discovery.md"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_immutable(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != rendered:
            raise ValueError(f"immutable artifact differs at {path}")
        return
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)


def _run_one(*, name: str, client: Any, prompt: str, packet: Any, output_dir: Path) -> dict[str, Any]:
    raw_path = output_dir / f"{name}.raw.json"
    discovery_path = output_dir / f"{name}.discovery.json"
    normalization_path = output_dir / f"{name}.normalization.json"
    if discovery_path.exists():
        discovery = validate_discovery(packet, _read(discovery_path))
        normalization = _read(normalization_path) if normalization_path.exists() else {"component_key_delimiter_repairs": 0}
        return {"name": name, "cached": True, "normalization": normalization, "discovery": discovery}

    if raw_path.exists():
        raw = _read(raw_path)
        elapsed = None
    else:
        started = time.monotonic()
        raw = client.generate_json(
            system_prompt=prompt,
            user_prompt=json.dumps(packet.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")),
            json_schema=BlindSemanticGraphDiscovery.model_json_schema(),
            temperature=0,
            timeout_seconds=1200,
        )
        elapsed = round(time.monotonic() - started, 3)
        _write_immutable(raw_path, raw)
    normalized, repair_count = canonicalize_component_key_delimiters(raw)
    normalization = {
        "schema_version": "wang_blind_semantic_graph_normalization_v1",
        "raw_response_sha256": sha256_json(raw),
        "component_key_delimiter_repairs": repair_count,
        "semantic_fields_changed": 0,
    }
    normalization["artifact_sha256"] = sha256_json(normalization)
    _write_immutable(normalization_path, normalization)
    discovery = validate_discovery(packet, normalized)
    _write_immutable(discovery_path, discovery.model_dump(mode="json"))
    return {"name": name, "cached": elapsed is None, "elapsed_seconds": elapsed, "normalization": normalization, "discovery": discovery}


def _jaccard(left: set[str], right: set[str]) -> float:
    return 1.0 if not left and not right else round(len(left & right) / len(left | right), 4)


def _compare_sets(left: set[str], right: set[str]) -> dict[str, Any]:
    return {
        "jaccard": _jaccard(left, right),
        "shared": sorted(left & right),
        "sol_only": sorted(left - right),
        "opus_only": sorted(right - left),
    }


def run_poc(*, input_path: Path, output_dir: Path) -> dict[str, Any]:
    packet = build_blind_packet(_read(input_path))
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    _write_immutable(output_dir / "blind-packet.json", packet.model_dump(mode="json"))

    runs = [
        _run_one(
            name="sol-high",
            client=CodexSubscriptionClient(
                model="gpt-5.6-sol", reasoning_effort="high", timeout_seconds=1200,
                max_output_tokens=32000,
            ),
            prompt=prompt,
            packet=packet,
            output_dir=output_dir,
        ),
        _run_one(
            name="opus-high",
            client=ClaudeSubscriptionClient(
                model="claude-opus-5", reasoning_effort="high", timeout_seconds=1200,
                max_output_tokens=32000,
            ),
            prompt=prompt,
            packet=packet,
            output_dir=output_dir,
        ),
    ]
    metrics = {row["name"]: discovery_metrics(row["discovery"]) for row in runs}
    structures = {
        row["name"]: discovery_structure_sets(row["discovery"])
        for row in runs
    }
    comparison = {
        key: _compare_sets(structures["sol-high"][key], structures["opus-high"][key])
        for key in structures["sol-high"]
    }
    comparison["central_synthesis"] = {
        "sol": [row.statement for row in runs[0]["discovery"].central_synthesis],
        "opus": [row.statement for row in runs[1]["discovery"].central_synthesis],
        "basis_claim_comparison": comparison["central_basis_claim_ids"],
    }
    report = {
        "schema_version": "wang_blind_semantic_graph_poc_report_v2",
        "input_packet_sha256": packet.packet_sha256,
        "prompt_sha256": sha256_json({"prompt": prompt}),
        "blind_input_claim_count": len(packet.claims),
        "models": {
            row["name"]: {
                "cached": row["cached"],
                "elapsed_seconds": row.get("elapsed_seconds"),
                "discovery_sha256": sha256_json(row["discovery"].model_dump(mode="json")),
                "normalization": row["normalization"],
                "metrics": metrics[row["name"]],
            }
            for row in runs
        },
        "deterministic_comparison": comparison,
        "blindness": {
            "model_input_fields": ["schema_version", "claims", "packet_sha256"],
            "excluded": [
                "existing CanonicalViewpoints",
                "existing ArgumentRoutes",
                "articles and CompositionPlans",
                "design worked examples",
                "target question or expected synthesis",
            ],
        },
        "master_data_mutations": 0,
        "apply_allowed": False,
    }
    report["artifact_sha256"] = sha256_json(report)
    _write_immutable(output_dir / "poc-report.v2.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run_poc(input_path=args.input, output_dir=args.output_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
