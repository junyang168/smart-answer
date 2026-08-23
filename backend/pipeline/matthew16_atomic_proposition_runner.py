"""Decompose one Matthew 16 viewpoint candidate into evidence-bound atoms."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_proposition_units import (
    AtomicDecompositionBatchResponse,
    build_claim_atomic_decomposition,
)
from backend.api.canonical_repository.viewpoint_resolution import (
    StructuredJsonReviewerAdapter,
    ViewpointIdentityReviewPacket,
)
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = Path(__file__).with_name("prompts") / "matthew16_atomic_proposition.md"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_new(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if _read(path) != payload:
            raise ValueError(f"immutable artifact differs at {path}")
        return
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _canonicalize_set_fields(raw: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Canonicalize JSON set fields without changing any semantic value."""

    normalized = copy.deepcopy(raw)
    changes: list[dict[str, Any]] = []
    proposals = normalized.get("proposals") or []
    sorted_proposals = sorted(proposals, key=lambda item: str(item.get("claim_id") or ""))
    if proposals != sorted_proposals:
        changes.append({"field": "proposals", "operation": "sort_by_claim_id"})
        normalized["proposals"] = sorted_proposals
    for proposal in normalized.get("proposals") or []:
        claim_id = str(proposal.get("claim_id") or "")
        for unit in proposal.get("units") or []:
            unit_id = str(unit.get("local_unit_id") or "")
            for field, key_fields in (
                ("claim_statement_spans", ("start_char", "end_char", "exact_text")),
                ("evidence_references", ("evidence_step_id", "source_fragment_id")),
            ):
                values = unit.get(field) or []
                unique = {
                    tuple(item.get(key) for key in key_fields): item
                    for item in values
                }
                canonical = [unique[key] for key in sorted(unique)]
                if values != canonical:
                    unit[field] = canonical
                    changes.append(
                        {
                            "claim_id": claim_id,
                            "local_unit_id": unit_id,
                            "field": field,
                            "operation": "exact_deduplicate_and_sort",
                            "before_count": len(values),
                            "after_count": len(canonical),
                        }
                    )
        for segment in proposal.get("coverage_segments") or []:
            values = segment.get("local_unit_ids") or []
            canonical = sorted(set(values))
            if values != canonical:
                segment["local_unit_ids"] = canonical
                changes.append(
                    {
                        "claim_id": claim_id,
                        "field": "coverage_segments.local_unit_ids",
                        "operation": "exact_deduplicate_and_sort",
                        "before_count": len(values),
                        "after_count": len(canonical),
                    }
                )
    return normalized, changes


def run_atomic_pilot(
    *,
    packet: ViewpointIdentityReviewPacket,
    claim_ids: list[str],
    target_proposition: str,
    output_dir: Path,
    model: str,
    reasoning_effort: str,
) -> dict[str, Any]:
    selected = [claim for claim in packet.claims if claim.claim_id in set(claim_ids)]
    if [claim.claim_id for claim in selected] != sorted(set(claim_ids)):
        missing = sorted(set(claim_ids) - {claim.claim_id for claim in selected})
        raise ValueError(f"selected Claims missing or unsorted in packet: {missing}")
    if any(not any(item.valid_for_identity_review for item in claim.evidence) for claim in selected):
        raise ValueError("every selected Claim needs identity-eligible evidence")
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    adapter = StructuredJsonReviewerAdapter(
        client=ClaudeSubscriptionClient(
            model=model,
            reasoning_effort=reasoning_effort,
            timeout_seconds=900,
            max_output_tokens=30000,
        ),
        prompt=prompt,
        response_model=AtomicDecompositionBatchResponse,
        schema_name="wang_viewpoint_atomic_decomposition_batch_response_v1",
    )
    plan = {
        "schema_version": "wang_matthew16_atomic_viewpoint_plan_v1",
        "target_proposition": target_proposition,
        "parent_packet_sha256": packet.packet_sha256,
        "selected_claim_ids": [claim.claim_id for claim in selected],
        "model_id": adapter.model_id,
        "backend": adapter.backend,
        "reasoning_effort": reasoning_effort,
        "prompt_sha256": adapter.prompt_sha256,
        "generation_config_sha256": adapter.generation_config_sha256,
        "master_data_mutations": 0,
        "apply_allowed": False,
    }
    plan["artifact_sha256"] = sha256_json(plan)
    _write_new(output_dir / "atomic-plan.json", plan)
    input_payload = {
        "target_proposition": target_proposition,
        "parent_packet_sha256": packet.packet_sha256,
        "claims": [claim.model_dump(mode="json") for claim in selected],
    }
    raw_path = output_dir / "raw-response.json"
    if raw_path.exists():
        raw = _read(raw_path)["response"]
        model_calls_executed = 0
    else:
        raw = dict(adapter.generate(input_payload))
        raw_artifact = {
            "schema_version": "wang_matthew16_atomic_raw_response_v1",
            "plan_artifact_sha256": plan["artifact_sha256"],
            "response_sha256": sha256_json(raw),
            "response": raw,
        }
        raw_artifact["artifact_sha256"] = sha256_json(raw_artifact)
        _write_new(raw_path, raw_artifact)
        model_calls_executed = 1
    normalized, normalization_changes = _canonicalize_set_fields(raw)
    normalization = {
        "schema_version": "wang_matthew16_atomic_normalization_v1",
        "raw_response_sha256": sha256_json(raw),
        "normalized_response_sha256": sha256_json(normalized),
        "changes": normalization_changes,
        "reader_visible_text_changed": False,
        "truth_conditions_changed": False,
    }
    normalization["artifact_sha256"] = sha256_json(normalization)
    _write_new(
        output_dir
        / "normalization-ledgers"
        / f"{normalization['artifact_sha256']}.json",
        normalization,
    )
    response = AtomicDecompositionBatchResponse.model_validate(normalized)
    if response.parent_packet_sha256 != packet.packet_sha256:
        raise ValueError("atomic response packet binding mismatch")
    if [item.claim_id for item in response.proposals] != [claim.claim_id for claim in selected]:
        raise ValueError("atomic response does not cover the exact selected Claim set")
    compiled = []
    claim_index = {claim.claim_id: claim for claim in selected}
    for proposal in response.proposals:
        artifact = build_claim_atomic_decomposition(
            parent_packet_sha256=packet.packet_sha256,
            claim=claim_index[proposal.claim_id],
            proposal=proposal,
            model_calls_executed=model_calls_executed,
        )
        path = output_dir / "decompositions" / f"{proposal.claim_id}.json"
        _write_new(path, artifact.model_dump(mode="json"))
        compiled.append(
            {
                "claim_id": proposal.claim_id,
                "artifact_sha256": artifact.artifact_sha256,
                "proposition_unit_ids": [item.proposition_unit_id for item in artifact.proposition_units],
            }
        )
    compiled.sort(key=lambda item: item["claim_id"])
    report = {
        "schema_version": "wang_matthew16_atomic_viewpoint_execution_v1",
        "plan_artifact_sha256": plan["artifact_sha256"],
        "parent_packet_sha256": packet.packet_sha256,
        "target_proposition": target_proposition,
        "model_id": adapter.model_id,
        "backend": adapter.backend,
        "prompt_sha256": adapter.prompt_sha256,
        "generation_config_sha256": adapter.generation_config_sha256,
        "raw_response_sha256": sha256_json(raw),
        "normalized_response_sha256": sha256_json(normalized),
        "normalization_artifact_sha256": normalization["artifact_sha256"],
        "decompositions": compiled,
        "claim_count": len(compiled),
        "proposition_unit_count": sum(len(item["proposition_unit_ids"]) for item in compiled),
        "model_calls_executed": model_calls_executed,
        "master_data_mutations": 0,
        "apply_allowed": False,
    }
    report["artifact_sha256"] = sha256_json(report)
    _write_new(output_dir / "atomic-execution.json", report)
    return report


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--claim-id", action="append", required=True)
    parser.add_argument("--target-proposition", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="claude-opus-5")
    parser.add_argument("--reasoning-effort", choices=("medium", "high", "xhigh"), default="high")
    args = parser.parse_args()
    report = run_atomic_pilot(
        packet=ViewpointIdentityReviewPacket.model_validate(_read(args.packet)),
        claim_ids=sorted(set(args.claim_id)),
        target_proposition=args.target_proposition,
        output_dir=args.output_dir,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
