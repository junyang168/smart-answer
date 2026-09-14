import pytest

from backend.pipeline.source_contract_cleanup_runner import parse_only_sources


def test_parse_only_sources_uses_manifest_to_recover_space_bearing_argv():
    known = ["S 210101", "S 210101 extended", "plain"]
    command = "python -m runner --batch batch.json --only S 210101 extended plain"
    assert parse_only_sources(command, known) == {"S 210101 extended", "plain"}


def test_parse_only_sources_without_only_owns_the_whole_batch():
    assert parse_only_sources("python -m runner --batch batch.json", ["a", "b"]) == {
        "a",
        "b",
    }


def test_parse_only_sources_fails_closed_on_unrecognized_tail():
    with pytest.raises(ValueError, match="cannot parse"):
        parse_only_sources("python -m runner --only unknown", ["known"])
