from datetime import datetime, timezone
import json

import httpx
import pytest

from backend.sunday_assistant.admin_monitor import AdminMonitor, monitor_state
from backend.sunday_assistant.models import Settings
from backend.sunday_assistant.store import Store


NOW = 1804212000.0
CONFIG = {"enabled": True, "admin_help_enabled": True, "admin_group": "999@g.us"}
ENV = {"SUNDAY_WHATSAPP_BRIDGE_URL": "http://127.0.0.1:8769", "FULL_ARTICLE_SERVICE_URL": "http://127.0.0.1:8555", "SUNDAY_ASSISTANT_SECRET": "unit-test-private-secret"}


class Client:
    def __init__(self):
        self.sent = []
        self.mode = "ok"

    def request(self, method, url, **kwargs):
        if method == "POST":
            self.sent.append(kwargs["json"])
            if self.mode == "connect_error":
                raise httpx.ConnectError("not sent")
            if self.mode == "uncertain":
                raise httpx.ReadTimeout("may have sent")
            if self.mode == "not_ready":
                return httpx.Response(409, request=httpx.Request(method, url))
            return httpx.Response(200, json={"status": "sent", "message_id": "remote"}, request=httpx.Request(method, url))
        if "/outbox/" in url:
            return httpx.Response(200, json={"status": "unknown"}, request=httpx.Request(method, url))
        return httpx.Response(200, json={"state": "ready", "synced": True}, request=httpx.Request(method, url))


@pytest.fixture
def monitor(tmp_path):
    store = Store(tmp_path)
    store.put("settings", CONFIG)
    store.put("worker", {"at": datetime.fromtimestamp(NOW, timezone.utc).isoformat(), "status": "idle"})
    return AdminMonitor(tmp_path, env=ENV, client=Client(), started=NOW - 1000)


def test_internal_error_notifies_once_across_restart_and_redacts_details(monitor):
    monitor.observe({"task_errors": ("內部任務執行失敗", 2, 0)}, CONFIG, NOW)
    monitor.deliver(NOW)
    reopened = AdminMonitor(monitor.root, env=ENV, client=monitor.client, started=NOW - 1000)
    reopened.observe({"task_errors": ("內部任務執行失敗", 3, 0)}, CONFIG, NOW + 10)
    reopened.deliver(NOW + 10)
    assert len(monitor.client.sent) == 1
    text = monitor.client.sent[0]["text"]
    assert text.startswith("【主日助理系統求助】")
    assert ENV["SUNDAY_ASSISTANT_SECRET"] not in text
    assert monitor.client.sent[0]["group_id"] == CONFIG["admin_group"]


def test_input_review_and_clarifications_are_not_incidents(monitor):
    with Store(monitor.root).db() as db:
        for state in ["waiting_reply", "review"]:
            db.execute("INSERT INTO messages(id,payload,status,received_at) VALUES(?,?,?,?)", (state, json.dumps({"text": "private text"}), state, "then"))
    monitor.tick(NOW)
    assert not monitor.client.sent
    assert not monitor_state(monitor.root)["incidents"]


def test_stalled_worker_is_detected_independently_of_its_lock(monitor):
    worker = {"at": datetime.fromtimestamp(NOW - 301, timezone.utc).isoformat(), "status": "processing"}
    conditions = monitor.conditions(worker, 0, {"state": "ready", "synced": True}, True, NOW)
    with Store(monitor.root).work_lock():
        monitor.observe(conditions, CONFIG, NOW)
        monitor.deliver(NOW)
    assert len(monitor.client.sent) == 1
    assert "五分鐘" in monitor.client.sent[0]["text"]


def test_short_reconnect_does_not_notify_and_recovery_reports_once(monitor):
    condition = {"transport": ("連線持續異常", 1, 120)}
    monitor.observe(condition, CONFIG, NOW)
    monitor.observe({}, CONFIG, NOW + 20)
    monitor.deliver(NOW + 20)
    assert not monitor.client.sent
    monitor.observe(condition, CONFIG, NOW + 30)
    monitor.observe(condition, CONFIG, NOW + 151)
    monitor.deliver(NOW + 151)
    monitor.observe({}, CONFIG, NOW + 160)
    monitor.deliver(NOW + 160)
    monitor.observe({}, CONFIG, NOW + 170)
    monitor.deliver(NOW + 170)
    assert len(monitor.client.sent) == 2
    assert "故障條件已解除" in monitor.client.sent[-1]["text"]


def test_normal_pause_does_not_raise_worker_alert(monitor):
    store = Store(monitor.root)
    store.put("settings", {**CONFIG, "enabled": False})
    store.put("worker", {"at": "2000-01-01T00:00:00+00:00", "status": "paused"})
    monitor.tick(NOW)
    assert not monitor.client.sent


def test_read_timeout_never_blindly_resends(monitor):
    monitor.client.mode = "uncertain"
    monitor.observe({"worker": ("工作程序失敗", 1, 0)}, CONFIG, NOW)
    monitor.deliver(NOW)
    monitor.deliver(NOW + 20)
    assert len(monitor.client.sent) == 1
    assert monitor_state(monitor.root)["notices"][0]["status"] == "unknown"


@pytest.mark.parametrize("mode", ["connect_error", "not_ready"])
def test_known_unsent_notification_retries_after_transport_recovers(monitor, mode):
    monitor.client.mode = mode
    monitor.observe({"worker": ("工作程序失敗", 1, 0)}, CONFIG, NOW)
    monitor.deliver(NOW)
    assert monitor_state(monitor.root)["notices"][0]["status"] == "queued"
    monitor.client.mode = "ok"
    monitor.deliver(NOW + 20)
    assert monitor_state(monitor.root)["notices"][0]["status"] == "sent"
    assert monitor.client.sent[0] == monitor.client.sent[1]


def test_repeated_crash_threshold_and_startup_grace(monitor):
    bridge = {"state": "ready", "synced": True}
    assert "worker" not in AdminMonitor(monitor.root, env=ENV, client=monitor.client, started=NOW).conditions({}, 0, bridge, True, NOW)
    for stamp in [NOW - 80, NOW - 40, NOW]:
        monitor.record_exit(stamp)
    assert "restart_loop" in monitor.conditions({}, 0, bridge, True, NOW)


def test_administrator_group_cannot_be_a_source_group():
    with pytest.raises(ValueError):
        Settings(availability_group="111@g.us", admin_group="111@g.us", admin_help_enabled=True)


def test_test_notice_is_explicit_and_does_not_fake_a_real_incident(monitor):
    key = monitor.queue_test(CONFIG, NOW)
    monitor.deliver(NOW)
    assert key.endswith(":test")
    assert "上線測試，無需處理" in monitor.client.sent[0]["text"]
    assert monitor_state(monitor.root)["incidents"] == []


def test_main_database_failure_uses_separate_monitor_state(monitor, monkeypatch):
    monitor.tick(NOW)  # Cache only the approved destination/settings.
    def broken():
        raise RuntimeError("database locked: private source text")
    monkeypatch.setattr(monitor, "settings_snapshot", broken)
    monitor.tick(NOW + 1)
    monitor.tick(NOW + 62)
    assert len(monitor.client.sent) == 1
    assert "資料庫" in monitor.client.sent[0]["text"]
    assert "private source text" not in monitor.client.sent[0]["text"]
