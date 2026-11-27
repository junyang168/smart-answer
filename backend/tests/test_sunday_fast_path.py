"""Fast-path tests run the production entry point and forbid model/schema setup."""
import json

import pytest

from backend.sunday_assistant.models import ChatMessage, Extraction
from backend.tests.test_sunday_assistant import agent


def message(agent, text, **fields):
    msg = ChatMessage(message_id="ack", group_id="222@g.us", sender_id="sender",
                      sent_at="2026-11-03T16:00:00+00:00", source_id="original-whatsapp-id", text=text, **fields)
    agent.store.receive(msg)
    return msg


def process(agent):
    with agent.store.db() as db:
        row = db.execute("SELECT * FROM messages WHERE id='ack'").fetchone()
    agent.process_message(row)
    with agent.store.db() as db:
        return dict(db.execute("SELECT * FROM messages WHERE id='ack'").fetchone())


@pytest.mark.parametrize("text", ["谢谢", " 謝謝大家！🙏 ", "阿们。", "阿們🙏🏻", "AMEN!", "Thanks.", "Thank you！"])
def test_standalone_ack_never_builds_tools_or_calls_model(agent, monkeypatch, text):
    def forbidden(*args, **kwargs):
        pytest.fail("standalone acknowledgement reached graph/model")
    monkeypatch.setattr("backend.sunday_assistant.agent_graph.run_agent", forbidden)
    message(agent, text)
    result = process(agent)
    assert result["status"] == "irrelevant"
    assert json.loads(result["result"])["processing_path"] == "fast_path_ignore"
    assert not (agent.store.root / "agent-checkpoints.sqlite3").exists()
    assert not agent.repository.services and not agent.bridge.sent
    assert process(agent)["status"] == "irrelevant"  # receipt remains harmless on repeat


@pytest.mark.parametrize("text,fields", [
    ("收到", {}), ("好", {}), ("谢谢，11/15我们夫妻不在", {}), ("阿们，第二首改75", {}),
    ("谢谢？", {}), ("谢！谢", {}), ("", {}),
    ("谢谢", {"quoted_message_id": "question"}), ("谢谢", {"unsupported_media": True}),
    ("谢谢", {"source_sent_at": "2026-11-02T16:00:00+00:00"}),
])
def test_contextual_or_non_allowlisted_messages_take_normal_path(agent, text, fields):
    seen = []
    agent.extractor = lambda *args: seen.append(args[0]) or Extraction()
    msg = message(agent, text, **fields)
    process(agent)
    assert seen == [msg]


@pytest.mark.parametrize("same_sender", [True, False])
def test_pending_case_in_same_group_disables_fast_path_even_when_questions_held(agent, same_sender):
    settings = agent.store.settings()
    settings.group_questions_enabled = False
    agent.store.put("settings", settings.model_dump())
    original = ChatMessage(message_id="pending", group_id="222@g.us", sender_id="sender" if same_sender else "someone",
                           sent_at="2026-11-02T16:00:00+00:00", text="安排還需要確認")
    agent.store.receive(original)
    agent.execute_request(original, Extraction(questions=["請確認安排？"]))
    seen = []
    agent.extractor = lambda *args: seen.append(args[0]) or Extraction()
    msg = message(agent, "谢谢")
    process(agent)
    assert seen == [msg]
    assert not agent.bridge.sent


def test_interrupted_attempt_cannot_skip_existing_execution(agent):
    message(agent, "谢谢")
    with agent.store.db() as db:
        db.execute("UPDATE messages SET attempts=1 WHERE id='ack'")
    seen = []
    agent.extractor = lambda *args: seen.append(args[0]) or Extraction()
    process(agent)
    assert len(seen) == 1


def test_pending_case_in_other_group_does_not_disable_fast_path(agent, monkeypatch):
    original = ChatMessage(message_id="other", group_id="111@g.us", sender_id="sender",
                           sent_at="2026-11-02T16:00:00+00:00", text="我需要請假")
    agent.store.receive(original)
    agent.execute_request(original, Extraction(questions=["哪幾天？"]))
    def forbidden(*args, **kwargs):
        pytest.fail("unrelated group's pending case forced model call")
    monkeypatch.setattr("backend.sunday_assistant.agent_graph.run_agent", forbidden)
    message(agent, "阿们")
    assert process(agent)["status"] == "irrelevant"


def test_existing_partial_proposal_cannot_be_discarded_as_chatter(agent):
    message(agent, "谢谢")
    with agent.store.db() as db:
        db.execute("INSERT INTO proposals(message_id,payload,status,updated_at) VALUES ('ack','{\"partial_action\":true}','applied','2026-11-03')")
    seen = []
    agent.extractor = lambda *args: seen.append(args[0]) or Extraction()
    assert process(agent)["status"] == "done"
    assert len(seen) == 1
