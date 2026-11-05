from datetime import datetime,date
import json

import pytest

from backend.api.models import FellowshipEntry
from backend.sunday_assistant.models import ChatMessage
from backend.sunday_assistant.fellowship import Fellowship,FellowshipPatch,SourceVerdict,FIELDS
from backend.sunday_assistant.agent_graph import run_agent
from backend.tests.test_sunday_assistant import agent
from backend.tests.test_sunday_assistant_graph import ScriptModel


def local(value):
    return datetime.fromisoformat(value)


@pytest.fixture
def fellowship(agent,monkeypatch):
    entries={'11/06/2026':FellowshipEntry(date='11/06/2026',host='支勇',title='馬太福音21章',series='馬太福音21章',sequence=1)}
    agent.repository.list_fellowships=lambda:list(entries.values())
    def create(row):
        assert row.date not in entries
        entries[row.date]=row
        return row
    def update(day,row):
        assert day in entries
        entries.pop(day);entries[row.date]=row
        return row
    agent.repository.create_fellowship=create;agent.repository.update_fellowship=update
    settings=agent.store.settings();settings.admin_group='999@g.us';settings.fellowship_enabled=True
    settings.fellowship_start_date=date(2026,11,5);settings.fellowship_since=local('2026-11-05T12:00:00Z')
    settings.group_questions_enabled=False;settings.reminders_enabled=False
    agent.store.put('settings',settings.model_dump())
    monkeypatch.setattr('backend.sunday_assistant.fellowship.verify_source',lambda *_:SourceVerdict(supported=True))
    return agent,Fellowship(agent),entries


def seed(fellowship):
    agent,service,entries=fellowship
    service.maintain(local('2026-11-08T15:00:00-06:00'))
    return agent,service,entries


def event(agent,text,mid='admin-1',sender='sender'):
    msg=ChatMessage(message_id=mid,group_id='999@g.us',sender_id=sender,sent_at='2026-11-09T16:00:00Z',text=text)
    assert agent.store.receive(msg)
    return msg


def reads(service,msg):
    return {f'fellowship:{c["target_day"]}:{key}':c['fields'].get(key) for c in service.context(msg)['cases'] for key in FIELDS}


def patch(service,msg,changes,**kwargs):
    request=FellowshipPatch(date='2026-11-20',changes=changes,evidence={k:msg.text for k in changes},**kwargs)
    return service.patch(msg,request,reads(service,msg))


def test_sunday_1500_creates_date_and_asks_all_five_fields_once(fellowship):
    agent,service,entries=fellowship
    service.maintain(local('2026-11-08T14:59:59-06:00'))
    assert len(entries)==2 and not agent.store.outbox()
    service.maintain(local('2026-11-08T21:00:00Z'))
    assert entries['11/20/2026'].host is None
    row=agent.store.outbox()[0]
    assert row['group_id']=='999@g.us'
    for label in FIELDS.values():assert '• '+label in row['text']
    assert '• 日期' not in row['text']
    service.maintain(local('2026-11-08T16:00:00-06:00'))
    assert len(agent.store.outbox())==1


def test_thursday_0900_reminds_only_missing_fields_and_once(fellowship):
    agent,service,entries=seed(fellowship)
    msg=event(agent,'下一次王明帶，主題饒恕')
    patch(service,msg,{'host':'王明','title':'饒恕'})
    service.maintain(local('2026-11-12T08:59:59-06:00'))
    assert not any(r['key'].startswith('fellowship:reminder:') for r in agent.store.outbox())
    service.maintain(local('2026-11-12T09:00:00-06:00'))
    reminder=next(r for r in agent.store.outbox() if r['key'].startswith('fellowship:reminder:'))
    assert '系列、序號、來源連結' in reminder['text'] and '主講、主題' not in reminder['text']
    service.maintain(local('2026-11-12T12:00:00-06:00'))
    assert len([r for r in agent.store.outbox() if r['key'].startswith('fellowship:reminder:')])==1


def test_different_admins_partial_answers_preserve_full_form_and_optional_none(fellowship):
    agent,service,entries=seed(fellowship)
    entries['11/20/2026'].summary='原來回顧';entries['11/20/2026'].email_body_html='<p>Custom HTML</p>'
    patch(service,event(agent,'下次由王明帶','a'),{'host':'王明'})
    patch(service,event(agent,'主題饒恕，系列馬太福音18章','b','another'),{'title':'饒恕','series':'馬太福音18章'})
    patch(service,event(agent,'序號無，來源連結也無','c','third'),{'sequence':None,'sourceLinks':[]})
    actual=entries['11/20/2026']
    assert actual.summary=='原來回顧' and actual.email_body_html=='<p>Custom HTML</p>'
    assert not service.state()[0]['missing'] and service.state()[0]['data']['status']=='ready'
    assert not agent.repository.services


def test_graph_has_only_fellowship_tools_and_saves_admin_reply(fellowship):
    agent,service,entries=seed(fellowship)
    msg=event(agent,'下次王明帶，主題饒恕，系列馬太福音18章，序號1，來源連結無')
    request={'date':'2026-11-20','changes':{'host':'王明','title':'饒恕','series':'馬太福音18章','sequence':1,'sourceLinks':[]},'evidence':{k:msg.text for k in FIELDS}}
    model=ScriptModel(('read_fellowship_context',{}),('patch_fellowship',{'request':request}))
    assert run_agent(agent,msg,model=model)['status']=='done'
    assert model.names=={'read_fellowship_context','patch_fellowship','ask_fellowship','ignore_fellowship_message'}
    assert entries['11/20/2026'].host=='王明'
    assert not service.state()[0]['missing']
    count=len(agent.store.outbox());run_agent(agent,msg,model=ScriptModel())
    assert len(agent.store.outbox())==count


def test_same_field_conflict_is_held_but_independent_field_patch_preserves_change(fellowship):
    agent,service,entries=seed(fellowship);msg=event(agent,'王明帶，主題饒恕')
    snapshot=reads(service,msg)
    entries['11/20/2026'].host='李芳'
    with pytest.raises(ValueError,match='其他人'):
        service.patch(msg,FellowshipPatch(date='2026-11-20',changes={'host':'王明'},evidence={'host':msg.text}),snapshot)
    service.patch(msg,FellowshipPatch(date='2026-11-20',changes={'title':'饒恕'},evidence={'title':msg.text}),snapshot)
    assert entries['11/20/2026'].host=='李芳'


def test_source_rejection_keeps_form_and_creates_specific_admin_question(fellowship,monkeypatch):
    agent,service,entries=seed(fellowship);msg=event(agent,'可能王明帶')
    monkeypatch.setattr('backend.sunday_assistant.fellowship.verify_source',lambda *_:SourceVerdict(supported=False,questions=['請確認是否已確定由王明主講？']))
    result=patch(service,msg,{'host':'王明'})
    assert result['status']=='waiting_reply' and entries['11/20/2026'].host is None
    assert any(r['key'].startswith('fellowship:clarify:') for r in agent.store.outbox())


def test_initial_sync_and_other_group_cannot_patch(fellowship):
    agent,service,entries=seed(fellowship)
    msg=event(agent,'王明帶')
    for update in ({'initial_sync':True},{'group_id':'111@g.us'},{'revoked':True}):
        with pytest.raises(ValueError):service.context(msg.model_copy(update=update))


def test_explicit_existing_schedule_exception_is_preserved(fellowship):
    agent,service,entries=fellowship
    entries['11/27/2026']=FellowshipEntry(date='11/27/2026',host='李芳',title='既有主題',series='既有系列')
    service.maintain(local('2026-11-08T15:00:00-06:00'))
    assert '11/20/2026' not in entries and service.cases()[0]['target_day']=='2026-11-27'


def test_activation_does_not_post_old_missed_sunday_questions(fellowship):
    agent,service,entries=fellowship
    entries['10/30/2026']=FellowshipEntry(date='10/30/2026',host='王明',title='舊資料')
    service.maintain(local('2026-11-05T16:00:00-06:00'))
    assert not agent.store.outbox() and not service.cases()


def test_crash_after_form_replace_recovers_saved_verdict_without_second_model_call(fellowship,monkeypatch):
    agent,service,entries=seed(fellowship);msg=event(agent,'王明帶領')
    snapshot=reads(service,msg);request=FellowshipPatch(date='2026-11-20',changes={'host':'王明'},evidence={'host':msg.text})
    calls=[]
    monkeypatch.setattr('backend.sunday_assistant.fellowship.verify_source',lambda *_:(calls.append('verified') or SourceVerdict(supported=True)))
    original=agent.repository.update_fellowship
    def interrupted(day,row):original(day,row);raise OSError('after file replace')
    agent.repository.update_fellowship=interrupted
    with pytest.raises(OSError):service.patch(msg,request,snapshot)
    agent.repository.update_fellowship=original
    assert service.patch(msg,request,snapshot)['status']=='done'
    assert calls==['verified'] and entries['11/20/2026'].host=='王明'
    assert len([r for r in agent.store.outbox() if r['key'].startswith('fellowship:confirmed:')])==1


def test_crash_after_commit_repairs_ack_without_resaving_form(fellowship,monkeypatch):
    agent,service,entries=seed(fellowship);msg=event(agent,'王明帶領')
    request=FellowshipPatch(date='2026-11-20',changes={'host':'王明'},evidence={'host':msg.text});snapshot=reads(service,msg)
    queue=service.queue
    def interrupted(key,text):
        if key.startswith('fellowship:confirmed:'):raise OSError('before ack')
        queue(key,text)
    service.queue=interrupted
    with pytest.raises(OSError):service.patch(msg,request,snapshot)
    service.queue=queue
    agent.repository.update_fellowship=lambda *_:pytest.fail('applied form must not be rewritten')
    assert service.patch(msg,request,snapshot)['status']=='done'
    assert len([r for r in agent.store.outbox() if r['key'].startswith('fellowship:confirmed:')])==1


def test_unrelated_partial_answer_does_not_close_a_host_clarification(fellowship,monkeypatch):
    agent,service,entries=seed(fellowship);first=event(agent,'可能王明帶領','uncertain-host')
    monkeypatch.setattr('backend.sunday_assistant.fellowship.verify_source',lambda *_:SourceVerdict(supported=False,questions=['主講是否已確認？']))
    patch(service,first,{'host':'王明'})
    monkeypatch.setattr('backend.sunday_assistant.fellowship.verify_source',lambda *_:SourceVerdict(supported=True))
    patch(service,event(agent,'主題饒恕','topic'),{'title':'饒恕'})
    assert service.state()[0]['data']['questions']==['主講是否已確認？']
    patch(service,event(agent,'主講確定王明','confirmed-host'),{'host':'王明'})
    assert not service.state()[0]['data']['questions']
    with agent.store.db() as db:assert db.execute('SELECT status FROM messages WHERE id=?',(first.message_id,)).fetchone()[0]=='done'


def test_form_date_normalization_does_not_change_the_target_date():
    assert FellowshipPatch(date='11/20/2026').date==date(2026,11,20)
    assert FellowshipPatch(date='2026-11-20').date==date(2026,11,20)


def test_agent_can_correct_tool_schema_error_without_human_help(fellowship):
    agent,service,entries=seed(fellowship);msg=event(agent,'王明帶領')
    request={'date':'not-a-date','changes':{'host':'王明'},'evidence':{'host':msg.text}}
    model=ScriptModel(('read_fellowship_context',{}),('patch_fellowship',{'request':request}),('patch_fellowship',{'request':{**request,'date':'2026-11-20'}}))
    assert run_agent(agent,msg,model=model)['status']=='done'
    assert entries['11/20/2026'].host=='王明'


def test_backend_does_not_accept_admin_history_before_activation(fellowship):
    agent,service,entries=fellowship
    old=ChatMessage(message_id='before-activation',group_id='999@g.us',sender_id='sender',sent_at='2026-11-04T12:00:00Z',text='下次王明帶')
    assert agent.store.receive(old) is False
    assert not agent.store.messages()
