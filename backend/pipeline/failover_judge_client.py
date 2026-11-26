"""Judge client that fails over between subscription backends when one hits its quota.

Order: Gemini 4 Neon (agy) first; when its quota is exhausted switch to Claude Opus;
when Opus is exhausted switch back to Gemini (once its cooldown has passed).
Subscription CLIs only: no API keys are ever used.
"""
import re
import threading
import time

QUOTA_MARKERS = (
    "individual quota reached",
    "resource_exhausted",
    "usage_limit_reached",
    "hit your 5-hour limit",
    "hit your limit",
    "rate_limit_error",
)
DEFAULT_COOLDOWN = 30 * 60


class AllJudgesExhausted(RuntimeError):
    pass


def is_quota_error(exc) -> bool:
    text = str(exc).lower()
    return any(m in text for m in QUOTA_MARKERS)


def reset_seconds(text: str) -> float:
    m = re.search(r"resets in (?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", text, re.I)
    if m and any(m.groups()):
        h, mi, s = (int(x or 0) for x in m.groups())
        return h * 3600 + mi * 60 + s + 30
    return DEFAULT_COOLDOWN


class FailoverState:
    """Shared across threads: when each backend is usable again (monotonic seconds)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.until = {}

    def usable(self, name):
        with self.lock:
            return self.until.get(name, 0) <= time.monotonic()

    def block(self, name, seconds):
        with self.lock:
            self.until[name] = max(self.until.get(name, 0), time.monotonic() + seconds)

    def soonest_wait(self, names):
        with self.lock:
            return max(0.0, min(self.until.get(n, 0) for n in names) - time.monotonic())


STATE = FailoverState()


class FailoverJudgeClient:
    backend = "failover_subscription"

    def __init__(self, members, state=None):
        """members: ordered list of (name, client). The first is preferred."""
        self.members = list(members)
        self.state = state or STATE
        self.model = self.members[0][1].model  # stable cache key = preferred model
        self.last_metadata = {}
        self.last_raw_response = None
        self.last_usage = None
        self._call_artifacts = None

    @property
    def call_artifacts(self):
        return self._call_artifacts

    @call_artifacts.setter
    def call_artifacts(self, value):
        self._call_artifacts = value
        for _, c in self.members:
            c.call_artifacts = value

    def generate_json(self, system_prompt, user_prompt, json_schema):
        names = [n for n, _ in self.members]
        last_exc = None
        for name, client in self.members:
            if not self.state.usable(name):
                continue
            try:
                out = client.generate_json(system_prompt, user_prompt, json_schema)
            except Exception as exc:  # noqa: BLE001
                if is_quota_error(exc):
                    self.state.block(name, reset_seconds(str(exc)))
                    last_exc = exc
                    print(f"[failover] {name} quota exhausted; switching", flush=True)
                    continue
                raise
            self.last_metadata = {"served_by": name, **getattr(client, "last_metadata", {})}
            self.last_raw_response = getattr(client, "last_raw_response", None)
            return out
        wait = self.state.soonest_wait(names)
        raise AllJudgesExhausted(
            f"all judge backends exhausted {names}; soonest retry in {int(wait)}s; last: {last_exc}"
        )
