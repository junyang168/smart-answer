"""A reading index cannot silently drop or bind changed transcript sources."""

import json
import pytest
from backend.pipeline.media_batch.runtime import atomic, file_sha
from backend.pipeline.media_batch.series_catalog import build, render


def fixture(tmp_path):
    out = tmp_path / "raw"
    chunk = dict(
        chunk_id="w00/chunk-000",
        start_ms=0,
        end_ms=2000,
        text="原話 <script>not code</script>",
    )
    atomic(
        out / "rough.json",
        dict(uncorrected=True, source_sha256="source", chunks=[chunk]),
    )
    atomic(out / "rough-summary.json", dict(summary=dict(theme="題目", passages=[])))
    row = dict(
        recording="R",
        out=str(out),
        name="lecture.wav",
        source_sha256="source",
        rough_sha256=file_sha(out / "rough.json"),
        summary_sha256=file_sha(out / "rough-summary.json"),
        duration_ms=2000,
        summary=dict(theme="題目", passages=[]),
    )
    inputs = tmp_path / "inputs.json"
    review = tmp_path / "review.json"
    atomic(inputs, dict(batch="B", recordings=[row]))
    entry = dict(
        recording="R",
        rough_sha256=row["rough_sha256"],
        summary_sha256=row["summary_sha256"],
        series="羅馬書釋經",
        anchor="7:4",
        date="2018-04",
        date_basis="來源月份",
        camp_position=1,
        review_note="",
        primary_topic=None,
    )
    atomic(review, dict(input_sha256=file_sha(inputs), records=[entry]))
    return inputs, review, out


def test_changed_source_is_rejected_and_raw_quotes_are_escaped(tmp_path):
    inputs, review, out = fixture(tmp_path)
    result = build(inputs, review)
    page = render(result)
    assert "<script>not code</script>" not in page
    assert "&lt;script&gt;not code&lt;/script&gt;" in page
    (out / "rough.json").write_text("{}")
    with pytest.raises(ValueError, match="source changed"):
        build(inputs, review)


def test_omission_or_unreviewed_extra_series_is_rejected(tmp_path):
    inputs, review, _ = fixture(tmp_path)
    data = json.loads(review.read_text())
    entry = data["records"][0]
    data["records"] = []
    atomic(review, data)
    with pytest.raises(ValueError, match="every source exactly once"):
        build(inputs, review)
    entry["series"] = "羅馬書第七章系列"
    data["records"] = [entry]
    atomic(review, data)
    with pytest.raises(ValueError, match="Only five"):
        build(inputs, review)
