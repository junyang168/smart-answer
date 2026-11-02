from datetime import datetime, timedelta, timezone

import pytest
from pptx import Presentation

from backend.api.models import SundayServiceEntry, SundaySong, SundayWorker, UnavailableDateRange
from backend.sunday_assistant.engine import Assistant
from backend.sunday_assistant.models import ChatMessage, Extraction, ZONE
from backend.sunday_assistant.ppt_ready import PptReady, PREFIX
from backend.sunday_assistant.role_assignment import assign_readers
from backend.tests.test_sunday_assistant import agent


@pytest.fixture
def ready(agent, tmp_path, monkeypatch):
    from backend.sunday_assistant.engine import upcoming_sunday
    now = datetime.now(timezone.utc)
    day = upcoming_sunday(now.astimezone(ZONE).date()).isoformat()
    settings = agent.store.settings()
    settings.admin_group = '999@g.us'
    settings.group_questions_enabled = settings.reminders_enabled = False
    agent.store.put('settings', settings.model_dump())
    agent.repository.services[day] = SundayServiceEntry(date=day,presider='王明',worshipLeader='李芳',
        sermonSpeaker='陳牧師',sermonTitle='盼望',scripture=['mat-20-28'],scriptureReaders=['讀經甲'],
        hymn='奇異恩典',responseHymn='回應歌',donationAmount=0)
    for weeks in (1,2):
        future = (datetime.fromisoformat(day)+timedelta(weeks=weeks)).date().isoformat()
        agent.repository.services[future] = SundayServiceEntry(date=future,presider='王明',worshipLeader='李芳',sermonSpeaker='陳牧師')
    agent.repository.songs.append(SundaySong(id='response',title='回應歌',lyricsMarkdown='甲\n乙\n\n丙'))
    template = tmp_path/'template.pptx'
    deck = Presentation();deck.slides.add_slide(deck.slide_layouts[6]);deck.save(template)
    calls = []
    def generate(day, *, output_path):
        calls.append(day)
        deck.save(output_path)
        return output_path
    ppt = PptReady(agent,generator=generate,template=template)
    agent.bridge.status = lambda: {'state':'ready','pending':0,'synced':True,'lastSyncAt':datetime.now(timezone.utc).isoformat()}
    monkeypatch.setattr('backend.sunday_assistant.ppt_ready.PptReady', lambda *args,**kw: ppt)
    return agent,ppt,day,now,calls


def notices(agent):
    return [row for row in agent.store.outbox() if row['key'].startswith('ppt-ready:')]


def test_completed_form_generates_then_notifies_admin_once_across_restart(ready):
    agent,ppt,day,now,calls=ready
    ppt.tick(now)
    assert len(calls)==1 and len(notices(agent))==1
    assert notices(agent)[0]['text'].startswith(PREFIX)
    assert notices(agent)[0]['group_id']=='999@g.us'
    assert agent.repository.services[day].pianist is None  # optional; offering zero is valid
    version=agent.store.get('ppt_readiness')['version']
    assert ppt.downloadable(day,version).exists()
    agent.deliver_outbox()
    reopened=PptReady(agent,generator=ppt.generator,template=ppt.template)
    reopened.tick(now);agent.deliver_outbox()
    assert len(calls)==1 and len(agent.bridge.sent)==1


@pytest.mark.parametrize('field,value',[('sermon_title',None),('donation_amount',None),('scripture',[]),('response_hymn',None)])
def test_incomplete_form_never_generates_or_announces(ready,field,value):
    agent,ppt,day,now,calls=ready
    setattr(agent.repository.services[day],field,value)
    ppt.tick(now)
    assert not calls and not notices(agent)
    assert agent.store.get('ppt_readiness')['missing']


def test_missing_song_lyrics_or_future_speaker_blocks_ready(ready):
    agent,ppt,day,now,calls=ready
    agent.repository.songs[0].lyrics_markdown=None
    ppt.tick(now);assert not calls
    agent.repository.songs[0].lyrics_markdown='既有歌詞'
    future=sorted(agent.repository.services)[-1]
    agent.repository.services[future].sermon_speaker=None
    ppt.tick(now);assert not calls and not notices(agent)


def test_change_creates_new_version_and_cancels_unsent_old_notice(ready):
    agent,ppt,day,now,calls=ready
    ppt.tick(now);old=notices(agent)[0]
    agent.repository.services[day].sermon_title='新題目'
    with pytest.raises(ValueError,match='已有更新'):
        ppt.downloadable(day,old['key'].split(':')[2])
    ppt.tick(now);agent.deliver_outbox()
    assert len(calls)==2 and len(agent.bridge.sent)==1
    assert {row['status'] for row in notices(agent)}=={'cancelled','sent'}


def test_new_lyrics_and_future_role_change_rebuild_but_email_metadata_does_not(ready):
    agent,ppt,day,now,calls=ready
    ppt.tick(now)
    agent.repository.services[day].email_body_html='不是PPT內容'
    ppt.tick(now);assert len(calls)==1
    agent.repository.songs[0].lyrics_markdown='新歌詞\n下一行'
    ppt.tick(now);assert len(calls)==2
    future=sorted(agent.repository.services)[-1]
    agent.repository.services[future].pianist='司琴甲'
    ppt.tick(now);assert len(calls)==3


def test_generation_failure_keeps_no_success_message_and_requires_explicit_retry(ready):
    agent,ppt,day,now,calls=ready
    good=ppt.generator
    def fail(*args,**kwargs):
        calls.append('failed');raise RuntimeError('template failure')
    ppt.generator=fail
    ppt.tick(now);ppt.tick(now)
    assert calls==['failed'] and not notices(agent)
    assert agent.store.get('ppt_readiness')['status']=='error'
    ppt.generator=good;ppt.retry(day);ppt.tick(now)
    assert len(calls)==2 and len(notices(agent))==1


def test_empty_or_invalid_ppt_never_announces(ready):
    agent,ppt,day,now,calls=ready
    ppt.generator=lambda day,output_path: output_path.write_text('not a ppt')
    ppt.tick(now)
    assert not notices(agent)
    assert agent.store.get('ppt_readiness')['status']=='error'


def test_changed_inputs_during_generation_are_not_published(ready):
    agent,ppt,day,now,calls=ready
    generate=ppt.generator
    def change(*args,**kwargs):
        result=generate(*args,**kwargs)
        agent.repository.services[day].sermon_title='期間更正'
        return result
    ppt.generator=change;ppt.tick(now)
    assert not notices(agent)


def test_queued_and_held_current_cases_block_ready_but_old_history_does_not(ready):
    agent,ppt,day,now,calls=ready
    msg=ChatMessage(message_id='pending',group_id='222@g.us',sender_id='sender',sent_at=now,text='更正待確認')
    agent.store.receive(msg);ppt.tick(now);assert not calls
    agent.execute_request(msg,Extraction(questions=['請補充？']))
    ppt.tick(now);assert not calls
    with agent.store.db() as db:db.execute("UPDATE messages SET status='done' WHERE id='pending'")
    ppt.tick(now);assert calls


def test_offline_or_unsynced_bridge_and_paused_settings_never_announce(ready):
    agent,ppt,day,now,calls=ready
    agent.bridge.status=lambda:{'state':'ready','pending':1,'synced':False}
    ppt.tick(now);assert not calls
    settings=agent.store.settings();settings.auto_generate_ppt=False;agent.store.put('settings',settings.model_dump())
    ppt.tick(now);assert not notices(agent)


def test_notification_unknown_result_is_queried_and_not_resent(ready):
    agent,ppt,day,now,calls=ready
    ppt.tick(now)
    original=agent.bridge.send
    def uncertain(*args):
        original(*args);raise TimeoutError('outcome unknown')
    agent.bridge.send=uncertain
    agent.deliver_outbox();agent.deliver_outbox()
    assert len(agent.bridge.sent)==1 and notices(agent)[0]['status']=='unknown'


def test_corrupt_saved_artifact_regenerates_and_download_is_blocked(ready):
    agent,ppt,day,now,calls=ready
    ppt.tick(now);version=agent.store.get('ppt_readiness')['version']
    ppt.artifact(day,version).write_text('corrupt')
    with pytest.raises(ValueError):ppt.downloadable(day,version)
    ppt.tick(now);assert len(calls)==2


def test_auto_readers_rotate_and_avoid_same_day_roles_and_absences(agent):
    for name in ['讀經甲','讀經乙','讀經丙','讀經丁','讀經戊']:
        agent.repository.workers[name]=SundayWorker(name=name,preferredRoles=['讀經經文'])
    agent.repository.workers['讀經戊'].unavailable_ranges=[UnavailableDateRange(startDate='2026-11-08',endDate='2026-11-08')]
    agent.repository.services['2026-11-01']=SundayServiceEntry(date='2026-11-01',scriptureReaders=['讀經甲','讀經乙'])
    agent.repository.services['2026-11-08']=SundayServiceEntry(date='2026-11-08',presider='讀經丙',scripture=['gen-5-21-29','heb-11-5-7'])
    assign_readers(agent.repository,agent.store,['2026-11-08'])
    readers=agent.repository.services['2026-11-08'].scripture_readers
    assert readers[0]=='讀經丁' and '讀經戊' not in readers and '讀經丙' not in readers
    agent.repository.services['2026-11-15']=SundayServiceEntry(date='2026-11-15',scripture=['gen-1-1'])
    assign_readers(agent.repository,agent.store,['2026-11-15'])
    assert agent.repository.services['2026-11-15'].scripture_readers[0] not in readers
    explicit=agent.repository.services['2026-11-15'].model_copy(update={'scripture_readers':['讀經甲']})
    agent.repository.services['2026-11-15']=explicit
    assign_readers(agent.repository,agent.store,['2026-11-15'])
    assert agent.repository.services['2026-11-15'].scripture_readers==['讀經甲']


def test_real_generator_runs_before_download_and_admin_notification(ready,monkeypatch,tmp_path):
    from backend.api import service
    from pptx.util import Inches
    agent,ppt,day,now,calls=ready
    deck=Presentation()
    for token in ['sermonTitle','hymnLyrics','responseHymnLyrics','scriptureVerses']:
        slide=deck.slides.add_slide(deck.slide_layouts[6])
        slide.shapes.add_textbox(Inches(1),Inches(1),Inches(6),Inches(4)).text='{'+token+'}'
    deck.save(ppt.template)
    monkeypatch.setattr(service,'repository',agent.repository)
    monkeypatch.setattr(service,'get_sunday_service',lambda target:agent.repository.services[target])
    monkeypatch.setattr(service,'list_fellowships',lambda:[])
    monkeypatch.setattr(service,'PPT_TEMPLATE_FILE',ppt.template)
    monkeypatch.setattr(service,'SUNDAY_WORSHIP_DIR',tmp_path/'worship')
    sections=[{'display':'馬太福音20:28','book':'馬太福音','verses':[{'chapter':20,'verse':28,'text':'測試經文內容'}]}]
    monkeypatch.setattr(service,'_fetch_scripture_sections',lambda refs:sections)
    ppt.generator=lambda target,output_path:service.generate_sunday_service_ppt(target,output_path=output_path,require_complete=True)
    ppt.tick(now)
    version=agent.store.get('ppt_readiness')['version']
    output=Presentation(ppt.downloadable(day,version))
    texts=[shape.text for slide in output.slides for shape in slide.shapes if shape.has_text_frame]
    assert '盼望' in texts and any('測試經文內容' in text for text in texts)
    assert any('甲\n乙'==text for text in texts) and '丙' in texts
    agent.deliver_outbox()
    assert len(agent.bridge.sent)==1 and agent.bridge.sent[0][1]=='999@g.us'


def test_missing_bible_response_is_not_a_ready_ppt(ready,monkeypatch,tmp_path):
    from backend.api import service
    agent,ppt,day,now,calls=ready
    monkeypatch.setattr(service,'repository',agent.repository)
    monkeypatch.setattr(service,'get_sunday_service',lambda target:agent.repository.services[target])
    monkeypatch.setattr(service,'list_fellowships',lambda:[])
    monkeypatch.setattr(service,'_fetch_scripture_sections',lambda refs:[{'verses':[]}])
    ppt.generator=lambda target,output_path:service.generate_sunday_service_ppt(target,output_path=output_path,require_complete=True)
    ppt.tick(now)
    assert not notices(agent) and agent.store.get('ppt_readiness')['status']=='error'


def test_download_and_retry_api_are_authenticated_and_serve_exact_artifact(ready,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import sunday_assistant
    agent,ppt,day,now,calls=ready
    ppt.tick(now);version=agent.store.get('ppt_readiness')['version']
    app=FastAPI();app.include_router(sunday_assistant.router)
    monkeypatch.setattr(sunday_assistant,'get_assistant',lambda:agent)
    monkeypatch.setenv('SUNDAY_ASSISTANT_SECRET','unit-secret')
    client=TestClient(app)
    url=f'/admin/sunday-assistant/ppt-ready/{day}/{version}'
    assert client.get(url).status_code==401
    headers={'Authorization':'Bearer unit-secret'}
    response=client.get(url,headers=headers)
    assert response.status_code==200 and response.content==ppt.artifact(day,version).read_bytes()
    assert 'presentationml' in response.headers['content-type']
    assert client.post(f'/admin/sunday-assistant/ppt-retry/{day}').status_code==401
    agent.repository.services[day].sermon_title='更正'
    assert client.get(url,headers=headers).status_code==409


def test_whole_tick_invokes_generation_and_notification(ready):
    agent,ppt,day,now,calls=ready
    agent.tick(now)
    assert calls==[day] and len(agent.bridge.sent)==1


def test_generation_error_enters_independent_admin_monitor(ready):
    from backend.sunday_assistant.admin_monitor import AdminMonitor
    agent,ppt,day,now,calls=ready
    ppt.generator=lambda *args,**kwargs:(_ for _ in ()).throw(RuntimeError('private rendering failure'))
    ppt.tick(now)
    monitor=AdminMonitor(agent.store.root)
    settings,worker,errors=monitor.settings_snapshot()
    assert errors>0
    settings.update(admin_help_enabled=True,admin_group='999@g.us')
    monitor.observe({'task_errors':('內部任務執行失敗',errors,0)},settings,now.timestamp())
    with monitor.db() as db:
        text=db.execute('SELECT text FROM notices').fetchone()[0]
    assert 'private rendering failure' not in text and '系統求助' in text
    assert not notices(agent)


def test_no_repeat_readers_when_enough_available_across_three_sundays(agent):
    from backend.sunday_assistant.role_assignment import assign_readers
    for name in ['A','B','C','D','E','F']:
        agent.repository.workers[name]=SundayWorker(name=name,preferredRoles=['讀經經文'])
    days=['2026-11-08','2026-11-15','2026-11-22']
    for day in days:
        agent.repository.services[day]=SundayServiceEntry(date=day,scripture=['gen-1-1','mat-20-28'])
    assign_readers(agent.repository,agent.store,days)
    groups=[set(agent.repository.services[day].scripture_readers) for day in days]
    assert groups[0].isdisjoint(groups[1]) and groups[1].isdisjoint(groups[2])
    assert set.union(*groups)=={'A','B','C','D','E','F'}


def test_existing_leader_rotation_skips_absent_person_without_replacing_manual_role(agent):
    from backend.sunday_service_skeletons import maintain_skeletons
    from backend.api.models import UnavailableDateRange
    from datetime import date
    settings=agent.store.settings();settings.worship_rotation=['王明','李芳'];agent.store.put('settings',settings.model_dump())
    agent.repository.services['2026-11-01']=SundayServiceEntry(date='2026-11-01',worshipLeader='王明')
    agent.repository.workers['李芳'].unavailable_ranges=[UnavailableDateRange(startDate='2026-11-08',endDate='2026-11-08')]
    agent.repository.services['2026-11-15']=SundayServiceEntry(date='2026-11-15',worshipLeader='陳牧師')
    maintain_skeletons(agent.repository,agent.store,date(2026,11,1))
    assert agent.repository.services['2026-11-08'].worship_leader=='王明'
    assert agent.repository.services['2026-11-15'].worship_leader=='陳牧師'


def test_input_clarification_error_is_waiting_not_an_admin_fault(ready):
    from fastapi import HTTPException
    from backend.sunday_assistant.admin_monitor import AdminMonitor
    agent,ppt,day,now,calls=ready
    def unclear(*args,**kwargs):
        calls.append('blocked');raise HTTPException(400,'詩歌演唱說明需核對')
    ppt.generator=unclear
    ppt.tick(now);ppt.tick(now)
    assert calls==['blocked'] and not notices(agent)
    assert agent.store.get('ppt_readiness')['status']=='waiting'
    assert AdminMonitor(agent.store.root).settings_snapshot()[2]==0


def test_definitely_unsent_notification_retries_without_regenerating(ready):
    import httpx
    agent,ppt,day,now,calls=ready
    ppt.tick(now)
    good=agent.bridge.send
    def offline(*args):raise httpx.ConnectError('connection refused')
    agent.bridge.send=offline;agent.deliver_outbox()
    assert notices(agent)[0]['status']=='queued'
    agent.bridge.send=good;agent.deliver_outbox()
    assert len(calls)==1 and len(agent.bridge.sent)==1


def test_new_message_at_transport_barrier_defers_ppt_notice(ready):
    agent,ppt,day,now,calls=ready
    ppt.tick(now)
    good=agent.bridge.send
    agent.bridge.send=lambda *args:{'status':'deferred'}
    agent.deliver_outbox()
    assert notices(agent)[0]['status']=='queued' and not agent.bridge.sent
    agent.bridge.send=good;agent.deliver_outbox()
    assert len(agent.bridge.sent)==1 and len(calls)==1


def test_edit_between_generation_and_delivery_cannot_announce_old_version(ready):
    agent,ppt,day,now,calls=ready
    ppt.tick(now)
    agent.repository.services[day].sermon_title='剛剛更正'
    agent.deliver_outbox()
    assert not agent.bridge.sent and notices(agent)[0]['status']=='cancelled'


def test_interrupted_generation_recovers_without_duplicate_notice(ready):
    agent,ppt,day,now,calls=ready
    ppt.tick(now)
    with agent.store.db() as db:
        db.execute("UPDATE ppt_jobs SET status='generating'")
    reopened=PptReady(agent,generator=ppt.generator,template=ppt.template)
    reopened.tick(now);agent.deliver_outbox()
    assert len(calls)==2 and len(agent.bridge.sent)==1 and len(notices(agent))==1
