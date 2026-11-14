from datetime import date,datetime,timezone
import pytest

from backend.api.models import SundayServiceEntry,SundayWorker
from backend.sunday_service_skeletons import maintain_skeletons,next_two_sundays,skeleton_status
from backend.tests.test_sunday_assistant import agent


@pytest.mark.parametrize('as_of,expected',[('2026-11-01',['2026-11-08','2026-11-15']),('2026-11-02',['2026-11-08','2026-11-15']),('2026-12-27',['2027-01-03','2027-01-10'])])
def test_exact_next_two_sundays(as_of,expected):
    assert [x.isoformat() for x in next_two_sundays(date.fromisoformat(as_of))]==expected


def enable(agent):
    settings=agent.store.settings();settings.maintain_future_skeletons=True;settings.songs_group='333@g.us';agent.store.put('settings',settings.model_dump())
    for name in ['王明','李芳']:agent.repository.workers[name].preferred_roles=['司會']


def test_creates_two_empty_rows_and_available_presiders_without_dummy_scripture(agent):
    enable(agent)
    agent.repository.workers['王明'].unavailable_ranges=[__import__('backend.api.models',fromlist=['UnavailableDateRange']).UnavailableDateRange(startDate='2026-11-08',endDate='2026-11-08')]
    report=maintain_skeletons(agent.repository,agent.store,date(2026,11,1))
    assert set(agent.repository.services)=={'2026-11-08','2026-11-15'}
    assert agent.repository.services['2026-11-08'].presider=='李芳'
    assert agent.repository.services['2026-11-15'].presider=='王明'
    assert all(not entry.scripture and not entry.sermon_title and not entry.hymn for entry in agent.repository.services.values())
    assert all(row['missing']==['講員','領詩'] for row in report)
    original={day:entry.model_dump() for day,entry in agent.repository.services.items()}
    maintain_skeletons(agent.repository,agent.store,date(2026,11,1))
    assert original=={day:entry.model_dump() for day,entry in agent.repository.services.items()}


def test_existing_prepopulated_assignments_and_optional_data_are_preserved(agent):
    enable(agent)
    agent.repository.services['2026-11-08']=SundayServiceEntry(date='2026-11-08',presider='王明',worshipLeader='李芳',sermonSpeaker='陳牧師',sermonTitle='人工題目')
    before=agent.repository.services['2026-11-08'].model_dump()
    maintain_skeletons(agent.repository,agent.store,date(2026,11,1))
    assert agent.repository.services['2026-11-08'].model_dump()==before
    assert skeleton_status(agent.repository,date(2026,11,1))[0]['complete']


def test_historical_presider_with_no_current_role_is_not_reenrolled(agent):
    enable(agent)
    agent.repository.workers['孫劍雯'] = SundayWorker(name='孫劍雯', preferredRoles=[])
    agent.repository.services['2026-10-25'] = SundayServiceEntry(date='2026-10-25', presider='孫劍雯')
    maintain_skeletons(agent.repository,agent.store,date(2026,11,1))
    assert all(agent.repository.services[day].presider != '孫劍雯' for day in ['2026-11-08','2026-11-15'])
    assert agent.repository.services['2026-10-25'].presider == '孫劍雯'


@pytest.mark.parametrize('replacement_available',[True,False])
def test_auto_presider_is_rechecked_when_role_removed(agent,replacement_available):
    enable(agent)
    entry=SundayServiceEntry(date='2026-11-08',presider='王明',sermonTitle='保留題目',pianist='陳牧師')
    agent.repository.services[entry.date]=entry
    agent.store.put('auto_presider:'+entry.date,{'status':'applied','after':'王明'})
    agent.repository.workers['王明'].preferred_roles=[]
    if not replacement_available:
        agent.repository.workers['李芳'].preferred_roles=[]
    maintain_skeletons(agent.repository,agent.store,date(2026,11,1))
    updated=agent.repository.services[entry.date]
    assert updated.presider == ('李芳' if replacement_available else None)
    assert updated.sermon_title=='保留題目' and updated.pianist=='陳牧師'
    assert agent.store.get('auto_presider:'+entry.date)['after']==updated.presider


def test_auto_presider_can_change_after_absence_but_manual_presider_is_preserved(agent):
    enable(agent)
    maintain_skeletons(agent.repository,agent.store,date(2026,11,1))
    first=agent.repository.services['2026-11-08'].presider
    from backend.api.models import UnavailableDateRange
    agent.repository.workers[first].unavailable_ranges=[UnavailableDateRange(startDate='2026-11-08',endDate='2026-11-08')]
    maintain_skeletons(agent.repository,agent.store,date(2026,11,1))
    assert agent.repository.services['2026-11-08'].presider!=first
    agent.store.put('auto_presider:2026-11-08',{'after':agent.repository.services['2026-11-08'].presider,'status':'confirmed_in_group'})
    manual=agent.repository.services['2026-11-08'].presider
    agent.repository.workers[manual].unavailable_ranges=[UnavailableDateRange(startDate='2026-11-08',endDate='2026-11-08')]
    maintain_skeletons(agent.repository,agent.store,date(2026,11,1))
    assert agent.repository.services['2026-11-08'].presider==manual
    assert '司會' in skeleton_status(agent.repository,date(2026,11,1))[0]['missing']


def test_existing_worship_rotation_anchor_fills_only_missing_future_leaders(agent):
    enable(agent)
    settings=agent.store.settings();settings.worship_rotation=['王明','李芳'];agent.store.put('settings',settings.model_dump())
    agent.repository.services['2026-11-01']=SundayServiceEntry(date='2026-11-01',worshipLeader='王明')
    maintain_skeletons(agent.repository,agent.store,date(2026,11,1))
    assert agent.repository.services['2026-11-08'].worship_leader=='李芳'
    assert agent.repository.services['2026-11-15'].worship_leader=='王明'
    agent.repository.services['2026-11-08'].worship_leader='陳牧師'
    maintain_skeletons(agent.repository,agent.store,date(2026,11,1))
    assert agent.repository.services['2026-11-08'].worship_leader=='陳牧師'


def test_missing_required_fields_ask_their_groups_once_and_manual_fill_resolves_case(agent):
    enable(agent)
    now=datetime(2026,11,1,18,tzinfo=timezone.utc)
    agent.maintain_future_forms(now);agent.deliver_outbox()
    assert len(agent.bridge.sent)==4
    assert {group for _,group,_ in agent.bridge.sent}=={'222@g.us','333@g.us'}
    agent.maintain_future_forms(now);agent.deliver_outbox()
    assert len(agent.bridge.sent)==4
    for entry in agent.repository.services.values():entry.sermon_speaker='陳牧師';entry.worship_leader='李芳'
    agent.maintain_future_forms(now)
    with agent.store.db() as db:assert not db.execute("SELECT 1 FROM clarifications WHERE status='waiting'").fetchone()


def test_weekly_report_uses_exact_dates_instead_of_skipping_missing_week(agent,monkeypatch):
    from backend.api import service
    monkeypatch.setattr(service,'repository',agent.repository)
    agent.repository.services={'2026-11-08':SundayServiceEntry(date='2026-11-08',sermonSpeaker='王明'),'2026-11-22':SundayServiceEntry(date='2026-11-22',sermonSpeaker='李芳')}
    values={};service._populate_future_service_tokens(values,SundayServiceEntry(date='2026-11-01'))
    assert values['date1']=='11/08' and values['sermonSpeaker1']=='王明'
    assert values['date2']=='11/15' and values['sermonSpeaker2']==''


def test_optional_scripture_can_be_saved_empty_without_placeholder(agent,monkeypatch):
    from backend.api import service
    monkeypatch.setattr(service,'repository',agent.repository)
    entry=SundayServiceEntry(date='2026-11-08',sermonSpeaker='陳牧師',presider='王明',worshipLeader='李芳')
    service.create_sunday_service(entry)
    assert agent.repository.services['2026-11-08'].scripture==[]
    service.update_sunday_service('2026-11-08',entry)
    assert not agent.repository.services['2026-11-08'].scripture_readers


def test_report_generation_stops_when_next_two_required_skeletons_are_incomplete(agent,monkeypatch):
    from backend.api import service
    from fastapi import HTTPException
    monkeypatch.setattr(service,'repository',agent.repository)
    monkeypatch.setattr(service,'get_sunday_service',lambda _:SundayServiceEntry(date='2026-11-01'))
    with pytest.raises(HTTPException,match='未來兩個主日') as error:
        service.generate_sunday_service_ppt('2026-11-01')
    assert error.value.status_code==400
    assert '2026-11-01 的主日 PPT 尚未生成' in error.value.detail
    assert '沒有下載檔案' in error.value.detail
    assert '2026-11-08' in error.value.detail and '2026-11-15' in error.value.detail
    assert '講員' in error.value.detail and '補齊後' in error.value.detail
    assert set(agent.repository.services)=={'2026-11-08','2026-11-15'}


def test_question_for_following_week_does_not_block_this_fridays_reminders(agent):
    enable(agent)
    agent.repository.services['2026-11-08']=SundayServiceEntry(date='2026-11-08',presider='王明',worshipLeader='李芳',sermonSpeaker='陳牧師')
    agent.repository.services['2026-11-15']=SundayServiceEntry(date='2026-11-15',presider='王明',worshipLeader='李芳')
    agent.maintain_future_forms(datetime(2026,11,1,18,tzinfo=timezone.utc));agent.deliver_outbox()
    assert len(agent.bridge.sent)==1
    assert agent.remind(datetime(2026,11,6,23,tzinfo=timezone.utc))=='sent'
    assert len(agent.bridge.sent)==3


def test_ppt_returns_actionable_error_for_ambiguous_manual_song_notes(agent,monkeypatch):
    from backend.api import service
    from fastapi import HTTPException
    monkeypatch.setattr(service,'repository',agent.repository)
    monkeypatch.setattr(service,'list_fellowships',lambda:[])
    current=SundayServiceEntry(date='2026-11-01',hymn='奇異恩典',hymnNotes='最後一節有返覆')
    monkeypatch.setattr(service,'get_sunday_service',lambda _:current)
    for day in ['2026-11-08','2026-11-15']:
        agent.repository.services[day]=SundayServiceEntry(date=day,sermonSpeaker='陳牧師',presider='王明',worshipLeader='李芳')
    with pytest.raises(HTTPException,match='演唱說明需核對') as error:service.generate_sunday_service_ppt('2026-11-01')
    assert error.value.status_code==400


@pytest.mark.parametrize("submitted,expected,status", [
    (["gen-1-1-1"], ["acts-20-25-35"], None),
    ([], ["acts-20-25-35"], 409),
    (["acts-20-25-35"], ["acts-20-25-35"], None),
])
def test_stale_form_cannot_restore_placeholder_or_clear_new_reading(
    agent, monkeypatch, submitted, expected, status,
):
    from fastapi import HTTPException
    from backend.api import service

    monkeypatch.setattr(service, "repository", agent.repository)
    day = "2026-11-15"
    saved = SundayServiceEntry(date=day, scripture=["acts-20-25-35"])
    agent.repository.services[day] = saved
    edit = saved.model_copy(update={"scripture": submitted, "sermon_title": "宣教感言"})

    if status:
        with pytest.raises(HTTPException) as error:
            service.update_sunday_service(day, edit, original_scripture=["gen-1-1-1"])
        assert error.value.status_code == status
    else:
        result = service.update_sunday_service(day, edit, original_scripture=["gen-1-1-1"])
        assert result.sermon_title == "宣教感言"
    assert agent.repository.services[day].scripture == expected


def test_operator_can_select_genesis_and_clear_it(agent, monkeypatch):
    from backend.api import service

    monkeypatch.setattr(service, "repository", agent.repository)
    day = "2026-11-15"
    empty = SundayServiceEntry(date=day)
    agent.repository.services[day] = empty
    chosen = empty.model_copy(update={"scripture": ["gen-1-1-1"]})
    assert service.update_sunday_service(day, chosen, original_scripture=[]).scripture == ["gen-1-1-1"]
    assert service.update_sunday_service(day, empty, original_scripture=chosen.scripture).scripture == []
