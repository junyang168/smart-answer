import os

import pytest

from backend.sunday_assistant.agent_graph import run_agent
from backend.sunday_assistant.fellowship import verify_source as actual_verify_source
from backend.tests.test_sunday_fellowship import fellowship,seed,event
from backend.tests.test_sunday_assistant import agent

pytestmark=pytest.mark.skipif(os.getenv('RUN_SUNDAY_AGENT_MODEL_TESTS')!='1',reason='opt-in real model test; synthetic data and no network WhatsApp/email sends')


def test_real_agent_merges_different_admins_partial_answers_and_optional_none(fellowship,monkeypatch):
    monkeypatch.setattr('backend.sunday_assistant.fellowship.verify_source',actual_verify_source)
    agent,service,entries=seed(fellowship)
    for mid,text,sender in [('one','下次團契我來帶領','sender'),('two','下一次主題是饒恕，系列是馬太福音18章','another'),('three','團契序號2，來源連結無','third')]:
        msg=event(agent,text,mid,sender)
        result=run_agent(agent,msg)
        assert result['status']=='done'
    entry=entries['11/20/2026']
    assert entry.host=='王明' and entry.title=='饒恕' and '18' in entry.series and entry.sequence==2
    assert not service.state()[0]['missing'] and service.state()[0]['data']['status']=='ready'
    assert not agent.repository.services


def test_real_agent_does_not_write_sunday_fields_from_admin_chatter(fellowship,monkeypatch):
    monkeypatch.setattr('backend.sunday_assistant.fellowship.verify_source',actual_verify_source)
    agent,service,entries=seed(fellowship)
    msg=event(agent,'主日11/22陳牧師講道，題目恩典，讀經創世記1:1。','unrelated')
    result=run_agent(agent,msg)
    assert result['status']=='irrelevant'
    assert entries['11/20/2026'].host is None and not agent.repository.services


def test_real_agent_keeps_speculative_host_unconfirmed(fellowship,monkeypatch):
    monkeypatch.setattr('backend.sunday_assistant.fellowship.verify_source',actual_verify_source)
    agent,service,entries=seed(fellowship)
    msg=event(agent,'下一次團契可能由李芳帶領，但還沒有確認，先別確定。','tentative')
    run_agent(agent,msg)
    assert entries['11/20/2026'].host is None
