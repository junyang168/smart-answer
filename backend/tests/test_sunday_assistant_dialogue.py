from datetime import datetime, timezone

import pytest

from backend.sunday_assistant.engine import Assistant
from backend.sunday_assistant.models import ChatMessage, Extraction
from backend.sunday_assistant.store import Store
from backend.tests.test_sunday_assistant import agent, receive, service, FRIDAY


def ask(agent, text="王明和李芳11月要外出", question="請問外出的起訖日期？", **kwargs):
    receive(agent, {"questions": [question]}, group="111@g.us", text=text, **kwargs)
    return agent.store.proposals()[0]["id"]


def absence(names=("王明", "李芳"), evidence="王明和李芳11月要外出"):
    return [{"worker": n, "start": "2026-11-15", "end": "2026-11-29", "evidence": evidence} for n in names]


def test_multiturn_reply_survives_restart_and_applies_all_sources_once(agent):
    dialogue_id = ask(agent)
    assert len(agent.bridge.sent) == 1
    assert not agent.repository.workers["王明"].unavailable_ranges
    restarted = Assistant(Store(agent.store.root), agent.repository, bridge=agent.bridge)
    restarted.tick(datetime(2026, 11, 2, 18, tzinfo=timezone.utc))
    assert len(agent.bridge.sent) == 1
    query = ChatMessage(message_id="answer", group_id="111@g.us", sender_id="sender", sent_at="2026-11-03T16:00:00+00:00", text="15日出發")
    context = restarted.context(query)[-1]["pending_clarifications"][0]
    assert context["clarification_id"] == dialogue_id
    assert context["source_messages"][0]["text"] == "王明和李芳11月要外出"
    receive(restarted, {"clarification_id": dialogue_id, "questions": ["請問哪一天回來，回程當天能否安排？"]},
            group="111@g.us", text="15日出發", id="a1", at="2026-11-03T16:00:00+00:00")
    assert len(agent.bridge.sent) == 2
    assert not agent.repository.workers["李芳"].unavailable_ranges
    msg = receive(restarted, {"clarification_id": dialogue_id, "absence_changes": absence()},
                  group="111@g.us", text="29日回來，當天也不能安排", id="a2", at="2026-11-03T16:01:00+00:00")
    assert all(not agent.repository.workers[n].is_available_on("2026-11-29") for n in ["王明", "李芳"])
    assert len(agent.bridge.sent) == 3
    assert agent.bridge.sent[-1][2].startswith("【請假登記回覆】收到，已更新")
    assert all(m["status"] == "done" for m in restarted.store.messages())
    assert not restarted.pending_dialogues(msg.model_copy(update={"sent_at": datetime(2026, 11, 4, tzinfo=timezone.utc)}))
    assert not restarted.store.receive(msg)
    # A crash after apply but before marking the current message done must not
    # re-extract an answer without its now-resolved dialogue.
    with restarted.store.db() as db:
        db.execute("UPDATE messages SET status='processing' WHERE id='a2'")
    restarted.extractor = lambda *_: pytest.fail("applied message was re-extracted")
    restarted.tick(datetime(2026, 11, 4, tzinfo=timezone.utc))
    assert len(agent.bridge.sent) == 3
    assert restarted.store.messages()[0]["status"] == "done"


def test_reply_completes_service_fields_and_confirms_after_save(agent):
    receive(agent, {"questions": ["請問是哪個主日？"]}, text="講道題目是盼望", id="title")
    dialogue_id = agent.store.proposals()[0]["id"]
    receive(agent, {"clarification_id": dialogue_id, "service_changes": [service(field="sermonTitle", value="盼望", evidence="講道題目是盼望")]},
            text="11月8日", id="answer", at="2026-11-03T16:00:00+00:00")
    assert agent.repository.services["2026-11-08"].sermon_title == "盼望"
    assert agent.bridge.sent[-1][2].startswith("【主日資料回覆】收到，已更新")
    assert agent.store.proposals()[-1]["status"] == "resolved"
    assert agent.remind(FRIDAY) == "sent"


@pytest.mark.parametrize("group,sender", [("222@g.us", "sender"), ("111@g.us", "other")])
def test_other_chat_or_unaddressed_sender_cannot_answer(agent, group, sender):
    dialogue_id = ask(agent)
    msg = ChatMessage(message_id="spoof", group_id=group, sender_id=sender, sent_at="2026-11-03T16:00:00+00:00", text="15至29日")
    assert agent.pending_dialogues(msg) == []
    agent.store.receive(msg)
    agent.extractor = lambda *_: Extraction(clarification_id=dialogue_id, absence_changes=absence())
    agent.tick(datetime(2026, 11, 3, 17, tzinfo=timezone.utc))
    assert agent.store.messages()[0]["status"] == "error"
    assert not agent.repository.workers["李芳"].unavailable_ranges
    assert len(agent.bridge.sent) == 1


def test_another_group_member_can_explicitly_reply_to_group_question(agent):
    dialogue_id = ask(agent)
    msg = ChatMessage(message_id="help", group_id="111@g.us", sender_id="helper", sent_at="2026-11-03T16:00:00+00:00",
                      text="15日至29日", quoted_message_id="remote-" + agent.bridge.sent[0][0])
    assert agent.pending_dialogues(msg)[0]["clarification_id"] == dialogue_id
    agent.store.receive(msg)
    agent.extractor = lambda *_: Extraction(clarification_id=dialogue_id, absence_changes=absence())
    agent.tick(datetime(2026, 11, 3, 17, tzinfo=timezone.utc))
    assert agent.store.messages()[0]["status"] == "done"
    assert not agent.repository.workers["李芳"].is_available_on("2026-11-22")


def test_answer_cannot_use_evidence_from_unrelated_recent_message(agent):
    dialogue_id = ask(agent)
    receive(agent, {}, group="111@g.us", text="陳牧師12月也要外出", id="unrelated", at="2026-11-03T15:00:00+00:00")
    receive(agent, {"clarification_id": dialogue_id, "absence_changes": absence(names=["陳牧師"], evidence="陳牧師12月也要外出")},
            group="111@g.us", text="15日至29日", id="bad", at="2026-11-03T16:00:00+00:00")
    assert agent.store.messages()[0]["status"] == "error"
    assert not agent.repository.workers["陳牧師"].unavailable_ranges


def test_unrelated_message_leaves_question_open_and_blocks_premature_reminder(agent):
    receive(agent, {"questions": ["請問講員姓名？"]}, text="11/8的講員還沒定", id="ask")
    dialogue_id = agent.store.proposals()[0]["id"]
    receive(agent, {}, text="謝謝大家", id="thanks", at="2026-11-03T16:00:00+00:00")
    assert agent.remind(FRIDAY) == "waiting_for_messages"
    assert len(agent.bridge.sent) == 1
    with agent.store.db() as db:
        assert db.execute("SELECT status FROM clarifications WHERE id=?", (dialogue_id,)).fetchone()[0] == "waiting"


def test_same_remaining_question_is_not_repeated_and_cancel_closes_pending(agent):
    question = "請問外出的起訖日期？"
    dialogue_id = ask(agent, question=question)
    receive(agent, {"clarification_id": dialogue_id, "questions": [question]}, group="111@g.us", text="還沒決定", id="later", at="2026-11-03T16:00:00+00:00")
    assert len(agent.bridge.sent) == 1
    receive(agent, {"clarification_id": dialogue_id, "cancel_clarification": True}, group="111@g.us", text="不用登記了", id="cancel", at="2026-11-03T16:01:00+00:00")
    assert len(agent.bridge.sent) == 2
    assert "已取消" in agent.bridge.sent[-1][2]
    assert all(m["status"] == "done" for m in agent.store.messages())
    assert not agent.repository.workers["王明"].unavailable_ranges


@pytest.mark.parametrize("initial,auto", [(True, True), (False, False)])
def test_history_and_preview_do_not_post_questions(agent, initial, auto):
    settings = agent.store.settings(); settings.auto_apply = auto; agent.store.put("settings", settings.model_dump())
    msg = ChatMessage(message_id="history", group_id="111@g.us", sender_id="sender", sent_at="2026-11-02T16:00:00+00:00", text="我要外出", initial_sync=initial)
    agent.store.receive(msg); agent.extractor = lambda *_: Extraction(questions=["請問日期？"])
    agent.tick(datetime(2026, 11, 2, 17, tzinfo=timezone.utc))
    assert not agent.bridge.sent


def test_multiple_pending_items_resolve_only_selected_dialogue(agent):
    first = ask(agent)
    second = ask(agent, text="陳牧師12月要外出", id="other", at="2026-11-03T15:00:00+00:00")
    receive(agent, {"clarification_id": first, "absence_changes": absence()}, group="111@g.us", text="前面兩位是15日至29日", id="answer", at="2026-11-03T16:00:00+00:00")
    with agent.store.db() as db:
        assert db.execute("SELECT status FROM clarifications WHERE id=?", (first,)).fetchone()[0] == "resolved"
        assert db.execute("SELECT status FROM clarifications WHERE id=?", (second,)).fetchone()[0] == "waiting"
    assert not agent.repository.workers["陳牧師"].unavailable_ranges


def test_different_group_members_fill_separate_fields_and_offering_incrementally(agent):
    values = [("sermonSpeaker", "陳牧師"), ("sermonTitle", "盼望"), ("hymn", "奇異恩典"),
              ("responseHymn", "奇異恩典"), ("scripture", ["mat-20-28"]), ("donationAmount", 1234.56)]
    for index, (field, value) in enumerate(values):
        text = f"11月8日的{field}：{value}"
        msg = ChatMessage(message_id=f"field-{index}", group_id="222@g.us", sender_id=f"member-{index}",
                          sent_at=f"2026-11-02T16:0{index}:00+00:00", text=text)
        agent.store.receive(msg)
        agent.extractor = lambda *_, field=field, value=value, text=text: Extraction(service_changes=[service(field=field, value=value, evidence=text)])
        agent.tick(datetime(2026, 11, 2, 17, tzinfo=timezone.utc))
        assert agent.store.messages()[0]["status"] == "done"
    saved = agent.repository.services["2026-11-08"].model_dump(by_alias=True)
    assert all(saved[field] == value for field, value in values)
    assert not agent.bridge.sent
    assert not agent.missing("2026-11-08")
    assert "本週奉獻" not in agent.missing("2026-11-15")


@pytest.mark.parametrize("amount", [-1, True, float("nan"), float("inf"), 123.456, "$123", "約一千"])
def test_offering_rejects_non_amount_values(amount):
    with pytest.raises(ValueError):
        Extraction(service_changes=[service(field="donationAmount", value=amount)])


def test_zero_offering_is_a_valid_amount():
    assert Extraction(service_changes=[service(field="donationAmount", value=0)]).service_changes[0].value == 0


def test_dismissing_pending_dialogue_closes_history_and_cancels_unsent_question(agent, monkeypatch):
    from backend.api import sunday_assistant as api
    dialogue_id = ask(agent)
    receive(agent, {"clarification_id": dialogue_id, "questions": ["請問回程日期？"]}, group="111@g.us", text="15日出發", id="partial", at="2026-11-03T16:00:00+00:00")
    current = agent.store.proposals()[0]["id"]
    with agent.store.db() as db:
        db.execute("UPDATE outbox SET status='queued' WHERE key=?", (f"clarify:{dialogue_id}:{current}",))
    monkeypatch.setattr(api, "get_assistant", lambda: agent)
    assert api.decide(current, api.Decision(action="dismiss"))["status"] == "dismissed"
    assert all(m["status"] in {"done", "dismissed"} for m in agent.store.messages())
    with agent.store.db() as db:
        assert db.execute("SELECT status FROM clarifications WHERE id=?", (dialogue_id,)).fetchone()[0] == "dismissed"
        assert db.execute("SELECT status FROM outbox WHERE key=?", (f"clarify:{dialogue_id}:{current}",)).fetchone()[0] == "cancelled"


def test_restart_recovers_second_worker_write_failure_without_early_success(agent):
    original = agent.repository.update_sunday_worker
    fail_once = {"pending": True}
    def update(name, worker):
        if name == "李芳" and fail_once["pending"]:
            fail_once["pending"] = False
            raise OSError("second worker write unavailable")
        original(name, worker)
    agent.repository.update_sunday_worker = update
    text = "王明和李芳11月15日至29日不能服事"
    receive(agent, {"absence_changes": absence(evidence=text)}, group="111@g.us", text=text)
    assert not agent.bridge.sent
    assert agent.store.proposals()[0]["status"] == "applying"
    restarted = Assistant(Store(agent.store.root), agent.repository, bridge=agent.bridge)
    restarted.tick(datetime(2026, 11, 3, 17, tzinfo=timezone.utc))
    assert all(not agent.repository.workers[name].is_available_on("2026-11-22") for name in ["王明", "李芳"])
    assert restarted.store.messages()[0]["status"] == "done"
    assert len(agent.bridge.sent) == 1
