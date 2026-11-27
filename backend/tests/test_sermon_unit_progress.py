import json

from backend.pipeline import sermon_unit_progress as progress


def test_cross_lecture_progress_uses_series_units_and_preserves_revision(tmp_path, monkeypatch):
    folder = tmp_path / "unit_argument_series" / "series"
    (folder / "units" / "U1").mkdir(parents=True)
    (folder / "units" / "U1" / "unit-argument.json").write_text("{}")
    (folder / "segmentation.json").write_text(json.dumps({
        "sources": [{"lecture": "L1", "source_id": "first"}, {"lecture": "L2", "source_id": "second"}],
        "units": [{"unit_id": "U1", "ranges": ["L1-S0001", "L2-S0001"]},
                  {"unit_id": "U2", "ranges": ["L2-S0002"]}],
    }))
    monkeypatch.setattr(progress.reader, "_loaded_unit_keys", lambda: {("series", "U1")})
    monkeypatch.setattr(progress.reader, "_latest_rulings", lambda *_: {
        "U1": {"verdict": "needs_revision"},
    })
    rows = progress.lecture_progress(tmp_path)
    assert rows["first"] == dict(series_id="series", total=1, generated=1, loaded=1,
                                 approved=0, needs_revision=1)
    assert rows["second"] == dict(series_id="series", total=2, generated=1, loaded=1,
                                  approved=0, needs_revision=1)
    assert progress.lecture_progress(tmp_path / "absent") == {}
