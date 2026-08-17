from pathlib import Path

from backend.config.wang_platform_paths import wang_platform_paths


def test_wang_platform_paths_share_one_canonical_root(tmp_path: Path) -> None:
    paths = wang_platform_paths(tmp_path)

    assert paths.root == tmp_path.resolve() / "wang-knowledge-platform"
    assert paths.repository == paths.root / "repository"
    assert paths.active_snapshots == paths.root / "compiled" / "active-snapshots"
    assert paths.claim_layer_staging == paths.root / "staging" / "claim-layer"
    assert paths.corpus_survey_staging == paths.root / "staging" / "corpus-survey"
    assert paths.seed_catalog == paths.root / "catalog" / "seed-catalog"
    assert paths.sermon_catalog == paths.root / "catalog" / "sermon_catalog.json"
    assert paths.sermon_catalog_overrides == (
        paths.root / "catalog" / "sermon_catalog_overrides.json"
    )
    assert paths.matthew_source_coverage == (
        paths.root / "catalog" / "matthew_source_coverage.json"
    )


def test_resolver_does_not_create_directories(tmp_path: Path) -> None:
    paths = wang_platform_paths(tmp_path)

    assert not paths.root.exists()
