from datetime import datetime, timezone

import pytest

from backend.sunday_assistant.member_identity import Member, Roster, audit, resolution, save_roster
from backend.sunday_assistant.models import Settings
from backend.sunday_assistant.store import Store
from backend.tests.test_sunday_assistant import agent


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path)
    s.put("settings", Settings(availability_group="123@g.us", sender_workers={"1555@c.us": ["楊軍"]}, households=[["楊軍", "孫劍雯"]]).model_dump())
    return s


def roster(*members):
    return Roster(groups=["123@g.us"], checked_at=datetime.now(timezone.utc), members=[Member(groups=["123@g.us"], **m) for m in members])


def test_phone_lid_and_device_ids_resolve_to_one_self_across_restart(store):
    save_roster(store, roster({"id": "999@lid", "ids": ["999@lid", "1555@c.us"], "push_name": "孫劍雯"}))
    reopened = Store(store.root)
    result = resolution(reopened, "999:3@lid", ["楊軍", "孫劍雯"])
    assert result["workers"] == ["楊軍"]  # Pushname/spouse never changes the author.
    assert result["conflict"] is None
    assert resolution(reopened, "1555:9@s.whatsapp.net", ["楊軍"])["workers"] == ["楊軍"]


def test_display_names_are_only_candidates_not_identity(store):
    save_roster(store, roster({"id": "888@lid", "ids": ["888@lid"], "contact_name": "孫劍雯", "is_my_contact": True}))
    assert resolution(store, "888@lid", ["楊軍", "孫劍雯"])["workers"] == []
    row = audit(store, ["楊軍", "孫劍雯"])["members"][0]
    assert row["status"] == "needs_confirmation"
    assert row["candidates"] == ["孫劍雯"]
    assert row["worker"] is None


def test_conflicting_confirmed_names_on_equivalent_ids_fail_closed(store):
    save_roster(store, roster({"id": "999@lid", "ids": ["999@lid", "1555@c.us"]}))
    settings = store.settings()
    settings.sender_workers["999:2@lid"] = ["孫劍雯"]
    store.put("settings", settings.model_dump())
    result = resolution(store, "999@lid", ["楊軍", "孫劍雯"])
    assert result["workers"] == [] and result["conflict"] == "multiple_workers"


def test_phone_reassignment_retains_old_evidence_and_stops_resolution(store):
    save_roster(store, roster({"id": "999@lid", "ids": ["999@lid", "1555@c.us"]}))
    save_roster(store, roster({"id": "888@lid", "ids": ["888@lid", "1555@c.us"]}))
    assert resolution(store, "999@lid", ["楊軍"])["conflict"] == "account_alias_conflict"
    assert resolution(store, "888@lid", ["楊軍"])["workers"] == []


def test_roster_rejects_other_groups_and_fake_pairs(store):
    with pytest.raises(ValueError):
        Member(id="999@lid", ids=["888@lid", "1555@c.us"], groups=["123@g.us"])
    with pytest.raises(ValueError):
        save_roster(store, Roster(groups=["456@g.us"], members=[], checked_at=datetime.now(timezone.utc)))


def test_existing_operator_identity_beats_old_self_introduction_and_keeps_spouse_separate(store):
    save_roster(store, roster({"id": "999@lid", "ids": ["999@lid", "1555@c.us"]}))
    with store.db() as db:
        db.execute("INSERT INTO identities VALUES ('999@lid','孫劍雯','old','then')")
    assert resolution(store, "999@lid", ["楊軍", "孫劍雯"])["workers"] == ["楊軍"]


def test_duplicate_names_are_exposed_as_multiple_candidates(store):
    settings = store.settings()
    settings.worker_aliases = {"Lily": "孫劍雯", "lily": "楊軍"}
    store.put("settings", settings.model_dump())
    save_roster(store, roster({"id": "888@lid", "ids": ["888@lid"], "push_name": "LILY"}))
    row = audit(store, ["楊軍", "孫劍雯"])["members"][0]
    assert set(row["candidates"]) == {"楊軍", "孫劍雯"}
    assert row["worker"] is None


@pytest.mark.parametrize("delivery,expected", [("held", "queued"), ("sent", "waiting_reply"), ("unknown", "waiting_reply")])
def test_new_account_evidence_resumes_only_unsent_held_identity_cases(agent, delivery, expected):
    from backend.sunday_assistant.models import ChatMessage, Extraction
    settings = agent.store.settings()
    settings.sender_workers = {"1555@c.us": ["李芳"]}
    settings.group_questions_enabled = False
    agent.store.put("settings", settings.model_dump())
    message = ChatMessage(message_id="unknown-self", sender_id="999@lid", group_id="111@g.us", sent_at="2026-11-02T16:00:00Z", text="我俩12/7-12/22不在")
    agent.store.receive(message)
    agent.execute_request(message, Extraction(questions=["請核對本人"]), action_key="identity")
    with agent.store.db() as db:
        db.execute("UPDATE outbox SET status=?", (delivery,))
    save_roster(agent.store, Roster(groups=sorted(settings.source_group_ids), checked_at=datetime.now(timezone.utc), members=[Member(id="999@lid", ids=["999@lid", "1555@c.us"], groups=["111@g.us"])]))
    with agent.store.db() as db:
        assert db.execute("SELECT status FROM messages WHERE id='unknown-self'").fetchone()[0] == expected
        if expected == "queued":
            assert db.execute("SELECT status FROM outbox").fetchone()[0] == "cancelled"
    assert not agent.bridge.sent
