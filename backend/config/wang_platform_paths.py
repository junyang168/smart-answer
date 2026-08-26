"""Canonical filesystem layout for the Wang Knowledge Platform.

PostgreSQL remains the authoring authority.  These paths are for published
repository artifacts, compiled snapshots, catalog projections, and
regenerable staging data only.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


WANG_PLATFORM_DIRNAME = "wang-knowledge-platform"


@dataclass(frozen=True)
class WangPlatformPaths:
    root: Path
    repository: Path
    active_snapshots: Path
    catalog: Path
    staging: Path
    claim_layer_staging: Path
    corpus_survey_staging: Path
    library_audit_reports: Path
    seed_catalog: Path
    sermon_catalog: Path
    sermon_catalog_overrides: Path
    matthew_source_coverage: Path
    matthew_source_coverage_report: Path


def wang_platform_paths(data_base_dir: str | Path | None = None) -> WangPlatformPaths:
    """Resolve the canonical file layout without creating any directories."""

    value = data_base_dir if data_base_dir is not None else os.getenv("DATA_BASE_DIR")
    if not value:
        raise RuntimeError("DATA_BASE_DIR is required")
    root = (Path(value).expanduser().resolve() / WANG_PLATFORM_DIRNAME).resolve()
    catalog = root / "catalog"
    staging = root / "staging"
    return WangPlatformPaths(
        root=root,
        repository=root / "repository",
        active_snapshots=root / "compiled" / "active-snapshots",
        catalog=catalog,
        staging=staging,
        claim_layer_staging=staging / "claim-layer",
        corpus_survey_staging=staging / "corpus-survey",
        # `scripts/audit-library.py` writes here. It cannot import this module
        # -- it imports nothing from `backend/`, which is the point of it -- so
        # the path is spelled out in both places. Changing one means changing
        # the other.
        library_audit_reports=staging / "reports" / "library-audit",
        seed_catalog=catalog / "seed-catalog",
        sermon_catalog=catalog / "sermon_catalog.json",
        sermon_catalog_overrides=catalog / "sermon_catalog_overrides.json",
        matthew_source_coverage=catalog / "matthew_source_coverage.json",
        matthew_source_coverage_report=catalog / "matthew_source_coverage.md",
    )
