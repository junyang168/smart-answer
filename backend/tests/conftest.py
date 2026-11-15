"""Shared test setup."""
import pytest


@pytest.fixture(autouse=True)
def _no_local_bible_cache(monkeypatch):
    """Flash tests must not read the machine's 和合本 cache under DATA_BASE_DIR;
    a test that needs verses passes its own `claim_flash_citations.Bible`."""
    from backend.pipeline import claim_flash_citations as citations
    monkeypatch.setattr(citations, 'default_bible', lambda: citations.Bible({}))


@pytest.fixture(autouse=True)
def _no_prayer_question(request, monkeypatch):
    """The Evidence layer asks Jev which paragraphs are his prayer. Fakes written
    before that question would choke on it, so it answers "none" unless a test
    is marked `prayer` and supplies its own Jev answer."""
    if 'prayer' in request.keywords:
        return
    from backend.pipeline import claim_flash_evidence as evidence
    monkeypatch.setattr(evidence, 'prayer_spans', lambda root, ports, paragraphs: {})


def pytest_configure(config):
    config.addinivalue_line('markers', 'prayer: the test answers the Evidence-layer prayer question itself')
