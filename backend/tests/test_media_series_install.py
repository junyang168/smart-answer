"""Real SQL/file install: preserve prior sermons, fail closed on drift, resume once."""

import json
import pytest
from psycopg.types.json import Jsonb
from backend.tests.test_media_batch import store
from backend.pipeline.media_batch.runtime import atomic, file_sha
from backend.pipeline.media_batch.series_catalog import SERIES
from backend.pipeline.media_batch.series_install import prepare, apply, IDS


def setup_plan(store, tmp_path):
    root = tmp_path / "site"
    atomic(
        root / "config/sermon.json",
        [
            dict(
                item="legacy", status="published", title="人手校對", assigned_to="owner"
            )
        ],
    )
    atomic(
        root / "config/sermon_series.json",
        [dict(id="羅馬書釋經", title="舊版", sermons=["legacy"])],
    )
    b = store.batch(dict(capacity_root=str(tmp_path / "capacity"), mode="rough"))
    records = []
    with store.connection() as c:
        for i, series in enumerate(SERIES):
            rid = f"R{i}"
            item = f"INGEST-{i}"
            sha = f"source-{i}"
            out = tmp_path / rid
            atomic(out / "rough.json", dict(text="已存原話"))
            atomic(out / "rough-summary.json", dict(theme="題目"))
            info = dict(source_sha=sha, video=False)
            c.execute(
                "INSERT INTO media_ingest.recordings VALUES (%s,NULL,%s,%s,%s)",
                (rid, sha, item, Jsonb(info)),
            )
            c.execute(
                "INSERT INTO media_ingest.assets VALUES (%s,%s,%s)",
                (sha, rid, Jsonb(info)),
            )
            tid = store.task(
                c, b, rid, "rough_summary", "all", "binding", dict(info=info)
            )
            result = dict(
                rough_sha256=file_sha(out / "rough.json"),
                summary_sha256=file_sha(out / "rough-summary.json"),
            )
            c.execute(
                "UPDATE media_ingest.tasks SET state='complete',result=%s WHERE id=%s",
                (Jsonb(result), tid),
            )
            records.append(
                dict(
                    recording=rid,
                    out=str(out),
                    source_sha256=sha,
                    series=series,
                    reading_position=1,
                    date="2018-04",
                    href="/rough/" + rid,
                    summary=dict(theme=f"題目{i}"),
                    **result,
                )
            )
    catalog = tmp_path / "catalog.json"
    atomic(catalog, dict(batch=b, counts={s: 1 for s in SERIES}, records=records))
    return root, catalog, prepare(store, catalog, root, tmp_path / "install")


def test_install_is_idempotent_preserves_legacy_and_does_not_start_work(
    store, tmp_path
):
    root, catalog, plan = setup_plan(store, tmp_path)
    receipt = apply(store, plan, "owner-operation")
    assert receipt["applied"]
    rows = json.loads((root / "config/sermon.json").read_text())
    assert rows[0] == dict(
        item="legacy", status="published", title="人手校對", assigned_to="owner"
    )
    assert len(rows) == 6 and all(
        r["status"] == "processing" and not r["assigned_to"] for r in rows[1:]
    )
    series = json.loads((root / "config/sermon_series.json").read_text())
    assert series[0]["sermons"] == ["legacy"]
    assert [s["id"] for s in series[1:]] == [IDS[s] for s in SERIES]
    with store.connection() as c:
        rows = c.execute(
            "SELECT info->>'processing_order' AS position FROM media_ingest.recordings WHERE id<>'legacy' ORDER BY id"
        ).fetchall()
        assert [int(r["position"]) for r in rows] == list(range(5))
        assert (
            c.execute(
                "SELECT count(*) AS n FROM media_ingest.tasks WHERE state<>'complete'"
            ).fetchone()["n"]
            == 0
        )
        assert (
            c.execute("SELECT count(*) AS n FROM media_ingest.calls").fetchone()["n"]
            == 0
        )
    assert apply(store, plan, "owner-operation")["already_applied"]
    assert prepare(store, catalog, root, tmp_path / "install") == plan


def test_drift_is_not_overwritten_and_no_sql_priority_changes(store, tmp_path):
    root, _, plan = setup_plan(store, tmp_path)
    p = root / "config/sermon.json"
    data = json.loads(p.read_text())
    data[0]["title"] = "剛剛人手修改"
    atomic(p, data)
    with pytest.raises(ValueError, match="metadata changed"):
        apply(store, plan, "owner-operation")
    assert json.loads(p.read_text())[0]["title"] == "剛剛人手修改"
    with store.connection() as c:
        assert not c.execute(
            "SELECT 1 FROM media_ingest.recordings WHERE info ? 'processing_order'"
        ).fetchone()
