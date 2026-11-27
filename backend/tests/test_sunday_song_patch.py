import json

import pytest

from backend.api.models import SundayServiceEntry, SundaySong
from backend.sunday_assistant.agent_graph import run_agent
from backend.sunday_assistant.models import ServiceChange, Extraction
from backend.tests.test_sunday_assistant import agent, service
from backend.tests.test_sunday_assistant_graph import ScriptModel, incoming, trace


@pytest.fixture(autouse=True)
def grounding(monkeypatch):
    monkeypatch.setattr("backend.sunday_assistant.agent_graph.verify_grounding", lambda *_: {"supported": True, "questions": []})


def seed(agent):
    agent.repository.songs.append(SundaySong(id="new", title="新歌", lyricsMarkdown="新歌第一節\n\n新歌第二節"))
    agent.repository.services["2026-11-08"] = SundayServiceEntry(date="2026-11-08", hymn="奇異恩典", hymnNotes="只唱第1節",
        responseHymn="奇異恩典", responseHymnNotes="只唱第1節", sermonTitle="保留講題", pianist="李芳", donationAmount=300)


def test_second_song_swap_resets_only_its_old_performance_notes(agent):
    seed(agent)
    before = agent.repository.services["2026-11-08"].model_dump()
    msg = incoming(agent, "11月8日第二首歌換成新歌")
    request = {"service_changes": [service(field="responseHymn", value="新歌", evidence=msg.text)]}
    model = ScriptModel(("read_sunday_record", {"day": "2026-11-08"}), ("update_records", {"request": request, "replace_existing": True}))
    assert run_agent(agent, msg, model=model)["status"] == "done"
    after = agent.repository.services["2026-11-08"].model_dump()
    assert after["response_hymn"] == "新歌" and after["response_hymn_notes"] is None
    assert {k: v for k, v in before.items() if k not in ["response_hymn", "response_hymn_notes"]} == {k: v for k, v in after.items() if k not in ["response_hymn", "response_hymn_notes"]}
    with agent.store.db() as db:
        plan = json.loads(db.execute("SELECT payload FROM proposals WHERE status='applied'").fetchone()[0])
    reset = next(op for op in plan["operations"] if op["field"] == "responseHymnNotes")
    assert reset["before"] == "只唱第1節" and reset["after"] is None and reset["derived_from"].endswith(":responseHymn")


def test_same_song_preserves_existing_notes_and_explicit_new_notes_win(agent):
    seed(agent)
    msg = incoming(agent, "11月8日第二首仍用奇異恩典")
    request = {"service_changes": [service(field="responseHymn", value="奇異恩典", evidence=msg.text)]}
    assert run_agent(agent, msg, model=ScriptModel(("read_sunday_record", {"day": "2026-11-08"}), ("update_records", {"request": request, "replace_existing": True})))["status"] == "done"
    assert agent.repository.services["2026-11-08"].response_hymn_notes == "只唱第1節"
    msg = incoming(agent, "第二首換新歌，只唱第2節", id="new-notes", at="2026-11-02T16:01:00+00:00")
    request = {"service_changes": [service(field="responseHymn", value="新歌", evidence=msg.text), service(field="responseHymnNotes", value="只唱第2節", evidence=msg.text)]}
    assert run_agent(agent, msg, model=ScriptModel(("read_sunday_record", {"day": "2026-11-08"}), ("update_records", {"request": request, "replace_existing": True})))["status"] == "done"
    assert agent.repository.services["2026-11-08"].response_hymn_notes == "只唱第2節"


@pytest.mark.parametrize("change_song", [False, True])
def test_concurrent_change_of_a_song_or_its_notes_is_not_overwritten(agent, change_song):
    seed(agent)
    msg = incoming(agent, "11月8日第二首歌換新歌" if not change_song else "11月8日回應歌只唱第1節")
    field = "responseHymnNotes" if change_song else "responseHymn"
    request = {"service_changes": [service(field=field, value="只唱第1節" if change_song else "新歌", evidence=msg.text)]}
    def concurrent():
        entry = agent.repository.services["2026-11-08"]
        if change_song:
            entry.response_hymn = "新歌"
        else:
            entry.response_hymn_notes = "最後一節重複一次"
        return "update_records", {"request": request, "replace_existing": True}
    model = ScriptModel(("read_sunday_record", {"day": "2026-11-08"}), concurrent, ("ask_group", {"request": {"questions": ["表單剛有修改，需核對此次安排。"]}}))
    assert run_agent(agent, msg, model=model)["status"] == "waiting_reply"
    assert "record_changed" in trace(agent)[1].content
    entry = agent.repository.services["2026-11-08"]
    assert entry.response_hymn == ("新歌" if change_song else "奇異恩典")
    assert entry.response_hymn_notes == ("只唱第1節" if change_song else "最後一節重複一次")


def test_notes_can_be_explicitly_cleared_but_other_fields_cannot_be_null():
    assert ServiceChange(date="2026-11-08", field="responseHymnNotes", value=None, evidence="取消選節").value is None
    with pytest.raises(ValueError):
        ServiceChange(date="2026-11-08", field="sermonTitle", value=None, evidence="取消")


def test_saved_note_proposal_is_not_approved_for_a_different_song(agent):
    seed(agent)
    settings = agent.store.settings()
    settings.auto_apply = False
    agent.store.put("settings", settings.model_dump())
    msg = incoming(agent, "11月8日回應歌最後一節重複一次")
    agent.execute_request(msg, Extraction(service_changes=[service(field="responseHymnNotes", value="最後一節重複一次", evidence=msg.text)]))
    proposal = agent.store.proposals()[0]
    agent.repository.services["2026-11-08"].response_hymn = "新歌"
    with pytest.raises(ValueError, match="所對應的詩歌已變更"):
        agent.apply(proposal["id"], approved=True)
    assert agent.repository.services["2026-11-08"].response_hymn_notes == "只唱第1節"


def test_song_and_new_notes_resume_after_form_write_before_receipt(agent):
    seed(agent)
    settings = agent.store.settings()
    settings.auto_apply = False
    agent.store.put("settings", settings.model_dump())
    msg = incoming(agent, "11月8日回應歌換新歌，只唱第2節")
    agent.execute_request(msg, Extraction(service_changes=[service(field="responseHymn", value="新歌", evidence=msg.text), service(field="responseHymnNotes", value="只唱第2節", evidence=msg.text)]))
    proposal = agent.store.proposals()[0]
    entry = agent.repository.services["2026-11-08"]
    entry.response_hymn, entry.response_hymn_notes = "新歌", "只唱第2節"
    with agent.store.db() as db:
        db.execute("UPDATE proposals SET status='applying' WHERE id=?", (proposal["id"],))
    agent.apply(proposal["id"], approved=True)
    assert agent.store.proposals()[0]["status"] == "applied"
    assert entry.sermon_title == "保留講題"
