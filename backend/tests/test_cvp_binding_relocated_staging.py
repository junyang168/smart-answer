import pytest

from backend.pipeline import cvp_sql_commit as m


@pytest.fixture
def staging(tmp_path, monkeypatch):
    """Staging moved behind a symlink, as OPS-38 did with the Jun SSD."""
    target = tmp_path / "ssd" / "staging"
    (target / "run").mkdir(parents=True)
    (target / "run" / "raw.json").write_text('{"a": 1}')
    link = tmp_path / "data" / "staging"
    link.parent.mkdir()
    link.symlink_to(target)
    import backend.api.config as config
    monkeypatch.setattr(config, "WANG_STAGING_DIR", link)
    return link, target


def test_binding_recorded_before_the_move_still_checks(staging):
    link, _ = staging
    recorded = m.binding(link / "run" / "raw.json")
    assert recorded["path"] == str(link / "run" / "raw.json")
    m.check_binding(recorded)


def test_binding_recorded_through_the_ssd_path_still_checks(staging):
    _, target = staging
    m.check_binding(m.binding(target / "run" / "raw.json"))


def test_changed_content_is_still_drift(staging):
    link, target = staging
    recorded = m.binding(link / "run" / "raw.json")
    (target / "run" / "raw.json").write_text('{"a": 2}')
    with pytest.raises(ValueError, match="drift"):
        m.check_binding(recorded)
