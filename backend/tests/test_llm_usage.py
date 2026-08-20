from types import SimpleNamespace

from backend.pipeline.llm_usage import usage_row, usage_summary


def test_anthropic_cache_legs_count_as_input() -> None:
    """The cached and freshly written prefix are billed input, not free."""
    row = usage_row(
        SimpleNamespace(
            input_tokens=1134,
            cache_creation_input_tokens=48000,
            cache_read_input_tokens=2000,
            output_tokens=32123,
        ),
        attempt=1,
    )

    assert row["prompt_tokens"] == 1134 + 48000 + 2000
    assert row["cache_write_tokens"] == 48000
    assert row["cached_tokens"] == 2000
    assert row["total_tokens"] == 1134 + 48000 + 2000 + 32123


def test_openai_prompt_tokens_already_include_the_cached_prefix() -> None:
    row = usage_row(
        SimpleNamespace(
            prompt_tokens=50000,
            completion_tokens=1000,
            total_tokens=51000,
            prompt_tokens_details=SimpleNamespace(cached_tokens=40000),
        ),
        attempt=2,
    )

    assert row == {
        "attempt": 2,
        "prompt_tokens": 50000,
        "cached_tokens": 40000,
        "cache_write_tokens": None,
        "completion_tokens": 1000,
        "total_tokens": 51000,
    }


def test_summary_adds_up_every_attempt_including_the_rejected_one() -> None:
    summary = usage_summary("review", [
        {"prompt_tokens": 100, "cached_tokens": 50, "completion_tokens": 10, "total_tokens": 110},
        {"prompt_tokens": 100, "cached_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    ])

    assert summary["calls"] == 2
    assert summary["prompt_tokens"] == 200
    assert summary["total_tokens"] == 230
    assert summary["cache_hit"] == "75%"
