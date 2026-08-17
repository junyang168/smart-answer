"""Move article authoring contracts from staging JSON into the authoring store.

The load-bearing steps of every published Matthew article -- which sentences of
the base manuscript an article must preserve -- lived only in a
`base-manuscript-contract-input.json` beside the staging artifacts. That file
was outside version control, outside PostgreSQL, produced by no program, and
asserted its own `editor_confirmed` status with nothing able to verify the
claim. Meanwhile the authoring store already held the CompositionPlan the
contract belongs to.

This module reads those contracts, verifies every `source_excerpt` is still a
verbatim substring of the manuscript it names, and merges them onto their
CompositionPlan so the authoring store becomes the single authority. It is a
migration, not a generator: it never invents a step and never edits prose.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from backend.api.canonical_repository.knowledge_models import CompositionPlanRecord


class AuthoringContractMigrationError(RuntimeError):
    """Raised when a contract cannot be migrated without losing or altering data."""


@dataclass
class ContractVerification:
    contract_path: Path
    plan_id: str
    step_total: int = 0
    verified_steps: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures and self.step_total == self.verified_steps


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AuthoringContractMigrationError(f"missing contract: {path}") from exc
    except json.JSONDecodeError as exc:
        raise AuthoringContractMigrationError(f"invalid JSON: {path}") from exc


def load_contract(path: Path) -> dict[str, Any]:
    """Return the contract body, unwrapping the generation envelope if present."""

    raw = _read_json(path)
    return raw.get("result", raw) if "result" in raw else raw


def _source_paths(contract: dict[str, Any]) -> dict[str, Path]:
    records: list[dict[str, Any]] = []
    if contract.get("base_source"):
        records.append(contract["base_source"])
    records.extend(contract.get("additional_base_sources") or [])
    return {
        str(record["source_id"]): Path(record["path"])
        for record in records
        if record.get("source_id") and record.get("path")
    }


def verify_contract(contract: dict[str, Any], contract_path: Path) -> ContractVerification:
    """Check every required step still quotes its manuscript verbatim.

    A step whose excerpt no longer appears in the source cannot be migrated:
    either the manuscript changed under it, or the excerpt was never accurate.
    Both need a human decision, so neither is written.
    """

    plan_id = str((contract.get("composition_plan") or {}).get("plan_id") or "")
    result = ContractVerification(contract_path=contract_path, plan_id=plan_id)
    source_paths = _source_paths(contract)
    source_text: dict[str, str] = {}

    for section in contract.get("sections", []) or []:
        for step in section.get("required_argument_steps", []) or []:
            result.step_total += 1
            step_id = str(step.get("step_id") or "<unnamed>")
            source_id = str(step.get("source_id") or "")
            excerpt = str(step.get("source_excerpt") or "")

            if not excerpt:
                result.failures.append(f"{step_id}: 沒有 source_excerpt")
                continue
            path = source_paths.get(source_id)
            if path is None:
                result.failures.append(
                    f"{step_id}: source_id {source_id!r} 不在 base_source / additional_base_sources 之中"
                )
                continue
            if source_id not in source_text:
                try:
                    source_text[source_id] = path.read_text(encoding="utf-8")
                except OSError as exc:
                    result.failures.append(f"{step_id}: 無法讀取來源 {path} ({exc})")
                    continue
            if excerpt in source_text[source_id]:
                result.verified_steps += 1
            else:
                result.failures.append(
                    f"{step_id}: source_excerpt 不是 {source_id} 的逐字子字串"
                )
    return result


def merge_contract_into_plan(
    plan_payload: dict[str, Any],
    contract: dict[str, Any],
    *,
    confirmed_by: str,
    confirmed_at: str,
) -> dict[str, Any]:
    """Return the plan payload carrying the contract, without touching decisions.

    `contract_confirmed_by` records who is standing behind the migrated
    contract. The staging file's bare `editor_confirmed` string named nobody
    and no time, which is exactly what made it unverifiable.
    """

    merged = dict(plan_payload)
    merged["contract_id"] = contract.get("contract_id")
    merged["contract_schema_version"] = contract.get("schema_version")
    merged["passage"] = contract.get("passage")
    merged["authoring_mode"] = contract.get("authoring_mode")
    merged["base_source"] = contract.get("base_source")
    merged["additional_base_sources"] = contract.get("additional_base_sources") or []
    merged["authoring_sections"] = contract.get("sections") or []
    merged["supplemental_material"] = contract.get("supplemental_material") or []
    merged["global_rules"] = contract.get("global_rules") or []
    merged["contract_confirmed_by"] = confirmed_by
    merged["contract_confirmed_at"] = confirmed_at

    contract_decision_ids = {
        decision_id
        for section in contract.get("sections", []) or []
        for decision_id in section.get("decision_ids", []) or []
    }
    plan_decision_ids = set(merged.get("decision_ids") or [])
    unknown = contract_decision_ids - plan_decision_ids
    if unknown:
        raise AuthoringContractMigrationError(
            f"contract references decisions absent from plan {merged.get('plan_id')}: {sorted(unknown)}"
        )
    # Validate the merged shape before it can reach the store.
    CompositionPlanRecord.model_validate(merged)
    return merged


def build_migration_package(
    entries: Iterable[tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]],
    *,
    package_id: str,
    confirmed_by: str,
    confirmed_at: str,
) -> dict[str, Any]:
    """Build a knowledge package carrying the contract-bearing plans.

    The importer re-splits each `product_plans` entry into a plan plus its
    decisions and recomputes `decision_ids` from the `decisions` list, so the
    package must carry every existing decision verbatim. Omitting them erases
    the plan's decision_ids; sending id-only stubs would overwrite real
    decision records with empty ones. Passing them through unchanged leaves
    their content fingerprints identical, so the store records them as
    unchanged rather than bumping their revisions.
    """

    plans = []
    for plan_payload, decisions, contract in entries:
        merged = merge_contract_into_plan(
            plan_payload, contract, confirmed_by=confirmed_by, confirmed_at=confirmed_at
        )
        carried = [dict(decision) for decision in decisions]
        carried_ids = {decision.get("decision_id") for decision in carried}
        expected_ids = set(merged.get("decision_ids") or [])
        if carried_ids != expected_ids:
            raise AuthoringContractMigrationError(
                f"plan {merged.get('plan_id')} decisions do not match decision_ids: "
                f"missing={sorted(expected_ids - carried_ids)}, extra={sorted(carried_ids - expected_ids)}"
            )
        merged["decisions"] = carried
        plans.append(merged)
    return {"package_id": package_id, "product_plans": plans}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--contract", action="append", required=True, type=Path,
        help="path to a base-manuscript-contract JSON (repeatable)",
    )
    parser.add_argument("--confirmed-by", required=True)
    parser.add_argument("--confirmed-at", required=True)
    parser.add_argument("--package-id", default="AUTHORING-CONTRACT-MIGRATION-01")
    parser.add_argument(
        "--apply", action="store_true",
        help="write to PostgreSQL; without it the run only verifies and reports",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    contracts = [(path, load_contract(path)) for path in args.contract]

    verifications = [verify_contract(contract, path) for path, contract in contracts]
    for check in verifications:
        status = "ok" if check.ok else "FAILED"
        print(
            f"{check.plan_id:<24} steps={check.verified_steps}/{check.step_total} {status}"
        )
        for failure in check.failures:
            print(f"    - {failure}")
    if any(not check.ok for check in verifications):
        print("\n驗證未通過，沒有寫入任何資料。")
        return 1

    if not args.apply:
        print("\n驗證通過。加 --apply 才會寫入 PostgreSQL。")
        return 0

    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore

    store = PostgresKnowledgeStore()
    entries = []
    for (path, contract), check in zip(contracts, verifications):
        record = store.get_record("composition_plans", check.plan_id)
        if record is None:
            raise AuthoringContractMigrationError(
                f"plan {check.plan_id} is not in the authoring store"
            )
        decisions = []
        for decision_id in record.get("decision_ids") or []:
            decision = store.get_record("composition_decisions", decision_id)
            if decision is None:
                raise AuthoringContractMigrationError(
                    f"decision {decision_id} referenced by {check.plan_id} is not in the authoring store"
                )
            decisions.append(decision)
        entries.append((record, decisions, contract))

    package = build_migration_package(
        entries,
        package_id=args.package_id,
        confirmed_by=args.confirmed_by,
        confirmed_at=args.confirmed_at,
    )
    plan = store.plan_package(package, source_kind="authoring_contract_migration")
    result = store.apply_plan(plan, metadata={"migration": "base_manuscript_contract"})
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
