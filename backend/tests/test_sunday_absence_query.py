from datetime import date
import json

import pytest

from backend.api.models import SundayWorker
from backend.sunday_assistant.absence_query import question_day, queue_answer, PREFIX
from backend.sunday_assistant.agent_graph import run_agent
from backend.sunday_assistant.models import ChatMessage
from backend.tests.test_sunday_assistant import agent
from backend.tests.test_sunday_assistant_graph import ScriptModel


def message(text='這週主日誰不能來？',**values):
    return ChatMessage(message_id='query',group_id='111@g.us',sender_id='sender',sent_at='2026-11-03T01:00:00Z',text=text,**values)


@pytest.mark.parametrize('text,at,expected',[
 ('這週主日誰不能來？','2026-11-03T01:00:00Z','2026-11-08'),
 ('本周日哪些同工请假','2026-11-03T01:00:00Z','2026-11-08'),
 ('下週主日誰不在？','2026-11-03T01:00:00Z','2026-11-15'),
 ('上週主日哪些人不能來','2026-11-03T01:00:00Z','2026-11-01'),
 ('11月8日誰不能來','2026-11-03T01:00:00Z','2026-11-08'),
 ('2026/11/8誰不能來','2026-11-03T01:00:00Z','2026-11-08'),
 ('2026-11-08 誰不能來','2026-11-03T01:00:00Z','2026-11-08'),
 ('Who is away this Sunday?','2026-11-03T01:00:00Z','2026-11-08'),
 ('這週主日誰不能來','2026-11-02T04:30:00Z','2026-11-01'),
 ('1/3誰不能來','2026-12-30T20:00:00Z','2027-01-03'),
])
def test_source_time_dates(text,at,expected):
    msg=message(text).model_copy(update={'sent_at':ChatMessage.model_validate({**message().model_dump(mode='json'),'sent_at':at}).sent_at})
    assert str(question_day(msg))==expected


def test_edit_time_does_not_change_relative_date():
    msg=message().model_copy(update={'source_sent_at':message().sent_at,'sent_at':message().sent_at.replace(day=16)})
    assert str(question_day(msg))=='2026-11-08'


@pytest.mark.parametrize('text',['11/8和11/15誰不能來','11/9誰不能來','11月誰不能來','這週主日誰會來','這週主日我不能來','11/8下週主日誰不能來'])
def test_ambiguous_non_sunday_or_non_query_is_rejected(text):
    with pytest.raises(ValueError):question_day(message(text))


def absent(agent):
    agent.repository.workers['王冬丽']=SundayWorker(name='王冬丽',unavailableRanges=[{'startDate':'2026-11-08','endDate':'2026-11-08'}])
    agent.repository.workers['余克宇']=SundayWorker(name='余克宇',unavailableRanges=[{'startDate':'2026-11-09','endDate':'2026-11-15'}])
    settings=agent.store.settings();settings.group_questions_enabled=False;settings.reminders_enabled=False
    agent.store.put('settings',settings.model_dump())


def test_actual_graph_queries_saved_data_no_form_writes_and_replays_once(agent):
    absent(agent);msg=message();agent.store.receive(msg)
    before={k:v.model_dump() for k,v in agent.repository.workers.items()}
    assert run_agent(agent,msg,model=ScriptModel(('answer_sunday_absences',{})))['status']=='done'
    assert run_agent(agent,msg,model=ScriptModel())['status']=='done'
    assert {k:v.model_dump() for k,v in agent.repository.workers.items()}==before
    assert not agent.repository.services and not agent.store.proposals()
    out=agent.store.outbox();assert len(out)==1
    assert '王冬丽' in out[0]['text'] and '余克宇' not in out[0]['text']
    assert '2026-11-08' in out[0]['text'] and '不代表已確認會出席' in out[0]['text']
    agent.deliver_outbox();agent.deliver_outbox()
    assert len(agent.bridge.sent)==1 and agent.bridge.sent[0][1]=='111@g.us'
    assert agent.bridge.sent[0][2].startswith(PREFIX)


def test_no_entries_never_claims_everyone_will_attend(agent):
    msg=message();agent.store.receive(msg);queue_answer(agent,msg);agent.deliver_outbox()
    text=agent.bridge.sent[0][2]
    assert '目前沒有同工登記' in text and '不代表已確認會出席' in text


def test_unsent_answer_is_refreshed_if_absences_change(agent):
    absent(agent);msg=message();agent.store.receive(msg);queue_answer(agent,msg)
    agent.repository.workers['王冬丽'].unavailable_ranges=[]
    agent.deliver_outbox()
    assert '王冬丽' not in agent.bridge.sent[0][2]


def test_changed_destination_cancels_unsent_answer(agent):
    msg=message();agent.store.receive(msg);queue_answer(agent,msg)
    settings=agent.store.settings();settings.availability_group='333@g.us'
    agent.store.put('settings',settings.model_dump());agent.deliver_outbox()
    assert not agent.bridge.sent
    assert agent.store.outbox()[0]['status']=='cancelled'


@pytest.mark.parametrize('changes',[{'group_id':'222@g.us'},{'initial_sync':True},{'revoked':True},{'unverified_edit':True}])
def test_no_answer_in_other_group_or_to_history_or_unverified_event(agent,changes):
    msg=message().model_copy(update=changes);agent.store.receive(msg)
    with pytest.raises(ValueError):queue_answer(agent,msg)
    assert not agent.store.outbox()


def test_bridge_deferred_answer_stays_queued_without_duplicate_send(agent):
    msg=message();agent.store.receive(msg);queue_answer(agent,msg)
    agent.bridge.send=lambda *_:{'status':'deferred'}
    agent.deliver_outbox();assert agent.store.outbox()[0]['status']=='queued'


def test_two_weeks_ahead_is_not_silently_interpreted_as_next_week():
    with pytest.raises(ValueError):question_day(message('下下週主日誰不能來'))


def test_submitted_uncertain_reply_is_not_rewritten_or_blindly_retried(agent):
    absent(agent);msg=message();agent.store.receive(msg);queue_answer(agent,msg)
    key='absence-query:'+msg.message_id
    with agent.store.db() as db:db.execute("UPDATE outbox SET status='unknown' WHERE key=?",(key,))
    agent.repository.workers['王冬丽'].unavailable_ranges=[]
    before=agent.store.outbox()[0]['text']
    agent.deliver_outbox()
    assert not agent.bridge.sent and agent.store.outbox()[0]['text']==before
    assert agent.store.outbox()[0]['status']=='unknown'
