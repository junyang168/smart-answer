"""Shared test setup."""
import pytest


@pytest.fixture(autouse=True)
def _no_local_bible_cache(monkeypatch):
    """Flash tests must not read the machine's 和合本 cache under DATA_BASE_DIR;
    a test that needs verses passes its own `claim_flash_citations.Bible`."""
    from backend.pipeline import claim_flash_citations as citations
    monkeypatch.setattr(citations, 'default_bible', lambda: citations.Bible({}))
