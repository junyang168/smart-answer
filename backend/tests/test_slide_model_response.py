import json

import pytest

from backend.pipeline.slide_change_pilot import REVIEW_SCHEMA
from backend.pipeline.slide_editor_alignment import ALIGNMENT_SCHEMA, FIDELITY_SCHEMA, FIDELITY_REPAIR_SCHEMA
from backend.pipeline.slide_model_response import parse_slide_answer
from backend.pipeline.slide_visual_evidence import SCHEMA


@pytest.mark.parametrize("schema", [REVIEW_SCHEMA, SCHEMA, ALIGNMENT_SCHEMA, FIDELITY_SCHEMA, FIDELITY_REPAIR_SCHEMA])
def test_one_payload_keeps_unicode_and_negative_verdict(schema):
    # The parser treats row data as opaque. Each caller validates its own fields.
    value = {"results": [{"id": "a", "passed": False, "text": '「λόγος」, {"results": []}'}]}
    doc, echo = json.dumps(value, ensure_ascii=False), json.dumps(schema)
    for text in (doc, echo + "\n" + doc, echo + ", " + doc[1:], json.dumps({**schema, **value}),
                 echo + "\n" + doc[:-2] + "箍]}"):
        assert parse_slide_answer(text, schema, id_field="id", expected_ids=["a"]) == value


@pytest.mark.parametrize("text", [
    '{"results":[],"results":[]}', '{"results":[{"id":"a","passed":false,"passed":true}]}',
    '{"results":[]} {"results":[]}', '{"results":[{"id":"a"} {"id":"b"}]}',
    '{"results":[{"id":"a"}, {"id":"a"} broken]}', '{"results":[{"id":"b"} broken]}',
    '{"results":[{"id":"a","text":"unfinished', '{"results":[{"id":"a"} extra: []}',
    '{"type":"wrong","results":[]}',
])
def test_ambiguous_or_incomplete_answers_are_rejected(text):
    with pytest.raises(ValueError):
        parse_slide_answer(text, FIDELITY_SCHEMA, id_field="id", expected_ids=["a", "b"])


def test_saved_lecture_responses_offline():
    """Replay the raw provider records, including earlier rejected repair attempts."""
    import os
    from pathlib import Path

    from backend.pipeline.slide_change_pilot import object_sha, validate_results
    from backend.pipeline.slide_pilot_models import VisionReviewer
    from backend.pipeline.slide_visual_evidence import check_schema, validate_content

    paths = os.environ.get("SLIDE_RESPONSE_REPLAY_DIRS")
    if not paths:
        pytest.skip("Set SLIDE_RESPONSE_REPLAY_DIRS to saved lecture run directories")
    adapter = object.__new__(VisionReviewer)
    adapter.provider = "gemini"  # read saved envelopes without initializing a network client
    for path in paths.split(os.pathsep):
        records = list(Path(path).rglob("response.json"))
        assert records, path
        rejected, accepted, children = {}, {}, {}
        for record in records:
            request = json.loads(record.with_name("request.json").read_text())
            raw = json.loads(record.read_text())
            sha = object_sha(raw)
            stage = request["stage"]
            parent = request.get("parent_response_sha256") or request.get("parent_rejected_batch_sha256")
            if parent:
                children.setdefault(parent, []).append(sha)
            if stage in {"slide_json_repair_once", "full_video_slide_classification"}:
                schema, key = REVIEW_SCHEMA, "candidate_id"
                ids = [Path(image["path"]).stem for image in request["images"]]
            elif stage in {"slide_visual_structure_repair_once", "slide_visual_extraction"}:
                schema, key, ids = SCHEMA, "asset_id", request["asset_ids"]
            elif stage == "slide_editor_alignment":
                schema, key, ids = ALIGNMENT_SCHEMA, "event_id", request["event_ids"]
            else:
                assert stage == "slide_fidelity_audit", stage
                schema, key, ids = FIDELITY_SCHEMA, "asset_id", request["asset_ids"]
            try:
                value = parse_slide_answer(adapter.text(raw), schema, id_field=key, expected_ids=ids)
                check_schema(value, schema)
                if [row[key] for row in value["results"]] != ids:
                    raise ValueError("Wrong result IDs")
                if schema is SCHEMA:
                    for row in value["results"]:
                        validate_content(row)
                elif schema is REVIEW_SCHEMA:
                    validate_results(value, ids)
            except ValueError:
                rejected[sha] = set(ids)
            else:
                accepted[sha] = set(ids)

        def recovered_ids(sha):
            if sha in accepted:
                return accepted[sha]
            return set().union(*(recovered_ids(child) for child in children.get(sha, [])))

        for sha, ids in rejected.items():
            assert recovered_ids(sha) == ids, (path, sha)
