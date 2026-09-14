"""Recover two source rows proven to have been blanked during publication."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from backend.api.canonical_repository.postgres_store import canonical_json, sha256_json
from backend.api.sc_api.script_delta import ScriptDelta
from backend.pipeline.source_contract_cleanup_runner import (
    _clean_git_commit,
    _seal,
    _validate_seal,
    _write_new,
)
from backend.pipeline.source_projection import project_script


SCHEMA_VERSION = "wang_source_body_recovery_dry_run_v1"
RECEIPT_VERSION = "wang_source_body_recovery_receipt_v1"
RECOVERIES = (
    {
        "transcript_id": "2016 NYSC 專題：馬太福音釋經（四）2",
        "source_segment_index": 860,
    },
    {
        "transcript_id": "2016 NYSC 專題：馬太福音釋經（五）3",
        "source_segment_index": 1057,
    },
)


def _row(rows: list[dict[str, Any]], source_segment_index: Any) -> dict[str, Any]:
    matches = [
        row
        for row in rows
        if str(row.get("index")) == str(source_segment_index)
        and str(row.get("type") or "") not in {"subtitle", "comment"}
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected one body row for source segment {source_segment_index}, "
            f"found {len(matches)}"
        )
    return matches[0]


def _read_inputs(
    data_base_path: Path, transcript_id: str
) -> tuple[bytes, list[dict[str, Any]], bytes, dict[str, Any], bytes, list[dict[str, Any]]]:
    review_path = data_base_path / "script_review" / f"{transcript_id}.json"
    published_path = data_base_path / "script_published" / f"{transcript_id}.json"
    patched_path = data_base_path / "script_patched" / f"{transcript_id}.json"
    review_raw = review_path.read_bytes()
    published_raw = published_path.read_bytes()
    patched_raw = patched_path.read_bytes()
    review = json.loads(review_raw)
    published = json.loads(published_raw)
    patched = json.loads(patched_raw)
    if not isinstance(review, list) or not isinstance(patched, list):
        raise ValueError(f"review/patched source is not a row list: {transcript_id}")
    if not isinstance(published, dict) or not isinstance(published.get("script"), list):
        raise ValueError(f"published source is not a script envelope: {transcript_id}")
    return review_raw, review, published_raw, published, patched_raw, patched


def _changed_text_rows(
    before: list[Mapping[str, Any]], after: list[Mapping[str, Any]]
) -> list[str]:
    if len(before) != len(after):
        raise ValueError("source recovery changed the row count")
    return [
        str(left.get("index"))
        for left, right in zip(before, after)
        if str(left.get("text") or "") != str(right.get("text") or "")
    ]


def build_dry_run(
    *, data_base_path: Path, output_root: Path, repo_root: Path
) -> Path:
    runner_commit = _clean_git_commit(repo_root)
    recoveries: list[dict[str, Any]] = []
    for specification in RECOVERIES:
        transcript_id = specification["transcript_id"]
        source_index = specification["source_segment_index"]
        (
            review_raw,
            review,
            published_raw,
            published,
            patched_raw,
            patched,
        ) = _read_inputs(data_base_path, transcript_id)
        review_row = _row(review, source_index)
        published_row = _row(published["script"], source_index)
        patched_row = _row(patched, source_index)
        recovered_text = str(patched_row.get("text") or "")
        if (
            str(review_row.get("text") or "").strip()
            or str(published_row.get("text") or "").strip()
            or not recovered_text.strip()
        ):
            raise ValueError(f"source no longer has the proven blank-row shape: {transcript_id}")

        updated_review = copy.deepcopy(review)
        _row(updated_review, source_index)["text"] = recovered_text
        if _changed_text_rows(review, updated_review) != [str(source_index)]:
            raise ValueError(f"source recovery would change another row: {transcript_id}")
        review_encoded = json.dumps(
            updated_review, ensure_ascii=False, indent=4
        ).encode("UTF-8")

        delta = ScriptDelta(str(data_base_path), transcript_id)
        expected_published_script = copy.deepcopy(updated_review)
        delta.add_timeline(expected_published_script)
        if _changed_text_rows(published["script"], expected_published_script) != [
            str(source_index)
        ]:
            raise ValueError(
                f"republishing recovery would change another body row: {transcript_id}"
            )
        recoveries.append(
            {
                **specification,
                "review_path": str(
                    data_base_path / "script_review" / f"{transcript_id}.json"
                ),
                "published_path": str(
                    data_base_path / "script_published" / f"{transcript_id}.json"
                ),
                "patched_path": str(
                    data_base_path / "script_patched" / f"{transcript_id}.json"
                ),
                "review_before_sha256": hashlib.sha256(review_raw).hexdigest(),
                "review_after_sha256": hashlib.sha256(review_encoded).hexdigest(),
                "published_before_sha256": hashlib.sha256(published_raw).hexdigest(),
                "patched_sha256": hashlib.sha256(patched_raw).hexdigest(),
                "recovered_text_sha256": hashlib.sha256(
                    recovered_text.encode("utf-8")
                ).hexdigest(),
                "recovered_text_length": len(recovered_text),
                "published_author": str(
                    (published.get("metadata") or {}).get("author") or "WKP #368"
                ),
                "before_body_sha256": project_script(published["script"]).body_sha256,
                "after_body_sha256": project_script(
                    expected_published_script
                ).body_sha256,
                "expected_published_script_sha256": sha256_json(
                    expected_published_script
                ),
                "updated_review": updated_review,
            }
        )

    report = _seal(
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runner_git_commit": runner_commit,
            "recovery_count": len(recoveries),
            "recoveries": recoveries,
        }
    )
    run_dir = output_root / datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    report_path = run_dir / f"dry-run.{report['artifact_sha256'][:20]}.json"
    _write_new(report_path, report)
    print(report_path)
    print(
        canonical_json(
            {
                "recovery_count": len(recoveries),
                "recoveries": [
                    {
                        "transcript_id": row["transcript_id"],
                        "source_segment_index": row["source_segment_index"],
                        "recovered_text_length": row["recovered_text_length"],
                    }
                    for row in recoveries
                ],
            }
        )
    )
    return report_path


def _write_raw_new(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def apply_dry_run(*, report_path: Path, repo_root: Path) -> Path:
    runner_commit = _clean_git_commit(repo_root)
    report_bytes = report_path.read_bytes()
    report = json.loads(report_bytes)
    _validate_seal(report)
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported source recovery report")
    if report.get("runner_git_commit") != runner_commit:
        raise ValueError("runner commit differs from frozen source recovery")

    receipts: list[dict[str, Any]] = []
    backup_dir = report_path.parent / "source-backups"
    for recovery in report.get("recoveries") or []:
        transcript_id = str(recovery["transcript_id"])
        source_index = recovery["source_segment_index"]
        data_base_path = Path(str(recovery["review_path"])).parents[1]
        (
            review_raw,
            review,
            published_raw,
            published,
            patched_raw,
            patched,
        ) = _read_inputs(data_base_path, transcript_id)
        expected = {
            "review_before_sha256": hashlib.sha256(review_raw).hexdigest(),
            "published_before_sha256": hashlib.sha256(published_raw).hexdigest(),
            "patched_sha256": hashlib.sha256(patched_raw).hexdigest(),
        }
        for key, value in expected.items():
            if value != recovery[key]:
                raise ValueError(f"source changed after recovery dry-run: {transcript_id}/{key}")
        patched_text = str(_row(patched, source_index).get("text") or "")
        if hashlib.sha256(patched_text.encode("utf-8")).hexdigest() != recovery[
            "recovered_text_sha256"
        ]:
            raise ValueError(f"recovery provenance changed: {transcript_id}")
        updated_review = list(recovery["updated_review"])
        encoded = json.dumps(updated_review, ensure_ascii=False, indent=4).encode("UTF-8")
        if hashlib.sha256(encoded).hexdigest() != recovery["review_after_sha256"]:
            raise ValueError(f"sealed recovery payload changed: {transcript_id}")

        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", transcript_id).strip("_")
        review_backup = backup_dir / f"{slug}.review.{expected['review_before_sha256']}.json"
        published_backup = (
            backup_dir / f"{slug}.published.{expected['published_before_sha256']}.json"
        )
        _write_raw_new(review_backup, review_raw)
        _write_raw_new(published_backup, published_raw)

        written_review_sha = ScriptDelta.save_rows(
            str(data_base_path),
            transcript_id,
            "script_review",
            updated_review,
            expected_current_sha256=recovery["review_before_sha256"],
        )
        delta = ScriptDelta(str(data_base_path), transcript_id)
        written_published_sha = delta.publish(
            recovery["published_author"],
            expected_review_sha256=written_review_sha,
            expected_published_sha256=recovery["published_before_sha256"],
        )
        published_after = json.loads(Path(recovery["published_path"]).read_bytes())
        if sha256_json(published_after.get("script")) != recovery[
            "expected_published_script_sha256"
        ]:
            raise ValueError(f"published recovery readback changed: {transcript_id}")
        receipts.append(
            {
                "transcript_id": transcript_id,
                "source_segment_index": source_index,
                "review_before_sha256": recovery["review_before_sha256"],
                "review_after_sha256": written_review_sha,
                "published_before_sha256": recovery["published_before_sha256"],
                "published_after_sha256": written_published_sha,
                "recovered_text_sha256": recovery["recovered_text_sha256"],
                "review_backup_path": str(review_backup),
                "published_backup_path": str(published_backup),
                "readback": "verified",
            }
        )

    receipt = _seal(
        {
            "schema_version": RECEIPT_VERSION,
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "runner_git_commit": runner_commit,
            "dry_run_report_path": str(report_path),
            "dry_run_report_file_sha256": hashlib.sha256(report_bytes).hexdigest(),
            "recoveries": receipts,
        }
    )
    receipt_path = report_path.parent / f"apply-receipt.{receipt['artifact_sha256'][:20]}.json"
    _write_new(receipt_path, receipt)
    print(receipt_path)
    print(canonical_json({"recoveries": receipts}))
    return receipt_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-base-path",
        type=Path,
        default=Path("/opt/homebrew/var/www/church/web/data"),
    )
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--apply-report", type=Path)
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    output_root = args.output_root or (
        args.data_base_path
        / "wang-knowledge-platform/staging/source-contract-cleanup/WKP368/source-body-recovery"
    )
    if args.apply_report:
        apply_dry_run(report_path=args.apply_report, repo_root=repo_root)
    else:
        build_dry_run(
            data_base_path=args.data_base_path,
            output_root=output_root,
            repo_root=repo_root,
        )


if __name__ == "__main__":
    main()
