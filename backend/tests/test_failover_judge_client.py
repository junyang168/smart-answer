import pytest

from backend.pipeline import failover_judge_client as fj


class Fake:
    def __init__(self, model, outcomes):
        self.model = model
        self.outcomes = list(outcomes)
        self.calls = 0
        self.call_artifacts = None

    def generate_json(self, system, user, schema):
        self.calls += 1
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


QUOTA = RuntimeError("error: Individual quota reached. Resets in 2h9m19s.")
OPUS_QUOTA = RuntimeError('{"api_error":"usage_limit_reached","result":"You\'ve hit your 5-hour limit"}')


def make(gem, opus):
    return fj.FailoverJudgeClient([("gemini", gem), ("opus", opus)], state=fj.FailoverState())


def test_switches_to_opus_when_gemini_quota_is_exhausted_and_stays_there():
    gem, opus = Fake("gemini-4-neon-high", [QUOTA]), Fake("claude-opus-5-5", [{"v": 1}, {"v": 2}])
    c = make(gem, opus)
    assert c.generate_json("s", "u", {}) == {"v": 1}
    assert c.generate_json("s", "u", {}) == {"v": 2}
    assert gem.calls == 1 and opus.calls == 2          # second call skipped the cooling Gemini
    assert c.last_metadata["served_by"] == "opus"


def test_switches_back_to_gemini_after_opus_runs_out_and_gemini_recovers():
    gem, opus = Fake("g", [{"v": "g1"}]), Fake("o", [OPUS_QUOTA])
    c = make(gem, opus)
    c.state.block("gemini", 0)                          # Gemini usable again immediately
    c.state.block("opus", 3600)                         # Opus cooling
    assert c.generate_json("s", "u", {}) == {"v": "g1"}
    assert opus.calls == 0


def test_non_quota_errors_are_not_swallowed():
    gem, opus = Fake("g", [ValueError("bad schema")]), Fake("o", [{"v": 1}])
    with pytest.raises(ValueError):
        make(gem, opus).generate_json("s", "u", {})
    assert opus.calls == 0


def test_all_exhausted_fails_closed():
    gem, opus = Fake("g", [QUOTA]), Fake("o", [OPUS_QUOTA])
    with pytest.raises(fj.AllJudgesExhausted):
        make(gem, opus).generate_json("s", "u", {})


def test_model_is_the_preferred_one_and_artifacts_propagate(tmp_path):
    gem, opus = Fake("gemini-4-neon-high", []), Fake("claude-opus-5-5", [])
    c = make(gem, opus)
    c.call_artifacts = tmp_path
    assert c.model == "gemini-4-neon-high"
    assert gem.call_artifacts == tmp_path and opus.call_artifacts == tmp_path


def test_reset_seconds_parses_the_provider_hint():
    assert fj.reset_seconds("Resets in 2h9m19s") == 2 * 3600 + 9 * 60 + 19 + 30
    assert fj.reset_seconds("no hint") == fj.DEFAULT_COOLDOWN
